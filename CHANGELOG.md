## Unreleased

### Feat

- Verifying MCP proxy (`kiji-safeguard proxy`) so non-Python clients such as
  Claude Code and Zed get wire-level interface verification: transparent
  forward on success, tripwire (or hard-fail) on a blocked interface, approval
  mode, and expected-name pinning. Adds the `[proxy]` extra.
- Proxy HTTP/SSE transport: front a remote upstream with `--upstream-url`
  (Streamable HTTP or `--upstream-transport sse`, `--header` for auth) and
  re-serve it as a Streamable HTTP endpoint (`--http-host/-port/-path`).

## v0.3.0 (2026-06-22)

### Feat

- approval step (#9)
- individual approval (#8)

## v0.2.0 (2026-06-12)

### Feat

- Add client-side safeguard hook for MCP agent connections (#6)
- MCP server interface signing and verification (#1)
