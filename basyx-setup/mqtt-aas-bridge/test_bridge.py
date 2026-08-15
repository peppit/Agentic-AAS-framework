import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("bridge.py")
SPEC = importlib.util.spec_from_file_location("telemetry_bridge", MODULE_PATH)
bridge = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(bridge)


class StationRegistryTests(unittest.TestCase):
    def test_load_seed_bindings_accepts_canonical_station_03(self):
        registry = {
            "schemaVersion": "1.0",
            "stations": {
                "station_03": {
                    "stationId": "Station_03",
                    "conveyorSubmodelB64": "conveyor-state-3",
                    "robotStateSubmodelB64": "robot-state-3",
                    "conveyorProperties": {
                        "boxDetected": {
                            "idShort": "BoxAtStationThree",
                            "type": "boolean",
                        }
                    },
                }
            },
        }

        with tempfile.TemporaryDirectory() as tempdir:
            registry_path = Path(tempdir) / "stations.json"
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            bindings = bridge.load_seed_bindings(str(registry_path))

        self.assertIn("station_03", bindings)
        self.assertEqual(
            bindings["station_03"]["boxDetected"],
            bridge.SignalBinding(
                "conveyor-state-3",
                "BoxAtStationThree",
                "boolean",
            ),
        )
        self.assertEqual(
            bindings["station_03"]["isMoving"],
            bridge.SignalBinding("robot-state-3", "IsMoving", "bool"),
        )

    def test_robot_bindings_are_keyed_by_robot_id(self):
        registry = {
            "schemaVersion": "2.0",
            "stations": {
                "station_01": {"stationId": "Station_01"},
            },
            "robots": {
                "robot_01": {
                    "robotId": "Robot_01",
                    "stateSubmodelB64": "robot-state-1",
                },
                "robot_02": {
                    "robotId": "Robot_02",
                    "stateSubmodelB64": "robot-state-2",
                },
            },
            "conveyors": {},
            "stationAssets": [
                {
                    "stationId": "Station_01",
                    "assetType": "robot",
                    "assetId": "Robot_01",
                },
                {
                    "stationId": "Station_01",
                    "assetType": "robot",
                    "assetId": "Robot_02",
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tempdir:
            registry_path = Path(tempdir) / "stations.json"
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            bindings = bridge.load_robot_bindings(str(registry_path))

        self.assertEqual(
            bindings["robot_01"]["faultActive"],
            bridge.SignalBinding("robot-state-1", "FaultActive", "bool"),
        )
        self.assertEqual(
            bindings["robot_02"]["faultActive"],
            bridge.SignalBinding("robot-state-2", "FaultActive", "bool"),
        )

    def test_parse_robot_fault_telemetry(self):
        result = bridge.parse_telemetry(
            "factory/robots/Robot_01/telemetry/faultActive",
            json.dumps(
                {
                    "value": True,
                    "robotId": "Robot_01",
                    "eventId": "fault-1",
                }
            ).encode(),
        )

        self.assertEqual(
            result,
            ("robot", "robot_01", "faultActive", True, "fault-1"),
        )

    def test_robot_topic_rejects_mismatched_payload_robot(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            bridge.parse_telemetry(
                "factory/robots/Robot_01/telemetry/faultActive",
                json.dumps({"value": True, "robotId": "Robot_02"}).encode(),
            )

    def test_v2_conveyor_bindings_and_topic_are_asset_scoped(self):
        registry = {
            "schemaVersion": "2.0",
            "stations": {"station_01": {"stationId": "Station_01"}},
            "robots": {},
            "conveyors": {
                "conveyor_01": {
                    "conveyorId": "Conveyor_01",
                    "stateSubmodelB64": "conveyor-state-1",
                    "properties": {
                        "boxDetected": {
                            "idShort": "BoxPresentOne",
                            "type": "boolean",
                        }
                    },
                },
                "conveyor_02": {
                    "conveyorId": "Conveyor_02",
                    "stateSubmodelB64": "conveyor-state-2",
                },
            },
            "stationAssets": [
                {
                    "stationId": "Station_01",
                    "assetType": "conveyor",
                    "assetId": "Conveyor_01",
                },
                {
                    "stationId": "Station_01",
                    "assetType": "conveyor",
                    "assetId": "Conveyor_02",
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tempdir:
            registry_path = Path(tempdir) / "stations.json"
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            conveyor_bindings = bridge.load_conveyor_bindings(str(registry_path))

        self.assertEqual(
            conveyor_bindings["conveyor_01"]["boxDetected"],
            bridge.SignalBinding("conveyor-state-1", "BoxPresentOne", "boolean"),
        )
        self.assertEqual(
            bridge.parse_telemetry(
                "factory/conveyors/Conveyor_02/telemetry/currentSpeed",
                json.dumps({"value": 1.5, "conveyorId": "Conveyor_02"}).encode(),
            ),
            ("conveyor", "conveyor_02", "currentSpeed", 1.5, None),
        )

    def test_legacy_simulation_station_topic_remains_supported(self):
        self.assertEqual(
            bridge.parse_telemetry(
                "simulation/Station_01/boxDetected",
                json.dumps({"boxDetected": True}).encode(),
            ),
            ("station", "station_01", "boxDetected", True, None),
        )


if __name__ == "__main__":
    unittest.main()
