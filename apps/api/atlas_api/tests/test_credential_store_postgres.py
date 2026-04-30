"""Postgres-backed CredentialStore tests (use apps/api's db_session fixture)."""

import pytest
from atlas_plugins import CredentialNotFound, CredentialStore, SqlAlchemyBackend
from cryptography.fernet import Fernet


@pytest.mark.asyncio
async def test_sqlalchemy_backend_round_trip(db_session):
    backend = SqlAlchemyBackend(session_factory=lambda: db_session)
    store = CredentialStore(backend=backend, master_key=Fernet.generate_key().decode())

    await store.set("github", "default", {"token": "abc"})
    got = await store.get("github", "default")
    assert got == {"token": "abc"}


@pytest.mark.asyncio
async def test_sqlalchemy_backend_upsert_overwrites(db_session):
    backend = SqlAlchemyBackend(session_factory=lambda: db_session)
    store = CredentialStore(backend=backend, master_key=Fernet.generate_key().decode())

    await store.set("github", "default", {"token": "old"})
    await store.set("github", "default", {"token": "new"})
    got = await store.get("github", "default")
    assert got == {"token": "new"}


@pytest.mark.asyncio
async def test_sqlalchemy_backend_list_returns_account_ids(db_session):
    backend = SqlAlchemyBackend(session_factory=lambda: db_session)
    store = CredentialStore(backend=backend, master_key=Fernet.generate_key().decode())

    await store.set("gmail", "alice@example.com", {"refresh": "a"})
    await store.set("gmail", "bob@example.com", {"refresh": "b"})

    accounts = await store.list("gmail")
    assert sorted(accounts) == ["alice@example.com", "bob@example.com"]


@pytest.mark.asyncio
async def test_sqlalchemy_backend_delete(db_session):
    backend = SqlAlchemyBackend(session_factory=lambda: db_session)
    store = CredentialStore(backend=backend, master_key=Fernet.generate_key().decode())

    await store.set("github", "default", {"token": "t"})
    await store.delete("github", "default")
    with pytest.raises(CredentialNotFound):
        await store.get("github", "default")
