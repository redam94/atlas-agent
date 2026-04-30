"""Encrypted credential store backing for atlas-plugins.

Storage backend is pluggable via the ``CredentialBackend`` Protocol so tests
can use an in-memory dict; the production binding (Task 7) wires the
SQLAlchemy backend that talks to ``plugin_credentials``.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

import structlog
from cryptography.fernet import Fernet, InvalidToken

from atlas_plugins.errors import CredentialDecryptError, CredentialNotFound

log = structlog.get_logger("atlas.plugins.credentials")


class CredentialBackend(Protocol):
    """Async storage interface for the encrypted credential store."""

    async def upsert(self, plugin_name: str, account_id: str, ciphertext: bytes) -> None: ...
    async def fetch(self, plugin_name: str, account_id: str) -> bytes | None: ...
    async def list_accounts(self, plugin_name: str) -> list[str]: ...
    async def remove(self, plugin_name: str, account_id: str) -> None: ...


class InMemoryBackend:
    """In-memory backend for tests. Production uses the SQLAlchemy backend."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], bytes] = {}

    async def upsert(self, plugin_name: str, account_id: str, ciphertext: bytes) -> None:
        self._data[(plugin_name, account_id)] = ciphertext

    async def fetch(self, plugin_name: str, account_id: str) -> bytes | None:
        return self._data.get((plugin_name, account_id))

    async def list_accounts(self, plugin_name: str) -> list[str]:
        return [aid for (pname, aid) in self._data.keys() if pname == plugin_name]

    async def remove(self, plugin_name: str, account_id: str) -> None:
        self._data.pop((plugin_name, account_id), None)


class CredentialStore:
    """Fernet-encrypted credential storage with safe-mode for missing keys.

    With ``master_key=None`` the store enters safe-mode: ``set`` no-ops with a
    WARN log per call, ``get`` raises ``CredentialNotFound``, ``list`` returns
    ``[]``, ``delete`` no-ops. This lets local dev boot without secrets.
    """

    def __init__(self, *, backend: CredentialBackend, master_key: str | None) -> None:
        self._backend = backend
        self._master_key = master_key
        self._fernet: Fernet | None = Fernet(master_key.encode()) if master_key else None

    @property
    def safe_mode(self) -> bool:
        return self._fernet is None

    async def set(
        self, plugin_name: str, account_id: str, payload: dict[str, Any]
    ) -> None:
        if self._fernet is None:
            log.warning(
                "plugins.credentials.set_in_safe_mode",
                plugin=plugin_name, account_id=account_id,
            )
            return
        ciphertext = self._fernet.encrypt(json.dumps(payload).encode())
        await self._backend.upsert(plugin_name, account_id, ciphertext)

    async def get(self, plugin_name: str, account_id: str) -> dict[str, Any]:
        if self._fernet is None:
            raise CredentialNotFound(f"credential store in safe mode")
        ciphertext = await self._backend.fetch(plugin_name, account_id)
        if ciphertext is None:
            raise CredentialNotFound(
                f"no credentials for plugin={plugin_name!r} account_id={account_id!r}"
            )
        try:
            plaintext = self._fernet.decrypt(ciphertext)
        except InvalidToken as e:
            raise CredentialDecryptError(
                f"failed to decrypt credentials for plugin={plugin_name!r} "
                f"account_id={account_id!r}: master key mismatch or tampering"
            ) from e
        return json.loads(plaintext)

    async def list(self, plugin_name: str) -> list[str]:
        if self._fernet is None:
            return []
        return await self._backend.list_accounts(plugin_name)

    async def delete(self, plugin_name: str, account_id: str) -> None:
        if self._fernet is None:
            log.warning(
                "plugins.credentials.delete_in_safe_mode",
                plugin=plugin_name, account_id=account_id,
            )
            return
        await self._backend.remove(plugin_name, account_id)
