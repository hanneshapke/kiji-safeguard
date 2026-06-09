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

import hashlib
import json
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
        components.append(
            {
                "type": "tool",
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.parameters or {},
            }
        )

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


@dataclass
class VerificationResult:
    """Outcome of a registry lookup for a server's current interface."""

    valid: bool
    reason: str
    record: dict[str, Any] | None = None

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
        the interface has changed since registration.
        """
        base = registry_url.rstrip("/")
        status, body = _http_json("GET", f"{base}/servers/{self.hash}", timeout=timeout)
        if status == 200:
            records = body if isinstance(body, list) else [body]
            for record in records:
                if record.get("name") == self.name:
                    return VerificationResult(
                        valid=True,
                        reason="interface hash matches registered record",
                        record=record,
                    )
            other_names = sorted({str(record.get("name")) for record in records})
            return VerificationResult(
                valid=False,
                reason=(
                    "interface hash is registered, but under different "
                    f"name(s): {', '.join(other_names)}"
                ),
            )

        if status != 404:
            return VerificationResult(
                valid=False, reason=f"registry lookup failed ({status}): {body}"
            )

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
            )
        return VerificationResult(
            valid=False, reason=f"server {self.name!r} is not registered"
        )


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
