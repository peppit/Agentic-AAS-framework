"""CSV research metrics for semantic scheduling jobs."""

import asyncio
import csv
import time
from pathlib import Path

from semantic_model import ProcessJob


METRIC_HEADERS = [
    "job_id",
    "run_id",
    "trigger_asset_id",
    "trigger_semantic_id",
    "process_requirement_id",
    "required_capability_semantic",
    "candidate_count",
    "reachable_candidate_count",
    "available_candidate_count",
    "selected_resource_id",
    "matching_ms",
    "reservation_ms",
    "invocation_ms",
    "end_to_end_ms",
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
                if stream.readline().strip() == expected:
                    return
            fallback = self.path.with_name(
                f"{self.path.stem}.phase2{self.path.suffix or '.csv'}"
            )
            print(
                f"[METRICS] Existing schema retained at {self.path}; "
                f"Phase 2 metrics use {fallback}"
            )
            self.path = fallback
            if self.path.exists() and self.path.stat().st_size:
                return
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
                "candidate_count": job.candidate_count,
                "reachable_candidate_count": job.reachable_candidate_count,
                "available_candidate_count": job.available_candidate_count,
                "selected_resource_id": job.selected_resource_id or "",
                "matching_ms": f"{job.matching_ms:.3f}",
                "reservation_ms": f"{job.reservation_ms:.3f}",
                "invocation_ms": f"{job.invocation_ms:.3f}",
                "end_to_end_ms": f"{(time.monotonic() - job.created_at) * 1000:.3f}",
                "result": result,
                "failure_reason": failure_reason,
            }
            with self.path.open("a", newline="", encoding="utf-8") as stream:
                csv.DictWriter(stream, fieldnames=METRIC_HEADERS).writerow(row)
