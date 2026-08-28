"""MQTT transport for semantic AAS state events and operation replies."""

import asyncio
import time
from typing import Optional

from aiomqtt import Client as MqttClient
from aiomqtt import MqttError

from catalog_runtime import CatalogManager
from config_models import AgentConfig
from orchestration import FactoryOrchestrator


def parse_topic(topic: str) -> Optional[tuple[str, str]]:
    parts = topic.split("/")
    if (
        len(parts) >= 7
        and parts[0] == "sm-repository"
        and parts[2] == "submodels"
        and parts[4] == "submodelElements"
        and parts[-1] == "updated"
    ):
        return parts[3], parts[5]
    return None


def is_operation_reply_topic(topic: str) -> bool:
    parts = topic.split("/")
    return len(parts) >= 4 and parts[0] == "simulation" and "replies" in parts


async def run_agent(config: AgentConfig) -> None:
    catalog_manager = await CatalogManager.discover(config)
    initial_catalog = await catalog_manager.snapshot()
    if config.semantic_discovery_diagnostic:
        print(initial_catalog.diagnostic_summary())

    orchestrator = FactoryOrchestrator(config, catalog_manager)
    await orchestrator.initialize()
    worker = asyncio.create_task(orchestrator.start_worker())
    refresher = asyncio.create_task(
        catalog_manager.run_refresh_loop(orchestrator.reconcile_catalog)
    )

    try:
        while True:
            try:
                async with MqttClient(
                    hostname=config.mqtt_host,
                    port=config.mqtt_port,
                ) as client:
                    await client.subscribe(config.mqtt_topic)
                    await client.subscribe(config.operation_reply_topic)
                    print(
                        "[AGENT] Connected to MQTT broker "
                        f"{config.mqtt_host}:{config.mqtt_port}; subscribed "
                        f"to semantic states {config.mqtt_topic} and "
                        f"operation replies {config.operation_reply_topic}"
                    )

                    async for message in client.messages:
                        topic = str(message.topic)
                        payload = message.payload.decode(errors="replace")
                        if is_operation_reply_topic(topic):
                            await orchestrator.handle_operation_ack(payload)
                            continue
                        parsed = parse_topic(topic)
                        if parsed is not None:
                            submodel_token, element_token = parsed
                            await orchestrator.handle_event(
                                submodel_token,
                                element_token,
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
        refresher.cancel()
        await asyncio.gather(worker, refresher, return_exceptions=True)
        await orchestrator.close()
