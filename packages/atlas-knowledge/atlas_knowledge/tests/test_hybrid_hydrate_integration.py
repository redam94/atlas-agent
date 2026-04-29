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


@pytest.mark.asyncio
async def test_hydrate_returns_text_and_title(real_pg_session):
    from atlas_knowledge.retrieval.hybrid.hydrate import hydrate

    pid = uuid4()
    doc_id = uuid4()
    chunk_a = uuid4()
    chunk_b = uuid4()

    await real_pg_session.execute(
        text(
            "INSERT INTO projects (id, user_id, name, status, default_model) "
            "VALUES (:id, 'matt', 'h', 'active', 'test-model')"
        ),
        {"id": pid},
    )
    await real_pg_session.execute(
        text(
            "INSERT INTO knowledge_nodes (id, user_id, project_id, type, title, text, metadata) "
            "VALUES (:id, 'matt', :pid, 'document', 'Doc Title', '', '{}'::jsonb)"
        ),
        {"id": doc_id, "pid": pid},
    )
    for cid, content in ((chunk_a, "alpha text"), (chunk_b, "beta text")):
        await real_pg_session.execute(
            text(
                "INSERT INTO knowledge_nodes (id, user_id, project_id, type, parent_id, text, metadata) "
                "VALUES (:id, 'matt', :pid, 'chunk', :doc, :text, '{}'::jsonb)"
            ),
            {"id": cid, "pid": pid, "doc": doc_id, "text": content},
        )
    await real_pg_session.commit()

    try:
        out = await hydrate(real_pg_session, [chunk_a, chunk_b, uuid4()])
        assert set(out.keys()) == {chunk_a, chunk_b}
        assert out[chunk_a].text == "alpha text"
        assert out[chunk_a].parent_title == "Doc Title"
        assert out[chunk_a].parent_id == doc_id
        assert out[chunk_a].user_id == "matt"
        assert out[chunk_a].created_at is not None
        assert out[chunk_b].text == "beta text"
    finally:
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
async def test_hydrate_empty_input(real_pg_session):
    from atlas_knowledge.retrieval.hybrid.hydrate import hydrate

    out = await hydrate(real_pg_session, [])
    assert out == {}
