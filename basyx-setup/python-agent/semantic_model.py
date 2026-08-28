"""Typed semantic inventory and runtime records."""

import time
from dataclasses import dataclass, field


@dataclass(frozen=True, order=True)
class ElementRef:
    submodel_id: str
    id_short_path: str


@dataclass(frozen=True)
class ResolvedReference:
    reference_type: str
    canonical_id: str | None = None
    element_ref: ElementRef | None = None
    element: dict | None = field(default=None, compare=False, hash=False, repr=False)


@dataclass
class Resource:
    aas_id: str
    global_asset_id: str
    asset_type: str | None
    id_short: str | None
    submodel_endpoints: dict[str, str]
    asset_kind: str | None = None

    @property
    def is_instance(self) -> bool:
        return (self.asset_kind or "Instance").lower() != "type"


@dataclass
class CapabilityOffer:
    owner_asset_id: str
    capability_ref: ElementRef
    semantic_ids: set[str]
    skill_ref: ElementRef


@dataclass
class OperationParameter:
    semantic_ids: set[str]
    id_short: str
    value_type: str | None


@dataclass
class OperationBinding:
    owner_asset_id: str
    skill_ref: ElementRef
    operation_ref: ElementRef
    submodel_endpoint: str
    parameters: list[OperationParameter]


@dataclass
class ProcessRequirement:
    requirement_ref: ElementRef
    id_short: str | None
    trigger_asset_id: str | None
    trigger_semantic_id: str | None
    required_capability_semantics: set[str]
    source_id: str | None
    target_id: str | None


@dataclass
class ProcessJob:
    job_id: str
    requirement_ref: ElementRef
    trigger_asset_id: str
    trigger_semantic_id: str
    required_capability_semantic: str
    source_id: str
    target_id: str
    created_at: float = field(default_factory=time.monotonic)
    received_at_ms: int | None = None
    selected_resource_id: str | None = None
    candidate_count: int = 0
    reachable_candidate_count: int = 0
    available_candidate_count: int = 0
    matching_ms: float = 0.0
    reservation_ms: float = 0.0
    invocation_ms: float = 0.0


@dataclass
class ResourceStateDefinition:
    owner_asset_id: str
    semantic_id: str
    element_ref: ElementRef
    current_value: object | None = None


@dataclass
class CatalogMetrics:
    aas_descriptors: int = 0
    submodels: int = 0
    http_requests: int = 0
    resources: int = 0
    offered_capabilities: int = 0
    process_requirements: int = 0
    build_duration_seconds: float = 0.0


@dataclass
class ValidationDiagnostic:
    subject: str
    message: str
    severity: str = "warning"
