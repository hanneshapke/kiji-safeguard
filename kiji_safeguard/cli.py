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
        failed = failed or not result.valid
    if failed:
        raise SystemExit(1)


def _cmd_serve(args: argparse.Namespace) -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit(
            "the registry requires the server extras: pip install 'kiji-safeguard[server]'"
        ) from exc
    if args.db:
        os.environ["KIJI_SAFEGUARD_DB"] = args.db
    uvicorn.run("server.backend.main:app", host=args.host, port=args.port)


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

    serve_parser = subparsers.add_parser("serve", help="Run the registry server")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument("--db", help="SQLite database path", default=None)
    serve_parser.set_defaults(func=_cmd_serve)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
