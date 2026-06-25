"""Command-line interface for kiji-safeguard.

Examples::

    kiji-safeguard hash mcp_servers/stock_price_server.py
    kiji-safeguard register mcp_servers/stock_price_server.py --registry http://127.0.0.1:8000
    kiji-safeguard verify mcp_servers/stock_price_server.py
    kiji-safeguard serve --port 8000
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

from .signer import DEFAULT_REGISTRY_URL, MCPSigner


def _load_servers(path: str) -> list[Any]:
    """Import a Python file and return every FastMCP-shaped object in it.

    ``__name__`` is not ``"__main__"`` during the import, so the usual
    ``if __name__ == "__main__": mcp.run()`` guard keeps the server from
    starting.
    """
    file_path = Path(path).resolve()
    module_name = f"_kiji_safeguard_target_{file_path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(file_path.parent))
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(file_path.parent))

    servers = [
        value
        for value in vars(module).values()
        if hasattr(value, "_tool_manager") and hasattr(value, "name")
    ]
    if not servers:
        raise SystemExit(f"no FastMCP server instance found in {path}")
    return servers


def _cmd_hash(args: argparse.Namespace) -> None:
    for server in _load_servers(args.path):
        signer = MCPSigner.from_server(server, name=args.name)
        print(f"{signer.name}\t{signer.hash}")
        if args.show_interface:
            json.dump(signer.interface, sys.stdout, indent=2, sort_keys=True)
            print()


def _cmd_register(args: argparse.Namespace) -> None:
    for server in _load_servers(args.path):
        signer = MCPSigner.from_server(server, name=args.name)
        record = signer.register(args.registry)
        print(f"registered {signer.name!r} with hash {record['hash']}")


def _cmd_verify(args: argparse.Namespace) -> None:
    failed = False
    for server in _load_servers(args.path):
        signer = MCPSigner.from_server(server, name=args.name)
        result = signer.verify(args.registry)
        status = "OK" if result else "FAILED"
        print(f"{status}\t{signer.name}\t{result.reason}")
        if result.diff:
            print(result.diff)
        failed = failed or not result.valid
    if failed:
        raise SystemExit(1)


def _parse_headers(raw: list[str] | None) -> dict[str, str]:
    """Parse repeated ``--header 'Key: Value'`` flags into a dict."""
    headers: dict[str, str] = {}
    for item in raw or []:
        key, sep, value = item.partition(":")
        if not sep:
            raise SystemExit(f"--header must be 'Key: Value', got {item!r}")
        headers[key.strip()] = value.strip()
    return headers


def _cmd_proxy(args: argparse.Namespace) -> None:
    import functools

    import anyio

    from ._config import SafeguardError
    from .proxy import run_http_proxy, run_stdio_proxy

    if args.registry:
        os.environ["KIJI_SAFEGUARD_REGISTRY"] = args.registry
    if args.on_block:
        os.environ["KIJI_SAFEGUARD_PROXY_ON_BLOCK"] = args.on_block
    expected_name = args.expect_name or os.environ.get("KIJI_SAFEGUARD_PROXY_EXPECT_NAME")
    if expected_name:
        os.environ["KIJI_SAFEGUARD_PROXY_EXPECT_NAME"] = expected_name

    def _find_safeguard_error(exc: BaseException) -> SafeguardError | None:
        if isinstance(exc, SafeguardError):
            return exc
        for sub in getattr(exc, "exceptions", ()):
            found = _find_safeguard_error(sub)
            if found is not None:
                return found
        return None

    if args.upstream_url:
        # HTTP mode: connect to a remote upstream, serve downstream over HTTP.
        runner = functools.partial(
            run_http_proxy,
            args.upstream_url,
            host=args.http_host,
            port=args.http_port,
            path=args.http_path,
            upstream_transport=args.upstream_transport,
            headers=_parse_headers(args.header),
            expected_name=expected_name,
        )
    else:
        # stdio mode: spawn the upstream subprocess after the -- separator.
        target = list(args.target)
        if target and target[0] == "--":
            target = target[1:]
        if not target:
            raise SystemExit(
                "usage: kiji-safeguard proxy [options] -- <command> [args...]\n"
                "   or: kiji-safeguard proxy --upstream-url <url> [options]"
            )
        # Forward the proxy's full environment to the upstream so anything the
        # client configured (API keys, and the KIJI_SAFEGUARD_* knobs) reaches
        # it, exactly as if the client had launched the upstream directly.
        command, *cmd_args = target
        runner = functools.partial(
            run_stdio_proxy, command, cmd_args, dict(os.environ), expected_name
        )

    try:
        anyio.run(runner)
    except BaseException as exc:  # noqa: BLE001 - surface clean message, re-raise rest
        blocked = _find_safeguard_error(exc)
        if blocked is None:
            raise
        raise SystemExit(f"[kiji-safeguard] {blocked}") from exc


def _cmd_serve(args: argparse.Namespace) -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit(
            "the registry requires the server extras: pip install 'kiji-safeguard[server]'"
        ) from exc
    if args.db:
        os.environ["KIJI_SAFEGUARD_DB"] = args.db
    uvicorn.run("kiji_safeguard.server.backend.main:app", host=args.host, port=args.port)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="kiji-safeguard", description="Sign and verify MCP server interfaces."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_target(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("path", help="Python file containing a FastMCP server")
        sub.add_argument("--name", help="Override the server name", default=None)

    hash_parser = subparsers.add_parser("hash", help="Print the interface hash")
    add_target(hash_parser)
    hash_parser.add_argument(
        "--show-interface", action="store_true", help="Also print the extracted interface"
    )
    hash_parser.set_defaults(func=_cmd_hash)

    register_parser = subparsers.add_parser("register", help="Register with the registry")
    add_target(register_parser)
    register_parser.add_argument("--registry", default=DEFAULT_REGISTRY_URL)
    register_parser.set_defaults(func=_cmd_register)

    verify_parser = subparsers.add_parser("verify", help="Verify against the registry")
    add_target(verify_parser)
    verify_parser.add_argument("--registry", default=DEFAULT_REGISTRY_URL)
    verify_parser.set_defaults(func=_cmd_verify)

    proxy_parser = subparsers.add_parser(
        "proxy",
        help="Run a verifying MCP proxy in front of a stdio server",
        description=(
            "Spawn an upstream stdio MCP server and re-expose it to a "
            "downstream client (Claude Code, Zed, ...), verifying its "
            "interface against the registry on connect. Put the upstream "
            "command after a -- separator."
        ),
    )
    proxy_parser.add_argument(
        "--registry", default=None, help="Registry base URL (overrides the env var)"
    )
    proxy_parser.add_argument(
        "--expect-name",
        default=None,
        help="Server name the upstream must report (pins against renames)",
    )
    proxy_parser.add_argument(
        "--on-block",
        choices=("tripwire", "fail"),
        default=None,
        help="On a blocked interface: serve a tripwire (default) or refuse the connection",
    )
    proxy_parser.add_argument(
        "--upstream-url",
        default=None,
        help="Front a remote HTTP/SSE upstream at this URL instead of a stdio command",
    )
    proxy_parser.add_argument(
        "--upstream-transport",
        choices=("streamable-http", "sse"),
        default="streamable-http",
        help="Transport for the HTTP upstream (default: streamable-http)",
    )
    proxy_parser.add_argument(
        "--header",
        action="append",
        metavar="'Key: Value'",
        help="HTTP header to send to the upstream (repeatable; e.g. auth)",
    )
    proxy_parser.add_argument(
        "--http-host", default="127.0.0.1", help="Host to serve the downstream HTTP endpoint on"
    )
    proxy_parser.add_argument(
        "--http-port", type=int, default=8000, help="Port to serve the downstream HTTP endpoint on"
    )
    proxy_parser.add_argument(
        "--http-path", default="/mcp", help="Path of the downstream HTTP endpoint (default: /mcp)"
    )
    proxy_parser.add_argument(
        "target",
        nargs=argparse.REMAINDER,
        help="-- followed by the upstream command and its arguments (stdio mode)",
    )
    proxy_parser.set_defaults(func=_cmd_proxy)

    serve_parser = subparsers.add_parser("serve", help="Run the registry server")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument("--db", help="SQLite database path", default=None)
    serve_parser.set_defaults(func=_cmd_serve)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
