"""Semantic factory orchestration agent entry point."""

import asyncio

from config_models import AgentConfig, parse_bool_value
from mqtt_transport import (
    is_operation_reply_topic,
    parse_topic,
    run_agent,
)
from orchestration import FactoryOrchestrator

__all__ = [
    "AgentConfig",
    "FactoryOrchestrator",
    "is_operation_reply_topic",
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
