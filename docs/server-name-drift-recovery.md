---
title: "Server-name drift recovery"
parent: "Operator guide"
nav_order: 17
---

# Server-name drift recovery (v9.1.6+ / v9.1.7+)

## What is server-name drift

The IRCd reads its server name from the `--name` flag at startup and
caches it for the lifetime of the daemon. Observer + bridge clients
mint their nicks using the prefix recorded in
`~/.culture/server.yaml::server.name`. If those two values disagree —
e.g. the operator edited `server.yaml` in place after the IRCd was
started, or `culture server rename` ran without restarting the IRCd —
clients try to register with the wrong prefix and the IRCd rejects
them with **432 ERR_ERRONEUSNICKNAME** carrying the reason text
`Nickname must start with <expected>-`.

Pre-9.1.6 this surfaced as a silent timeout (`Timed out waiting for
server welcome`) with zero diagnostic value. Pre-9.1.7 the bridge
spun in the read loop indefinitely with no log entry.

## v9.1.7 behavior — split by transport lifecycle

The recovery story is split by **transport lifecycle** because the
adversarial-critique workflow before implementation identified
8 blockers in auto-recovery for persistent transports
(AuditWriter / IPC socket symlink desync when `self.nick` mutates,
owner_map / role_map / DM-routing lookups keyed on stale nick,
hostile-IRCd attack surface, log-spam loops under flapping IRCd).

| Transport | Lifecycle | v9.1.7 behavior |
|---|---|---|
| **Observer** (`culture/observer.py`) | ephemeral (one CLI command) | **Auto-recover**: parse `<expected>-` from the 432 reply, retry once with the corrected prefix, log one warning, return result. |
| **Bridge / claude / codex / copilot / acp** (`culture/clients/*/irc_transport.py`) | persistent (holds session) | **Fail loud**: log the IRCd's reason text verbatim, set `transport.fatal_exit`, close the writer; the daemon's main loop sees `fatal_exit` and exits cleanly with code 1. Operator restarts after fixing the drift. |

### Observer auto-recovery — what you'll see

```
WARNING culture.observer Observer detected server.name drift on
127.0.0.1:6667 — server.yaml says 'plenty', IRCd requires 'local'-.
Used 'local'- for this connection. Fix permanently:
`culture server migrate-prefix plenty local`
```

The CLI call (e.g. `culture boss brief`) still succeeds. The
`self.server_name` attribute is NOT mutated — it stays congruent
with what's on disk so the operator notices the drift and fixes it.

### Bridge / daemon fail-loud — what you'll see

In `~/.culture/logs/<nick>.log`:

```
ERROR Bridge nick 'plenty-foo' rejected by IRCd at 127.0.0.1:6667 (432):
Nickname must start with local-. This is server.name drift — the IRCd
was started with a different --name than the bridge expected. Fix:
run `culture server migrate-prefix <old> <new>` AND restart the IRCd
with the right --name, then `culture bridge start plenty-foo` again.

ERROR Bridge plenty-foo exiting due to fatal IRC registration error
(see preceding error lines for the IRCd's reason and actionable fix).
```

The bridge daemon exits with code 1. `culture bridge status`
correctly shows it as stopped, and the PID file is removed.

## Recovery commands

### When server.yaml and the IRCd were always meant to be the same name

This is the typical case — you renamed the server with
`culture server rename <new>` (which from v9.1.6 automatically
migrates worker `culture.yaml::boss:` fields) but forgot to restart
the IRCd:

```bash
culture server stop
culture server start --name <new>
```

The migration was done in-place; no other step is needed.

### When server.yaml was edited directly

If you edited `~/.culture/server.yaml::server.name` by hand (or
recovered from a test-fixture leak — see v9.1.5), worker
`culture.yaml::boss:` fields were not migrated. Run:

```bash
# Inspect what would be rewritten.
culture server migrate-prefix <old> <new> --dry-run

# Apply.
culture server migrate-prefix <old> <new>

# Restart the IRCd with the new name.
culture server stop
culture server start --name <new>
```

### When the running IRCd's --name disagrees with server.yaml and you want server.yaml to follow

Same flow as above — `<old>` is what's currently in worker
`boss:` fields, `<new>` is the IRCd's `--name`. Operator decides
which side is authoritative.

## The new `culture server migrate-prefix` command

See [`docs/reference/cli/commands.md`](reference/cli/commands.md)
for the full reference. Headlines:

- AD-2 multi-project safety: exact-prefix-plus-hyphen matching, so
  `local` will never accidentally rewrite a worker whose stored
  boss starts with `local2-` or `fork-rearch-`.
- Idempotent — re-runs are no-ops.
- `--dry-run` for inspecting changes before applying.

## Defensive design notes

### Why the parser regex is narrow

`_parse_expected_prefix` in `culture/observer.py` only accepts
candidate prefixes that match `[a-z0-9-]+` (same charset
`sanitize_agent_name` produces). A hostile IRCd that sends a
crafted 432 with shell metacharacters can never influence the
client's chosen nick.

### Why `self.server_name` is not mutated

The observer's auto-recovery uses a **connection-local** effective
prefix. The `self.server_name` attribute stays exactly what
`server.yaml` says, so:

- The operator notices the drift (the warning fires every call).
- Future calls don't silently accept a value that disagrees with
  what's on disk.
- No risk of the observer reporting one identity to the IRCd and
  another to downstream code that reads `self.server_name`.

### Why bridges don't auto-recover

Persistent transports hold session-level state — AuditWriter keyed
on `self.nick`, IPC socket symlink at `culture-<nick>.sock`,
owner_map / role_map / DM-routing lookups — that would corrupt
under nick mutation. Auto-recovery would split identity between
the live IRC connection and on-disk state. Fail-loud + operator
restart is the right shape; the observer's auto-recovery handles
the read-only CLI case where ephemeral state means no split-brain
is possible.

### Contract test

`tests/test_observer_registration.py::test_parser_contract_round_trip_against_real_ircd`
spins up a real `IRCd` instance in-process, captures the verbatim
432 reply, and asserts the parser extracts the right value. Any
change to the IRCd's reason text breaks this test at the same
commit, not at runtime in production.
