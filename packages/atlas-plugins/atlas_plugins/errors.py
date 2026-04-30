"""Plugin-framework exceptions."""


class CredentialNotFound(Exception):
    """Raised when CredentialStore.get cannot find the (plugin_name, account_id) row."""


class CredentialDecryptError(Exception):
    """Raised when Fernet decrypt fails (wrong master key, ciphertext tampering)."""
