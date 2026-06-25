"""Tests for the verifying MCP proxy.

Three layers:

* ``guard_interface`` -- the verdict logic, against a live registry.
* ``decide_serving`` -- how a verdict + policy maps to forward / tripwire / fail.
* end-to-end -- a downstream client connected to the proxy, whose upstream is an
  in-memory FastMCP server (and, once, a real subprocess via the stdio CLI).
"""

from __future__ import annotations

import sys
from pathlib import Path

import anyio
import pytest
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.session import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session as connect

import kiji_safeguard.proxy as proxy
from kiji_safeguard import MCPSigner
from kiji_safeguard._config import SafeguardError
from tests.conftest import make_server, resolve_pending

import threading


# --- guard_interface -------------------------------------------------------


def _snapshot(server) -> proxy.UpstreamSnapshot:
    """Build a snapshot straight from a FastMCP server (no wire round-trip)."""
    signer = MCPSigner.from_server(server)
    return proxy.UpstreamSnapshot(
        name=signer.name,
        instructions=getattr(server, "instructions", None),
        capabilities=None,
        tools=[],
        prompts=[],
        resources=[],
    ), signer


def test_guard_off_mode_allows(monkeypatch):
    monkeypatch.setenv("KIJI_SAFEGUARD_MODE", "off")
    _, signer = _snapshot(make_server())
    decision = proxy.guard_interface(signer)
    assert decision and decision.code == "off"


def test_guard_verifies_registered(live_registry, monkeypatch):
    server = make_server()
    MCPSigner.from_server(server).register(live_registry)
    monkeypatch.setenv("KIJI_SAFEGUARD_MODE", "verify")
    monkeypatch.setenv("KIJI_SAFEGUARD_REGISTRY", live_registry)
    _, signer = _snapshot(server)
    decision = proxy.guard_interface(signer)
    assert decision and decision.code == "ok"


def test_guard_auto_registers_on_first_sight(live_registry, monkeypatch):
    monkeypatch.delenv("KIJI_SAFEGUARD_MODE", raising=False)
    monkeypatch.setenv("KIJI_SAFEGUARD_REGISTRY", live_registry)
    _, signer = _snapshot(make_server())
    decision = proxy.guard_interface(signer)
    assert decision and decision.code == "ok"
    # second sight verifies the now-registered interface
    assert proxy.guard_interface(signer).code == "ok"


def test_guard_verify_blocks_unregistered(live_registry, monkeypatch):
    monkeypatch.setenv("KIJI_SAFEGUARD_MODE", "verify")
    monkeypatch.setenv("KIJI_SAFEGUARD_REGISTRY", live_registry)
    _, signer = _snapshot(make_server(name="never-registered"))
    decision = proxy.guard_interface(signer)
    assert not decision and decision.code == "unregistered"


def test_guard_blocks_changed_interface_with_diff(live_registry, monkeypatch):
    MCPSigner.from_server(make_server()).register(live_registry)
    monkeypatch.setenv("KIJI_SAFEGUARD_MODE", "verify")
    monkeypatch.setenv("KIJI_SAFEGUARD_REGISTRY", live_registry)
    _, signer = _snapshot(make_server(extra_tool=True))
    decision = proxy.guard_interface(signer)
    assert not decision and decision.code == "changed"
    assert decision.diff and "sneaky" in decision.diff


def test_guard_expected_name_mismatch_blocks(monkeypatch):
    monkeypatch.setenv("KIJI_SAFEGUARD_MODE", "verify")
    _, signer = _snapshot(make_server(name="impostor"))
    decision = proxy.guard_interface(signer, expected_name="stock-prices")
    assert not decision and decision.code == "renamed"


def test_guard_registry_unreachable_is_error(monkeypatch):
    monkeypatch.setenv("KIJI_SAFEGUARD_MODE", "verify")
    monkeypatch.setenv("KIJI_SAFEGUARD_REGISTRY", "http://127.0.0.1:1")
    _, signer = _snapshot(make_server())
    decision = proxy.guard_interface(signer)
    assert not decision and decision.code == "error"


def test_guard_approval_approved_proceeds(live_registry, monkeypatch):
    MCPSigner.from_server(make_server()).register(live_registry)
    monkeypatch.setenv("KIJI_SAFEGUARD_MODE", "approval")
    monkeypatch.setenv("KIJI_SAFEGUARD_REGISTRY", live_registry)
    monkeypatch.setenv("KIJI_SAFEGUARD_APPROVAL_POLL_INTERVAL", "0.05")
    monkeypatch.setenv("KIJI_SAFEGUARD_APPROVAL_TIMEOUT", "5")

    _, signer = _snapshot(make_server(extra_tool=True))
    resolver = threading.Thread(
        target=resolve_pending, args=(live_registry, "approve"), daemon=True
    )
    resolver.start()
    decision = proxy.guard_interface(signer)
    resolver.join(timeout=3)
    assert decision and decision.code == "ok"


def test_guard_approval_rejected_hard_blocks(live_registry, monkeypatch):
    MCPSigner.from_server(make_server()).register(live_registry)
    monkeypatch.setenv("KIJI_SAFEGUARD_MODE", "approval")
    monkeypatch.setenv("KIJI_SAFEGUARD_REGISTRY", live_registry)
    monkeypatch.setenv("KIJI_SAFEGUARD_APPROVAL_POLL_INTERVAL", "0.05")
    monkeypatch.setenv("KIJI_SAFEGUARD_APPROVAL_TIMEOUT", "5")

    _, signer = _snapshot(make_server(extra_tool=True))
    resolver = threading.Thread(
        target=resolve_pending, args=(live_registry, "reject"), daemon=True
    )
    resolver.start()
    decision = proxy.guard_interface(signer)
    resolver.join(timeout=3)
    # Rejection blocks regardless of enforce, and decide_serving must raise.
    assert not decision and decision.code == "rejected" and decision.hard
    with pytest.raises(SafeguardError):
        proxy.decide_serving(_snap(), decision)


# --- decide_serving --------------------------------------------------------


def _snap(name="demo-server"):
    return proxy.UpstreamSnapshot(
        name=name, instructions=None, capabilities=None
    )


def test_decide_allow_forwards():
    forward, notice = proxy.decide_serving(_snap(), proxy.ProxyDecision(True, "verified"))
    assert forward is True and notice is None


def test_decide_block_without_enforce_forwards(monkeypatch, capsys):
    monkeypatch.delenv("KIJI_SAFEGUARD_ENFORCE", raising=False)
    forward, notice = proxy.decide_serving(
        _snap(), proxy.ProxyDecision(False, "interface changed", code="changed")
    )
    assert forward is True and notice is None
    assert "WARNING" in capsys.readouterr().err


def test_decide_block_enforce_tripwire(monkeypatch):
    monkeypatch.setenv("KIJI_SAFEGUARD_ENFORCE", "1")
    monkeypatch.setenv("KIJI_SAFEGUARD_PROXY_ON_BLOCK", "tripwire")
    forward, notice = proxy.decide_serving(
        _snap(), proxy.ProxyDecision(False, "interface changed", code="changed")
    )
    assert forward is False and notice and "blocked" in notice


def test_decide_block_enforce_fail_raises(monkeypatch):
    monkeypatch.setenv("KIJI_SAFEGUARD_ENFORCE", "1")
    monkeypatch.setenv("KIJI_SAFEGUARD_PROXY_ON_BLOCK", "fail")
    with pytest.raises(SafeguardError):
        proxy.decide_serving(
            _snap(), proxy.ProxyDecision(False, "interface changed", code="changed")
        )


def test_decide_hard_reject_always_raises(monkeypatch):
    monkeypatch.delenv("KIJI_SAFEGUARD_ENFORCE", raising=False)
    monkeypatch.setenv("KIJI_SAFEGUARD_PROXY_ON_BLOCK", "tripwire")
    with pytest.raises(SafeguardError):
        proxy.decide_serving(
            _snap(),
            proxy.ProxyDecision(False, "rejected", code="rejected", hard=True),
        )


# --- end-to-end (in-memory) ------------------------------------------------


def test_snapshot_hash_matches_server_side():
    async def run():
        up = make_server(full=True)
        async with connect(up._mcp_server) as upstream:
            init = await upstream.initialize()
            snap = await proxy.capture_upstream(upstream, init)
            return snap.signer().hash, MCPSigner.from_server(up).hash

    wire_hash, server_hash = anyio.run(run)
    assert wire_hash == server_hash


def test_forward_proxy_is_transparent():
    async def run():
        up = make_server(full=True)
        async with connect(up._mcp_server) as upstream:
            init = await upstream.initialize()
            snap = await proxy.capture_upstream(upstream, init)
            server = proxy.build_proxy_server(snap, upstream, forward=True)
            async with connect(server) as down:
                di = await down.initialize()
                tools = sorted(t.name for t in (await down.list_tools()).tools)
                added = await down.call_tool("add", {"a": 3, "b": 4})
                resources = (await down.list_resources()).resources
                read = await down.read_resource(resources[0].uri)
                return di.serverInfo.name, di.instructions, tools, added, read

    name, instructions, tools, added, read = anyio.run(run)
    assert name == "demo-server"
    assert instructions == "Handle market data carefully."
    assert tools == ["add", "price_report", "shout"]
    assert added.structuredContent == {"result": 7}
    assert read.contents[0].text == "hello"


def test_tripwire_proxy_hides_tools():
    async def run():
        up = make_server(full=True)
        async with connect(up._mcp_server) as upstream:
            init = await upstream.initialize()
            snap = await proxy.capture_upstream(upstream, init)
            decision = proxy.ProxyDecision(
                False, "interface changed", code="changed", diff="~ changed tool 'add'"
            )
            notice = proxy.blocked_notice(snap.name, decision)
            server = proxy.build_proxy_server(
                snap, upstream, forward=False, notice=notice
            )
            async with connect(server) as down:
                await down.initialize()
                tools = [t.name for t in (await down.list_tools()).tools]
                result = await down.call_tool("add", {"a": 1, "b": 2})
                return tools, result

    tools, result = anyio.run(run)
    assert tools == [proxy.BLOCKED_TOOL_NAME]
    assert result.isError and "blocked" in result.content[0].text


# --- end-to-end (real subprocess via the stdio CLI) ------------------------


WEATHER = Path(__file__).resolve().parents[1] / "demo" / "fixtures" / "weather_server.py"


def test_stdio_cli_proxy_forwards_a_real_subprocess(live_registry, monkeypatch):
    """`kiji-safeguard proxy -- python weather_server.py` end to end.

    The downstream client launches the proxy CLI, which spawns the weather
    fixture upstream, verifies it (auto/TOFU against the live registry) and
    forwards its single tool.
    """
    env = {
        "KIJI_SAFEGUARD_REGISTRY": live_registry,
        "KIJI_SAFEGUARD_MODE": "auto",
        "PATH": __import__("os").environ.get("PATH", ""),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
    }
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "kiji_safeguard.cli", "proxy", "--", sys.executable, str(WEATHER)],
        env=env,
    )

    async def run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = [t.name for t in (await session.list_tools()).tools]
                result = await session.call_tool("get_forecast", {"city": "Portland"})
                return tools, result

    tools, result = anyio.run(run)
    assert "get_forecast" in tools
    assert "Portland" in result.content[0].text


def test_stdio_cli_proxy_hard_fail_exits_cleanly(live_registry):
    """`--on-block fail` with an expected-name mismatch (rename pinning).

    The upstream starts fine (auto/TOFU), but the proxy pins a different
    expected name, so it must refuse the connection and exit non-zero with a
    clean ``[kiji-safeguard]`` message rather than dumping a traceback.
    """
    import os
    import subprocess

    env = {
        **os.environ,
        "KIJI_SAFEGUARD_REGISTRY": live_registry,
        "KIJI_SAFEGUARD_MODE": "auto",
        "KIJI_SAFEGUARD_ENFORCE": "1",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
    }
    proc = subprocess.run(
        [
            sys.executable, "-m", "kiji_safeguard.cli", "proxy",
            "--on-block", "fail", "--expect-name", "not-weather", "--",
            sys.executable, str(WEATHER),
        ],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode != 0
    assert "[kiji-safeguard]" in proc.stderr
    assert "expected 'not-weather'" in proc.stderr
    assert "Traceback" not in proc.stderr


# --- HTTP transport (Phase 5) ----------------------------------------------


def test_open_upstream_selects_transport(monkeypatch):
    import mcp.client.sse as sse_mod
    import mcp.client.streamable_http as sh_mod

    calls: list[tuple[str, str]] = []
    name = "streamable_http_client" if hasattr(sh_mod, "streamable_http_client") else "streamablehttp_client"
    monkeypatch.setattr(
        sh_mod, name, lambda url, headers=None: calls.append(("http", url))
    )
    monkeypatch.setattr(
        sse_mod, "sse_client", lambda url, headers=None: calls.append(("sse", url))
    )
    proxy._open_upstream("u1", "streamable-http", None)
    proxy._open_upstream("u2", "sse", None)
    assert calls == [("http", "u1"), ("sse", "u2")]


def _free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class _ThreadServer:
    """Serve an ASGI app with uvicorn in a background thread (controllable)."""

    def __init__(self, app, port: int) -> None:
        import uvicorn

        config = uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="error", lifespan="on"
        )
        self._server = uvicorn.Server(config)

    def __enter__(self) -> "_ThreadServer":
        import threading
        import time

        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("upstream server did not start in time")
            time.sleep(0.02)
        return self

    def __exit__(self, *exc) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


def test_http_proxy_forwards_over_http(live_registry, monkeypatch):
    """Full HTTP -> HTTP chain.

    A FastMCP upstream is served over Streamable HTTP in a thread; the proxy
    connects to it as an HTTP client, verifies it (auto/TOFU) and re-serves it
    over its own Streamable HTTP endpoint, which a real downstream client then
    drives.
    """
    monkeypatch.setenv("KIJI_SAFEGUARD_MODE", "auto")
    monkeypatch.setenv("KIJI_SAFEGUARD_REGISTRY", live_registry)

    up_port = _free_port()
    down_port = _free_port()
    upstream_app = make_server(full=True).streamable_http_app()
    up_url = f"http://127.0.0.1:{up_port}/mcp"
    down_url = f"http://127.0.0.1:{down_port}/mcp"

    async def drive():
        import uvicorn

        with anyio.fail_after(30):
            async with proxy._open_upstream(up_url, "streamable-http", None) as up:
                async with ClientSession(up[0], up[1]) as upstream:
                    init = await upstream.initialize()
                    server = await proxy.prepare_server(upstream, init)
                    app = proxy.build_http_app(server, path="/mcp")
                    dserver = uvicorn.Server(
                        uvicorn.Config(
                            app, host="127.0.0.1", port=down_port,
                            log_level="error", lifespan="on",
                        )
                    )
                    dserver.install_signal_handlers = lambda: None
                    async with anyio.create_task_group() as tg:
                        tg.start_soon(dserver.serve)
                        while not dserver.started:
                            await anyio.sleep(0.02)
                        async with proxy._open_upstream(down_url, "streamable-http", None) as dn:
                            async with ClientSession(dn[0], dn[1]) as down:
                                di = await down.initialize()
                                tools = sorted(
                                    t.name for t in (await down.list_tools()).tools
                                )
                                added = await down.call_tool("add", {"a": 2, "b": 5})
                        dserver.should_exit = True
                    return di.serverInfo.name, tools, added

    with _ThreadServer(upstream_app, up_port):
        name, tools, added = anyio.run(drive)

    assert name == "demo-server"
    assert tools == ["add", "price_report", "shout"]
    assert added.structuredContent == {"result": 7}
