"""Knowledge layer REST endpoints.

POST   /api/v1/knowledge/ingest          Upload markdown text
POST   /api/v1/knowledge/ingest/pdf      Upload a PDF (multipart)
POST   /api/v1/knowledge/ingest/url      Ingest a URL (Playwright + Trafilatura)
GET    /api/v1/knowledge/jobs/{id}       Ingestion job status
GET    /api/v1/knowledge/nodes           List nodes for a project
DELETE /api/v1/knowledge/nodes/{id}      Delete node + chunks
GET    /api/v1/knowledge/search          Debug RAG search
GET    /api/v1/knowledge/graph           Subgraph for explorer visualization
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from atlas_core.config import AtlasConfig
from atlas_core.db.converters import (
    ingestion_job_from_orm,
    knowledge_node_from_orm,
)
from atlas_core.db.orm import IngestionJobORM, KnowledgeNodeORM, ProjectORM
from atlas_knowledge.ingestion.service import IngestionService
from atlas_knowledge.models.graph import (
    GraphEdge,
    GraphMeta,
    GraphNode,
    GraphResponse,
)
from atlas_knowledge.models.ingestion import (
    IngestionJob,
    IngestRequest,
    SourceType,
    UrlIngestRequest,
)
from atlas_knowledge.models.nodes import KnowledgeNode
from atlas_knowledge.models.retrieval import RetrievalQuery, RetrievalResult
from atlas_knowledge.parsers.markdown import parse_markdown
from atlas_knowledge.parsers.pdf import parse_pdf
from atlas_knowledge.parsers.url import parse_url, validate_url
from atlas_knowledge.retrieval.retriever import Retriever
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atlas_api.deps import (
    get_graph_store,
    get_ingestion_service,
    get_retriever,
    get_session,
    get_settings,
)
from atlas_graph import GraphStore

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
    if await db.get(ProjectORM, payload.project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
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
    if await db.get(ProjectORM, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
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


@router.post("/knowledge/ingest/url", response_model=IngestionJob, status_code=202)
async def ingest_url_endpoint(
    payload: UrlIngestRequest,
    db: AsyncSession = Depends(get_session),
    service: IngestionService = Depends(get_ingestion_service),
    settings: AtlasConfig = Depends(get_settings),
) -> IngestionJob:
    if await db.get(ProjectORM, payload.project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    url = str(payload.url)
    try:
        # validate_url does a blocking socket.getaddrinfo; run in a worker
        # thread so the FastAPI event loop isn't stalled during DNS resolution.
        await asyncio.to_thread(validate_url, url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        parsed = await parse_url(url)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=f"could not extract content: {e}") from e
    except Exception as e:  # noqa: BLE001 — Playwright errors are varied
        raise HTTPException(status_code=502, detail=f"fetch failed: {e}") from e
    job_id = await service.ingest(
        db=db,
        user_id=settings.user_id,
        project_id=payload.project_id,
        parsed=parsed,
        source_type="url",
        source_filename=url,
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


# --- Graph (explorer) ----

def _to_graph_node(raw: dict) -> GraphNode:
    return GraphNode(
        id=UUID(raw["id"]),
        type=raw["type"],
        label=raw["label"] or "",
        pagerank=raw.get("pagerank"),
        metadata=raw.get("metadata") or {},
    )


def _to_graph_edge(raw: dict) -> GraphEdge:
    return GraphEdge(
        id=raw["id"],
        source=UUID(raw["source"]),
        target=UUID(raw["target"]),
        type=raw["type"],
    )


@router.get("/knowledge/graph", response_model=GraphResponse)
async def get_knowledge_graph(
    project_id: UUID,
    q: str | None = None,
    seed_chunk_ids: str | None = None,
    seed_node_ids: str | None = None,
    node_types: str | None = None,
    limit: int | None = None,
    db: AsyncSession = Depends(get_session),
    graph_store: GraphStore = Depends(get_graph_store),
    retriever: Retriever = Depends(get_retriever),
) -> GraphResponse:
    """Return a subgraph of the project's knowledge graph for visualization.

    Modes (priority: q > seed_node_ids > seed_chunk_ids > none):
      - search: q is set → run hybrid retriever, expand chunk hits 1-hop.
      - expand: seed_*_ids set → 1-hop expansion of those seeds.
      - top_entities: none of the above → top-N entities by PageRank.
    """
    project = await db.get(ProjectORM, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    valid_types = {"Document", "Chunk", "Entity"}
    types_filter: set[str] | None = None
    if node_types:
        types_filter = {t.strip() for t in node_types.split(",") if t.strip()}
        unknown = types_filter - valid_types
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"unknown node_types: {sorted(unknown)}",
            )

    # Mode discrimination — top_entities only for Task 4.
    if q is None and not seed_node_ids and not seed_chunk_ids:
        cap = limit if limit is not None else 30
        cap = min(cap, 200)
        nodes_raw, edges_raw = await graph_store.fetch_top_entities(
            project_id=project_id, limit=cap
        )
        truncated = len(nodes_raw) >= cap
        nodes = [_to_graph_node(n) for n in nodes_raw]
        if types_filter:
            nodes = [n for n in nodes if n.type in types_filter]
            kept = {n.id for n in nodes}
            edges = [_to_graph_edge(e) for e in edges_raw if UUID(e["source"]) in kept and UUID(e["target"]) in kept]
        else:
            edges = [_to_graph_edge(e) for e in edges_raw]
        return GraphResponse(
            nodes=nodes,
            edges=edges,
            meta=GraphMeta(mode="top_entities", truncated=truncated),
        )

    # search and expand modes implemented in subsequent tasks.
    raise HTTPException(status_code=501, detail="not implemented yet")
