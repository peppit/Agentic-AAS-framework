"""Atomic SemanticCatalog snapshots and periodic Registry reconciliation."""

import asyncio
from collections.abc import Awaitable, Callable

from config_models import AgentConfig
from registry_client import RegistryClient
from semantic_catalog import SemanticCatalog

CatalogCallback = Callable[
    [SemanticCatalog, SemanticCatalog], Awaitable[None]
]


class CatalogManager:
    def __init__(self, catalog: SemanticCatalog, config: AgentConfig) -> None:
        self._catalog = catalog
        self._config = config
        self._lock = asyncio.Lock()

    @classmethod
    async def discover(cls, config: AgentConfig) -> "CatalogManager":
        catalog = await cls._discover_catalog(config)
        return cls(catalog, config)

    @staticmethod
    async def _discover_catalog(config: AgentConfig) -> SemanticCatalog:
        async with RegistryClient(
            config.aas_registry_url,
            config.submodel_registry_url,
            timeout_seconds=config.http_timeout_seconds,
        ) as client:
            return await SemanticCatalog.discover(client)

    async def snapshot(self) -> SemanticCatalog:
        async with self._lock:
            return self._catalog

    async def replace(self, catalog: SemanticCatalog) -> SemanticCatalog:
        async with self._lock:
            previous = self._catalog
            self._catalog = catalog
            return previous

    async def refresh_once(
        self, callback: CatalogCallback | None = None
    ) -> SemanticCatalog:
        refreshed = await self._discover_catalog(self._config)
        previous = await self.replace(refreshed)
        if callback is not None:
            await callback(previous, refreshed)
        return refreshed

    async def run_refresh_loop(
        self, callback: CatalogCallback | None = None
    ) -> None:
        interval = self._config.registry_refresh_seconds
        if interval <= 0:
            return
        while True:
            await asyncio.sleep(interval)
            try:
                await self.refresh_once(callback)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Keep the last complete snapshot; never expose a partial build.
                print(f"[CATALOG] Refresh failed; retaining prior snapshot: {exc}")
