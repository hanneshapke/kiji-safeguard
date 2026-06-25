"""A verifying MCP proxy: the safeguard for clients that can't import Python.

The magic import (``import kiji_safeguard.autosign``) puts the agent-side
verification -- the real security boundary -- *inside* a Python client process.
Clients like **Claude Code** (TypeScript) and **Zed** (Rust) can't run it, so
they can't get the wire-level check.  This module moves that check into a
standalone process the client points at:

    Claude Code / Zed  --MCP-->  kiji-safeguard proxy  --MCP-->  upstream server

The proxy is an MCP server to the downstream client and an MCP *client* to the
upstream server.  On the upstream handshake it rebuilds the interface from the
wire (exactly as :func:`kiji_safeguard.autosign._on_initialize` does), hashes
it and checks it against the registry.  Then, depending on the verdict and the
``KIJI_SAFEGUARD_*`` policy, it either forwards the upstream transparently or
serves a **tripwire** interface that exposes no real tools -- so a rug-pulled
server never reaches the model.

Because the proxy hashes what actually arrives over the wire, the upstream's
language and transport stop mattering: an ``npx`` server, a Go binary, a hosted
HTTP endpoint all verify the same way.

Behaviour is driven by the same environment variables as the import
(``KIJI_SAFEGUARD_MODE`` / ``_REGISTRY`` / ``_ENFORCE`` / approval knobs), plus:

``KIJI_SAFEGUARD_PROXY_ON_BLOCK``
    ``tripwire`` (default) serves an empty interface plus a single notice tool
    explaining the block; ``fail`` refuses the connection outright.
``KIJI_SAFEGUARD_PROXY_EXPECT_NAME``
    The server name the upstream is expected to report.  A mismatch is treated
    as a verification failure -- closing the "a tampered server renames itself"
    hole that the import alone leaves open.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from typing import Any

from . import _config
from .signer import MCPSigner, extract_interface_from_listing

BLOCKED_TOOL_NAME = "kiji_safeguard_blocked"


def _on_block() -> str:
    value = os.environ.get("KIJI_SAFEGUARD_PROXY_ON_BLOCK", "tripwire").strip().lower()
    return value if value in {"tripwire", "fail"} else "tripwire"


def _expected_name() -> str | None:
    return os.environ.get("KIJI_SAFEGUARD_PROXY_EXPECT_NAME") or None


# --- verification verdict ---------------------------------------------------


@dataclass
class ProxyDecision:
    """Whether the upstream interface is trusted, and why.

    ``allow`` is the raw security verdict: the interface verified, was
    registered, was trust-on-first-use registered, or was approved.  ``hard``
    marks a verdict that must block regardless of enforce mode (an explicit
    human rejection).  ``code`` mirrors :class:`signer.VerificationResult`
    codes plus ``"renamed"`` (expected-name mismatch) and ``"off"``.
    """

    allow: bool
    reason: str
    code: str = "ok"
    diff: str | None = None
    hard: bool = False

    def __bool__(self) -> bool:
        return self.allow


def guard_interface(
    signer: MCPSigner, expected_name: str | None = None
) -> ProxyDecision:
    """Decide whether ``signer``'s interface should be trusted.

    Mirrors :func:`kiji_safeguard.autosign._apply_policy`, but returns a
    decision object instead of logging/raising, so the proxy can choose how to
    surface a failure (tripwire vs. hard-fail).  A registry that can't be
    reached becomes an ``"error"`` verdict, which enforce mode blocks.
    """
    if _config.mode() == "off":
        return ProxyDecision(True, "safeguard disabled (mode=off)", code="off")

    if expected_name is not None and signer.name != expected_name:
        return ProxyDecision(
            False,
            f"upstream reported name {signer.name!r}, expected {expected_name!r}",
            code="renamed",
        )

    registry = _config.registry_url()
    mode = _config.mode()

    if mode == "register":
        try:
            signer.register(registry)
        except (ConnectionError, ValueError) as exc:
            return ProxyDecision(False, f"registration failed: {exc}", code="error")
        return ProxyDecision(True, f"registered with hash {signer.hash}", code="ok")

    try:
        result = signer.verify(registry)
    except ConnectionError as exc:
        return ProxyDecision(False, f"registry unreachable: {exc}", code="error")

    if result:
        return ProxyDecision(True, f"verified (hash {signer.hash})", code="ok")

    if mode == "auto" and result.code == "unregistered":
        try:
            signer.register(registry)
        except (ConnectionError, ValueError) as exc:
            return ProxyDecision(False, f"registration failed: {exc}", code="error")
        return ProxyDecision(
            True, f"first sight — registered with hash {signer.hash}", code="ok"
        )

    if mode == "approval" and result.code == "changed":
        return _resolve_approval(signer, result, registry)

    return ProxyDecision(False, result.reason, code=result.code, diff=result.diff)


def _resolve_approval(signer: MCPSigner, result: Any, registry: str) -> ProxyDecision:
    """Open an approval request and block until a human decides (or we time out)."""
    recorded_hash = (result.record or {}).get("hash")
    _config.note(
        f"interface of {signer.name!r} changed; requesting human approval at "
        f"{registry} (waiting up to {int(_config.approval_timeout())}s)"
    )
    try:
        approval_id = signer.request_approval(registry, recorded_hash, result.diff)
        decision = signer.poll_approval(
            registry,
            approval_id,
            interval=_config.approval_interval(),
            overall_timeout=_config.approval_timeout(),
        )
    except (ConnectionError, ValueError) as exc:
        return ProxyDecision(
            False, f"approval request failed: {exc}", code="error", diff=result.diff
        )
    except TimeoutError as exc:
        return ProxyDecision(False, str(exc), code="changed", diff=result.diff)

    if decision == "approved":
        return ProxyDecision(
            True, f"approved: new interface (hash {signer.hash}) is now trusted"
        )
    # An explicit human rejection always blocks, even with enforce off.
    return ProxyDecision(
        False,
        f"approval for {signer.name!r} (hash {signer.hash}) was rejected",
        code="rejected",
        diff=result.diff,
        hard=True,
    )


# --- upstream snapshot ------------------------------------------------------


@dataclass
class UpstreamSnapshot:
    """The typed wire objects an upstream advertised at connection time.

    Captured once, right after ``initialize``, and re-served verbatim for the
    downstream ``list_*`` endpoints so the client sees exactly the interface
    that was verified -- even if the upstream mutates its listing afterwards.
    """

    name: str
    instructions: str | None
    capabilities: Any
    tools: list[Any] = field(default_factory=list)
    prompts: list[Any] = field(default_factory=list)
    resources: list[Any] = field(default_factory=list)

    def interface(self) -> list[dict[str, Any]]:
        return extract_interface_from_listing(
            tools=self.tools,
            prompts=self.prompts,
            resources=self.resources,
            instructions=self.instructions,
        )

    def signer(self) -> MCPSigner:
        return MCPSigner(name=self.name, interface=self.interface())


async def capture_upstream(upstream: Any, init_result: Any) -> UpstreamSnapshot:
    """Drain an upstream session's advertised tools/prompts/resources once."""
    capabilities = init_result.capabilities
    tools = await _config.drain(upstream.list_tools, "tools") if capabilities.tools else []
    prompts = (
        await _config.drain(upstream.list_prompts, "prompts")
        if capabilities.prompts
        else []
    )
    resources = (
        await _config.drain(upstream.list_resources, "resources")
        if capabilities.resources
        else []
    )
    return UpstreamSnapshot(
        name=getattr(getattr(init_result, "serverInfo", None), "name", None) or "",
        instructions=init_result.instructions,
        capabilities=capabilities,
        tools=tools,
        prompts=prompts,
        resources=resources,
    )


def blocked_notice(name: str, decision: ProxyDecision) -> str:
    """The message the tripwire surfaces to the user in place of real tools."""
    lines = [
        f"kiji-safeguard blocked the MCP server {name!r}: {decision.reason}.",
        "Its tools are withheld until the change is reviewed in the registry's "
        "web UI (Pending Approvals).",
    ]
    if decision.diff:
        lines.append("\nInterface diff (recorded -> live):\n" + decision.diff)
    return "\n".join(lines)


# --- downstream server (transparent forward, or tripwire) -------------------


def build_proxy_server(
    snapshot: UpstreamSnapshot,
    upstream: Any,
    *,
    forward: bool,
    notice: str | None = None,
    name: str | None = None,
) -> Any:
    """Construct the downstream MCP server.

    With ``forward`` true, the verified snapshot is served for the ``list_*``
    endpoints while ``call_tool`` / ``get_prompt`` / ``read_resource`` forward
    live to ``upstream``.  With ``forward`` false, a single notice tool is
    served and nothing else (the tripwire).
    """
    import mcp.types as types
    from mcp.server.lowlevel import Server
    from mcp.server.lowlevel.helper_types import ReadResourceContents

    server_name = name or snapshot.name or "kiji-safeguard-proxy"
    server: Any = Server(server_name, instructions=snapshot.instructions)

    if not forward:
        message = notice or f"kiji-safeguard blocked {server_name!r}."

        @server.list_tools()
        async def _list_tools() -> list[Any]:
            return [
                types.Tool(
                    name=BLOCKED_TOOL_NAME,
                    description=message,
                    inputSchema={"type": "object", "properties": {}},
                )
            ]

        @server.call_tool(validate_input=False)
        async def _call_tool_blocked(tool_name: str, arguments: dict[str, Any]) -> Any:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=message)],
                isError=True,
            )

        return server

    @server.list_tools()
    async def _list_tools() -> list[Any]:
        return snapshot.tools

    @server.call_tool(validate_input=False)
    async def _call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
        return await upstream.call_tool(tool_name, arguments or {})

    if snapshot.capabilities.prompts:

        @server.list_prompts()
        async def _list_prompts() -> list[Any]:
            return snapshot.prompts

        @server.get_prompt()
        async def _get_prompt(prompt_name: str, arguments: dict[str, str] | None) -> Any:
            return await upstream.get_prompt(prompt_name, arguments)

    if snapshot.capabilities.resources:

        @server.list_resources()
        async def _list_resources() -> list[Any]:
            return snapshot.resources

        @server.read_resource()
        async def _read_resource(uri: Any) -> Any:
            result = await upstream.read_resource(uri)
            contents: list[ReadResourceContents] = []
            for item in result.contents:
                if getattr(item, "text", None) is not None:
                    contents.append(
                        ReadResourceContents(content=item.text, mime_type=item.mimeType)
                    )
                elif getattr(item, "blob", None) is not None:
                    contents.append(
                        ReadResourceContents(
                            content=base64.b64decode(item.blob),
                            mime_type=item.mimeType,
                        )
                    )
            return contents

    return server


def decide_serving(
    snapshot: UpstreamSnapshot, decision: ProxyDecision
) -> tuple[bool, str | None]:
    """Map a verdict + policy to a serving choice: (forward?, notice).

    Raises :class:`_config.SafeguardError` when the policy is to hard-fail a
    blocked interface (``on_block=fail``, or an unreachable registry / failed
    verification under enforce, or an explicit human rejection).
    """
    if decision.allow:
        _config.note(f"{snapshot.name or 'upstream'!r}: {decision.reason}")
        return True, None

    if decision.diff:
        _config.note(f"interface diff for {snapshot.name!r}:\n{decision.diff}")

    must_block = decision.hard or _config.enforce()
    if not must_block:
        _config.note(
            f"WARNING: {snapshot.name or 'upstream'!r} {decision.reason}; "
            "forwarding anyway (enforce off)"
        )
        return True, None

    if _on_block() == "fail" or decision.hard:
        raise _config.SafeguardError(
            f"verification of {snapshot.name or 'upstream'!r} failed: {decision.reason}"
        )

    _config.note(
        f"BLOCKED: {snapshot.name or 'upstream'!r} {decision.reason}; serving tripwire"
    )
    return False, blocked_notice(snapshot.name or "upstream", decision)


# --- serving (shared core + transports) -------------------------------------


async def prepare_server(
    upstream: Any, init_result: Any, expected_name: str | None = None
) -> Any:
    """Capture, verify and build the downstream server for an upstream session.

    Transport-independent: both the stdio and HTTP entrypoints call this after
    they have an initialized upstream ``ClientSession``.  Raises
    :class:`_config.SafeguardError` when policy is to hard-fail a blocked
    interface.
    """
    snapshot = await capture_upstream(upstream, init_result)
    decision = guard_interface(snapshot.signer(), expected_name=expected_name)
    forward, notice = decide_serving(snapshot, decision)
    return build_proxy_server(snapshot, upstream, forward=forward, notice=notice)


def build_http_app(
    server: Any,
    *,
    path: str = "/mcp",
    json_response: bool = False,
    stateless: bool = False,
) -> Any:
    """Wrap a low-level MCP ``server`` in a Streamable HTTP ASGI app.

    The downstream client (Claude Code / Zed remote, Cursor, ...) points its
    MCP URL at ``http://host:port{path}``.  Returned as a Starlette app whose
    lifespan drives the session manager, so it can be served by uvicorn.
    """
    import contextlib

    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette
    from starlette.routing import Mount

    manager = StreamableHTTPSessionManager(
        app=server, json_response=json_response, stateless=stateless
    )

    async def handle(scope: Any, receive: Any, send: Any) -> None:
        await manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(_app: Any) -> Any:
        async with manager.run():
            yield

    return Starlette(routes=[Mount(path, app=handle)], lifespan=lifespan)


def _open_upstream(url: str, transport: str, headers: dict[str, str] | None) -> Any:
    """Return the async-context-manager that connects to an HTTP upstream.

    Yields ``(read, write, ...)`` streams; the extra trailing element of the
    streamable-http tuple (a session-id getter) is ignored by the caller.
    """
    if transport == "sse":
        from mcp.client.sse import sse_client

        return sse_client(url, headers=headers)

    import mcp.client.streamable_http as streamable_http

    # mcp >= 1.28 renamed streamablehttp_client -> streamable_http_client and
    # swapped its ``headers=`` kwarg for an injected httpx client.  Prefer the
    # new (non-deprecated) name; fall back to the old one on older mcp.
    new_client = getattr(streamable_http, "streamable_http_client", None)
    if new_client is not None:
        if headers:
            import httpx

            return new_client(url, http_client=httpx.AsyncClient(headers=headers))
        return new_client(url)
    return streamable_http.streamablehttp_client(url, headers=headers)


# --- stdio entrypoint -------------------------------------------------------


async def run_stdio_proxy(
    command: str,
    cmd_args: list[str] | None = None,
    env: dict[str, str] | None = None,
    expected_name: str | None = None,
) -> None:
    """Run a stdio proxy: spawn ``command`` upstream, serve verified downstream.

    The proxy reads the downstream client over this process's stdin/stdout and
    talks to the upstream over the subprocess's own pipes, so the two transports
    never collide.  Diagnostics go to stderr.
    """
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from mcp.server.stdio import stdio_server

    if expected_name is None:
        expected_name = _expected_name()

    params = StdioServerParameters(command=command, args=cmd_args or [], env=env)
    async with stdio_client(params) as (up_read, up_write):
        async with ClientSession(up_read, up_write) as upstream:
            init_result = await upstream.initialize()
            server = await prepare_server(upstream, init_result, expected_name)
            init_options = server.create_initialization_options()
            async with stdio_server() as (down_read, down_write):
                await server.run(down_read, down_write, init_options)


# --- HTTP / SSE entrypoint --------------------------------------------------


async def run_http_proxy(
    upstream_url: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    path: str = "/mcp",
    upstream_transport: str = "streamable-http",
    headers: dict[str, str] | None = None,
    expected_name: str | None = None,
    json_response: bool = False,
) -> None:
    """Run an HTTP proxy: connect to a remote upstream, serve verified downstream.

    The upstream is reached over Streamable HTTP (default) or SSE; the
    downstream is served as a Streamable HTTP endpoint on ``host:port{path}``.
    The single upstream session is verified once at startup and stays open for
    the lifetime of the server.
    """
    import uvicorn
    from mcp.client.session import ClientSession

    if expected_name is None:
        expected_name = _expected_name()

    async with _open_upstream(upstream_url, upstream_transport, headers) as streams:
        up_read, up_write = streams[0], streams[1]
        async with ClientSession(up_read, up_write) as upstream:
            init_result = await upstream.initialize()
            server = await prepare_server(upstream, init_result, expected_name)
            app = build_http_app(server, path=path, json_response=json_response)
            _config.note(
                f"serving verified proxy for {upstream_url} on "
                f"http://{host}:{port}{path}"
            )
            config = uvicorn.Config(app, host=host, port=port, log_level="warning")
            await uvicorn.Server(config).serve()
