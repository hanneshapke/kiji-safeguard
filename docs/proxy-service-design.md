# Design: a verifying MCP proxy

Status: **draft / sketch** · Owner: TBD · Target branch: `claude/mcp-safeguard-usage-3bfadi`

## 1. Problem

`kiji-safeguard` today protects MCP interfaces through a single Python import
(`import kiji_safeguard.autosign`). The import patches `FastMCP.run` on the
**server** side (self-attestation / drift detection) and
`ClientSession.initialize` on the **agent** side — and the agent side is the
real security boundary: it hashes what actually arrives over the wire and
checks it against the registry before any tool reaches the model.

That boundary is only reachable from a **Python** client process. The clients
most people actually drive MCP servers from — **Claude Code** (TypeScript) and
**Zed** (Rust), plus Cursor and others — can't run the import, so they can't
get the wire-level check. The only workarounds available today are:

- adding the import to a server you own (self-attestation, not a boundary), or
- wrapping a server launch with `kiji-safeguard verify <file.py>` — which only
  works for **Python FastMCP source files**, leaving `npx`/node/third-party
  and any binary-only server completely uncovered.

A **verifying proxy** closes both gaps: it puts the agent-side check into a
standalone process the client points at, and it verifies the *wire* interface,
so the client's language and the upstream's language stop mattering.

## 2. Goals / non-goals

**Goals**

- Give non-Python MCP clients (Claude Code, Zed, Cursor, …) the same
  verify-before-tools-reach-the-model guarantee Python agents get today.
- Cover upstreams of any language/transport by hashing the wire interface,
  not source.
- Reuse the existing registry, hashing, verify/register, and approval flow
  unchanged.
- Fail closed and legibly: when verification fails, the user should understand
  *why*, not just see a dead server.
- Close the README's "known limitation" by pinning the **expected** server
  name (and optionally hash/registry) per upstream in proxy config.

**Non-goals**

- Replacing the import. The one-liner stays the right tool for Python apps and
  for server-side drift detection. The proxy is additive.
- Authenticating *users* or encrypting transport. The signature is still a
  content hash of the public interface — no keys, no identity. Unchanged.
- Deep inspection of tool *call arguments or results*. The unit of trust
  remains the interface hash (names, descriptions, schemas, prompts,
  resources, instructions).

## 3. Architecture

```
Claude Code / Zed / Cursor          kiji-safeguard proxy                 upstream MCP server
        (any client)        ──MCP──▶  (verifying bridge)    ──MCP──▶   (any language/transport)
                                          │
                                          │ list tools/prompts/resources from the wire,
                                          │ rebuild interface, hash, verify vs. registry
                                          ▼
                                    kiji-safeguard registry  ◀── web UI / approvals
```

The proxy is an MCP server to the downstream client and an MCP **client** to
the upstream server. On the upstream `initialize` handshake it performs the
exact extract → hash → verify the existing `_on_initialize` hook already does
(`kiji_safeguard/autosign.py`), then forwards (or refuses to forward) the
interface to the client. After the handshake it is a transparent JSON-RPC pump:
tool calls and results pass through untouched.

Two transports, same core:

- **stdio** — the proxy is launched by the client and spawns the upstream as a
  subprocess, piping JSON-RPC both ways. This is the Claude Code / Zed *local
  server* case.
- **HTTP/SSE** — the proxy is an HTTP MCP endpoint the client's URL points at;
  it connects to a remote upstream URL.

### Reuse map

| Concern | Source today | In the proxy |
| --- | --- | --- |
| Interface extraction from a live listing | `signer.extract_interface_from_listing` | reused as-is |
| Hashing (order-independent SHA-256) | `signer.aggregate_hash` | reused as-is |
| Register / verify / approval HTTP | `MCPSigner`, `_apply_policy`, `_await_approval` | reused as-is |
| "Verify a connection's wire interface" | `autosign._on_initialize` | reused — proxy's upstream client is a `ClientSession` with `autosign` imported |
| Registry + web UI + approvals | `kiji_safeguard/server/**` | unchanged |
| Env-var policy (`MODE`/`ENFORCE`/`REGISTRY`/approval) | `autosign._mode` etc. | unchanged; same vars drive the proxy |

The genuinely new code is the **bridge**: a bidirectional JSON-RPC pump, the
upstream-target + name-pinning config, and a `kiji-safeguard proxy` CLI
subcommand.

## 4. Use cases

1. **Claude Code, local stdio server.** User sets the configured `command` to
   `kiji-safeguard proxy -- npx some-mcp-server`. First run TOFU-registers, every
   run verifies; a rug-pulled `npx` package trips the seal before its tools
   reach the model.
2. **Zed, same shape.** Identical, via Zed's `context_servers.command`.
3. **Remote HTTP server.** Client URL points at the proxy; proxy verifies a
   hosted upstream over HTTP/SSE. Useful for SaaS MCP endpoints the client
   can't otherwise vet.
4. **Human-gated change (approval mode).** A changed interface holds the
   handshake open while a reviewer approves/rejects in the web UI; on approve
   the new interface is pinned and the connection proceeds.
5. **Name-pinned upstream.** Operator pins `expected_name = "stock-prices"`;
   a tampered server that renames itself fails instead of being TOFU-adopted —
   the README's known limitation, closed.
6. **Fleet / shared registry.** One registry, many developers behind proxies,
   all verifying against the same transparency log; drift anywhere is visible
   in one web UI.

Out of scope (today): clients that speak a transport the proxy doesn't yet
implement, and verifying a server's *runtime behavior* beyond its declared
interface.

## 5. Behavior on a failed verify

Driven by the existing `KIJI_SAFEGUARD_MODE` / `KIJI_SAFEGUARD_ENFORCE`:

- **warn (enforce off)** — forward the upstream interface unchanged, emit the
  diff to stderr and (optionally) the registry. Matches today's default.
- **tripwire (enforce on)** *[recommended default when enforcing]* — complete
  the downstream handshake but expose **zero upstream tools** plus a single
  `kiji_safeguard_blocked` notice tool/resource explaining what changed and
  where to approve it. The user sees *why* instead of a silent dead server.
- **hard-fail (enforce on, strict)** — refuse the connection; the client sees
  the server as unavailable. Simplest, least legible.
- **approval** — hold the handshake (up to `KIJI_SAFEGUARD_APPROVAL_TIMEOUT`)
  pending a human decision; reuse `_await_approval`. Approve → proceed with the
  new interface pinned; reject → hard-fail regardless of enforce.

## 6. Phases

Incremental; each phase is independently useful and shippable.

### Phase 0 — design + scaffolding *(S)*
This doc, a `[proxy]` extra, a `kiji-safeguard proxy` subcommand stub, and an
end-to-end test harness (a fixture upstream + a fixture downstream client).

### Phase 1 — stdio passthrough proxy, verify-only *(M)*
Spawn the upstream subprocess, pump JSON-RPC both ways, verify on `initialize`
via the existing client path, **warn** on mismatch (no enforcement yet). Proves
the bridge and the wire hash matching the server's self-computed hash.
Deliverable: Claude Code / Zed can point at the proxy and see verify logs.

### Phase 2 — enforcement + tripwire *(M)*
`KIJI_SAFEGUARD_ENFORCE` wired to hard-fail and tripwire behaviors; the
`kiji_safeguard_blocked` notice surface. This is the first phase that is a real
security boundary for non-Python clients.

### Phase 3 — name pinning + per-upstream config *(M)*
A small config (CLI flags and/or a file) mapping a proxy instance to an
upstream target, expected name, registry URL, and mode. Closes the rename
limitation. Decide config surface: pure CLI flags vs. a `kiji-safeguard.toml`.

### Phase 4 — approval mode through the proxy *(S–M)*
Hold the handshake open on a changed interface; reuse `_await_approval` and the
existing Pending Approvals UI. Mostly wiring, since the flow already exists.

### Phase 5 — HTTP/SSE transport *(L)*
Proxy as an HTTP MCP endpoint connecting to a remote upstream URL. More moving
parts (streaming, reconnection, auth passthrough) than stdio; deferred until
the stdio path is solid.

### Phase 6 — docs, demo, hardening *(M)*
A README "Using it with Claude Code / Zed" section, a VHS demo tape of a
proxied rug-pull (mirrors the existing `demo/`), and robustness work:
subprocess lifecycle, partial-message framing, large/paginated listings,
upstream crash propagation.

## 7. Complexity & risk

| Area | Complexity | Notes / risk |
| --- | --- | --- |
| Verify core | **Low** | Already exists and is transport-independent; the proxy calls into it. |
| stdio bridge | **Medium** | JSON-RPC framing, subprocess lifecycle, clean stdout (diagnostics must stay on stderr), backpressure. The fiddly-but-bounded heart of Phase 1. |
| Enforcement / tripwire | **Medium** | Synthesizing a downstream interface (zero tools + notice) and keeping the handshake well-formed for picky clients. |
| Name pinning / config | **Medium** | Mostly design: how operators express targets without it becoming a second config language. |
| Approval passthrough | **Low–Med** | Flow exists; just needs to gate the handshake instead of a Python connection. |
| HTTP/SSE transport | **High** | Streaming, reconnection, auth headers, SSE semantics. The big rock; isolate behind the transport seam. |
| Dependency footprint | **Medium (decision)** | The proxy needs the `mcp` SDK (FastMCP/anyio). Keep it behind a `[proxy]` extra so the stdlib-only client library stays dependency-free. |
| Operational | **Medium** | The proxy is now in the critical path; a crash = unreachable server. Fail-closed is intended, but lifecycle/supervision and clear errors matter (Phase 6). |

**Overall:** the security logic is cheap because it already exists; the cost is
in the **bridge plumbing and transport coverage**, concentrated in Phases 1, 2,
and 5. A useful, real boundary for Claude Code / Zed is reachable by the end of
**Phase 2** (stdio + enforcement); everything after widens coverage and polish.

## 8. Open questions

- **Config surface** — CLI flags only, or a `kiji-safeguard.toml` mapping
  proxy → upstream/expected-name/mode? Affects Phase 3.
- **Tripwire shape** — a notice *tool*, a *resource*, or server `instructions`?
  Which do Claude Code and Zed surface most legibly to the user?
- **Multiplexing** — one proxy process per upstream (simple, matches one
  `command` per server) vs. one proxy fronting many upstreams (fewer processes,
  but a router and naming scheme). Start one-per-upstream.
- **Latency budget** — verification adds one registry round-trip at connect
  time; tool calls are pass-through. Confirm the connect-time cost is invisible
  in practice and consider caching the last good verdict per `(name, hash)`.
- **Coexistence** — when both the import (server side) and the proxy verify the
  same server, the registry sees two registrations with the same hash
  (idempotent today). Confirm that stays clean.

## 9. Recommendation

Build **Phases 0–2 on stdio first** — that delivers the actual security
boundary for Claude Code and Zed with the least new surface, reusing the entire
verify/registry stack. Treat HTTP/SSE (Phase 5) as a separate, later effort
behind the same transport seam. Keep the proxy behind a `[proxy]` extra so the
client library stays dependency-free, and keep the import path first-class for
Python apps — the two are complementary, not a migration.
