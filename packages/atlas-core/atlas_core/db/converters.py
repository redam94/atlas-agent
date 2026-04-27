"""ORM → Pydantic conversion helpers.

Centralizes the explicit-enum-construction pattern that satisfies
``AtlasModel``'s ``strict=True`` configuration when the source values
come from the DB as plain strings.
"""

from atlas_core.db.orm import ProjectORM
from atlas_core.models.projects import (
    PrivacyLevel,
    Project,
    ProjectStatus,
)


def project_from_orm(row: ProjectORM) -> Project:
    """Convert a ProjectORM row to the Project Pydantic model."""
    return Project(
        id=row.id,
        user_id=row.user_id,
        name=row.name,
        description=row.description,
        status=ProjectStatus(row.status),
        privacy_level=PrivacyLevel(row.privacy_level),
        default_model=row.default_model,
        enabled_plugins=list(row.enabled_plugins or []),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
