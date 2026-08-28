import unittest

import httpx

from registry_client import RegistryClient, descriptor_endpoint, encode_identifier
from semantic_catalog import SemanticCatalog
from semantics import ONTOPROCAP_CONVEYING, ONTOPROCAP_TRANSPORT, WORKPIECE_PRESENT
from test_semantic_discovery import fixture


def endpoint(href):
    return {
        "interface": "SUBMODEL-3.0",
        "protocolInformation": {"href": href},
    }


class RegistryClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_pagination_and_submodel_registry_endpoint_resolution(self):
        calls = []

        def handler(request):
            calls.append(str(request.url))
            if request.url.path == "/shell-descriptors":
                if request.url.params.get("cursor") == "page-2":
                    return httpx.Response(200, json={"result": []})
                return httpx.Response(
                    200,
                    json={
                        "result": [
                            {
                                "id": "urn:test:aas",
                                "submodelDescriptors": [
                                    {
                                        "id": "urn:test:sm",
                                        "endpoints": [endpoint("http://old/sm")],
                                    }
                                ],
                            }
                        ],
                        "paging_metadata": {"cursor": "page-2"},
                    },
                )
            if request.url.path == "/submodel-descriptors":
                return httpx.Response(
                    200,
                    json={
                        "result": [
                            {
                                "id": "urn:test:sm",
                                "endpoints": [endpoint("http://repo/submodels/encoded")],
                            }
                        ]
                    },
                )
            raise AssertionError(f"unexpected request: {request.url}")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = RegistryClient(
                "http://aas-registry",
                "http://sm-registry",
                client=http,
            )
            descriptors = await client.list_aas_descriptors()
            submodels = await client.list_submodel_descriptors("urn:test:aas")

        self.assertEqual(len(descriptors), 1)
        self.assertEqual(
            descriptor_endpoint(submodels[0]),
            "http://repo/submodels/encoded",
        )
        self.assertTrue(any("cursor=page-2" in call for call in calls))
        self.assertEqual(client.http_request_count, 3)

    async def test_aas_identifier_is_base64url_encoded_for_superpath(self):
        aas_id = "https://example.org/aas/one#instance"
        expected_path = (
            f"/shell-descriptors/{encode_identifier(aas_id)}/submodel-descriptors"
        )

        def handler(request):
            if request.url.path == "/shell-descriptors":
                return httpx.Response(
                    200,
                    json={
                        "result": [
                            {
                                "id": aas_id,
                                "endpoints": [endpoint("http://repo/shell")],
                            }
                        ]
                    },
                )
            if request.url.path == expected_path:
                return httpx.Response(200, json={"result": []})
            if request.url.path == "/shell":
                return httpx.Response(200, json={"id": aas_id, "submodels": []})
            if request.url.path == "/submodel-descriptors":
                return httpx.Response(200, json={"result": []})
            raise AssertionError(f"unexpected request: {request.url}")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = RegistryClient(
                "http://aas-registry", "http://sm-registry", client=http
            )
            await client.list_aas_descriptors()
            self.assertEqual(await client.list_submodel_descriptors(aas_id), [])

    async def test_fetch_submodel_uses_descriptor_href_verbatim(self):
        advertised = "http://another-repository/submodels/custom-token"

        def handler(request):
            self.assertEqual(str(request.url), advertised)
            return httpx.Response(
                200, json={"id": "urn:test:sm", "modelType": "Submodel"}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = RegistryClient(
                "http://aas-registry", "http://sm-registry", client=http
            )
            result = await client.fetch_submodel(
                {"id": "urn:test:sm", "endpoints": [endpoint(advertised)]}
            )
        self.assertEqual(result["id"], "urn:test:sm")

    async def test_catalog_builds_runtime_indexes_and_excludes_type_assets(self):
        _, submodels, resources, _ = fixture()
        submodels_by_id = {submodel["id"]: submodel for submodel in submodels}

        class FakeRegistry:
            http_request_count = 17
            warnings = []

            async def list_aas_descriptors(self):
                return [
                    {
                        "id": resource.aas_id,
                        "idShort": resource.id_short,
                        "globalAssetId": resource.global_asset_id,
                        "assetType": resource.asset_type,
                        "assetKind": resource.asset_kind,
                    }
                    for resource in resources
                ] + [
                    {
                        "id": "urn:test:aas:type",
                        "idShort": "AType",
                        "globalAssetId": "urn:test:type",
                        "assetKind": "Type",
                    }
                ]

            async def list_submodel_descriptors(self, aas_id):
                resource = next(
                    (item for item in resources if item.aas_id == aas_id), None
                )
                if resource is None:
                    return []
                return [
                    {
                        "id": submodel_id,
                        "endpoints": [endpoint(href)],
                        "_model": submodels_by_id[submodel_id],
                    }
                    for submodel_id, href in resource.submodel_endpoints.items()
                ]

            async def fetch_submodel(self, descriptor):
                return descriptor["_model"]

        catalog = await SemanticCatalog.discover(FakeRegistry())

        self.assertEqual(len(catalog.resources), 3)
        self.assertEqual(len(catalog.all_resources), 4)
        self.assertIn(ONTOPROCAP_TRANSPORT, catalog.capabilities_by_semantic_id)
        self.assertIn(ONTOPROCAP_CONVEYING, catalog.capabilities_by_semantic_id)
        self.assertIn(
            ("urn:test:conveyor", WORKPIECE_PRESENT),
            catalog.requirements_by_trigger,
        )
        self.assertIn("SEMANTIC AAS DISCOVERY", catalog.diagnostic_summary())


if __name__ == "__main__":
    unittest.main()
