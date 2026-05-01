"""Contextvar that signals whether the current agent call is interactive.

Interactive = originating from the WebSocket chat handler (React UI).
Non-interactive = originating from the internal Discord HTTP endpoint.

Plugins read this to decide whether to enforce confirmation gates.
"""

import contextvars

_INTERACTIVE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "atlas_interactive", default=True
)


def is_interactive() -> bool:
    """Return True if the current call is from an interactive session."""
    return _INTERACTIVE.get()


def set_interactive(v: bool) -> contextvars.Token[bool]:
    """Set the interactive flag; returns a token for reset."""
    return _INTERACTIVE.set(v)


def reset_interactive(token: contextvars.Token[bool]) -> None:
    """Reset the interactive flag using the token from set_interactive."""
    _INTERACTIVE.reset(token)
