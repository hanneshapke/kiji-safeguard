from __future__ import annotations

import socket
import threading
import time

import pytest
import uvicorn
from mcp.server.fastmcp import FastMCP


@pytest.fixture()
def registry_db(tmp_path, monkeypatch):
    """Point the registry at a fresh SQLite file."""
    db_path = tmp_path / "registry.db"
    monkeypatch.setenv("KIJI_SAFEGUARD_DB", str(db_path))
    return db_path


@pytest.fixture()
def live_registry(registry_db):
    """A real registry served by uvicorn on a random localhost port."""
    from server.backend.main import app

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("registry did not start in time")
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def make_server(name: str = "demo-server", extra_tool: bool = False) -> FastMCP:
    """Build a small FastMCP server for tests."""
    mcp = FastMCP(name)

    @mcp.tool()
    def add(a: int, b: int = 2) -> int:
        """Add two numbers."""
        return a + b

    @mcp.tool()
    def shout(text: str) -> str:
        """Uppercase some text."""
        return text.upper()

    if extra_tool:

        @mcp.tool()
        def sneaky(path: str) -> str:
            """Read a file."""
            return path

    return mcp
