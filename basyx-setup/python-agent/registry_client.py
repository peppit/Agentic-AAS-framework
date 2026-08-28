"""Generic BaSyx AAS/Submodel Registry discovery client."""

import asyncio
import base64
from collections.abc import AsyncIterator
from typing import Any

import httpx


class RegistryError(RuntimeError):
    """Raised when registry discovery returns an unusable response."""


def encode_identifier(identifier: str) -> str:
    """Return the AAS v3 base64url identifier path/query representation."""

    return base64.urlsafe_b64encode(identifier.encode("utf-8")).decode("ascii").rstrip("=")


def descriptor_endpoint(descriptor: dict) -> str:
    """Select an HTTP endpoint advertised by a registry descriptor."""

    endpoints = descriptor.get("endpoints")
    if not isinstance(endpoints, list):
        return ""
    fallback = ""
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        protocol = endpoint.get("protocolInformation") or endpoint.get(
            "protocol_information"
        )
        if not isinstance(protocol, dict):
            continue
        href = str(protocol.get("href") or "").strip()
        if not href:
            continue
        fallback = fallback or href
        if href.lower().startswith(("http://", "https://")):
            return href
    return fallback


def shell_submodel_ids(shell: object) -> list[str]:
    """Extract Submodel identifiers from an AAS model's ModelReferences."""

    if not isinstance(shell, dict):
        return []
    references = shell.get("submodels", [])
    if not isinstance(references, list):
        return []
    identifiers: list[str] = []
    for reference in references:
        if not isinstance(reference, dict):
            continue
        keys = reference.get("keys", [])
        if not isinstance(keys, list):
            continue
        identifier = next(
            (
                str(key.get("value") or "").strip()
                for key in keys
                if isinstance(key, dict)
                and str(key.get("type") or "").lower() == "submodel"
                and str(key.get("value") or "").strip()
            ),
            "",
        )
        if identifier:
            identifiers.append(identifier)
    return identifiers


class RegistryClient:
    """Discover descriptors and models without applying domain assumptions."""

    def __init__(
        self,
        aas_registry_url: str,
        submodel_registry_url: str,
        *,
        timeout_seconds: float = 8.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.aas_registry_url = aas_registry_url.rstrip("/")
        self.submodel_registry_url = submodel_registry_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds)
        )
        self.http_request_count = 0
        self.warnings: list[str] = []
        self._aas_descriptors_by_id: dict[str, dict] = {}
        self._submodel_descriptors_by_id: dict[str, dict] | None = None
        self._submodel_cache_lock = asyncio.Lock()

    async def __aenter__(self) -> "RegistryClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _get_json(
        self, url: str, *, params: dict[str, str] | None = None
    ) -> Any:
        self.http_request_count += 1
        try:
            response = await self._client.get(url, params=params)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise RegistryError(f"Timed out while requesting {url}: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:300].replace("\n", " ")
            raise RegistryError(
                f"Registry request {url} returned HTTP "
                f"{exc.response.status_code}: {body}"
            ) from exc
        except httpx.HTTPError as exc:
            raise RegistryError(f"Registry request {url} failed: {exc}") from exc
        try:
            return response.json()
        except ValueError as exc:
            raise RegistryError(f"Registry request {url} returned invalid JSON") from exc

    async def _pages(
        self, url: str, *, params: dict[str, str] | None = None
    ) -> AsyncIterator[list[dict]]:
        query = dict(params or {})
        seen_cursors: set[str] = set()
        while True:
            payload = await self._get_json(url, params=query)
            if isinstance(payload, list):
                result = payload
                metadata: dict = {}
            elif isinstance(payload, dict):
                result = payload.get("result", [])
                metadata = (
                    payload.get("paging_metadata")
                    or payload.get("pagingMetadata")
                    or {}
                )
            else:
                raise RegistryError(
                    f"Registry request {url} returned {type(payload).__name__}, "
                    "expected an object or list"
                )
            if not isinstance(result, list):
                raise RegistryError(
                    f"Registry request {url} has a non-list 'result' field"
                )
            yield [item for item in result if isinstance(item, dict)]
            cursor = str(metadata.get("cursor") or "").strip()
            if not cursor:
                return
            if cursor in seen_cursors:
                raise RegistryError(
                    f"Registry request {url} repeated pagination cursor {cursor!r}"
                )
            seen_cursors.add(cursor)
            query["cursor"] = cursor

    async def list_aas_descriptors(self) -> list[dict]:
        descriptors: list[dict] = []
        async for page in self._pages(
            f"{self.aas_registry_url}/shell-descriptors"
        ):
            descriptors.extend(page)
        self._aas_descriptors_by_id = {
            str(descriptor.get("id") or ""): descriptor
            for descriptor in descriptors
            if str(descriptor.get("id") or "").strip()
        }
        return descriptors

    async def _standalone_submodel_descriptors(self) -> dict[str, dict]:
        async with self._submodel_cache_lock:
            if self._submodel_descriptors_by_id is not None:
                return self._submodel_descriptors_by_id
            descriptors: list[dict] = []
            async for page in self._pages(
                f"{self.submodel_registry_url}/submodel-descriptors"
            ):
                descriptors.extend(page)
            self._submodel_descriptors_by_id = {
                str(descriptor.get("id") or ""): descriptor
                for descriptor in descriptors
                if str(descriptor.get("id") or "").strip()
            }
            return self._submodel_descriptors_by_id

    async def list_submodel_descriptors(self, aas_id: str) -> list[dict]:
        if not aas_id.strip():
            raise ValueError("aas_id must not be blank")
        aas_descriptor = self._aas_descriptors_by_id.get(aas_id, {})
        associated = aas_descriptor.get("submodelDescriptors", [])
        if not isinstance(associated, list) or not associated:
            associated = []
            async for page in self._pages(
                f"{self.aas_registry_url}/shell-descriptors/"
                f"{encode_identifier(aas_id)}/submodel-descriptors",
            ):
                associated.extend(page)

        if not associated:
            aas_endpoint = descriptor_endpoint(aas_descriptor)
            if not aas_endpoint:
                raise RegistryError(
                    f"AAS descriptor {aas_id!r} has no usable endpoint from "
                    "which to discover Submodel references"
                )
            shell = await self._get_json(aas_endpoint)
            associated = [
                {"id": submodel_id} for submodel_id in shell_submodel_ids(shell)
            ]

        # The AAS Registry establishes ownership. The standalone Submodel
        # Registry is authoritative for repository endpoints when it has the
        # same descriptors. If it is unavailable, the associated descriptors
        # remain usable and Phase 1 reports the warning without inventing URLs.
        try:
            standalone = await self._standalone_submodel_descriptors()
        except RegistryError as exc:
            self._submodel_descriptors_by_id = {}
            self.warnings.append(
                f"Submodel Registry discovery failed; using AAS Registry "
                f"descriptors for {aas_id}: {exc}"
            )
            standalone = {}
        return [
            standalone.get(str(descriptor.get("id") or ""), descriptor)
            for descriptor in associated
            if isinstance(descriptor, dict)
        ]

    async def fetch_submodel(self, descriptor: dict) -> dict:
        endpoint = descriptor_endpoint(descriptor)
        submodel_id = str(descriptor.get("id") or "<unknown>")
        if not endpoint:
            raise RegistryError(
                f"Submodel descriptor {submodel_id!r} has no usable endpoint"
            )
        payload = await self._get_json(endpoint)
        if not isinstance(payload, dict):
            raise RegistryError(
                f"Submodel endpoint {endpoint} returned {type(payload).__name__}, "
                "expected an object"
            )
        return payload

    # Compatibility with the names in the initial Phase 1 stub.
    list_shells = list_aas_descriptors
    list_submodels = list_submodel_descriptors
