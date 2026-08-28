import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class AgentConfig:
    mqtt_host: str = os.getenv("MQTT_HOST", "mosquitto")
    mqtt_port: int = int(os.getenv("MQTT_PORT", "1883"))
    mqtt_topic: str = os.getenv(
        "MQTT_TOPIC",
        "sm-repository/+/submodels/+/submodelElements/+/updated",
    )
    operation_reply_topic: str = os.getenv(
        "OPERATION_REPLY_TOPIC",
        "simulation/+/replies/+",
    )
    server_status_topic: str = os.getenv(
        "SERVER_STATUS_TOPIC",
        "simulation/server/status",
    )
    station_status_topic: str = os.getenv(
        "STATION_STATUS_TOPIC",
        "simulation/+/status",
    )
    robot_fault_topic_prefix: str = os.getenv(
        "ROBOT_FAULT_TOPIC_PREFIX",
        "factory/robots",
    )
    basyx_base_url: str = os.getenv("BASYX_BASE_URL", "http://aas-env:8081")
    aas_registry_url: str = os.getenv(
        "AAS_REGISTRY_URL", "http://aas-registry:8080"
    )
    submodel_registry_url: str = os.getenv(
        "SUBMODEL_REGISTRY_URL", "http://sm-registry:8080"
    )
    registry_refresh_seconds: float = float(
        os.getenv("REGISTRY_REFRESH_SECONDS", "5")
    )
    semantic_discovery_diagnostic: bool = os.getenv(
        "SEMANTIC_DISCOVERY_DIAGNOSTIC", "false"
    ).strip().lower() in {"true", "1", "on", "yes"}
    http_timeout_seconds: float = float(os.getenv("HTTP_TIMEOUT_SECONDS", "8"))
    job_retry_seconds: float = float(os.getenv("JOB_RETRY_SECONDS", "0.5"))
    job_timeout_seconds: float = float(os.getenv("JOB_TIMEOUT_SECONDS", "60"))
    queue_timeout_seconds: float = float(
        os.getenv("QUEUE_TIMEOUT_SECONDS", os.getenv("JOB_TIMEOUT_SECONDS", "60"))
    )
    operation_timeout_seconds: float = float(
        os.getenv("OPERATION_TIMEOUT_SECONDS", os.getenv("JOB_TIMEOUT_SECONDS", "60"))
    )
    invoke_retry_count: int = int(os.getenv("INVOKE_RETRY_COUNT", "3"))
    station_registry_file: str = os.getenv("STATION_REGISTRY_FILE", "")
    orchestrator_log_csv_path: str = os.getenv(
        "ORCHESTRATOR_LOG_CSV_PATH",
        str(Path(__file__).resolve().parent / "orchestrator_logs.csv"),
    )
    orchestrator_summary_csv_path: str = os.getenv(
        "ORCHESTRATOR_SUMMARY_CSV_PATH",
        str(Path(__file__).resolve().parent / "orchestrator_summary.csv"),
    )
    summary_batch_size: int = int(os.getenv("SUMMARY_BATCH_SIZE", "5"))
    measurement_run_id: str = os.getenv("MEASUREMENT_RUN_ID", "1")


@dataclass(frozen=True)
class RobotEndpoints:
    state_submodel_b64: str
    skills_submodel_b64: str
    station_id: str = ""
    robot_id: str = ""


def normalize_submodel_id(submodel_id: str) -> str:
    return submodel_id.strip().replace("+", "-").replace("/", "_").rstrip("=")


def normalize_station_id(station_id: str) -> str:
    return station_id.strip().lower()


def parse_bool_value(raw_payload: str) -> Optional[bool]:
    text = raw_payload.strip().lower()
    if text in {"true", "1", "on", "yes"}:
        return True
    if text in {"false", "0", "off", "no"}:
        return False

    try:
        parsed = json.loads(text)
        if isinstance(parsed, bool):
            return parsed
        if isinstance(parsed, (int, float)):
            return bool(parsed)
        if isinstance(parsed, dict):
            for key in ("value", "newValue", "payload"):
                if key in parsed:
                    value = parse_bool_value(str(parsed[key]))
                    if value is not None:
                        return value
    except json.JSONDecodeError:
        pass
    return None


def _upgrade_v1_registry(data: dict) -> dict:
    station_entries = data.get("stations")
    if not isinstance(station_entries, dict):
        return data

    stations: dict[str, dict] = {}
    robots: dict[str, dict] = {}
    conveyors: dict[str, dict] = {}
    station_assets: list[dict] = []
    for station_key, entry in station_entries.items():
        if not isinstance(entry, dict):
            continue
        station_id = str(entry.get("stationId", station_key)).strip()
        if not station_id:
            continue
        stations[str(station_key)] = {"stationId": station_id}

        robot_id = str(
            entry.get("robotId")
            or station_id.replace("Station_", "Robot_", 1)
        ).strip()
        if entry.get("robotStateSubmodelB64") and entry.get("robotSkillsSubmodelB64"):
            robots[normalize_station_id(robot_id)] = {
                "robotId": robot_id,
                "stateSubmodelB64": entry.get("robotStateSubmodelB64"),
                "skillsSubmodelB64": entry.get("robotSkillsSubmodelB64"),
                "properties": entry.get("robotProperties", {}),
            }
            station_assets.append(
                {
                    "stationId": station_id,
                    "assetType": "robot",
                    "assetId": robot_id,
                }
            )

        conveyor_id = str(
            entry.get("conveyorId")
            or station_id.replace("Station_", "Conveyor_", 1)
        ).strip()
        if entry.get("conveyorSubmodelB64"):
            conveyor_key = normalize_station_id(conveyor_id)
            conveyors[conveyor_key] = {
                "conveyorId": conveyor_id,
                "stateSubmodelB64": entry.get("conveyorSubmodelB64"),
                "operationsSubmodelB64": entry.get("conveyorOperationsSubmodelB64", ""),
                "properties": entry.get("conveyorProperties", {}),
            }
            station_assets.append(
                {
                    "stationId": station_id,
                    "assetType": "conveyor",
                    "assetId": conveyor_id,
                }
            )
    return {
        "schemaVersion": "2.0",
        "stations": stations,
        "robots": robots,
        "conveyors": conveyors,
        "stationAssets": station_assets,
    }


def load_asset_registry(file_path: str) -> dict:
    if not file_path:
        return {}

    path = Path(file_path)
    if not path.exists():
        print(f"[ORCHESTRATOR] Station registry not found: {path}")
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[ORCHESTRATOR] Failed to read station registry '{path}': {exc}")
        return {}

    if not isinstance(data, dict):
        print("[ORCHESTRATOR] Asset registry must contain a JSON object")
        return {}
    if str(data.get("schemaVersion", "1.0")).startswith("1"):
        data = _upgrade_v1_registry(data)
    stations = data.get("stations")
    robots = data.get("robots")
    conveyors = data.get("conveyors")
    station_assets = data.get("stationAssets")
    if not all(
        (
            isinstance(stations, dict),
            isinstance(robots, dict),
            isinstance(conveyors, dict),
            isinstance(station_assets, list),
        )
    ):
        print(
            "[ORCHESTRATOR] Asset registry must contain 'stations', "
            "'robots', 'conveyors', and 'stationAssets'"
        )
        return {}
    print(
        f"[ORCHESTRATOR] Loaded asset registry from {path}: "
        f"stations={len(stations)} robots={len(robots)} "
        f"conveyors={len(conveyors)}"
    )
    return data


def build_robot_endpoints(asset_registry: dict) -> list[RobotEndpoints]:
    robots: list[RobotEndpoints] = []
    seen: set[tuple[str, str]] = set()
    robot_entries = asset_registry.get("robots", {})
    station_assets = asset_registry.get("stationAssets", [])
    if not isinstance(robot_entries, dict):
        robot_entries = {}
    if "robots" not in asset_registry and asset_registry:
        upgraded = _upgrade_v1_registry({"stations": asset_registry})
        robot_entries = upgraded.get("robots", {})
        station_assets = upgraded.get("stationAssets", [])
    if not isinstance(station_assets, list):
        station_assets = []
    station_by_robot_id: dict[str, str] = {}
    for relation in station_assets:
        if (
            not isinstance(relation, dict)
            or str(relation.get("assetType", "")).strip().lower() != "robot"
        ):
            continue
        asset_id = normalize_station_id(str(relation.get("assetId", "")))
        station_id = str(relation.get("stationId", "")).strip()
        if asset_id and station_id:
            station_by_robot_id.setdefault(asset_id, station_id)
    for robot_key_name, entry in robot_entries.items():
        if not isinstance(entry, dict):
            continue
        state_id = normalize_submodel_id(
            str(entry.get("stateSubmodelB64", ""))
        )
        skills_id = normalize_submodel_id(
            str(entry.get("skillsSubmodelB64", ""))
        )
        robot_key = (state_id, skills_id)
        if not state_id or not skills_id or robot_key in seen:
            continue
        seen.add(robot_key)
        robot_id = str(entry.get("robotId", robot_key_name)).strip()
        robots.append(
            RobotEndpoints(
                state_submodel_b64=state_id,
                skills_submodel_b64=skills_id,
                station_id=station_by_robot_id.get(normalize_station_id(robot_id), ""),
                robot_id=robot_id,
            )
        )
    return robots


def build_station_bindings(asset_registry: dict) -> dict[str, str]:
    bindings: dict[str, str] = {}
    conveyors = asset_registry.get("conveyors", {})
    station_assets = asset_registry.get("stationAssets", [])
    if "conveyors" not in asset_registry and asset_registry:
        upgraded = _upgrade_v1_registry({"stations": asset_registry})
        conveyors = upgraded.get("conveyors", {})
        station_assets = upgraded.get("stationAssets", [])
    if not isinstance(conveyors, dict) or not isinstance(station_assets, list):
        return bindings
    conveyors_by_id = {
        normalize_station_id(str(entry.get("conveyorId", key))): entry
        for key, entry in conveyors.items()
        if isinstance(entry, dict)
    }
    for relation in station_assets:
        if (
            not isinstance(relation, dict)
            or str(relation.get("assetType", "")).strip().lower() != "conveyor"
        ):
            continue
        station_id = str(relation.get("stationId", "")).strip()
        asset_id = normalize_station_id(str(relation.get("assetId", "")))
        entry = conveyors_by_id.get(asset_id, {})
        conveyor_submodel = normalize_submodel_id(
            str(entry.get("stateSubmodelB64", ""))
        )
        if conveyor_submodel and station_id:
            bindings[conveyor_submodel] = station_id
    return bindings
