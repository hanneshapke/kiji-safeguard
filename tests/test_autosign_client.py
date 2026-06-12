"""Tests for the agent/client side of the autosign import hook."""

from __future__ import annotations

import importlib
import sys

import anyio
import mcp.types as types
import pytest
from mcp.server.lowlevel import Server
from mcp.shared.memory import create_connected_server_and_client_session

import kiji_safeguard.autosign as autosign
from kiji_safeguard import (
    MCPSigner,
    aggregate_hash,
    canonical_json,
    extract_interface_from_listing,
)
from tests.conftest import make_server


def _connect(lowlevel_server) -> None:
    """Open (and immediately close) a client session against ``lowlevel_server``.

    The in-memory helper calls ``ClientSession.initialize()`` itself, so the
    patched hook fires during connection.
    """

    async def run() -> None:
        async with create_connected_server_and_client_session(lowlevel_server):
            pass

    anyio.run(run)


def _contains_safeguard_error(exc: BaseException) -> bool:
    """True when ``exc`` is (or an exception group contains) a SafeguardError."""
    if isinstance(exc, autosign.SafeguardError):
        return True
    return any(
        _contains_safeguard_error(sub) for sub in getattr(exc, "exceptions", ())
    )


def make_tools_only_server() -> Server:
    """A lowlevel server that advertises only the tools capability."""
    server = Server("only-tools")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="ping",
                description="Ping.",
                inputSchema={"type": "object", "properties": {}},
            )
        ]

    return server


# --- hook installation -----------------------------------------------------


def test_already_imported_client_session_is_patched():
    autosign.install()
    from mcp.client.session import ClientSession

    assert getattr(ClientSession.initialize, autosign._PATCH_MARKER, False)


def test_import_hook_patches_fresh_client_import(monkeypatch):
    saved = {name: mod for name, mod in sys.modules.items() if name.startswith("mcp")}
    for name in saved:
        monkeypatch.delitem(sys.modules, name)
    autosign.uninstall()
    autosign.install()
    try:
        module = importlib.import_module("mcp.client.session")
        assert getattr(module.ClientSession.initialize, autosign._PATCH_MARKER, False)
    finally:
        autosign.uninstall()
        for name in [n for n in sys.modules if n.startswith("mcp")]:
            del sys.modules[name]
        sys.modules.update(saved)
        autosign.install()


def test_client_patch_is_idempotent():
    autosign.install()
    autosign.install()
    finders = [f for f in sys.meta_path if isinstance(f, autosign._AutosignFinder)]
    assert len(finders) <= 1
    from mcp.client.session import ClientSession

    inner = ClientSession.initialize.__wrapped__
    assert not getattr(inner, autosign._PATCH_MARKER, False)


# --- pagination helper -----------------------------------------------------


def test_drain_follows_cursors():
    class Page:
        def __init__(self, tools, nextCursor=None):
            self.tools = tools
            self.nextCursor = nextCursor

    calls = []

    async def list_tools(cursor=None):
        calls.append(cursor)
        if cursor is None:
            return Page(["a", "b"], nextCursor="page2")
        return Page(["c"])

    items = anyio.run(autosign._drain, list_tools, "tools")
    assert items == ["a", "b", "c"]
    assert calls == [None, "page2"]


# --- hash parity (the contract between the two extractors) ------------------


def test_wire_interface_matches_server_side_hash(monkeypatch):
    monkeypatch.setenv("KIJI_SAFEGUARD_MODE", "off")
    autosign.install()
    server = make_server(full=True)
    expected = MCPSigner.from_server(server)

    captured: dict[str, object] = {}
    real_on_initialize = autosign._on_initialize

    async def capture(session, result):
        captured["result"] = result
        await real_on_initialize(session, result)

    monkeypatch.setattr(autosign, "_on_initialize", capture)

    async def run():
        async with create_connected_server_and_client_session(
            server._mcp_server
        ) as session:
            return (
                await autosign._drain(session.list_tools, "tools"),
                await autosign._drain(session.list_prompts, "prompts"),
                await autosign._drain(session.list_resources, "resources"),
            )

    tools, prompts, resources = anyio.run(run)
    init_result = captured["result"]

    assert init_result.serverInfo.name == server.name
    interface = extract_interface_from_listing(
        tools=tools,
        prompts=prompts,
        resources=resources,
        instructions=init_result.instructions,
    )
    assert sorted(canonical_json(c) for c in interface) == sorted(
        canonical_json(c) for c in expected.interface
    )
    assert aggregate_hash(interface) == expected.hash


# --- behaviour against a live registry --------------------------------------


def test_connect_registers_server(live_registry, monkeypatch):
    monkeypatch.setenv("KIJI_SAFEGUARD_MODE", "register")
    monkeypatch.setenv("KIJI_SAFEGUARD_REGISTRY", live_registry)
    autosign.install()

    server = make_server(full=True)
    _connect(server._mcp_server)

    assert MCPSigner.from_server(server).verify(live_registry)


def test_connect_verifies_registered_server(live_registry, monkeypatch, capsys):
    server = make_server(full=True)
    MCPSigner.from_server(server).register(live_registry)

    monkeypatch.setenv("KIJI_SAFEGUARD_MODE", "verify")
    monkeypatch.setenv("KIJI_SAFEGUARD_REGISTRY", live_registry)
    autosign.install()

    _connect(server._mcp_server)
    assert "verified 'demo-server'" in capsys.readouterr().err


def test_connect_auto_mode_registers_on_first_sight(live_registry, monkeypatch, capsys):
    monkeypatch.delenv("KIJI_SAFEGUARD_MODE", raising=False)
    monkeypatch.setenv("KIJI_SAFEGUARD_REGISTRY", live_registry)
    autosign.install()

    server = make_server()
    _connect(server._mcp_server)
    assert "first sight of 'demo-server'" in capsys.readouterr().err

    _connect(server._mcp_server)
    assert "verified 'demo-server'" in capsys.readouterr().err


def test_connect_warns_on_changed_interface(live_registry, monkeypatch, capsys):
    MCPSigner.from_server(make_server()).register(live_registry)

    monkeypatch.setenv("KIJI_SAFEGUARD_MODE", "verify")
    monkeypatch.setenv("KIJI_SAFEGUARD_REGISTRY", live_registry)
    autosign.install()

    _connect(make_server(extra_tool=True)._mcp_server)
    err = capsys.readouterr().err
    assert "WARNING" in err and "interface changed" in err


def test_connect_enforce_aborts_connection(live_registry, monkeypatch):
    monkeypatch.setenv("KIJI_SAFEGUARD_MODE", "verify")
    monkeypatch.setenv("KIJI_SAFEGUARD_REGISTRY", live_registry)
    monkeypatch.setenv("KIJI_SAFEGUARD_ENFORCE", "1")
    autosign.install()

    # The task group inside the in-memory helper may wrap the error in an
    # exception group, so unwrap before asserting.
    with pytest.raises(BaseException) as excinfo:
        _connect(make_server(name="never-registered")._mcp_server)
    assert _contains_safeguard_error(excinfo.value)


def test_connect_off_mode_does_nothing(monkeypatch, capsys):
    monkeypatch.setenv("KIJI_SAFEGUARD_MODE", "off")
    monkeypatch.setenv("KIJI_SAFEGUARD_REGISTRY", "http://127.0.0.1:1")
    autosign.install()

    _connect(make_server()._mcp_server)
    assert "[kiji-safeguard]" not in capsys.readouterr().err


def test_connect_skips_unadvertised_capabilities(live_registry, monkeypatch, capsys):
    monkeypatch.delenv("KIJI_SAFEGUARD_MODE", raising=False)
    monkeypatch.setenv("KIJI_SAFEGUARD_REGISTRY", live_registry)
    autosign.install()

    _connect(make_tools_only_server())
    assert "first sight of 'only-tools'" in capsys.readouterr().err
