"""FastAPI registry for MCP server interface signatures.

Run with::

    uvicorn server.backend.main:app --reload

or ``kiji-safeguard serve``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from kiji_safeguard.signer import aggregate_hash

from . import database
from .models import (
    InterfaceSummary,
    ServerListResponse,
    ServerRecord,
    ServerRegistration,
)


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    database.init_db()
    yield


app = FastAPI(
    title="kiji-safeguard registry",
    description="Registry of MCP server interface hashes",
    lifespan=_lifespan,
)


def _summarise(interface: list[dict[str, Any]]) -> InterfaceSummary:
    tools = [c.get("name", "") for c in interface if c.get("type") == "tool"]
    return InterfaceSummary(
        tool_count=len(tools),
        prompt_count=sum(1 for c in interface if c.get("type") == "prompt"),
        resource_count=sum(1 for c in interface if c.get("type") == "resource"),
        tools=sorted(tools),
    )


def _to_response(record: dict[str, Any]) -> ServerRecord:
    return ServerRecord(**record, summary=_summarise(record["interface"]))


@app.post("/servers", response_model=ServerRecord, status_code=201)
def register_server(submission: ServerRegistration) -> ServerRecord:
    """Register an MCP server by name, interface hash and interface description."""
    derived = aggregate_hash(submission.interface)
    if derived != submission.hash:
        raise HTTPException(
            status_code=400,
            detail=(
                "submitted hash does not match the submitted interface "
                f"(expected {derived})"
            ),
        )
    record = database.insert_server(submission.name, submission.hash, submission.interface)
    return _to_response(record)


@app.get("/servers/{hash_value}", response_model=list[ServerRecord])
def lookup_by_hash(hash_value: str) -> list[ServerRecord]:
    """Return every registration matching an interface hash."""
    records = database.get_by_hash(hash_value)
    if not records:
        raise HTTPException(status_code=404, detail="no server registered with this hash")
    return [_to_response(record) for record in records]


@app.get("/servers", response_model=ServerListResponse)
def list_servers(
    name: str | None = Query(default=None, description="Filter by server name"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ServerListResponse:
    """List registered servers, most recent first, optionally filtered by name."""
    records, total = database.get_recent(limit=limit, offset=offset, name=name)
    return ServerListResponse(servers=[_to_response(r) for r in records], total=total)


_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>kiji-safeguard registry</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 60rem; }
  h1 { font-size: 1.4rem; }
  table { border-collapse: collapse; width: 100%; }
  th, td { border-bottom: 1px solid #ddd; padding: .5rem; text-align: left; }
  code { font-size: .85em; word-break: break-all; }
</style>
</head>
<body>
<h1>kiji-safeguard registry</h1>
<p>Registered MCP server interfaces (most recent first).</p>
<table>
  <thead><tr><th>Name</th><th>Hash</th><th>Tools</th><th>Registered</th></tr></thead>
  <tbody id="rows"></tbody>
</table>
<script>
fetch("servers?limit=100").then(r => r.json()).then(data => {
  const rows = document.getElementById("rows");
  for (const s of data.servers) {
    const tr = document.createElement("tr");
    const tools = s.summary.tools.join(", ");
    tr.innerHTML = `<td>${s.name}</td><td><code>${s.hash}</code></td>` +
                   `<td>${tools}</td><td>${s.registered_at}</td>`;
    rows.appendChild(tr);
  }
});
</script>
</body>
</html>
"""


@app.get("/", include_in_schema=False)
def index() -> HTMLResponse:
    return HTMLResponse(_INDEX_HTML)
