"""ATLAS plugin framework."""

from atlas_plugins._fake import FakePlugin
from atlas_plugins.base import AtlasPlugin, HealthStatus, PluginInfo
from atlas_plugins.credentials import CredentialBackend, CredentialStore, InMemoryBackend
from atlas_plugins.errors import CredentialDecryptError, CredentialNotFound
from atlas_plugins.registry import PluginRegistry, REGISTERED_PLUGINS

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
]
