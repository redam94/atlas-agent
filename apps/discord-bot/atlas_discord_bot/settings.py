"""Bot settings — fail fast on missing required env vars."""

from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings


class BotSettings(BaseSettings):
    model_config = {"env_prefix": "ATLAS_DISCORD__", "case_sensitive": False}

    bot_token: str = Field(..., description="Discord bot token")
    guild_id: int = Field(..., description="Discord guild (server) ID for slash commands")
    internal_secret: str = Field(..., description="Shared secret for API↔bot HTTP calls")
    api_base_url: str = Field(default="http://api:8000", description="ATLAS API base URL")
    default_project_id: UUID = Field(..., description="Default project ID for all commands")
    notify_channel_id: str | None = Field(
        default=None, description="Fallback channel for ingestion-complete notifications"
    )
    internal_app_port: int = Field(default=8001, description="Port for the bot's internal FastAPI app")
