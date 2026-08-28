"""Environment configuration and wire-value parsing for the semantic agent."""

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
    operation_timeout_seconds: float = float(
        os.getenv("OPERATION_TIMEOUT_SECONDS", "60")
    )
    invoke_retry_count: int = int(os.getenv("INVOKE_RETRY_COUNT", "3"))
    orchestrator_log_csv_path: str = os.getenv(
        "ORCHESTRATOR_LOG_CSV_PATH",
        str(Path(__file__).resolve().parent / "orchestrator_logs.csv"),
    )
    measurement_run_id: str = os.getenv("MEASUREMENT_RUN_ID", "1")


def parse_bool_value(raw_payload: object) -> Optional[bool]:
    if isinstance(raw_payload, bool):
        return raw_payload
    if isinstance(raw_payload, (int, float)):
        return bool(raw_payload)
    if raw_payload is None:
        return None

    text = str(raw_payload).strip().lower()
    if text in {"true", "1", "on", "yes"}:
        return True
    if text in {"false", "0", "off", "no"}:
        return False

    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(parsed, (bool, int, float)):
        return bool(parsed)
    if isinstance(parsed, dict):
        for key in ("value", "newValue", "payload"):
            if key in parsed:
                value = parse_bool_value(parsed[key])
                if value is not None:
                    return value
    return None
