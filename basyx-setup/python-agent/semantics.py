"""Semantic vocabulary used by Phase 1 AAS discovery.

Only identifiers present in the current AAS packages belong here. Asset,
shell, submodel, and element instance identifiers intentionally do not.
"""

ONTOPROCAP_TRANSPORT = "http://css.iat.rwth-aachen.de/OntoProCap#Transport"
ONTOPROCAP_CONVEYING = "http://css.iat.rwth-aachen.de/OntoProCap#Conveying"

WORKPIECE_PRESENT = "urn:agent-aas:semantics:WorkpiecePresent:1"
FAULT_ACTIVE = "urn:agent-aas:semantics:FaultActive:1"
AVAILABLE_FOR_SCHEDULING = (
    "urn:agent-aas:semantics:AvailableForScheduling:1"
)
IS_MOVING = "urn:agent-aas:semantics:IsMoving:1"
CAN_REACH = "urn:agent-aas:semantics:CanReach:1"
SKILL_INVOKED_BY_OPERATION = (
    "urn:agent-aas:semantics:SkillInvokedByOperation:1"
)
SOURCE_TRANSFER_LOCATION = (
    "urn:agent-aas:semantics:SourceTransferLocation:1"
)
TARGET_TRANSFER_LOCATION = (
    "urn:agent-aas:semantics:TargetTransferLocation:1"
)
PROCESS_REQUIREMENTS = "urn:agent-aas:semantics:ProcessRequirements:1"
TRANSFER_REQUIREMENT = "urn:agent-aas:semantics:TransferRequirement:1"
TRIGGER_RESOURCE = "urn:agent-aas:semantics:TriggerResource:1"
TRIGGER_CONDITION = "urn:agent-aas:semantics:TriggerCondition:1"
REQUIRED_CAPABILITY = "urn:agent-aas:semantics:RequiredCapability:1"
TRANSFER_SOURCE = "urn:agent-aas:semantics:TransferSource:1"
TRANSFER_TARGET = "urn:agent-aas:semantics:TransferTarget:1"

# IDTA Capability Description 1.0 identifiers in the current packages.
IDTA_CAPABILITY = (
    "https://admin-shell.io/idta/CapabilityDescription/Capability/1/0"
)
IDTA_CAPABILITY_REALIZED_BY = (
    "https://admin-shell.io/idta/CapabilityDescription/CapabilityRealizedBy/1/0"
)
IDTA_CAPABILITY_ROLE_OFFERED = (
    "https://admin-shell.io/idta/CapabilityDescription/"
    "CapabilityRoleQualifier/Offered/1/0"
)
IDTA_CAPABILITY_ROLE_REQUIRED = (
    "https://admin-shell.io/idta/CapabilityDescription/"
    "CapabilityRoleQualifier/Required/1/0"
)

# IDTA Control Component 2.0 identifiers in the current packages.
IDTA_CONTROL_COMPONENT_SKILL = (
    "https://admin-shell.io/idta/ControlComponent/Skill/2/0"
)
IDTA_SKILL_PARAMETER = (
    "https://admin-shell.io/idta/ControlComponent/Skill/Parameter/2/0"
)

# IDTA Hierarchical Structures 1.0 identifier used by co-managed entities.
IDTA_HIERARCHICAL_STRUCTURES_NODE = (
    "https://admin-shell.io/idta/HierarchicalStructures/Node/1/0"
)

STATE_SEMANTIC_IDS = frozenset(
    {
        WORKPIECE_PRESENT,
        FAULT_ACTIVE,
        AVAILABLE_FOR_SCHEDULING,
        IS_MOVING,
    }
)

# Compatibility aliases for the names used by the initial Phase 1 stub.
CAPABILITY = IDTA_CAPABILITY
CAPABILITY_REALIZED_BY = IDTA_CAPABILITY_REALIZED_BY
OFFERED = IDTA_CAPABILITY_ROLE_OFFERED
REQUIRED = IDTA_CAPABILITY_ROLE_REQUIRED
SKILL = IDTA_CONTROL_COMPONENT_SKILL
AVAILABLE = AVAILABLE_FOR_SCHEDULING
SOURCE_LOCATION = SOURCE_TRANSFER_LOCATION
TARGET_LOCATION = TARGET_TRANSFER_LOCATION
