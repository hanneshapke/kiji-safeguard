from __future__ import annotations

import pytest

from kiji_safeguard import MCPSigner, aggregate_hash, extract_interface
from tests.conftest import make_server

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
    assert "interface changed" in result.reason


def test_verify_unregistered_server(live_registry):
    result = MCPSigner.from_server(make_server(name="ghost")).verify(live_registry)
    assert not result
    assert "not registered" in result.reason


def test_verify_detects_name_mismatch(live_registry):
    MCPSigner.from_server(make_server()).register(live_registry)

    impostor = MCPSigner.from_server(make_server(), name="impostor")
    result = impostor.verify(live_registry)
    assert not result
    assert "different name" in result.reason


def test_verify_unreachable_registry():
    signer = MCPSigner.from_server(make_server())
    with pytest.raises(ConnectionError):
        signer.verify("http://127.0.0.1:1", timeout=0.5)
