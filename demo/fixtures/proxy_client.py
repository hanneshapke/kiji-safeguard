"""A tiny MCP client that connects *through the kiji-safeguard proxy*.

This stands in for Claude Code / Zed in the proxy demo: it launches

    kiji-safeguard proxy -- python <upstream>

as its MCP server, completes the handshake and prints the tools it can see.
On a clean upstream that is the real ``get_forecast``; on a tampered one the
proxy serves a tripwire, so all the client (and the model behind it) sees is a
single ``kiji_safeguard_blocked`` notice -- the poisoned tool never arrives.

The proxy's own ``[kiji-safeguard] …`` diagnostics go to stderr and show up in
the recording alongside this output.

Usage::

    python demo/fixtures/proxy_client.py demo/fixtures/proxy_upstream.py
"""

from __future__ import annotations

import os
import sys

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

BLOCKED = "kiji_safeguard_blocked"


async def main(upstream: str) -> None:
    print(f"[client] launching: kiji-safeguard proxy -- python {upstream}")
    params = StdioServerParameters(
        command="kiji-safeguard",
        args=["proxy", "--", sys.executable, upstream],
        # Forward our environment so the proxy inherits KIJI_SAFEGUARD_MODE /
        # _ENFORCE / _REGISTRY (StdioServerParameters drops them otherwise).
        env=dict(os.environ),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools

            print()
            print("[client] tools the model can see through the proxy:")
            for tool in tools:
                summary = (tool.description or "").strip().splitlines()[0]
                if len(summary) > 88:
                    summary = summary[:88].rstrip() + "…"
                print(f"    • {tool.name} — {summary}")
            if any(tool.name == BLOCKED for tool in tools):
                print("[client] the tampered tool never reached the model. ✔")
            else:
                print("[client] interface verified; tools forwarded unchanged. ✔")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: proxy_client.py <upstream_server.py>")
    anyio.run(main, sys.argv[1])
