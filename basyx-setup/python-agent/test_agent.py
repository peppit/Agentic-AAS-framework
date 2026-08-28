import asyncio
import copy
import tempfile
import unittest
from dataclasses import replace

from aas_access import build_invocation_payload, operation_invoke_url
from catalog_runtime import CatalogManager
from config_models import AgentConfig
from orchestration import FactoryOrchestrator
from registry_client import encode_identifier
from semantic_catalog import SemanticCatalog
from semantic_model import ElementRef, Resource, ResourceStateDefinition
from semantic_parser import SemanticParser
from semantics import (
    AVAILABLE_FOR_SCHEDULING,
    FAULT_ACTIVE,
    ONTOPROCAP_CONVEYING,
    ONTOPROCAP_TRANSPORT,
    SOURCE_TRANSFER_LOCATION,
    TARGET_TRANSFER_LOCATION,
    WORKPIECE_PRESENT,
)
from test_semantic_discovery import fixture


class FakeResponse:
    def __init__(self, status_code=202):
        self.status_code = status_code


class FakeHttpClient:
    def __init__(self):
        self.posts = []

    async def post(self, url, json):
        self.posts.append((url, json))
        await asyncio.sleep(0)
        return FakeResponse()


def catalog_from_inventory(inventory, resources, asset_by_submodel):
    catalog = SemanticCatalog(
        all_resources=resources,
        assets_by_global_id={
            resource.global_asset_id: resource
            for resource in resources
            if resource.is_instance
        },
        asset_by_submodel_id=asset_by_submodel,
        capability_offers=inventory.capability_offers,
        operation_by_skill_ref={
            binding.skill_ref: binding for binding in inventory.operation_bindings
        },
        reachability_by_skill_ref=inventory.reachability_by_skill_ref,
        skill_disabled_by_skill_ref=inventory.skill_disabled_by_skill_ref,
        process_requirements=inventory.process_requirements,
    )
    for offer in inventory.capability_offers:
        for semantic_id in offer.semantic_ids:
            catalog.capabilities_by_semantic_id.setdefault(semantic_id, []).append(
                offer
            )
    for definition in inventory.state_definitions:
        catalog.state_elements_by_ref[definition.element_ref] = definition
        catalog.state_elements_by_asset_and_semantic[
            (definition.owner_asset_id, definition.semantic_id)
        ] = definition
    for requirement in inventory.process_requirements:
        catalog.requirements_by_trigger.setdefault(
            (requirement.trigger_asset_id, requirement.trigger_semantic_id), []
        ).append(requirement)
    return catalog


class SemanticOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        inventory, self.submodels, resources, asset_by_submodel = fixture()
        self.catalog = catalog_from_inventory(
            inventory, resources, asset_by_submodel
        )
        self.tempdir = tempfile.TemporaryDirectory()
        self.config = AgentConfig(
            registry_refresh_seconds=0,
            operation_timeout_seconds=10,
            invoke_retry_count=1,
            orchestrator_log_csv_path=f"{self.tempdir.name}/metrics.csv",
            measurement_run_id="test",
        )
        self.manager = CatalogManager(self.catalog, self.config)
        self.http = FakeHttpClient()
        self.orchestrator = FactoryOrchestrator(
            self.config, self.manager, http_client=self.http
        )
        await self.orchestrator.initialize()

    async def asyncTearDown(self):
        await self.orchestrator.close()
        self.tempdir.cleanup()

    def make_job(self):
        return self.orchestrator._create_job(
            self.catalog.process_requirements[0]
        )

    async def test_a_semantic_trigger_is_idshort_independent_and_latched(self):
        renamed = copy.deepcopy(self.submodels)
        state = next(item for item in renamed if item["id"] == "conveyor:state")
        state["submodelElements"][0]["idShort"] = "PartAtPickupPoint"
        inventory = SemanticParser(
            self.catalog.all_resources,
            renamed,
            self.catalog.asset_by_submodel_id,
        ).parse()
        refreshed = catalog_from_inventory(
            inventory,
            self.catalog.all_resources,
            self.catalog.asset_by_submodel_id,
        )
        await self.manager.replace(refreshed)
        await self.orchestrator.reconcile_catalog(self.catalog, refreshed)

        submodel_token = encode_identifier("conveyor:state")
        await self.orchestrator.handle_event(
            submodel_token, "PartAtPickupPoint", "true"
        )
        await self.orchestrator.handle_event(
            submodel_token, "PartAtPickupPoint", "true"
        )
        self.assertEqual(self.orchestrator.job_queue.qsize(), 1)
        job = self.orchestrator.job_queue.get_nowait()
        self.assertEqual(job.trigger_semantic_id, WORKPIECE_PRESENT)
        self.orchestrator.job_queue.task_done()

        await self.orchestrator.handle_event(
            submodel_token, "PartAtPickupPoint", "false"
        )
        await self.orchestrator.handle_event(
            submodel_token, "PartAtPickupPoint", "true"
        )
        self.assertEqual(self.orchestrator.job_queue.qsize(), 1)

    def test_b_exact_capability_matching_excludes_conveying(self):
        transport = self.catalog.capabilities_by_semantic_id[ONTOPROCAP_TRANSPORT]
        conveying = self.catalog.capabilities_by_semantic_id[ONTOPROCAP_CONVEYING]
        self.assertEqual([offer.owner_asset_id for offer in transport], ["urn:test:robot"])
        self.assertEqual([offer.owner_asset_id for offer in conveying], ["urn:test:conveyor"])

    async def test_c_unreachable_transport_candidate_is_rejected(self):
        offer = self.catalog.capabilities_by_semantic_id[ONTOPROCAP_TRANSPORT][0]
        self.catalog.reachability_by_skill_ref[offer.skill_ref] = {"urn:test:conveyor"}
        job = self.make_job()
        await self.orchestrator.process_job(job)
        self.assertEqual(job.reachable_candidate_count, 0)
        self.assertIsNone(job.selected_resource_id)

    async def test_d_unavailable_or_faulted_candidate_is_rejected(self):
        self.orchestrator.state["urn:test:robot"][AVAILABLE_FOR_SCHEDULING] = False
        unavailable = self.make_job()
        await self.orchestrator.process_job(unavailable)
        self.assertEqual(unavailable.available_candidate_count, 0)

        self.orchestrator.state["urn:test:robot"][AVAILABLE_FOR_SCHEDULING] = True
        self.orchestrator.state["urn:test:robot"][FAULT_ACTIVE] = True
        faulted = self.make_job()
        await self.orchestrator.process_job(faulted)
        self.assertEqual(faulted.available_candidate_count, 0)

        self.orchestrator.state["urn:test:robot"].pop(AVAILABLE_FOR_SCHEDULING)
        self.orchestrator.state["urn:test:robot"][FAULT_ACTIVE] = False
        unknown_availability = self.make_job()
        await self.orchestrator.process_job(unknown_availability)
        self.assertEqual(unknown_availability.available_candidate_count, 0)

    async def test_e_reservation_race_allows_at_most_one_job(self):
        first = self.make_job()
        second = self.make_job()
        await asyncio.gather(
            self.orchestrator.process_job(first),
            self.orchestrator.process_job(second),
        )
        selected = [job for job in (first, second) if job.selected_resource_id]
        self.assertEqual(len(selected), 1)
        self.assertEqual(
            self.orchestrator.reservations.reserved_resources,
            {"urn:test:robot"},
        )

    def test_f_operation_and_parameters_are_idshort_independent(self):
        binding = next(iter(self.catalog.operation_by_skill_ref.values()))
        payload = build_invocation_payload(
            binding,
            {
                SOURCE_TRANSFER_LOCATION: "urn:test:conveyor",
                TARGET_TRANSFER_LOCATION: "urn:test:pallet",
            },
            requested_timeout_ms=8000,
        )
        arguments = {
            item["value"]["idShort"]: item["value"]["value"]
            for item in payload["inputArguments"]
        }
        self.assertEqual(
            arguments,
            {
                "ChangedSourceName": "urn:test:conveyor",
                "ChangedTargetName": "urn:test:pallet",
            },
        )
        semantics_by_id_short = {
            item["value"]["idShort"]: item["value"]["semanticId"]["keys"][0][
                "value"
            ]
            for item in payload["inputArguments"]
        }
        self.assertEqual(
            semantics_by_id_short,
            {
                "ChangedSourceName": SOURCE_TRANSFER_LOCATION,
                "ChangedTargetName": TARGET_TRANSFER_LOCATION,
            },
        )
        self.assertTrue(operation_invoke_url(binding).endswith("/ArbitraryOperation/invoke"))

    def test_g_comanaged_and_external_target_ids_are_equal(self):
        requirement = self.catalog.process_requirements[0]
        offer = self.catalog.capabilities_by_semantic_id[ONTOPROCAP_TRANSPORT][0]
        self.assertIn(
            requirement.target_id,
            self.catalog.reachability_by_skill_ref[offer.skill_ref],
        )

    async def test_h_dynamic_robot02_is_used_without_config_change(self):
        refreshed = copy.deepcopy(self.catalog)
        original_offer = refreshed.capabilities_by_semantic_id[
            ONTOPROCAP_TRANSPORT
        ][0]
        original_binding = refreshed.operation_by_skill_ref[
            original_offer.skill_ref
        ]
        robot02_skill = ElementRef("robot02:skills", "Skills.PickAndPlace")
        robot02_offer = replace(
            original_offer,
            owner_asset_id="urn:test:robot02",
            skill_ref=robot02_skill,
        )
        robot02_binding = replace(
            original_binding,
            owner_asset_id="urn:test:robot02",
            skill_ref=robot02_skill,
            submodel_endpoint="http://repo/submodels/robot02-ops",
        )
        robot02 = Resource(
            "urn:test:aas:robot02",
            "urn:test:robot02",
            "urn:test:type:robot",
            "Robot02",
            {"robot02:state": "http://repo/submodels/robot02-state"},
            "Instance",
        )
        refreshed.all_resources.append(robot02)
        refreshed.assets_by_global_id[robot02.global_asset_id] = robot02
        refreshed.asset_by_submodel_id["robot02:state"] = robot02
        refreshed.capability_offers.append(robot02_offer)
        refreshed.capabilities_by_semantic_id[ONTOPROCAP_TRANSPORT].append(
            robot02_offer
        )
        refreshed.operation_by_skill_ref[robot02_skill] = robot02_binding
        refreshed.reachability_by_skill_ref[robot02_skill] = {
            "urn:test:conveyor",
            "urn:test:pallet",
        }
        available = ResourceStateDefinition(
            "urn:test:robot02",
            AVAILABLE_FOR_SCHEDULING,
            ElementRef("robot02:state", "ScheduleReady"),
            True,
        )
        refreshed.state_elements_by_ref[available.element_ref] = available
        refreshed.state_elements_by_asset_and_semantic[
            (available.owner_asset_id, available.semantic_id)
        ] = available

        await self.manager.replace(refreshed)
        await self.orchestrator.reconcile_catalog(self.catalog, refreshed)
        await self.orchestrator.reservations.reserve_if_available("urn:test:robot")
        job = self.orchestrator._create_job(refreshed.process_requirements[0])
        await self.orchestrator.process_job(job)
        self.assertEqual(job.candidate_count, 2)
        self.assertEqual(job.selected_resource_id, "urn:test:robot02")

    async def test_i_initialization_has_no_station_registry_dependency(self):
        self.assertFalse(hasattr(self.config, "station_registry_file"))
        self.assertEqual(len(self.catalog.resources), 3)

    async def test_completion_releases_selected_resource_by_request_id(self):
        job = self.make_job()
        await self.orchestrator.process_job(job)
        self.assertIn(job.selected_resource_id, self.orchestrator.reservations.reserved_resources)
        await self.orchestrator.handle_operation_ack(
            '{"requestId":"%s","status":"completed","stationId":"ignored"}'
            % job.job_id
        )
        self.assertNotIn(job.selected_resource_id, self.orchestrator.reservations.reserved_resources)
        self.assertNotIn(job.job_id, self.orchestrator.active_jobs_by_request_id)


if __name__ == "__main__":
    unittest.main()
