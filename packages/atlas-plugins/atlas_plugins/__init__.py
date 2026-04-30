"""ATLAS plugin framework."""

from atlas_plugins._fake import FakePlugin
from atlas_plugins.base import AtlasPlugin, HealthStatus, PluginInfo
from atlas_plugins.credentials import (
    CredentialBackend,
    CredentialStore,
    InMemoryBackend,
    SqlAlchemyBackend,
)
from atlas_plugins.errors import CredentialDecryptError, CredentialNotFound
from atlas_plugins.registry import REGISTERED_PLUGINS, PluginRegistry

__all__ = [
    "AtlasPlugin",
    "CredentialBackend",
    "CredentialDecryptError",
    "CredentialNotFound",
    "CredentialStore",
    "FakePlugin",
    "HealthStatus",
    "InMemoryBackend",
    "PluginInfo",
    "PluginRegistry",
    "REGISTERED_PLUGINS",
    "SqlAlchemyBackend",
]
