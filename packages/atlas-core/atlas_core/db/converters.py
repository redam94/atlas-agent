"""ORM → Pydantic conversion helpers.

Centralizes the explicit-enum-construction pattern that satisfies
``AtlasModel``'s ``strict=True`` configuration when the source values
come from the DB as plain strings.
"""

from atlas_core.db.orm import IngestionJobORM, KnowledgeNodeORM, MessageORM, ProjectORM, SessionORM
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


def knowledge_node_from_orm(row: KnowledgeNodeORM):
    """Convert KnowledgeNodeORM → KnowledgeNode (Pydantic).

    Imports are local to avoid making atlas-core depend on atlas-knowledge
    at module-import time.
    """
    from atlas_knowledge.models.nodes import KnowledgeNode, KnowledgeNodeType

    return KnowledgeNode(
        id=row.id,
        user_id=row.user_id,
        project_id=row.project_id,
        type=KnowledgeNodeType(row.type),
        parent_id=row.parent_id,
        title=row.title,
        text=row.text,
        metadata=dict(row.metadata_ or {}),
        embedding_id=row.embedding_id,
        created_at=row.created_at,
    )


def ingestion_job_from_orm(row: IngestionJobORM):
    """Convert IngestionJobORM → IngestionJob (Pydantic)."""
    from uuid import UUID

    from atlas_knowledge.models.ingestion import IngestionJob, IngestionStatus, SourceType

    # discord_channel_id and notified_at are intentionally omitted — they are internal
    # fields used by the notification poller and not part of the public job contract.
    return IngestionJob(
        id=row.id,
        user_id=row.user_id,
        project_id=row.project_id,
        source_type=SourceType(row.source_type),
        source_filename=row.source_filename,
        status=IngestionStatus(row.status),
        node_ids=[UUID(s) for s in (row.node_ids or [])],
        error=row.error,
        created_at=row.created_at,
        completed_at=row.completed_at,
    )
