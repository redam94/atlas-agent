"""WebSocket chat endpoint.

Per-message flow:
  1. Receive a JSON message; validate against the WS protocol.
  2. Load (or create) the Session row for this WebSocket connection.
  3. Load the Project + recent Messages for context.
  4. Build the system prompt via SystemPromptBuilder.
  5. Route to a provider via ModelRouter.
  6. Stream ModelEvents → translate to StreamEvents → send to client.
     For Anthropic providers: run a tool-use loop (max 10 turns).
  7. On done: persist user + assistant Message rows + ModelUsage row.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from atlas_core.config import AtlasConfig
from atlas_core.db.orm import MessageORM, ModelUsageORM, ProjectORM, SessionORM
from atlas_core.models.llm import ModelEventType, ToolSchema
from atlas_core.models.messages import ChatRequest, StreamEvent, StreamEventType
from atlas_core.models.sessions import MessageRole
from atlas_core.prompts.builder import SystemPromptBuilder
from atlas_core.prompts.registry import prompt_registry
from atlas_core.providers.registry import ModelRouter
from atlas_knowledge.models.retrieval import RetrievalQuery
from atlas_knowledge.retrieval.builder import build_rag_context
from atlas_knowledge.retrieval.retriever import Retriever
from atlas_plugins import PluginRegistry
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from atlas_api.deps import (
    get_model_router,
    get_plugin_registry,
    get_retriever,
    get_session,
    get_settings,
)

router = APIRouter()
log = structlog.get_logger("atlas.api.ws")
prompt_builder = SystemPromptBuilder(prompt_registry)

CONTEXT_WINDOW_TURNS = 20
MAX_TOOL_TURNS = 10


def _to_anthropic_tool(s: ToolSchema) -> dict[str, Any]:
    """Convert a ToolSchema to the Anthropic API tool dict format."""
    return {"name": s.name, "description": s.description, "input_schema": s.parameters}


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(UTC).isoformat()


@router.websocket("/ws/{session_id}")
async def chat_ws(
    websocket: WebSocket,
    session_id: UUID,
    db: AsyncSession = Depends(get_session),
    model_router: ModelRouter = Depends(get_model_router),
    retriever: Retriever = Depends(get_retriever),
    settings: AtlasConfig = Depends(get_settings),
    plugin_registry: PluginRegistry | None = Depends(get_plugin_registry),
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
                    websocket,
                    session_id,
                    req,
                    db,
                    model_router,
                    retriever,
                    settings,
                    plugin_registry,
                    sequence,
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
    retriever: Retriever,
    settings: AtlasConfig,
    plugin_registry: PluginRegistry | None,
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

    # 3b. Optionally retrieve RAG context. Skipped if rag_enabled=false or
    # if the knowledge base for this project has no relevant chunks.
    rag_block: str | None = None
    rag_citations: list[dict] | None = None
    if req.rag_enabled:
        rag_result = await retriever.retrieve(
            RetrievalQuery(
                project_id=project.id,
                text=req.text,
                top_k=req.top_k_context,
            )
        )
        if rag_result.chunks:
            rag_ctx = build_rag_context(rag_result.chunks)
            rag_block = rag_ctx.rendered
            rag_citations = rag_ctx.citations
            sequence = await _send(
                websocket,
                StreamEventType.RAG_CONTEXT,
                {"citations": rag_citations},
                sequence,
            )

    system_prompt = prompt_builder.build(_project_to_pydantic(project))
    messages_for_provider = _assemble_messages(
        system_prompt, history_rows, req.text, rag_block=rag_block
    )

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

    # 6. Build tool list (Anthropic only; registry may be None in tests that don't wire it)
    tools_payload: list[dict[str, Any]] | None = None
    if plugin_registry is not None and provider.spec.provider == "anthropic":
        enabled = list(project.enabled_plugins or [])
        schemas = plugin_registry.get_tool_schemas(enabled=enabled)
        if schemas:
            tools_payload = [_to_anthropic_tool(s) for s in schemas]

    # 7. Stream events — tool-use loop (max MAX_TOOL_TURNS turns)
    assistant_text_parts: list[str] = []
    usage: dict | None = None
    started = time.monotonic()
    tool_turn = 0
    all_tool_calls_across_turns: list[dict[str, Any]] = []
    error_occurred = False

    while True:
        pending_tool_calls: list[dict[str, Any]] = []

        async for event in provider.stream(
            messages=messages_for_provider,
            tools=tools_payload,
            temperature=req.temperature,
        ):
            if event.type == ModelEventType.TOKEN:
                text = event.data.get("text", "")
                assistant_text_parts.append(text)
                sequence = await _send(
                    websocket, StreamEventType.TOKEN, {"token": text}, sequence
                )
            elif event.type == ModelEventType.TOOL_CALL:
                call = event.data  # {"id": ..., "tool": ..., "args": ...}
                sequence = await _send(
                    websocket,
                    StreamEventType.TOOL_CALL,
                    {
                        "tool_name": call["tool"],
                        "call_id": call["id"],
                        "started_at": _now_iso(),
                    },
                    sequence,
                )
                pending_tool_calls.append(call)
            elif event.type == ModelEventType.ERROR:
                sequence = await _send(websocket, StreamEventType.ERROR, event.data, sequence)
                error_occurred = True
                break
            elif event.type == ModelEventType.DONE:
                usage = event.data.get("usage", {})

        if error_occurred:
            break

        # No tool calls this turn — the model responded with text; we're done.
        if not pending_tool_calls:
            break

        # Dispatch each tool call (plugin_registry is always set when tool calls arrive,
        # because tools_payload is only built when registry is not None)
        tool_turn += 1
        tool_results = []
        for call in pending_tool_calls:
            call_started = time.monotonic()
            if plugin_registry is None:
                # Safety: no registry to dispatch to; treat as error
                from atlas_core.models.llm import ToolResult as _ToolResult

                result = _ToolResult(
                    call_id=call["id"],
                    tool=call["tool"],
                    result=None,
                    error="no plugin registry available",
                )
            else:
                result = await plugin_registry.invoke(
                    call["tool"], call["args"], call_id=call["id"]
                )
            duration_ms = int((time.monotonic() - call_started) * 1000)
            ok = result.error is None
            sequence = await _send(
                websocket,
                StreamEventType.TOOL_RESULT,
                {
                    "tool_name": call["tool"],
                    "call_id": call["id"],
                    "ok": ok,
                    "duration_ms": duration_ms,
                },
                sequence,
            )
            tool_results.append(result)
            # Accumulate for persistence on the assistant row
            all_tool_calls_across_turns.append(
                {
                    "call_id": call["id"],
                    "tool": call["tool"],
                    "args": call["args"],
                    "result": result.result if ok else None,
                    "error": result.error,
                }
            )

        # Append assistant tool_use turn + user tool_result turn to the message list
        messages_for_provider.append(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": c["id"],
                        "name": c["tool"],
                        "input": c["args"],
                    }
                    for c in pending_tool_calls
                ],
            }
        )
        messages_for_provider.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": r.call_id,
                        "content": (
                            json.dumps(r.result) if r.error is None else f"Error: {r.error}"
                        ),
                        "is_error": r.error is not None,
                    }
                    for r in tool_results
                ],
            }
        )

        if tool_turn >= MAX_TOOL_TURNS:
            # Force a final non-tool turn: drop tools, add a system instruction.
            tools_payload = None
            messages_for_provider.append(
                {
                    "role": "user",
                    "content": "Tool call limit reached; respond to the user without using tools.",
                }
            )
            # Loop one more time — will yield text only (no tools offered).

    if error_occurred:
        return sequence

    latency_ms = int((time.monotonic() - started) * 1000)
    full_assistant_text = "".join(assistant_text_parts)

    # 8. Persist the assistant turn + usage
    assistant_row = MessageORM(
        user_id=settings.user_id,
        session_id=session_id,
        role=MessageRole.ASSISTANT.value,
        content=full_assistant_text,
        rag_context=rag_citations,
        model=provider.spec.model_id,
        token_count=(usage or {}).get("output_tokens"),
        tool_calls=all_tool_calls_across_turns if all_tool_calls_across_turns else None,
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


async def _load_recent_messages(db: AsyncSession, session_id: UUID, limit: int) -> list[MessageORM]:
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
    *,
    rag_block: str | None = None,
) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system_prompt}]
    if rag_block:
        # Second system message — keeps the persona prompt (cache-friendly,
        # stable across turns) separate from the per-turn retrieved context.
        out.append({"role": "system", "content": rag_block})
    for row in history:
        out.append({"role": row.role, "content": row.content})
    out.append({"role": "user", "content": new_user_text})
    return out


def _project_to_pydantic(row: ProjectORM):
    """Local converter to avoid circular imports — uses the public converter."""
    from atlas_core.db.converters import project_from_orm

    return project_from_orm(row)
