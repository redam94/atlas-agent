"""Integration tests for /api/v1/plugins/* (Plan 1, Phase 3)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas_plugins import (
    CredentialNotFound, FakePlugin, HealthStatus, InMemoryBackend, PluginInfo,
    PluginRegistry, CredentialStore,
)
from atlas_api.deps import get_credential_store, get_plugin_registry
from atlas_api.main import app


@pytest.fixture
def fake_credential_store():
    from cryptography.fernet import Fernet
    return CredentialStore(backend=InMemoryBackend(), master_key=Fernet.generate_key().decode())


@pytest.fixture
def fake_registry(fake_credential_store):
    plugin = FakePlugin(credentials=fake_credential_store)
    reg = PluginRegistry([plugin])
    # warm synchronously: pretend health is ok
    reg._health = {"fake": HealthStatus(ok=True)}
    return reg


@pytest.fixture
def app_with_plugin_overrides(app_client, fake_registry, fake_credential_store):
    app.dependency_overrides[get_plugin_registry] = lambda: fake_registry
    app.dependency_overrides[get_credential_store] = lambda: fake_credential_store
    yield app_client
    app.dependency_overrides.pop(get_plugin_registry, None)
    app.dependency_overrides.pop(get_credential_store, None)


@pytest.mark.asyncio
async def test_list_plugins_returns_fake(app_with_plugin_overrides):
    resp = await app_with_plugin_overrides.get("/api/v1/plugins")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == "fake"
    assert body[0]["tool_count"] == 3
    assert body[0]["health"]["ok"] is True


@pytest.mark.asyncio
async def test_get_schema_returns_three_tools(app_with_plugin_overrides):
    resp = await app_with_plugin_overrides.get("/api/v1/plugins/fake/schema")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    assert {t["name"] for t in body} == {"fake.echo", "fake.fail", "fake.recurse"}


@pytest.mark.asyncio
async def test_get_schema_unknown_plugin_404(app_with_plugin_overrides):
    resp = await app_with_plugin_overrides.get("/api/v1/plugins/unknown/schema")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_invoke_echo_happy_path(app_with_plugin_overrides):
    resp = await app_with_plugin_overrides.post(
        "/api/v1/plugins/fake/invoke",
        json={"tool_name": "fake.echo", "args": {"text": "banana"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tool"] == "fake.echo"
    assert body["result"] == {"echo": "banana"}
    assert body["error"] is None


@pytest.mark.asyncio
async def test_invoke_fail_returns_200_with_error(app_with_plugin_overrides):
    """Tool errors return 200 with ToolResult.error set, not 5xx."""
    resp = await app_with_plugin_overrides.post(
        "/api/v1/plugins/fake/invoke",
        json={"tool_name": "fake.fail", "args": {}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is not None
    assert "forced failure" in body["error"]


@pytest.mark.asyncio
async def test_invoke_unknown_tool_returns_200_with_error(app_with_plugin_overrides):
    resp = await app_with_plugin_overrides.post(
        "/api/v1/plugins/fake/invoke",
        json={"tool_name": "fake.nope", "args": {}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is not None


@pytest.mark.asyncio
async def test_invoke_unknown_plugin_returns_200_with_error(app_with_plugin_overrides):
    resp = await app_with_plugin_overrides.post(
        "/api/v1/plugins/fake/invoke",
        json={"tool_name": "missing.foo", "args": {}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is not None
    assert "unknown plugin" in body["error"]


@pytest.mark.asyncio
async def test_credentials_list_set_delete(app_with_plugin_overrides):
    # Initially empty.
    resp = await app_with_plugin_overrides.get("/api/v1/plugins/fake/credentials")
    assert resp.status_code == 200
    assert resp.json() == []

    # Set.
    resp = await app_with_plugin_overrides.post(
        "/api/v1/plugins/fake/credentials",
        json={"account_id": "alice", "payload": {"foo": "bar"}},
    )
    assert resp.status_code == 201
    assert resp.json() == {"account_id": "alice"}

    # List sees the new account.
    resp = await app_with_plugin_overrides.get("/api/v1/plugins/fake/credentials")
    assert resp.status_code == 200
    assert resp.json() == ["alice"]

    # Delete.
    resp = await app_with_plugin_overrides.delete("/api/v1/plugins/fake/credentials/alice")
    assert resp.status_code == 204

    resp = await app_with_plugin_overrides.get("/api/v1/plugins/fake/credentials")
    assert resp.json() == []


@pytest.mark.asyncio
async def test_credentials_default_account_id(app_with_plugin_overrides):
    resp = await app_with_plugin_overrides.post(
        "/api/v1/plugins/fake/credentials",
        json={"payload": {"foo": "bar"}},   # no account_id
    )
    assert resp.status_code == 201
    assert resp.json() == {"account_id": "default"}


@pytest.mark.asyncio
async def test_credentials_set_in_safe_mode_returns_503(app_client, fake_registry):
    """When CredentialStore is in safe-mode, POST credentials returns 503."""
    safe_store = CredentialStore(backend=InMemoryBackend(), master_key=None)
    app.dependency_overrides[get_plugin_registry] = lambda: fake_registry
    app.dependency_overrides[get_credential_store] = lambda: safe_store
    try:
        resp = await app_client.post(
            "/api/v1/plugins/fake/credentials",
            json={"payload": {"foo": "bar"}},
        )
    finally:
        app.dependency_overrides.pop(get_plugin_registry, None)
        app.dependency_overrides.pop(get_credential_store, None)
    assert resp.status_code == 503
    assert resp.json()["detail"] == "credential_store_unavailable"
