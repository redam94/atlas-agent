"""ORM → Pydantic conversion helpers.

Centralizes the explicit-enum-construction pattern that satisfies
``AtlasModel``'s ``strict=True`` configuration when the source values
come from the DB as plain strings.
"""

from atlas_core.db.orm import MessageORM, ProjectORM, SessionORM
from atlas_core.models.messages import Message
from atlas_core.models.projects import (
    PrivacyLevel,
    Project,
    ProjectStatus,
)
from atlas_core.models.sessions import MessageRole, Session


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


def session_from_orm(row: SessionORM) -> Session:
    """Convert a SessionORM row to the Session Pydantic model."""
    return Session(
        id=row.id,
        user_id=row.user_id,
        project_id=row.project_id,
        model=row.model,
        created_at=row.created_at,
        last_active_at=row.last_active_at,
    )


def message_from_orm(row: MessageORM) -> Message:
    """Convert a MessageORM row to the Message Pydantic model."""
    return Message(
        id=row.id,
        user_id=row.user_id,
        session_id=row.session_id,
        role=MessageRole(row.role),
        content=row.content,
        tool_calls=row.tool_calls,
        rag_context=row.rag_context,
        model=row.model,
        token_count=row.token_count,
        created_at=row.created_at,
    )
