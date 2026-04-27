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
