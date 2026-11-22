"""Shared base class for collectors that use asynchronous HTTP."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from src.collectors.base_collector import BaseCollector

import httpx

if TYPE_CHECKING:
    from src.collectors.definitions.common.measurement import Measurement


class AsyncHttpCollector(BaseCollector, ABC):
    """Collector that owns a lazily created ``httpx.AsyncClient``.

    The measurement manager awaits :meth:`collect_async`, so HTTP waits do not
    occupy a worker thread. The synchronous :meth:`collect` entry point remains
    available for scripts and tests; it closes the HTTP client inside the same
    event loop that created it.
    """

    http_timeout_s: float = 5.0

    _http_client: httpx.AsyncClient | None = None

    def _client(self) -> httpx.AsyncClient:
        """Return the per-collector HTTP client, creating it on first use."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.http_timeout_s),
                follow_redirects=True,
            )
        return self._http_client

    @abstractmethod
    async def collect_async(self) -> list[Measurement]:
        """Collect measurements without blocking the event loop."""

    def collect(self) -> list[Measurement]:
        """Run the asynchronous collector in a private event loop."""

        async def run() -> list[Measurement]:
            try:
                return await self.collect_async()
            finally:
                await self.close_http()

        return asyncio.run(run())

    async def close_http(self) -> None:
        """Close the HTTP client if it was opened."""
        client = self._http_client
        if client is not None:
            self._http_client = None
            await client.aclose()

    async def disconnect_async(self) -> None:
        """Close the HTTP client during manager shutdown."""
        await self.close_http()
