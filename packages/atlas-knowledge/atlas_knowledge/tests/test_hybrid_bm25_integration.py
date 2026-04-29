"""bm25.search against a real Postgres with the 0005 migration applied."""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


def _enabled() -> bool:
    return os.getenv("ATLAS_RUN_POSTGRES_INTEGRATION") == "1"


@pytest_asyncio.fixture
async def real_pg_session():
    if not _enabled():
        pytest.skip("set ATLAS_RUN_POSTGRES_INTEGRATION=1 to enable")
    url = os.environ["ATLAS_DB__DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def isolated_project_id_pg(real_pg_session):
    """Create a project row, yield its id, teardown deletes its chunks."""
    pid = uuid4()
    await real_pg_session.execute(
        text(
            "INSERT INTO projects (id, user_id, name, status, default_model) "
            "VALUES (:id, 'matt', 'bm25-test', 'active', 'test-model')"
        ),
        {"id": pid},
    )
    await real_pg_session.commit()
    yield pid
    await real_pg_session.execute(
        text("DELETE FROM knowledge_nodes WHERE project_id = :pid"),
        {"pid": pid},
    )
    await real_pg_session.execute(
        text("DELETE FROM projects WHERE id = :pid"),
        {"pid": pid},
    )
    await real_pg_session.commit()


@pytest.mark.asyncio
async def test_bm25_returns_ranked_chunks(real_pg_session, isolated_project_id_pg):
    from atlas_knowledge.retrieval.hybrid.bm25 import search

    pid = isolated_project_id_pg
    chunk_a = uuid4()
    chunk_b = uuid4()
    chunk_c = uuid4()
    # chunk_a strongly matches "geo lift", chunk_b weakly, chunk_c not at all.
    rows = [
        (chunk_a, "geo lift methodology measures geo lift in geo lift studies", 0),
        (chunk_b, "geo lift appeared once in the study", 1),
        (chunk_c, "completely unrelated content about coffee", 2),
    ]
    for cid, content, pos in rows:
        await real_pg_session.execute(
            text(
                "INSERT INTO knowledge_nodes (id, user_id, project_id, type, text, metadata) "
                "VALUES (:id, 'matt', :pid, 'chunk', :text, '{}'::jsonb)"
            ),
            {"id": cid, "pid": pid, "text": content},
        )
    await real_pg_session.commit()

    results = await search(
        session=real_pg_session, project_id=pid, query="geo lift", top_k=10
    )

    assert len(results) == 2  # chunk_c does not match
    assert results[0][0] == chunk_a  # strongest match first
    assert results[0][1] == 1  # rank position 1
    assert results[1][0] == chunk_b
    assert results[1][1] == 2


@pytest.mark.asyncio
async def test_bm25_empty_query_returns_empty(real_pg_session, isolated_project_id_pg):
    from atlas_knowledge.retrieval.hybrid.bm25 import search

    results = await search(
        session=real_pg_session,
        project_id=isolated_project_id_pg,
        query="",  # websearch_to_tsquery treats this as empty
        top_k=10,
    )
    assert results == []


@pytest.mark.asyncio
async def test_bm25_filters_by_project(real_pg_session, isolated_project_id_pg):
    from atlas_knowledge.retrieval.hybrid.bm25 import search

    other_pid = uuid4()
    other_chunk = uuid4()
    await real_pg_session.execute(
        text(
            "INSERT INTO projects (id, user_id, name, status, default_model) "
            "VALUES (:id, 'matt', 'other', 'active', 'test-model')"
        ),
        {"id": other_pid},
    )
    await real_pg_session.execute(
        text(
            "INSERT INTO knowledge_nodes (id, user_id, project_id, type, text, metadata) "
            "VALUES (:id, 'matt', :pid, 'chunk', 'geo lift', '{}'::jsonb)"
        ),
        {"id": other_chunk, "pid": other_pid},
    )
    await real_pg_session.commit()
    try:
        results = await search(
            session=real_pg_session,
            project_id=isolated_project_id_pg,
            query="geo lift",
            top_k=10,
        )
        assert all(r[0] != other_chunk for r in results)
    finally:
        await real_pg_session.execute(
            text("DELETE FROM knowledge_nodes WHERE project_id = :pid"),
            {"pid": other_pid},
        )
        await real_pg_session.execute(
            text("DELETE FROM projects WHERE id = :pid"),
            {"pid": other_pid},
        )
        await real_pg_session.commit()
