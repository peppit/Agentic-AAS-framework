"""Factory agent entry point and backwards-compatible public API."""

import asyncio

from config_models import (
    AgentConfig,
    RobotEndpoints,
    normalize_station_id,
    normalize_submodel_id,
    parse_bool_value,
)
from mqtt_transport import (
    is_operation_reply_topic,
    parse_topic,
    run_agent,
)
from orchestration import FactoryOrchestrator

__all__ = [
    "AgentConfig",
    "FactoryOrchestrator",
    "RobotEndpoints",
    "is_operation_reply_topic",
    "normalize_station_id",
    "normalize_submodel_id",
    "parse_bool_value",
    "parse_topic",
    "run_agent",
]


async def main() -> None:
    config = AgentConfig()
    print("[AGENT] Starting factory orchestration agent...")
    await run_agent(config)


if __name__ == "__main__":
    asyncio.run(main())
