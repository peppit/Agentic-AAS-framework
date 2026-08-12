import asyncio
import base64
import csv
import json
import time
import uuid
from collections import defaultdict, deque
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
        self.lifecycle = LifecycleCoordinator(self.config.operation_timeout_seconds)
        self.lifecycle.on_robot_available = self._schedule_next_for_robot
        # Compatibility aliases keep the existing public surface while lifecycle
        # state and transitions are owned by LifecycleCoordinator.
        self.active_jobs = self.lifecycle.active_jobs
        self.sensor_states = self.lifecycle.sensor_states
        self.sensor_waiting_for_clear = self.lifecycle.sensor_waiting_for_clear
        self.station_lifecycles = self.lifecycle.station_lifecycles
        self.lifecycle_by_request_id = self.lifecycle.lifecycle_by_request_id
        self.reserved_robots = self.lifecycle.reserved_robots
        self.box_queues_by_station: dict[str, deque[dict]] = defaultdict(deque)
        self.pending_by_robot: dict[str, deque[dict]] = defaultdict(deque)
        self.pending_unassigned: deque[dict] = deque()
        self.pending_by_station: dict[str, deque[dict]] = defaultdict(deque)
        self.pending_request_ids: set[str] = set()
        self.pending_timeout_tasks: dict[str, asyncio.Task] = {}
        self.server_online = False
        self.server_instance_id = ""
        self.station_statuses: dict[str, dict] = {}
        self.fault_state_publisher = None
        self.published_fault_states: dict[str, bool] = {}
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
        for task in self.pending_timeout_tasks.values():
            task.cancel()
        await asyncio.gather(
            *self.pending_timeout_tasks.values(),
            return_exceptions=True,
        )
        self.pending_timeout_tasks.clear()
        self.pending_request_ids.clear()
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

    @staticmethod
    def _robot_id(robot: RobotEndpoints) -> str:
        encoded = robot.state_submodel_b64
        try:
            padding = "=" * (-len(encoded) % 4)
            decoded = base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
            robot_id = decoded.rstrip("/").rsplit("/", 1)[-1]
        except (ValueError, UnicodeDecodeError):
            robot_id = encoded
        return robot_id.replace("/", "_").replace("+", "_").replace("#", "_")

    async def _publish_robot_fault_state(
        self,
        robot: RobotEndpoints,
        fault_active: Optional[bool],
        observed_at_ms: Optional[int] = None,
    ) -> None:
        if fault_active is None or self.fault_state_publisher is None:
            return
        robot_key = robot.state_submodel_b64
        if self.published_fault_states.get(robot_key) is fault_active:
            return

        robot_id = self._robot_id(robot)
        topic_prefix = self.config.robot_fault_topic_prefix.rstrip("/")
        topic = f"{topic_prefix}/{robot_id}/fault"
        payload = json.dumps(
            {
                "robotId": robot_id,
                "stationId": robot.station_id,
                "faultActive": fault_active,
                "observedAtMs": observed_at_ms or int(time.time() * 1000),
                "source": "RobotStateAAS",
            },
            separators=(",", ":"),
        )
        try:
            await self.fault_state_publisher(topic, payload)
        except Exception as exc:
            print(
                f"[ORCHESTRATOR] Failed to publish fault state for "
                f"{robot_id}: {exc}"
            )
            return
        self.published_fault_states[robot_key] = fault_active
        print(
            f"[ORCHESTRATOR] Published retained fault state "
            f"robot={robot_id} faultActive={fault_active} topic={topic}"
        )


    async def handle_event(self, submodel_b64: str, property_id: str, payload: str, mqtt_topic: str, received_at_ms: int) -> None:

        if property_id == "FaultActive":
            fault_active = parse_bool_value(payload)
            robot = next(
                (
                    candidate
                    for candidate in self.robots
                    if candidate.state_submodel_b64
                    == normalize_submodel_id(submodel_b64)
                ),
                None,
            )
            if robot is not None:
                await self._publish_robot_fault_state(
                    robot,
                    fault_active,
                    received_at_ms,
                )
            return

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
            self.sensor_waiting_for_clear[sensor_key] = False
            station_key = normalize_station_id(station_id)
            box_queue = self.box_queues_by_station.get(station_key)
            cleared_job = None
            if box_queue:
                for queued_job in box_queue:
                    if (
                        queued_job.get("sensor_key") == sensor_key
                        and not queued_job.get("sensor_clear")
                    ):
                        cleared_job = queued_job
                        break
            if cleared_job is not None:
                cleared_job["sensor_clear"] = True
                box_queue.remove(cleared_job)
                lifecycle = self.lifecycle_by_request_id.get(
                    cleared_job["request_id"]
                )
                if lifecycle is not None:
                    lifecycle["sensor_clear"] = True
                    await self._try_finalize_lifecycle(lifecycle)
            if box_queue is not None and not box_queue:
                self.box_queues_by_station.pop(station_key, None)
            print(
                f"[ORCHESTRATOR] Sensor '{property_id}' on Conveyor "
                f"'{submodel_b64}' cleared; ready for next box detection"
            )
            return

        if self.sensor_waiting_for_clear.get(sensor_key):
            return

        request_id = str(uuid.uuid4())
        job = {
            "conveyor_b64": submodel_b64,
            "station_id": station_id,
            "sensor": property_id,
            "sensor_key": sensor_key,
            "sensor_clear": False,
            "token": request_id,
            "mqtt_topic": mqtt_topic,
            "request_id": request_id,
            "run_id": self.run_id,
            "t1_ms": received_at_ms,
            "deadline": time.monotonic() + self.config.queue_timeout_seconds,
        }
        self.sensor_waiting_for_clear[sensor_key] = True
        self.active_jobs.add(request_id)
        self.box_queues_by_station[normalize_station_id(station_id)].append(job)
        await self.job_queue.put(job)
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
        robot_key = await self.lifecycle.release(dispatch_job, reason)
        if robot_key:
            await self._schedule_next_for_robot(robot_key)

    async def _try_finalize_lifecycle(self, lifecycle: dict) -> None:
        await self.lifecycle.try_finalize(lifecycle)

    async def handle_operation_ack(self, payload: str) -> None:
        robot_key = await self.lifecycle.handle_ack(payload)
        if robot_key:
            await self._schedule_next_for_robot(robot_key)

    def _station_is_ready(self, station_id: str) -> bool:
        station_key = normalize_station_id(station_id)
        status = self.station_statuses.get(station_key, {})
        return (
            self.server_online
            and bool(self.server_instance_id)
            and status.get("serverInstanceId") == self.server_instance_id
            and status.get("online") is True
            and status.get("robotReady") is True
        )

    async def _queue_station_pending_job(self, job: dict) -> None:
        request_id = str(job["request_id"])
        if time.monotonic() >= float(job["deadline"]):
            await self._log_request_status(job, "timeout")
            self.active_jobs.discard(job["token"])
            return
        if request_id in self.pending_request_ids:
            return

        station_key = normalize_station_id(job.get("station_id") or "")
        job["pending_station_key"] = station_key
        self.pending_request_ids.add(request_id)
        self.active_jobs.add(job["token"])
        self.pending_by_station[station_key].append(job)
        self.pending_timeout_tasks[request_id] = asyncio.create_task(
            self._expire_pending_job(job)
        )
        print(
            f"[ORCHESTRATOR] Queued request {request_id} for station "
            f"{station_key}; waiting for server and robot readiness"
        )

    async def _drain_station_pending(self, station_id: str) -> None:
        station_key = normalize_station_id(station_id)
        if not self._station_is_ready(station_key):
            return
        queue = self.pending_by_station.get(station_key)
        while queue:
            job = queue[0]
            self._remove_pending_job(job)
            self._cancel_pending_timeout(str(job["request_id"]))
            await self.job_queue.put(job)

    async def _handle_station_unavailable(self, station_id: str) -> None:
        station_key = normalize_station_id(station_id)

        queued_jobs = []
        for queue in self.pending_by_robot.values():
            queued_jobs.extend(
                job
                for job in list(queue)
                if normalize_station_id(job.get("station_id") or "") == station_key
            )
        queued_jobs.extend(
            job
            for job in list(self.pending_unassigned)
            if normalize_station_id(job.get("station_id") or "") == station_key
        )
        for job in queued_jobs:
            self._remove_pending_job(job)
            self._cancel_pending_timeout(str(job["request_id"]))
            await self._queue_station_pending_job(job)

        for lifecycle in list(self.station_lifecycles.get(station_key, [])):
            operation_started = bool(lifecycle.get("operation_started"))
            request_id = lifecycle.get("request_id")
            robot_key = await self.lifecycle.release(
                lifecycle,
                "station became unavailable",
            )
            if operation_started:
                print(
                    f"[ORCHESTRATOR] Request {request_id} was interrupted after "
                    "operation start; state is unknown and it will not be retried"
                )
            else:
                lifecycle["deadline"] = (
                    time.monotonic() + self.config.queue_timeout_seconds
                )
                await self._queue_station_pending_job(lifecycle)
            if robot_key:
                await self._schedule_next_for_robot(robot_key)

    async def handle_server_status(self, payload: str) -> None:
        try:
            status = json.loads(payload)
        except json.JSONDecodeError:
            print(f"[ORCHESTRATOR] Ignored malformed server status: {payload!r}")
            return
        if not isinstance(status, dict) or not isinstance(status.get("online"), bool):
            return

        instance_id = str(status.get("serverInstanceId") or "").strip()
        if not instance_id:
            return
        if (
            status["online"] is False
            and self.server_instance_id
            and instance_id != self.server_instance_id
        ):
            return

        known_stations = set(self.station_statuses)
        previously_ready = {
            station_id: self._station_is_ready(station_id)
            for station_id in known_stations
        }
        self.server_online = status["online"]
        self.server_instance_id = instance_id

        for station_id in known_stations:
            ready = self._station_is_ready(station_id)
            if previously_ready[station_id] and not ready:
                await self._handle_station_unavailable(station_id)
            elif not previously_ready[station_id] and ready:
                await self._drain_station_pending(station_id)

    async def handle_station_status(self, topic: str, payload: str) -> None:
        try:
            status = json.loads(payload)
        except json.JSONDecodeError:
            print(f"[ORCHESTRATOR] Ignored malformed station status: {payload!r}")
            return
        if not isinstance(status, dict):
            return

        topic_parts = topic.split("/")
        station_id = str(status.get("stationId") or topic_parts[1]).strip()
        station_key = normalize_station_id(station_id)
        if (
            not station_key
            or not isinstance(status.get("online"), bool)
            or not isinstance(status.get("robotReady"), bool)
            or not str(status.get("serverInstanceId") or "").strip()
        ):
            return

        previously_ready = self._station_is_ready(station_key)
        self.station_statuses[station_key] = status
        ready = self._station_is_ready(station_key)
        if previously_ready and not ready:
            await self._handle_station_unavailable(station_key)
        elif not previously_ready and ready:
            await self._drain_station_pending(station_key)

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

    def _cancel_pending_timeout(self, request_id: str) -> None:
        timeout_task = self.pending_timeout_tasks.pop(request_id, None)
        if timeout_task and timeout_task is not asyncio.current_task():
            timeout_task.cancel()

    def _remove_pending_job(self, job: dict) -> None:
        request_id = str(job.get("request_id") or "")
        station_key = job.get("pending_station_key")
        robot_key = job.get("pending_robot_key")
        if station_key:
            queue = self.pending_by_station.get(station_key)
            if queue and job in queue:
                queue.remove(job)
            if queue is not None and not queue:
                self.pending_by_station.pop(station_key, None)
        elif robot_key:
            queue = self.pending_by_robot.get(robot_key)
            if queue and job in queue:
                queue.remove(job)
            if queue is not None and not queue:
                self.pending_by_robot.pop(robot_key, None)
        elif job in self.pending_unassigned:
            self.pending_unassigned.remove(job)
        self.pending_request_ids.discard(request_id)
        job.pop("pending_station_key", None)
        job.pop("pending_robot_key", None)

    async def _expire_pending_job(self, job: dict) -> None:
        request_id = str(job["request_id"])
        delay = max(0.0, float(job["deadline"]) - time.monotonic())
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        if request_id not in self.pending_request_ids:
            return
        self._remove_pending_job(job)
        self.pending_timeout_tasks.pop(request_id, None)
        print(
            f"[ORCHESTRATOR] Queued job for {job['sensor']} timed out after "
            f"{self.config.queue_timeout_seconds:.1f}s"
        )
        await self._log_request_status(job, "timeout")
        self.active_jobs.discard(job["token"])

    async def _queue_pending_job(
        self,
        job: dict,
        candidates: list[dict],
        robot_key: Optional[str] = None,
    ) -> None:
        request_id = str(job["request_id"])
        if time.monotonic() >= float(job["deadline"]):
            await self._log_request_status(job, "timeout")
            self.active_jobs.discard(job["token"])
            return
        if request_id in self.pending_request_ids:
            return

        job["candidate_by_robot"] = {
            candidate["robot"].state_submodel_b64: candidate
            for candidate in candidates
        }
        job["pending_robot_key"] = robot_key
        self.pending_request_ids.add(request_id)
        if robot_key:
            self.pending_by_robot[robot_key].append(job)
            print(
                f"[ORCHESTRATOR] Queued request {request_id} for busy "
                f"robot {robot_key}"
            )
        else:
            self.pending_unassigned.append(job)
            print(
                f"[ORCHESTRATOR] Queued request {request_id} without a robot; "
                "waiting for a capable robot to become available"
            )
        self.pending_timeout_tasks[request_id] = asyncio.create_task(
            self._expire_pending_job(job)
        )

    async def _schedule_next_for_robot(self, robot_key: str) -> None:
        assigned_queue = self.pending_by_robot.get(robot_key)
        if assigned_queue:
            job = assigned_queue[0]
            candidate = job.get("candidate_by_robot", {}).get(robot_key)
            if candidate is not None:
                self._remove_pending_job(job)
                self._cancel_pending_timeout(str(job["request_id"]))
                await self._dispatch_to_robot(job, candidate)
                return

        for job in list(self.pending_unassigned):
            candidate = job.get("candidate_by_robot", {}).get(robot_key)
            if candidate is None:
                continue
            self._remove_pending_job(job)
            self._cancel_pending_timeout(str(job["request_id"]))
            await self._dispatch_to_robot(job, candidate)
            return

    async def _dispatch_to_robot(self, job: dict, candidate: dict) -> None:
        if not self._station_is_ready(job.get("station_id") or ""):
            await self._queue_station_pending_job(job)
            return

        robot = candidate["robot"]
        selected_route = candidate["route"]
        robot_key = robot.state_submodel_b64
        robot_id = robot.skills_submodel_b64
        if robot_key in self.reserved_robots:
            await self._queue_pending_job(job, [candidate], robot_key)
            return

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
            "requestedTimeout": int(self.config.http_timeout_seconds * 1000),
        }
        selected_station_id = selected_route["StationId"]
        target_op = selected_route["TargetOperation"]
        skills_url = (
            f"{self.config.basyx_base_url}/submodels/"
            f"{robot.skills_submodel_b64}"
        )
        dispatch_payload = {
            "token": job.get("token"),
            "station_id": selected_station_id,
            "source_position": selected_route["SourcePosition"],
            "target_position": selected_route["TargetPosition"],
            "sensor": job["sensor"],
            "sensor_key": job.get("sensor_key"),
            "sensor_clear": bool(job.get("sensor_clear")),
            "t1_ms": job.get("t1_ms"),
            "t2_ms": int(time.time() * 1000),
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
            "deadline": job.get("deadline"),
            "required_operation": job.get("required_operation"),
        }

        self.reserved_robots.add(robot_key)
        try:
            await self.dispatch_queue.put(dispatch_payload)
        except BaseException:
            self.reserved_robots.discard(robot_key)
            raise
        print(
            f"[ORCHESTRATOR] Reserved robot {robot_id} as {robot_key}; "
            f"queued operation={target_op} inputs={dispatch_payload['operation_inputs']}"
        )


    async def process_factory_job(self, job: dict) -> None:
        job.setdefault("request_id", str(uuid.uuid4()))
        job.setdefault("run_id", self.run_id)
        job.setdefault("token", job["request_id"])
        job.setdefault(
            "sensor_clear",
            self.sensor_states.get(job.get("sensor_key")) is False,
        )
        triggering_sensor = job["sensor"]
        job_station_id = job.get("station_id") or ""
        required_operation = str(
            job.get("required_operation") or job.get("target_operation") or ""
        ).strip()
        client = self.http_client

        job.setdefault(
            "deadline",
            time.monotonic() + self.config.queue_timeout_seconds,
        )

        if not self._station_is_ready(job_station_id):
            await self._queue_station_pending_job(job)
            return

        capable_candidates = []
        for robot in self.robots:
            robot_id = robot.skills_submodel_b64
            skills_url = f"{self.config.basyx_base_url}/submodels/{robot.skills_submodel_b64}"
            routes = await fetch_supported_capabilities(
                client,
                skills_url,
                robot.skills_submodel_b64,
            )
            if routes is None:
                continue

            selected_route = match_capability_route(
                routes,
                robot_id=robot_id,
                station_id=job_station_id,
                triggering_sensor=triggering_sensor,
                required_operation=required_operation,
            )
            if selected_route is None:
                continue
            candidate = {"robot": robot, "route": selected_route}
            state_url = (
                f"{self.config.basyx_base_url}/submodels/"
                f"{robot.state_submodel_b64}"
            )
            fault_active = await self._read_robot_bool_state(
                client, state_url, "FaultActive"
            )
            await self._publish_robot_fault_state(robot, fault_active)
            if fault_active is not False:
                print(
                    f"[ORCHESTRATOR] Rejected robot {robot_id}: "
                    f"FaultActive is {fault_active!r}, expected False"
                )
                continue
            moving = await self._read_robot_bool_state(client, state_url, "IsMoving")
            if moving is None:
                print(
                    f"[ORCHESTRATOR] Rejected robot {robot_id}: "
                    "IsMoving could not be read"
                )
                continue
            candidate["moving"] = moving
            capable_candidates.append(candidate)

            if moving is False and robot.state_submodel_b64 not in self.reserved_robots:
                print(
                    f"[ORCHESTRATOR] Selected route {selected_route['route_id'] or '<unnamed>'} "
                    f"on robot {robot_id}: station={selected_route['StationId']} "
                    f"source={selected_route['SourcePosition'] or 'n/a'} "
                    f"target={selected_route['TargetPosition'] or 'n/a'} "
                    f"operation={selected_route['TargetOperation']}"
                )
                await self._dispatch_to_robot(job, candidate)
                return

        if len(capable_candidates) == 1:
            robot_key = capable_candidates[0]["robot"].state_submodel_b64
            await self._queue_pending_job(
                job,
                capable_candidates,
                robot_key,
            )
            return

        await self._queue_pending_job(job, capable_candidates)

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
        if not self._station_is_ready(dispatch_job.get("station_id") or ""):
            self.reserved_robots.discard(dispatch_job.get("robot_key"))
            await self._queue_station_pending_job(dispatch_job)
            return

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
