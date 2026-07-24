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


@dataclass(frozen=True)
class AgentConfig:
    mqtt_host: str = os.getenv("MQTT_HOST", "mosquitto")
    mqtt_port: int = int(os.getenv("MQTT_PORT", "1883"))
    mqtt_topic: str = os.getenv("MQTT_TOPIC", "sm-repository/+/submodels/+/submodelElements/+/updated")
    operation_reply_topic: str = os.getenv("OPERATION_REPLY_TOPIC", "simulation/+/replies/+")
    basyx_base_url: str = os.getenv("BASYX_BASE_URL", "http://aas-env:8081") 
    http_timeout_seconds: float = float(os.getenv("HTTP_TIMEOUT_SECONDS", "8"))
    job_retry_seconds: float = float(os.getenv("JOB_RETRY_SECONDS", "0.5"))
    job_timeout_seconds: float = float(os.getenv("JOB_TIMEOUT_SECONDS", "60"))
    invoke_retry_count: int = int(os.getenv("INVOKE_RETRY_COUNT", "3"))
    station_registry_file: str = os.getenv("STATION_REGISTRY_FILE", "")
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
        self.sensor_waiting_for_clear: dict[str, bool] = {}
        self.station_lifecycles: dict[str, dict] = {}
        self.lifecycle_by_request_id: dict[str, dict] = {}
        self.log_lock = asyncio.Lock()
        self.log_headers = [
            "status",
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
        station_registry = self._load_station_registry(self.config.station_registry_file)
        self.station_by_conveyor_submodel = self._build_station_bindings(station_registry)
        self._ensure_csv_file(self.log_path, self.log_headers, check_headers=True)
        self._ensure_csv_file(self.summary_path, self.summary_headers)
        self.robots = self._build_robot_endpoints(station_registry)
        self.reserved_robots: set[str] = set()
        self.reserved_stations: set[str] = set()
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
        for lifecycle in list(self.lifecycle_by_request_id.values()):
            await self._release_dispatch_job(lifecycle, "orchestrator shutdown")
        self.reserved_robots.clear()
        self.reserved_stations.clear()
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
        print(f"[ORCHESTRATOR] Logged run # status={row.get('status')} sensor={row.get('sensor')}")

    def _load_station_registry(self, file_path: str) -> dict[str, dict]:
        if not file_path:
            return {}

        path = Path(file_path)
        if not path.exists():
            print(f"[ORCHESTRATOR] Station registry not found: {path}")
            return {}

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[ORCHESTRATOR] Failed to read station registry '{path}': {exc}")
            return {}

        station_entries = data.get("stations") if isinstance(data, dict) else None
        if not isinstance(station_entries, dict):
            print("[ORCHESTRATOR] Station registry must contain a 'stations' object")
            return {}

        registry = {
            str(station_key): entry
            for station_key, entry in station_entries.items()
            if isinstance(entry, dict)
        }
        print(f"[ORCHESTRATOR] Loaded {len(registry)} station registry entry(s) from {path}")
        return registry

    def _build_robot_endpoints(
        self, station_registry: dict[str, dict]
    ) -> list[RobotEndpoints]:
        robots: list[RobotEndpoints] = []
        seen: set[tuple[str, str]] = set()
        for station_key, entry in station_registry.items():
            state_id = normalize_submodel_id(
                str(entry.get("robotStateSubmodelB64", ""))
            )
            skills_id = normalize_submodel_id(
                str(entry.get("robotSkillsSubmodelB64", ""))
            )
            robot_key = (state_id, skills_id)
            if not state_id or not skills_id or robot_key in seen:
                continue
            seen.add(robot_key)
            robots.append(
                RobotEndpoints(
                    state_submodel_b64=state_id,
                    skills_submodel_b64=skills_id,
                    station_id=str(entry.get("stationId", station_key)).strip(),
                )
            )
        return robots

    def _build_station_bindings(
        self, station_registry: dict[str, dict]
    ) -> dict[str, str]:
        bindings: dict[str, str] = {}
        for station_key, entry in station_registry.items():
            conveyor_submodel = normalize_submodel_id(
                str(entry.get("conveyorSubmodelB64", ""))
            )
            station_id = str(entry.get("stationId", station_key)).strip()
            if conveyor_submodel and station_id:
                bindings[conveyor_submodel] = station_id
        return bindings


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
        self.sensor_states[sensor_key] = bool_value

        station_id = self.station_by_conveyor_submodel.get(normalize_submodel_id(submodel_b64), "")

        if not station_id:
            print(
                f"[ORCHESTRATOR] Ignored sensor {property_id}: "
                f"no station binding for conveyor {submodel_b64}"
            )
            return

        if bool_value is False:
            lifecycle = self.station_lifecycles.get(normalize_station_id(station_id)) if station_id else None
            if lifecycle and lifecycle.get("sensor_key") == sensor_key:
                lifecycle["sensor_clear"] = True
                await self._try_finalize_lifecycle(lifecycle)
            elif self.sensor_waiting_for_clear.get(sensor_key):
                self.sensor_waiting_for_clear[sensor_key] = False
                print(f"[ORCHESTRATOR] Sensor '{property_id}' on Conveyor '{submodel_b64}' cleared; ready for next box detection")
            return

        lifecycle = self.station_lifecycles.get(normalize_station_id(station_id))

        if lifecycle and lifecycle.get("sensor_key") == sensor_key:
            lifecycle["sensor_clear"] = False


        if self.sensor_waiting_for_clear.get(sensor_key):
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
            finally:
                self.dispatch_queue.task_done()

    async def _expire_lifecycle(self, request_id: str) -> None:
        await asyncio.sleep(self.config.job_timeout_seconds)
        lifecycle = self.lifecycle_by_request_id.get(request_id)
        if lifecycle is not None:
            await self._release_dispatch_job(
                lifecycle,
                f"operation lifecycle timed out after {self.config.job_timeout_seconds:.1f}s",
            )

    async def _release_dispatch_job(self, dispatch_job: dict, reason: str) -> None:
        token = dispatch_job.get("token")
        sensor_key = dispatch_job.get("sensor_key")
        station_id = normalize_station_id(dispatch_job.get("station_id") or "")
        robot_key = dispatch_job.get("robot_key")
        if token:
            self.active_jobs.discard(token)
        if sensor_key:
            self.sensor_waiting_for_clear[sensor_key] = False
        if robot_key:
            self.reserved_robots.discard(robot_key)
        if station_id:
            self.reserved_stations.discard(station_id)
            lifecycle = self.station_lifecycles.pop(station_id, None)
            if lifecycle and lifecycle.get("request_id"):
                self.lifecycle_by_request_id.pop(lifecycle["request_id"], None)
                timeout_task = lifecycle.get("timeout_task")
                if timeout_task and timeout_task is not asyncio.current_task():
                    timeout_task.cancel()
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
        robot_key = lifecycle.get("robot_key")
        if token:
            self.active_jobs.discard(token)
        if sensor_key:
            self.sensor_waiting_for_clear[sensor_key] = False
        if robot_key:
            self.reserved_robots.discard(robot_key)
        if station_id:
            self.reserved_stations.discard(station_id)
        self.station_lifecycles.pop(station_id, None)
        if request_id:
            self.lifecycle_by_request_id.pop(request_id, None)
        timeout_task = lifecycle.get("timeout_task")
        if timeout_task and timeout_task is not asyncio.current_task():
            timeout_task.cancel()
        print(
            f"[ORCHESTRATOR] Station '{station_id}' rearmed after operation completion "
            "and sensor clear"
        )

    async def handle_operation_ack(self, payload: str) -> None:
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
            print(f"[ORCHESTRATOR] Ignored acknowledgement for unknown request {request_id}")
            return

        if lifecycle["station_id"] != station_id:
            print(
                f"[ORCHESTRATOR] Ignored acknowledgement for request {request_id}: "
                f"station mismatch {station_id} != {lifecycle['station_id']}"
            )
            return

        if status == "started":
            return
        elif status == "completed":
            lifecycle["operation_completed"] = True
            await self._try_finalize_lifecycle(lifecycle)
        else:
            await self._release_dispatch_job(lifecycle, ack.get("error") or "operation failed")

    async def _read_robot_bool_state(
        self,
        client: httpx.AsyncClient,
        state_url: str,
        property_id: str,
    ) -> Optional[bool]:
        try:
            response = await client.get(f"{state_url}/submodel-elements/{property_id}")

            if response.status_code != 200:
                print(
                    f"[ORCHESTRATOR] Robot state {property_id} read returned "
                    f"HTTP {response.status_code}"
                )
                return None

            value = parse_bool_value(response.text)
            if value is None:
                print(f"[ORCHESTRATOR] Invalid {property_id} value: {response.text!r}")
            return value

        except Exception as exc:
            print(f"[ORCHESTRATOR] Error reading robot state {property_id}: {exc}")
            return None

    def _parse_capability_route(self, route: object) -> Optional[dict]:
        if not isinstance(route, dict):
            return None

        route_values = route.get("value", [])
        if not isinstance(route_values, list):
            return None

        properties = {
            element["idShort"]: element
            for element in route_values
            if isinstance(element, dict) and element.get("idShort")
        }
        return {
            "route_id": str(route.get("idShort") or "").strip(),
            "StationId": str(properties.get("StationId", {}).get("value") or "").strip(),
            "TriggerSensor": str(properties.get("TriggerSensor", {}).get("value") or "").strip(),
            "TargetOperation": str(properties.get("TargetOperation", {}).get("value") or "").strip(),
            "SourcePosition": str(properties.get("SourcePosition", {}).get("value") or "").strip(),
            "TargetPosition": str(properties.get("TargetPosition", {}).get("value") or "").strip(),
            "properties": properties,
        }

    def _build_operation_inputs(self, selected_route: dict) -> dict[str, dict]:
        target_operation = selected_route["TargetOperation"]
        if target_operation == "ExecuteMoveBox":
            return {
                id_short: {
                    "value": selected_route[id_short],
                    "valueType": selected_route["properties"].get(id_short, {}).get("valueType", "xs:string"),
                }
                for id_short in ("StationId", "SourcePosition", "TargetPosition")
            }

        # MoveToHome and other operations retain their advertised input contract.
        # StationId selects the route but is not added to a fixed-home operation.
        return {
            id_short: {
                "value": element.get("value"),
                "valueType": element.get("valueType", "xs:string"),
            }
            for id_short, element in selected_route["properties"].items()
            if id_short not in {"StationId", "TriggerSensor", "TargetOperation"}
        }


    async def process_factory_job(self, job: dict) -> None:
        triggering_sensor = job["sensor"]
        triggering_sensor_key = job.get("sensor_key")
        job_station_id = job.get("station_id") or ""
        normalized_job_station_id = normalize_station_id(job_station_id)
        required_operation = str(
            job.get("required_operation") or job.get("target_operation") or ""
        ).strip()
        client = self.http_client

        deadline = job.setdefault(
            "deadline",
            time.monotonic() + self.config.job_timeout_seconds,
        )

        for robot in self.robots:
            robot_key = robot.state_submodel_b64
            robot_id = robot.skills_submodel_b64
            print(
                "[ORCHESTRATOR] Considering robot "
                f"{robot_id} home_station={robot.station_id or 'unspecified'} "
                f"requested_station={job_station_id or 'missing'} sensor={triggering_sensor}"
            )
            locally_reserved = robot_key in self.reserved_robots

            state_url = f"{self.config.basyx_base_url}/submodels/{robot.state_submodel_b64}"
            skills_url = f"{self.config.basyx_base_url}/submodels/{robot.skills_submodel_b64}"
            # 1. Semantic discovery: route station is authoritative. The configured
            # robot station is metadata and never excludes a cross-station route.
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

            # 2. Match station, sensor, and (when supplied) required operation.
            selected_route = None
            for route in routes:
                parsed_route = self._parse_capability_route(route)
                if parsed_route is None:
                    print(
                        f"[ORCHESTRATOR] Rejected malformed capability route on robot {robot_id}"
                    )
                    continue

                route_id = parsed_route["route_id"] or "<unnamed>"
                route_station_id = parsed_route["StationId"]
                print(
                    f"[ORCHESTRATOR] Robot {robot_id} route={route_id} "
                    f"advertised_station={route_station_id or 'missing'} "
                    f"requested_station={job_station_id or 'missing'} "
                    f"source={parsed_route['SourcePosition'] or 'missing'} "
                    f"target={parsed_route['TargetPosition'] or 'missing'} "
                    f"operation={parsed_route['TargetOperation'] or 'missing'}"
                )

                if not route_station_id:
                    print(
                        f"[ORCHESTRATOR] Rejected route {route_id} on robot {robot_id}: "
                        "StationId is missing or blank"
                    )
                    continue

                if normalize_station_id(route_station_id) != normalized_job_station_id:
                    print(
                        f"[ORCHESTRATOR] Rejected route {route_id} on robot {robot_id}: "
                        f"station {route_station_id} does not match requested {job_station_id or 'missing'}"
                    )
                    continue

                if parsed_route["TriggerSensor"] != triggering_sensor:
                    print(
                        f"[ORCHESTRATOR] Rejected route {route_id} on robot {robot_id}: "
                        f"sensor {parsed_route['TriggerSensor'] or 'missing'} does not match {triggering_sensor}"
                    )
                    continue

                target_op = parsed_route["TargetOperation"]
                if not target_op:
                    print(
                        f"[ORCHESTRATOR] Rejected route {route_id} on robot {robot_id}: "
                        "TargetOperation is missing"
                    )
                    continue

                if required_operation and target_op != required_operation:
                    print(
                        f"[ORCHESTRATOR] Rejected route {route_id} on robot {robot_id}: "
                        f"operation {target_op} does not match required {required_operation}"
                    )
                    continue

                if target_op == "ExecuteMoveBox" and (
                    not parsed_route["SourcePosition"] or not parsed_route["TargetPosition"]
                ):
                    print(
                        f"[ORCHESTRATOR] Rejected route {route_id} on robot {robot_id}: "
                        "ExecuteMoveBox requires SourcePosition and TargetPosition"
                    )
                    continue

                selected_route = parsed_route
                break

            if selected_route is None:
                print(
                    f"[ORCHESTRATOR] Rejected robot {robot_id}: no matching station-aware route"
                )
                continue

            if locally_reserved or robot_key in self.reserved_robots:
                print(
                    f"[ORCHESTRATOR] Rejected robot {robot_id}: "
                    f"local reservation already exists for {robot_key}"
                )
                continue

            # 3. Robot state and fault checks.
            moving = await self._read_robot_bool_state(client, state_url, "IsMoving")
            if moving is not False:
                print(
                    f"[ORCHESTRATOR] Rejected robot {robot_id}: "
                    f"IsMoving is {moving!r}, expected False"
                )
                continue

            fault_active = await self._read_robot_bool_state(client, state_url, "FaultActive")
            if fault_active is not False:
                print(
                    f"[ORCHESTRATOR] Rejected robot {robot_id}: "
                    f"FaultActive is {fault_active!r}, expected False"
                )
                continue

            target_op = selected_route["TargetOperation"]
            print(
                f"[ORCHESTRATOR] Selected route {selected_route['route_id'] or '<unnamed>'} "
                f"on robot {robot_id}: station={selected_route['StationId']} "
                f"source={selected_route['SourcePosition'] or 'n/a'} "
                f"target={selected_route['TargetPosition'] or 'n/a'} operation={target_op}"
            )

            # 4. Generate operation arguments from the selected route.
            operation_inputs = self._build_operation_inputs(selected_route)
            input_arguments = [
                {
                    "value": {
                        "modelType": "Property",
                        "idShort": id_short,
                        "valueType": details["valueType"],
                        "value": details["value"],
                    }
                }
                for id_short, details in operation_inputs.items()
            ]

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
            selected_station_id = selected_route["StationId"]
            normalized_station_id = normalize_station_id(selected_station_id)

            # No await is allowed between these checks and additions. This makes
            # the local reservation atomic with respect to other asyncio jobs.
            if robot_key in self.reserved_robots:
                print(
                    f"[ORCHESTRATOR] Rejected robot {robot_id}: "
                    "reservation was acquired by another job during discovery"
                )
                continue
            if normalized_station_id and normalized_station_id in self.reserved_stations:
                print(
                    f"[ORCHESTRATOR] Rejected route on robot {robot_id}: "
                    f"station {selected_station_id} is already reserved"
                )
                continue

            self.reserved_robots.add(robot_key)
            if normalized_station_id:
                self.reserved_stations.add(normalized_station_id)
            dispatch_payload = {
                "token": job.get("token"),
                "station_id": selected_station_id,
                "source_position": selected_route["SourcePosition"],
                "target_position": selected_route["TargetPosition"],
                "sensor": triggering_sensor,
                "sensor_key": triggering_sensor_key,
                "t1_ms": job.get("t1_ms"),
                "t2_ms": t2_ms,
                "robot_skills_submodel_b64": robot.skills_submodel_b64,
                "robot_key": robot_key,
                "target_operation": target_op,
                "selected_route": selected_route["route_id"],
                "operation_inputs": {
                    id_short: details["value"]
                    for id_short, details in operation_inputs.items()
                },
                "invoke_url": f"{skills_url}/submodel-elements/{target_op}/invoke",
                "body": body,
                "request_id": request_id,
            }
            try:
                await self.dispatch_queue.put(dispatch_payload)
            except BaseException:
                self.reserved_robots.discard(robot_key)
                if normalized_station_id:
                    self.reserved_stations.discard(normalized_station_id)
                raise
            print(
                f"[ORCHESTRATOR] Reserved robot {robot_id} as {robot_key}; "
                f"queued operation={target_op} inputs={dispatch_payload['operation_inputs']}"
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
        try:
            await self._dispatch_factory_job(dispatch_job)
        except asyncio.CancelledError:
            await self._release_dispatch_job(dispatch_job, "dispatch cancelled")
            raise
        except Exception as exc:
            await self._release_dispatch_job(
                dispatch_job,
                f"dispatch exception: {exc}",
            )
            raise

    async def _dispatch_factory_job(self, dispatch_job: dict) -> None:
        t3_ms = int(time.time() * 1000)

        print(
            "[ORCHESTRATOR] Dispatching "
            f"operation={dispatch_job.get('target_operation')} "
            f"robot={dispatch_job.get('robot_skills_submodel_b64')} "
            f"route={dispatch_job.get('selected_route') or 'unknown'} "
            f"inputs={dispatch_job.get('operation_inputs', {})}"
        )

        request_id = dispatch_job["request_id"]
        station_id = normalize_station_id(dispatch_job.get("station_id") or "")

        if not station_id:
            await self._release_dispatch_job(dispatch_job, "station_id is missing")
            return
        
        sensor_key = dispatch_job.get("sensor_key")
        lifecycle = {
            **dispatch_job,
            "station_id": station_id,
            "request_id": request_id,
            "operation_completed": False,
            "sensor_clear": self.sensor_states.get(sensor_key) is False,
        }
        self.station_lifecycles[station_id] = lifecycle
        self.lifecycle_by_request_id[request_id] = lifecycle
        lifecycle["timeout_task"] = asyncio.create_task(
            self._expire_lifecycle(request_id)
        )
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

                if response.status_code < 500:
                    break

                print(
                    f"[ORCHESTRATOR] Robot invocation returned "
                    f"HTTP {response.status_code} "
                    f"(attempt {attempt}/{attempts})"
                )

                if attempt < attempts:
                    response = None

            except httpx.HTTPError as exc:
                response = None
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
                "status": (
                    "ok"
                    if response.status_code < 400
                    else "error_invoke"
                ),
                "station_id": dispatch_job.get("station_id") or "",
                "sensor": dispatch_job.get("sensor"),
                "t1_ms": t1_ms,
                "t2_ms": t2_ms,
                "t3_ms": t3_ms,
            }
        )

        if response.status_code < 400:
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
                            await orchestrator.handle_operation_ack(payload)
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
