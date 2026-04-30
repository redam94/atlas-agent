"""AtlasPlugin ABC and supporting models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from atlas_core.models.base import AtlasModel
from atlas_core.models.llm import ToolSchema

if TYPE_CHECKING:
    from atlas_plugins.credentials import CredentialStore


class HealthStatus(AtlasModel):
    """Result of a plugin health check."""

    ok: bool
    detail: str | None = None


class PluginInfo(AtlasModel):
    """One row in GET /api/v1/plugins."""

    name: str
    description: str
    tool_count: int
    health: HealthStatus


class AtlasPlugin(ABC):
    """Plugins implement this. Two required overrides: get_tools and invoke."""

    name: str = ""  # set on the subclass; "fake", "github", etc.
    description: str = ""

    def __init__(self, credentials: CredentialStore) -> None:
        if not self.name:
            raise ValueError(f"{self.__class__.__name__}.name must be set")
        self._credentials = credentials

    async def _get_credentials(self, account_id: str = "default") -> dict[str, Any]:
        """Lazy fetch — called per-invoke so credential rotations take effect immediately.

        Raises ``CredentialNotFound`` if no row exists.
        """
        return await self._credentials.get(self.name, account_id)

    @abstractmethod
    def get_tools(self) -> list[ToolSchema]:
        """Return the tool schemas this plugin exposes.

        Each ToolSchema.name MUST start with f"{self.name}." and ToolSchema.plugin
        MUST equal self.name.
        """

    @abstractmethod
    async def invoke(self, tool_name: str, args: dict[str, Any]) -> Any:
        """Execute the tool. Return the result value. Raise on failure.

        The PluginRegistry catches exceptions and converts to a ToolResult with
        ``error=str(e)``; the plugin doesn't construct a ToolResult itself.
        """

    async def health(self) -> HealthStatus:
        """Default: ok if at least one credential row exists."""
        accounts = await self._credentials.list(self.name)
        if not accounts:
            return HealthStatus(ok=False, detail="no credentials registered")
        return HealthStatus(ok=True)
