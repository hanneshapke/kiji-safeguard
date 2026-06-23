from __future__ import annotations

import threading

import pytest

from kiji_safeguard import (
    MCPSigner,
    aggregate_hash,
    diff_interfaces,
    extract_interface,
)
from tests.conftest import make_server, resolve_pending

TOOL_A = {"type": "tool", "name": "a", "description": "A", "input_schema": {}}
TOOL_B = {"type": "tool", "name": "b", "description": "B", "input_schema": {}}


def test_aggregate_hash_is_order_independent():
    assert aggregate_hash([TOOL_A, TOOL_B]) == aggregate_hash([TOOL_B, TOOL_A])


def test_aggregate_hash_detects_changes():
    tampered = dict(TOOL_A, description="A, but evil")
    assert aggregate_hash([TOOL_A, TOOL_B]) != aggregate_hash([tampered, TOOL_B])


def test_aggregate_hash_is_key_order_independent():
    reordered = {"input_schema": {}, "description": "A", "name": "a", "type": "tool"}
    assert aggregate_hash([TOOL_A]) == aggregate_hash([reordered])


def test_extract_interface_from_fastmcp():
    interface = extract_interface(make_server())
    tools = {c["name"]: c for c in interface if c["type"] == "tool"}
    assert set(tools) == {"add", "shout"}
    assert tools["add"]["description"] == "Add two numbers."
    schema = tools["add"]["input_schema"]
    assert schema["required"] == ["a"]
    assert schema["properties"]["b"]["default"] == 2


def test_extract_interface_passes_dicts_through():
    assert extract_interface([TOOL_A]) == [TOOL_A]


def test_extract_interface_rejects_unknown_objects():
    with pytest.raises(TypeError):
        extract_interface(object())


def test_signer_hash_is_stable_across_instances():
    first = MCPSigner.from_server(make_server())
    second = MCPSigner.from_server(make_server())
    assert first.name == "demo-server"
    assert first.hash == second.hash
    assert len(first.hash) == 64


def test_signer_hash_changes_with_interface():
    base = MCPSigner.from_server(make_server())
    extended = MCPSigner.from_server(make_server(extra_tool=True))
    assert base.hash != extended.hash


def test_diff_interfaces_identical_is_empty():
    assert diff_interfaces([TOOL_A, TOOL_B], [TOOL_B, TOOL_A]) == ""


def test_diff_interfaces_reports_added_component():
    diff = diff_interfaces([TOOL_A], [TOOL_A, TOOL_B])
    assert "+ added tool 'b'" in diff
    assert "removed" not in diff


def test_diff_interfaces_reports_removed_component():
    diff = diff_interfaces([TOOL_A, TOOL_B], [TOOL_A])
    assert "- removed tool 'b'" in diff
    assert "added" not in diff


def test_diff_interfaces_reports_changed_component():
    tampered = dict(TOOL_A, description="A, but evil")
    diff = diff_interfaces([TOOL_A], [tampered])
    assert "~ changed tool 'a'" in diff
    # The unified diff shows the old and new field values.
    assert '-  "description": "A"' in diff
    assert '+  "description": "A, but evil"' in diff


def test_register_and_verify_round_trip(live_registry):
    signer = MCPSigner.from_server(make_server())
    record = signer.register(live_registry)
    assert record["name"] == "demo-server"
    assert record["hash"] == signer.hash

    result = signer.verify(live_registry)
    assert result
    assert result.record["hash"] == signer.hash


def test_verify_detects_interface_change(live_registry):
    MCPSigner.from_server(make_server()).register(live_registry)

    tampered = MCPSigner.from_server(make_server(extra_tool=True))
    result = tampered.verify(live_registry)
    assert not result
    assert result.code == "changed"
    assert "interface changed" in result.reason
    # The diff names the component that appeared since registration.
    assert result.diff
    assert "+ added tool 'sneaky'" in result.diff


def test_verify_unregistered_server(live_registry):
    result = MCPSigner.from_server(make_server(name="ghost")).verify(live_registry)
    assert not result
    assert result.code == "unregistered"
    assert "not registered" in result.reason


def test_verify_unregistered_name_mentions_other_names(live_registry):
    MCPSigner.from_server(make_server()).register(live_registry)

    impostor = MCPSigner.from_server(make_server(), name="impostor")
    result = impostor.verify(live_registry)
    assert not result
    assert result.code == "unregistered"
    assert "registered under: demo-server" in result.reason


def test_verify_unreachable_registry():
    signer = MCPSigner.from_server(make_server())
    with pytest.raises(ConnectionError):
        signer.verify("http://127.0.0.1:1", timeout=0.5)


def _changed_signer(live_registry) -> tuple[MCPSigner, str]:
    """Register a server, return a tampered signer + the recorded hash."""
    original = MCPSigner.from_server(make_server())
    original.register(live_registry)
    tampered = MCPSigner.from_server(make_server(extra_tool=True))
    return tampered, original.hash


def test_request_approval_creates_pending(live_registry):
    tampered, recorded_hash = _changed_signer(live_registry)
    approval_id = tampered.request_approval(
        live_registry, recorded_hash, diff="+ added tool 'sneaky'"
    )
    assert isinstance(approval_id, int)
    # A repeat request joins the same pending row instead of duplicating.
    assert tampered.request_approval(live_registry, recorded_hash) == approval_id


def test_poll_approval_returns_approved(live_registry):
    tampered, recorded_hash = _changed_signer(live_registry)
    approval_id = tampered.request_approval(live_registry, recorded_hash)

    resolver = threading.Thread(
        target=resolve_pending, args=(live_registry, "approve"), daemon=True
    )
    resolver.start()
    decision = tampered.poll_approval(
        live_registry, approval_id, interval=0.05, overall_timeout=5
    )
    resolver.join(timeout=2)
    assert decision == "approved"
    # Approval registered the new interface, so it now verifies.
    assert tampered.verify(live_registry)


def test_poll_approval_returns_rejected(live_registry):
    tampered, recorded_hash = _changed_signer(live_registry)
    approval_id = tampered.request_approval(live_registry, recorded_hash)

    resolver = threading.Thread(
        target=resolve_pending, args=(live_registry, "reject"), daemon=True
    )
    resolver.start()
    decision = tampered.poll_approval(
        live_registry, approval_id, interval=0.05, overall_timeout=5
    )
    resolver.join(timeout=2)
    assert decision == "rejected"


def test_poll_approval_times_out(live_registry):
    tampered, recorded_hash = _changed_signer(live_registry)
    approval_id = tampered.request_approval(live_registry, recorded_hash)
    with pytest.raises(TimeoutError):
        tampered.poll_approval(
            live_registry, approval_id, interval=0.05, overall_timeout=0.2
        )
