import copy
import unittest

from aas_reference import ReferenceResolver
from semantic_model import ElementRef, Resource
from semantic_parser import SemanticParser, get_semantic_ids
from semantics import (
    CAN_REACH,
    IDTA_CAPABILITY,
    IDTA_CAPABILITY_REALIZED_BY,
    IDTA_CAPABILITY_ROLE_OFFERED,
    IDTA_CAPABILITY_ROLE_REQUIRED,
    IDTA_CONTROL_COMPONENT_SKILL,
    ONTOPROCAP_CONVEYING,
    ONTOPROCAP_TRANSPORT,
    PROCESS_REQUIREMENTS,
    REQUIRED_CAPABILITY,
    SKILL_INVOKED_BY_OPERATION,
    SOURCE_TRANSFER_LOCATION,
    TARGET_TRANSFER_LOCATION,
    TRANSFER_REQUIREMENT,
    TRANSFER_SOURCE,
    TRANSFER_TARGET,
    TRIGGER_CONDITION,
    TRIGGER_RESOURCE,
    WORKPIECE_PRESENT,
)


def external(identifier):
    return {
        "type": "ExternalReference",
        "keys": [{"type": "GlobalReference", "value": identifier}],
    }


def model(submodel_id, *path):
    return {
        "type": "ModelReference",
        "keys": [
            {"type": "Submodel", "value": submodel_id},
            *({"type": "SubmodelElementCollection", "value": part} for part in path),
        ],
    }


def semantic_element(model_type, id_short, semantic_id, **extra):
    return {
        "modelType": model_type,
        "idShort": id_short,
        "semanticId": external(semantic_id),
        **extra,
    }


def capability(id_short, domain, role):
    return {
        "modelType": "Capability",
        "idShort": id_short,
        "semanticId": external(IDTA_CAPABILITY),
        "supplementalSemanticIds": [external(domain)],
        "qualifiers": [{"semanticId": external(role), "value": "1"}],
    }


def fixture():
    robot_id = "urn:test:robot"
    conveyor_id = "urn:test:conveyor"
    factory_id = "urn:test:factory"
    pallet_id = "urn:test:pallet"
    resources = [
        Resource(
            "urn:test:aas:robot",
            robot_id,
            "urn:test:type:robot",
            "ResourceA",
            {
                "robot:cap": "http://repo/submodels/cap",
                "robot:skills": "http://repo/submodels/skills",
                "robot:ops": "http://repo/submodels/ops",
                "robot:reach": "http://repo/submodels/reach",
                "robot:state": "http://repo/submodels/state",
            },
            "Instance",
        ),
        Resource(
            "urn:test:aas:conveyor",
            conveyor_id,
            "urn:test:type:conveyor",
            "ResourceB",
            {
                "conveyor:cap": "http://repo/submodels/conveyor-cap",
                "conveyor:skills": "http://repo/submodels/conveyor-skills",
                "conveyor:state": "http://repo/submodels/conveyor-state",
            },
            "Instance",
        ),
        Resource(
            "urn:test:aas:factory",
            factory_id,
            "urn:test:type:factory",
            "ResourceC",
            {
                "factory:tree": "http://repo/submodels/tree",
                "factory:cap": "http://repo/submodels/factory-cap",
                "factory:req": "http://repo/submodels/requirements",
            },
            "Instance",
        ),
    ]
    asset_by_submodel = {
        submodel_id: resource
        for resource in resources
        for submodel_id in resource.submodel_endpoints
    }
    robot_cap_ref = model(
        "robot:cap", "Capabilities", "TransportContainer", "ArbitraryCapability"
    )
    robot_skill_ref = model("robot:skills", "Skills", "ArbitrarySkill")
    operation_ref = model("robot:ops", "ArbitraryOperation")
    factory_cap_ref = model(
        "factory:cap", "FactoryRequirements", "TransportContainer", "FactoryRequired"
    )
    submodels = [
        {
            "id": "robot:cap",
            "submodelElements": [
                {
                    "modelType": "SubmodelElementCollection",
                    "idShort": "Capabilities",
                    "value": [
                        {
                            "modelType": "SubmodelElementCollection",
                            "idShort": "TransportContainer",
                            "value": [
                                capability(
                                    "ArbitraryCapability",
                                    ONTOPROCAP_TRANSPORT,
                                    IDTA_CAPABILITY_ROLE_OFFERED,
                                )
                            ],
                        }
                    ],
                },
                semantic_element(
                    "RelationshipElement",
                    "RelationWithArbitraryName",
                    IDTA_CAPABILITY_REALIZED_BY,
                    first=robot_cap_ref,
                    second=robot_skill_ref,
                ),
            ],
        },
        {
            "id": "robot:skills",
            "submodelElements": [
                {
                    "modelType": "SubmodelElementCollection",
                    "idShort": "Skills",
                    "value": [
                        semantic_element(
                            "SubmodelElementCollection",
                            "ArbitrarySkill",
                            IDTA_CONTROL_COMPONENT_SKILL,
                            value=[],
                        )
                    ],
                }
            ],
        },
        {
            "id": "robot:ops",
            "submodelElements": [
                {
                    "modelType": "Operation",
                    "idShort": "ArbitraryOperation",
                    "inputVariables": [
                        {
                            "value": semantic_element(
                                "Property",
                                "ChangedSourceName",
                                SOURCE_TRANSFER_LOCATION,
                                valueType="xs:string",
                            )
                        },
                        {
                            "value": semantic_element(
                                "Property",
                                "ChangedTargetName",
                                TARGET_TRANSFER_LOCATION,
                                valueType="xs:string",
                            )
                        },
                    ],
                },
                semantic_element(
                    "RelationshipElement",
                    "BindingWithArbitraryName",
                    SKILL_INVOKED_BY_OPERATION,
                    first=robot_skill_ref,
                    second=operation_ref,
                ),
            ],
        },
        {
            "id": "robot:reach",
            "submodelElements": [
                semantic_element(
                    "RelationshipElement",
                    "ReachA",
                    CAN_REACH,
                    first=robot_skill_ref,
                    second=external(conveyor_id),
                ),
                semantic_element(
                    "RelationshipElement",
                    "ReachB",
                    CAN_REACH,
                    first=robot_skill_ref,
                    second=external(pallet_id),
                ),
            ],
        },
        {
            "id": "robot:state",
            "submodelElements": [
                semantic_element(
                    "Property", "AnyAvailabilityName", "urn:agent-aas:semantics:AvailableForScheduling:1", value=True
                )
            ],
        },
        {
            "id": "conveyor:cap",
            "submodelElements": [
                capability(
                    "AnotherArbitraryCapability",
                    ONTOPROCAP_CONVEYING,
                    IDTA_CAPABILITY_ROLE_OFFERED,
                ),
                semantic_element(
                    "RelationshipElement",
                    "ConveyorRealization",
                    IDTA_CAPABILITY_REALIZED_BY,
                    first=model("conveyor:cap", "AnotherArbitraryCapability"),
                    second=model("conveyor:skills", "Skills", "ConveySkill"),
                ),
            ],
        },
        {
            "id": "conveyor:skills",
            "submodelElements": [
                {
                    "modelType": "SubmodelElementCollection",
                    "idShort": "Skills",
                    "value": [
                        semantic_element(
                            "SubmodelElementCollection",
                            "ConveySkill",
                            IDTA_CONTROL_COMPONENT_SKILL,
                            value=[],
                        )
                    ],
                }
            ],
        },
        {
            "id": "conveyor:state",
            "submodelElements": [
                semantic_element(
                    "Property", "RenamableSensor", WORKPIECE_PRESENT, value=False
                )
            ],
        },
        {
            "id": "factory:tree",
            "submodelElements": [
                {
                    "modelType": "Entity",
                    "idShort": "OIPFactory",
                    "entityType": "SelfManagedEntity",
                    "globalAssetId": factory_id,
                    "statements": [
                        {
                            "modelType": "Entity",
                            "idShort": "Pallet",
                            "entityType": "CoManagedEntity",
                            "globalAssetId": pallet_id,
                        }
                    ],
                }
            ],
        },
        {
            "id": "factory:cap",
            "submodelElements": [
                {
                    "modelType": "SubmodelElementCollection",
                    "idShort": "FactoryRequirements",
                    "value": [
                        {
                            "modelType": "SubmodelElementCollection",
                            "idShort": "TransportContainer",
                            "value": [
                                capability(
                                    "FactoryRequired",
                                    ONTOPROCAP_TRANSPORT,
                                    IDTA_CAPABILITY_ROLE_REQUIRED,
                                )
                            ],
                        }
                    ],
                }
            ],
        },
        {
            "id": "factory:req",
            "semanticId": external(PROCESS_REQUIREMENTS),
            "submodelElements": [
                semantic_element(
                    "SubmodelElementCollection",
                    "ArbitraryRequirementName",
                    TRANSFER_REQUIREMENT,
                    value=[
                        semantic_element(
                            "ReferenceElement", "A", TRIGGER_RESOURCE, value=external(conveyor_id)
                        ),
                        semantic_element(
                            "ReferenceElement", "B", TRIGGER_CONDITION, value=external(WORKPIECE_PRESENT)
                        ),
                        semantic_element(
                            "ReferenceElement", "C", REQUIRED_CAPABILITY, value=factory_cap_ref
                        ),
                        semantic_element(
                            "ReferenceElement", "D", TRANSFER_SOURCE, value=external(conveyor_id)
                        ),
                        semantic_element(
                            "ReferenceElement",
                            "E",
                            TRANSFER_TARGET,
                            value=model("factory:tree", "OIPFactory", "Pallet"),
                        ),
                    ],
                )
            ],
        },
    ]
    parser = SemanticParser(resources, submodels, asset_by_submodel)
    return parser.parse(), submodels, resources, asset_by_submodel


class SemanticDiscoveryTests(unittest.TestCase):
    def test_semantic_ids_combine_primary_and_supplemental(self):
        element = {
            "semanticId": external(IDTA_CAPABILITY),
            "supplementalSemanticIds": [external(ONTOPROCAP_TRANSPORT)],
        }
        self.assertEqual(
            get_semantic_ids(element),
            {IDTA_CAPABILITY, ONTOPROCAP_TRANSPORT},
        )

    def test_capability_domains_distinguish_transport_and_conveying(self):
        inventory, *_ = fixture()
        domains = {domain for offer in inventory.capability_offers for domain in offer.semantic_ids}
        self.assertIn(ONTOPROCAP_TRANSPORT, domains)
        self.assertIn(ONTOPROCAP_CONVEYING, domains)

    def test_capability_realized_by_resolves_capability_to_skill(self):
        inventory, *_ = fixture()
        transport = next(
            offer
            for offer in inventory.capability_offers
            if ONTOPROCAP_TRANSPORT in offer.semantic_ids
        )
        self.assertEqual(
            transport.skill_ref,
            ElementRef("robot:skills", "Skills.ArbitrarySkill"),
        )

    def test_skill_invoked_by_operation_resolves_binding_and_parameters(self):
        inventory, *_ = fixture()
        binding = next(
            binding
            for binding in inventory.operation_bindings
            if binding.skill_ref == ElementRef("robot:skills", "Skills.ArbitrarySkill")
        )
        self.assertEqual(binding.operation_ref, ElementRef("robot:ops", "ArbitraryOperation"))
        self.assertEqual(
            {semantic for parameter in binding.parameters for semantic in parameter.semantic_ids},
            {SOURCE_TRANSFER_LOCATION, TARGET_TRANSFER_LOCATION},
        )

    def test_can_reach_external_targets_are_canonical(self):
        inventory, *_ = fixture()
        self.assertEqual(
            inventory.reachability_by_skill_ref[
                ElementRef("robot:skills", "Skills.ArbitrarySkill")
            ],
            {"urn:test:conveyor", "urn:test:pallet"},
        )

    def test_pallet_model_and_external_references_normalize_equally(self):
        inventory, *_ = fixture()
        model_identity = inventory.resolver.canonical_entity_id(
            model("factory:tree", "OIPFactory", "Pallet")
        )
        external_identity = inventory.resolver.canonical_entity_id(
            external("urn:test:pallet")
        )
        self.assertEqual(model_identity, external_identity)

    def test_process_requirement_resolves_complete_semantic_fields(self):
        inventory, *_ = fixture()
        requirement = inventory.process_requirements[0]
        self.assertEqual(requirement.trigger_asset_id, "urn:test:conveyor")
        self.assertEqual(requirement.trigger_semantic_id, WORKPIECE_PRESENT)
        self.assertEqual(
            requirement.required_capability_semantics,
            {ONTOPROCAP_TRANSPORT},
        )
        self.assertEqual(requirement.source_id, "urn:test:conveyor")
        self.assertEqual(requirement.target_id, "urn:test:pallet")

    def test_id_short_change_does_not_change_state_recognition(self):
        _, submodels, resources, asset_by_submodel = fixture()
        renamed = copy.deepcopy(submodels)
        state = next(item for item in renamed if item["id"] == "conveyor:state")
        state["submodelElements"][0]["idShort"] = "CompletelyDifferentName"
        inventory = SemanticParser(resources, renamed, asset_by_submodel).parse()
        definition = next(
            item
            for item in inventory.state_definitions
            if item.semantic_id == WORKPIECE_PRESENT
        )
        self.assertEqual(
            definition.element_ref.id_short_path, "CompletelyDifferentName"
        )


if __name__ == "__main__":
    unittest.main()
