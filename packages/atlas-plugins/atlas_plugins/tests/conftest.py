"""Shared fixtures for atlas-plugins tests."""

import pytest


@pytest.fixture
def fernet_key() -> str:
    """A deterministic Fernet key for tests. NOT for production."""
    # Fernet keys are 32 url-safe base64 bytes.
    return "VGhpcy1pcy1hLXRlc3Qta2V5LXdpdGgtMzItYnl0ZXM="
