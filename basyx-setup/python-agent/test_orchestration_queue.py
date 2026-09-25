import asyncio
import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from config_models import AgentConfig
from orchestration import FactoryOrchestrator
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

    async def test_fault_transition_records_and_clears_t1(self) -> None:
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

        with patch("orchestration.time.time_ns", return_value=1_234_567_000):
            await orchestrator.handle_event(
                fault_ref.submodel_id,
                fault_ref.id_short_path,
                "true",
            )

        self.assertEqual(orchestrator.fault_t1_by_resource[ROBOT_ID], 1_234_567)

        await orchestrator.handle_event(
            fault_ref.submodel_id,
            fault_ref.id_short_path,
            "false",
        )
        self.assertNotIn(ROBOT_ID, orchestrator.fault_t1_by_resource)

    async def test_faulted_preferred_resource_records_t2_selection(self) -> None:
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
        orchestrator.fault_t1_by_resource[preferred_id] = 1_000_000
        job = build_job()
        job.request_received_unix_us = 1_200_000

        with (
            patch("orchestration.time.time_ns", return_value=1_250_000_000),
            patch(
                "orchestration.invoke_operation",
                new=AsyncMock(return_value=SimpleNamespace(status_code=200)),
            ),
        ):
            await orchestrator.process_job(job)

        self.assertEqual(job.faulted_resource_id, preferred_id)
        self.assertEqual(job.selected_resource_id, ROBOT_ID)
        self.assertEqual(job.t1_unix_us, 1_000_000)
        self.assertEqual(job.t2_unix_us, 1_250_000)
        self.assertEqual(job.tD_unix_us, 1_250_000)

        with orchestrator.resource_sub_metrics.path.open(
            newline="", encoding="utf-8"
        ) as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["faulted_resource_id"], preferred_id)
        self.assertEqual(rows[0]["replacement_resource_id"], ROBOT_ID)
        self.assertEqual(rows[0]["request_received_unix_us"], "1200000")
        self.assertEqual(rows[0]["tD_unix_us"], "1250000")
        self.assertEqual(rows[0]["idle_wait_ms"], "200.000")
        self.assertEqual(rows[0]["selection_ms"], "50.000")

        await orchestrator.close()


if __name__ == "__main__":
    unittest.main()
