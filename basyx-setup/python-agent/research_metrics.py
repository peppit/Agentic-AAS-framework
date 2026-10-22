"""CSV research metrics for semantic scheduling jobs."""

import asyncio
import csv
from pathlib import Path

from semantic_model import ProcessJob


METRIC_HEADERS = [
    "job_id",
    "run_id",
    "trigger_asset_id",
    "trigger_semantic_id",
    "process_requirement_id",
    "required_capability_semantic",
    "selected_resource_id",
    "t1_update_notification_received_unix_us",
    "t2_orchestrator_handling_started_unix_us",
    "t3_resource_selected_reserved_unix_us",
    "t4_operation_dispatch_started_unix_us",
    "result",
    "failure_reason",
]


class SemanticMetricsLogger:
    def __init__(self, path: str, run_id: str) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self._lock = asyncio.Lock()
        self._logged_jobs: set[str] = set()
        self._ensure_file()

    def _ensure_file(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        expected = ",".join(METRIC_HEADERS)
        if self.path.exists() and self.path.stat().st_size:
            with self.path.open("r", encoding="utf-8") as stream:
                actual = stream.readline().strip()
            if actual == expected:
                return
            raise RuntimeError(
                f"Unexpected CSV header in {self.path}. "
                "Move or rename the previous log before starting a new run. "
                f"Expected {expected!r}, got {actual!r}."
            )
        with self.path.open("w", newline="", encoding="utf-8") as stream:
            csv.DictWriter(stream, fieldnames=METRIC_HEADERS).writeheader()

    async def record(
        self, job: ProcessJob, result: str, failure_reason: str = ""
    ) -> None:
        async with self._lock:
            if job.job_id in self._logged_jobs:
                return
            self._logged_jobs.add(job.job_id)
            row = {
                "job_id": job.job_id,
                "run_id": self.run_id,
                "trigger_asset_id": job.trigger_asset_id,
                "trigger_semantic_id": job.trigger_semantic_id,
                "process_requirement_id": job.requirement_ref.id_short_path,
                "required_capability_semantic": job.required_capability_semantic,
                "selected_resource_id": job.selected_resource_id or "",
                "t1_update_notification_received_unix_us": (
                    job.t1_update_notification_received_unix_us or ""
                ),
                "t2_orchestrator_handling_started_unix_us": (
                    job.t2_orchestrator_handling_started_unix_us or ""
                ),
                "t3_resource_selected_reserved_unix_us": (
                    job.t3_resource_selected_reserved_unix_us or ""
                ),
                "t4_operation_dispatch_started_unix_us": (
                    job.t4_operation_dispatch_started_unix_us or ""
                ),
                "result": result,
                "failure_reason": failure_reason,
            }
            with self.path.open("a", newline="", encoding="utf-8") as stream:
                csv.DictWriter(stream, fieldnames=METRIC_HEADERS).writerow(row)
