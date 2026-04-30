"""Tests for FakePlugin."""

from unittest.mock import AsyncMock

import pytest

from atlas_plugins import FakePlugin


@pytest.fixture
def plugin():
    cred = AsyncMock()
    cred.list.return_value = ["default"]
    return FakePlugin(credentials=cred)


def test_get_tools_returns_three_tools(plugin):
    tools = plugin.get_tools()
    names = sorted(t.name for t in tools)
    assert names == ["fake.echo", "fake.fail", "fake.recurse"]
    for t in tools:
        assert t.plugin == "fake"


def test_tool_schemas_match_plugin_namespace(plugin):
    for tool in plugin.get_tools():
        assert tool.name.startswith("fake.")


@pytest.mark.asyncio
async def test_echo_returns_echo_dict(plugin):
    result = await plugin.invoke("fake.echo", {"text": "banana"})
    assert result == {"echo": "banana"}


@pytest.mark.asyncio
async def test_fail_raises_runtime_error(plugin):
    with pytest.raises(RuntimeError, match="forced failure"):
        await plugin.invoke("fake.fail", {})


@pytest.mark.asyncio
async def test_recurse_returns_incremented_depth(plugin):
    result = await plugin.invoke("fake.recurse", {"depth": 3})
    assert result["recurse_again"] is True
    assert result["depth"] == 4


@pytest.mark.asyncio
async def test_recurse_default_depth_zero(plugin):
    result = await plugin.invoke("fake.recurse", {})
    assert result["depth"] == 1


@pytest.mark.asyncio
async def test_unknown_tool_raises(plugin):
    with pytest.raises(ValueError, match="unknown tool"):
        await plugin.invoke("fake.nope", {})
