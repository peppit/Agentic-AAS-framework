import asyncio
import json
from typing import Optional

from config_models import normalize_station_id


class LifecycleCoordinator:
    def __init__(self, timeout_seconds: float):
        self.timeout_seconds = timeout_seconds
        self.active_jobs: set[str] = set()
        self.sensor_states: dict[str, bool] = {}
        self.sensor_waiting_for_clear: dict[str, bool] = {}
        self.station_lifecycles: dict[str, dict] = {}
        self.lifecycle_by_request_id: dict[str, dict] = {}
        self.reserved_robots: set[str] = set()
        self.reserved_stations: set[str] = set()

    async def close(self) -> None:
        for lifecycle in list(self.lifecycle_by_request_id.values()):
            await self.release(lifecycle, "orchestrator shutdown")
        self.reserved_robots.clear()
        self.reserved_stations.clear()

    async def expire(self, request_id: str) -> None:
        await asyncio.sleep(self.timeout_seconds)
        lifecycle = self.lifecycle_by_request_id.get(request_id)
        if lifecycle is not None:
            await self.release(
                lifecycle,
                f"operation lifecycle timed out after "
                f"{self.timeout_seconds:.1f}s",
            )

    async def release(self, dispatch_job: dict, reason: str) -> None:
        token = dispatch_job.get("token")
        sensor_key = dispatch_job.get("sensor_key")
        station_id = normalize_station_id(
            dispatch_job.get("station_id") or ""
        )
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
                self.lifecycle_by_request_id.pop(
                    lifecycle["request_id"],
                    None,
                )
                timeout_task = lifecycle.get("timeout_task")
                if (
                    timeout_task
                    and timeout_task is not asyncio.current_task()
                ):
                    timeout_task.cancel()
        print(
            f"[ORCHESTRATOR] Released station lifecycle "
            f"station={station_id or 'unknown'} reason={reason}"
        )

    async def try_finalize(self, lifecycle: dict) -> None:
        if (
            not lifecycle.get("operation_completed")
            or not lifecycle.get("sensor_clear")
        ):
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
            f"[ORCHESTRATOR] Station '{station_id}' rearmed after "
            "operation completion and sensor clear"
        )

    async def begin_dispatch(
        self,
        dispatch_job: dict,
    ) -> Optional[dict]:
        request_id = dispatch_job["request_id"]
        station_id = normalize_station_id(
            dispatch_job.get("station_id") or ""
        )
        if not station_id:
            await self.release(dispatch_job, "station_id is missing")
            return None

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
            self.expire(request_id)
        )
        if sensor_key:
            self.sensor_waiting_for_clear[sensor_key] = True
        print(
            f"[ORCHESTRATOR] Station '{station_id}' latched for request "
            f"{request_id}; waiting for operation completion and sensor clear"
        )
        return lifecycle

    async def handle_ack(self, payload: str) -> None:
        try:
            ack = json.loads(payload)
        except json.JSONDecodeError:
            print(
                "[ORCHESTRATOR] Ignored malformed operation "
                f"acknowledgement: {payload!r}"
            )
            return
        if not isinstance(ack, dict):
            return

        request_id = str(ack.get("requestId") or "").strip()
        station_id = normalize_station_id(
            str(ack.get("stationId") or "")
        )
        status = str(ack.get("status") or "").strip().lower()
        if (
            not request_id
            or not station_id
            or status not in {"started", "completed", "failed"}
        ):
            print(
                "[ORCHESTRATOR] Ignored incomplete operation "
                f"acknowledgement: {ack}"
            )
            return

        lifecycle = self.lifecycle_by_request_id.get(request_id)
        if lifecycle is None:
            print(
                "[ORCHESTRATOR] Ignored acknowledgement for unknown "
                f"request {request_id}"
            )
            return
        if lifecycle["station_id"] != station_id:
            print(
                f"[ORCHESTRATOR] Ignored acknowledgement for request "
                f"{request_id}: station mismatch {station_id} != "
                f"{lifecycle['station_id']}"
            )
            return

        if status == "started":
            return
        if status == "completed":
            lifecycle["operation_completed"] = True
            await self.try_finalize(lifecycle)
        else:
            await self.release(
                lifecycle,
                ack.get("error") or "operation failed",
            )
