# NVIDIA Stock Advisor (CrewAI + MCP)

A small demo agent that researches a stock and recommends whether to buy it.

It uses **[CrewAI](https://docs.crewai.com/)** to orchestrate two agents, and
gets its data from **two local [MCP](https://modelcontextprotocol.io/) servers**:

| MCP server | Tools | Source |
| --- | --- | --- |
| `stock_price_server.py` | `get_current_price`, `get_price_history` | Yahoo Finance (`yfinance`) |
| `news_server.py` | `get_ticker_news` | Yahoo Finance (`yfinance`) |

The crew runs sequentially:

1. **Equity Market Researcher** — calls the MCP tools to pull the live price,
   the 1-month trend, and the latest headlines.
2. **Investment Advisor** — turns that brief into a `BUY / HOLD / AVOID` call
   with rationale, risks, and a not-financial-advice disclaimer.

```
main.py ──► MCPServerAdapter ──┬─► stock_price_server.py  (stdio)
                               └─► news_server.py         (stdio)
   │
   └─► Crew(Researcher → Advisor)  ──► recommendation
```

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/) (manages the Python 3.12 toolchain for you).
- An OpenAI API key in `.env` (the key is read as `OPENAI_API_KEY`):

  ```dotenv
  OPENAI_API_KEY=sk-...
  OPENAI_MODEL_NAME=gpt-4o-mini   # optional override
  ```

  A `.env` template is already in this directory — just drop in your key.

## Run it

```bash
cd examples/nvidia_stock_advisor

# Install deps into a local, Python-3.12 virtual env and run the demo.
uv run main.py            # defaults to NVDA
uv run main.py NVDA       # or pass any other ticker, e.g. AMD, MSFT
```

`uv run` installs the dependencies declared in `pyproject.toml` on first use,
then launches `main.py`. `MCPServerAdapter` starts the two MCP servers as
subprocesses (using the same interpreter), aggregates their tools, and hands
them to the researcher agent.

## Files

```
nvidia_stock_advisor/
├── main.py                       # entry point: wires MCP tools into the crew
├── crew.py                       # CrewAI agents, tasks, and crew definition
├── mcp_servers/
│   ├── stock_price_server.py     # MCP server: live price + history (yfinance)
│   └── news_server.py            # MCP server: latest headlines (yfinance)
├── pyproject.toml                # deps + Python version constraint (uv)
├── .python-version               # pins Python 3.12 for uv
└── .env                          # your OPENAI_API_KEY (gitignored)
```

## MCP server signing (kiji-safeguard)

Both MCP servers carry the magic one-liner

```python
import kiji_safeguard.autosign  # noqa: F401
```

so every `mcp.run()` checks the server's interface hash against the
[kiji-safeguard registry](../../README.md). To try it:

```bash
# from the repo root, in another terminal
uv run --extra server kiji-safeguard serve --port 8000

# then run the demo as usual
uv run main.py
```

On the first run both servers register themselves (trust-on-first-use);
they show up at <http://127.0.0.1:8000/>. Subsequent runs verify
automatically and warn on stderr if a tool, schema or description changed
(note: the warning is printed by the MCP server *subprocesses*, so
depending on the adapter it may not surface in the crew's console — the
registry UI is the reliable place to check). Set `KIJI_SAFEGUARD_ENFORCE=1`
to refuse startup on a mismatch instead, `KIJI_SAFEGUARD_MODE=verify` to
disable auto-registration, or `KIJI_SAFEGUARD_MODE=off` to disable
entirely.

## Notes

- **Not financial advice.** This is a demo of agent + MCP orchestration; the
  recommendations are illustrative only.
- Yahoo Finance data is delayed and occasionally rate-limited. If a tool returns
  an error message, re-run after a short pause.
- You can test an MCP server on its own with the MCP inspector:
  `uv run mcp dev mcp_servers/stock_price_server.py`.
