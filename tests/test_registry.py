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


# --- approval workflow ------------------------------------------------------


def _approval_body(
    name: str = "demo-server",
    tools: list[dict] | None = None,
    recorded_hash: str | None = None,
    diff: str = "",
) -> dict:
    interface = tools if tools is not None else TOOLS
    return {
        "name": name,
        "recorded_hash": recorded_hash,
        "new_hash": aggregate_hash(interface),
        "new_interface": interface,
        "diff": diff,
    }


def test_create_approval_returns_pending(client):
    response = client.post("/approvals", json=_approval_body(diff="+ added tool x"))
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["summary"]["tool_count"] == 2
    assert body["diff"] == "+ added tool x"


def test_create_approval_rejects_mismatched_hash(client):
    body = _approval_body()
    body["new_hash"] = "0" * 64
    response = client.post("/approvals", json=body)
    assert response.status_code == 400
    assert "does not match" in response.json()["detail"]


def test_create_approval_is_idempotent_per_pending(client):
    first = client.post("/approvals", json=_approval_body()).json()
    second = client.post("/approvals", json=_approval_body()).json()
    assert first["id"] == second["id"]
    assert client.get("/approvals", params={"status": "pending"}).json()["total"] == 1


def test_list_pending_excludes_resolved(client):
    first = client.post("/approvals", json=_approval_body("alpha")).json()
    client.post("/approvals", json=_approval_body("beta"))
    client.post(f"/approvals/{first['id']}/approve")

    pending = client.get("/approvals", params={"status": "pending"}).json()
    assert pending["total"] == 1
    assert pending["approvals"][0]["name"] == "beta"


def test_approve_registers_interface_and_marks_approved(client):
    body = _approval_body()
    created = client.post("/approvals", json=body).json()

    response = client.post(f"/approvals/{created['id']}/approve")
    assert response.status_code == 200
    assert response.json()["status"] == "approved"

    lookup = client.get(f"/servers/{body['new_hash']}")
    assert lookup.status_code == 200
    assert lookup.json()[0]["name"] == "demo-server"


def test_reject_marks_rejected_without_registering(client):
    body = _approval_body()
    created = client.post("/approvals", json=body).json()

    response = client.post(f"/approvals/{created['id']}/reject")
    assert response.json()["status"] == "rejected"
    assert client.get(f"/servers/{body['new_hash']}").status_code == 404


def test_approve_supersedes_recorded_hash(client):
    """Approving a change deprecates the old hash and links the two records."""
    old = client.post("/servers", json=_registration()).json()
    assert old["status"] == "active"

    new_tools = TOOLS + [
        {"type": "tool", "name": "extra", "description": "New.", "input_schema": {}}
    ]
    body = _approval_body(tools=new_tools, recorded_hash=old["hash"])
    created = client.post("/approvals", json=body).json()
    client.post(f"/approvals/{created['id']}/approve")

    old_after = client.get(f"/servers/{old['hash']}").json()[0]
    assert old_after["status"] == "deprecated"
    assert old_after["superseded_by"] == body["new_hash"]
    assert old_after["superseded_at"]
    assert old_after["supersedes"] is None

    new_after = client.get(f"/servers/{body['new_hash']}").json()[0]
    assert new_after["status"] == "active"
    assert new_after["supersedes"] == old["hash"]
    assert new_after["superseded_by"] is None


def test_approve_without_recorded_hash_supersedes_nothing(client):
    body = _approval_body()
    created = client.post("/approvals", json=body).json()
    client.post(f"/approvals/{created['id']}/approve")

    record = client.get(f"/servers/{body['new_hash']}").json()[0]
    assert record["status"] == "active"
    assert record["supersedes"] is None
    assert record["superseded_by"] is None


def test_approve_is_idempotent(client):
    created = client.post("/approvals", json=_approval_body()).json()
    client.post(f"/approvals/{created['id']}/approve")
    second = client.post(f"/approvals/{created['id']}/approve")
    assert second.json()["status"] == "approved"
    assert client.get("/servers").json()["total"] == 1


def test_get_approval_for_polling(client):
    created = client.post("/approvals", json=_approval_body()).json()
    response = client.get(f"/approvals/{created['id']}")
    assert response.status_code == 200
    assert response.json()["status"] == "pending"


def test_resolve_unknown_approval_is_404(client):
    assert client.get("/approvals/999").status_code == 404
    assert client.post("/approvals/999/approve").status_code == 404
    assert client.post("/approvals/999/reject").status_code == 404
