"""Indexed Phase 1 semantic inventory and diagnostic rendering."""

import asyncio
import time
from dataclasses import dataclass, field

from registry_client import RegistryClient, descriptor_endpoint
from semantic_model import (
    CapabilityOffer,
    CatalogMetrics,
    ElementRef,
    OperationBinding,
    ProcessRequirement,
    Resource,
    ResourceStateDefinition,
    ValidationDiagnostic,
)
from semantic_parser import SemanticParser
from semantics import (
    ONTOPROCAP_TRANSPORT,
    SOURCE_TRANSFER_LOCATION,
    TARGET_TRANSFER_LOCATION,
)


def _descriptor_value(descriptor: dict, field_name: str) -> str | None:
    value = descriptor.get(field_name)
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("name") or value.get("value")
    text = str(value).strip()
    return text or None


@dataclass
class SemanticCatalog:
    all_resources: list[Resource] = field(default_factory=list)
    assets_by_global_id: dict[str, Resource] = field(default_factory=dict)
    asset_by_submodel_id: dict[str, Resource] = field(default_factory=dict)
    capabilities_by_semantic_id: dict[str, list[CapabilityOffer]] = field(
        default_factory=dict
    )
    capability_offers: list[CapabilityOffer] = field(default_factory=list)
    operation_by_skill_ref: dict[ElementRef, OperationBinding] = field(
        default_factory=dict
    )
    reachability_by_skill_ref: dict[ElementRef, set[str]] = field(
        default_factory=dict
    )
    state_elements_by_asset_and_semantic: dict[
        tuple[str, str], ResourceStateDefinition
    ] = field(default_factory=dict)
    requirements_by_trigger: dict[
        tuple[str, str], list[ProcessRequirement]
    ] = field(default_factory=dict)
    process_requirements: list[ProcessRequirement] = field(default_factory=list)
    diagnostics: list[ValidationDiagnostic] = field(default_factory=list)
    metrics: CatalogMetrics = field(default_factory=CatalogMetrics)

    @property
    def resources(self) -> list[Resource]:
        return [resource for resource in self.all_resources if resource.is_instance]

    @classmethod
    async def discover(cls, client: RegistryClient) -> "SemanticCatalog":
        started = time.monotonic()
        catalog = cls()
        aas_descriptors = await client.list_aas_descriptors()
        catalog.metrics.aas_descriptors = len(aas_descriptors)

        submodel_descriptor_groups = await asyncio.gather(
            *(
                client.list_submodel_descriptors(str(descriptor.get("id") or ""))
                for descriptor in aas_descriptors
                if str(descriptor.get("id") or "").strip()
            ),
            return_exceptions=True,
        )
        descriptor_group_index = 0
        unique_submodel_descriptors: dict[str, dict] = {}
        for aas_descriptor in aas_descriptors:
            aas_id = str(aas_descriptor.get("id") or "").strip()
            if not aas_id:
                catalog.diagnostics.append(
                    ValidationDiagnostic(
                        "AAS descriptor", "descriptor has no AAS id"
                    )
                )
                continue
            result = submodel_descriptor_groups[descriptor_group_index]
            descriptor_group_index += 1
            submodel_descriptors: list[dict] = []
            if isinstance(result, Exception):
                catalog.diagnostics.append(
                    ValidationDiagnostic(
                        _descriptor_value(aas_descriptor, "idShort") or aas_id,
                        f"Submodel Registry discovery failed: {result}",
                    )
                )
            else:
                submodel_descriptors = result

            endpoints: dict[str, str] = {}
            for descriptor in submodel_descriptors:
                submodel_id = str(descriptor.get("id") or "").strip()
                endpoint = descriptor_endpoint(descriptor)
                if not submodel_id:
                    continue
                unique_submodel_descriptors[submodel_id] = descriptor
                if endpoint:
                    endpoints[submodel_id] = endpoint
                else:
                    catalog.diagnostics.append(
                        ValidationDiagnostic(
                            _descriptor_value(aas_descriptor, "idShort") or aas_id,
                            f"Submodel {submodel_id} has no usable advertised endpoint",
                        )
                    )

            resource = Resource(
                aas_id=aas_id,
                global_asset_id=str(
                    aas_descriptor.get("globalAssetId") or ""
                ).strip(),
                asset_type=_descriptor_value(aas_descriptor, "assetType"),
                id_short=_descriptor_value(aas_descriptor, "idShort"),
                submodel_endpoints=endpoints,
                asset_kind=_descriptor_value(aas_descriptor, "assetKind"),
            )
            catalog.all_resources.append(resource)
            if resource.is_instance and resource.global_asset_id:
                catalog.assets_by_global_id[resource.global_asset_id] = resource
                for submodel_id in endpoints:
                    catalog.asset_by_submodel_id[submodel_id] = resource

        fetch_results = await asyncio.gather(
            *(
                client.fetch_submodel(descriptor)
                for descriptor in unique_submodel_descriptors.values()
            ),
            return_exceptions=True,
        )
        submodels: list[dict] = []
        for submodel_id, result in zip(
            unique_submodel_descriptors, fetch_results, strict=True
        ):
            if isinstance(result, Exception):
                catalog.diagnostics.append(
                    ValidationDiagnostic(
                        submodel_id, f"Submodel fetch failed: {result}"
                    )
                )
            else:
                submodels.append(result)

        parsed = SemanticParser(
            catalog.all_resources, submodels, catalog.asset_by_submodel_id
        ).parse()
        catalog.capability_offers = parsed.capability_offers
        for offer in parsed.capability_offers:
            for semantic_id in offer.semantic_ids:
                catalog.capabilities_by_semantic_id.setdefault(
                    semantic_id, []
                ).append(offer)
        catalog.operation_by_skill_ref = {
            binding.skill_ref: binding for binding in parsed.operation_bindings
        }
        catalog.reachability_by_skill_ref = parsed.reachability_by_skill_ref
        catalog.state_elements_by_asset_and_semantic = {
            (definition.owner_asset_id, definition.semantic_id): definition
            for definition in parsed.state_definitions
        }
        catalog.process_requirements = parsed.process_requirements
        for requirement in parsed.process_requirements:
            if requirement.trigger_asset_id and requirement.trigger_semantic_id:
                catalog.requirements_by_trigger.setdefault(
                    (
                        requirement.trigger_asset_id,
                        requirement.trigger_semantic_id,
                    ),
                    [],
                ).append(requirement)
        catalog.diagnostics.extend(parsed.diagnostics)
        catalog.diagnostics.extend(
            ValidationDiagnostic("Registry discovery", warning)
            for warning in dict.fromkeys(client.warnings)
        )
        catalog.diagnostics.extend(catalog.validate())
        catalog.metrics.submodels = len(submodels)
        catalog.metrics.http_requests = client.http_request_count
        catalog.metrics.resources = len(catalog.resources)
        catalog.metrics.offered_capabilities = len(catalog.capability_offers)
        catalog.metrics.process_requirements = len(catalog.process_requirements)
        catalog.metrics.build_duration_seconds = time.monotonic() - started
        return catalog

    def validate(self) -> list[ValidationDiagnostic]:
        diagnostics: list[ValidationDiagnostic] = []
        for offer in self.capability_offers:
            resource = self.assets_by_global_id.get(offer.owner_asset_id)
            subject = (
                resource.id_short
                if resource and resource.id_short
                else offer.owner_asset_id
            )
            binding = self.operation_by_skill_ref.get(offer.skill_ref)
            if not offer.semantic_ids:
                diagnostics.append(
                    ValidationDiagnostic(
                        subject,
                        "offered Capability has no supplemental domain semantic",
                    )
                )
            if binding is None:
                diagnostics.append(
                    ValidationDiagnostic(
                        subject,
                        "discoverable but not orchestratable: missing "
                        "SkillInvokedByOperation",
                    )
                )
                continue
            if not binding.submodel_endpoint:
                diagnostics.append(
                    ValidationDiagnostic(
                        subject,
                        "Operation Submodel has no advertised repository endpoint",
                    )
                )
            if ONTOPROCAP_TRANSPORT in offer.semantic_ids:
                parameter_semantics = {
                    semantic_id
                    for parameter in binding.parameters
                    for semantic_id in parameter.semantic_ids
                }
                for expected in (
                    SOURCE_TRANSFER_LOCATION,
                    TARGET_TRANSFER_LOCATION,
                ):
                    if expected not in parameter_semantics:
                        diagnostics.append(
                            ValidationDiagnostic(
                                subject,
                                f"Transport operation parameter semantic missing: {expected}",
                            )
                        )
                if not self.reachability_by_skill_ref.get(offer.skill_ref):
                    diagnostics.append(
                        ValidationDiagnostic(
                            subject, "Transport Skill has no resolved CanReach targets"
                        )
                    )

        for requirement in self.process_requirements:
            subject = requirement.id_short or requirement.requirement_ref.id_short_path
            checks = (
                (requirement.trigger_asset_id, "TriggerResource does not resolve"),
                (requirement.trigger_semantic_id, "TriggerCondition is missing"),
                (
                    requirement.required_capability_semantics,
                    "RequiredCapability does not resolve to domain semantics",
                ),
                (requirement.source_id, "TransferSource does not resolve"),
                (requirement.target_id, "TransferTarget does not resolve"),
            )
            for value, message in checks:
                if not value:
                    diagnostics.append(ValidationDiagnostic(subject, message))
            if (
                requirement.trigger_asset_id
                and requirement.trigger_semantic_id
                and (
                    requirement.trigger_asset_id,
                    requirement.trigger_semantic_id,
                )
                not in self.state_elements_by_asset_and_semantic
            ):
                diagnostics.append(
                    ValidationDiagnostic(
                        subject,
                        "TriggerCondition has no matching discovered state element",
                    )
                )
            for semantic_id in requirement.required_capability_semantics:
                if semantic_id not in self.capabilities_by_semantic_id:
                    diagnostics.append(
                        ValidationDiagnostic(
                            subject,
                            f"no offered Capability matches required semantic {semantic_id}",
                        )
                    )
        return diagnostics

    def diagnostic_summary(self) -> str:
        lines = [
            "================================================",
            "SEMANTIC AAS DISCOVERY",
            "================================================",
            "",
            f"Resources: {len(self.resources)}",
            (
                "Discovery: "
                f"AAS descriptors={self.metrics.aas_descriptors}, "
                f"Submodels={self.metrics.submodels}, "
                f"HTTP requests={self.metrics.http_requests}, "
                f"duration={self.metrics.build_duration_seconds:.3f}s"
            ),
        ]
        offers_by_owner: dict[str, list[tuple[str, CapabilityOffer]]] = {}
        for semantic_id, offers in self.capabilities_by_semantic_id.items():
            for offer in offers:
                offers_by_owner.setdefault(offer.owner_asset_id, []).append(
                    (semantic_id, offer)
                )
        states_by_owner: dict[str, list[ResourceStateDefinition]] = {}
        for definition in self.state_elements_by_asset_and_semantic.values():
            states_by_owner.setdefault(definition.owner_asset_id, []).append(definition)

        for resource in sorted(
            self.resources, key=lambda item: item.id_short or item.global_asset_id
        ):
            lines.extend(
                [
                    "",
                    "Resource:",
                    f"  idShort: {resource.id_short or '<none>'}",
                    f"  globalAssetId: {resource.global_asset_id or '<none>'}",
                ]
            )
            resource_offers = offers_by_owner.get(resource.global_asset_id, [])
            if resource_offers:
                lines.append("  Offered capabilities:")
            for semantic_id, offer in resource_offers:
                lines.append(f"    {semantic_id}")
                lines.append(f"      realizedBy: {offer.skill_ref.id_short_path}")
                binding = self.operation_by_skill_ref.get(offer.skill_ref)
                if binding:
                    lines.append(
                        f"      invokedBy: {binding.operation_ref.id_short_path}"
                    )
                    if binding.parameters:
                        lines.append("      Operation parameters:")
                    for parameter in binding.parameters:
                        semantic_text = ", ".join(sorted(parameter.semantic_ids)) or "<none>"
                        lines.append(f"        semantic: {semantic_text}")
                        lines.append(f"          actual idShort: {parameter.id_short}")
                targets = sorted(
                    self.reachability_by_skill_ref.get(offer.skill_ref, set())
                )
                if targets:
                    lines.append("      Reachability:")
                    lines.extend(f"        {target}" for target in targets)
            resource_states = states_by_owner.get(resource.global_asset_id, [])
            if resource_states:
                lines.append("  State definitions:")
                for definition in sorted(
                    resource_states, key=lambda item: item.semantic_id
                ):
                    lines.append(f"    {definition.semantic_id}")

        for requirement in self.process_requirements:
            lines.extend(
                [
                    "",
                    "Process requirement:",
                    f"  {requirement.id_short or requirement.requirement_ref.id_short_path}",
                    "  Trigger:",
                    f"    asset: {requirement.trigger_asset_id or '<unresolved>'}",
                    f"    semantic: {requirement.trigger_semantic_id or '<unresolved>'}",
                    "  Required capability:",
                ]
            )
            lines.extend(
                f"    {semantic_id}"
                for semantic_id in sorted(requirement.required_capability_semantics)
            )
            lines.extend(
                [
                    f"  Source: {requirement.source_id or '<unresolved>'}",
                    f"  Target: {requirement.target_id or '<unresolved>'}",
                ]
            )
        if self.diagnostics:
            lines.extend(["", "Validation diagnostics:"])
            lines.extend(
                f"  [{item.severity}] {item.subject}: {item.message}"
                for item in self.diagnostics
            )
        else:
            lines.extend(["", "Validation: complete semantic chains are valid"])
        lines.append("================================================")
        return "\n".join(lines)
