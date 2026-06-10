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

> **Note:** the demo's `.env` enables the kiji-safeguard check with
> `KIJI_SAFEGUARD_ENFORCE=1`, so the MCP servers refuse to start unless the
> registry is running. Start it first (see
> [MCP server signing](#mcp-server-signing-kiji-safeguard)) or set
> `KIJI_SAFEGUARD_ENFORCE=0` / `KIJI_SAFEGUARD_MODE=off` to skip it.

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

so every `mcp.run()` extracts the server's interface — tool names, descriptions,
and **both input and output schemas** — hashes it, and checks it against the
[kiji-safeguard registry](../../README.md). The demo's `.env` turns this on by
default:

```dotenv
KIJI_SAFEGUARD_REGISTRY=http://127.0.0.1:8000
KIJI_SAFEGUARD_ENFORCE=1   # a mismatch makes the server refuse to start → the crew aborts
```

Because `ENFORCE=1` aborts on any problem (mismatch **or** unreachable registry),
**start the registry before launching the demo**:

```bash
# terminal 1 — from the repo root
uv run --extra server kiji-safeguard serve --port 8000

# terminal 2 — from this directory
uv run main.py
```

The first run registers both servers (trust-on-first-use); they appear at
<http://127.0.0.1:8000/>. Every later run verifies them, and `main.py` prints
each server's registry status in its own console (the servers' stderr is not
reliably surfaced through the adapter). To run without the safeguard, set
`KIJI_SAFEGUARD_ENFORCE=0` (warn only) or `KIJI_SAFEGUARD_MODE=off` (disable) in
`.env`.

### Reproduce a tampered interface

1. Start the registry and run the demo once so both servers register.
2. Change a tool in `mcp_servers/` — rename it, edit its docstring, alter a
   parameter, **or change its return type** (e.g. `-> str` → `-> tuple[str, str]`,
   which the output-schema hash now catches).
3. Run again. The changed server's hash no longer matches its registration, so
   with `KIJI_SAFEGUARD_ENFORCE=1` it refuses to start and the crew aborts:

   ```text
   verification of 'stock-news' failed: interface changed: 'stock-news' is
   registered with hash …, but the live interface hashes to …
   ```

### Reset the registry

The signed interface includes output schemas, so registrations are tied to that
hashing scheme. After a kiji-safeguard upgrade (or to clear a stale baseline),
wipe the registry and let the next run re-register from scratch:

```bash
rm kiji_safeguard_registry.db   # at the repo root; recreated on the next `serve`
```

## Notes

- **Not financial advice.** This is a demo of agent + MCP orchestration; the
  recommendations are illustrative only.
- Yahoo Finance data is delayed and occasionally rate-limited. If a tool returns
  an error message, re-run after a short pause.
- You can test an MCP server on its own with the MCP inspector:
  `uv run mcp dev mcp_servers/stock_price_server.py`.
