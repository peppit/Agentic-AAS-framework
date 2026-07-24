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


if __name__ == "__main__":
    unittest.main()
