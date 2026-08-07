import asyncio
import json
from collections import defaultdict, deque
from typing import Optional

from config_models import normalize_station_id


class LifecycleCoordinator:
    def __init__(self, timeout_seconds: float):
        self.timeout_seconds = timeout_seconds
        self.active_jobs: set[str] = set()
        self.sensor_states: dict[str, bool] = {}
        self.sensor_waiting_for_clear: dict[str, bool] = {}
        self.station_lifecycles: dict[str, deque[dict]] = defaultdict(deque)
        self.lifecycle_by_request_id: dict[str, dict] = {}
        self.reserved_robots: set[str] = set()
        self.on_robot_available = None

    async def close(self) -> None:
        for lifecycle in list(self.lifecycle_by_request_id.values()):
            await self.release(lifecycle, "orchestrator shutdown")
        self.reserved_robots.clear()

    async def expire(self, request_id: str) -> None:
        await asyncio.sleep(self.timeout_seconds)
        lifecycle = self.lifecycle_by_request_id.get(request_id)
        if lifecycle is not None:
            robot_key = await self.release(
                lifecycle,
                f"operation lifecycle timed out after "
                f"{self.timeout_seconds:.1f}s",
            )
            if robot_key and self.on_robot_available is not None:
                await self.on_robot_available(robot_key)

    def _remove_lifecycle(self, station_id: str, request_id: str) -> None:
        lifecycles = self.station_lifecycles.get(station_id)
        if lifecycles is not None:
            remaining = deque(
                lifecycle
                for lifecycle in lifecycles
                if lifecycle.get("request_id") != request_id
            )
            if remaining:
                self.station_lifecycles[station_id] = remaining
            else:
                self.station_lifecycles.pop(station_id, None)
        if request_id:
            self.lifecycle_by_request_id.pop(request_id, None)

    async def release(self, dispatch_job: dict, reason: str) -> Optional[str]:
        token = dispatch_job.get("token")
        station_id = normalize_station_id(
            dispatch_job.get("station_id") or ""
        )
        robot_key = dispatch_job.get("robot_key")
        request_id = str(dispatch_job.get("request_id") or "")
        if token:
            self.active_jobs.discard(token)
        if robot_key:
            self.reserved_robots.discard(robot_key)
        lifecycle = self.lifecycle_by_request_id.get(request_id)
        if lifecycle is not None:
            timeout_task = lifecycle.get("timeout_task")
            if timeout_task and timeout_task is not asyncio.current_task():
                timeout_task.cancel()
        self._remove_lifecycle(station_id, request_id)
        print(
            f"[ORCHESTRATOR] Released station lifecycle "
            f"station={station_id or 'unknown'} reason={reason}"
        )
        return robot_key

    async def try_finalize(self, lifecycle: dict) -> None:
        if (
            not lifecycle.get("operation_completed")
            or not lifecycle.get("sensor_clear")
        ):
            return

        station_id = lifecycle["station_id"]
        request_id = lifecycle.get("request_id")
        token = lifecycle.get("token")
        robot_key = lifecycle.get("robot_key")
        if token:
            self.active_jobs.discard(token)
        if robot_key:
            self.reserved_robots.discard(robot_key)
        self._remove_lifecycle(station_id, request_id or "")
        timeout_task = lifecycle.get("timeout_task")
        if timeout_task and timeout_task is not asyncio.current_task():
            timeout_task.cancel()
        print(
            f"[ORCHESTRATOR] Station '{station_id}' lifecycle finalized after "
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

        lifecycle = {
            **dispatch_job,
            "station_id": station_id,
            "request_id": request_id,
            "operation_started": False,
            "operation_completed": False,
            "sensor_clear": bool(dispatch_job.get("sensor_clear")),
        }
        self.station_lifecycles[station_id].append(lifecycle)
        self.lifecycle_by_request_id[request_id] = lifecycle
        lifecycle["timeout_task"] = asyncio.create_task(
            self.expire(request_id)
        )
        print(
            f"[ORCHESTRATOR] Station '{station_id}' latched for request "
            f"{request_id}; waiting for operation completion and sensor clear"
        )
        return lifecycle

    async def handle_ack(self, payload: str) -> Optional[str]:
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
            lifecycle["operation_started"] = True
            return None
        if status == "completed":
            lifecycle["operation_completed"] = True
            robot_key = lifecycle.get("robot_key")
            if robot_key:
                self.reserved_robots.discard(robot_key)
            timeout_task = lifecycle.get("timeout_task")
            if timeout_task and timeout_task is not asyncio.current_task():
                timeout_task.cancel()
            await self.try_finalize(lifecycle)
            return robot_key
        else:
            return await self.release(
                lifecycle,
                ack.get("error") or "operation failed",
            )
