"""FastAPI registry for MCP server interface signatures.

Run with::

    uvicorn kiji_safeguard.server.backend.main:app --reload

or ``kiji-safeguard serve``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from kiji_safeguard.signer import aggregate_hash

from . import database
from .models import (
    ApprovalListResponse,
    ApprovalRecord,
    ApprovalRequest,
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
    status = "deprecated" if record.get("superseded_by") else "active"
    return ServerRecord(
        **record, summary=_summarise(record["interface"]), status=status
    )


def _to_approval_response(record: dict[str, Any]) -> ApprovalRecord:
    return ApprovalRecord(**record, summary=_summarise(record["new_interface"]))


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
    record = database.insert_server(
        submission.name, submission.hash, submission.interface
    )
    return _to_response(record)


@app.get("/servers/{hash_value}", response_model=list[ServerRecord])
def lookup_by_hash(hash_value: str) -> list[ServerRecord]:
    """Return every registration matching an interface hash."""
    records = database.get_by_hash(hash_value)
    if not records:
        raise HTTPException(
            status_code=404, detail="no server registered with this hash"
        )
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


@app.post("/approvals", response_model=ApprovalRecord, status_code=201)
def create_approval(submission: ApprovalRequest) -> ApprovalRecord:
    """Open a pending approval request for a changed interface."""
    derived = aggregate_hash(submission.new_interface)
    if derived != submission.new_hash:
        raise HTTPException(
            status_code=400,
            detail=(
                "submitted new_hash does not match the submitted interface "
                f"(expected {derived})"
            ),
        )
    record = database.create_approval(
        submission.name,
        submission.recorded_hash,
        submission.new_hash,
        submission.new_interface,
        submission.diff,
    )
    return _to_approval_response(record)


@app.get("/approvals", response_model=ApprovalListResponse)
def list_pending_approvals(
    status: str = Query(default="pending", description="Only 'pending' is supported"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ApprovalListResponse:
    """List pending approval requests, most recent first."""
    records, total = database.get_pending_approvals(limit=limit, offset=offset)
    return ApprovalListResponse(
        approvals=[_to_approval_response(r) for r in records], total=total
    )


@app.get("/approvals/{approval_id}", response_model=ApprovalRecord)
def get_approval(approval_id: int) -> ApprovalRecord:
    """Return a single approval request (the client polls this until resolved)."""
    record = database.get_approval(approval_id)
    if record is None:
        raise HTTPException(status_code=404, detail="no approval request with this id")
    return _to_approval_response(record)


@app.post("/approvals/{approval_id}/approve", response_model=ApprovalRecord)
def approve_request(approval_id: int) -> ApprovalRecord:
    """Approve a request: register the new interface, then mark it approved.

    Registering first guarantees that the moment a polling client observes
    ``approved`` the trusted ``(name, hash)`` row already exists, so its next
    verification passes.  When the change replaced a previously-trusted hash,
    that earlier interface is deprecated and linked to its replacement so the
    supersession is auditable in the UI.  Every step is idempotent, so a
    double-click or retry is harmless.
    """
    record = database.get_approval(approval_id)
    if record is None:
        raise HTTPException(status_code=404, detail="no approval request with this id")
    if record["status"] != "pending":
        return _to_approval_response(record)

    derived = aggregate_hash(record["new_interface"])
    if derived != record["new_hash"]:
        raise HTTPException(
            status_code=400,
            detail=(
                "stored new_hash does not match the stored interface "
                f"(expected {derived})"
            ),
        )
    database.insert_server(record["name"], record["new_hash"], record["new_interface"])
    recorded_hash = record["recorded_hash"]
    if recorded_hash and recorded_hash != record["new_hash"]:
        database.supersede(record["name"], recorded_hash, record["new_hash"])
    resolved = database.resolve_approval(approval_id, "approved")
    return _to_approval_response(resolved or record)


@app.post("/approvals/{approval_id}/reject", response_model=ApprovalRecord)
def reject_request(approval_id: int) -> ApprovalRecord:
    """Reject a request: mark it rejected without registering anything."""
    record = database.get_approval(approval_id)
    if record is None:
        raise HTTPException(status_code=404, detail="no approval request with this id")
    if record["status"] != "pending":
        return _to_approval_response(record)
    resolved = database.resolve_approval(approval_id, "rejected")
    return _to_approval_response(resolved or record)


_FRONTEND_INDEX = Path(__file__).resolve().parent.parent / "frontend" / "index.html"


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(_FRONTEND_INDEX)
