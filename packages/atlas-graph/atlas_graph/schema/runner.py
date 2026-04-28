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
            # Split schema (DDL) from write statements since Neo4j doesn't allow them
            # in the same transaction.
            schema_keywords = (
                "CREATE CONSTRAINT",
                "CREATE INDEX",
                "DROP CONSTRAINT",
                "DROP INDEX",
            )
            schema_stmts = []
            write_stmts = []
            for stmt in [s.strip() for s in cypher.split(";") if s.strip()]:
                if any(kw in stmt.upper() for kw in schema_keywords):
                    schema_stmts.append(stmt)
                else:
                    write_stmts.append(stmt)

            # Execute schema DDL in its own transaction.
            if schema_stmts:
                async with self._driver.session() as s:
                    for stmt in schema_stmts:
                        await s.execute_write(
                            lambda tx, s=stmt: tx.run(s)
                        )

            # Execute write statements and migration ledger in a separate transaction.
            async with self._driver.session() as s:
                await s.execute_write(self._make_apply(mid, write_stmts))
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
    def _make_apply(mid: str, write_stmts: list[str]):
        async def _apply(tx: AsyncTransaction) -> None:
            # Execute write statements (data manipulation only, no DDL).
            for stmt in write_stmts:
                await tx.run(stmt)
            # Record migration in ledger.
            await tx.run(
                "MERGE (m:Migration {id: $id}) "
                "ON CREATE SET m.applied_at = datetime()",
                id=mid,
            )
        return _apply
