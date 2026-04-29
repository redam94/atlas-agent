"""Notes REST endpoints (Plan 6).

POST   /api/v1/notes              Create a draft note (no ingestion).
GET    /api/v1/notes              List notes for a project.
GET    /api/v1/notes/{id}         Get a note.
PATCH  /api/v1/notes/{id}         Update title/body/mentions (no ingestion).
DELETE /api/v1/notes/{id}         Delete + cleanup chunks across all stores.
POST   /api/v1/notes/{id}/index   Run the heavy ingestion pipeline.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from atlas_core.db.orm import IngestionJobORM, NoteORM, ProjectORM
from atlas_core.models.notes import (
    CreateNoteRequest,
    Note,
    NoteListItem,
    PatchNoteRequest,
)
from atlas_graph import GraphStore
from atlas_knowledge.ingestion.service import IngestionService
from atlas_knowledge.models.ingestion import IngestionJob
from atlas_knowledge.parsers.markdown import parse_markdown
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atlas_api.deps import get_graph_store, get_ingestion_service, get_session

router = APIRouter(tags=["notes"])


def _note_from_orm(row: NoteORM) -> Note:
    return Note(
        id=row.id,
        user_id=row.user_id,
        project_id=row.project_id,
        knowledge_node_id=row.knowledge_node_id,
        title=row.title,
        body_markdown=row.body_markdown,
        mention_entity_ids=list(row.mention_entity_ids or []),
        indexed_at=row.indexed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post("/notes", response_model=Note, status_code=201)
async def create_note(
    payload: CreateNoteRequest,
    db: AsyncSession = Depends(get_session),
) -> Note:
    project = await db.get(ProjectORM, payload.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    row = NoteORM(
        user_id=project.user_id,
        project_id=payload.project_id,
        title=payload.title,
        body_markdown=payload.body_markdown,
    )
    db.add(row)
    await db.flush()
    return _note_from_orm(row)


@router.get("/notes", response_model=list[NoteListItem])
async def list_notes(
    project_id: UUID,
    db: AsyncSession = Depends(get_session),
) -> list[NoteListItem]:
    result = await db.execute(
        select(NoteORM)
        .where(NoteORM.project_id == project_id)
        .order_by(NoteORM.updated_at.desc())
    )
    return [
        NoteListItem(
            id=r.id, title=r.title, updated_at=r.updated_at, indexed_at=r.indexed_at
        )
        for r in result.scalars().all()
    ]


@router.get("/notes/{note_id}", response_model=Note)
async def get_note(
    note_id: UUID,
    db: AsyncSession = Depends(get_session),
) -> Note:
    row = await db.get(NoteORM, note_id)
    if row is None:
        raise HTTPException(status_code=404, detail="note not found")
    return _note_from_orm(row)


@router.patch("/notes/{note_id}", response_model=Note)
async def patch_note(
    note_id: UUID,
    payload: PatchNoteRequest,
    db: AsyncSession = Depends(get_session),
) -> Note:
    row = await db.get(NoteORM, note_id)
    if row is None:
        raise HTTPException(status_code=404, detail="note not found")
    if payload.title is not None:
        row.title = payload.title
    if payload.body_markdown is not None:
        row.body_markdown = payload.body_markdown
    if payload.mention_entity_ids is not None:
        row.mention_entity_ids = list(payload.mention_entity_ids)
    row.updated_at = datetime.now(UTC)
    await db.flush()
    return _note_from_orm(row)


@router.delete("/notes/{note_id}", status_code=204)
async def delete_note(
    note_id: UUID,
    db: AsyncSession = Depends(get_session),
    service: IngestionService = Depends(get_ingestion_service),
) -> None:
    row = await db.get(NoteORM, note_id)
    if row is None:
        raise HTTPException(status_code=404, detail="note not found")
    if row.knowledge_node_id is not None:
        await service.cleanup_document(
            db=db, project_id=row.project_id, document_id=row.knowledge_node_id
        )
    await db.delete(row)
    await db.flush()


@router.post("/notes/{note_id}/index", response_model=IngestionJob)
async def index_note(
    note_id: UUID,
    db: AsyncSession = Depends(get_session),
    service: IngestionService = Depends(get_ingestion_service),
    graph_store: GraphStore = Depends(get_graph_store),
) -> IngestionJob:
    """Run the full ingestion pipeline (chunker + embedder + NER + graph) on the note's body."""
    note = await db.get(NoteORM, note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="note not found")

    if note.knowledge_node_id is not None:
        await service.cleanup_document(
            db=db, project_id=note.project_id, document_id=note.knowledge_node_id
        )

    parsed = parse_markdown(note.body_markdown, title=note.title)
    result = await service.ingest(
        db=db,
        user_id=note.user_id,
        project_id=note.project_id,
        parsed=parsed,
        source_type="note",
        source_filename=None,
    )

    if result.document_id is not None and note.mention_entity_ids:
        await graph_store.tag_note(
            note_id=result.document_id,
            entity_ids=list(note.mention_entity_ids),
        )

    note.knowledge_node_id = result.document_id
    note.indexed_at = datetime.now(UTC)
    await db.flush()

    job_row = await db.get(IngestionJobORM, result.job_id)
    if job_row is None:
        raise HTTPException(status_code=500, detail="ingest produced no job row")
    from atlas_core.db.converters import ingestion_job_from_orm
    return ingestion_job_from_orm(job_row)
