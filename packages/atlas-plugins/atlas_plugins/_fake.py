"""FakePlugin — exercises the framework end-to-end without external API calls.

Three tools:
- fake.echo  : happy-path tool, returns {"echo": text}
- fake.fail  : always raises; tests the registry's exception → ToolResult error path
- fake.recurse: returns {"recurse_again": True, "depth": N+1}; the chat-handler test
                  mocks Anthropic to keep calling this until the 10-turn cap fires.
"""

from __future__ import annotations

from typing import Any

from atlas_core.models.llm import ToolSchema

from atlas_plugins.base import AtlasPlugin


class FakePlugin(AtlasPlugin):
    name = "fake"
    description = "Test plugin used to exercise the framework end-to-end."

    def get_tools(self) -> list[ToolSchema]:
        return [
            ToolSchema(
                name="fake.echo",
                description="Echo the given text back as {echo: text}.",
                parameters={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                plugin="fake",
            ),
            ToolSchema(
                name="fake.fail",
                description="Always raises 'forced failure'. Tests the error path.",
                parameters={"type": "object", "properties": {}},
                plugin="fake",
            ),
            ToolSchema(
                name="fake.recurse",
                description=(
                    "Return {recurse_again: true, depth: N+1}. Used to drive the "
                    "tool-use loop cap in tests."
                ),
                parameters={
                    "type": "object",
                    "properties": {"depth": {"type": "integer", "default": 0}},
                },
                plugin="fake",
            ),
        ]

    async def invoke(self, tool_name: str, args: dict[str, Any]) -> Any:
        if tool_name == "fake.echo":
            return {"echo": args.get("text", "")}
        if tool_name == "fake.fail":
            raise RuntimeError("forced failure")
        if tool_name == "fake.recurse":
            depth = int(args.get("depth", 0))
            return {"recurse_again": True, "depth": depth + 1}
        raise ValueError(f"unknown tool {tool_name!r}")
