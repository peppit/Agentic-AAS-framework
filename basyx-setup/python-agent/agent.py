import asyncio
import csv
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Optional
import httpx
from aiomqtt import Client as MqttClient
from aiomqtt import MqttError
import traceback
from urllib.parse import urlparse


@dataclass(frozen=True)
class AgentConfig:
    mqtt_host: str = os.getenv("MQTT_HOST", "mosquitto")
    mqtt_port: int = int(os.getenv("MQTT_PORT", "1883"))
    mqtt_topic: str = os.getenv("MQTT_TOPIC", "sm-repository/+/submodels/+/submodelElements/+/updated")
    operation_reply_topic: str = os.getenv("OPERATION_REPLY_TOPIC", "simulation/+/replies/+")
    basyx_base_url: str = os.getenv("BASYX_BASE_URL", "http://aas-env:8081") 
    http_timeout_seconds: float = float(os.getenv("HTTP_TIMEOUT_SECONDS", "8"))
    robot_settle_timeout_seconds: float = float(os.getenv("ROBOT_SETTLE_TIMEOUT_SECONDS", "45"))
    robot_status_poll_seconds: float = float(os.getenv("ROBOT_STATUS_POLL_SECONDS", "0.4"))
    robot_motion_start_grace_seconds: float = float(os.getenv("ROBOT_MOTION_START_GRACE_SECONDS", "5.0"))
    sensor_true_rearm_seconds: float = float(os.getenv("SENSOR_TRUE_REARM_SECONDS", "2.0"))
    job_retry_seconds: float = float(os.getenv("JOB_RETRY_SECONDS", "0.5"))
    job_timeout_seconds: float = float(os.getenv("JOB_TIMEOUT_SECONDS", "60"))
    invoke_retry_count: int = int(os.getenv("INVOKE_RETRY_COUNT", "3"))
    robot_bindings_file: str = os.getenv("ROBOT_BINDINGS_FILE", "")
    station_bindings_file: str = os.getenv("STATION_BINDINGS_FILE", "")
    register_robots: str = os.getenv("REGISTERED_ROBOTS", "")
    robot_submodel_bindings: str = os.getenv("ROBOT_SUBMODEL_BINDINGS", "")
    orchestrator_log_csv_path: str = os.getenv("ORCHESTRATOR_LOG_CSV_PATH", str(Path(__file__).resolve().parent / "orchestrator_logs.csv"))
    orchestrator_summary_csv_path: str = os.getenv("ORCHESTRATOR_SUMMARY_CSV_PATH", str(Path(__file__).resolve().parent / "orchestrator_summary.csv"))
    summary_batch_size: int = int(os.getenv("SUMMARY_BATCH_SIZE", "5"))


@dataclass(frozen=True)
class RobotEndpoints:
    state_submodel_b64: str
    skills_submodel_b64: str
    station_id: str = ""


def normalize_submodel_id(submodel_id: str) -> str:
    return submodel_id.strip().replace("+", "-").replace("/", "_").rstrip("=")


def parse_bool_value(raw_payload: str) -> Optional[bool]:
    text = raw_payload.strip().lower()
    if text in {"true", "1", "on", "yes"}: return True
    if text in {"false", "0", "off", "no"}: return False

    try:
        parsed = json.loads(text)
        if isinstance(parsed, bool): return parsed
        if isinstance(parsed, (int, float)): return bool(parsed)
        if isinstance(parsed, dict):
            for key in ("value", "newValue", "payload"):
                if key in parsed and (val := parse_bool_value(str(parsed[key]))) is not None:
                    return val
    except json.JSONDecodeError:
        pass
    return None


class FactoryOrchestrator:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(self.config.http_timeout_seconds))
        self.job_queue = asyncio.Queue()
        self.dispatch_queue = asyncio.Queue()
        self.active_jobs = set()
        self.sensor_states: dict[str, bool] = {}
        self.sensor_last_true_at: dict[str, float] = {}
        self.sensor_waiting_for_clear: dict[str, bool] = {}
        self.station_lifecycles: dict[str, dict] = {}
        self.lifecycle_by_request_id: dict[str, dict] = {}
        self.pending_operation_acks: dict[str, dict] = {}
        self.log_lock = asyncio.Lock()
        self.log_headers = [
            "station_id",
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
        self.station_by_conveyor_submodel = self._load_station_bindings(self.config.station_bindings_file)
        self._ensure_csv_file(self.log_path, self.log_headers, check_headers=True)
        self._ensure_csv_file(self.summary_path, self.summary_headers)
        self.robots = self._build_robot_endpoints(config)
        if not self.robots:
            print("[ORCHESTRATOR] Warning: no robot bindings configured; dispatch cannot start")


    async def _retry_job_later(self, job: dict) -> None:
        await asyncio.sleep(self.config.job_retry_seconds)
        await self.job_queue.put(job)

    def _ensure_csv_file(self, path: Path, headers: list[str], check_headers: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 0:
            if not check_headers: return
            try:
                with path.open("r", encoding="utf-8") as f:
                    if f.readline().strip() == ",".join(headers): return
                print(f"[ORCHESTRATOR] Resetting outdated header in {path.name}")
            except OSError:
                pass
        with path.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=headers).writeheader()
    
    async def close(self):
        await self.http_client.aclose()

    async def _append_log_row(self, row: dict) -> None:
        async with self.log_lock:
            with self.log_path.open("a", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=self.log_headers).writerow({k: row.get(k, "") for k in self.log_headers})
    
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

        print(f"[ORCHESTRATOR] Logged summary batch #{self.summary_batch_id} to {self.summary_path}")

    async def _log_and_print(self, row: dict) -> None:
        await self._append_log_row(row)
        await self._append_summary_row_if_ready(row)
        print(f"[ORCHESTRATOR] Logged run #{row.get('run_id')} status={row.get('status')} sensor={row.get('sensor')}")

    def _build_robot_endpoints(self, config: AgentConfig) -> list[RobotEndpoints]:
        robots: list[RobotEndpoints] = []

        # Preferred source: file-based bindings for scalable multi-robot setups.
        robots.extend(self._load_robot_bindings_from_file(config.robot_bindings_file))
        if robots:
            return robots

        # Preferred format per robot: stateSubmodelId|skillsSubmodelId
        for chunk in config.robot_submodel_bindings.split(","):
            raw = chunk.strip()
            if not raw:
                continue

            state_id, skills_id = self._parse_robot_binding(raw)

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

    def _load_robot_bindings_from_file(self, file_path: str) -> list[RobotEndpoints]:
        if not file_path:
            return []

        path = Path(file_path)
        if not path.exists():
            print(f"[ORCHESTRATOR] Robot bindings file not found: {path}")
            return []

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[ORCHESTRATOR] Failed to read robot bindings file '{path}': {exc}")
            return []

        robots: list[RobotEndpoints] = []

        # Accept either list entries or map entries.
        if isinstance(data, list):
            entries = data
        elif isinstance(data, dict):
            entries = list(data.values())
        else:
            print(f"[ORCHESTRATOR] Robot bindings file has unsupported shape: {type(data).__name__}")
            return []

        for entry in entries:
            state_id: Optional[str] = None
            skills_id: Optional[str] = None
            station_id = ""

            if isinstance(entry, str):
                state_id, skills_id = self._parse_robot_binding(entry)
            elif isinstance(entry, dict):
                state_raw = str(entry.get("stateSubmodelB64", "")).strip()
                skills_raw = str(entry.get("skillsSubmodelB64", "")).strip()
                station_id = str(entry.get("stationId", "")).strip()
                if not state_raw and "binding" in entry:
                    state_id, skills_id = self._parse_robot_binding(str(entry.get("binding", "")))
                else:
                    state_id = normalize_submodel_id(state_raw)
                    skills_id = normalize_submodel_id(skills_raw if skills_raw else state_raw)

            if state_id and skills_id:
                robots.append(RobotEndpoints(state_submodel_b64=state_id, skills_submodel_b64=skills_id, station_id=station_id))

        print(f"[ORCHESTRATOR] Loaded {len(robots)} robot binding(s) from {path}")
        return robots

    def _load_station_bindings(self, file_path: str) -> dict[str, str]:
        if not file_path:
            return {}

        path = Path(file_path)
        if not path.exists():
            print(f"[ORCHESTRATOR] Station bindings file not found: {path}")
            return {}

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[ORCHESTRATOR] Failed to read station bindings file '{path}': {exc}")
            return {}

        bindings: dict[str, str] = {}
        if isinstance(data, dict):
            entries = data.items()
        elif isinstance(data, list):
            entries = enumerate(data)
        else:
            print(f"[ORCHESTRATOR] Station bindings file has unsupported shape: {type(data).__name__}")
            return {}

        for station_key, entry in entries:
            conveyor_submodel = self._extract_conveyor_submodel_id(entry)
            if not conveyor_submodel:
                continue
            station_id = self._infer_station_id(entry, conveyor_submodel, station_key)
            if station_id:
                bindings[normalize_submodel_id(conveyor_submodel)] = station_id

        print(f"[ORCHESTRATOR] Loaded {len(bindings)} station binding(s) from {path}")
        return bindings

    def _extract_conveyor_submodel_id(self, entry: object) -> str:
        if isinstance(entry, dict):
            if entry.get("conveyorSubmodelB64"):
                return str(entry.get("conveyorSubmodelB64")).strip()
            endpoint = str(entry.get("submodelEndpoint", "")).strip()
            if endpoint:
                parsed = urlparse(endpoint)
                parts = [part for part in parsed.path.split("/") if part]
                if "submodels" in parts:
                    index = parts.index("submodels")
                    if index + 1 < len(parts):
                        return parts[index + 1].strip()
        if isinstance(entry, str):
            return entry.strip()
        return ""

    def _infer_station_id(self, entry: object, conveyor_submodel: str, station_key: object) -> str:
        if isinstance(entry, dict):
            unique_id = str(entry.get("uniqueId", "")).strip().lower()
            endpoint = str(entry.get("submodelEndpoint", "")).strip().lower()

            if "station02" in endpoint or unique_id.endswith("_02") or "robot02" in endpoint or "conveyor02" in endpoint:
                return "station_02"
            if "station01" in endpoint or unique_id.endswith("_01") or "robot01" in endpoint or "conveyor01" in endpoint:
                return "station_01"

        if isinstance(station_key, int):
            # Current aasserver.json is ordered station_01 first, station_02 second.
            return "station_02" if station_key >= 4 else "station_01"

        text = str(station_key).strip().lower()
        if text in {"station_01", "station_02"}:
            return text

        return ""

    def _parse_robot_binding(self, raw: str) -> tuple[str, str]:
        if not (value:= raw.strip()):
            return "", ""

        if "|" in value:
            state_raw, skills_raw = value.split("|", 1)
            state_id = normalize_submodel_id(state_raw)
            skills_id = normalize_submodel_id(skills_raw)
            return state_id, skills_id

        # Backward-compatible form where one submodel hosts both state and skills.
        normalized = normalize_submodel_id(value)
        return normalized, normalized


    async def handle_event(self, submodel_b64: str, property_id: str, payload: str, mqtt_topic: str, received_at_ms: int) -> None:

        if "Present" not in property_id and "Clear" not in property_id: 
            return

        if (bool_value:=parse_bool_value(payload)) is None:
            print(
                f"[ORCHESTRATOR] Ignored sensor event {property_id}: "
                f"payload did not resolve to boolean ({payload})"
            )
            return

        sensor_key = f"{submodel_b64}:{property_id}"
        last_state = self.sensor_states.get(sensor_key)
        self.sensor_states[sensor_key] = bool_value

        station_id = self.station_by_conveyor_submodel.get(normalize_submodel_id(submodel_b64), "")

        if bool_value is False:
            lifecycle = self.station_lifecycles.get(normalize_station_id(station_id)) if station_id else None
            if lifecycle and lifecycle.get("sensor_key") == sensor_key:
                lifecycle["sensor_clear"] = True
                await self._try_finalize_lifecycle(lifecycle)
            elif self.sensor_waiting_for_clear.get(sensor_key):
                self.sensor_waiting_for_clear[sensor_key] = False
                print(f"[ORCHESTRATOR] Sensor '{property_id}' on Conveyor '{submodel_b64}' cleared; ready for next box detection")
            return

        lifecycle = self.station_lifecycles.get(normalize_station_id(station_id)) if station_id else None
        if lifecycle and lifecycle.get("sensor_key") == sensor_key:
            # A short false pulse must not rearm the station if the sensor is true again.
            lifecycle["sensor_clear"] = False

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
                "station_id": station_id,
                "sensor": property_id,
                "sensor_key": sensor_key,
                "token": job_token,
                "mqtt_topic": mqtt_topic,
                "t1_ms": received_at_ms,
                "deadline": time.monotonic() + self.config.job_timeout_seconds,
            })
            print(f"[ORCHESTRATOR] Enqueued event: Sensor '{property_id}' triggered on Conveyor '{submodel_b64}'")

    async def start_worker(self) -> None:
        while True:
            job = await self.job_queue.get()
            try:
                await self.process_factory_job(job)
            except Exception as e:
                traceback.print_exc()
                await self._log_and_print(
                    {
                        "run_id": job.get("run_id"), 
                        "status": "error_match", 
                        "conveyor_submodel_b64": job.get("conveyor_b64"), 
                        "sensor": job.get("sensor"), 
                        "mqtt_topic": job.get("mqtt_topic"), 
                        "t1_ms": job.get("t1_ms"), 
                        "notes": str(e)
                    }
                )
                if token := job.get("token"): self.active_jobs.discard(token)
            self.job_queue.task_done()

    async def start_dispatcher(self) -> None:
        while True:
            dispatch_job = await self.dispatch_queue.get()
            try:
                await self.dispatch_factory_job(dispatch_job)
            except Exception as e:
                print(f"[ERROR] Dispatch failed: {e}")
                await self._release_dispatch_job(dispatch_job, f"dispatch exception: {e}")
            self.dispatch_queue.task_done()

    async def _release_dispatch_job(self, dispatch_job: dict, reason: str) -> None:
        token = dispatch_job.get("token")
        sensor_key = dispatch_job.get("sensor_key")
        station_id = normalize_station_id(dispatch_job.get("station_id") or "")
        if token:
            self.active_jobs.discard(token)
        if sensor_key:
            self.sensor_waiting_for_clear[sensor_key] = False
        if station_id:
            lifecycle = self.station_lifecycles.pop(station_id, None)
            if lifecycle and lifecycle.get("request_id"):
                self.lifecycle_by_request_id.pop(lifecycle["request_id"], None)
        print(
            f"[ORCHESTRATOR] Released station lifecycle station={station_id or 'unknown'} "
            f"reason={reason}"
        )

    async def _try_finalize_lifecycle(self, lifecycle: dict) -> None:
        if not lifecycle.get("operation_completed") or not lifecycle.get("sensor_clear"):
            return

        station_id = lifecycle["station_id"]
        request_id = lifecycle.get("request_id")
        sensor_key = lifecycle.get("sensor_key")
        token = lifecycle.get("token")
        if token:
            self.active_jobs.discard(token)
        if sensor_key:
            self.sensor_waiting_for_clear[sensor_key] = False
        self.station_lifecycles.pop(station_id, None)
        if request_id:
            self.lifecycle_by_request_id.pop(request_id, None)
            self.pending_operation_acks.pop(request_id, None)
        print(
            f"[ORCHESTRATOR] Station '{station_id}' rearmed after operation completion "
            "and sensor clear"
        )

    async def handle_operation_ack(self, topic: str, payload: str) -> None:
        try:
            ack = json.loads(payload)
        except json.JSONDecodeError:
            print(f"[ORCHESTRATOR] Ignored malformed operation acknowledgement: {payload!r}")
            return
        if not isinstance(ack, dict):
            return

        request_id = str(ack.get("requestId") or "").strip()
        station_id = normalize_station_id(str(ack.get("stationId") or ""))
        status = str(ack.get("status") or "").strip().lower()
        if not request_id or not station_id or status not in {"started", "completed", "failed"}:
            print(f"[ORCHESTRATOR] Ignored incomplete operation acknowledgement: {ack}")
            return

        lifecycle = self.lifecycle_by_request_id.get(request_id)
        if lifecycle is None:
            # The MQTT acknowledgement can arrive before the HTTP invocation response.
            self.pending_operation_acks[request_id] = ack
            return

        if lifecycle["station_id"] != station_id:
            print(
                f"[ORCHESTRATOR] Ignored acknowledgement for request {request_id}: "
                f"station mismatch {station_id} != {lifecycle['station_id']}"
            )
            return

        if status == "started":
            lifecycle["operation_started"] = True
        elif status == "completed":
            lifecycle["operation_completed"] = True
            await self._try_finalize_lifecycle(lifecycle)
        else:
            lifecycle["operation_failed"] = True
            await self._release_dispatch_job(lifecycle, ack.get("error") or "operation failed")

    async def _read_is_moving(
        self,
        client: httpx.AsyncClient,
        state_url: str,
    ) -> Optional[bool]:
        try:
            response = await client.get(f"{state_url}/submodel-elements/IsMoving")

            if response.status_code != 200:
                print(
                    "[ORCHESTRATOR] Robot state read returned "
                    f"HTTP {response.status_code}"
                )
                return None

            value = parse_bool_value(response.text)
            if value is None:
                print(f"[ORCHESTRATOR] Invalid IsMoving value: {response.text!r}")
            return value

        except Exception as exc:
            print(f"[ORCHESTRATOR] Error reading robot state: {exc}")
            return None

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

                # After a grace period, repeated false readings indicate robot is idle.
                if (not saw_moving and asyncio.get_running_loop().time() >= grace_until and consecutive_not_moving >= 3):
                    return True

            await asyncio.sleep(self.config.robot_status_poll_seconds)

        return False
    
    async def _manage_robot_cooldown(self, robot: RobotEndpoints, state_url: str) -> None:
        """Monitors a specific robot's motion in the background without halting the factory queue"""
        settled = await self._wait_for_motion_settle(self.http_client, state_url)

        if not settled:
            print(f"[ORCHESTRATOR] Warning: Robot {robot.state_submodel_b64} motion did not settle within timeout.")
        else:
            print(f"[ORCHESTRATOR] Robot {robot.state_submodel_b64} has successfully completed motion and settled.")

    async def process_factory_job(self, job: dict) -> None:
        triggering_sensor = job["sensor"]
        triggering_sensor_key = job.get("sensor_key")
        job_station_id = job.get("station_id") or ""
        client = self.http_client

        deadline = job.setdefault(
            "deadline",
            time.monotonic() + self.config.job_timeout_seconds,
        )

        for robot in self.robots:
            if job_station_id and robot.station_id and normalize_station_id(robot.station_id) != normalize_station_id(job_station_id):
                continue

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
            target_op = None
            for route in routes:
                if not isinstance(route, dict):
                    continue

                route_values = route.get("value", [])
                if not isinstance(route_values, list):
                    continue

                elements = {
                    element["idShort"]: element.get("value")
                    for element in route_values
                    if isinstance(element, dict) and element.get("idShort")
                }

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

            request_id = str(uuid.uuid4())
            input_arguments.append({
                "value": {
                    "modelType": "Property",
                    "idShort": "requestId",
                    "valueType": "xs:string",
                    "value": request_id,
                }
            })
            body = {
                "inputArguments": input_arguments,
                "inoutputArguments": [],
                "requestedTimeout": int(self.config.http_timeout_seconds * 1000)
            }
            t2_ms = int(time.time() * 1000)
            dispatch_payload = {
                "run_id": job.get("run_id"),
                "token": job.get("token"),
                "conveyor_b64": job.get("conveyor_b64"),
                "station_id": job_station_id,
                "sensor": triggering_sensor,
                "sensor_key": triggering_sensor_key,
                "mqtt_topic": job.get("mqtt_topic"),
                "t1_ms": job.get("t1_ms"),
                "t2_ms": t2_ms,
                "robot_state_submodel_b64": robot.state_submodel_b64,
                "robot_skills_submodel_b64": robot.skills_submodel_b64,
                "target_operation": target_op,
                "invoke_url": f"{skills_url}/submodel-elements/{target_op}/invoke",
                "body": body,
                "request_id": request_id,
            }
            await self.dispatch_queue.put(dispatch_payload)
            print(
                f"[ORCHESTRATOR] Match completed for run #{job.get('run_id')}; "
                f"queued for dispatch (sensor={triggering_sensor}, robot={robot.skills_submodel_b64})"
            )
            return
        
        if time.monotonic() < deadline:
            print(
                f"[ORCHESTRATOR] Robot unavailable for {triggering_sensor}; "
                f"retrying in {self.config.job_retry_seconds:.1f}s"
            )
            asyncio.create_task(self._retry_job_later(job))
            return

        print(
            f"[ORCHESTRATOR] Job for {triggering_sensor} timed out after "
            f"{self.config.job_timeout_seconds:.1f}s"
        )
        self.active_jobs.discard(job["token"])

    async def dispatch_factory_job(self, dispatch_job: dict) -> None:
        t3_ms = int(time.time() * 1000)

        print(
            f"[ORCHESTRATOR] Dispatching run #{dispatch_job.get('run_id')} "
            f"skill '{dispatch_job.get('target_operation')}' to robot {dispatch_job.get('robot_skills_submodel_b64')}"
        )

        request_id = dispatch_job["request_id"]
        station_id = normalize_station_id(dispatch_job.get("station_id") or "")
        sensor_key = dispatch_job.get("sensor_key")
        lifecycle = {
            **dispatch_job,
            "station_id": station_id,
            "request_id": request_id,
            "operation_started": False,
            "operation_completed": False,
            "operation_failed": False,
            "sensor_clear": self.sensor_states.get(sensor_key) is False,
        }
        self.station_lifecycles[station_id] = lifecycle
        self.lifecycle_by_request_id[request_id] = lifecycle
        if sensor_key:
            self.sensor_waiting_for_clear[sensor_key] = True
        print(
            f"[ORCHESTRATOR] Station '{station_id}' latched for request {request_id}; "
            "waiting for operation completion and sensor clear"
        )

        response = None
        attempts = max(1, self.config.invoke_retry_count)
        for attempt in range(1, attempts + 1):
            try:
                response = await self.http_client.post(
                    dispatch_job["invoke_url"],
                    json=dispatch_job["body"],
                )
                break
            except httpx.HTTPError as exc:
                print(
                    f"[ORCHESTRATOR] Robot invocation failed "
                    f"(attempt {attempt}/{attempts}): {exc}"
                )

        if response is None:
            await self._release_dispatch_job(dispatch_job, "operation invocation produced no response")
            return

        print(f"[ORCHESTRATOR] Response status from robot: {response.status_code}")

        t1_ms = dispatch_job.get("t1_ms")
        t2_ms = dispatch_job.get("t2_ms")

        await self._log_and_print(
            {
                "run_id": dispatch_job.get("run_id"),
                "status": "ok" if response.status_code < 400 else "invoke_http_error",
                "station_id": dispatch_job.get("station_id") or "",
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
            pending_ack = self.pending_operation_acks.pop(request_id, None)
            if pending_ack:
                await self.handle_operation_ack("", json.dumps(pending_ack))

            state_url = f"{self.config.basyx_base_url}/submodels/{dispatch_job.get('robot_state_submodel_b64')}"
            robot = RobotEndpoints(
                state_submodel_b64=dispatch_job.get("robot_state_submodel_b64"),
                skills_submodel_b64=dispatch_job.get("robot_skills_submodel_b64"),
                station_id=dispatch_job.get("station_id") or "",
            )
            asyncio.create_task(self._manage_robot_cooldown(robot, state_url))
            await self._try_finalize_lifecycle(lifecycle)
        else:
            await self._release_dispatch_job(
                dispatch_job,
                f"operation invocation returned HTTP {response.status_code}",
            )


def parse_topic(topic: str) -> Optional[tuple[str, str]]:
    p = topic.split("/")
    if len(p) >= 7 and p[0] == "sm-repository" and p[2] == "submodels" and p[4] == "submodelElements" and p[6] == "updated":
        return p[3], p[5]
    return None


def is_operation_reply_topic(topic: str) -> bool:
    parts = topic.split("/")
    return (
        len(parts) == 4
        and parts[0] == "simulation"
        and parts[2] == "replies"
        and bool(parts[1])
        and bool(parts[3])
    )


def normalize_station_id(station_id: str) -> str:
    return station_id.strip().lower()


async def run_agent(config: AgentConfig) -> None:
    orchestrator = FactoryOrchestrator(config)
    asyncio.create_task(orchestrator.start_worker())
    asyncio.create_task(orchestrator.start_dispatcher())

    try:
        while True:
            try:
                async with MqttClient(hostname=config.mqtt_host, port=config.mqtt_port) as client:
                    await client.subscribe(config.mqtt_topic)
                    await client.subscribe(config.operation_reply_topic)
                    print(
                        "[AGENT] Connected to MQTT broker "
                        f"{config.mqtt_host}:{config.mqtt_port}, subscribed to "
                        f"{config.mqtt_topic} and {config.operation_reply_topic}"
                    )

                    async for message in client.messages:
                        topic = str(message.topic)
                        payload = message.payload.decode(errors="replace")
                        if is_operation_reply_topic(topic):
                            await orchestrator.handle_operation_ack(topic, payload)
                            continue

                        parsed = parse_topic(topic)
                        if parsed is not None:
                            submodel_b64, property_id = parsed
                            received_at_ms = int(time.time() * 1000)
                            # Pass events to the event tracker safely
                            await orchestrator.handle_event(
                                submodel_b64,
                                property_id,
                                payload,
                                topic,
                                received_at_ms,
                            )

            except MqttError as exc:
                print(f"[AGENT] MQTT connection error: {exc}. Reconnecting in 3s...")
                await asyncio.sleep(3)
    finally:
        await orchestrator.close()


async def main() -> None:
    config = AgentConfig()
    print("[AGENT] Starting factory orchestration agent...")
    await run_agent(config)


if __name__ == "__main__":
    asyncio.run(main())
