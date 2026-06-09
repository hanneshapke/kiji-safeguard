# kiji-safeguard

Sign and verify **MCP servers**. Detects when tools, schemas or descriptions
change.

`kiji-safeguard` is the MCP-server sibling of
[`agent-signing`](https://github.com/hanneshapke/agent-signing). The signature
of an MCP server is a **content hash of its public interface** — tool names,
descriptions and JSON schemas (plus prompts, resources and server
instructions). No keys, no user identity: a server is registered with a
**name**, its interface **hash** and the full **interface description**, and
verification recomputes the hash from the live server and looks it up in the
registry.

This catches the classic MCP supply-chain problems: a tool quietly added or
removed, a schema widened, or a description rewritten to poison the model
("rug pull" / tool-description injection).

## The magic one-liner

Add a single import to any [FastMCP](https://github.com/modelcontextprotocol/python-sdk)
server — before *or* after `mcp` is imported:

```python
import kiji_safeguard.autosign  # noqa: F401
```

That's it. An import hook patches `FastMCP.run()` the moment
`mcp.server.fastmcp` is imported (or in place, if it already was). Every time
the server starts, its interface is extracted, hashed and checked against the
registry — with zero further code changes:

```text
$ python weather_server.py
[kiji-safeguard] verified 'weather' (hash 4c469eb41474f6eb…)
```

If someone edits a tool description after registration:

```text
[kiji-safeguard] WARNING: verification of 'weather' failed: interface changed:
'weather' is registered with hash 4c469eb4…, but the live interface hashes to 740e904b…
```

Behaviour is driven by environment variables, so the same code runs in every
stage of the lifecycle:

| Variable | Values | Default | Meaning |
| --- | --- | --- | --- |
| `KIJI_SAFEGUARD_MODE` | `verify` / `register` / `off` | `verify` | Check against the registry, publish to it, or do nothing |
| `KIJI_SAFEGUARD_REGISTRY` | URL | `http://127.0.0.1:8000` | Registry base URL |
| `KIJI_SAFEGUARD_ENFORCE` | `1`/`true`/… | unset | Abort startup on failure instead of warning |

All diagnostics go to **stderr** — stdout stays clean for the stdio transport.

## Quickstart

```bash
pip install "kiji-safeguard[server]"   # or: uv pip install -e ".[dev]" from this repo

# 1. Run the registry (FastAPI + SQLite, with a tiny web UI at /)
kiji-safeguard serve --port 8000

# 2. Register a server (loads the file, finds the FastMCP instance)
kiji-safeguard register mcp_servers/stock_price_server.py

# 3. Verify it any time — exits non-zero on mismatch
kiji-safeguard verify mcp_servers/stock_price_server.py
```

Or with the magic import instead of the CLI:

```bash
KIJI_SAFEGUARD_MODE=register python my_server.py   # first run: publish
python my_server.py                                # every run after: verify
KIJI_SAFEGUARD_ENFORCE=1 python my_server.py       # production: refuse to start on mismatch
```

## Programmatic API

```python
from kiji_safeguard import MCPSigner

signer = MCPSigner.from_server(mcp)          # any FastMCP instance
signer.hash                                  # 64-char interface hash
signer.register("http://127.0.0.1:8000")     # POST name + hash + interface

result = signer.verify("http://127.0.0.1:8000")
if not result:
    raise RuntimeError(result.reason)
```

`extract_interface()` and `aggregate_hash()` are exposed too if you only want
the hashing.

## How the hash works

Following `agent-signing`, the hash is **order-independent**:

1. Every interface component (tool, prompt, resource, instructions) is
   serialised as canonical JSON (sorted keys, compact separators).
2. Each serialisation is hashed with SHA-256.
3. The per-component digests are sorted lexicographically, concatenated and
   hashed again.

Reordering tools never changes the hash; changing a name, description or any
schema detail always does. The server **name is not part of the hash** — it is
registry metadata, which lets verification distinguish "interface changed"
from "same interface registered under a different name".

## Registry API

| Method & path | Purpose |
| --- | --- |
| `POST /servers` | Register `{name, hash, interface}`. Rejects submissions whose hash doesn't match the interface (400). Idempotent per `(name, hash)`. |
| `GET /servers/{hash}` | All registrations for an interface hash (404 if none). |
| `GET /servers?name=&limit=&offset=` | Recent registrations, optionally filtered by name. |
| `GET /` | Web UI: browse, search by name or hash, and inspect registered interfaces. |

Storage is SQLite (`KIJI_SAFEGUARD_DB`, default `kiji_safeguard_registry.db`).

## Repository layout

```
kiji_safeguard/        # client library (stdlib-only, no dependencies)
├── signer.py          # interface extraction, hashing, register/verify
├── autosign.py        # the magic import hook
└── cli.py             # hash / register / verify / serve
server/                # registry service (mirrors agent-signing's layout)
├── backend/
│   ├── main.py        # FastAPI endpoints
│   ├── models.py      # pydantic models
│   └── database.py    # SQLite persistence
└── frontend/
    └── index.html     # web UI (shares agent-signing's registry design)
examples/              # demo project whose MCP servers use the magic import
tests/                 # pytest suite (incl. live-registry round trips)
```

The client library is intentionally **dependency-free** (stdlib `urllib` +
`hashlib`), so adding the safeguard import to an MCP server pulls in nothing
else. The registry extras (`fastapi`, `uvicorn`) are only needed where the
registry runs.

## Development

```bash
uv venv && uv pip install -e ".[dev]"
pytest
```
