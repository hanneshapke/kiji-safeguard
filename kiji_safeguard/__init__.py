"""kiji-safeguard: sign and verify MCP server interfaces.

Programmatic use::

    from kiji_safeguard import MCPSigner

    signer = MCPSigner.from_server(mcp)
    signer.register("http://127.0.0.1:8000")
    result = signer.verify("http://127.0.0.1:8000")

Magic use (one line, zero code changes)::

    import kiji_safeguard.autosign  # noqa: F401
"""

from .signer import (
    DEFAULT_REGISTRY_URL,
    MCPSigner,
    VerificationResult,
    aggregate_hash,
    canonical_json,
    extract_interface,
)

__all__ = [
    "DEFAULT_REGISTRY_URL",
    "MCPSigner",
    "VerificationResult",
    "aggregate_hash",
    "canonical_json",
    "extract_interface",
]

__version__ = "0.1.1"
