"""Tiny FastMCP server used by the kiji-safeguard demo tapes.

This is the *clean*, reviewed interface. It carries the magic one-liner, so
simply running it through ``FastMCP.run`` registers/verifies its interface
against the registry. It depends only on the standard library and ``mcp`` --
no API keys, no network calls -- so the demo renders anywhere the dev
dependencies install.
"""

from __future__ import annotations

import kiji_safeguard.autosign  # noqa: F401  - registers/verifies on run
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("weather")


@mcp.tool()
def get_forecast(city: str) -> str:
    """Return the 5-day forecast for a city."""
    return f"Five-day outlook for {city}: sunny with a light breeze."


if __name__ == "__main__":
    mcp.run(transport="stdio")
