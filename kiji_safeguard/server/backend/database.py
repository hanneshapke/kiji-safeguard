"""SQLite persistence for the MCP server registry.

The database path is read from ``KIJI_SAFEGUARD_DB`` on every connection so
tests (and the CLI) can point the registry at a fresh file at runtime.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

DEFAULT_DB_PATH = "kiji_safeguard_registry.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    hash TEXT NOT NULL,
    interface TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    UNIQUE (name, hash)
);
CREATE INDEX IF NOT EXISTS idx_servers_hash ON servers (hash);
CREATE INDEX IF NOT EXISTS idx_servers_name ON servers (name);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    recorded_hash TEXT,
    new_hash TEXT NOT NULL,
    new_interface TEXT NOT NULL,
    diff TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals (status);
-- At most one *pending* request per (name, new_hash): reconnect storms collapse
-- onto a single row instead of piling up duplicates awaiting the same decision.
CREATE UNIQUE INDEX IF NOT EXISTS idx_approvals_pending_unique
    ON approvals (name, new_hash) WHERE status = 'pending';
"""


def _db_path() -> str:
    return os.environ.get("KIJI_SAFEGUARD_DB", DEFAULT_DB_PATH)


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(_db_path())
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with _connect() as connection:
        connection.executescript(_SCHEMA)


def insert_server(name: str, hash_value: str, interface: list[dict[str, Any]]) -> dict[str, Any]:
    """Insert a registration; re-registering the same (name, hash) is a no-op."""
    registered_at = datetime.now(timezone.utc).isoformat()
    with _connect() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO servers (name, hash, interface, registered_at) "
            "VALUES (?, ?, ?, ?)",
            (name, hash_value, json.dumps(interface), registered_at),
        )
        row = connection.execute(
            "SELECT * FROM servers WHERE name = ? AND hash = ?", (name, hash_value)
        ).fetchone()
    return _row_to_record(row)


def get_by_hash(hash_value: str) -> list[dict[str, Any]]:
    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM servers WHERE hash = ? ORDER BY registered_at DESC",
            (hash_value,),
        ).fetchall()
    return [_row_to_record(row) for row in rows]


def get_recent(
    limit: int = 20, offset: int = 0, name: str | None = None
) -> tuple[list[dict[str, Any]], int]:
    with _connect() as connection:
        if name is not None:
            rows = connection.execute(
                "SELECT * FROM servers WHERE name = ? "
                "ORDER BY registered_at DESC LIMIT ? OFFSET ?",
                (name, limit, offset),
            ).fetchall()
            total = connection.execute(
                "SELECT COUNT(*) FROM servers WHERE name = ?", (name,)
            ).fetchone()[0]
        else:
            rows = connection.execute(
                "SELECT * FROM servers ORDER BY registered_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            total = connection.execute("SELECT COUNT(*) FROM servers").fetchone()[0]
    return [_row_to_record(row) for row in rows], total


def create_approval(
    name: str,
    recorded_hash: str | None,
    new_hash: str,
    new_interface: list[dict[str, Any]],
    diff: str = "",
) -> dict[str, Any]:
    """Open a pending approval request for a changed interface.

    Idempotent per ``(name, new_hash)`` while pending: a repeated request for
    the same change returns the existing pending row instead of creating a
    duplicate (mirrors :func:`insert_server`'s select-or-insert pattern).
    """
    created_at = datetime.now(timezone.utc).isoformat()
    with _connect() as connection:
        existing = connection.execute(
            "SELECT * FROM approvals "
            "WHERE name = ? AND new_hash = ? AND status = 'pending'",
            (name, new_hash),
        ).fetchone()
        if existing is not None:
            return _approval_row_to_record(existing)
        cursor = connection.execute(
            "INSERT INTO approvals "
            "(name, recorded_hash, new_hash, new_interface, diff, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (name, recorded_hash, new_hash, json.dumps(new_interface), diff, created_at),
        )
        row = connection.execute(
            "SELECT * FROM approvals WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    return _approval_row_to_record(row)


def get_approval(approval_id: int) -> dict[str, Any] | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
    return _approval_row_to_record(row) if row is not None else None


def get_pending_approvals(
    limit: int = 50, offset: int = 0
) -> tuple[list[dict[str, Any]], int]:
    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM approvals WHERE status = 'pending' "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        total = connection.execute(
            "SELECT COUNT(*) FROM approvals WHERE status = 'pending'"
        ).fetchone()[0]
    return [_approval_row_to_record(row) for row in rows], total


def resolve_approval(approval_id: int, status: str) -> dict[str, Any] | None:
    """Mark a pending request ``approved``/``rejected``; single-shot.

    The ``status = 'pending'`` guard means only the first caller flips a row,
    so concurrent approve/reject clicks (or client retries) are race-safe.
    Returns the row whether or not this call performed the transition, or
    ``None`` if the id is unknown.
    """
    resolved_at = datetime.now(timezone.utc).isoformat()
    with _connect() as connection:
        connection.execute(
            "UPDATE approvals SET status = ?, resolved_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (status, resolved_at, approval_id),
        )
        row = connection.execute(
            "SELECT * FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
    return _approval_row_to_record(row) if row is not None else None


def _row_to_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "hash": row["hash"],
        "interface": json.loads(row["interface"]),
        "registered_at": row["registered_at"],
    }


def _approval_row_to_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "recorded_hash": row["recorded_hash"],
        "new_hash": row["new_hash"],
        "new_interface": json.loads(row["new_interface"]),
        "diff": row["diff"],
        "status": row["status"],
        "created_at": row["created_at"],
        "resolved_at": row["resolved_at"],
    }
