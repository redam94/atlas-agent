"""/api/v1/plugins/* — plugin framework REST surface (Plan 1, Phase 3)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from atlas_core.models.base import AtlasModel
from atlas_core.models.llm import ToolResult, ToolSchema
from atlas_plugins import CredentialStore, PluginInfo, PluginRegistry
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from atlas_api.deps import get_credential_store, get_plugin_registry

router = APIRouter(tags=["plugins"])


class InvokeRequest(AtlasModel):
    model_config = {"strict": False}
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)


class CredentialSetRequest(AtlasModel):
    model_config = {"strict": False}
    account_id: str = "default"
    payload: dict[str, Any]


class CredentialSetResponse(AtlasModel):
    account_id: str


@router.get("/plugins", response_model=list[PluginInfo])
async def list_plugins(
    registry: PluginRegistry = Depends(get_plugin_registry),
) -> list[PluginInfo]:
    return registry.list()


@router.get("/plugins/{name}/schema", response_model=list[ToolSchema])
async def get_plugin_schema(
    name: str,
    registry: PluginRegistry = Depends(get_plugin_registry),
) -> list[ToolSchema]:
    plugin = registry.get(name)
    if plugin is None:
        raise HTTPException(status_code=404, detail="plugin not found")
    return plugin.get_tools()


@router.post("/plugins/{name}/invoke", response_model=ToolResult)
async def invoke_plugin(
    name: str,
    payload: InvokeRequest,
    registry: PluginRegistry = Depends(get_plugin_registry),
) -> ToolResult:
    """Direct tool invocation. Tool errors live in the response, not as 5xx."""
    return await registry.invoke(
        payload.tool_name, payload.args, call_id=f"manual_{uuid4().hex[:8]}"
    )


@router.get("/plugins/{name}/credentials", response_model=list[str])
async def list_plugin_credentials(
    name: str,
    store: CredentialStore = Depends(get_credential_store),
    registry: PluginRegistry = Depends(get_plugin_registry),
) -> list[str]:
    if registry.get(name) is None:
        raise HTTPException(status_code=404, detail="plugin not found")
    return await store.list(name)


@router.post(
    "/plugins/{name}/credentials",
    response_model=CredentialSetResponse,
    status_code=201,
)
async def set_plugin_credential(
    name: str,
    payload: CredentialSetRequest,
    store: CredentialStore = Depends(get_credential_store),
    registry: PluginRegistry = Depends(get_plugin_registry),
) -> CredentialSetResponse:
    if registry.get(name) is None:
        raise HTTPException(status_code=404, detail="plugin not found")
    if store.safe_mode:
        raise HTTPException(status_code=503, detail="credential_store_unavailable")
    await store.set(name, payload.account_id, payload.payload)
    return CredentialSetResponse(account_id=payload.account_id)


@router.delete("/plugins/{name}/credentials/{account_id}", status_code=204)
async def delete_plugin_credential(
    name: str,
    account_id: str,
    store: CredentialStore = Depends(get_credential_store),
    registry: PluginRegistry = Depends(get_plugin_registry),
) -> None:
    if registry.get(name) is None:
        raise HTTPException(status_code=404, detail="plugin not found")
    await store.delete(name, account_id)
