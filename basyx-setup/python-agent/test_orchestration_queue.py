import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from config_models import AgentConfig
from orchestration import FactoryOrchestrator
from semantic_catalog import SemanticCatalog
from semantic_model import (
    CapabilityOffer,
    ElementRef,
    OperationBinding,
    OperationParameter,
    ProcessJob,
)
from semantics import (
    AVAILABLE_FOR_SCHEDULING,
    FAULT_ACTIVE,
    IS_MOVING,
    SOURCE_TRANSFER_LOCATION,
    TARGET_TRANSFER_LOCATION,
)


ROBOT_ID = "urn:test:robot02"
SOURCE_ID = "urn:test:conveyor02"
TARGET_ID = "urn:test:pallet01"
CAPABILITY = "urn:test:transport"
SKILL_REF = ElementRef("urn:test:robot02:control", "Skills.MoveBox")


class CatalogManagerStub:
    def __init__(self, catalog: SemanticCatalog) -> None:
        self.catalog = catalog

    async def snapshot(self) -> SemanticCatalog:
        return self.catalog


def build_catalog(*, reachable: bool = True) -> SemanticCatalog:
    offer = CapabilityOffer(
        owner_asset_id=ROBOT_ID,
        capability_ref=ElementRef("urn:test:robot02:capability", "Transport"),
        semantic_ids={CAPABILITY},
        skill_ref=SKILL_REF,
    )
    binding = OperationBinding(
        owner_asset_id=ROBOT_ID,
        skill_ref=SKILL_REF,
        operation_ref=ElementRef("urn:test:robot02:execution", "MoveBox"),
        submodel_endpoint="http://example.test/submodel",
        parameters=[
            OperationParameter({SOURCE_TRANSFER_LOCATION}, "Source", "xs:string"),
            OperationParameter({TARGET_TRANSFER_LOCATION}, "Target", "xs:string"),
        ],
    )
    catalog = SemanticCatalog()
    catalog.capabilities_by_semantic_id[CAPABILITY] = [offer]
    catalog.operation_by_skill_ref[SKILL_REF] = binding
    catalog.reachability_by_skill_ref[SKILL_REF] = (
        {SOURCE_ID, TARGET_ID} if reachable else {TARGET_ID}
    )
    return catalog


def build_job() -> ProcessJob:
    return ProcessJob(
        job_id="job-1",
        requirement_ref=ElementRef("urn:test:requirements", "Transfer02"),
        trigger_asset_id=SOURCE_ID,
        trigger_semantic_id="urn:test:workpiece-present",
        required_capability_semantic=CAPABILITY,
        source_id=SOURCE_ID,
        target_id=TARGET_ID,
    )


class PendingJobTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def orchestrator(
        self, catalog: SemanticCatalog, *, timeout: float = 1.0
    ) -> FactoryOrchestrator:
        config = AgentConfig(
            queue_timeout_seconds=timeout,
            orchestrator_log_csv_path=str(
                Path(self.temp_dir.name) / "orchestrator.csv"
            ),
        )
        orchestrator = FactoryOrchestrator(
            config,
            CatalogManagerStub(catalog),
            http_client=object(),
        )
        orchestrator.metrics.record = AsyncMock()
        orchestrator.state[ROBOT_ID] = {
            AVAILABLE_FOR_SCHEDULING: True,
            FAULT_ACTIVE: False,
            IS_MOVING: True,
        }
        return orchestrator

    async def test_busy_reachable_job_retries_after_availability_change(self) -> None:
        orchestrator = self.orchestrator(build_catalog())
        job = build_job()

        await orchestrator.process_job(job)

        self.assertIn(job.job_id, orchestrator.pending_jobs_by_request_id)
        orchestrator.state[ROBOT_ID][IS_MOVING] = False
        orchestrator._availability_changed.set()
        retried = await asyncio.wait_for(orchestrator.job_queue.get(), timeout=1)
        orchestrator.job_queue.task_done()

        self.assertIs(retried, job)
        self.assertNotIn(job.job_id, orchestrator.pending_jobs_by_request_id)
        orchestrator.metrics.record.assert_not_awaited()

    async def test_pending_job_fails_after_queue_timeout(self) -> None:
        orchestrator = self.orchestrator(build_catalog(), timeout=0.01)
        job = build_job()

        await orchestrator.process_job(job)
        await asyncio.sleep(0.03)

        self.assertNotIn(job.job_id, orchestrator.pending_jobs_by_request_id)
        orchestrator.metrics.record.assert_awaited_once()
        _, result, reason = orchestrator.metrics.record.await_args.args
        self.assertEqual(result, "failed")
        self.assertIn("queue timeout", reason)

    async def test_unreachable_job_still_fails_immediately(self) -> None:
        orchestrator = self.orchestrator(build_catalog(reachable=False))
        job = build_job()

        await orchestrator.process_job(job)

        self.assertFalse(orchestrator.pending_jobs_by_request_id)
        orchestrator.metrics.record.assert_awaited_once_with(
            job, "failed", "candidates but none reachable"
        )


if __name__ == "__main__":
    unittest.main()
