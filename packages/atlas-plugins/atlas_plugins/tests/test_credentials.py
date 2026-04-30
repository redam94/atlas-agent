"""Tests for CredentialStore (Fernet + in-memory backing for unit tests)."""

import pytest
from cryptography.fernet import Fernet

from atlas_plugins import CredentialDecryptError, CredentialNotFound
from atlas_plugins.credentials import CredentialStore, InMemoryBackend


@pytest.fixture
def store():
    backend = InMemoryBackend()
    return CredentialStore(backend=backend, master_key=Fernet.generate_key().decode())


@pytest.fixture
def safe_mode_store():
    backend = InMemoryBackend()
    return CredentialStore(backend=backend, master_key=None)


@pytest.mark.asyncio
async def test_set_and_get_round_trip(store):
    payload = {"token": "abc123", "scope": ["read", "write"]}
    await store.set("github", "default", payload)
    got = await store.get("github", "default")
    assert got == payload


@pytest.mark.asyncio
async def test_get_missing_raises_credential_not_found(store):
    with pytest.raises(CredentialNotFound):
        await store.get("github", "default")


@pytest.mark.asyncio
async def test_set_upsert_overwrites_payload(store):
    await store.set("github", "default", {"token": "old"})
    await store.set("github", "default", {"token": "new"})
    got = await store.get("github", "default")
    assert got == {"token": "new"}


@pytest.mark.asyncio
async def test_list_returns_account_ids_only(store):
    await store.set("gmail", "alice@example.com", {"refresh": "a"})
    await store.set("gmail", "bob@example.com", {"refresh": "b"})
    await store.set("github", "default", {"token": "t"})

    accounts = await store.list("gmail")
    assert sorted(accounts) == ["alice@example.com", "bob@example.com"]
    # No plaintext crosses this method — we only assert on account_ids.


@pytest.mark.asyncio
async def test_delete_removes_row(store):
    await store.set("github", "default", {"token": "t"})
    await store.delete("github", "default")
    with pytest.raises(CredentialNotFound):
        await store.get("github", "default")


@pytest.mark.asyncio
async def test_delete_missing_is_idempotent(store):
    # Should not raise.
    await store.delete("github", "default")


@pytest.mark.asyncio
async def test_decrypt_with_wrong_key_raises(store):
    payload = {"token": "secret"}
    await store.set("github", "default", payload)

    other_store = CredentialStore(
        backend=store._backend,  # same backing data
        master_key=Fernet.generate_key().decode(),  # different key
    )
    with pytest.raises(CredentialDecryptError):
        await other_store.get("github", "default")


@pytest.mark.asyncio
async def test_safe_mode_set_noops(safe_mode_store):
    await safe_mode_store.set("github", "default", {"token": "t"})
    # No exception, but also no readable data.
    with pytest.raises(CredentialNotFound):
        await safe_mode_store.get("github", "default")


@pytest.mark.asyncio
async def test_safe_mode_list_returns_empty(safe_mode_store):
    accounts = await safe_mode_store.list("github")
    assert accounts == []


@pytest.mark.asyncio
async def test_safe_mode_get_raises_credential_not_found(safe_mode_store):
    with pytest.raises(CredentialNotFound):
        await safe_mode_store.get("github", "default")
