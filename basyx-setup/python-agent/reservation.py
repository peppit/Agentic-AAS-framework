"""Single-process atomic resource reservation for semantic scheduling."""

import asyncio
from collections.abc import Iterable


class ReservationManager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.reserved_resources: set[str] = set()

    async def reserve_if_available(self, resource_id: str) -> bool:
        async with self._lock:
            if resource_id in self.reserved_resources:
                return False
            self.reserved_resources.add(resource_id)
            return True

    async def select_and_reserve(
        self, candidate_resource_ids: Iterable[str]
    ) -> str | None:
        """Select stable-first and reserve in the same critical section."""

        async with self._lock:
            for resource_id in sorted(set(candidate_resource_ids)):
                if resource_id not in self.reserved_resources:
                    self.reserved_resources.add(resource_id)
                    return resource_id
        return None

    async def release(self, resource_id: str) -> None:
        async with self._lock:
            self.reserved_resources.discard(resource_id)

    async def is_reserved(self, resource_id: str) -> bool:
        async with self._lock:
            return resource_id in self.reserved_resources
