import asyncio
import time
from typing import Optional

from aiomqtt import Client as MqttClient
from aiomqtt import MqttError

from config_models import AgentConfig
from orchestration import FactoryOrchestrator


def parse_topic(topic: str) -> Optional[tuple[str, str]]:
    parts = topic.split("/")
    if (
        len(parts) >= 7
        and parts[0] == "sm-repository"
        and parts[2] == "submodels"
        and parts[4] == "submodelElements"
        and parts[6] == "updated"
    ):
        return parts[3], parts[5]
    return None


def is_operation_reply_topic(topic: str) -> bool:
    parts = topic.split("/")
    return (
        len(parts) == 4
        and parts[0] == "simulation"
        and parts[2] == "replies"
        and bool(parts[1])
        and bool(parts[3])
    )


def is_server_status_topic(topic: str) -> bool:
    return topic == "simulation/server/status"


def is_station_status_topic(topic: str) -> bool:
    parts = topic.split("/")
    return (
        len(parts) == 3
        and parts[0] == "simulation"
        and parts[1] != "server"
        and parts[2] == "status"
    )


async def run_agent(config: AgentConfig) -> None:
    orchestrator = FactoryOrchestrator(config)
    worker = asyncio.create_task(orchestrator.start_worker())
    dispatcher = asyncio.create_task(orchestrator.start_dispatcher())

    try:
        while True:
            try:
                async with MqttClient(
                    hostname=config.mqtt_host,
                    port=config.mqtt_port,
                ) as client:
                    await client.subscribe(config.mqtt_topic)
                    await client.subscribe(config.operation_reply_topic)
                    await client.subscribe(config.server_status_topic)
                    await client.subscribe(config.station_status_topic)
                    print(
                        "[AGENT] Connected to MQTT broker "
                        f"{config.mqtt_host}:{config.mqtt_port}, subscribed "
                        f"to {config.mqtt_topic} and "
                        f"{config.operation_reply_topic}, "
                        f"{config.server_status_topic}, and "
                        f"{config.station_status_topic}"
                    )

                    async for message in client.messages:
                        topic = str(message.topic)
                        payload = message.payload.decode(errors="replace")
                        if is_operation_reply_topic(topic):
                            await orchestrator.handle_operation_ack(payload)
                            continue
                        if is_server_status_topic(topic):
                            await orchestrator.handle_server_status(payload)
                            continue
                        if is_station_status_topic(topic):
                            await orchestrator.handle_station_status(topic, payload)
                            continue

                        parsed = parse_topic(topic)
                        if parsed is not None:
                            submodel_b64, property_id = parsed
                            await orchestrator.handle_event(
                                submodel_b64,
                                property_id,
                                payload,
                                topic,
                                int(time.time() * 1000),
                            )
            except MqttError as exc:
                print(
                    f"[AGENT] MQTT connection error: {exc}. "
                    "Reconnecting in 3s..."
                )
                await asyncio.sleep(3)
    finally:
        worker.cancel()
        dispatcher.cancel()
        await asyncio.gather(
            worker,
            dispatcher,
            return_exceptions=True,
        )
        await orchestrator.close()
