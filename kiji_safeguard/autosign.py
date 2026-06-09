"""Magic import hook: safeguard FastMCP servers with a single import.

Add one line to any MCP server, before or after ``mcp`` is imported::

    import kiji_safeguard.autosign  # noqa: F401

From then on every ``FastMCP.run(...)`` call first extracts the server's
interface (tool names, descriptions, schemas, ...), hashes it and talks to
the registry.  Behaviour is controlled by environment variables:

``KIJI_SAFEGUARD_MODE``
    ``verify`` (default) checks the live interface against the registry,
    ``register`` publishes it, ``off`` disables the hook.
``KIJI_SAFEGUARD_REGISTRY``
    Registry base URL, default ``http://127.0.0.1:8000``.
``KIJI_SAFEGUARD_ENFORCE``
    When truthy (``1``/``true``/``yes``/``on``) a failed verification or an
    unreachable registry aborts startup instead of printing a warning.

All diagnostics go to stderr: stdout belongs to the stdio transport.
"""

from __future__ import annotations

import functools
import importlib.abc
import importlib.machinery
import os
import sys
from types import ModuleType
from typing import Any, Sequence

from .signer import DEFAULT_REGISTRY_URL, MCPSigner

_TARGET = "mcp.server.fastmcp"
_PATCH_MARKER = "__kiji_safeguard_patched__"


class SafeguardError(RuntimeError):
    """Raised in enforce mode when registration or verification fails."""


def _mode() -> str:
    return os.environ.get("KIJI_SAFEGUARD_MODE", "verify").strip().lower()


def _registry_url() -> str:
    return os.environ.get("KIJI_SAFEGUARD_REGISTRY", DEFAULT_REGISTRY_URL)


def _enforce() -> bool:
    return os.environ.get("KIJI_SAFEGUARD_ENFORCE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _note(message: str) -> None:
    print(f"[kiji-safeguard] {message}", file=sys.stderr)


def _fail(message: str) -> None:
    if _enforce():
        raise SafeguardError(message)
    _note(f"WARNING: {message}")


def _on_run(server: Any) -> None:
    """Register or verify ``server`` according to the environment."""
    mode = _mode()
    if mode == "off":
        return

    try:
        signer = MCPSigner.from_server(server)
    except (TypeError, ValueError) as exc:
        _fail(f"could not extract MCP interface: {exc}")
        return

    registry = _registry_url()
    if mode == "register":
        try:
            signer.register(registry)
        except (ConnectionError, ValueError) as exc:
            _fail(f"registration of {signer.name!r} failed: {exc}")
        else:
            _note(f"registered {signer.name!r} with hash {signer.hash} at {registry}")
        return

    try:
        result = signer.verify(registry)
    except ConnectionError as exc:
        _fail(f"verification of {signer.name!r} failed: {exc}")
        return
    if result:
        _note(f"verified {signer.name!r} (hash {signer.hash})")
    else:
        _fail(f"verification of {signer.name!r} failed: {result.reason}")


def _patch_module(module: ModuleType) -> None:
    """Wrap ``FastMCP.run`` so the safeguard fires before the server starts."""
    fastmcp_cls = getattr(module, "FastMCP", None)
    if fastmcp_cls is None or getattr(fastmcp_cls.run, _PATCH_MARKER, False):
        return

    original_run = fastmcp_cls.run

    @functools.wraps(original_run)
    def run(self: Any, *args: Any, **kwargs: Any) -> Any:
        _on_run(self)
        return run.__wrapped__(self, *args, **kwargs)

    setattr(run, _PATCH_MARKER, True)
    fastmcp_cls.run = run


class _PatchingLoader(importlib.abc.Loader):
    """Delegate to the real loader, then patch the freshly imported module."""

    def __init__(self, wrapped: importlib.abc.Loader) -> None:
        self._wrapped = wrapped

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType | None:
        return self._wrapped.create_module(spec)

    def exec_module(self, module: ModuleType) -> None:
        self._wrapped.exec_module(module)
        _patch_module(module)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)


class _FastMCPFinder(importlib.abc.MetaPathFinder):
    """Meta-path finder that intercepts the import of ``mcp.server.fastmcp``."""

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname != _TARGET:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.loader is None:
            return None
        spec.loader = _PatchingLoader(spec.loader)
        return spec


def install() -> None:
    """Install the import hook (idempotent).

    If ``mcp.server.fastmcp`` was imported already it is patched in place;
    otherwise a meta-path finder patches it the moment it is imported.
    """
    module = sys.modules.get(_TARGET)
    if module is not None:
        _patch_module(module)
        return
    if not any(isinstance(finder, _FastMCPFinder) for finder in sys.meta_path):
        sys.meta_path.insert(0, _FastMCPFinder())


def uninstall() -> None:
    """Remove the meta-path finder (an already patched class stays patched)."""
    sys.meta_path[:] = [
        finder for finder in sys.meta_path if not isinstance(finder, _FastMCPFinder)
    ]


install()
