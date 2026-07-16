import asyncio
import csv
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Optional
import httpx
from aiomqtt import Client as MqttClient
from aiomqtt import MqttError
import traceback


@dataclass(frozen=True)
class AgentConfig:
    mqtt_host: str = os.getenv("MQTT_HOST", "mosquitto")
    mqtt_port: int = int(os.getenv("MQTT_PORT", "1883"))
    mqtt_topic: str = os.getenv("MQTT_TOPIC", "sm-repository/+/submodels/+/submodelElements/+/updated")
    basyx_base_url: str = os.getenv("BASYX_BASE_URL", "http://aas-env:8081") 
    http_timeout_seconds: float = float(os.getenv("HTTP_TIMEOUT_SECONDS", "8"))
    robot_settle_timeout_seconds: float = float(os.getenv("ROBOT_SETTLE_TIMEOUT_SECONDS", "45"))
    robot_status_poll_seconds: float = float(os.getenv("ROBOT_STATUS_POLL_SECONDS", "0.4"))
    robot_motion_start_grace_seconds: float = float(os.getenv("ROBOT_MOTION_START_GRACE_SECONDS", "5.0"))
    sensor_true_rearm_seconds: float = float(os.getenv("SENSOR_TRUE_REARM_SECONDS", "2.0"))
    register_robots: str = os.getenv("REGISTERED_ROBOTS", "")
    robot_submodel_bindings: str = os.getenv("ROBOT_SUBMODEL_BINDINGS", "")
    orchestrator_log_csv_path: str = os.getenv(
        "ORCHESTRATOR_LOG_CSV_PATH",
        str(Path(__file__).resolve().parent / "orchestrator_logs.csv"),
    )
    orchestrator_summary_csv_path: str = os.getenv(
        "ORCHESTRATOR_SUMMARY_CSV_PATH",
        str(Path(__file__).resolve().parent / "orchestrator_summary.csv"),
    )
    summary_batch_size: int = int(os.getenv("SUMMARY_BATCH_SIZE", "5"))


@dataclass(frozen=True)
class RobotEndpoints:
    state_submodel_b64: str
    skills_submodel_b64: str


def normalize_submodel_id(submodel_id: str) -> str:
    # BaSyx submodel identifiers in URLs are URL-safe base64 without padding
    return submodel_id.strip().replace("+", "-").replace("/", "_").rstrip("=")


def parse_bool_value(raw_payload: str) -> Optional[bool]:
    text = raw_payload.strip().lower()
    if text in {"true", "1", "on", "yes"}:
        return True
    if text in {"false", "0", "off", "no"}:
        return False

    try:
        parsed = json.loads(text)
        if isinstance(parsed, bool):
            return parsed
        if isinstance(parsed, (int, float)):
            return bool(parsed)
        if isinstance(parsed, dict):
            for key in ("value", "newValue", "payload"):
                if key in parsed:
                    val = parsed[key]
                    if isinstance(val, bool):
                        return val
                    return parse_bool_value(str(val))
    except json.JSONDecodeError:
        pass
    return None


class FactoryOrchestrator:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.job_queue = asyncio.Queue()
        self.dispatch_queue = asyncio.Queue()
        self.active_jobs = set()
        self.sensor_states: dict[str, bool] = {}
        self.sensor_last_true_at: dict[str, float] = {}
        self.sensor_waiting_for_clear: dict[str, bool] = {}
        self.log_lock = asyncio.Lock()
        self.log_headers = [
            "t1_ms",
            "t2_ms",
            "t3_ms",
        ]
        self.summary_headers = [
            "batch_id",
            "batch_size",
            "ok_count",
            "error_count",
            "t1_ms_min",
            "t1_ms_max",
            "t1_ms_mean",
            "t2_ms_min",
            "t2_ms_max",
            "t2_ms_mean",
            "t3_ms_min",
            "t3_ms_max",
            "t3_ms_mean",
            "t_match_ms_min",
            "t_match_ms_max",
            "t_match_ms_mean",
            "t_queue_ms_min",
            "t_queue_ms_max",
            "t_queue_ms_mean",
        ]
        self.summary_batch_id = 0
        self.summary_buffer: list[dict] = []
        self.log_path = Path(self.config.orchestrator_log_csv_path)
        self.summary_path = Path(self.config.orchestrator_summary_csv_path)
        self._ensure_log_file_exists()
        self._ensure_summary_file_exists()
        self.robots = self._build_robot_endpoints(config)
        if not self.robots:
            print("[ORCHESTRATOR] Warning: no robot bindings configured; dispatch cannot start")

    def _ensure_log_file_exists(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        expected_header = ",".join(self.log_headers)
        if self.log_path.exists() and self.log_path.stat().st_size > 0:
            try:
                with self.log_path.open("r", encoding="utf-8") as file:
                    current_header = file.readline().strip()
                if current_header == expected_header:
                    return
                print(
                    "[ORCHESTRATOR] Detected outdated orchestrator_logs.csv header; "
                    "resetting file to t1/t2/t3 format"
                )
            except OSError:
                pass
        with self.log_path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=self.log_headers)
            writer.writeheader()

    def _ensure_summary_file_exists(self) -> None:
        self.summary_path.parent.mkdir(parents=True, exist_ok=True)
        if self.summary_path.exists() and self.summary_path.stat().st_size > 0:
            return
        with self.summary_path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=self.summary_headers)
            writer.writeheader()

    async def _append_log_row(self, row: dict) -> None:
        async with self.log_lock:
            row_to_write = {key: row.get(key, "") for key in self.log_headers}
            with self.log_path.open("a", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=self.log_headers)
                writer.writerow(row_to_write)

    def _safe_float(self, value: object) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    async def _append_summary_row_if_ready(self, row: dict) -> None:
        async with self.log_lock:
            batch_size = max(1, self.config.summary_batch_size)
            t1 = self._safe_float(row.get("t1_ms"))
            t2 = self._safe_float(row.get("t2_ms"))
            t3 = self._safe_float(row.get("t3_ms"))

            # Only completed rows with all timestamps contribute to batch statistics.
            if t1 is None or t2 is None or t3 is None:
                return

            self.summary_buffer.append(
                {
                    "run_id": self._safe_float(row.get("run_id")),
                    "status": str(row.get("status", "")),
                    "t1_ms": t1,
                    "t2_ms": t2,
                    "t3_ms": t3,
                    "t_match_ms": t2 - t1,
                    "t_queue_ms": t3 - t2,
                }
            )

            if len(self.summary_buffer) < batch_size:
                return

            batch = self.summary_buffer[:batch_size]
            self.summary_buffer = self.summary_buffer[batch_size:]
            self.summary_batch_id += 1

            ok_count = sum(1 for item in batch if item["status"] == "ok")
            error_count = len(batch) - ok_count

            def stats(values: list[int]) -> tuple[int, int, float]:
                return min(values), max(values), float(mean(values))

            t1_min, t1_max, t1_mean = stats([item["t1_ms"] for item in batch])
            t2_min, t2_max, t2_mean = stats([item["t2_ms"] for item in batch])
            t3_min, t3_max, t3_mean = stats([item["t3_ms"] for item in batch])
            tm_min, tm_max, tm_mean = stats([item["t_match_ms"] for item in batch])
            tq_min, tq_max, tq_mean = stats([item["t_queue_ms"] for item in batch])

            summary_row = {
                "batch_id": self.summary_batch_id,
                "batch_size": len(batch),
                "ok_count": ok_count,
                "error_count": error_count,
                "t1_ms_min": t1_min,
                "t1_ms_max": t1_max,
                "t1_ms_mean": f"{t1_mean:.3f}",
                "t2_ms_min": t2_min,
                "t2_ms_max": t2_max,
                "t2_ms_mean": f"{t2_mean:.3f}",
                "t3_ms_min": t3_min,
                "t3_ms_max": t3_max,
                "t3_ms_mean": f"{t3_mean:.3f}",
                "t_match_ms_min": tm_min,
                "t_match_ms_max": tm_max,
                "t_match_ms_mean": f"{tm_mean:.3f}",
                "t_queue_ms_min": tq_min,
                "t_queue_ms_max": tq_max,
                "t_queue_ms_mean": f"{tq_mean:.3f}",
            }

            with self.summary_path.open("a", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=self.summary_headers)
                writer.writerow(summary_row)

        print(
            "[ORCHESTRATOR] Logged summary batch "
            f"#{self.summary_batch_id} ({len(batch)} runs) to {self.summary_path}"
        )

    async def _log_and_print(self, row: dict) -> None:
        await self._append_log_row(row)
        await self._append_summary_row_if_ready(row)
        print(
            "[ORCHESTRATOR] Logged run "
            f"#{row.get('run_id')} status={row.get('status')} sensor={row.get('sensor')}"
        )

    def _build_robot_endpoints(self, config: AgentConfig) -> list[RobotEndpoints]:
        robots: list[RobotEndpoints] = []

        # Preferred format per robot: stateSubmodelId|skillsSubmodelId
        for chunk in config.robot_submodel_bindings.split(","):
            raw = chunk.strip()
            if not raw:
                continue

            if "|" in raw:
                state_raw, skills_raw = raw.split("|", 1)
                state_id = normalize_submodel_id(state_raw)
                skills_id = normalize_submodel_id(skills_raw)
            else:
                # Backward-compatible form where one submodel hosts both state and skills.
                state_id = normalize_submodel_id(raw)
                skills_id = state_id

            if state_id and skills_id:
                robots.append(RobotEndpoints(state_submodel_b64=state_id, skills_submodel_b64=skills_id))

        if robots:
            return robots

        # Legacy fallback from REGISTERED_ROBOTS.
        for robot_id in config.register_robots.split(","):
            normalized = normalize_submodel_id(robot_id)
            if normalized:
                robots.append(RobotEndpoints(state_submodel_b64=normalized, skills_submodel_b64=normalized))

        return robots


    async def handle_event(self, submodel_b64: str, property_id: str, payload: str, mqtt_topic: str, received_at_ms: int) -> None:
        bool_value = parse_bool_value(payload)
        
        if "Present" not in property_id and "Clear" not in property_id: 
            return

        if bool_value is None:
            print(
                f"[ORCHESTRATOR] Ignored sensor event {property_id}: "
                f"payload did not resolve to boolean ({payload})"
            )
            return

        sensor_key = f"{submodel_b64}:{property_id}"
        last_state = self.sensor_states.get(sensor_key)
        self.sensor_states[sensor_key] = bool_value

        if bool_value is False:
            if self.sensor_waiting_for_clear.get(sensor_key):
                self.sensor_waiting_for_clear[sensor_key] = False
                print(
                    f"[ORCHESTRATOR] Sensor '{property_id}' on Conveyor '{submodel_b64}' cleared; ready for next box detection"
                )
            return

        # After dispatching one move for this sensor, require an explicit clear (false)
        # before accepting another true detection.
        if self.sensor_waiting_for_clear.get(sensor_key):
            return

        # Support both edge-based and pulse-based sensors.
        # If a sensor emits repeated true pulses without explicit false, re-arm after a cooldown.
        now = time.monotonic()
        last_true_at = self.sensor_last_true_at.get(sensor_key)
        self.sensor_last_true_at[sensor_key] = now
        is_rising_edge = last_state is not True
        is_rearmed_pulse = (
            last_true_at is None
            or (now - last_true_at) >= self.config.sensor_true_rearm_seconds
        )
        if not is_rising_edge and not is_rearmed_pulse:
            return
        
        #Create am unique job identifier to prevent duplicate ingestion
        job_token = f"{submodel_b64}_{property_id}"

        if job_token not in self.active_jobs:
            self.active_jobs.add(job_token)
            await self.job_queue.put({
                "conveyor_b64": submodel_b64,
                "sensor": property_id,
                "sensor_key": sensor_key,
                "token": job_token,
                "mqtt_topic": mqtt_topic,
                "t1_ms": received_at_ms,
            })
            print(f"[ORCHESTRATOR] Enqueued event: Sensor '{property_id}' triggered on Conveyor '{submodel_b64}'")

    async def start_worker(self) -> None:
        """Sequential matching stage: resolves robot and operation, then hands off for dispatch."""
        print("[ORCHESTRATOR] Matchmaking loop active.")
        while True:
            job = await self.job_queue.get()
            await self._run_match_safely(job)
            self.job_queue.task_done()

    async def start_dispatcher(self) -> None:
        """Sequential dispatch stage: invokes AAS operation and records t3."""
        print("[ORCHESTRATOR] Dispatch loop active.")
        while True:
            dispatch_job = await self.dispatch_queue.get()
            await self._run_dispatch_safely(dispatch_job)
            self.dispatch_queue.task_done()

    async def _run_match_safely(self, job: dict) -> None:
        try:
            await self.process_factory_job(job)
        except Exception as e:
            print(f"[ERROR] Failed to process factory job: {e}")
            traceback.print_exc()
            await self._log_and_print(
                {
                    "run_id": job.get("run_id"),
                    "status": "error_match",
                    "conveyor_submodel_b64": job.get("conveyor_b64"),
                    "sensor": job.get("sensor"),
                    "mqtt_topic": job.get("mqtt_topic"),
                    "t1_ms": job.get("t1_ms"),
                    "notes": str(e),
                }
            )
            token = job.get("token")
            if token:
                self.active_jobs.discard(token)
        finally:
            pass

    async def _run_dispatch_safely(self, dispatch_job: dict) -> None:
        try:
            await self.dispatch_factory_job(dispatch_job)
        except Exception as e:
            print(f"[ERROR] Failed to dispatch operation: {e}")
        finally:
            token = dispatch_job.get("token")
            if token:
                self.active_jobs.discard(token)

    async def _read_is_moving(self, client: httpx.AsyncClient, state_url: str) -> Optional[bool]:
        try:
            status_res = await client.get(f"{state_url}/submodel-elements/IsMoving")
            if status_res.status_code == 200:
                return parse_bool_value(status_res.text)
        except Exception as exc:
            print(f"[ORCHESTRATOR] Error reading robot state via HTTP: {exc}")
        return False

    async def _wait_for_motion_settle(self, client: httpx.AsyncClient, state_url: str) -> bool:
        timeout_at = asyncio.get_running_loop().time() + self.config.robot_settle_timeout_seconds
        grace_until = asyncio.get_running_loop().time() + self.config.robot_motion_start_grace_seconds
        saw_moving = False
        consecutive_not_moving = 0

        while asyncio.get_running_loop().time() < timeout_at:
            moving = await self._read_is_moving(client, state_url)
            if moving is True:
                saw_moving = True
                consecutive_not_moving = 0
            elif moving is False:
                consecutive_not_moving += 1

                # Standard success: motion seen, then robot stops.
                if saw_moving and consecutive_not_moving >= 2:
                    return True

                # Fallback success: some stacks never toggle IsMoving reliably.
                # After a grace period, repeated false readings indicate robot is idle.
                if (not saw_moving and asyncio.get_running_loop().time() >= grace_until and consecutive_not_moving >= 3):
                    return True

            await asyncio.sleep(self.config.robot_status_poll_seconds)

        return False
    
    async def _manage_robot_cooldown(self, robot: RobotEndpoints, state_url: str) -> None:
        """Monitors a specific robot's motion in the background without halting the factory queue"""
        timeout = httpx.Timeout(self.config.http_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            settled = await self._wait_for_motion_settle(client, state_url)
        if not settled:
            print(f"[ORCHESTRATOR] Warning: Robot {robot.state_submodel_b64} motion did not settle within timeout.")
        else:
            print(f"[ORCHESTRATOR] Robot {robot.state_submodel_b64} has successfully completed motion and settled.")

    async def process_factory_job(self, job: dict) -> None:
        triggering_sensor = job["sensor"]
        triggering_sensor_key = job.get("sensor_key")
        timeout = httpx.Timeout(self.config.http_timeout_seconds)

        async with httpx.AsyncClient(timeout=timeout) as client:
            # Matchmaking Loop: Search across our pool of registered robots
            for robot in self.robots:
                state_url = f"{self.config.basyx_base_url}/submodels/{robot.state_submodel_b64}"
                skills_url = f"{self.config.basyx_base_url}/submodels/{robot.skills_submodel_b64}"
                print(
                    "[ORCHESTRATOR] Checking robot "
                    f"state={robot.state_submodel_b64}, skills={robot.skills_submodel_b64} "
                    f"for capability to service sensor {triggering_sensor}"
                )
                # 1. Check if this specific robot is currently busy executing a task
                moving = await self._read_is_moving(client, state_url)
                if moving is None or moving is True:
                    continue

                # 2. Semantic Discovery: Read what capabilities this robot twin supports
                try:
                    cap_res = await client.get(f"{skills_url}/submodel-elements/SupportedCapabilities")
                    if cap_res.status_code != 200:
                        print(
                            f"[ORCHESTRATOR] Robot skills submodel {robot.skills_submodel_b64} has no SupportedCapabilities "
                            f"(HTTP {cap_res.status_code})"
                        )
                        continue
                    routes = cap_res.json().get("value", [])
                    if not isinstance(routes, list):
                        print(
                            "[ORCHESTRATOR] Robot "
                            f"{robot.skills_submodel_b64} SupportedCapabilities is not a collection"
                        )
                        continue
                except Exception as e:
                    print(
                        "[ORCHESTRATOR] Could not fetch capabilities for robot "
                        f"{robot.skills_submodel_b64}: {e}"
                    )
                    continue

                # 3. Parameter and Operation Matching
                matched_route = None
                for route in routes:
                    route_values = route.get("value", [])
                    if not isinstance(route_values, list):
                        continue
                    elements = {elem["idShort"]: elem.get("value") for elem in route_values if "idShort" in elem}
                    if elements.get("TriggerSensor") == triggering_sensor:
                        matched_route = route
                        target_op = elements.get("TargetOperation")
                        break

                if not matched_route:
                    continue # This robot doesn't have a route for this sensor, try next robot

                if not target_op:
                    print(
                        f"[ORCHESTRATOR] Route matched for sensor {triggering_sensor} on robot {robot.skills_submodel_b64} "
                        "but TargetOperation is missing"
                    )
                    continue
                print(
                    "[ORCHESTRATOR] Found matching route for sensor "
                    f"{triggering_sensor} on robot {robot.skills_submodel_b64}, invoking operation {target_op}"
                )
                # 4. Fully Generic Argument Generation Loop
                input_arguments = []
                for route_element in matched_route.get("value", []):
                    # Strip out orchestrator-specific metadata tags
                    if route_element["idShort"] in ["TriggerSensor", "TargetOperation"]:
                        continue
                    
                    # Pack any remaining properties dynamically into the payload
                    input_arguments.append({
                        "value": {
                            "modelType": "Property",
                            "idShort": route_element["idShort"],
                            "valueType": route_element.get("valueType", "xs:string"),
                            "value": route_element.get("value")
                        }
                    })

                body = {
                    "inputArguments": input_arguments,
                    "inoutputArguments": [],
                    "requestedTimeout": int(self.config.http_timeout_seconds * 1000)
                }
                if triggering_sensor_key:
                    self.sensor_waiting_for_clear[triggering_sensor_key] = True
                    print(
                        f"[ORCHESTRATOR] Sensor '{triggering_sensor}' latched until it clears (false) before next move"
                    )

                t2_ms = int(time.time() * 1000)
                dispatch_payload = {
                    "run_id": job.get("run_id"),
                    "token": job.get("token"),
                    "conveyor_b64": job.get("conveyor_b64"),
                    "sensor": triggering_sensor,
                    "mqtt_topic": job.get("mqtt_topic"),
                    "t1_ms": job.get("t1_ms"),
                    "t2_ms": t2_ms,
                    "robot_state_submodel_b64": robot.state_submodel_b64,
                    "robot_skills_submodel_b64": robot.skills_submodel_b64,
                    "target_operation": target_op,
                    "invoke_url": f"{skills_url}/submodel-elements/{target_op}/invoke",
                    "body": body,
                }
                await self.dispatch_queue.put(dispatch_payload)
                print(
                    f"[ORCHESTRATOR] Match completed for run #{job.get('run_id')}; "
                    f"queued for dispatch (sensor={triggering_sensor}, robot={robot.skills_submodel_b64})"
                )
                return
            
            print(f"[ORCHESTRATOR] Resource Warning: No available robots can currently service sensor {triggering_sensor}")
            self.active_jobs.discard(job["token"])

    async def dispatch_factory_job(self, dispatch_job: dict) -> None:
        timeout = httpx.Timeout(self.config.http_timeout_seconds)
        t3_ms = int(time.time() * 1000)

        print(
            f"[ORCHESTRATOR] Dispatching run #{dispatch_job.get('run_id')} "
            f"skill '{dispatch_job.get('target_operation')}' to robot {dispatch_job.get('robot_skills_submodel_b64')}"
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(dispatch_job["invoke_url"], json=dispatch_job["body"])
            print(f"[ORCHESTRATOR] Response status from robot: {response.status_code}")

            t1_ms = dispatch_job.get("t1_ms")
            t2_ms = dispatch_job.get("t2_ms")

            await self._log_and_print(
                {
                    "run_id": dispatch_job.get("run_id"),
                    "status": "ok" if response.status_code < 400 else "invoke_http_error",
                    "conveyor_submodel_b64": dispatch_job.get("conveyor_b64"),
                    "sensor": dispatch_job.get("sensor"),
                    "matched_robot_state_submodel_b64": dispatch_job.get("robot_state_submodel_b64"),
                    "matched_robot_skills_submodel_b64": dispatch_job.get("robot_skills_submodel_b64"),
                    "target_operation": dispatch_job.get("target_operation"),
                    "http_status": response.status_code,
                    "mqtt_topic": dispatch_job.get("mqtt_topic"),
                    "t1_ms": t1_ms,
                    "t2_ms": t2_ms,
                    "t3_ms": t3_ms,
                    "notes": "Raw timestamps only; compute latency terms offline",
                }
            )

            if response.status_code < 400:
                state_url = f"{self.config.basyx_base_url}/submodels/{dispatch_job.get('robot_state_submodel_b64')}"
                robot = RobotEndpoints(
                    state_submodel_b64=dispatch_job.get("robot_state_submodel_b64"),
                    skills_submodel_b64=dispatch_job.get("robot_skills_submodel_b64"),
                )
                asyncio.create_task(self._manage_robot_cooldown(robot, state_url))


def parse_topic(topic: str) -> Optional[tuple[str, str]]:
    parts = topic.split("/")
    if len(parts) < 7:
        print(f"[AGENT] Ignoring topic with insufficient parts0: {topic}")
        return None
    if parts[0] != "sm-repository" or parts[2] != "submodels":
        print(f"[AGENT] Ignoring topic with unexpected structure1: {topic}")
        return None
    if parts[4] != "submodelElements" or parts[6] != "updated":
        print(f"[AGENT] Ignoring topic with unexpected structure2: {topic}")
        return None
    
    # parts[3] is the submodelIdBase64URLEncoded
    # parts[5] is the property idShortPath
    print(f"[AGENT] Parsed topic: submodelId={parts[3]}, propertyId={parts[5]}")
    return parts[3], parts[5]


async def run_agent(config: AgentConfig) -> None:
    orchestrator = FactoryOrchestrator(config)
    asyncio.create_task(orchestrator.start_worker())
    asyncio.create_task(orchestrator.start_dispatcher())

    while True:
        try:
            async with MqttClient(hostname=config.mqtt_host, port=config.mqtt_port) as client:
                await client.subscribe(config.mqtt_topic)
                print(
                    "[AGENT] Connected to MQTT broker "
                    f"{config.mqtt_host}:{config.mqtt_port}, subscribed to {config.mqtt_topic}"
                )

                async for message in client.messages:
                    parsed = parse_topic(str(message.topic))
                    if parsed is not None:
                        submodel_b64, property_id = parsed
                        payload = message.payload.decode(errors="replace")
                        received_at_ms = int(time.time() * 1000)
                        # Pass events to the event tracker safely
                        await orchestrator.handle_event(
                            submodel_b64,
                            property_id,
                            payload,
                            str(message.topic),
                            received_at_ms,
                        )

        except MqttError as exc:
            print(f"[AGENT] MQTT connection error: {exc}. Reconnecting in 3s...")
            await asyncio.sleep(3)


async def main() -> None:
    config = AgentConfig()
    print("[AGENT] Starting factory orchestration agent...")
    await run_agent(config)


if __name__ == "__main__":
    asyncio.run(main())