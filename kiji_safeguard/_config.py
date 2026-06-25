"""Shared runtime primitives for the safeguard hooks and the proxy.

These helpers read the ``KIJI_SAFEGUARD_*`` environment variables and emit
diagnostics.  They live here -- rather than in :mod:`kiji_safeguard.autosign`
-- so the proxy can reuse them *without* importing ``autosign``, whose import
installs the global ``ClientSession``/``FastMCP`` patches.  The proxy speaks to
its upstream through its own ``ClientSession`` and must not have that session
silently re-verified by the client hook, so it depends on this module instead.

Importing this module has no side effects.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Callable

from .signer import DEFAULT_REGISTRY_URL


class SafeguardError(RuntimeError):
    """Raised in enforce mode when registration or verification fails."""


def mode() -> str:
    return os.environ.get("KIJI_SAFEGUARD_MODE", "auto").strip().lower()


def registry_url() -> str:
    return os.environ.get("KIJI_SAFEGUARD_REGISTRY", DEFAULT_REGISTRY_URL)


def enforce() -> bool:
    return os.environ.get("KIJI_SAFEGUARD_ENFORCE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def approval_timeout() -> float:
    try:
        return float(os.environ.get("KIJI_SAFEGUARD_APPROVAL_TIMEOUT", "1800"))
    except ValueError:
        return 1800.0


def approval_interval() -> float:
    try:
        return float(os.environ.get("KIJI_SAFEGUARD_APPROVAL_POLL_INTERVAL", "3"))
    except ValueError:
        return 3.0


def note(message: str) -> None:
    """Emit a diagnostic to stderr -- stdout belongs to the stdio transport."""
    print(f"[kiji-safeguard] {message}", file=sys.stderr)


def fail(message: str) -> None:
    """Raise in enforce mode, otherwise warn -- the shared failure policy."""
    if enforce():
        raise SafeguardError(message)
    note(f"WARNING: {message}")


async def drain(method: Callable[..., Any], items_attr: str) -> list[Any]:
    """Collect every item from a paginated ``list_*`` session method."""
    import warnings

    items: list[Any] = []
    cursor: str | None = None
    while True:
        with warnings.catch_warnings():
            # mcp >= 1.27 deprecates the ``cursor`` kwarg in favour of
            # ``params=``; it still works everywhere we support.
            warnings.simplefilter("ignore", DeprecationWarning)
            result = await (method(cursor=cursor) if cursor else method())
        items.extend(getattr(result, items_attr))
        cursor = getattr(result, "nextCursor", None)
        if not cursor:
            return items
