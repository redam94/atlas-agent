"""Bulk-fetch chunk text + parent_title from Postgres in one round-trip."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class ChunkText:
    id: UUID
    user_id: str
    text: str
    parent_id: UUID
    parent_title: str | None
    created_at: datetime


_SQL = text(
    """
    SELECT c.id, c.user_id, c.text, c.parent_id, d.title AS parent_title, c.created_at
    FROM knowledge_nodes c
    LEFT JOIN knowledge_nodes d ON d.id = c.parent_id
    WHERE c.id = ANY(:ids) AND c.type = 'chunk'
    """
)


async def hydrate(
    session: AsyncSession,
    chunk_ids: Iterable[UUID],
) -> dict[UUID, ChunkText]:
    """Return ``{id: ChunkText}``. Missing or non-chunk IDs are silently dropped."""
    ids = list(chunk_ids)
    if not ids:
        return {}
    result = await session.execute(_SQL, {"ids": ids})
    out: dict[UUID, ChunkText] = {}
    for row in result.all():
        out[row[0]] = ChunkText(
            id=row[0],
            user_id=row[1],
            text=row[2] or "",
            parent_id=row[3],
            parent_title=row[4],
            created_at=row[5],
        )
    return out
