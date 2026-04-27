"""GET /api/v1/sessions/{session_id}/messages — used by the frontend to rehydrate
per-project conversations on chat-view mount.

Returns ``[]`` when the session row does not exist: the frontend mints
session_ids client-side before any WS connection, so "no row yet" is the
normal first-load state, not an error.
"""

from uuid import UUID

from atlas_core.config import AtlasConfig
from atlas_core.db.converters import message_from_orm
from atlas_core.db.orm import MessageORM, SessionORM
from atlas_core.models.messages import Message
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atlas_api.deps import get_session, get_settings

router = APIRouter(tags=["sessions"])


@router.get("/sessions/{session_id}/messages", response_model=list[Message])
async def list_messages(
    session_id: UUID,
    db: AsyncSession = Depends(get_session),
    settings: AtlasConfig = Depends(get_settings),
) -> list[Message]:
    session_row = await db.get(SessionORM, session_id)
    if session_row is None:
        return []
    if session_row.user_id != settings.user_id:
        # 403 (rather than []) intentionally surfaces "session exists but is not yours"
        # in Phase 1 single-user mode, where this branch is dead code anyway. In Phase 2
        # multi-user, this becomes an existence oracle on session UUIDs — at that point,
        # consider collapsing this branch to `return []` so cross-user probes can't
        # distinguish "session is real but yours" from "session never existed".
        raise HTTPException(status_code=403, detail="forbidden")
    result = await db.execute(
        select(MessageORM)
        .where(MessageORM.session_id == session_id)
        .order_by(MessageORM.created_at.asc())
    )
    return [message_from_orm(row) for row in result.scalars().all()]
