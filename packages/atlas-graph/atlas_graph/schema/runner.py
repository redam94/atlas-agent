"""Migration runner — applies *.cypher files in id order, records ledger."""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from neo4j import AsyncDriver
    from neo4j._async.driver import AsyncTransaction

log = structlog.get_logger("atlas.graph.migrations")

_MIGRATION_FILE_RE = re.compile(r"^(\d{3})_[a-z0-9_]+\.cypher$")


class MigrationRunner:
    """Discover and apply *.cypher files; record applied ids in (:Migration)."""

    def __init__(self, driver: AsyncDriver, migrations_dir: Path) -> None:
        self._driver = driver
        self._migrations_dir = migrations_dir

    async def run_pending(self) -> list[str]:
        """Apply every migration not already in the (:Migration) ledger.

        Returns the ordered list of newly-applied migration ids.
        """
        applied = await self._load_applied()
        files: list[tuple[str, Path]] = []
        for f in sorted(self._migrations_dir.glob("*.cypher")):
            m = _MIGRATION_FILE_RE.match(f.name)
            if not m:
                continue
            mid = m.group(1)
            files.append((mid, f))

        newly_applied: list[str] = []
        for mid, path in files:
            if mid in applied:
                continue
            cypher = path.read_text()
            async with self._driver.session() as s:
                await s.execute_write(self._make_apply(mid, cypher))
            log.info("graph.migration.applied", id=mid, file=path.name)
            newly_applied.append(mid)
        return newly_applied

    async def _load_applied(self) -> set[str]:
        async with self._driver.session() as s:
            records = await s.execute_read(self._read_applied)
        return {r["id"] for r in records}

    @staticmethod
    async def _read_applied(tx: AsyncTransaction):
        result = await tx.run("MATCH (m:Migration) RETURN m.id AS id")
        return [r async for r in result]

    @staticmethod
    def _make_apply(mid: str, cypher: str):
        async def _apply(tx: AsyncTransaction) -> None:
            for stmt in [s.strip() for s in cypher.split(";") if s.strip()]:
                await tx.run(stmt)
            await tx.run(
                "MERGE (m:Migration {id: $id}) "
                "ON CREATE SET m.applied_at = datetime()",
                id=mid,
            )
        return _apply
