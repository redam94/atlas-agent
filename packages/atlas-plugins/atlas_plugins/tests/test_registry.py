"""Tests for PluginRegistry."""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from atlas_plugins import AtlasPlugin, HealthStatus
from atlas_plugins.registry import PluginRegistry
from atlas_core.models.llm import ToolSchema


def _make_plugin(name: str, tools: list[ToolSchema], invoke_value: Any = None,
                 invoke_raises: Exception | None = None,
                 health_value: HealthStatus | None = None,
                 health_raises: Exception | None = None) -> AtlasPlugin:
    # Use exec + class definition to capture locals properly
    class_dict = {
        "name": name,
        "description": f"test plugin {name}",
    }

    def _get_tools_impl(self):
        return tools

    async def _invoke_impl(self, tool_name, args):
        if invoke_raises:
            raise invoke_raises
        return invoke_value

    async def _health_impl(self):
        if health_raises:
            raise health_raises
        return health_value or HealthStatus(ok=True)

    class_dict["get_tools"] = _get_tools_impl
    class_dict["invoke"] = _invoke_impl
    class_dict["health"] = _health_impl

    _P = type("_P", (AtlasPlugin,), class_dict)

    cred = AsyncMock()
    cred.list.return_value = ["default"]
    return _P(credentials=cred)


def test_construction_with_two_well_formed_plugins():
    p1 = _make_plugin("a", [ToolSchema(name="a.t1", description="", parameters={}, plugin="a")])
    p2 = _make_plugin("b", [ToolSchema(name="b.t1", description="", parameters={}, plugin="b")])
    reg = PluginRegistry([p1, p2])
    names = [p.name for p in reg.list()]
    assert sorted(names) == ["a", "b"]


def test_tool_name_must_match_plugin_namespace():
    p = _make_plugin("a", [ToolSchema(name="not_a_namespace.t1", description="", parameters={}, plugin="a")])
    with pytest.raises(ValueError, match="does not match plugin"):
        PluginRegistry([p])


def test_duplicate_tool_name_raises():
    p1 = _make_plugin("a", [ToolSchema(name="a.t1", description="", parameters={}, plugin="a")])
    # Different plugins (b and c) each declaring the same tool name "a.t1" (should fail namespace check)
    # But let's test the duplicate check: both b and c declare their own namespace tools "b.t1" initially
    p2 = _make_plugin("b", [ToolSchema(name="b.t1", description="", parameters={}, plugin="b")])
    # Now force the duplicate scenario by having two different plugin instances that declare the same tool
    # within their own namespaces—that shouldn't happen. Instead, test by having plugin 'c' reuse 'a.t1':
    # But tool must match namespace. Actually, per the spec comment: "different plugins declaring the same tool name"
    # Example: both plugin a and plugin b expose tool "shared.tool" — but they can't because tool names
    # must match their plugin namespace. So the only way to get duplicate tool names is:
    # plugin a declares ["a.x", "shared.foo"] (which fails namespace check)
    # OR we need two different plugin instances with same name 'a'
    p3a = _make_plugin("a", [ToolSchema(name="a.x", description="", parameters={}, plugin="a")])
    p3b = _make_plugin("a", [ToolSchema(name="a.x", description="", parameters={}, plugin="a")])
    # Same plugin name would collide in the dict, so they get deduplicated.
    # Let's test it differently: use a single plugin that declares the tool twice in its schema:
    p_dup = _make_plugin("x", [
        ToolSchema(name="x.t1", description="", parameters={}, plugin="x"),
        ToolSchema(name="x.t1", description="", parameters={}, plugin="x"),  # Duplicate
    ])
    with pytest.raises(ValueError, match="duplicate tool name"):
        PluginRegistry([p_dup])


@pytest.mark.asyncio
async def test_warm_runs_health_for_each_plugin():
    p1 = _make_plugin("a", [ToolSchema(name="a.t1", description="", parameters={}, plugin="a")],
                     health_value=HealthStatus(ok=True))
    p2 = _make_plugin("b", [ToolSchema(name="b.t1", description="", parameters={}, plugin="b")],
                     health_value=HealthStatus(ok=False, detail="creds missing"))
    reg = PluginRegistry([p1, p2])
    await reg.warm()
    infos = {info.name: info for info in reg.list()}
    assert infos["a"].health.ok is True
    assert infos["b"].health.ok is False
    assert "creds missing" in (infos["b"].health.detail or "")


@pytest.mark.asyncio
async def test_warm_health_failure_does_not_break_others():
    p1 = _make_plugin("a", [ToolSchema(name="a.t1", description="", parameters={}, plugin="a")],
                     health_raises=RuntimeError("boom"))
    p2 = _make_plugin("b", [ToolSchema(name="b.t1", description="", parameters={}, plugin="b")],
                     health_value=HealthStatus(ok=True))
    reg = PluginRegistry([p1, p2])
    await reg.warm()
    infos = {info.name: info for info in reg.list()}
    assert infos["a"].health.ok is False
    assert "boom" in (infos["a"].health.detail or "")
    assert infos["b"].health.ok is True


@pytest.mark.asyncio
async def test_get_tool_schemas_returns_tools_when_healthy():
    schema = ToolSchema(name="a.t1", description="", parameters={}, plugin="a")
    p = _make_plugin("a", [schema])
    reg = PluginRegistry([p])
    await reg.warm()
    out = reg.get_tool_schemas(enabled=["a"])
    assert out == [schema]


@pytest.mark.asyncio
async def test_get_tool_schemas_skips_degraded_plugins():
    schema = ToolSchema(name="a.t1", description="", parameters={}, plugin="a")
    p = _make_plugin("a", [schema], health_value=HealthStatus(ok=False, detail="x"))
    reg = PluginRegistry([p])
    await reg.warm()
    out = reg.get_tool_schemas(enabled=["a"])
    assert out == []


def test_get_tool_schemas_silently_skips_unknown_plugin():
    p = _make_plugin("a", [ToolSchema(name="a.t1", description="", parameters={}, plugin="a")])
    reg = PluginRegistry([p])
    out = reg.get_tool_schemas(enabled=["unknown"])
    assert out == []


@pytest.mark.asyncio
async def test_invoke_happy_path_returns_tool_result_with_result():
    p = _make_plugin("a", [ToolSchema(name="a.echo", description="", parameters={}, plugin="a")],
                     invoke_value={"echo": "hi"})
    reg = PluginRegistry([p])
    result = await reg.invoke("a.echo", {"text": "hi"}, call_id="call_1")
    assert result.call_id == "call_1"
    assert result.tool == "a.echo"
    assert result.result == {"echo": "hi"}
    assert result.error is None


@pytest.mark.asyncio
async def test_invoke_unknown_plugin_returns_error_tool_result():
    reg = PluginRegistry([])
    result = await reg.invoke("missing.foo", {}, call_id="call_1")
    assert result.call_id == "call_1"
    assert result.tool == "missing.foo"
    assert result.result is None
    assert result.error is not None
    assert "unknown plugin" in result.error


@pytest.mark.asyncio
async def test_invoke_plugin_raise_is_caught_into_tool_result():
    p = _make_plugin("a", [ToolSchema(name="a.fail", description="", parameters={}, plugin="a")],
                     invoke_raises=RuntimeError("forced"))
    reg = PluginRegistry([p])
    result = await reg.invoke("a.fail", {}, call_id="call_1")
    assert result.error is not None
    assert "forced" in result.error
    assert result.result is None
