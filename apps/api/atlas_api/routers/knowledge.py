"""Knowledge layer REST endpoints.

POST   /api/v1/knowledge/ingest          Upload markdown text
POST   /api/v1/knowledge/ingest/pdf      Upload a PDF (multipart)
GET    /api/v1/knowledge/jobs/{id}       Ingestion job status
GET    /api/v1/knowledge/nodes           List nodes for a project
DELETE /api/v1/knowledge/nodes/{id}      Delete node + chunks
GET    /api/v1/knowledge/search          Debug RAG search
"""

from __future__ import annotations

from uuid import UUID

from atlas_core.config import AtlasConfig
from atlas_core.db.converters import (
    ingestion_job_from_orm,
    knowledge_node_from_orm,
)
from atlas_core.db.orm import IngestionJobORM, KnowledgeNodeORM
from atlas_knowledge.ingestion.service import IngestionService
from atlas_knowledge.models.ingestion import (
    IngestionJob,
    IngestRequest,
    SourceType,
)
from atlas_knowledge.models.nodes import KnowledgeNode
from atlas_knowledge.models.retrieval import RetrievalQuery, RetrievalResult
from atlas_knowledge.parsers.markdown import parse_markdown
from atlas_knowledge.parsers.pdf import parse_pdf
from atlas_knowledge.retrieval.retriever import Retriever
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atlas_api.deps import (
    get_ingestion_service,
    get_retriever,
    get_session,
    get_settings,
)

router = APIRouter(tags=["knowledge"])


# --- Ingestion -----------------------------------------------------------


@router.post("/knowledge/ingest", response_model=IngestionJob, status_code=202)
async def ingest_endpoint(
    payload: IngestRequest,
    db: AsyncSession = Depends(get_session),
    service: IngestionService = Depends(get_ingestion_service),
    settings: AtlasConfig = Depends(get_settings),
) -> IngestionJob:
    """Markdown ingest. PDF goes through ``ingest_pdf_endpoint``."""
    if payload.source_type is not SourceType.MARKDOWN:
        raise HTTPException(
            status_code=400,
            detail="source_type=markdown for this endpoint; use multipart upload for PDFs",
        )
    parsed = parse_markdown(payload.text or "", title=None)
    job_id = await service.ingest(
        db=db,
        user_id=settings.user_id,
        project_id=payload.project_id,
        parsed=parsed,
        source_type="markdown",
        source_filename=payload.source_filename,
    )
    job_row = await db.get(IngestionJobORM, job_id)
    if job_row is None:
        raise HTTPException(status_code=500, detail="ingest created no job row")
    return ingestion_job_from_orm(job_row)


@router.post("/knowledge/ingest/pdf", response_model=IngestionJob, status_code=202)
async def ingest_pdf_endpoint(
    project_id: UUID = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_session),
    service: IngestionService = Depends(get_ingestion_service),
    settings: AtlasConfig = Depends(get_settings),
) -> IngestionJob:
    data = await file.read()
    parsed = parse_pdf(data, source_filename=file.filename)
    job_id = await service.ingest(
        db=db,
        user_id=settings.user_id,
        project_id=project_id,
        parsed=parsed,
        source_type="pdf",
        source_filename=file.filename,
    )
    job_row = await db.get(IngestionJobORM, job_id)
    if job_row is None:
        raise HTTPException(status_code=500, detail="ingest created no job row")
    return ingestion_job_from_orm(job_row)


@router.get("/knowledge/jobs/{job_id}", response_model=IngestionJob)
async def get_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_session),
) -> IngestionJob:
    row = await db.get(IngestionJobORM, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="ingestion job not found")
    return ingestion_job_from_orm(row)


# --- Nodes ---------------------------------------------------------------


@router.get("/knowledge/nodes", response_model=list[KnowledgeNode])
async def list_nodes(
    project_id: UUID,
    db: AsyncSession = Depends(get_session),
) -> list[KnowledgeNode]:
    result = await db.execute(
        select(KnowledgeNodeORM).where(KnowledgeNodeORM.project_id == project_id)
    )
    return [knowledge_node_from_orm(r) for r in result.scalars().all()]


@router.delete("/knowledge/nodes/{node_id}", status_code=204)
async def delete_node(
    node_id: UUID,
    db: AsyncSession = Depends(get_session),
) -> None:
    row = await db.get(KnowledgeNodeORM, node_id)
    if row is None:
        raise HTTPException(status_code=404, detail="node not found")
    await db.delete(row)
    await db.flush()


# --- Search (debug) ------------------------------------------------------


@router.get("/knowledge/search", response_model=RetrievalResult)
async def search(
    project_id: UUID,
    query: str,
    top_k: int = 8,
    retriever: Retriever = Depends(get_retriever),
) -> RetrievalResult:
    return await retriever.retrieve(RetrievalQuery(project_id=project_id, text=query, top_k=top_k))
