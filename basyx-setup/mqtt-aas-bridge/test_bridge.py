import importlib.util
import json
import sys
import unittest
from pathlib import Path

import httpx


MODULE_PATH = Path(__file__).with_name("bridge.py")
SPEC = importlib.util.spec_from_file_location("telemetry_bridge", MODULE_PATH)
bridge = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = bridge
SPEC.loader.exec_module(bridge)


ASSET = "urn:agent-aas:asset-instance:conveyor01"
SEMANTIC = "urn:agent-aas:semantics:WorkpiecePresent:1"
SUBMODEL_ID = "urn:test:state:conveyor01"
SUBMODEL_ENDPOINT = "http://repo/submodels/state-token"


def semantic_reference(identifier):
    return {
        "type": "ExternalReference",
        "keys": [{"type": "GlobalReference", "value": identifier}],
    }


def descriptor(asset_id=ASSET):
    return {
        "id": "urn:test:aas:conveyor01",
        "globalAssetId": asset_id,
        "assetKind": "Instance",
        "submodelDescriptors": [{"id": SUBMODEL_ID}],
    }


def submodel(property_id_short="AnythingAtAll"):
    return {
        "modelType": "Submodel",
        "id": SUBMODEL_ID,
        "submodelElements": [
            {
                "modelType": "SubmodelElementCollection",
                "idShort": "RuntimeState",
                "value": [
                    {
                        "modelType": "Property",
                        "idShort": property_id_short,
                        "valueType": "xs:boolean",
                        "semanticId": semantic_reference(SEMANTIC),
                        "value": "false",
                    }
                ],
            }
        ],
    }


def config(**overrides):
    values = {
        "aas_registry_url": "http://aas-registry",
        "submodel_registry_url": "http://sm-registry",
        "update_retry_count": 1,
    }
    values.update(overrides)
    return bridge.Config(**values)


class TelemetryContractTests(unittest.TestCase):
    def test_canonical_payload_is_not_coupled_to_topic_or_local_names(self):
        event = bridge.parse_telemetry(
            json.dumps(
                {
                    "assetId": ASSET,
                    "semanticId": SEMANTIC,
                    "value": True,
                    "eventId": "sensor-42",
                }
            ).encode()
        )
        self.assertEqual(
            event, bridge.TelemetryEvent(ASSET, SEMANTIC, True, "sensor-42")
        )

    def test_payload_requires_canonical_routing_fields(self):
        with self.assertRaisesRegex(ValueError, "assetId"):
            bridge.parse_telemetry(
                json.dumps({"semanticId": SEMANTIC, "value": True}).encode()
            )
        with self.assertRaisesRegex(ValueError, "semanticId"):
            bridge.parse_telemetry(
                json.dumps({"assetId": ASSET, "value": True}).encode()
            )
        with self.assertRaisesRegex(ValueError, "no value"):
            bridge.parse_telemetry(
                json.dumps({"assetId": ASSET, "semanticId": SEMANTIC}).encode()
            )

    def test_aas_value_types_are_coerced(self):
        self.assertTrue(bridge.coerce_value("true", "xs:boolean"))
        self.assertEqual(bridge.coerce_value("7", "xs:int"), 7)
        self.assertEqual(bridge.coerce_value(1, "xs:double"), 1.0)


class SemanticRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovers_renamed_nested_property_from_semantics(self):
        def handler(request):
            if request.url.host == "aas-registry" and request.url.path == "/shell-descriptors":
                return httpx.Response(200, json={"result": [descriptor()]})
            if request.url.host == "sm-registry" and request.url.path == "/submodel-descriptors":
                return httpx.Response(
                    200,
                    json={
                        "result": [
                            {
                                "id": SUBMODEL_ID,
                                "endpoints": [
                                    {
                                        "protocolInformation": {
                                            "href": SUBMODEL_ENDPOINT
                                        }
                                    }
                                ],
                            }
                        ]
                    },
                )
            if request.url.host == "repo" and request.url.path == "/submodels/state-token":
                return httpx.Response(200, json=submodel("HeterogeneousSensorName"))
            raise AssertionError(f"Unexpected request: {request.url}")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            catalog = bridge.SemanticRegistry(config(), http)
            bindings, diagnostics = await catalog.discover()

        self.assertEqual(diagnostics, [])
        self.assertEqual(
            bindings[(ASSET, SEMANTIC)],
            bridge.SignalBinding(
                ASSET,
                SEMANTIC,
                SUBMODEL_ENDPOINT,
                "RuntimeState.HeterogeneousSensorName",
                "xs:boolean",
            ),
        )

    async def test_ambiguous_semantic_property_is_rejected(self):
        ambiguous = submodel("FirstName")
        ambiguous["submodelElements"].append(
            {
                "modelType": "Property",
                "idShort": "SecondName",
                "valueType": "xs:boolean",
                "semanticId": semantic_reference(SEMANTIC),
            }
        )

        def handler(request):
            if request.url.host == "aas-registry":
                return httpx.Response(200, json={"result": [descriptor()]})
            if request.url.host == "sm-registry":
                return httpx.Response(
                    200,
                    json={
                        "result": [
                            {
                                "id": SUBMODEL_ID,
                                "endpoints": [
                                    {"protocolInformation": {"href": SUBMODEL_ENDPOINT}}
                                ],
                            }
                        ]
                    },
                )
            return httpx.Response(200, json=ambiguous)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            bindings, diagnostics = await bridge.SemanticRegistry(config(), http).discover()

        self.assertNotIn((ASSET, SEMANTIC), bindings)
        self.assertTrue(any("ambiguous" in item for item in diagnostics))

    async def test_refresh_adds_and_removes_assets_without_restart(self):
        active_assets = [ASSET]

        def handler(request):
            if request.url.host == "aas-registry":
                return httpx.Response(
                    200, json={"result": [descriptor(item) for item in active_assets]}
                )
            if request.url.host == "sm-registry":
                return httpx.Response(
                    200,
                    json={
                        "result": [
                            {
                                "id": SUBMODEL_ID,
                                "endpoints": [
                                    {"protocolInformation": {"href": SUBMODEL_ENDPOINT}}
                                ],
                            }
                        ]
                    },
                )
            return httpx.Response(200, json=submodel())

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            telemetry_bridge = bridge.TelemetryBridge(config(), http=http)
            self.assertTrue(await telemetry_bridge.refresh_catalog())
            self.assertIn((ASSET, SEMANTIC), telemetry_bridge.bindings)

            robot = "urn:agent-aas:asset-instance:robot02"
            active_assets.append(robot)
            self.assertTrue(await telemetry_bridge.refresh_catalog())
            self.assertIn((robot, SEMANTIC), telemetry_bridge.bindings)

            active_assets.remove(ASSET)
            self.assertTrue(await telemetry_bridge.refresh_catalog())
            self.assertNotIn((ASSET, SEMANTIC), telemetry_bridge.bindings)
            self.assertIn((robot, SEMANTIC), telemetry_bridge.bindings)

    async def test_patch_uses_discovered_endpoint_and_idshort_path(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(204)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            telemetry_bridge = bridge.TelemetryBridge(config(), http=http)
            event = bridge.TelemetryEvent(ASSET, SEMANTIC, True)
            binding = bridge.SignalBinding(
                ASSET,
                SEMANTIC,
                SUBMODEL_ENDPOINT,
                "Runtime State.Sensor Name",
                "xs:boolean",
            )
            await telemetry_bridge.update_aas(event, binding)

        self.assertEqual(requests[0].method, "PATCH")
        self.assertEqual(
            requests[0].url.raw_path,
            b"/submodels/state-token/submodel-elements/Runtime%20State.Sensor%20Name/$value",
        )
        self.assertEqual(json.loads(requests[0].content), "true")


if __name__ == "__main__":
    unittest.main()
