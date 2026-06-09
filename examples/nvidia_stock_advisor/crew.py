"""CrewAI crew that researches a stock and produces a purchase recommendation.

The crew has two agents:

* a *Market Researcher* that uses the MCP tools (stock price + news) to gather
  current data, and
* an *Investment Advisor* that turns that research into a BUY / HOLD / AVOID call.

Tools are provided by two local MCP servers (see ``mcp_servers/``) and wired in
by ``main.py`` via ``MCPServerAdapter``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from crewai import LLM, Agent, Crew, Process, Task
from mcp import StdioServerParameters

MCP_DIR = Path(__file__).parent / "mcp_servers"


def server_params() -> list[StdioServerParameters]:
    """Return the stdio parameters for every MCP server this demo uses.

    ``sys.executable`` is used so the servers run inside the same virtual
    environment as the crew (where ``mcp`` and ``yfinance`` are installed).
    """
    child_env = {**os.environ}
    return [
        StdioServerParameters(
            command=sys.executable,
            args=[str(MCP_DIR / "stock_price_server.py")],
            env=child_env,
        ),
        StdioServerParameters(
            command=sys.executable,
            args=[str(MCP_DIR / "news_server.py")],
            env=child_env,
        ),
    ]


def build_crew(tools, ticker: str = "NVDA") -> Crew:
    """Assemble the research + recommendation crew.

    Args:
        tools: Aggregated MCP tools yielded by ``MCPServerAdapter``.
        ticker: Stock symbol to analyze.
    """
    llm = LLM(model=os.getenv("OPENAI_MODEL_NAME", "gpt-4o-mini"))

    researcher = Agent(
        role="Equity Market Researcher",
        goal=(
            f"Collect accurate, up-to-date price data and news for {ticker} "
            "using the available tools."
        ),
        backstory=(
            "You are a meticulous markets researcher. You never invent numbers; "
            "you always call the available tools to fetch live prices, recent "
            "price history, and the latest headlines before reporting."
        ),
        tools=tools,
        llm=llm,
        verbose=True,
    )

    advisor = Agent(
        role="Investment Advisor",
        goal=(
            f"Turn the research brief into a clear, well-reasoned recommendation "
            f"for {ticker}."
        ),
        backstory=(
            "You are a disciplined investment advisor. You weigh momentum, "
            "valuation context, and news sentiment, you are explicit about risks, "
            "and you always remind the reader that this is not financial advice."
        ),
        llm=llm,
        verbose=True,
    )

    research_task = Task(
        description=(
            f"Research {ticker}. Use the tools to gather: (1) the current price "
            f"snapshot, (2) the 1-month price history, and (3) the latest 5 news "
            f"headlines. Do not fabricate any data — only report what the tools "
            f"return."
        ),
        expected_output=(
            "A concise research brief containing the current price and day change, "
            "the 1-month trend, and a bulleted list of 3-5 key headlines with a "
            "one-line takeaway for each."
        ),
        agent=researcher,
    )

    recommendation_task = Task(
        description=(
            f"Using only the research brief, produce an investment recommendation "
            f"for {ticker}. Decide BUY, HOLD, or AVOID and justify it."
        ),
        expected_output=(
            "A structured recommendation with these sections:\n"
            "- Recommendation: BUY / HOLD / AVOID (one line)\n"
            "- Conviction: low / medium / high\n"
            "- Rationale: 3-5 bullets tied to the price action and news\n"
            "- Key risks: 2-3 bullets\n"
            "- Suggested position sizing: a brief, general note\n"
            "- Disclaimer: a one-line reminder that this is not financial advice."
        ),
        agent=advisor,
        context=[research_task],
    )

    return Crew(
        agents=[researcher, advisor],
        tasks=[research_task, recommendation_task],
        process=Process.sequential,
        verbose=True,
    )
