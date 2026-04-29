"""Postgres FTS BM25-flavored search via websearch_to_tsquery + ts_rank_cd."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Returns rank positions (1..N), not raw scores, so RRF can merge across heterogeneous rankers.
_SQL = text(
    """
    SELECT id
    FROM knowledge_nodes
    WHERE type = 'chunk'
      AND project_id = :project_id
      AND fts @@ websearch_to_tsquery('english', :query)
    ORDER BY ts_rank_cd(fts, websearch_to_tsquery('english', :query)) DESC, id ASC
    LIMIT :top_k
    """
)


async def search(
    session: AsyncSession,
    project_id: UUID,
    query: str,
    top_k: int = 20,
) -> list[tuple[UUID, int]]:
    """Return ``[(chunk_id, rank), ...]`` ordered by descending FTS relevance.

    ``rank`` is 1-indexed ordinal position. An empty match set returns ``[]``;
    callers should treat this as "BM25 found nothing", not an error.
    """
    if not query.strip():
        return []
    result = await session.execute(
        _SQL, {"project_id": project_id, "query": query, "top_k": top_k}
    )
    rows = result.all()
    return [(row[0], idx) for idx, row in enumerate(rows, start=1)]
