"""Entry point for the NVIDIA stock advisor demo.

Usage:

    export OPENAI_API_KEY=sk-...
    python main.py            # defaults to NVDA
    python main.py NVDA       # or any other ticker

The crew connects to two local MCP servers (stock prices + news), gathers live
data, and prints a buy/hold/avoid recommendation.
"""

from __future__ import annotations

import sys

from dotenv import load_dotenv
from crewai_tools import MCPServerAdapter

from crew import build_crew, server_params


def main() -> None:
    load_dotenv()

    import os

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
        crew = build_crew(tools, ticker=ticker)
        result = crew.kickoff()

    print("\n=== Recommendation ===\n")
    print(result)


if __name__ == "__main__":
    main()
