"""Sign and verify MCP server interfaces.

The "signature" of an MCP server is a content hash derived from its public
interface: tool names, descriptions and input schemas, plus prompts,
resources and server instructions when present.  There is no key material
and no user identity involved -- a server is registered with the registry
under a *name* together with its interface *hash* and the full interface
description, and verification simply recomputes the hash from the live
server and looks it up.

The hashing scheme follows ``agent-signing``: every component is serialised
as canonical JSON and hashed with SHA-256, the per-component digests are
sorted lexicographically, concatenated and hashed again.  The result is
therefore independent of the order in which tools were declared.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

DEFAULT_REGISTRY_URL = "http://127.0.0.1:8000"


def canonical_json(component: dict[str, Any]) -> str:
    """Serialise a component deterministically."""
    return json.dumps(component, sort_keys=True, separators=(",", ":"), default=str)


def aggregate_hash(components: list[dict[str, Any]]) -> str:
    """Order-independent SHA-256 digest over a list of interface components."""
    digests = sorted(
        hashlib.sha256(canonical_json(component).encode()).hexdigest()
        for component in components
    )
    return hashlib.sha256("".join(digests).encode()).hexdigest()


def _component_key(component: dict[str, Any]) -> tuple[str, ...]:
    """Stable identity for a component, so two interfaces can be aligned.

    Tools and prompts are identified by name, resources by URI and the
    server instructions are a singleton; anything else falls back to its
    canonical form so unknown component types still diff sensibly.
    """
    component_type = str(component.get("type", ""))
    if component_type == "resource":
        return (component_type, str(component.get("uri", "")))
    if component_type == "server_instructions":
        return (component_type,)
    if "name" in component:
        return (component_type, str(component.get("name", "")))
    return (component_type, canonical_json(component))


def _describe(key: tuple[str, ...]) -> str:
    """Human-readable label for a component key, e.g. ``tool 'foo'``."""
    if key[0] == "server_instructions":
        return "server instructions"
    if len(key) > 1:
        return f"{key[0]} {key[1]!r}"
    return key[0]


def diff_interfaces(
    recorded: list[dict[str, Any]], generated: list[dict[str, Any]]
) -> str:
    """Human-readable diff between two interface component lists.

    Components are aligned by :func:`_component_key`; added and removed
    components are reported one per line, and a component present in both
    whose canonical form changed gets a unified diff of its pretty-printed
    JSON (so nested ``input_schema`` / ``output_schema`` changes show up).
    Returns an empty string when the two interfaces are identical.
    """
    recorded_by_key = {_component_key(c): c for c in recorded}
    generated_by_key = {_component_key(c): c for c in generated}
    keys = sorted(
        set(recorded_by_key) | set(generated_by_key),
        key=lambda key: tuple(map(str, key)),
    )

    lines: list[str] = []
    for key in keys:
        old = recorded_by_key.get(key)
        new = generated_by_key.get(key)
        if old is None:
            lines.append(f"+ added {_describe(key)}")
        elif new is None:
            lines.append(f"- removed {_describe(key)}")
        elif canonical_json(old) != canonical_json(new):
            lines.append(f"~ changed {_describe(key)}:")
            lines.extend(
                "  " + line
                for line in difflib.unified_diff(
                    json.dumps(old, indent=2, sort_keys=True).splitlines(),
                    json.dumps(new, indent=2, sort_keys=True).splitlines(),
                    fromfile="recorded",
                    tofile="generated",
                    lineterm="",
                )
            )
    return "\n".join(lines)


def extract_interface(server: Any) -> list[dict[str, Any]]:
    """Extract the public interface of an MCP server as component dicts.

    Detection is duck-typed so any ``FastMCP``-shaped object works: it must
    expose ``_tool_manager`` (and optionally ``_prompt_manager`` /
    ``_resource_manager``) the way ``mcp.server.fastmcp.FastMCP`` does.
    A plain list of dicts is passed through unchanged.
    """
    if isinstance(server, list):
        return [dict(component) for component in server]

    tool_manager = getattr(server, "_tool_manager", None)
    if tool_manager is None:
        raise TypeError(
            "cannot extract an MCP interface from "
            f"{type(server).__name__!r}; expected a FastMCP server "
            "or a list of component dicts"
        )

    components: list[dict[str, Any]] = []
    for tool in tool_manager.list_tools():
        component: dict[str, Any] = {
            "type": "tool",
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": tool.parameters or {},
        }
        # The output (return) schema is part of a tool's contract too: a tool
        # that quietly starts returning a different shape -- extra fields, a
        # changed structure -- is exactly the kind of tampering we want to
        # catch.  FastMCP only populates it for tools with structured output,
        # so unstructured tools (e.g. ``-> str``) keep their previous hash.
        output_schema = getattr(tool, "output_schema", None)
        if output_schema:
            component["output_schema"] = output_schema
        components.append(component)

    prompt_manager = getattr(server, "_prompt_manager", None)
    if prompt_manager is not None:
        for prompt in prompt_manager.list_prompts():
            components.append(
                {
                    "type": "prompt",
                    "name": prompt.name,
                    "description": prompt.description or "",
                    "arguments": [
                        {
                            "name": argument.name,
                            "description": argument.description or "",
                            "required": bool(argument.required),
                        }
                        for argument in (prompt.arguments or [])
                    ],
                }
            )

    resource_manager = getattr(server, "_resource_manager", None)
    if resource_manager is not None:
        for resource in resource_manager.list_resources():
            components.append(
                {
                    "type": "resource",
                    "uri": str(resource.uri),
                    "name": resource.name or "",
                    "description": resource.description or "",
                    "mime_type": resource.mime_type or "",
                }
            )

    instructions = getattr(server, "instructions", None)
    if instructions:
        components.append({"type": "server_instructions", "instructions": instructions})

    return components


def extract_interface_from_listing(
    tools: Any = (),
    prompts: Any = (),
    resources: Any = (),
    instructions: str | None = None,
) -> list[dict[str, Any]]:
    """Extract an MCP interface from wire-format listing results.

    This is the client-side counterpart of :func:`extract_interface`: instead
    of a server's internal managers it consumes the objects an MCP *client*
    receives over the wire -- ``mcp.types.Tool`` / ``Prompt`` / ``Resource``
    from ``tools/list`` etc. plus the ``instructions`` from the initialize
    result.  Access is duck-typed (camelCase wire attributes), so no ``mcp``
    import is needed and the library stays dependency-free.

    The normalisations mirror :func:`extract_interface` exactly, so the
    resulting components -- and therefore :func:`aggregate_hash` -- match the
    hash the server computes for itself.  Fields the server-side extractor
    ignores (titles, icons, annotations, resource templates) are ignored here
    too; adding them on one side only would break that parity.
    """
    components: list[dict[str, Any]] = []
    for tool in tools:
        component: dict[str, Any] = {
            "type": "tool",
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": tool.inputSchema or {},
        }
        # ``outputSchema`` only exists on the wire from mcp >= 1.10; the
        # truthy guard mirrors the server side, where FastMCP only populates
        # it for tools with structured output.
        output_schema = getattr(tool, "outputSchema", None)
        if output_schema:
            component["output_schema"] = output_schema
        components.append(component)

    for prompt in prompts:
        components.append(
            {
                "type": "prompt",
                "name": prompt.name,
                "description": prompt.description or "",
                "arguments": [
                    {
                        "name": argument.name,
                        "description": argument.description or "",
                        # The wire allows ``required`` to be omitted (None);
                        # bool(None) is False, matching FastMCP's default.
                        "required": bool(argument.required),
                    }
                    for argument in (prompt.arguments or [])
                ],
            }
        )

    for resource in resources:
        components.append(
            {
                "type": "resource",
                "uri": str(resource.uri),
                "name": resource.name or "",
                "description": resource.description or "",
                "mime_type": getattr(resource, "mimeType", None) or "",
            }
        )

    if instructions:
        components.append({"type": "server_instructions", "instructions": instructions})

    return components


@dataclass
class VerificationResult:
    """Outcome of a registry lookup for a server's current interface.

    ``code`` is one of ``"ok"``, ``"changed"`` (name registered with a
    different hash), ``"unregistered"`` (name unknown to the registry) or
    ``"error"`` (lookup failed).  ``diff`` is a human-readable description of
    how the live interface differs from the registered one; it is only set
    for ``code="changed"`` results.
    """

    valid: bool
    reason: str
    record: dict[str, Any] | None = None
    code: str = "error"
    diff: str | None = None

    def __bool__(self) -> bool:
        return self.valid


class MCPSigner:
    """Compute, register and verify the interface hash of an MCP server."""

    def __init__(self, name: str, interface: list[dict[str, Any]]) -> None:
        self.name = name
        self.interface = interface

    @classmethod
    def from_server(cls, server: Any, name: str | None = None) -> "MCPSigner":
        resolved = name or getattr(server, "name", None)
        if not resolved:
            raise ValueError("MCP server has no name; pass name= explicitly")
        return cls(name=resolved, interface=extract_interface(server))

    @property
    def hash(self) -> str:
        return aggregate_hash(self.interface)

    def build_record(self) -> dict[str, Any]:
        return {"name": self.name, "hash": self.hash, "interface": self.interface}

    def register(
        self, registry_url: str = DEFAULT_REGISTRY_URL, timeout: float = 10.0
    ) -> dict[str, Any]:
        """Publish this server's name, hash and interface to the registry."""
        status, body = _http_json(
            "POST",
            f"{registry_url.rstrip('/')}/servers",
            payload=self.build_record(),
            timeout=timeout,
        )
        if status not in (200, 201):
            raise ValueError(f"registry rejected registration ({status}): {body}")
        return body

    def verify(
        self, registry_url: str = DEFAULT_REGISTRY_URL, timeout: float = 10.0
    ) -> VerificationResult:
        """Check the live interface hash against the registry.

        Valid only when the recomputed hash is registered under this
        server's name.  When the name is registered with a different hash
        the interface has changed since registration (``code="changed"``);
        when the name is unknown the server was never registered
        (``code="unregistered"``).
        """
        base = registry_url.rstrip("/")
        status, body = _http_json("GET", f"{base}/servers/{self.hash}", timeout=timeout)
        records: list[dict[str, Any]] = []
        if status == 200:
            records = body if isinstance(body, list) else [body]
            for record in records:
                if record.get("name") == self.name:
                    return VerificationResult(
                        valid=True,
                        reason="interface hash matches registered record",
                        record=record,
                        code="ok",
                    )
        elif status != 404:
            return VerificationResult(
                valid=False,
                reason=f"registry lookup failed ({status}): {body}",
                code="error",
            )

        # The hash is not registered under this name; is the name known at all?
        query = urllib.parse.urlencode({"name": self.name})
        status, body = _http_json("GET", f"{base}/servers?{query}", timeout=timeout)
        if status == 200 and isinstance(body, dict) and body.get("servers"):
            registered = body["servers"][0]
            return VerificationResult(
                valid=False,
                reason=(
                    f"interface changed: {self.name!r} is registered with hash "
                    f"{registered.get('hash')}, but the live interface hashes "
                    f"to {self.hash}"
                ),
                record=registered,
                code="changed",
                diff=diff_interfaces(registered.get("interface") or [], self.interface),
            )

        other_names = sorted({str(record.get("name")) for record in records})
        suffix = (
            f" (the same interface is registered under: {', '.join(other_names)})"
            if other_names
            else ""
        )
        return VerificationResult(
            valid=False,
            reason=f"server {self.name!r} is not registered{suffix}",
            code="unregistered",
        )

    def request_approval(
        self,
        registry_url: str = DEFAULT_REGISTRY_URL,
        recorded_hash: str | None = None,
        diff: str | None = None,
        timeout: float = 10.0,
    ) -> int:
        """Open a pending approval request for this changed interface.

        Returns the request id to poll.  Idempotent on the registry side: a
        repeated request for the same ``(name, hash)`` joins the existing
        pending row rather than creating a duplicate.
        """
        status, body = _http_json(
            "POST",
            f"{registry_url.rstrip('/')}/approvals",
            payload={
                "name": self.name,
                "recorded_hash": recorded_hash,
                "new_hash": self.hash,
                "new_interface": self.interface,
                "diff": diff or "",
            },
            timeout=timeout,
        )
        if status not in (200, 201) or not isinstance(body, dict) or "id" not in body:
            raise ValueError(f"registry rejected approval request ({status}): {body}")
        return int(body["id"])

    def poll_approval(
        self,
        registry_url: str = DEFAULT_REGISTRY_URL,
        approval_id: int = 0,
        interval: float = 3.0,
        overall_timeout: float = 1800.0,
        per_request_timeout: float = 10.0,
    ) -> str:
        """Block until an approval request resolves; return its final status.

        Returns ``"approved"`` or ``"rejected"``.  Transient connection errors
        (e.g. the registry restarting) are swallowed and retried until the
        overall deadline.  Raises :class:`TimeoutError` if the request is still
        pending when the deadline passes.
        """
        base = registry_url.rstrip("/")
        url = f"{base}/approvals/{approval_id}"
        deadline = time.monotonic() + overall_timeout
        while True:
            try:
                status, body = _http_json("GET", url, timeout=per_request_timeout)
                if status == 200 and isinstance(body, dict):
                    decision = body.get("status")
                    if decision in ("approved", "rejected"):
                        return decision
            except ConnectionError:
                pass  # registry briefly unreachable; retry until the deadline
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"approval request {approval_id} for {self.name!r} was not "
                    f"resolved within {overall_timeout:.0f}s"
                )
            time.sleep(min(interval, remaining))


def _http_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> tuple[int, Any]:
    """Tiny stdlib JSON HTTP client so the library needs no dependencies."""
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            body = raw.decode(errors="replace")
        return exc.code, body
    except urllib.error.URLError as exc:
        raise ConnectionError(f"could not reach registry at {url}: {exc.reason}") from exc
