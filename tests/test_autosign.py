from __future__ import annotations

import importlib
import sys
import threading

import pytest

import kiji_safeguard.autosign as autosign
from kiji_safeguard import MCPSigner
from kiji_safeguard.signer import _http_json
from tests.conftest import make_server, resolve_pending


def test_already_imported_fastmcp_is_patched():
    # mcp is imported by conftest, so install() must have patched in place.
    autosign.install()
    from mcp.server.fastmcp import FastMCP

    assert getattr(FastMCP.run, autosign._PATCH_MARKER, False)


def test_import_hook_patches_fresh_import(monkeypatch):
    saved = {name: mod for name, mod in sys.modules.items() if name.startswith("mcp")}
    for name in saved:
        monkeypatch.delitem(sys.modules, name)
    autosign.uninstall()
    autosign.install()
    try:
        module = importlib.import_module("mcp.server.fastmcp")
        assert getattr(module.FastMCP.run, autosign._PATCH_MARKER, False)
    finally:
        autosign.uninstall()
        for name in [n for n in sys.modules if n.startswith("mcp")]:
            del sys.modules[name]
        sys.modules.update(saved)
        autosign.install()


def test_install_is_idempotent():
    autosign.install()
    autosign.install()
    finders = [f for f in sys.meta_path if isinstance(f, autosign._AutosignFinder)]
    assert len(finders) <= 1
    from mcp.server.fastmcp import FastMCP

    # Patching twice must not stack wrappers.
    inner = FastMCP.run.__wrapped__
    assert not getattr(inner, autosign._PATCH_MARKER, False)


def test_on_run_registers_server(live_registry, monkeypatch):
    monkeypatch.setenv("KIJI_SAFEGUARD_MODE", "register")
    monkeypatch.setenv("KIJI_SAFEGUARD_REGISTRY", live_registry)

    server = make_server()
    autosign._on_run(server)

    result = MCPSigner.from_server(server).verify(live_registry)
    assert result


def test_on_run_verifies_registered_server(live_registry, monkeypatch, capsys):
    server = make_server()
    MCPSigner.from_server(server).register(live_registry)

    monkeypatch.setenv("KIJI_SAFEGUARD_MODE", "verify")
    monkeypatch.setenv("KIJI_SAFEGUARD_REGISTRY", live_registry)
    autosign._on_run(server)
    assert "verified 'demo-server'" in capsys.readouterr().err


def test_on_run_auto_mode_registers_on_first_sight(live_registry, monkeypatch, capsys):
    monkeypatch.delenv("KIJI_SAFEGUARD_MODE", raising=False)
    monkeypatch.setenv("KIJI_SAFEGUARD_REGISTRY", live_registry)

    server = make_server()
    autosign._on_run(server)
    assert "first sight of 'demo-server'" in capsys.readouterr().err

    autosign._on_run(server)
    assert "verified 'demo-server'" in capsys.readouterr().err


def test_on_run_auto_mode_never_reregisters_changed_interface(
    live_registry, monkeypatch, capsys
):
    monkeypatch.delenv("KIJI_SAFEGUARD_MODE", raising=False)
    monkeypatch.setenv("KIJI_SAFEGUARD_REGISTRY", live_registry)
    MCPSigner.from_server(make_server()).register(live_registry)

    autosign._on_run(make_server(extra_tool=True))
    err = capsys.readouterr().err
    assert "WARNING" in err and "interface changed" in err

    # The changed interface must still fail verification (it was not adopted).
    result = MCPSigner.from_server(make_server(extra_tool=True)).verify(live_registry)
    assert not result


def test_on_run_warns_on_changed_interface(live_registry, monkeypatch, capsys):
    MCPSigner.from_server(make_server()).register(live_registry)

    monkeypatch.setenv("KIJI_SAFEGUARD_MODE", "verify")
    monkeypatch.setenv("KIJI_SAFEGUARD_REGISTRY", live_registry)
    autosign._on_run(make_server(extra_tool=True))
    err = capsys.readouterr().err
    assert "WARNING" in err and "interface changed" in err
    # The diff of what changed is logged alongside the warning.
    assert "interface diff" in err
    assert "+ added tool 'sneaky'" in err


def test_on_run_enforce_raises_on_failure(live_registry, monkeypatch):
    monkeypatch.setenv("KIJI_SAFEGUARD_MODE", "verify")
    monkeypatch.setenv("KIJI_SAFEGUARD_REGISTRY", live_registry)
    monkeypatch.setenv("KIJI_SAFEGUARD_ENFORCE", "1")

    with pytest.raises(autosign.SafeguardError):
        autosign._on_run(make_server(name="never-registered"))


def test_on_run_off_mode_does_nothing(monkeypatch, capsys):
    monkeypatch.setenv("KIJI_SAFEGUARD_MODE", "off")
    monkeypatch.setenv("KIJI_SAFEGUARD_REGISTRY", "http://127.0.0.1:1")
    autosign._on_run(make_server())
    assert capsys.readouterr().err == ""


def _approval_env(monkeypatch, live_registry, *, timeout="5"):
    monkeypatch.setenv("KIJI_SAFEGUARD_MODE", "approval")
    monkeypatch.setenv("KIJI_SAFEGUARD_REGISTRY", live_registry)
    monkeypatch.setenv("KIJI_SAFEGUARD_APPROVAL_POLL_INTERVAL", "0.05")
    monkeypatch.setenv("KIJI_SAFEGUARD_APPROVAL_TIMEOUT", timeout)


def test_approval_mode_approved_proceeds(live_registry, monkeypatch, capsys):
    MCPSigner.from_server(make_server()).register(live_registry)
    _approval_env(monkeypatch, live_registry)

    resolver = threading.Thread(
        target=resolve_pending, args=(live_registry, "approve"), daemon=True
    )
    resolver.start()
    autosign._on_run(make_server(extra_tool=True))  # must not raise
    resolver.join(timeout=3)

    assert "approved" in capsys.readouterr().err
    # The newly-approved interface is now trusted.
    assert MCPSigner.from_server(make_server(extra_tool=True)).verify(live_registry)


def test_approval_mode_rejected_raises_without_enforce(live_registry, monkeypatch):
    MCPSigner.from_server(make_server()).register(live_registry)
    _approval_env(monkeypatch, live_registry)
    monkeypatch.delenv("KIJI_SAFEGUARD_ENFORCE", raising=False)

    resolver = threading.Thread(
        target=resolve_pending, args=(live_registry, "reject"), daemon=True
    )
    resolver.start()
    with pytest.raises(autosign.SafeguardError):
        autosign._on_run(make_server(extra_tool=True))
    resolver.join(timeout=3)


def test_approval_mode_timeout_fails_under_enforce(live_registry, monkeypatch):
    MCPSigner.from_server(make_server()).register(live_registry)
    _approval_env(monkeypatch, live_registry, timeout="0.2")
    monkeypatch.setenv("KIJI_SAFEGUARD_ENFORCE", "1")

    with pytest.raises(autosign.SafeguardError):
        autosign._on_run(make_server(extra_tool=True))


def test_approval_mode_leaves_unregistered_untouched(live_registry, monkeypatch, capsys):
    # approval mode only gates a *changed* interface; an unknown server still
    # just fails/warns and never opens an approval request.
    _approval_env(monkeypatch, live_registry)
    autosign._on_run(make_server(name="ghost"))

    err = capsys.readouterr().err
    assert "WARNING" in err and "not registered" in err
    _, body = _http_json("GET", live_registry.rstrip("/") + "/approvals?status=pending")
    assert body["total"] == 0


def test_patched_run_invokes_safeguard_before_serving(monkeypatch):
    autosign.install()
    from mcp.server.fastmcp import FastMCP

    calls = []
    monkeypatch.setattr(autosign, "_on_run", lambda server: calls.append(server.name))
    monkeypatch.setattr(FastMCP.run, "__wrapped__", lambda self, *a, **k: None)

    make_server(name="patched").run()
    assert calls == ["patched"]
