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
    basyx_base_url: str = os.getenv("BASYX_BASE_URL", "http://aas-env:8081")
    http_timeout_seconds: float = float(os.getenv("HTTP_TIMEOUT_SECONDS", "8"))
    job_retry_seconds: float = float(os.getenv("JOB_RETRY_SECONDS", "0.5"))
    job_timeout_seconds: float = float(os.getenv("JOB_TIMEOUT_SECONDS", "60"))
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


@dataclass(frozen=True)
class RobotEndpoints:
    state_submodel_b64: str
    skills_submodel_b64: str
    station_id: str = ""


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


def load_station_registry(file_path: str) -> dict[str, dict]:
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

    station_entries = data.get("stations") if isinstance(data, dict) else None
    if not isinstance(station_entries, dict):
        print("[ORCHESTRATOR] Station registry must contain a 'stations' object")
        return {}

    registry = {
        str(station_key): entry
        for station_key, entry in station_entries.items()
        if isinstance(entry, dict)
    }
    print(
        f"[ORCHESTRATOR] Loaded {len(registry)} station registry entry(s) "
        f"from {path}"
    )
    return registry


def build_robot_endpoints(station_registry: dict[str, dict]) -> list[RobotEndpoints]:
    robots: list[RobotEndpoints] = []
    seen: set[tuple[str, str]] = set()
    for station_key, entry in station_registry.items():
        state_id = normalize_submodel_id(
            str(entry.get("robotStateSubmodelB64", ""))
        )
        skills_id = normalize_submodel_id(
            str(entry.get("robotSkillsSubmodelB64", ""))
        )
        robot_key = (state_id, skills_id)
        if not state_id or not skills_id or robot_key in seen:
            continue
        seen.add(robot_key)
        robots.append(
            RobotEndpoints(
                state_submodel_b64=state_id,
                skills_submodel_b64=skills_id,
                station_id=str(entry.get("stationId", station_key)).strip(),
            )
        )
    return robots


def build_station_bindings(station_registry: dict[str, dict]) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for station_key, entry in station_registry.items():
        conveyor_submodel = normalize_submodel_id(
            str(entry.get("conveyorSubmodelB64", ""))
        )
        station_id = str(entry.get("stationId", station_key)).strip()
        if conveyor_submodel and station_id:
            bindings[conveyor_submodel] = station_id
    return bindings
