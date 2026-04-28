"""Unit tests for MigrationRunner — mocked driver, temp migrations dir."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas_graph.schema.runner import MigrationRunner


def _mock_driver_with_session(applied_ids: list[str]):
    """Driver mock whose execute_read returns applied_ids; execute_write captures calls."""
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    session.run = AsyncMock()
    session.execute_read = AsyncMock(return_value=[{"id": mid} for mid in applied_ids])
    session.execute_write = AsyncMock()
    driver = MagicMock()
    driver.session = MagicMock(return_value=session)
    driver.close = AsyncMock()
    return driver, session


@pytest.mark.asyncio
async def test_run_pending_applies_unapplied_files_in_order(tmp_path: Path):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_first.cypher").write_text("CREATE CONSTRAINT a IF NOT EXISTS FOR (n:A) REQUIRE n.id IS UNIQUE;")
    (migrations_dir / "002_second.cypher").write_text("CREATE INDEX b IF NOT EXISTS FOR (n:A) ON (n.x);")

    driver, session = _mock_driver_with_session(applied_ids=[])
    runner = MigrationRunner(driver, migrations_dir)

    applied = await runner.run_pending()
    assert applied == ["001", "002"]
    assert session.execute_write.await_count == 2  # one execute_write per migration


@pytest.mark.asyncio
async def test_run_pending_skips_already_applied(tmp_path: Path):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_first.cypher").write_text("CREATE CONSTRAINT a IF NOT EXISTS FOR (n:A) REQUIRE n.id IS UNIQUE;")
    (migrations_dir / "002_second.cypher").write_text("CREATE INDEX b IF NOT EXISTS FOR (n:A) ON (n.x);")

    driver, session = _mock_driver_with_session(applied_ids=["001"])
    runner = MigrationRunner(driver, migrations_dir)

    applied = await runner.run_pending()
    assert applied == ["002"]
    assert session.execute_write.await_count == 1


@pytest.mark.asyncio
async def test_run_pending_idempotent_when_all_applied(tmp_path: Path):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_first.cypher").write_text("CREATE CONSTRAINT a IF NOT EXISTS FOR (n:A) REQUIRE n.id IS UNIQUE;")

    driver, session = _mock_driver_with_session(applied_ids=["001"])
    runner = MigrationRunner(driver, migrations_dir)

    applied = await runner.run_pending()
    assert applied == []
    session.execute_write.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_pending_ignores_non_matching_filenames(tmp_path: Path):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_real.cypher").write_text("RETURN 1;")
    (migrations_dir / "README.md").write_text("docs")
    (migrations_dir / "abc_invalid.cypher").write_text("RETURN 2;")
    (migrations_dir / "0001_too_many_digits.cypher").write_text("RETURN 3;")

    driver, _ = _mock_driver_with_session(applied_ids=[])
    runner = MigrationRunner(driver, migrations_dir)

    applied = await runner.run_pending()
    assert applied == ["001"]


@pytest.mark.asyncio
async def test_run_pending_handles_gap_in_id_sequence(tmp_path: Path):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_a.cypher").write_text("RETURN 1;")
    (migrations_dir / "003_c.cypher").write_text("RETURN 3;")
    # No 002 — runner should still apply 001 then 003.

    driver, session = _mock_driver_with_session(applied_ids=[])
    runner = MigrationRunner(driver, migrations_dir)

    applied = await runner.run_pending()
    assert applied == ["001", "003"]
    assert session.execute_write.await_count == 2


@pytest.mark.asyncio
async def test_run_pending_splits_multi_statement_files(tmp_path: Path):
    """A .cypher file with multiple ;-separated statements should run each."""
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_multi.cypher").write_text(
        "CREATE CONSTRAINT a IF NOT EXISTS FOR (n:A) REQUIRE n.id IS UNIQUE;\n"
        "CREATE INDEX a_x IF NOT EXISTS FOR (n:A) ON (n.x);"
    )

    captured: list[str] = []

    async def fake_execute_write(fn):
        tx = AsyncMock()
        async def fake_run(cypher, **params):
            captured.append(cypher.strip())
        tx.run = fake_run
        await fn(tx)

    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    session.execute_read = AsyncMock(return_value=[])
    session.execute_write = fake_execute_write
    driver = MagicMock()
    driver.session = MagicMock(return_value=session)

    runner = MigrationRunner(driver, migrations_dir)
    applied = await runner.run_pending()
    assert applied == ["001"]
    # 2 schema statements + 1 ledger MERGE
    assert len(captured) == 3
    assert any("CREATE CONSTRAINT" in c for c in captured)
    assert any("CREATE INDEX" in c for c in captured)
    assert any("MERGE (m:Migration" in c for c in captured)
