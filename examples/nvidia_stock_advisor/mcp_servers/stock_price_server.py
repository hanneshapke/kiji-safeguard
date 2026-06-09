"""MCP server exposing stock price data via Yahoo Finance (yfinance).

Run directly for a stdio transport:

    python mcp_servers/stock_price_server.py

CrewAI's ``MCPServerAdapter`` launches this file as a subprocess and talks to it
over stdin/stdout, so it never needs to run on its own port.
"""

from __future__ import annotations

import yfinance as yf
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("stock-prices")


def _fmt(value: object, suffix: str = "") -> str:
    """Format a possibly-missing numeric field for display."""
    if value is None:
        return "n/a"
    if isinstance(value, (int, float)):
        return f"{value:,.2f}{suffix}"
    return f"{value}{suffix}"


@mcp.tool()
def get_current_price(ticker: str) -> str:
    """Return the latest available price snapshot for a stock ticker.

    Args:
        ticker: Stock symbol to look up, for example "NVDA".
    """
    try:
        info = yf.Ticker(ticker).fast_info
    except Exception as exc:  # noqa: BLE001 - surface a usable message to the agent
        return f"Could not load price data for {ticker!r}: {exc}"

    def field(name: str):
        try:
            return info[name]
        except Exception:  # noqa: BLE001
            return None

    last = field("last_price")
    prev = field("previous_close")
    change = None
    if isinstance(last, (int, float)) and isinstance(prev, (int, float)) and prev:
        change = (last - prev) / prev * 100

    currency = field("currency") or "USD"
    return (
        f"Price snapshot for {ticker.upper()} ({currency}):\n"
        f"- Last price: {_fmt(last)}\n"
        f"- Previous close: {_fmt(prev)}\n"
        f"- Day change: {_fmt(change, '%')}\n"
        f"- Day range: {_fmt(field('day_low'))} - {_fmt(field('day_high'))}\n"
        f"- 52-week range: {_fmt(field('year_low'))} - {_fmt(field('year_high'))}\n"
        f"- 50-day avg: {_fmt(field('fifty_day_average'))}\n"
        f"- 200-day avg: {_fmt(field('two_hundred_day_average'))}\n"
        f"- Market cap: {_fmt(field('market_cap'))}"
    )


@mcp.tool()
def get_price_history(ticker: str, period: str = "1mo") -> str:
    """Return a summary of recent historical closing prices.

    Args:
        ticker: Stock symbol to look up, for example "NVDA".
        period: yfinance period string such as "5d", "1mo", "3mo", "6mo" or "1y".
    """
    try:
        hist = yf.Ticker(ticker).history(period=period)
    except Exception as exc:  # noqa: BLE001
        return f"Could not load price history for {ticker!r}: {exc}"

    if hist is None or hist.empty:
        return f"No price history available for {ticker!r} over period {period!r}."

    closes = hist["Close"].dropna()
    if closes.empty:
        return f"No closing prices available for {ticker!r} over period {period!r}."

    start = float(closes.iloc[0])
    end = float(closes.iloc[-1])
    pct = (end - start) / start * 100 if start else 0.0

    lines = [
        f"{ticker.upper()} closing-price history over {period}:",
        f"- Start ({closes.index[0].date()}): {_fmt(start)}",
        f"- End ({closes.index[-1].date()}): {_fmt(end)}",
        f"- Change over period: {_fmt(pct, '%')}",
        f"- Period low / high: {_fmt(float(closes.min()))} / {_fmt(float(closes.max()))}",
        f"- Trading days observed: {len(closes)}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")
