"""PluginRegistry: load + dispatch + health-cache for AtlasPlugin instances."""

from __future__ import annotations

from typing import Any

import structlog

from atlas_core.models.llm import ToolResult, ToolSchema
from atlas_plugins.base import AtlasPlugin, HealthStatus, PluginInfo

log = structlog.get_logger("atlas.plugins.registry")


# Future plans append their plugin classes here:
#   from atlas_plugins.registry import REGISTERED_PLUGINS
#   REGISTERED_PLUGINS.append(DiscordPlugin)
from atlas_plugins._fake import FakePlugin

REGISTERED_PLUGINS: list[type[AtlasPlugin]] = [FakePlugin]


class PluginRegistry:
    """Holds constructed plugin instances; dispatches tool invocations."""

    def __init__(self, plugins: list[AtlasPlugin]) -> None:
        self._plugins: dict[str, AtlasPlugin] = {p.name: p for p in plugins}
        self._health: dict[str, HealthStatus] = {}
        self._validate_namespace_and_uniqueness(plugins)

    @staticmethod
    def _validate_namespace_and_uniqueness(plugins: list[AtlasPlugin]) -> None:
        seen: set[str] = set()
        for p in plugins:
            for t in p.get_tools():
                if not t.name.startswith(f"{p.name}."):
                    raise ValueError(
                        f"tool {t.name!r} does not match plugin namespace {p.name!r}"
                    )
                if t.name in seen:
                    raise ValueError(f"duplicate tool name across plugins: {t.name!r}")
                seen.add(t.name)

    async def warm(self) -> None:
        """Run health checks on all plugins; results cached in self._health."""
        for name, plugin in self._plugins.items():
            try:
                self._health[name] = await plugin.health()
            except Exception as e:
                log.warning("plugins.health_failed", plugin=name, error=str(e))
                self._health[name] = HealthStatus(ok=False, detail=str(e))

    def list(self) -> list[PluginInfo]:
        return [
            PluginInfo(
                name=p.name,
                description=p.description,
                tool_count=len(p.get_tools()),
                health=self._health.get(p.name) or HealthStatus(ok=False, detail="not warmed"),
            )
            for p in self._plugins.values()
        ]

    def get(self, plugin_name: str) -> AtlasPlugin | None:
        return self._plugins.get(plugin_name)

    def get_tool_schemas(self, *, enabled: list[str]) -> list[ToolSchema]:
        out: list[ToolSchema] = []
        for name in enabled:
            plugin = self._plugins.get(name)
            if plugin is None:
                continue
            health = self._health.get(name) or HealthStatus(ok=False)
            if not health.ok:
                continue
            out.extend(plugin.get_tools())
        return out

    async def invoke(
        self, tool_name: str, args: dict[str, Any], *, call_id: str
    ) -> ToolResult:
        plugin_name = tool_name.partition(".")[0]
        plugin = self._plugins.get(plugin_name)
        if plugin is None:
            return ToolResult(
                call_id=call_id, tool=tool_name, result=None,
                error=f"unknown plugin: {plugin_name}",
            )
        try:
            value = await plugin.invoke(tool_name, args)
            return ToolResult(call_id=call_id, tool=tool_name, result=value, error=None)
        except Exception as e:
            log.warning("plugins.invoke_failed", tool=tool_name, error=str(e))
            return ToolResult(
                call_id=call_id, tool=tool_name, result=None, error=str(e)
            )
