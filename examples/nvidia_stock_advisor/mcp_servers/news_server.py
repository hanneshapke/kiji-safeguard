"""MCP server exposing recent stock news via Yahoo Finance (yfinance).

Run directly for a stdio transport:

    python mcp_servers/news_server.py

CrewAI's ``MCPServerAdapter`` launches this file as a subprocess and talks to it
over stdin/stdout.
"""

from __future__ import annotations

import yfinance as yf
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("stock-news")


def _extract(item: dict) -> dict:
    """Normalize a yfinance news item across the old and new payload shapes."""
    # Newer yfinance nests the article under "content".
    content = item.get("content") if isinstance(item, dict) else None
    if isinstance(content, dict):
        url = ""
        canonical = content.get("canonicalUrl") or content.get("clickThroughUrl")
        if isinstance(canonical, dict):
            url = canonical.get("url", "")
        provider = content.get("provider") or {}
        return {
            "title": content.get("title", ""),
            "summary": content.get("summary") or content.get("description", ""),
            "publisher": provider.get("displayName", "") if isinstance(provider, dict) else "",
            "published": content.get("pubDate") or content.get("displayTime", ""),
            "url": url,
        }
    # Older flat shape.
    return {
        "title": item.get("title", ""),
        "summary": item.get("summary", ""),
        "publisher": item.get("publisher", ""),
        "published": item.get("providerPublishTime", ""),
        "url": item.get("link", ""),
    }


@mcp.tool()
def get_ticker_news(ticker: str, limit: int = 5) -> str:
    """Return the most recent news headlines for a stock ticker.

    Args:
        ticker: Stock symbol to look up, for example "NVDA".
        limit: Maximum number of headlines to return (1-10).
    """
    limit = max(1, min(int(limit), 10))
    try:
        raw = yf.Ticker(ticker).news or []
    except Exception as exc:  # noqa: BLE001 - surface a usable message to the agent
        return f"Could not load news for {ticker!r}: {exc}"

    if not raw:
        return f"No recent news found for {ticker!r}."

    articles = [_extract(item) for item in raw[:limit]]
    blocks = []
    for i, art in enumerate(articles, start=1):
        title = art["title"] or "(untitled)"
        block = [f"{i}. {title}"]
        if art["publisher"] or art["published"]:
            block.append(f"   Source: {art['publisher']} {art['published']}".rstrip())
        if art["summary"]:
            block.append(f"   Summary: {art['summary']}")
        if art["url"]:
            block.append(f"   URL: {art['url']}")
        blocks.append("\n".join(block))

    header = f"Latest {len(articles)} headlines for {ticker.upper()}:"
    return header + "\n" + "\n".join(blocks)


if __name__ == "__main__":
    mcp.run(transport="stdio")
