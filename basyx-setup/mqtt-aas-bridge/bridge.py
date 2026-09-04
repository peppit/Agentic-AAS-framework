"""Semantic MQTT-to-AAS telemetry bridge.

Telemetry contains canonical AAS identities. Registry descriptors and the
semantic IDs in Submodels are the only routing configuration; local asset,
station, Submodel, and Property names are deliberately not interpreted here.
"""

import asyncio
import base64
import json
import os
from collections import deque
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from aiomqtt import Client as MqttClient
from aiomqtt import MqttError


@dataclass(frozen=True)
class SignalBinding:
    asset_id: str
    semantic_id: str
    submodel_endpoint: str
    element_path: str
    value_type: str


@dataclass(frozen=True)
class TelemetryEvent:
    asset_id: str
    semantic_id: str
    value: Any
    event_id: str | None = None


@dataclass(frozen=True)
class Config:
    mqtt_host: str = os.getenv("MQTT_HOST", "mosquitto")
    mqtt_port: int = int(os.getenv("MQTT_PORT", "1883"))
    telemetry_topic: str = os.getenv("MQTT_TELEMETRY_TOPIC", "oip/telemetry")
    aas_registry_url: str = os.getenv(
        "AAS_REGISTRY_URL", "http://aas-registry:8080"
    )
    submodel_registry_url: str = os.getenv(
        "SUBMODEL_REGISTRY_URL", "http://sm-registry:8080"
    )
    registry_refresh_seconds: float = float(
        os.getenv("REGISTRY_REFRESH_SECONDS", "5")
    )
    update_retry_count: int = int(os.getenv("AAS_UPDATE_RETRY_COUNT", "5"))
    retry_base_seconds: float = float(os.getenv("AAS_RETRY_BASE_SECONDS", "0.2"))
    http_timeout_seconds: float = float(os.getenv("HTTP_TIMEOUT_SECONDS", "8"))
    mqtt_reconnect_seconds: float = float(os.getenv("MQTT_RECONNECT_SECONDS", "2"))
    fault_topic: str = os.getenv("FAULT_TOPIC", "oip/fault/telemetry-bridge")
    queue_size: int = int(os.getenv("ASSET_QUEUE_SIZE", "1000"))
    dedup_window: int = int(os.getenv("EVENT_DEDUP_WINDOW", "4096"))


class RegistryError(RuntimeError):
    """Raised when semantic routing cannot be discovered from the Registries."""


def encode_identifier(identifier: str) -> str:
    return base64.urlsafe_b64encode(identifier.encode()).decode().rstrip("=")


def descriptor_endpoint(descriptor: object) -> str:
    if not isinstance(descriptor, dict):
        return ""
    endpoints = descriptor.get("endpoints", [])
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
        if href:
            fallback = fallback or href
            if href.lower().startswith(("http://", "https://")):
                return href
    return fallback


def shell_submodel_ids(shell: object) -> list[str]:
    """Extract Submodel IDs from an AAS model's ModelReferences."""

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


def semantic_ids(element: object) -> set[str]:
    if not isinstance(element, dict):
        return set()
    result = _reference_values(element.get("semanticId"))
    supplemental = element.get("supplementalSemanticIds", [])
    if isinstance(supplemental, dict):
        supplemental = [supplemental]
    if isinstance(supplemental, list):
        for reference in supplemental:
            result.update(_reference_values(reference))
    return result


def model_type(element: object) -> str:
    if not isinstance(element, dict):
        return ""
    value = element.get("modelType")
    if isinstance(value, dict):
        value = value.get("name")
    return str(value or "")


def _child_elements(element: dict) -> Iterator[dict]:
    if model_type(element).lower() == "operation":
        for field in ("inputVariables", "outputVariables", "inoutputVariables"):
            variables = element.get(field, [])
            if isinstance(variables, list):
                for variable in variables:
                    value = variable.get("value") if isinstance(variable, dict) else None
                    if isinstance(value, dict):
                        yield value
    for field in ("value", "statements", "annotations"):
        children = element.get(field)
        if isinstance(children, list):
            yield from (child for child in children if isinstance(child, dict))


def walk_elements(elements: object, prefix: str = "") -> Iterator[tuple[str, dict]]:
    if not isinstance(elements, list):
        return
    for index, element in enumerate(elements):
        if not isinstance(element, dict):
            continue
        id_short = str(element.get("idShort") or "").strip()
        segment = id_short or f"@{model_type(element) or 'Element'}[{index}]"
        path = f"{prefix}.{segment}" if prefix else segment
        yield path, element
        yield from walk_elements(list(_child_elements(element)), path)


def parse_telemetry(payload: bytes) -> TelemetryEvent:
    decoded = json.loads(payload.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("Telemetry payload must be a JSON object")
    asset_id = str(decoded.get("assetId") or "").strip()
    semantic_id = str(decoded.get("semanticId") or "").strip()
    if not asset_id:
        raise ValueError("Telemetry payload has no assetId")
    if not semantic_id:
        raise ValueError("Telemetry payload has no semanticId")
    if "value" not in decoded:
        raise ValueError("Telemetry payload has no value")
    event_id_raw = decoded.get("eventId", decoded.get("sequence"))
    event_id = str(event_id_raw) if event_id_raw is not None else None
    return TelemetryEvent(asset_id, semantic_id, decoded["value"], event_id)


def coerce_value(value: Any, value_type: str) -> Any:
    normalized = value_type.strip().lower()
    for prefix in ("xs:", "xsd:"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
    if normalized in {"bool", "boolean"}:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            return value.strip().lower() == "true"
        raise ValueError("value must be boolean")
    if normalized in {"float", "double", "decimal"}:
        if isinstance(value, bool):
            raise ValueError("value must be numeric")
        return float(value)
    integers = {
        "byte", "short", "int", "integer", "long", "unsignedbyte",
        "unsignedshort", "unsignedint", "unsignedlong",
    }
    if normalized in integers:
        if isinstance(value, bool):
            raise ValueError("value must be an integer")
        return int(value)
    if normalized in {"string", "anyuri", "datetime", "date", "time"}:
        return str(value)
    raise ValueError(f"Unsupported AAS value type {value_type!r}")


class SemanticRegistry:
    """Build telemetry routes from AAS ownership and Property semantics."""

    def __init__(self, config: Config, http: httpx.AsyncClient):
        self.config = config
        self.http = http

    async def _get_json(self, url: str, *, params: dict | None = None) -> Any:
        try:
            response = await self.http.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RegistryError(f"Registry request {url} failed: {exc}") from exc

    async def _pages(self, url: str) -> AsyncIterator[list[dict]]:
        params: dict[str, str] = {}
        seen: set[str] = set()
        while True:
            payload = await self._get_json(url, params=params)
            if isinstance(payload, list):
                result, metadata = payload, {}
            elif isinstance(payload, dict):
                result = payload.get("result", [])
                metadata = payload.get("pagingMetadata") or payload.get(
                    "paging_metadata"
                ) or {}
            else:
                raise RegistryError(f"Registry request {url} returned invalid JSON shape")
            if not isinstance(result, list):
                raise RegistryError(f"Registry request {url} has a non-list result")
            yield [item for item in result if isinstance(item, dict)]
            cursor = str(metadata.get("cursor") or "").strip()
            if not cursor:
                return
            if cursor in seen:
                raise RegistryError(f"Registry pagination repeated cursor {cursor!r}")
            seen.add(cursor)
            params["cursor"] = cursor

    async def _all(self, url: str) -> list[dict]:
        result: list[dict] = []
        async for page in self._pages(url):
            result.extend(page)
        return result

    async def _associated_submodels(self, descriptor: dict) -> list[dict]:
        # First use inline descriptors when the Registry provides them.
        associated = descriptor.get("submodelDescriptors", [])
        if isinstance(associated, list) and associated:
            return [item for item in associated if isinstance(item, dict)]

        aas_id = str(descriptor.get("id") or "").strip()
        if not aas_id:
            return []

        url = (
            f"{self.config.aas_registry_url.rstrip('/')}/shell-descriptors/"
            f"{encode_identifier(aas_id)}/submodel-descriptors"
        )
        associated = await self._all(url)

        if associated:
            return associated

        aas_endpoint = descriptor_endpoint(descriptor)
        if not aas_endpoint:
            raise RegistryError(
                f"AAS descriptor {aas_id!r} has no endpoint for "
                "Submodel-reference discovery"
            )

        shell = await self._get_json(aas_endpoint)

        return [
            {"id": submodel_id}
            for submodel_id in shell_submodel_ids(shell)
        ]

    async def discover(self) -> tuple[dict[tuple[str, str], SignalBinding], list[str]]:
        aas_url = f"{self.config.aas_registry_url.rstrip('/')}/shell-descriptors"
        sm_url = f"{self.config.submodel_registry_url.rstrip('/')}/submodel-descriptors"
        aas_descriptors, standalone_descriptors = await asyncio.gather(
            self._all(aas_url), self._all(sm_url)
        )
        standalone = {
            str(item.get("id") or "").strip(): item
            for item in standalone_descriptors
            if str(item.get("id") or "").strip()
        }
        associated_results = await asyncio.gather(
            *(self._associated_submodels(item) for item in aas_descriptors),
            return_exceptions=True,
        )
        candidates: dict[tuple[str, str], list[SignalBinding]] = {}
        diagnostics: list[str] = []

        for aas_descriptor, result in zip(
            aas_descriptors, associated_results, strict=True
        ):
            asset_id = str(aas_descriptor.get("globalAssetId") or "").strip()
            aas_id = str(aas_descriptor.get("id") or "<unknown>").strip()
            asset_kind = aas_descriptor.get("assetKind")
            if isinstance(asset_kind, dict):
                asset_kind = asset_kind.get("name") or asset_kind.get("value")
            if str(asset_kind or "Instance").lower() == "type":
                continue
            if not asset_id:
                diagnostics.append(f"AAS {aas_id} has no globalAssetId; skipped")
                continue
            if isinstance(result, Exception):
                diagnostics.append(f"AAS {aas_id} Submodel discovery failed: {result}")
                continue
            descriptors = [
                standalone.get(str(item.get("id") or "").strip(), item)
                for item in result
            ]
            fetches = await asyncio.gather(
                *(self._fetch_submodel(item) for item in descriptors),
                return_exceptions=True,
            )
            for descriptor, fetched in zip(descriptors, fetches, strict=True):
                submodel_id = str(descriptor.get("id") or "<unknown>")
                endpoint = descriptor_endpoint(descriptor).rstrip("/")
                if isinstance(fetched, Exception):
                    diagnostics.append(
                        f"Asset {asset_id} Submodel {submodel_id} fetch failed: {fetched}"
                    )
                    continue
                for path, element in walk_elements(fetched.get("submodelElements", [])):
                    if model_type(element).lower() != "property":
                        continue
                    value_type = str(element.get("valueType") or "xs:string").strip()
                    for semantic_id in semantic_ids(element):
                        binding = SignalBinding(
                            asset_id, semantic_id, endpoint, path, value_type
                        )
                        candidates.setdefault((asset_id, semantic_id), []).append(binding)

        bindings: dict[tuple[str, str], SignalBinding] = {}
        for key, routes in candidates.items():
            unique = list(dict.fromkeys(routes))
            if len(unique) == 1:
                bindings[key] = unique[0]
            else:
                diagnostics.append(
                    f"Asset {key[0]} has {len(unique)} Properties with semanticId "
                    f"{key[1]}; route is ambiguous and was excluded"
                )
        return bindings, diagnostics

    async def _fetch_submodel(self, descriptor: dict) -> dict:
        endpoint = descriptor_endpoint(descriptor)
        if not endpoint:
            raise RegistryError(
                f"Submodel {descriptor.get('id', '<unknown>')} has no HTTP endpoint"
            )
        payload = await self._get_json(endpoint)
        if not isinstance(payload, dict):
            raise RegistryError(f"Submodel endpoint {endpoint} returned a non-object")
        return payload


class TelemetryBridge:
    def __init__(self, config: Config, *, http: httpx.AsyncClient | None = None):
        self.config = config
        self._owns_http = http is None
        self.http = http or httpx.AsyncClient(timeout=config.http_timeout_seconds)
        self.registry = SemanticRegistry(config, self.http)
        self.bindings: dict[tuple[str, str], SignalBinding] = {}
        self.queues: dict[str, asyncio.Queue] = {}
        self.workers: dict[str, asyncio.Task] = {}
        self.seen_ids: dict[str, deque[str]] = {}
        self.seen_id_sets: dict[str, set[str]] = {}
        self.refresh_lock = asyncio.Lock()

    async def close(self) -> None:
        await self.stop_workers()
        if self._owns_http:
            await self.http.aclose()

    async def stop_workers(self) -> None:
        for worker in self.workers.values():
            worker.cancel()
        await asyncio.gather(*self.workers.values(), return_exceptions=True)
        self.workers.clear()
        self.queues.clear()
        self.seen_ids.clear()
        self.seen_id_sets.clear()

    async def refresh_catalog(self) -> bool:
        async with self.refresh_lock:
            try:
                discovered, diagnostics = await self.registry.discover()
            except Exception as exc:
                print(f"[DISCOVERY] Catalog refresh failed; keeping previous snapshot: {exc}")
                return False
            previous = set(self.bindings)
            self.bindings = discovered
            added = set(discovered) - previous
            removed = previous - set(discovered)
            print(
                f"[DISCOVERY] Semantic telemetry catalog refreshed: "
                f"routes={len(discovered)} added={len(added)} removed={len(removed)}"
            )
            for diagnostic in diagnostics:
                print(f"[DISCOVERY] Warning: {diagnostic}")
            return True

    async def catalog_refresher(self) -> None:
        while True:
            await asyncio.sleep(max(0.1, self.config.registry_refresh_seconds))
            await self.refresh_catalog()

    async def publish_fault(
        self, mqtt: MqttClient, message: str, event: TelemetryEvent | None = None
    ) -> None:
        payload: dict[str, Any] = {"error": message}
        if event is not None:
            payload.update({"assetId": event.asset_id, "semanticId": event.semantic_id})
            if event.event_id is not None:
                payload["eventId"] = event.event_id
        await mqtt.publish(self.config.fault_topic, json.dumps(payload), qos=1)

    def ensure_asset(self, asset_id: str, mqtt: MqttClient) -> asyncio.Queue:
        queue = self.queues.get(asset_id)
        if queue is None:
            queue = asyncio.Queue(maxsize=self.config.queue_size)
            self.queues[asset_id] = queue
            self.seen_ids[asset_id] = deque()
            self.seen_id_sets[asset_id] = set()
            self.workers[asset_id] = asyncio.create_task(self.asset_worker(asset_id, mqtt))
            print(f"[TELEMETRY] Activated asset queue {asset_id!r}")
        return queue

    def remember_event(self, asset_id: str, event_id: str | None) -> bool:
        if event_id is None:
            return True
        seen = self.seen_id_sets[asset_id]
        if event_id in seen:
            return False
        order = self.seen_ids[asset_id]
        order.append(event_id)
        seen.add(event_id)
        while len(order) > self.config.dedup_window:
            seen.discard(order.popleft())
        return True

    async def update_aas(self, event: TelemetryEvent, binding: SignalBinding) -> None:
        typed_value = coerce_value(event.value, binding.value_type)
        path = quote(binding.element_path, safe=".")
        url = f"{binding.submodel_endpoint}/submodel-elements/{path}/$value"
        attempts = max(1, self.config.update_retry_count)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                value_body = (
                    str(typed_value).lower()
                    if isinstance(typed_value, bool)
                    else str(typed_value)
                )
                response = await self.http.patch(url, json=value_body)
                response.raise_for_status()
                print(
                    f"[TELEMETRY] assetId={event.asset_id} "
                    f"semanticId={event.semantic_id} value={typed_value} "
                    f"-> {binding.element_path}"
                )
                return
            except httpx.HTTPError as exc:
                last_error = exc
                print(
                    f"[TELEMETRY] Update failed ({attempt}/{attempts}) for "
                    f"{event.asset_id}/{event.semantic_id}: {exc}"
                )
                if attempt < attempts:
                    await asyncio.sleep(
                        self.config.retry_base_seconds * (2 ** (attempt - 1))
                    )
        raise RuntimeError(f"AAS update failed after {attempts} attempts: {last_error}")

    async def asset_worker(self, asset_id: str, mqtt: MqttClient) -> None:
        queue = self.queues[asset_id]
        while True:
            event, binding = await queue.get()
            try:
                await self.update_aas(event, binding)
            except Exception as exc:
                print(
                    f"[TELEMETRY] Permanent failure for "
                    f"{event.asset_id}/{event.semantic_id}: {exc}"
                )
                await self.publish_fault(mqtt, str(exc), event)
            finally:
                queue.task_done()

    async def accept_event(
        self, mqtt: MqttClient, event: TelemetryEvent
    ) -> TelemetryEvent:
        binding = self.bindings.get((event.asset_id, event.semantic_id))
        if binding is None:
            # Close the race between a Registry change and the periodic refresh.
            await self.refresh_catalog()
            binding = self.bindings.get((event.asset_id, event.semantic_id))
        if binding is None:
            raise ValueError(
                "No unique semantic Property route for "
                f"assetId={event.asset_id!r}, semanticId={event.semantic_id!r}"
            )
        queue = self.ensure_asset(event.asset_id, mqtt)
        if not self.remember_event(event.asset_id, event.event_id):
            print(
                f"[TELEMETRY] Ignored duplicate {event.asset_id}/"
                f"{event.semantic_id} eventId={event.event_id}"
            )
            return event
        await queue.put((event, binding))
        return event

    async def accept_telemetry(self, mqtt: MqttClient, payload: bytes) -> TelemetryEvent:
        return await self.accept_event(mqtt, parse_telemetry(payload))

    async def run_connected(self) -> None:
        await self.refresh_catalog()
        async with MqttClient(self.config.mqtt_host, self.config.mqtt_port) as mqtt:
            refresher = asyncio.create_task(self.catalog_refresher())
            try:
                await mqtt.subscribe(self.config.telemetry_topic, qos=1)
                print(
                    f"[TELEMETRY] Listening on {self.config.telemetry_topic}; "
                    "routing by assetId + semanticId"
                )
                async for message in mqtt.messages:
                    event: TelemetryEvent | None = None
                    try:
                        event = parse_telemetry(message.payload)
                        await self.accept_event(mqtt, event)
                    except (
                        UnicodeDecodeError, json.JSONDecodeError, ValueError, KeyError
                    ) as exc:
                        print(f"[TELEMETRY] Rejected message: {exc}")
                        await self.publish_fault(mqtt, str(exc), event)
            finally:
                refresher.cancel()
                await asyncio.gather(refresher, return_exceptions=True)
                await self.stop_workers()

    async def run(self) -> None:
        while True:
            try:
                await self.run_connected()
            except MqttError as exc:
                print(f"[TELEMETRY] MQTT connection failed: {exc}; reconnecting")
                await asyncio.sleep(self.config.mqtt_reconnect_seconds)


async def main() -> None:
    bridge = TelemetryBridge(Config())
    try:
        await bridge.run()
    finally:
        await bridge.close()


if __name__ == "__main__":
    asyncio.run(main())
