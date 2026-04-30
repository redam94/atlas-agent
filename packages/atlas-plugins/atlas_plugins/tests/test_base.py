"""Tests for AtlasPlugin ABC."""

from unittest.mock import AsyncMock

import pytest

from atlas_core.models.llm import ToolSchema
from atlas_plugins import AtlasPlugin, HealthStatus


class _StubPlugin(AtlasPlugin):
    name = "stub"
    description = "test stub"

    def get_tools(self) -> list[ToolSchema]:
        return [
            ToolSchema(
                name="stub.echo",
                description="echo",
                parameters={},
                plugin="stub",
            )
        ]

    async def invoke(self, tool_name, args):
        return {"echo": args.get("text")}


def test_subclass_constructs_with_credential_store():
    cred = AsyncMock()
    plugin = _StubPlugin(credentials=cred)
    assert plugin.name == "stub"


def test_subclass_without_name_raises():
    class _NoName(AtlasPlugin):
        # name not set
        def get_tools(self):
            return []

        async def invoke(self, tool_name, args):
            return None

    cred = AsyncMock()
    with pytest.raises(ValueError, match="name must be set"):
        _NoName(credentials=cred)


@pytest.mark.asyncio
async def test_get_credentials_passes_through_to_store():
    cred = AsyncMock()
    cred.get.return_value = {"foo": "bar"}
    plugin = _StubPlugin(credentials=cred)

    result = await plugin._get_credentials(account_id="alice")

    cred.get.assert_awaited_once_with("stub", "alice")
    assert result == {"foo": "bar"}


@pytest.mark.asyncio
async def test_default_health_ok_when_credentials_exist():
    cred = AsyncMock()
    cred.list.return_value = ["default", "alice"]
    plugin = _StubPlugin(credentials=cred)

    health = await plugin.health()

    assert health.ok is True
    cred.list.assert_awaited_once_with("stub")


@pytest.mark.asyncio
async def test_default_health_degraded_when_no_credentials():
    cred = AsyncMock()
    cred.list.return_value = []
    plugin = _StubPlugin(credentials=cred)

    health = await plugin.health()

    assert health.ok is False
    assert "no credentials" in (health.detail or "")
