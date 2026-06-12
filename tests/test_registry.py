from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kiji_safeguard import aggregate_hash

TOOLS = [
    {"type": "tool", "name": "add", "description": "Add.", "input_schema": {}},
    {"type": "tool", "name": "shout", "description": "Shout.", "input_schema": {}},
]


@pytest.fixture()
def client(registry_db):
    from kiji_safeguard.server.backend.main import app

    with TestClient(app) as test_client:
        yield test_client


def _registration(name: str = "demo-server", tools: list[dict] | None = None) -> dict:
    interface = tools if tools is not None else TOOLS
    return {"name": name, "hash": aggregate_hash(interface), "interface": interface}


def test_register_returns_record_with_summary(client):
    response = client.post("/servers", json=_registration())
    assert response.status_code == 201
    record = response.json()
    assert record["name"] == "demo-server"
    assert record["summary"]["tool_count"] == 2
    assert record["summary"]["tools"] == ["add", "shout"]


def test_register_rejects_mismatched_hash(client):
    body = _registration()
    body["hash"] = "0" * 64
    response = client.post("/servers", json=body)
    assert response.status_code == 400
    assert "does not match" in response.json()["detail"]


def test_register_is_idempotent(client):
    first = client.post("/servers", json=_registration()).json()
    second = client.post("/servers", json=_registration()).json()
    assert first["id"] == second["id"]
    total = client.get("/servers").json()["total"]
    assert total == 1


def test_lookup_by_hash(client):
    registration = _registration()
    client.post("/servers", json=registration)
    response = client.get(f"/servers/{registration['hash']}")
    assert response.status_code == 200
    assert response.json()[0]["name"] == "demo-server"


def test_lookup_unknown_hash_is_404(client):
    assert client.get(f"/servers/{'f' * 64}").status_code == 404


def test_list_servers_filters_by_name(client):
    client.post("/servers", json=_registration("alpha"))
    client.post("/servers", json=_registration("beta"))

    everything = client.get("/servers").json()
    assert everything["total"] == 2

    filtered = client.get("/servers", params={"name": "alpha"}).json()
    assert filtered["total"] == 1
    assert filtered["servers"][0]["name"] == "alpha"


def test_index_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Kiji Safeguard" in response.text
    assert "MCP Registry" in response.text
