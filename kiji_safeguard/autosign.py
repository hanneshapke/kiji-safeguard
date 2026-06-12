"""Magic import hook: safeguard MCP servers *and* agents with a single import.

Add one line to any MCP server, before or after ``mcp`` is imported::

    import kiji_safeguard.autosign  # noqa: F401

From then on every ``FastMCP.run(...)`` call first extracts the server's
interface (tool names, descriptions, schemas, ...), hashes it and talks to
the registry.

The same import works on the *agent* (client) side: when placed in a process
that connects to MCP servers — directly through ``mcp.ClientSession`` or via
an adapter such as CrewAI's ``MCPServerAdapter`` — every session's
``initialize()`` is followed by listing the server's tools, prompts and
resources, rebuilding the interface from the wire and checking it against
the registry *before any tool reaches the agent*.  The server is identified
by the ``serverInfo.name`` it reports during the handshake, and the hash
matches the one the server computes for itself (see
``signer.extract_interface_from_listing``).  Both hooks can coexist in one
process; whichever module is imported gets patched.

Behaviour is controlled by environment variables:

``KIJI_SAFEGUARD_MODE``
    ``auto`` (default) verifies the live interface against the registry and
    registers it on first sight (trust-on-first-use); a *changed* interface
    is never re-registered, only flagged.  ``verify`` checks strictly
    without ever registering, ``register`` always publishes, ``off``
    disables the hook.
``KIJI_SAFEGUARD_REGISTRY``
    Registry base URL, default ``http://127.0.0.1:8000``.
``KIJI_SAFEGUARD_ENFORCE``
    When truthy (``1``/``true``/``yes``/``on``) a failed verification or an
    unreachable registry aborts startup — or, on the agent side, the
    connection — instead of printing a warning.

All diagnostics go to stderr: stdout belongs to the stdio transport.
"""

from __future__ import annotations

import functools
import importlib.abc
import importlib.machinery
import os
import sys
import warnings
from types import ModuleType
from typing import Any, Callable, Sequence

from .signer import (
    DEFAULT_REGISTRY_URL,
    MCPSigner,
    extract_interface_from_listing,
)

_PATCH_MARKER = "__kiji_safeguard_patched__"


class SafeguardError(RuntimeError):
    """Raised in enforce mode when registration or verification fails."""


def _mode() -> str:
    return os.environ.get("KIJI_SAFEGUARD_MODE", "auto").strip().lower()


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


def _apply_policy(signer: MCPSigner) -> None:
    """Register or verify ``signer``'s interface according to the mode."""
    registry = _registry_url()
    mode = _mode()
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
    elif mode == "auto" and result.code == "unregistered":
        try:
            signer.register(registry)
        except (ConnectionError, ValueError) as exc:
            _fail(f"registration of {signer.name!r} failed: {exc}")
        else:
            _note(
                f"first sight of {signer.name!r} — registered with hash "
                f"{signer.hash} at {registry}"
            )
    else:
        _fail(f"verification of {signer.name!r} failed: {result.reason}")


def _on_run(server: Any) -> None:
    """Server-side hook: register or verify ``server`` before it serves."""
    if _mode() == "off":
        return
    try:
        signer = MCPSigner.from_server(server)
    except (TypeError, ValueError) as exc:
        _fail(f"could not extract MCP interface: {exc}")
        return
    _apply_policy(signer)


async def _drain(method: Callable[..., Any], items_attr: str) -> list[Any]:
    """Collect every item from a paginated ``list_*`` session method."""
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


async def _on_initialize(session: Any, result: Any) -> None:
    """Client-side hook: verify the server an agent just connected to.

    Runs after ``ClientSession.initialize()`` completes, so list requests
    are legal but the caller has not received the session yet.  Every
    (re)connection re-verifies — cheap, and exactly the right semantics.
    """
    if _mode() == "off":
        return

    name = getattr(getattr(result, "serverInfo", None), "name", None)
    try:
        capabilities = result.capabilities
        tools = await _drain(session.list_tools, "tools") if capabilities.tools else []
        prompts = (
            await _drain(session.list_prompts, "prompts") if capabilities.prompts else []
        )
        resources = (
            await _drain(session.list_resources, "resources")
            if capabilities.resources
            else []
        )
        interface = extract_interface_from_listing(
            tools=tools,
            prompts=prompts,
            resources=resources,
            instructions=result.instructions,
        )
        if not name:
            raise ValueError("server reported no serverInfo.name")
        signer = MCPSigner(name=name, interface=interface)
    except SafeguardError:
        raise
    except Exception as exc:  # a misbehaving server must not crash the agent
        _fail(f"could not extract MCP interface from {name or 'server'!r}: {exc}")
        return

    # anyio is a hard dependency of mcp, so it is always importable wherever
    # this hook can fire; importing it lazily keeps kiji-safeguard itself
    # dependency-free.  The thread offload keeps the session's receive loop
    # responsive while the blocking registry HTTP calls run.
    import anyio.to_thread

    await anyio.to_thread.run_sync(functools.partial(_apply_policy, signer))


def _patch_server_module(module: ModuleType) -> None:
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


def _patch_client_module(module: ModuleType) -> None:
    """Wrap ``ClientSession.initialize`` so connections are verified."""
    session_cls = getattr(module, "ClientSession", None)
    if session_cls is None or getattr(session_cls.initialize, _PATCH_MARKER, False):
        return

    original_initialize = session_cls.initialize

    @functools.wraps(original_initialize)
    async def initialize(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = await initialize.__wrapped__(self, *args, **kwargs)
        await _on_initialize(self, result)
        return result

    setattr(initialize, _PATCH_MARKER, True)
    session_cls.initialize = initialize


_TARGETS: dict[str, Callable[[ModuleType], None]] = {
    "mcp.server.fastmcp": _patch_server_module,
    "mcp.client.session": _patch_client_module,
}


class _PatchingLoader(importlib.abc.Loader):
    """Delegate to the real loader, then patch the freshly imported module."""

    def __init__(
        self, wrapped: importlib.abc.Loader, patcher: Callable[[ModuleType], None]
    ) -> None:
        self._wrapped = wrapped
        self._patcher = patcher

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType | None:
        return self._wrapped.create_module(spec)

    def exec_module(self, module: ModuleType) -> None:
        self._wrapped.exec_module(module)
        self._patcher(module)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)


class _AutosignFinder(importlib.abc.MetaPathFinder):
    """Meta-path finder that intercepts the import of the patch targets."""

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        patcher = _TARGETS.get(fullname)
        if patcher is None:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.loader is None:
            return None
        spec.loader = _PatchingLoader(spec.loader, patcher)
        return spec


def install() -> None:
    """Install the import hook (idempotent).

    Targets that were imported already are patched in place; the meta-path
    finder patches the rest the moment they are imported.
    """
    for target, patcher in _TARGETS.items():
        module = sys.modules.get(target)
        if module is not None:
            patcher(module)
    if not any(isinstance(finder, _AutosignFinder) for finder in sys.meta_path):
        sys.meta_path.insert(0, _AutosignFinder())


def uninstall() -> None:
    """Remove the meta-path finder (an already patched class stays patched)."""
    sys.meta_path[:] = [
        finder for finder in sys.meta_path if not isinstance(finder, _AutosignFinder)
    ]


install()
