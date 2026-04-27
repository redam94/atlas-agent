"""WebSocket chat endpoint.

Per-message flow:
  1. Receive a JSON message; validate against the WS protocol.
  2. Load (or create) the Session row for this WebSocket connection.
  3. Load the Project + recent Messages for context.
  4. Build the system prompt via SystemPromptBuilder.
  5. Route to a provider via ModelRouter.
  6. Stream ModelEvents → translate to StreamEvents → send to client.
  7. On done: persist user + assistant Message rows + ModelUsage row.
"""
from __future__ import annotations

import time
from uuid import UUID

import structlog
from atlas_core.config import AtlasConfig
from atlas_core.db.orm import MessageORM, ModelUsageORM, ProjectORM, SessionORM
from atlas_core.models.llm import ModelEventType
from atlas_core.models.messages import ChatRequest, StreamEvent, StreamEventType
from atlas_core.models.sessions import MessageRole
from atlas_core.prompts.builder import SystemPromptBuilder
from atlas_core.prompts.registry import prompt_registry
from atlas_core.providers.registry import ModelRouter
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from atlas_api.deps import get_model_router, get_session, get_settings

router = APIRouter()
log = structlog.get_logger("atlas.api.ws")
prompt_builder = SystemPromptBuilder(prompt_registry)

CONTEXT_WINDOW_TURNS = 20  # Plan 5 will adapt this dynamically


@router.websocket("/ws/{session_id}")
async def chat_ws(
    websocket: WebSocket,
    session_id: UUID,
    db: AsyncSession = Depends(get_session),
    model_router: ModelRouter = Depends(get_model_router),
    settings: AtlasConfig = Depends(get_settings),
) -> None:
    await websocket.accept()
    sequence = 0
    structlog.contextvars.bind_contextvars(session_id=str(session_id))
    log.info("ws.connect")

    try:
        while True:
            try:
                raw = await websocket.receive_json()
            except WebSocketDisconnect:
                log.info("ws.disconnect")
                return

            msg_type = raw.get("type")
            payload = raw.get("payload", {})

            if msg_type != "chat.message":
                sequence = await _send(
                    websocket,
                    StreamEventType.ERROR,
                    {"code": "unknown_type", "message": f"unknown message type: {msg_type}"},
                    sequence,
                )
                continue

            try:
                req = ChatRequest.model_validate(payload)
            except Exception as e:
                sequence = await _send(
                    websocket,
                    StreamEventType.ERROR,
                    {"code": "invalid_payload", "message": str(e)},
                    sequence,
                )
                continue

            try:
                sequence = await _handle_chat_message(
                    websocket, session_id, req, db, model_router, settings, sequence
                )
            except Exception as e:
                log.exception("ws.unhandled_error")
                sequence = await _send(
                    websocket,
                    StreamEventType.ERROR,
                    {"code": "internal_error", "message": str(e)},
                    sequence,
                )
    finally:
        structlog.contextvars.unbind_contextvars("session_id")


async def _handle_chat_message(
    websocket: WebSocket,
    session_id: UUID,
    req: ChatRequest,
    db: AsyncSession,
    model_router: ModelRouter,
    settings: AtlasConfig,
    sequence: int,
) -> int:
    # 1. Resolve the Project (must exist for this user)
    project = await db.get(ProjectORM, req.project_id)
    if project is None or project.user_id != settings.user_id:
        return await _send(
            websocket,
            StreamEventType.ERROR,
            {"code": "project_not_found", "message": "project not found or unauthorized"},
            sequence,
        )

    # 2. Ensure the Session row exists
    session_row = await db.get(SessionORM, session_id)
    if session_row is None:
        session_row = SessionORM(
            id=session_id,
            user_id=settings.user_id,
            project_id=project.id,
            model=req.model_override or project.default_model,
        )
        db.add(session_row)
        await db.flush()

    # 3. Build the message history for the model
    history_rows = await _load_recent_messages(db, session_id, limit=CONTEXT_WINDOW_TURNS)
    system_prompt = prompt_builder.build(_project_to_pydantic(project))
    model_messages = _assemble_messages(system_prompt, history_rows, req.text)

    # 4. Persist the user turn before streaming the assistant response
    user_row = MessageORM(
        user_id=settings.user_id,
        session_id=session_id,
        role=MessageRole.USER.value,
        content=req.text,
    )
    db.add(user_row)
    await db.flush()

    # 5. Route to a provider
    try:
        provider = model_router.select(
            _project_to_pydantic(project), model_override=req.model_override
        )
    except ValueError as e:
        return await _send(
            websocket,
            StreamEventType.ERROR,
            {"code": "no_provider", "message": str(e)},
            sequence,
        )

    # 6. Stream events
    assistant_text_parts: list[str] = []
    usage: dict | None = None
    started = time.monotonic()

    async for event in provider.stream(
        messages=model_messages,
        temperature=req.temperature,
    ):
        if event.type == ModelEventType.TOKEN:
            text = event.data.get("text", "")
            assistant_text_parts.append(text)
            sequence = await _send(
                websocket, StreamEventType.TOKEN, {"token": text}, sequence
            )
        elif event.type == ModelEventType.TOOL_CALL:
            sequence = await _send(
                websocket, StreamEventType.TOOL_CALL, event.data, sequence
            )
        elif event.type == ModelEventType.TOOL_RESULT:
            sequence = await _send(
                websocket, StreamEventType.TOOL_RESULT, event.data, sequence
            )
        elif event.type == ModelEventType.ERROR:
            return await _send(
                websocket, StreamEventType.ERROR, event.data, sequence
            )
        elif event.type == ModelEventType.DONE:
            usage = event.data.get("usage", {})

    latency_ms = int((time.monotonic() - started) * 1000)
    full_assistant_text = "".join(assistant_text_parts)

    # 7. Persist the assistant turn + usage
    assistant_row = MessageORM(
        user_id=settings.user_id,
        session_id=session_id,
        role=MessageRole.ASSISTANT.value,
        content=full_assistant_text,
        model=provider.spec.model_id,
        token_count=(usage or {}).get("output_tokens"),
    )
    db.add(assistant_row)

    if usage:
        db.add(
            ModelUsageORM(
                user_id=settings.user_id,
                session_id=session_id,
                project_id=project.id,
                provider=usage.get("provider", provider.spec.provider),
                model_id=usage.get("model_id", provider.spec.model_id),
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                latency_ms=usage.get("latency_ms", latency_ms),
            )
        )

    await db.flush()

    sequence = await _send(
        websocket,
        StreamEventType.DONE,
        {
            "usage": usage or {},
            "model": provider.spec.model_id,
            "latency_ms": latency_ms,
        },
        sequence,
    )
    return sequence


async def _send(
    websocket: WebSocket,
    type_: StreamEventType,
    payload: dict,
    sequence: int,
) -> int:
    event = StreamEvent(type=type_, payload=payload, sequence=sequence)
    await websocket.send_json(event.model_dump(mode="json"))
    return sequence + 1


async def _load_recent_messages(
    db: AsyncSession, session_id: UUID, limit: int
) -> list[MessageORM]:
    result = await db.execute(
        select(MessageORM)
        .where(MessageORM.session_id == session_id)
        .order_by(desc(MessageORM.created_at))
        .limit(limit)
    )
    rows = list(result.scalars().all())
    rows.reverse()  # ascending
    return rows


def _assemble_messages(
    system_prompt: str,
    history: list[MessageORM],
    new_user_text: str,
) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system_prompt}]
    for row in history:
        out.append({"role": row.role, "content": row.content})
    out.append({"role": "user", "content": new_user_text})
    return out


def _project_to_pydantic(row: ProjectORM):
    """Local converter to avoid circular imports — uses the public converter."""
    from atlas_core.db.converters import project_from_orm
    return project_from_orm(row)
