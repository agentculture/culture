# Shared vs cited modules

The `culture` agent harness uses a two-tier code-distribution model.

## The rule

A harness module belongs in `culture/clients/shared/` if it has **no
backend-specific behavior** — nothing in it would ever differ between
`claude`, `codex`, `copilot`, or `acp`. Pure logic and pure-glue I/O
qualify; orchestration that reads SDK-specific shapes does not.

Cited modules live in `packages/agent-harness/` and are copied byte-for-byte
into each backend at `culture/clients/<backend>/`. The
[all-backends rule](../../CLAUDE.md#citation-pattern) — "a feature in only
one backend is a bug" — applies to the cited tier.

Shared modules live in `culture/clients/shared/` and are imported directly
by every backend. The all-backends rule doesn't need to apply because
Python's import system enforces it.

## Current file list

### Shared (imported)

| File | Why shared |
|---|---|
| `attention.py` | Pure state machine; no I/O, no SDK shapes |
| `message_buffer.py` | Pure value type |
| `ipc.py` | Frame encoder/decoder for whisper protocol |
| `telemetry.py` | OTel glue; identical config across backends |
| `irc_transport.py` | RFC 2812 client wrapper; no SDK shapes |
| `socket_server.py` | Unix-socket whisper plumbing |
| `webhook.py` | `urllib.request` POST; identical schema |
| `webhook_types.py` | `WebhookConfig` dataclass |

### Cited (copied)

| File | Why cited |
|---|---|
| `daemon.py` | Each backend's main loop wraps SDK-specific shapes (claude-agent-sdk, codex-agent-sdk, etc.) |
| `config.py` | Per-backend defaults and SDK-specific options |
| `constants.py` | Per-backend literals (channel names, timeouts) |
| `agent_runner.py` | "Yours to write" — the SDK call site itself |
| `supervisor.py` | "Yours to write" — backend-specific liveness logic |

The cited tier's parity is locked down by
`tests/harness/test_all_backends_parity.py`; the shared tier's "no
per-backend copy leaked" property is locked down by
`tests/harness/test_no_per_backend_copy_of_shared_modules.py`.

## Fork-back procedure

If a shared module needs to start diverging for one backend (for example,
an SDK upgrade forces telemetry to emit different attributes per backend):

1. `cp culture/clients/shared/X.py culture/clients/<backend>/X.py` for each
   backend that needs the local copy.
2. In *that backend's* code (its `daemon.py` and any tests that targeted
   the shared path), change `from culture.clients.shared.X import …` to
   `from culture.clients.<backend>.X import …`.
3. Leave any backends that still agree pointing at `shared/`.
4. Re-add `X.py` to the parity matrix in
   `tests/harness/test_all_backends_parity.py` for the now-cited backends
   so the cite-paste invariant is enforced again for them.
5. Move `X` from the "Shared" table above to the "Cited" table in this
   doc, and update the *Shared vs cited* paragraph in `CLAUDE.md` to match.

The two-tier model bends without breaking. The shared tier is **not** an
all-or-nothing commitment — it just describes where the line currently is.
