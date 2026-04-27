"""REST endpoints for /projects.

Single-user-aware: every query filters by the configured user_id from
AtlasConfig. Plan 2 has no auth — the user_id is hardcoded in config.
"""

from uuid import UUID

from atlas_core.config import AtlasConfig
from atlas_core.db.converters import project_from_orm
from atlas_core.db.orm import ProjectORM
from atlas_core.models.projects import (
    Project,
    ProjectCreate,
    ProjectStatus,
    ProjectUpdate,
)
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atlas_api.deps import get_session, get_settings

router = APIRouter(tags=["projects"])


@router.get("/projects", response_model=list[Project])
async def list_projects(
    session: AsyncSession = Depends(get_session),
    settings: AtlasConfig = Depends(get_settings),
) -> list[Project]:
    result = await session.execute(
        select(ProjectORM)
        .where(ProjectORM.user_id == settings.user_id)
        .order_by(ProjectORM.created_at.desc())
    )
    return [project_from_orm(row) for row in result.scalars().all()]


@router.post(
    "/projects",
    response_model=Project,
    status_code=status.HTTP_201_CREATED,
)
async def create_project(
    payload: ProjectCreate,
    session: AsyncSession = Depends(get_session),
    settings: AtlasConfig = Depends(get_settings),
) -> Project:
    row = ProjectORM(
        user_id=settings.user_id,
        name=payload.name,
        description=payload.description,
        privacy_level=payload.privacy_level.value,
        default_model=payload.default_model,
        enabled_plugins=payload.enabled_plugins,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return project_from_orm(row)


@router.get("/projects/{project_id}", response_model=Project)
async def get_project(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
    settings: AtlasConfig = Depends(get_settings),
) -> Project:
    row = await session.get(ProjectORM, project_id)
    if row is None or row.user_id != settings.user_id:
        raise HTTPException(status_code=404, detail="Project not found")
    return project_from_orm(row)


@router.patch("/projects/{project_id}", response_model=Project)
async def update_project(
    project_id: UUID,
    payload: ProjectUpdate,
    session: AsyncSession = Depends(get_session),
    settings: AtlasConfig = Depends(get_settings),
) -> Project:
    row = await session.get(ProjectORM, project_id)
    if row is None or row.user_id != settings.user_id:
        raise HTTPException(status_code=404, detail="Project not found")

    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        # Enum fields stored as their string value in Postgres
        if hasattr(value, "value"):
            value = value.value
        setattr(row, field, value)

    await session.flush()
    await session.refresh(row)
    return project_from_orm(row)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
    settings: AtlasConfig = Depends(get_settings),
) -> None:
    """Soft delete: set status='archived'. Hard delete is not exposed via REST."""
    row = await session.get(ProjectORM, project_id)
    if row is None or row.user_id != settings.user_id:
        raise HTTPException(status_code=404, detail="Project not found")
    row.status = ProjectStatus.ARCHIVED.value
    await session.flush()
