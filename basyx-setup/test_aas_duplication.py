from __future__ import annotations

import importlib.util
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree


SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPT_PATH = SCRIPT_DIR / "aas-duplication.py"
SPEC = importlib.util.spec_from_file_location("aas_duplication", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
aas_duplication = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(aas_duplication)

AAS_NAMESPACE = "https://admin-shell.io/aas/3/1"
NS = {"a": AAS_NAMESPACE}


def read_environment(package: Path) -> ElementTree.Element:
    with zipfile.ZipFile(package) as archive:
        xml_names = [name for name in archive.namelist() if name.endswith(".xml")]
        for name in xml_names:
            root = ElementTree.fromstring(archive.read(name))
            if root.tag == f"{{{AAS_NAMESPACE}}}environment":
                return root
    raise AssertionError(f"no AAS environment found in {package}")


def child_by_id_short(
    parent: ElementTree.Element, local_name: str, id_short: str
) -> ElementTree.Element:
    for child in parent.findall(f"a:{local_name}", NS):
        value = child.findtext("a:idShort", namespaces=NS)
        if value == id_short:
            return child
    raise AssertionError(f"{local_name}/{id_short} was not found")


class DuplicateStationTests(unittest.TestCase):
    def test_station_two_plan_maps_both_reachability_targets(self) -> None:
        robot_plan = aas_duplication.build_replacement_plans(1, 2)["robot"]
        self.assertEqual(
            robot_plan["urn:agent-aas:asset-instance:conveyor01"],
            "urn:agent-aas:asset-instance:conveyor02",
        )
        self.assertEqual(
            robot_plan["urn:agent-aas:entity:oip-factory01:pallet01"],
            "urn:agent-aas:entity:oip-factory01:pallet02",
        )
        self.assertEqual(robot_plan["MoveBoxConveyor01"], "MoveBoxConveyor02")
        self.assertEqual(robot_plan["MoveBoxPallet01"], "MoveBoxPallet02")

    def test_station_gets_own_pallet_and_two_local_reachability_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            aas_dir = Path(temp_dir)
            for name in ("conveyorbelt01.aasx", "robot01.aasx", "OIPFactory.aasx"):
                shutil.copy2(SCRIPT_DIR / "aas" / name, aas_dir / name)

            # Use a high station number so the test remains independent of
            # evaluation stations that may already exist in OIPFactory.aasx.
            aas_duplication.duplicate_station(aas_dir, 1, 99)

            robot_root = read_environment(aas_dir / "robot99.aasx")
            reachability = next(
                submodel
                for submodel in robot_root.findall("a:submodels/a:submodel", NS)
                if submodel.findtext("a:idShort", namespaces=NS) == "Reachability"
            )
            relationships = reachability.findall(
                "a:submodelElements/a:relationshipElement", NS
            )
            self.assertEqual(
                [item.findtext("a:idShort", namespaces=NS) for item in relationships],
                ["MoveBoxConveyor99", "MoveBoxPallet99"],
            )
            self.assertEqual(
                {
                    item.findtext("a:second/a:keys/a:key/a:value", namespaces=NS)
                    for item in relationships
                },
                {
                    "urn:agent-aas:asset-instance:conveyor99",
                    "urn:agent-aas:entity:oip-factory01:pallet99",
                },
            )

            factory_root = read_environment(aas_dir / "OIPFactory.aasx")
            hierarchy = next(
                submodel
                for submodel in factory_root.findall("a:submodels/a:submodel", NS)
                if submodel.findtext("a:idShort", namespaces=NS)
                == "HierarchicalStructures"
            )
            factory = child_by_id_short(
                hierarchy.find("a:submodelElements", NS), "entity", "OIPFactory"
            )
            statements = factory.find("a:statements", NS)
            pallet = child_by_id_short(statements, "entity", "Pallet99")
            self.assertEqual(
                pallet.findtext("a:globalAssetId", namespaces=NS),
                "urn:agent-aas:entity:oip-factory01:pallet99",
            )
            child_by_id_short(
                statements, "relationshipElement", "HasPartPallet99"
            )

            requirements = next(
                submodel
                for submodel in factory_root.findall("a:submodels/a:submodel", NS)
                if submodel.findtext("a:idShort", namespaces=NS)
                == "ProcessRequirements"
            )
            transfer = child_by_id_short(
                requirements.find("a:submodelElements", NS),
                "submodelElementCollection",
                "Transfer99",
            )
            values = {
                element.findtext("a:idShort", namespaces=NS): element
                for element in transfer.findall("a:value/a:referenceElement", NS)
            }
            self.assertEqual(
                values["Source"].findtext(
                    "a:value/a:keys/a:key/a:value", namespaces=NS
                ),
                "urn:agent-aas:asset-instance:conveyor99",
            )
            self.assertEqual(
                values["Target"].findall(
                    "a:value/a:keys/a:key/a:value", namespaces=NS
                )[-1].text,
                "Pallet99",
            )

    def test_live_factory_models_get_matching_station_elements(self) -> None:
        statements = []
        for kind in ("Robot", "Conveyor", "Pallet"):
            asset_id = (
                f"urn:agent-aas:asset-instance:{kind.lower()}01"
                if kind != "Pallet"
                else "urn:agent-aas:entity:oip-factory01:pallet01"
            )
            statements.extend(
                [
                    {
                        "modelType": "Entity",
                        "idShort": f"{kind}01",
                        "globalAssetId": asset_id,
                    },
                    {
                        "modelType": "RelationshipElement",
                        "idShort": f"HasPart{kind}01",
                        "second": {
                            "keys": [{"type": "GlobalReference", "value": asset_id}]
                        },
                    },
                ]
            )
        hierarchy = {
            "id": aas_duplication.FACTORY_HIERARCHY_ID,
            "submodelElements": [
                {
                    "modelType": "Entity",
                    "idShort": "OIPFactory",
                    "statements": statements,
                }
            ],
        }
        requirements = {
            "id": aas_duplication.FACTORY_REQUIREMENTS_ID,
            "submodelElements": [
                {
                    "modelType": "SubmodelElementCollection",
                    "idShort": "Transfer01",
                    "value": [
                        {
                            "modelType": "ReferenceElement",
                            "idShort": "Source",
                            "value": {
                                "keys": [
                                    {
                                        "type": "GlobalReference",
                                        "value": "urn:agent-aas:asset-instance:conveyor01",
                                    }
                                ]
                            },
                        },
                        {
                            "modelType": "ReferenceElement",
                            "idShort": "Target",
                            "value": {
                                "keys": [
                                    {
                                        "type": "SubmodelElement",
                                        "value": "Pallet01",
                                    },
                                    {
                                        "type": "GlobalReference",
                                        "value": "urn:agent-aas:entity:oip-factory01:pallet01",
                                    },
                                ]
                            },
                        },
                    ],
                }
            ],
        }

        updated_hierarchy, updated_requirements = (
            aas_duplication.update_factory_repository_models(
                hierarchy, requirements, 1, 13, overwrite=False
            )
        )

        updated_statements = updated_hierarchy["submodelElements"][0]["statements"]
        self.assertCountEqual(
            [element["idShort"] for element in updated_statements],
            [
                "Robot01",
                "Robot13",
                "Conveyor01",
                "Conveyor13",
                "Pallet01",
                "Pallet13",
                "HasPartRobot01",
                "HasPartRobot13",
                "HasPartConveyor01",
                "HasPartConveyor13",
                "HasPartPallet01",
                "HasPartPallet13",
            ],
        )
        transfer = updated_requirements["submodelElements"][-1]
        self.assertEqual(transfer["idShort"], "Transfer13")
        serialized = str(transfer)
        self.assertIn("conveyor13", serialized)
        self.assertIn("Pallet13", serialized)
        self.assertIn("pallet13", serialized)
        self.assertNotIn("conveyor01", serialized)
        self.assertNotIn("Pallet01", serialized)

        # The pure transformation must not mutate data returned by GET; those
        # originals are required for a best-effort rollback after a failed PUT.
        self.assertEqual(len(statements), 6)
        self.assertEqual(len(requirements["submodelElements"]), 1)


if __name__ == "__main__":
    unittest.main()
