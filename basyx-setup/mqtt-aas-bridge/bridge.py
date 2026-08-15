import asyncio
import json
import os
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from aiomqtt import Client as MqttClient
from aiomqtt import MqttError


DEFAULT_SIGNALS = {
    "isRunning": ("IsRunning", "bool"),
    "currentSpeed": ("CurrentSpeed", "float"),
    "boxDetected": ("Sensor_BoxPresent", "bool"),
    "isMoving": ("IsMoving", "bool"),
    "faultActive": ("FaultActive", "bool"),
}


@dataclass(frozen=True)
class SignalBinding:
    submodel_id: str
    element: str
    value_type: str


@dataclass(frozen=True)
class Config:
    mqtt_host: str = os.getenv("MQTT_HOST", "mosquitto")
    mqtt_port: int = int(os.getenv("MQTT_PORT", "1883"))
    legacy_telemetry_topic: str = os.getenv("MQTT_TELEMETRY_TOPIC", "simulation/+/+")
    telemetry_topic: str = os.getenv("MQTT_DYNAMIC_TELEMETRY_TOPIC", "factory/+/telemetry/+")
    robot_telemetry_topic: str = os.getenv("MQTT_ROBOT_TELEMETRY_TOPIC", "factory/robots/+/telemetry/+")
    conveyor_telemetry_topic: str = os.getenv("MQTT_CONVEYOR_TELEMETRY_TOPIC", "factory/conveyors/+/telemetry/+")
    manifest_topic: str = os.getenv("MQTT_MANIFEST_TOPIC", "factory/+/manifest")
    aas_base_url: str = os.getenv("BASYX_BASE_URL", "http://aas-env:8081")
    bindings_file: str = os.getenv("STATION_REGISTRY_FILE", "/config/stations.json")
    update_retry_count: int = int(os.getenv("AAS_UPDATE_RETRY_COUNT", "5"))
    retry_base_seconds: float = float(os.getenv("AAS_RETRY_BASE_SECONDS", "0.2"))
    http_timeout_seconds: float = float(os.getenv("HTTP_TIMEOUT_SECONDS", "8"))
    mqtt_reconnect_seconds: float = float(os.getenv("MQTT_RECONNECT_SECONDS", "2"))
    fault_topic_prefix: str = os.getenv("FAULT_TOPIC_PREFIX", "oip/fault/telemetry-bridge")
    queue_size: int = int(os.getenv("STATION_QUEUE_SIZE", "1000"))
    dedup_window: int = int(os.getenv("EVENT_DEDUP_WINDOW", "4096"))


def normalize_station_id(value: str) -> str:
    return value.strip().lower()


def _load_registry(path: str) -> dict:
    binding_path = Path(path)
    if not binding_path.exists():
        print(f"[DISCOVERY] No seed bindings at {binding_path}; waiting for retained manifests")
        return {}
    raw = json.loads(binding_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Asset registry must be a JSON object")
    return raw


def _asset_signal_bindings(
    asset: dict,
    allowed_signals: set[str],
    legacy_submodel_key: str = "",
    legacy_properties_key: str = "",
) -> dict[str, SignalBinding]:
    state_submodel_id = str(
        asset.get("stateSubmodelB64")
        or (asset.get(legacy_submodel_key) if legacy_submodel_key else "")
        or ""
    ).strip()
    if not state_submodel_id:
        return {}
    property_configs = asset.get("properties")
    if not isinstance(property_configs, dict) and legacy_properties_key:
        property_configs = asset.get(legacy_properties_key)
    signals: dict[str, SignalBinding] = {}
    for signal in allowed_signals:
        default_element, default_type = DEFAULT_SIGNALS[signal]
        property_config = (
            property_configs.get(signal, {})
            if isinstance(property_configs, dict)
            else {}
        )
        element = default_element
        value_type = default_type
        if isinstance(property_config, dict):
            element = str(property_config.get("idShort", element)).strip()
            value_type = str(property_config.get("type", value_type)).strip().lower()
        signals[signal] = SignalBinding(state_submodel_id, element, value_type)
    return signals


def load_conveyor_bindings(path: str, registry: dict | None = None) -> dict[str, dict[str, SignalBinding]]:
    raw = _load_registry(path) if registry is None else registry
    conveyors: dict[str, dict[str, SignalBinding]] = {}
    conveyor_entries = raw.get("conveyors")
    if isinstance(conveyor_entries, dict):
        for conveyor_key, entry in conveyor_entries.items():
            if not isinstance(entry, dict):
                continue
            conveyor_id = normalize_station_id(
                str(entry.get("conveyorId", conveyor_key))
            )
            signals = _asset_signal_bindings(
                entry, {"isRunning", "currentSpeed", "boxDetected"}
            )
            if conveyor_id and signals:
                conveyors[conveyor_id] = signals
        return conveyors

    station_entries = raw.get("stations")
    if not isinstance(station_entries, dict):
        return conveyors
    for station_key, entry in station_entries.items():
        if not isinstance(entry, dict):
            continue
        station_id = str(entry.get("stationId", station_key)).strip()
        conveyor_id = normalize_station_id(
            str(
                entry.get("conveyorId")
                or station_id.replace("Station_", "Conveyor_", 1)
            )
        )
        signals = _asset_signal_bindings(
            entry,
            {"isRunning", "currentSpeed", "boxDetected"},
            "conveyorSubmodelB64",
            "conveyorProperties",
        )
        if conveyor_id and signals:
            conveyors[conveyor_id] = signals
    return conveyors


def load_seed_bindings(path: str, registry: dict | None = None) -> dict[str, dict[str, SignalBinding]]:
    raw = _load_registry(path) if registry is None else registry
    if not raw:
        return {}

    station_entries = raw.get("stations")
    if not isinstance(station_entries, dict):
        raise ValueError("Station registry must contain a 'stations' object")

    if isinstance(raw.get("conveyors"), dict):
        conveyors = load_conveyor_bindings(path, raw)
        stations = {
            normalize_station_id(str(entry.get("stationId", key))): {}
            for key, entry in station_entries.items()
            if isinstance(entry, dict)
        }
        for relation in raw.get("stationAssets", []):
            if (
                not isinstance(relation, dict)
                or str(relation.get("assetType", "")).strip().lower()
                != "conveyor"
            ):
                continue
            station_id = normalize_station_id(str(relation.get("stationId", "")))
            conveyor_id = normalize_station_id(str(relation.get("assetId", "")))
            if station_id in stations and conveyor_id in conveyors:
                # Legacy station-scoped telemetry is unambiguous only while
                # signal names do not overlap. Asset-scoped topics remain exact.
                for signal, binding in conveyors[conveyor_id].items():
                    stations[station_id].setdefault(signal, binding)
        return {station: signals for station, signals in stations.items() if signals}

    stations: dict[str, dict[str, SignalBinding]] = {}
    for station_key, binding in station_entries.items():
        if not isinstance(binding, dict):
            raise ValueError(f"Binding for {station_key!r} must be an object")
        station_id = str(binding.get("stationId", station_key)).strip()
        if not station_id:
            raise ValueError(f"Binding for {station_key!r} has no stationId")
        signals = _asset_signal_bindings(
            binding,
            {"isRunning", "currentSpeed", "boxDetected"},
            "conveyorSubmodelB64",
            "conveyorProperties",
        )
        signals.update(
            _asset_signal_bindings(
                binding,
                {"isMoving", "faultActive"},
                "robotStateSubmodelB64",
                "robotProperties",
            )
        )
        if signals:
            stations[normalize_station_id(station_id)] = signals
    return stations


def load_robot_bindings(path: str, registry: dict | None = None) -> dict[str, dict[str, SignalBinding]]:
    raw = _load_registry(path) if registry is None else registry
    robot_entries = raw.get("robots") if isinstance(raw, dict) else None
    if isinstance(robot_entries, dict):
        robots: dict[str, dict[str, SignalBinding]] = {}
        for robot_key, entry in robot_entries.items():
            if not isinstance(entry, dict):
                continue
            robot_id = normalize_station_id(str(entry.get("robotId", robot_key)))
            signals = _asset_signal_bindings(entry, {"isMoving", "faultActive"})
            if robot_id and signals:
                robots[robot_id] = signals
        return robots

    station_entries = raw.get("stations") if isinstance(raw, dict) else None
    if not isinstance(station_entries, dict):
        raise ValueError("Station registry must contain a 'stations' object")

    robots: dict[str, dict[str, SignalBinding]] = {}
    for entry in station_entries.values():
        if not isinstance(entry, dict):
            continue
        station_id = str(entry.get("stationId", "")).strip()
        robot_id = normalize_station_id(
            str(
                entry.get("robotId")
                or station_id.replace("Station_", "Robot_", 1)
            )
        )
        if not robot_id:
            continue

        signals = _asset_signal_bindings(
            entry,
            {"isMoving", "faultActive"},
            "robotStateSubmodelB64",
            "robotProperties",
        )
        if signals:
            robots[robot_id] = signals
    return robots


def parse_manifest(topic: str, payload: bytes) -> tuple[str, dict[str, SignalBinding]]:
    parts = topic.split("/")
    if len(parts) != 3 or parts[0] != "factory" or parts[2] != "manifest":
        raise ValueError(f"Unsupported manifest topic {topic!r}")

    decoded = json.loads(payload.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("Station manifest must be a JSON object")

    topic_station = normalize_station_id(parts[1])
    station = normalize_station_id(str(decoded.get("stationId", topic_station)))
    if station != topic_station:
        raise ValueError(f"Manifest stationId {station!r} does not match topic {topic_station!r}")

    assets = decoded.get("assets")
    if not isinstance(assets, dict):
        raise ValueError("Station manifest must contain an assets object")

    signals: dict[str, SignalBinding] = {}
    for asset in assets.values():
        if not isinstance(asset, dict):
            continue
        submodel_id = str(asset.get("submodelId", "")).strip()
        properties = asset.get("properties")
        if not submodel_id or not isinstance(properties, dict):
            continue
        for signal, property_config in properties.items():
            if isinstance(property_config, str):
                element = property_config
                value_type = DEFAULT_SIGNALS.get(signal, ("", "string"))[1]
            elif isinstance(property_config, dict):
                element = str(property_config.get("idShort", "")).strip()
                value_type = str(property_config.get("type", "string")).strip().lower()
            else:
                continue
            if element and value_type in {"bool", "boolean", "float", "double", "int", "integer", "string"}:
                signals[signal] = SignalBinding(submodel_id, element, value_type)

    if not signals:
        raise ValueError("Station manifest does not define any telemetry properties")
    return station, signals


def parse_telemetry(topic: str, payload: bytes) -> tuple[str, str, str, Any, str | None]:
    parts = topic.split("/")
    if len(parts) == 4 and parts[0] == "factory" and parts[2] == "telemetry":
        target_type, target_id, signal = "station", normalize_station_id(parts[1]), parts[3]
    elif len(parts) == 3 and parts[0] == "simulation":
        target_type, target_id, signal = "station", normalize_station_id(parts[1]), parts[2]
    elif (
        len(parts) == 5
        and parts[0] == "factory"
        and parts[1] in {"robots", "conveyors"}
        and parts[3] == "telemetry"
    ):
        target_type = "robot" if parts[1] == "robots" else "conveyor"
        target_id, signal = normalize_station_id(parts[2]), parts[4]
    else:
        raise ValueError(f"Unsupported telemetry topic {topic!r}")

    decoded = json.loads(payload.decode("utf-8"))
    if isinstance(decoded, dict):
        payload_robot_id = decoded.get("robotId")
        payload_conveyor_id = decoded.get("conveyorId")
        if (
            target_type == "robot"
            and payload_robot_id is not None
            and normalize_station_id(str(payload_robot_id)) != target_id
        ):
            raise ValueError(
                f"Payload robotId {payload_robot_id!r} does not match topic robot {target_id!r}"
            )
        if (
            target_type == "conveyor"
            and payload_conveyor_id is not None
            and normalize_station_id(str(payload_conveyor_id)) != target_id
        ):
            raise ValueError(
                f"Payload conveyorId {payload_conveyor_id!r} does not match "
                f"topic conveyor {target_id!r}"
            )
        value = decoded.get("value", decoded.get(signal))
        event_id_raw = decoded.get("eventId", decoded.get("sequence"))
        event_id = str(event_id_raw) if event_id_raw is not None else None
    else:
        value, event_id = decoded, None
    return target_type, target_id, signal, value, event_id


def coerce_value(value: Any, value_type: str) -> Any:
    normalized = value_type.lower()
    if normalized in {"bool", "boolean"}:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            return value.strip().lower() == "true"
        raise ValueError("value must be boolean")
    if normalized in {"float", "double"}:
        if isinstance(value, bool):
            raise ValueError("value must be numeric")
        return float(value)
    if normalized in {"int", "integer"}:
        if isinstance(value, bool):
            raise ValueError("value must be an integer")
        return int(value)
    if normalized == "string":
        return str(value)
    raise ValueError(f"Unsupported value type {value_type!r}")


class TelemetryBridge:
    def __init__(self, config: Config):
        self.config = config
        registry = _load_registry(config.bindings_file)
        self.bindings = load_seed_bindings(config.bindings_file, registry)
        self.robot_bindings = load_robot_bindings(config.bindings_file, registry)
        self.conveyor_bindings = load_conveyor_bindings(config.bindings_file, registry)
        self.http = httpx.AsyncClient(timeout=config.http_timeout_seconds)
        self.queues: dict[str, asyncio.Queue] = {}
        self.workers: dict[str, asyncio.Task] = {}
        self.seen_ids: dict[str, deque[str]] = {}
        self.seen_id_sets: dict[str, set[str]] = {}

    async def close(self) -> None:
        await self.stop_workers()
        await self.http.aclose()

    async def stop_workers(self) -> None:
        for worker in self.workers.values():
            worker.cancel()
        await asyncio.gather(*self.workers.values(), return_exceptions=True)
        self.workers.clear()
        self.queues.clear()
        self.seen_ids.clear()
        self.seen_id_sets.clear()

    async def publish_fault(self, mqtt: MqttClient, station: str, message: str) -> None:
        payload = json.dumps({"stationId": station, "error": message})
        await mqtt.publish(f"{self.config.fault_topic_prefix}/{station}", payload, qos=1)

    def ensure_station(self, station: str, mqtt: MqttClient) -> asyncio.Queue:
        queue = self.queues.get(station)
        if queue is None:
            queue = asyncio.Queue(maxsize=self.config.queue_size)
            self.queues[station] = queue
            self.seen_ids[station] = deque()
            self.seen_id_sets[station] = set()
            self.workers[station] = asyncio.create_task(self.station_worker(station, mqtt))
            print(f"[DISCOVERY] Activated station {station!r}")
        return queue

    def remember_event(self, station: str, event_id: str | None) -> bool:
        if event_id is None:
            return True
        seen = self.seen_id_sets[station]
        if event_id in seen:
            return False
        order = self.seen_ids[station]
        order.append(event_id)
        seen.add(event_id)
        while len(order) > self.config.dedup_window:
            seen.discard(order.popleft())
        return True

    async def update_aas(
        self,
        target: str,
        signal: str,
        value: Any,
        binding: SignalBinding,
    ) -> None:
        typed_value = coerce_value(value, binding.value_type)
        url = (
            f"{self.config.aas_base_url}/submodels/{binding.submodel_id}"
            f"/submodel-elements/{binding.element}/$value"
        )
        attempts = max(1, self.config.update_retry_count)
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                value_body = str(typed_value).lower() if isinstance(typed_value, bool) else str(typed_value)
                response = await self.http.patch(url, json=value_body)
                response.raise_for_status()
                print(f"[TELEMETRY] {target} {signal}={typed_value} -> {binding.element}")
                return
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                last_error = exc
                print(f"[TELEMETRY] Update failed ({attempt}/{attempts}) for {target}/{signal}: {exc}")
                if attempt < attempts:
                    await asyncio.sleep(self.config.retry_base_seconds * (2 ** (attempt - 1)))
        raise RuntimeError(f"AAS update failed after {attempts} attempts: {last_error}")

    async def station_worker(self, station: str, mqtt: MqttClient) -> None:
        queue = self.queues[station]
        while True:
            signal, value, binding = await queue.get()
            try:
                await self.update_aas(station, signal, value, binding)
            except Exception as exc:
                print(f"[TELEMETRY] Permanent failure for {station}/{signal}: {exc}")
                await self.publish_fault(mqtt, station, str(exc))
            finally:
                queue.task_done()

    async def apply_manifest(self, mqtt: MqttClient, topic: str, payload: bytes) -> None:
        station, signals = parse_manifest(topic, payload)
        self.bindings[station] = signals
        self.ensure_station(f"station:{station}", mqtt)
        print(f"[DISCOVERY] Applied manifest for {station!r}; signals={sorted(signals)}")

    async def accept_telemetry(self, mqtt: MqttClient, topic: str, payload: bytes) -> None:
        target_type, target_id, signal, value, event_id = parse_telemetry(topic, payload)
        target_bindings = {
            "station": self.bindings,
            "robot": self.robot_bindings,
            "conveyor": self.conveyor_bindings,
        }[target_type]
        if target_id not in target_bindings:
            raise ValueError(f"Unknown {target_type} {target_id!r}")
        if signal not in target_bindings[target_id]:
            raise ValueError(f"Signal {signal!r} is not declared by {target_type} {target_id!r}")
        queue_key = f"{target_type}:{target_id}"
        queue = self.ensure_station(queue_key, mqtt)
        if not self.remember_event(queue_key, event_id):
            print(f"[TELEMETRY] Ignored duplicate {target_id}/{signal} eventId={event_id}")
            return
        await queue.put((signal, value, target_bindings[target_id][signal]))

    async def run_connected(self) -> None:
        async with MqttClient(self.config.mqtt_host, self.config.mqtt_port) as mqtt:
            try:
                for station in self.bindings:
                    self.ensure_station(f"station:{station}", mqtt)
                for robot in self.robot_bindings:
                    self.ensure_station(f"robot:{robot}", mqtt)
                for conveyor in self.conveyor_bindings:
                    self.ensure_station(f"conveyor:{conveyor}", mqtt)
                await mqtt.subscribe(self.config.manifest_topic, qos=1)
                await mqtt.subscribe(self.config.telemetry_topic, qos=1)
                await mqtt.subscribe(self.config.legacy_telemetry_topic, qos=1)
                await mqtt.subscribe(self.config.robot_telemetry_topic, qos=1)
                await mqtt.subscribe(self.config.conveyor_telemetry_topic, qos=1)
                print(
                    f"[TELEMETRY] Listening on {self.config.telemetry_topic} and "
                    f"{self.config.legacy_telemetry_topic} and "
                    f"{self.config.robot_telemetry_topic} and "
                    f"{self.config.conveyor_telemetry_topic}; "
                    f"manifests={self.config.manifest_topic}"
                )
                async for message in mqtt.messages:
                    topic = str(message.topic)
                    try:
                        if topic.startswith("factory/") and topic.endswith("/manifest"):
                            await self.apply_manifest(mqtt, topic, message.payload)
                        else:
                            await self.accept_telemetry(mqtt, topic, message.payload)
                    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, KeyError) as exc:
                        print(f"[TELEMETRY] Rejected message on {topic}: {exc}")
                        parts = topic.split("/")
                        station = normalize_station_id(parts[1]) if len(parts) > 1 else "unknown"
                        await self.publish_fault(mqtt, station, str(exc))
            finally:
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
