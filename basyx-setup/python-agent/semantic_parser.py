"""Generic semantic parser for AAS v3 Submodel JSON."""

from dataclasses import dataclass, field
from collections.abc import Iterable, Iterator

from aas_reference import ReferenceResolver
from semantic_model import (
    CapabilityOffer,
    ElementRef,
    OperationBinding,
    OperationParameter,
    ProcessRequirement,
    Resource,
    ResourceStateDefinition,
    ValidationDiagnostic,
)
from semantics import (
    CAN_REACH,
    IDTA_CAPABILITY,
    IDTA_CAPABILITY_REALIZED_BY,
    IDTA_CAPABILITY_ROLE_OFFERED,
    IDTA_CAPABILITY_ROLE_REQUIRED,
    IDTA_CONTROL_COMPONENT_SKILL,
    IDTA_SKILL_DISABLED,
    PROCESS_REQUIREMENTS,
    REQUIRED_CAPABILITY,
    SKILL_INVOKED_BY_OPERATION,
    STATE_SEMANTIC_IDS,
    TRANSFER_REQUIREMENT,
    TRANSFER_SOURCE,
    TRANSFER_TARGET,
    TRIGGER_CONDITION,
    TRIGGER_RESOURCE,
)


def _reference_values(reference: object) -> set[str]:
    if not isinstance(reference, dict):
        return set()
    keys = reference.get("keys", [])
    if not isinstance(keys, list):
        return set()
    return {
        str(key.get("value") or "").strip()
        for key in keys
        if isinstance(key, dict) and str(key.get("value") or "").strip()
    }


def get_semantic_ids(element: object) -> set[str]:
    """Combine semanticId and supplementalSemanticIds identifiers."""

    if not isinstance(element, dict):
        return set()
    identifiers = _reference_values(element.get("semanticId"))
    supplemental = element.get("supplementalSemanticIds", [])
    if isinstance(supplemental, dict):
        supplemental = [supplemental]
    if isinstance(supplemental, list):
        for reference in supplemental:
            identifiers.update(_reference_values(reference))
    return identifiers


def model_type(element: object) -> str:
    if not isinstance(element, dict):
        return ""
    value = element.get("modelType")
    if isinstance(value, dict):
        value = value.get("name")
    return str(value or "")


def _operation_variable_elements(element: dict) -> Iterator[dict]:
    for field_name in ("inputVariables", "outputVariables", "inoutputVariables"):
        variables = element.get(field_name, [])
        if not isinstance(variables, list):
            continue
        for variable in variables:
            if not isinstance(variable, dict):
                continue
            value = variable.get("value")
            if isinstance(value, dict):
                yield value


def child_elements(element: dict) -> Iterator[dict]:
    """Yield recursively addressable children for all relevant AAS containers."""

    kind = model_type(element).lower()
    if kind == "operation":
        yield from _operation_variable_elements(element)
    for field_name in ("value", "statements", "annotations"):
        children = element.get(field_name)
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    yield child


def walk_submodel_elements(submodel: dict) -> Iterator[tuple[ElementRef, dict]]:
    """Recursively walk elements while retaining their BaSyx idShort path."""

    submodel_id = str(submodel.get("id") or "").strip()
    roots = submodel.get("submodelElements", [])
    if not submodel_id or not isinstance(roots, list):
        return

    def walk(elements: Iterable[dict], prefix: str = "") -> Iterator[tuple[ElementRef, dict]]:
        for index, element in enumerate(elements):
            if not isinstance(element, dict):
                continue
            id_short = str(element.get("idShort") or "").strip()
            segment = id_short or f"@{model_type(element) or 'Element'}[{index}]"
            path = f"{prefix}.{segment}" if prefix else segment
            element_ref = ElementRef(submodel_id, path)
            yield element_ref, element
            yield from walk(child_elements(element), path)

    yield from walk(roots)


def _has_qualifier_semantic(element: dict, semantic_id: str) -> bool:
    qualifiers = element.get("qualifiers", [])
    if not isinstance(qualifiers, list):
        return False
    return any(
        semantic_id in get_semantic_ids(qualifier)
        for qualifier in qualifiers
        if isinstance(qualifier, dict)
    )


def _relationship_reference(element: dict, field_name: str) -> dict | None:
    value = element.get(field_name)
    return value if isinstance(value, dict) else None


def _referenced_value(element: dict) -> dict | None:
    value = element.get("value")
    if isinstance(value, dict) and isinstance(value.get("keys"), list):
        return value
    return None


@dataclass
class ParsedSemanticInventory:
    resolver: ReferenceResolver
    elements_by_ref: dict[ElementRef, dict]
    refs_by_semantic_id: dict[str, set[ElementRef]]
    capability_offers: list[CapabilityOffer] = field(default_factory=list)
    operation_bindings: list[OperationBinding] = field(default_factory=list)
    reachability_by_skill_ref: dict[ElementRef, set[str]] = field(default_factory=dict)
    skill_disabled_by_skill_ref: dict[ElementRef, object] = field(default_factory=dict)
    state_definitions: list[ResourceStateDefinition] = field(default_factory=list)
    process_requirements: list[ProcessRequirement] = field(default_factory=list)
    diagnostics: list[ValidationDiagnostic] = field(default_factory=list)


class SemanticParser:
    def __init__(
        self,
        resources: list[Resource],
        submodels: list[dict],
        asset_by_submodel_id: dict[str, Resource],
    ) -> None:
        self.resources = resources
        self.submodels = submodels
        self.asset_by_submodel_id = asset_by_submodel_id
        self.resolver = ReferenceResolver()
        self.elements_by_ref: dict[ElementRef, dict] = {}
        self.refs_by_semantic_id: dict[str, set[ElementRef]] = {}
        self.diagnostics: list[ValidationDiagnostic] = []

    def parse(self) -> ParsedSemanticInventory:
        self._build_indexes()
        return ParsedSemanticInventory(
            resolver=self.resolver,
            elements_by_ref=self.elements_by_ref,
            refs_by_semantic_id=self.refs_by_semantic_id,
            capability_offers=self._parse_capability_offers(),
            operation_bindings=self._parse_operation_bindings(),
            reachability_by_skill_ref=self._parse_reachability(),
            skill_disabled_by_skill_ref=self._parse_skill_disabled(),
            state_definitions=self._parse_state_definitions(),
            process_requirements=self._parse_process_requirements(),
            diagnostics=self.diagnostics,
        )

    def _build_indexes(self) -> None:
        for resource in self.resources:
            self.resolver.add_asset_identity(
                aas_id=resource.aas_id,
                global_asset_id=resource.global_asset_id,
                id_short=resource.id_short,
            )
        for submodel in self.submodels:
            for element_ref, element in walk_submodel_elements(submodel):
                self.elements_by_ref[element_ref] = element
                self.resolver.add_element(element_ref, element)
                global_asset_id = element.get("globalAssetId")
                if isinstance(global_asset_id, str) and global_asset_id.strip():
                    self.resolver.canonical_by_identifier[global_asset_id.strip()] = (
                        global_asset_id.strip()
                    )
                for semantic_id in get_semantic_ids(element):
                    self.refs_by_semantic_id.setdefault(semantic_id, set()).add(
                        element_ref
                    )

    def _elements_with_semantic(
        self, semantic_id: str
    ) -> Iterator[tuple[ElementRef, dict]]:
        for element_ref in self.refs_by_semantic_id.get(semantic_id, set()):
            element = self.elements_by_ref.get(element_ref)
            if element is not None:
                yield element_ref, element

    def _owner(self, element_ref: ElementRef) -> Resource | None:
        return self.asset_by_submodel_id.get(element_ref.submodel_id)

    def _parse_capability_offers(self) -> list[CapabilityOffer]:
        realization_relationships = list(
            self._elements_with_semantic(IDTA_CAPABILITY_REALIZED_BY)
        )
        offers: list[CapabilityOffer] = []
        for capability_ref, capability in self._elements_with_semantic(IDTA_CAPABILITY):
            if not _has_qualifier_semantic(
                capability, IDTA_CAPABILITY_ROLE_OFFERED
            ):
                continue
            owner = self._owner(capability_ref)
            if owner is None or not owner.is_instance:
                continue
            skill_ref: ElementRef | None = None
            for _, relationship in realization_relationships:
                first = self.resolver.resolve_reference(
                    _relationship_reference(relationship, "first")
                )
                if first.element_ref != capability_ref:
                    continue
                second = self.resolver.resolve_reference(
                    _relationship_reference(relationship, "second")
                )
                if (
                    second.element_ref is not None
                    and second.element is not None
                    and IDTA_CONTROL_COMPONENT_SKILL
                    in get_semantic_ids(second.element)
                ):
                    skill_ref = second.element_ref
                if skill_ref is not None:
                    break
            if skill_ref is None:
                self.diagnostics.append(
                    ValidationDiagnostic(
                        owner.id_short or owner.global_asset_id,
                        f"offered capability {capability_ref.id_short_path} has no "
                        "resolvable CapabilityRealizedBy relationship",
                    )
                )
                continue
            offers.append(
                CapabilityOffer(
                    owner_asset_id=owner.global_asset_id,
                    capability_ref=capability_ref,
                    semantic_ids=get_semantic_ids(capability) - {IDTA_CAPABILITY},
                    skill_ref=skill_ref,
                )
            )
        return offers

    def _parse_operation_bindings(self) -> list[OperationBinding]:
        bindings: list[OperationBinding] = []
        for _, relationship in self._elements_with_semantic(
            SKILL_INVOKED_BY_OPERATION
        ):
            skill = self.resolver.resolve_reference(
                _relationship_reference(relationship, "first")
            )
            operation = self.resolver.resolve_reference(
                _relationship_reference(relationship, "second")
            )
            if skill.element_ref is None or operation.element_ref is None:
                self.diagnostics.append(
                    ValidationDiagnostic(
                        "SkillInvokedByOperation",
                        "relationship has an unresolved Skill or Operation reference",
                    )
                )
                continue
            if (
                skill.element is None
                or IDTA_CONTROL_COMPONENT_SKILL
                not in get_semantic_ids(skill.element)
                or operation.element is None
                or model_type(operation.element).lower() != "operation"
            ):
                self.diagnostics.append(
                    ValidationDiagnostic(
                        "SkillInvokedByOperation",
                        "relationship endpoints are not an IDTA Skill and an Operation",
                    )
                )
                continue
            owner = self._owner(skill.element_ref)
            operation_element = self.elements_by_ref.get(operation.element_ref)
            if owner is None or operation_element is None or not owner.is_instance:
                continue
            endpoint = owner.submodel_endpoints.get(
                operation.element_ref.submodel_id, ""
            )
            parameters = [
                OperationParameter(
                    semantic_ids=get_semantic_ids(parameter),
                    id_short=str(parameter.get("idShort") or ""),
                    value_type=(
                        str(parameter.get("valueType"))
                        if parameter.get("valueType") is not None
                        else None
                    ),
                )
                for parameter in _operation_variable_elements(operation_element)
            ]
            bindings.append(
                OperationBinding(
                    owner_asset_id=owner.global_asset_id,
                    skill_ref=skill.element_ref,
                    operation_ref=operation.element_ref,
                    submodel_endpoint=endpoint,
                    parameters=parameters,
                )
            )
        return bindings

    def _parse_reachability(self) -> dict[ElementRef, set[str]]:
        reachability: dict[ElementRef, set[str]] = {}
        for _, relationship in self._elements_with_semantic(CAN_REACH):
            skill = self.resolver.resolve_reference(
                _relationship_reference(relationship, "first")
            )
            target = self.resolver.resolve_reference(
                _relationship_reference(relationship, "second")
            )
            if skill.element_ref is None or target.canonical_id is None:
                self.diagnostics.append(
                    ValidationDiagnostic(
                        "CanReach",
                        "relationship has an unresolved Skill or target reference",
                    )
                )
                continue
            reachability.setdefault(skill.element_ref, set()).add(
                target.canonical_id
            )
        return reachability

    def _parse_skill_disabled(self) -> dict[ElementRef, object]:
        disabled: dict[ElementRef, object] = {}
        for skill_ref, skill in self._elements_with_semantic(
            IDTA_CONTROL_COMPONENT_SKILL
        ):
            property_value = next(
                (
                    element.get("value")
                    for element in self._descendants(skill)
                    if IDTA_SKILL_DISABLED in get_semantic_ids(element)
                ),
                None,
            )
            if property_value is not None:
                disabled[skill_ref] = property_value
        return disabled

    def _parse_state_definitions(self) -> list[ResourceStateDefinition]:
        definitions: list[ResourceStateDefinition] = []
        for semantic_id in STATE_SEMANTIC_IDS:
            for element_ref, element in self._elements_with_semantic(semantic_id):
                owner = self._owner(element_ref)
                if owner is None or not owner.is_instance:
                    continue
                definitions.append(
                    ResourceStateDefinition(
                        owner_asset_id=owner.global_asset_id,
                        semantic_id=semantic_id,
                        element_ref=element_ref,
                        current_value=element.get("value"),
                    )
                )
        return definitions

    @staticmethod
    def _descendants(element: dict) -> Iterator[dict]:
        for child in child_elements(element):
            yield child
            yield from SemanticParser._descendants(child)

    def _requirement_field(
        self, requirement: dict, semantic_id: str
    ) -> dict | None:
        return next(
            (
                element
                for element in self._descendants(requirement)
                if semantic_id in get_semantic_ids(element)
            ),
            None,
        )

    def _parse_process_requirements(self) -> list[ProcessRequirement]:
        process_submodels = {
            str(submodel.get("id") or "")
            for submodel in self.submodels
            if PROCESS_REQUIREMENTS in get_semantic_ids(submodel)
        }
        requirements: list[ProcessRequirement] = []
        for requirement_ref, requirement in self._elements_with_semantic(
            TRANSFER_REQUIREMENT
        ):
            if requirement_ref.submodel_id not in process_submodels:
                continue
            trigger_resource = self._requirement_field(
                requirement, TRIGGER_RESOURCE
            )
            trigger_condition = self._requirement_field(
                requirement, TRIGGER_CONDITION
            )
            required_capability = self._requirement_field(
                requirement, REQUIRED_CAPABILITY
            )
            source = self._requirement_field(requirement, TRANSFER_SOURCE)
            target = self._requirement_field(requirement, TRANSFER_TARGET)

            trigger_asset_id = self.resolver.canonical_entity_id(
                _referenced_value(trigger_resource or {})
            )
            condition_resolved = self.resolver.resolve_reference(
                _referenced_value(trigger_condition or {})
            )
            condition_semantics = (
                get_semantic_ids(condition_resolved.element)
                if condition_resolved.element is not None
                else (
                    {condition_resolved.canonical_id}
                    if condition_resolved.canonical_id
                    else set()
                )
            )
            required_resolved = self.resolver.resolve_reference(
                _referenced_value(required_capability or {})
            )
            capability_semantics = (
                get_semantic_ids(required_resolved.element) - {IDTA_CAPABILITY}
                if required_resolved.element is not None
                and IDTA_CAPABILITY in get_semantic_ids(required_resolved.element)
                and _has_qualifier_semantic(
                    required_resolved.element, IDTA_CAPABILITY_ROLE_REQUIRED
                )
                else set()
            )
            requirements.append(
                ProcessRequirement(
                    requirement_ref=requirement_ref,
                    id_short=str(requirement.get("idShort") or "") or None,
                    trigger_asset_id=trigger_asset_id,
                    trigger_semantic_id=next(iter(condition_semantics), None),
                    required_capability_semantics=capability_semantics,
                    source_id=self.resolver.canonical_entity_id(
                        _referenced_value(source or {})
                    ),
                    target_id=self.resolver.canonical_entity_id(
                        _referenced_value(target or {})
                    ),
                )
            )
        return requirements
