"""Pytest configuration for ATLAS test suites.

Ensures required environment variables are set before any test module is
imported. Necessary because ``atlas_api.main`` constructs ``AtlasConfig()``
at module import time, and ``AtlasConfig`` requires ``ATLAS_DB__DATABASE_URL``.
This conftest keeps ``uv run pytest`` from a fresh clone working without
manually creating a ``.env`` file.
"""

import os

# Set defaults BEFORE pytest collects tests (which imports test modules,
# which import application modules, which build the config object).
os.environ.setdefault("ATLAS_DB__DATABASE_URL", "postgresql://atlas:atlas@localhost:5432/atlas")
os.environ.setdefault("ATLAS_ENVIRONMENT", "development")
