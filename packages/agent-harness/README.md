# Agent Harness — Reference Implementation

This directory holds the **cited** half of the agent harness — files that
are copied byte-for-byte into each new backend. The rest of the harness
(backend-agnostic modules) lives in `culture/clients/shared/` and is
imported, not copied. See
[`docs/architecture/shared-vs-cited.md`](../../docs/architecture/shared-vs-cited.md)
for the full tier rule and fork-back procedure.

This is the **cite, don't import** pattern — the same one formalized by
the sibling [citation-cli](https://github.com/OriNachum/citation-cli)
project (formerly `assimilai`).

## Bootstrapping a new (5th) backend

1. Copy the cited files from this directory into
   `culture/clients/<your-backend>/`:
   - `daemon.py`
   - `config.py`
   - `constants.py`
   - `culture.yaml`
2. Write your own `agent_runner.py` — the SDK/CLI call site itself.
3. Write your own `supervisor.py` — backend-specific liveness logic.
4. Adapt `daemon.py` to wire up your runner in `_start_agent_runner()`.
5. Import the shared modules from `culture.clients.shared` (do not copy
   them):

   ```python
   from culture.clients.shared.attention import AttentionTracker
   from culture.clients.shared.irc_transport import IRCTransport
   from culture.clients.shared.telemetry import TelemetryConfig
   # ...etc.
   ```

6. Write your `skill/SKILL.md` with IRC commands for your agent.

## Cited (copy these)

These files have backend-specific behavior. Copy them into your backend
directory and adapt as needed. The
[all-backends rule](../../CLAUDE.md#citation-pattern) applies — when you
change a cited file, propagate to all four backends.

| File | Purpose | Adapt? |
|------|---------|--------|
| `daemon.py` | Orchestrates IRC + agent + IPC | Yes — wire up your runner |
| `config.py` | YAML config loader + per-backend defaults | Yes — add backend-specific fields |
| `constants.py` | Per-backend literals (timeouts, channel names) | Yes — set your values |
| `culture.yaml` | Reference agent config | Yes — set your nick, channels |

The per-backend `agent_runner.py` and `supervisor.py` are not in this
directory because they're "yours to write" from scratch — there is no
generic reference for the SDK call site itself.

## Imported from culture.clients.shared (do not copy)

These modules are backend-agnostic and live once at
`culture/clients/shared/`. Import them; do not vendor them per backend.
A guard test (`tests/harness/test_no_per_backend_copy_of_shared_modules.py`)
fails CI if a per-backend copy reappears without going through the
fork-back procedure.

| Module | Purpose |
|--------|---------|
| `culture.clients.shared.attention` | `AttentionTracker` state machine |
| `culture.clients.shared.message_buffer` | Ring buffer for channel messages |
| `culture.clients.shared.ipc` | JSON Lines whisper-protocol frames |
| `culture.clients.shared.telemetry` | OTel harness metrics + spans |
| `culture.clients.shared.irc_transport` | IRC client (asyncio, RFC 2812) |
| `culture.clients.shared.socket_server` | Unix-socket whisper plumbing |
| `culture.clients.shared.webhook` | HTTP + IRC alerting |
| `culture.clients.shared.webhook_types` | `WebhookConfig` dataclass |

If one of these ever needs to start diverging for your backend, follow
the fork-back procedure in
[`docs/architecture/shared-vs-cited.md`](../../docs/architecture/shared-vs-cited.md)
— don't silently `cp` it into your backend directory.

## Telemetry

The `telemetry:` block in `culture.yaml` controls OpenTelemetry export of LLM
call metrics (`culture.harness.llm.calls`, `culture.harness.llm.call.duration`,
`culture.harness.llm.tokens.input/output`) and three spans that extend the
server-side trace tree across the harness boundary. It is **off by default** —
set `enabled: true` once your OTLP collector is running. See the operator guide
at [`docs/agentirc/harness-telemetry.html`](../../docs/agentirc/harness-telemetry.html)
for full configuration details and an end-to-end test recipe.

## Reference implementation

See `culture/clients/claude/` for the working Claude backend — it was
the original source for the cited files in this directory.

## Specification

See `docs/agent-harness-spec.md` for the full interface contracts that
any backend must satisfy.
