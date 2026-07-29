import asyncio
import csv
import time
import uuid
from pathlib import Path
from statistics import mean
from typing import Optional

import httpx
import traceback

from aas_access import (
    fetch_supported_capabilities,
    invoke_operation,
    read_robot_bool_state,
)
from capability_matching import (
    build_operation_inputs,
    match_capability_route,
    parse_capability_route,
)
from config_models import (
    AgentConfig,
    RobotEndpoints,
    build_robot_endpoints,
    build_station_bindings,
    load_station_registry,
    normalize_station_id,
    normalize_submodel_id,
    parse_bool_value,
)
from lifecycle import LifecycleCoordinator


class FactoryOrchestrator:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(self.config.http_timeout_seconds))
        self.job_queue = asyncio.Queue()
        self.dispatch_queue = asyncio.Queue()
        self.run_id = self.config.measurement_run_id.strip() or str(uuid.uuid4())
        self.lifecycle = LifecycleCoordinator(self.config.job_timeout_seconds)
        # Compatibility aliases keep the existing public surface while lifecycle
        # state and transitions are owned by LifecycleCoordinator.
        self.active_jobs = self.lifecycle.active_jobs
        self.sensor_states = self.lifecycle.sensor_states
        self.sensor_waiting_for_clear = self.lifecycle.sensor_waiting_for_clear
        self.station_lifecycles = self.lifecycle.station_lifecycles
        self.lifecycle_by_request_id = self.lifecycle.lifecycle_by_request_id
        self.reserved_robots = self.lifecycle.reserved_robots
        self.reserved_stations = self.lifecycle.reserved_stations
        self.log_lock = asyncio.Lock()
        self.logged_samples = 0
        self.log_headers = [
            "request_id",
            "run_id",
            "station_id",
            "sample",
            "t1_ms",
            "t2_ms",
            "t3_ms",
            "status",
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
        ]
        self.summary_batch_id = 0
        self.summary_buffer: list[dict] = []
        self.log_path = Path(self.config.orchestrator_log_csv_path)
        self.summary_path = Path(self.config.orchestrator_summary_csv_path)
        station_registry = load_station_registry(self.config.station_registry_file)
        self.station_by_conveyor_submodel = build_station_bindings(station_registry)
        self._ensure_csv_file(self.log_path, self.log_headers, check_headers=True)
        self._ensure_csv_file(self.summary_path, self.summary_headers)
        self.robots = build_robot_endpoints(station_registry)
        if not self.robots:
            print("[ORCHESTRATOR] Warning: no robot bindings configured; dispatch cannot start")
        print(f"[ORCHESTRATOR] Measurement run_id={self.run_id}")


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
                raise RuntimeError(
                    f"Unexpected CSV header in {path}. Move or rename the "
                    "previous log before starting a new run."
                )
            except OSError:
                pass
        with path.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=headers).writeheader()
    
    async def close(self):
        await self.lifecycle.close()
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
            }

            with self.summary_path.open("a", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=self.summary_headers)
                writer.writerow(summary_row)

        print(f"[ORCHESTRATOR] Logged summary batch #{self.summary_batch_id} to {self.summary_path}")

    async def _log_and_print(self, row: dict) -> None:
        await self._append_log_row(row)
        await self._append_summary_row_if_ready(row)
        print(f"[ORCHESTRATOR] Logged run # status={row.get('status')} sensor={row.get('sensor')}")

    async def _log_request_status(self, request: dict, status: str) -> None:
        if request.get("latency_logged"):
            return
        self.logged_samples += 1
        row = {
            "request_id": request.get("request_id"),
            "run_id": request.get("run_id") or self.run_id,
            "station_id": request.get("station_id") or "",
            "sample": self.logged_samples,
            "t1_ms": request.get("t1_ms"),
            "t2_ms": request.get("t2_ms"),
            "t3_ms": request.get("t3_ms"),
            "status": status,
        }
        if status == "ok":
            timestamps = [
                self._safe_float(row[key])
                for key in ("t1_ms", "t2_ms", "t3_ms")
            ]
            if (
                not all(
                    row.get(key) not in (None, "")
                    for key in self.log_headers
                )
                or any(value is None for value in timestamps)
                or not timestamps[0] <= timestamps[1] <= timestamps[2]
            ):
                row["status"] = "failed"
        await self._log_and_print(row)
        request["latency_logged"] = True

    def _load_station_registry(self, file_path: str) -> dict[str, dict]:
        return load_station_registry(file_path)

    def _build_robot_endpoints(
        self, station_registry: dict[str, dict]
    ) -> list[RobotEndpoints]:
        return build_robot_endpoints(station_registry)

    def _build_station_bindings(
        self, station_registry: dict[str, dict]
    ) -> dict[str, str]:
        return build_station_bindings(station_registry)


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
            request_id = str(uuid.uuid4())
            await self.job_queue.put({
                "conveyor_b64": submodel_b64,
                "station_id": station_id,
                "sensor": property_id,
                "sensor_key": sensor_key,
                "token": job_token,
                "mqtt_topic": mqtt_topic,
                "request_id": request_id,
                "run_id": self.run_id,
                "t1_ms": received_at_ms,
                "deadline": time.monotonic() + self.config.job_timeout_seconds,
            })
            print(
                f"[ORCHESTRATOR] Enqueued event request_id={request_id}: "
                f"Sensor '{property_id}' triggered on Conveyor '{submodel_b64}'"
            )

    async def start_worker(self) -> None:
        while True:
            job = await self.job_queue.get()
            try:
                await self.process_factory_job(job)
            except Exception as e:
                traceback.print_exc()
                await self._log_request_status(job, "failed")
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
        await self.lifecycle.expire(request_id)

    async def _release_dispatch_job(self, dispatch_job: dict, reason: str) -> None:
        await self.lifecycle.release(dispatch_job, reason)

    async def _try_finalize_lifecycle(self, lifecycle: dict) -> None:
        await self.lifecycle.try_finalize(lifecycle)

    async def handle_operation_ack(self, payload: str) -> None:
        await self.lifecycle.handle_ack(payload)

    async def _read_robot_bool_state(
        self,
        client: httpx.AsyncClient,
        state_url: str,
        property_id: str,
    ) -> Optional[bool]:
        return await read_robot_bool_state(client, state_url, property_id)

    def _parse_capability_route(self, route: object) -> Optional[dict]:
        return parse_capability_route(route)

    def _build_operation_inputs(self, selected_route: dict) -> dict[str, dict]:
        return build_operation_inputs(selected_route)


    async def process_factory_job(self, job: dict) -> None:
        job.setdefault("request_id", str(uuid.uuid4()))
        job.setdefault("run_id", self.run_id)
        triggering_sensor = job["sensor"]
        triggering_sensor_key = job.get("sensor_key")
        job_station_id = job.get("station_id") or ""
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
            routes = await fetch_supported_capabilities(
                client,
                skills_url,
                robot.skills_submodel_b64,
            )
            if routes is None:
                continue

            # 2. Match station, sensor, and (when supplied) required operation.
            selected_route = match_capability_route(
                routes,
                robot_id=robot_id,
                station_id=job_station_id,
                triggering_sensor=triggering_sensor,
                required_operation=required_operation,
            )
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

            request_id = str(job["request_id"])
            input_arguments.append({
                "value": {
                    "modelType": "Property",
                    "idShort": "requestId",
                    "valueType": "xs:string",
                    "value": request_id,
                }
            })
            input_arguments.append({
                "value": {
                    "modelType": "Property",
                    "idShort": "runId",
                    "valueType": "xs:string",
                    "value": job["run_id"],
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
                "run_id": job.get("run_id"),
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
        await self._log_request_status(job, "timeout")
        self.active_jobs.discard(job["token"])

    async def dispatch_factory_job(self, dispatch_job: dict) -> None:
        try:
            await self._dispatch_factory_job(dispatch_job)
        except asyncio.CancelledError:
            await self._log_request_status(dispatch_job, "failed")
            await self._release_dispatch_job(dispatch_job, "dispatch cancelled")
            raise
        except Exception as exc:
            await self._log_request_status(dispatch_job, "failed")
            await self._release_dispatch_job(
                dispatch_job,
                f"dispatch exception: {exc}",
            )
            raise

    async def _dispatch_factory_job(self, dispatch_job: dict) -> None:
        print(
            "[ORCHESTRATOR] Dispatching "
            f"operation={dispatch_job.get('target_operation')} "
            f"robot={dispatch_job.get('robot_skills_submodel_b64')} "
            f"route={dispatch_job.get('selected_route') or 'unknown'} "
            f"inputs={dispatch_job.get('operation_inputs', {})}"
        )

        lifecycle = await self.lifecycle.begin_dispatch(dispatch_job)
        if lifecycle is None:
            await self._log_request_status(dispatch_job, "failed")
            return

        t3_ms = int(time.time() * 1000)
        dispatch_job["t3_ms"] = t3_ms
        response = await invoke_operation(
            self.http_client,
            dispatch_job["invoke_url"],
            dispatch_job["body"],
            self.config.invoke_retry_count,
        )

        if response is None:
            await self._log_request_status(dispatch_job, "failed")
            await self._release_dispatch_job(dispatch_job, "operation invocation produced no response")
            return

        print(f"[ORCHESTRATOR] Response status from robot: {response.status_code}")

        await self._log_request_status(
            dispatch_job,
            "ok" if response.status_code < 400 else "failed",
        )

        if response.status_code < 400:
            await self._try_finalize_lifecycle(lifecycle)
        else:
            await self._release_dispatch_job(
                dispatch_job,
                f"operation invocation returned HTTP {response.status_code}",
            )
