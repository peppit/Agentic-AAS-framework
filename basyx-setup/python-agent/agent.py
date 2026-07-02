import asyncio
import json
import os
from dataclasses import dataclass
from typing import Optional

import httpx
from aiomqtt import Client as MqttClient
from aiomqtt import MqttError


@dataclass(frozen=True)
class AgentConfig:
    mqtt_host: str = os.getenv("MQTT_HOST", "mosquitto")
    mqtt_port: int = int(os.getenv("MQTT_PORT", "1883"))
    mqtt_topic: str = os.getenv(
        "MQTT_TOPIC", "sm-repository/+/submodels/+/submodelElements/+/updated"
    )
    box_detected_property: str = os.getenv("BOX_DETECTED_PROPERTY", "Sensor_BoxPresent")
    robot_is_moving_property: str = os.getenv("ROBOT_STATE_PROPERTY", "IsMoving")
    movebox_invoke_url: str = os.getenv(
        "MOVEBOX_INVOKE_URL",
        "http://aas-env:8081/submodels/aHR0cHM6Ly9leGFtcGxlLmNvbS9pZHMvc20vODE4MF8wMTgxXzYwNjJfODI0Nw/submodel-elements/MoveBox/invoke",
    )
    movebox_conveyor: str = os.getenv("MOVEBOX_CONVEYOR", "Conveyor1")
    movebox_pallet: str = os.getenv("MOVEBOX_PALLET", "Pallet1")
    http_timeout_seconds: float = float(os.getenv("HTTP_TIMEOUT_SECONDS", "8"))
    trigger_debounce_seconds: float = float(os.getenv("EVENT_DEBOUNCE_SECONDS", "1.5"))


def parse_bool_value(raw_payload: str) -> Optional[bool]:
    text = raw_payload.strip()
    lowered = text.lower()
    if lowered in {"true", "1", "on", "yes"}:
        return True
    if lowered in {"false", "0", "off", "no"}:
        return False

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None

    if isinstance(parsed, bool):
        return parsed

    if isinstance(parsed, dict):
        for key in ("value", "newValue", "payload"):
            if key in parsed:
                value = parsed[key]
                if isinstance(value, bool):
                    return value
                if isinstance(value, str):
                    return parse_bool_value(value)

    return None


class FactoryOrchestrator:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.last_property_bool_state: dict[tuple[str, str], Optional[bool]] = {}
        self.robot_is_moving = False
        self.last_movebox_trigger_ts = 0.0

    async def handle_event(self, submodel_b64: str, property_id: str, payload: str) -> None:
        bool_value = parse_bool_value(payload)
        key = (submodel_b64, property_id)
        previous_value = self.last_property_bool_state.get(key)
        self.last_property_bool_state[key] = bool_value


        if property_id == self.config.robot_is_moving_property and bool_value is not None:
            self.robot_is_moving = bool_value
            print(f"[AGENT] Robot motion state updated: isMoving={self.robot_is_moving}")

        if property_id != self.config.box_detected_property:
            return

        if bool_value is not True:
            return

        if previous_value is True:
            return

        now = asyncio.get_running_loop().time()
        if now - self.last_movebox_trigger_ts < self.config.trigger_debounce_seconds:
            print("[AGENT] Skipping duplicate boxDetected trigger due to debounce window.")
            return

        if self.robot_is_moving:
            print("[AGENT] boxDetected=true but robot is currently moving. Trigger skipped.")
            return

        await self.invoke_movebox_operation()
        self.last_movebox_trigger_ts = now

    async def invoke_movebox_operation(self) -> None:
        body = {
            "inputArguments": [
                {
                    "value": {
                        "modelType": "Property",
                        "idShort": "Conveyor1",
                        "valueType": "xs:string",
                        "value": self.config.movebox_conveyor,
                    }
                },
                {
                    "value": {
                        "modelType": "Property",
                        "idShort": "Pallet1",
                        "valueType": "xs:string",
                        "value": self.config.movebox_pallet,
                    }
                },
            ],
            "inoutputArguments": [],
            "requestedTimeout": int(self.config.http_timeout_seconds * 1000),
        }

        print(
            "[AGENT] boxDetected=true -> invoking MoveBox operation: "
            f"{self.config.movebox_invoke_url}"
        )

        timeout = httpx.Timeout(self.config.http_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(self.config.movebox_invoke_url, json=body)

        if response.status_code < 200 or response.status_code >= 300:
            print(
                "[AGENT] MoveBox invoke failed "
                f"status={response.status_code} body={response.text}"
            )
            return

        print(
            "[AGENT] MoveBox invoke accepted "
            f"status={response.status_code} body={response.text}"
        )


def parse_topic(topic: str) -> Optional[tuple[str, str]]:
    parts = topic.split("/")
    if len(parts) < 7:
        print(f"[AGENT] Ignoring topic with insufficient parts0: {topic}")
        return None
    if parts[0] != "sm-repository" or parts[2] != "submodels":
        print(f"[AGENT] Ignoring topic with unexpected structure1: {topic}")
        return None
    if parts[4] != "submodelElements" or parts[6] != "updated":
        print(f"[AGENT] Ignoring topic with unexpected structure2: {topic}")
        return None
    
    # parts[3] is the submodelIdBase64URLEncoded
    # parts[5] is the property idShortPath
    print(f"[AGENT] Parsed topic: submodelId={parts[3]}, propertyId={parts[5]}")
    return parts[3], parts[5]


async def run_agent(config: AgentConfig) -> None:
    orchestrator = FactoryOrchestrator(config)
    pending_tasks: set[asyncio.Task] = set()

    while True:
        try:
            async with MqttClient(hostname=config.mqtt_host, port=config.mqtt_port) as client:
                await client.subscribe(config.mqtt_topic)
                print(
                    "[AGENT] Connected to MQTT broker "
                    f"{config.mqtt_host}:{config.mqtt_port}, subscribed to {config.mqtt_topic}"
                )

                async for message in client.messages:
                    parsed = parse_topic(str(message.topic))
                    if parsed is None:
                        continue

                    submodel_b64, property_id = parsed
                    payload = message.payload.decode(errors="replace")

                    task = asyncio.create_task(
                        orchestrator.handle_event(submodel_b64, property_id, payload)
                    )
                    pending_tasks.add(task)
                    task.add_done_callback(pending_tasks.discard)

        except MqttError as exc:
            print(f"[AGENT] MQTT connection error: {exc}. Reconnecting in 3s...")
            await asyncio.sleep(3)


async def main() -> None:
    config = AgentConfig()
    print("[AGENT] Starting factory orchestration agent...")
    await run_agent(config)


if __name__ == "__main__":
    asyncio.run(main())