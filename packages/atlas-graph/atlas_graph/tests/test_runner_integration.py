"""MigrationRunner against a real Neo4j."""
from __future__ import annotations

from pathlib import Path

import pytest

from atlas_graph.schema.runner import MigrationRunner

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_initial_schema_applies_then_is_idempotent(real_neo4j_driver):
    """Apply 001_initial_schema, second run is a no-op, ledger node exists."""
    migrations_dir = Path(__file__).parent.parent / "schema" / "migrations"
    runner = MigrationRunner(real_neo4j_driver, migrations_dir)

    # First run applies (or no-op if already applied).
    first = await runner.run_pending()
    # Second run is always a no-op.
    second = await runner.run_pending()
    assert second == []

    # Ledger node exists for 001.
    async with real_neo4j_driver.session() as s:
        result = await s.run("MATCH (m:Migration {id: '001'}) RETURN m.id AS id")
        records = [r async for r in result]
    assert len(records) == 1
    assert records[0]["id"] == "001"
    # If first call applied for the first time, applied list must contain '001'.
    if first:
        assert "001" in first
