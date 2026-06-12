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


def _row_to_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "hash": row["hash"],
        "interface": json.loads(row["interface"]),
        "registered_at": row["registered_at"],
    }
