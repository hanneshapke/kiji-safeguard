"""Entry point for the NVIDIA stock advisor demo.

Usage:

    export OPENAI_API_KEY=sk-...
    python main.py            # defaults to NVDA
    python main.py NVDA       # or any other ticker

The crew connects to two local MCP servers (stock prices + news), gathers live
data, and prints a buy/hold/avoid recommendation.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

# Verify every MCP server this agent connects to (and register it on first
# sight) before its tools reach the crew. Imported before crewai_tools so the
# hook is in place when ``mcp`` is first loaded.
import kiji_safeguard.autosign  # noqa: F401

from dotenv import load_dotenv
from crewai_tools import MCPServerAdapter

from crew import build_crew, server_params

MCP_SERVER_NAMES = ("stock-prices", "stock-news")


def print_safeguard_status() -> None:
    """Show what the kiji-safeguard registry knows about our MCP servers.

    The servers register/verify themselves when they start (via the
    ``kiji_safeguard.autosign`` import in ``mcp_servers/*.py``), but their
    stderr is not always surfaced by the adapter — so ask the registry
    directly and report in this process, where the output is visible.
    """
    # Read here (not at import) so the value loaded from .env by load_dotenv()
    # is honored — and matches the registry the server subprocesses use.
    registry = os.environ.get("KIJI_SAFEGUARD_REGISTRY", "http://127.0.0.1:8000")
    print("kiji-safeguard registry status:")
    for name in MCP_SERVER_NAMES:
        url = (
            f"{registry}/servers?"
            + urllib.parse.urlencode({"name": name, "limit": 1})
        )
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                servers = json.load(response)["servers"]
        except (OSError, ValueError, KeyError):
            print(f"  {name}: registry not reachable at {registry}")
            continue
        if servers:
            print(f"  {name}: registered (hash {servers[0]['hash'][:16]}…)")
        else:
            print(f"  {name}: NOT registered")
    print()


def main() -> None:
    load_dotenv()

    if not os.getenv("OPENAI_API_KEY"):
        sys.exit(
            "OPENAI_API_KEY is not set. Add your key to the .env file in this "
            "directory (or export it) before running this demo."
        )

    ticker = sys.argv[1].upper() if len(sys.argv) > 1 else "NVDA"
    print(f"\n=== NVIDIA Stock Advisor — analyzing {ticker} ===\n")

    # MCPServerAdapter spawns the MCP servers as subprocesses and aggregates
    # their tools. The context manager guarantees the subprocesses are cleaned
    # up when the crew finishes.
    with MCPServerAdapter(server_params()) as tools:
        print(f"Connected to MCP servers. Available tools: {[t.name for t in tools]}\n")
        print_safeguard_status()
        crew = build_crew(tools, ticker=ticker)
        result = crew.kickoff()

    print("\n=== Recommendation ===\n")
    print(result)


if __name__ == "__main__":
    main()
