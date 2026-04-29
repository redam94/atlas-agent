"""Tests for atlas_core.db.orm — pure structural checks (no DB roundtrip)."""

from sqlalchemy import inspect

from atlas_core.db.orm import ProjectORM


def test_project_orm_is_mapped_to_projects_table():
    assert ProjectORM.__tablename__ == "projects"


def test_project_orm_has_expected_columns():
    columns = {c.name for c in inspect(ProjectORM).columns}
    assert columns == {
        "id",
        "user_id",
        "name",
        "description",
        "status",
        "privacy_level",
        "default_model",
        "enabled_plugins",
        "created_at",
        "updated_at",
    }


def test_project_orm_id_is_uuid_primary_key():
    pk_cols = inspect(ProjectORM).primary_key
    assert len(pk_cols) == 1
    assert pk_cols[0].name == "id"


import pytest


@pytest.mark.asyncio
async def test_ingestion_job_pagerank_status_default(db_session):
    """A freshly-inserted IngestionJob defaults pagerank_status to 'skipped'."""
    from atlas_core.db.orm import IngestionJobORM, ProjectORM

    proj = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(proj)
    await db_session.flush()

    job = IngestionJobORM(
        user_id="matt", project_id=proj.id, source_type="markdown",
    )
    db_session.add(job)
    await db_session.flush()
    await db_session.refresh(job)
    assert job.pagerank_status == "skipped"
