import asyncio
import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from config_models import AgentConfig
from orchestration import FactoryOrchestrator
from research_metrics import SemanticMetricsLogger
from semantic_catalog import SemanticCatalog
from semantic_model import (
    CapabilityOffer,
    ElementRef,
    OperationBinding,
    OperationParameter,
    ProcessJob,
    ResourceStateDefinition,
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
            resource_sub_csv_path=str(
                Path(self.temp_dir.name) / "resource_sub.csv"
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

    async def test_fault_transition_updates_resource_state(self) -> None:
        catalog = build_catalog()
        fault_ref = ElementRef("urn:test:robot02:state", "FaultActive")
        catalog.asset_by_submodel_id[fault_ref.submodel_id] = object()
        catalog.state_elements_by_ref[fault_ref] = ResourceStateDefinition(
            owner_asset_id=ROBOT_ID,
            semantic_id=FAULT_ACTIVE,
            element_ref=fault_ref,
            current_value=False,
        )
        orchestrator = self.orchestrator(catalog)
        orchestrator.state[ROBOT_ID][FAULT_ACTIVE] = False

        await orchestrator.handle_event(
            fault_ref.submodel_id,
            fault_ref.id_short_path,
            "true",
        )
        self.assertTrue(orchestrator.state[ROBOT_ID][FAULT_ACTIVE])

        await orchestrator.handle_event(
            fault_ref.submodel_id,
            fault_ref.id_short_path,
            "false",
        )
        self.assertFalse(orchestrator.state[ROBOT_ID][FAULT_ACTIVE])

    async def test_selection_records_t3_and_dispatch_records_t4(self) -> None:
        catalog = build_catalog()
        preferred_id = "urn:test:robot01"
        preferred_skill = ElementRef(
            "urn:test:robot01:control", "Skills.MoveBox"
        )
        catalog.capabilities_by_semantic_id[CAPABILITY].append(
            CapabilityOffer(
                owner_asset_id=preferred_id,
                capability_ref=ElementRef(
                    "urn:test:robot01:capability", "Transport"
                ),
                semantic_ids={CAPABILITY},
                skill_ref=preferred_skill,
            )
        )
        catalog.operation_by_skill_ref[preferred_skill] = OperationBinding(
            owner_asset_id=preferred_id,
            skill_ref=preferred_skill,
            operation_ref=ElementRef(
                "urn:test:robot01:execution", "MoveBox"
            ),
            submodel_endpoint="http://example.test/preferred-submodel",
            parameters=[
                OperationParameter(
                    {SOURCE_TRANSFER_LOCATION}, "Source", "xs:string"
                ),
                OperationParameter(
                    {TARGET_TRANSFER_LOCATION}, "Target", "xs:string"
                ),
            ],
        )
        catalog.reachability_by_skill_ref[preferred_skill] = {
            SOURCE_ID,
            TARGET_ID,
        }

        orchestrator = self.orchestrator(catalog)
        orchestrator.state[preferred_id] = {
            AVAILABLE_FOR_SCHEDULING: True,
            FAULT_ACTIVE: True,
            IS_MOVING: False,
        }
        orchestrator.state[ROBOT_ID][IS_MOVING] = False
        job = build_job()
        job.t1_update_notification_received_unix_us = 1_000_000
        job.t2_orchestrator_handling_started_unix_us = 1_200_000

        with (
            patch("orchestration.time.time_ns", return_value=1_250_000_000),
            patch(
                "orchestration.invoke_operation",
                new=AsyncMock(return_value=SimpleNamespace(status_code=200)),
            ),
        ):
            await orchestrator.process_job(job)

        self.assertEqual(job.selected_resource_id, ROBOT_ID)
        self.assertEqual(job.t3_resource_selected_reserved_unix_us, 1_250_000)
        self.assertEqual(job.t4_operation_dispatch_started_unix_us, 1_250_000)

        await orchestrator.close()

    async def test_orchestrator_csv_records_basyx_timestamps(self) -> None:
        path = Path(self.temp_dir.name) / "timestamp_metrics.csv"
        logger = SemanticMetricsLogger(str(path), "test-run")
        job = build_job()
        job.t1_update_notification_received_unix_us = 1_000_000
        job.t2_orchestrator_handling_started_unix_us = 1_001_000
        job.t3_resource_selected_reserved_unix_us = 1_006_000
        job.t4_operation_dispatch_started_unix_us = 1_008_000

        await logger.record(job, "completed")

        with path.open(newline="", encoding="utf-8") as stream:
            row = next(csv.DictReader(stream))
        self.assertEqual(
            row["t1_update_notification_received_unix_us"], "1000000"
        )
        self.assertEqual(
            row["t2_orchestrator_handling_started_unix_us"], "1001000"
        )
        self.assertEqual(
            row["t3_resource_selected_reserved_unix_us"], "1006000"
        )
        self.assertEqual(
            row["t4_operation_dispatch_started_unix_us"], "1008000"
        )
        self.assertNotIn("t1_to_t2_ms", row)


if __name__ == "__main__":
    unittest.main()
