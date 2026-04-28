"""GraphStore — async wrapper around the neo4j driver."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import structlog
from neo4j.exceptions import ServiceUnavailable, TransientError

from atlas_graph.errors import GraphUnavailableError

if TYPE_CHECKING:
    from neo4j import AsyncDriver
    from neo4j._async.driver import AsyncTransaction

log = structlog.get_logger("atlas.graph.store")


class GraphStore:
    """Async wrapper around the neo4j AsyncDriver.

    Constructor does NOT open a connection — the first method call (or
    healthcheck()) is when the driver actually probes the server. On transient
    failures we retry with exponential backoff; persistent failures raise
    GraphUnavailableError.
    """

    def __init__(self, driver: AsyncDriver, *, max_retries: int = 3) -> None:
        self._driver = driver
        self._max_retries = max_retries

    async def close(self) -> None:
        await self._driver.close()

    async def healthcheck(self) -> None:
        """Run `RETURN 1` against the driver. Raises GraphUnavailableError on persistent failure."""
        async with self._session() as s:
            await s.run("RETURN 1")

    async def _with_retry(
        self,
        fn: Callable[[AsyncTransaction], Awaitable[None]],
    ) -> None:
        """Execute fn inside a write transaction, retrying transient failures.

        Retries up to ``max_retries`` times with exponential backoff
        (0.5 s -> 1 s -> 2 s). Wraps the final failure in GraphUnavailableError.
        """
        delay = 0.5
        for attempt in range(1, self._max_retries + 1):
            try:
                async with self._session() as s:
                    await s.execute_write(fn)
                return
            except (ServiceUnavailable, TransientError) as e:
                if attempt == self._max_retries:
                    raise GraphUnavailableError(f"neo4j unavailable: {e}") from e
                log.warning("graph.retry", attempt=attempt, error=str(e))
                await asyncio.sleep(delay)
                delay *= 2

    @asynccontextmanager
    async def _session(self):
        async with self._driver.session() as s:
            yield s
