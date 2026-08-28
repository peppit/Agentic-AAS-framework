"""SemanticCatalog-driven factory orchestration runtime."""

import asyncio
import base64
import json
import time
import uuid
from dataclasses import dataclass
from urllib.parse import unquote

import httpx

from aas_access import invoke_operation
from catalog_runtime import CatalogManager
from config_models import AgentConfig, parse_bool_value
from research_metrics import SemanticMetricsLogger
from reservation import ReservationManager
from semantic_catalog import SemanticCatalog
from semantic_model import (
    CapabilityOffer,
    ElementRef,
    OperationBinding,
    ProcessJob,
    ProcessRequirement,
    ResourceStateDefinition,
)
from semantics import (
    AVAILABLE_FOR_SCHEDULING,
    FAULT_ACTIVE,
    IS_MOVING,
    SOURCE_TRANSFER_LOCATION,
    TARGET_TRANSFER_LOCATION,
)


@dataclass
class ActiveExecution:
    job: ProcessJob
    binding: OperationBinding
    timeout_task: asyncio.Task | None = None
    operation_started: bool = False


def _decode_submodel_identifier(value: str) -> str:
    """Decode a BaSyx base64url topic token, retaining plain identifiers."""

    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(value + padding).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return value
    return decoded if decoded.strip() else value


def select_candidate(
    candidates: list[tuple[CapabilityOffer, OperationBinding]],
    selected_resource_id: str,
) -> tuple[CapabilityOffer, OperationBinding]:
    """Isolated stable-first policy result lookup."""

    return min(
        (
            candidate
            for candidate in candidates
            if candidate[0].owner_asset_id == selected_resource_id
        ),
        key=lambda candidate: (
            candidate[0].owner_asset_id,
            candidate[0].skill_ref.submodel_id,
            candidate[0].skill_ref.id_short_path,
        ),
    )


class FactoryOrchestrator:
    """Schedule ProcessRequirements exclusively from a catalog snapshot."""

    def __init__(
        self,
        config: AgentConfig,
        catalog_manager: CatalogManager,
        *,
        http_client: httpx.AsyncClient | None = None,
        reservations: ReservationManager | None = None,
    ) -> None:
        self.config = config
        self.catalog_manager = catalog_manager
        self._owns_http_client = http_client is None
        self.http_client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(config.http_timeout_seconds)
        )
        self.reservations = reservations or ReservationManager()
        self.job_queue: asyncio.Queue[ProcessJob] = asyncio.Queue()
        self.state: dict[str, dict[str, object]] = {}
        self.latched_triggers: set[tuple[str, str]] = set()
        self.active_jobs_by_request_id: dict[str, ActiveExecution] = {}
        self.run_id = config.measurement_run_id.strip() or str(uuid.uuid4())
        self.metrics = SemanticMetricsLogger(
            config.orchestrator_log_csv_path, self.run_id
        )

    async def initialize(self) -> None:
        catalog = await self.catalog_manager.snapshot()
        await self.reconcile_catalog(SemanticCatalog(), catalog)
        print(
            f"[ORCHESTRATOR] Semantic runtime initialized "
            f"resources={len(catalog.resources)} run_id={self.run_id}"
        )

    async def close(self) -> None:
        executions = list(self.active_jobs_by_request_id.values())
        for execution in executions:
            await self._finish_execution(
                execution,
                "failed",
                "orchestrator shutdown",
            )
        for resource_id in list(self.reservations.reserved_resources):
            await self.reservations.release(resource_id)
        if self._owns_http_client:
            await self.http_client.aclose()

    async def reconcile_catalog(
        self, previous: SemanticCatalog, refreshed: SemanticCatalog
    ) -> None:
        previous_ids = set(previous.assets_by_global_id)
        for definition in refreshed.state_elements_by_ref.values():
            asset_state = self.state.setdefault(definition.owner_asset_id, {})
            if definition.semantic_id not in asset_state:
                value = parse_bool_value(definition.current_value)
                asset_state[definition.semantic_id] = (
                    value if value is not None else definition.current_value
                )
                trigger_key = (
                    definition.owner_asset_id,
                    definition.semantic_id,
                )
                if value is True and trigger_key in refreshed.requirements_by_trigger:
                    self.latched_triggers.add(trigger_key)

        for resource_id in sorted(set(refreshed.assets_by_global_id) - previous_ids):
            offered = sorted(
                {
                    semantic_id
                    for semantic_id, offers in refreshed.capabilities_by_semantic_id.items()
                    if any(offer.owner_asset_id == resource_id for offer in offers)
                }
            )
            print(
                "[CATALOG] semantic resource added: "
                f"globalAssetId={resource_id} offeredCapabilities={offered}"
            )

    @staticmethod
    def _event_element_candidates(element_token: str) -> list[str]:
        decoded = unquote(element_token)
        candidates = [decoded]
        dotted = decoded.replace("/", ".")
        if dotted not in candidates:
            candidates.append(dotted)
        return candidates

    @staticmethod
    def _resolve_state_definition(
        catalog: SemanticCatalog,
        submodel_token: str,
        element_token: str,
    ) -> ResourceStateDefinition | None:
        submodel_candidates = [submodel_token]
        decoded_submodel = _decode_submodel_identifier(submodel_token)
        if decoded_submodel not in submodel_candidates:
            submodel_candidates.append(decoded_submodel)

        for submodel_id in submodel_candidates:
            if submodel_id not in catalog.asset_by_submodel_id:
                continue
            for path in FactoryOrchestrator._event_element_candidates(element_token):
                definition = catalog.state_elements_by_ref.get(
                    ElementRef(submodel_id, path)
                )
                if definition is not None:
                    return definition

            # Some BaSyx event transports emit only the leaf idShort. It is
            # still used solely to address an already semantically indexed
            # property; ambiguous suffixes are rejected.
            leaf = FactoryOrchestrator._event_element_candidates(element_token)[-1]
            matches = [
                definition
                for ref, definition in catalog.state_elements_by_ref.items()
                if ref.submodel_id == submodel_id
                and ref.id_short_path.rsplit(".", 1)[-1] == leaf.rsplit(".", 1)[-1]
            ]
            if len(matches) == 1:
                return matches[0]
        return None

    async def handle_event(
        self,
        submodel_token: str,
        element_token: str,
        payload: str,
        mqtt_topic: str = "",
        received_at_ms: int | None = None,
    ) -> None:
        catalog = await self.catalog_manager.snapshot()
        definition = self._resolve_state_definition(
            catalog, submodel_token, element_token
        )
        if definition is None:
            print(
                "[ORCHESTRATOR] Ignored unindexed AAS state event "
                f"submodel={submodel_token} element={element_token}"
            )
            return

        value = parse_bool_value(payload)
        if value is None:
            print(
                "[ORCHESTRATOR] Ignored non-boolean semantic state event "
                f"asset={definition.owner_asset_id} "
                f"semantic={definition.semantic_id} payload={payload!r}"
            )
            return

        asset_state = self.state.setdefault(definition.owner_asset_id, {})
        asset_state[definition.semantic_id] = value
        trigger_key = (definition.owner_asset_id, definition.semantic_id)
        requirements = catalog.requirements_by_trigger.get(trigger_key, [])
        if not requirements:
            return

        if value is False:
            self.latched_triggers.discard(trigger_key)
            print(
                f"[ORCHESTRATOR] Semantic trigger rearmed asset={trigger_key[0]} "
                f"semantic={trigger_key[1]}"
            )
            return
        if trigger_key in self.latched_triggers:
            return

        self.latched_triggers.add(trigger_key)
        for requirement in requirements:
            job = self._create_job(requirement, received_at_ms)
            if job is None:
                continue
            await self.job_queue.put(job)
            print(
                f"[ORCHESTRATOR] Semantic job enqueued job_id={job.job_id} "
                f"trigger={trigger_key} requirement={requirement.id_short or requirement.requirement_ref.id_short_path}"
            )

    @staticmethod
    def _create_job(
        requirement: ProcessRequirement,
        received_at_ms: int | None = None,
    ) -> ProcessJob | None:
        if (
            not requirement.trigger_asset_id
            or not requirement.trigger_semantic_id
            or not requirement.source_id
            or not requirement.target_id
            or not requirement.required_capability_semantics
        ):
            print(
                "[ORCHESTRATOR] ProcessRequirement is incomplete: "
                f"{requirement.requirement_ref}"
            )
            return None
        required_semantic = sorted(requirement.required_capability_semantics)[0]
        return ProcessJob(
            job_id=str(uuid.uuid4()),
            requirement_ref=requirement.requirement_ref,
            trigger_asset_id=requirement.trigger_asset_id,
            trigger_semantic_id=requirement.trigger_semantic_id,
            required_capability_semantic=required_semantic,
            source_id=requirement.source_id,
            target_id=requirement.target_id,
            received_at_ms=received_at_ms,
        )

    async def start_worker(self) -> None:
        while True:
            job = await self.job_queue.get()
            try:
                await self.process_job(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if job.selected_resource_id:
                    await self.reservations.release(job.selected_resource_id)
                self.active_jobs_by_request_id.pop(job.job_id, None)
                await self.metrics.record(job, "failed", f"worker exception: {exc}")
                print(f"[ORCHESTRATOR] Job {job.job_id} failed: {exc}")
            finally:
                self.job_queue.task_done()

    def _state_value(self, asset_id: str, semantic_id: str) -> object | None:
        return self.state.get(asset_id, {}).get(semantic_id)

    def _state_rejection(self, offer: CapabilityOffer, catalog: SemanticCatalog) -> str:
        resource_id = offer.owner_asset_id
        available = self._state_value(resource_id, AVAILABLE_FOR_SCHEDULING)
        if available is not True:
            return f"AvailableForScheduling is {available!r}; fail-safe reject"
        fault = self._state_value(resource_id, FAULT_ACTIVE)
        if fault is True:
            return "FaultActive is true"
        moving = self._state_value(resource_id, IS_MOVING)
        if moving is True:
            return "IsMoving is true"
        disabled = parse_bool_value(
            catalog.skill_disabled_by_skill_ref.get(offer.skill_ref)
        )
        if disabled is True:
            return "Skill.Disabled is true"
        # Missing FaultActive means no fault assertion under the current state
        # semantic contract. Missing availability is deliberately not allowed.
        return ""

    @staticmethod
    def _parameter_rejection(binding: OperationBinding) -> str:
        semantics = {
            semantic_id
            for parameter in binding.parameters
            for semantic_id in parameter.semantic_ids
        }
        missing = {
            SOURCE_TRANSFER_LOCATION,
            TARGET_TRANSFER_LOCATION,
        } - semantics
        if missing:
            return "semantic Operation parameter missing: " + ", ".join(
                sorted(missing)
            )
        return ""

    async def _fail_unreserved(
        self, job: ProcessJob, reason: str
    ) -> None:
        await self.metrics.record(job, "failed", reason)
        print(f"[ORCHESTRATOR] Job {job.job_id} failed: {reason}")

    async def process_job(self, job: ProcessJob) -> None:
        matching_started = time.perf_counter()
        catalog = await self.catalog_manager.snapshot()
        offers = sorted(
            catalog.capabilities_by_semantic_id.get(
                job.required_capability_semantic, []
            ),
            key=lambda offer: (
                offer.owner_asset_id,
                offer.skill_ref.submodel_id,
                offer.skill_ref.id_short_path,
            ),
        )
        job.candidate_count = len(offers)
        if not offers:
            job.matching_ms = (time.perf_counter() - matching_started) * 1000
            await self._fail_unreserved(job, "no matching capability")
            return

        reachable: list[CapabilityOffer] = []
        for offer in offers:
            targets = catalog.reachability_by_skill_ref.get(offer.skill_ref, set())
            if job.source_id not in targets or job.target_id not in targets:
                print(
                    f"[ORCHESTRATOR] Rejected resource={offer.owner_asset_id}: "
                    f"Skill cannot reach both source={job.source_id} and target={job.target_id}"
                )
                continue
            reachable.append(offer)
        job.reachable_candidate_count = len(reachable)
        if not reachable:
            job.matching_ms = (time.perf_counter() - matching_started) * 1000
            await self._fail_unreserved(job, "candidates but none reachable")
            return

        available: list[CapabilityOffer] = []
        for offer in reachable:
            rejection = self._state_rejection(offer, catalog)
            if rejection:
                print(
                    f"[ORCHESTRATOR] Rejected resource={offer.owner_asset_id}: {rejection}"
                )
                continue
            available.append(offer)
        job.available_candidate_count = len(available)
        if not available:
            job.matching_ms = (time.perf_counter() - matching_started) * 1000
            await self._fail_unreserved(
                job, "candidates reachable but unavailable"
            )
            return

        runnable: list[tuple[CapabilityOffer, OperationBinding]] = []
        binding_failures: list[str] = []
        for offer in available:
            binding = catalog.operation_by_skill_ref.get(offer.skill_ref)
            if binding is None or not binding.submodel_endpoint:
                reason = "operation binding missing"
            else:
                reason = self._parameter_rejection(binding)
            if reason:
                binding_failures.append(reason)
                print(
                    f"[ORCHESTRATOR] Rejected resource={offer.owner_asset_id}: {reason}"
                )
                continue
            runnable.append((offer, binding))
        job.matching_ms = (time.perf_counter() - matching_started) * 1000
        if not runnable:
            await self._fail_unreserved(
                job,
                binding_failures[0] if binding_failures else "operation binding missing",
            )
            return

        reservation_started = time.perf_counter()
        selected_resource_id = await self.reservations.select_and_reserve(
            offer.owner_asset_id for offer, _ in runnable
        )
        job.reservation_ms = (time.perf_counter() - reservation_started) * 1000
        if selected_resource_id is None:
            await self._fail_unreserved(job, "reservation conflict")
            return

        offer, binding = select_candidate(runnable, selected_resource_id)
        job.selected_resource_id = selected_resource_id
        execution = ActiveExecution(job=job, binding=binding)
        self.active_jobs_by_request_id[job.job_id] = execution
        print(
            f"[ORCHESTRATOR] Selected resource={selected_resource_id} "
            f"capability={job.required_capability_semantic} "
            f"skill={offer.skill_ref.id_short_path} "
            f"operation={binding.operation_ref.id_short_path}"
        )

        invocation_started = time.perf_counter()
        try:
            response = await invoke_operation(
                binding,
                {
                    SOURCE_TRANSFER_LOCATION: job.source_id,
                    TARGET_TRANSFER_LOCATION: job.target_id,
                },
                client=self.http_client,
                retry_count=self.config.invoke_retry_count,
                requested_timeout_ms=int(
                    self.config.http_timeout_seconds * 1000
                ),
                metadata_arguments={
                    "requestId": job.job_id,
                    "runId": self.run_id,
                },
            )
        except Exception as exc:
            job.invocation_ms = (time.perf_counter() - invocation_started) * 1000
            await self._finish_execution(
                execution, "failed", f"HTTP invocation failure: {exc}"
            )
            return
        job.invocation_ms = (time.perf_counter() - invocation_started) * 1000

        # A very fast completion reply can finish the job while POST /invoke is
        # still returning. Do not recreate its lifecycle in that case.
        if job.job_id not in self.active_jobs_by_request_id:
            return
        if response is None:
            await self._finish_execution(
                execution, "failed", "HTTP invocation produced no response"
            )
            return
        if response.status_code >= 400:
            await self._finish_execution(
                execution,
                "failed",
                f"HTTP invocation returned {response.status_code}",
            )
            return
        execution.timeout_task = asyncio.create_task(
            self._expire_operation(job.job_id)
        )

    async def _expire_operation(self, request_id: str) -> None:
        await asyncio.sleep(self.config.operation_timeout_seconds)
        execution = self.active_jobs_by_request_id.get(request_id)
        if execution is not None:
            await self._finish_execution(
                execution,
                "failed",
                f"operation timeout after {self.config.operation_timeout_seconds:.1f}s",
            )

    async def handle_operation_ack(self, payload: str) -> None:
        try:
            acknowledgement = json.loads(payload)
        except json.JSONDecodeError:
            print(f"[ORCHESTRATOR] Ignored malformed operation reply: {payload!r}")
            return
        if not isinstance(acknowledgement, dict):
            return
        request_id = str(acknowledgement.get("requestId") or "").strip()
        execution = self.active_jobs_by_request_id.get(request_id)
        if execution is None:
            print(
                f"[ORCHESTRATOR] Ignored operation reply for unknown request {request_id or '<missing>'}"
            )
            return

        status = str(acknowledgement.get("status") or "").strip().lower()
        if not status and isinstance(acknowledgement.get("success"), bool):
            status = "completed" if acknowledgement["success"] else "failed"
        if status in {"started", "running", "accepted"}:
            execution.operation_started = True
            return
        if status in {"completed", "complete", "succeeded", "success"}:
            await self._finish_execution(execution, "completed")
            return
        if status in {"failed", "fault", "faulted", "error"}:
            await self._finish_execution(
                execution,
                "failed",
                str(
                    acknowledgement.get("error")
                    or acknowledgement.get("message")
                    or "controller fault reply"
                ),
            )
            return
        print(
            f"[ORCHESTRATOR] Ignored operation reply with unsupported status: {acknowledgement}"
        )

    async def _finish_execution(
        self,
        execution: ActiveExecution,
        result: str,
        failure_reason: str = "",
    ) -> None:
        job = execution.job
        current = self.active_jobs_by_request_id.get(job.job_id)
        if current is not execution:
            return
        self.active_jobs_by_request_id.pop(job.job_id, None)
        if (
            execution.timeout_task is not None
            and execution.timeout_task is not asyncio.current_task()
        ):
            execution.timeout_task.cancel()
        if job.selected_resource_id:
            await self.reservations.release(job.selected_resource_id)
        await self.metrics.record(job, result, failure_reason)
        print(
            f"[ORCHESTRATOR] Job {job.job_id} {result}; "
            f"resource={job.selected_resource_id or '<none>'} "
            f"reason={failure_reason or '<none>'}"
        )
