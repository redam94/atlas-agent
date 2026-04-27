"""GET /api/v1/models — list registered LLM providers."""
from atlas_core.models.llm import ModelSpec
from atlas_core.providers.registry import ModelRegistry
from fastapi import APIRouter, Depends

from atlas_api.deps import get_model_registry

router = APIRouter(tags=["models"])


@router.get("/models", response_model=list[ModelSpec])
async def list_models(
    registry: ModelRegistry = Depends(get_model_registry),
) -> list[ModelSpec]:
    return registry.specs()
