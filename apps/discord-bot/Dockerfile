FROM python:3.13-slim AS base
WORKDIR /app
RUN pip install uv

FROM base AS deps
COPY pyproject.toml uv.lock ./
COPY packages/atlas-core packages/atlas-core
COPY apps/discord-bot/pyproject.toml apps/discord-bot/pyproject.toml
RUN uv sync --frozen --no-dev --package atlas-discord-bot

FROM deps AS runtime
COPY apps/discord-bot/atlas_discord_bot apps/discord-bot/atlas_discord_bot
RUN uv sync --frozen --no-dev --package atlas-discord-bot
WORKDIR /app/apps/discord-bot
CMD ["uv", "run", "python", "-m", "atlas_discord_bot"]
