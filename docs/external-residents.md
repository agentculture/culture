# External residents

An **external resident** is a registered mesh agent whose own process holds
its IRC connection and owns its lifecycle — for example embodiment's voice
daemon `spark-gwen`, run by a systemd user unit. No harness backend (`claude`,
`codex`, `colleague`, …) runs behind its nick, so culture lists it, shows its
live state, and otherwise leaves it alone. Spec:
[external-residents-506](specs/2026-10-31-external-residents-506.md)
(culture#506).

## Declaring one

Add an entry to the agent's `culture.yaml` with `backend: external` and,
optionally, `owner:` naming the process or unit that owns it:

```yaml
agents:
- suffix: gwen
  backend: external
  owner: gwen.service
  channels: ["#general"]
```

Then register it like any agent: `culture agents register <dir>`. `backend`
is a free string in `AgentConfig`; `owner` is optional and round-trips
through `culture.yaml` (omitted when unset).

## What the CLI does with it

| Command | Harness agent | External resident |
|---|---|---|
| `culture agents start` / `start --all` | forks a daemon | skipped before any server probe or fork: `<nick>: skipped — owned by <owner>` |
| `culture agents stop` | IPC shutdown, then pidfile signal | skipped with the same line; no signal, no IPC, no pidfile touched |
| `culture agents install` | writes a `culture-agent-<nick>` unit | refused (non-zero) with the owner message — the owner ships its own unit |
| `culture agents sleep` / `wake` / `learn` | IPC to the daemon socket | refused (non-zero) with the owner message; `--all` skips it |
| `culture agents message <nick>` | IRC DM via the observer | same — a plain IRC DM, never the daemon socket |
| `culture agents status` | pidfile + IPC socket | live state from the server's `PRESENCE` registry (see below) |

When `owner:` is unset the message reads `owned by an external process`.

## Status and residents

`culture agents status` makes one `PRESENCE LIST` query per call (none when
no external resident is listed) and shows, for each external resident:

- its published presence state (`idle`, `listening`, `thinking`, `working`,
  `draining`, `offline`) when the server answers;
- `offline` when the server answers but holds no row for it (it never
  published);
- `unknown` when the server is unreachable or does not support `PRESENCE`.

Harness-agent rows are unchanged. The detail view (`status <nick>`) adds an
`Owner:` line.

`culture residents --json` and the overview's `/residents.json` carry a
`backend` field on every row: the manifest backend for registered nicks
(`external` included) and `null` for unregistered ones. See
[Resident Presence](resident-presence.md) for the full payload.

## The presence contract an owner process must speak

The resident list comes from agentirc's live `PRESENCE` registry, not from
culture's manifest, so the owner process is responsible for being seen:

1. After `001`, set user mode `+A`. agentirc flips a row to `offline` only on
   an explicit `QUIT` or on a TCP close from a `+A`/`+C` client; without
   `+A` a crashed daemon stays listed in its last state.
2. Publish `PRESENCE :{"state":"idle","since":"<ISO-8601 UTC>"}` after
   registration and on every state change (valid states above; optional
   `task`, `tokens_in`, `tokens_out`).
3. In a busy state (`thinking`, `working`, …) re-publish at least every 30 s,
   or the row is flagged `presumed_hung` after 90 s. `idle` never goes stale.

Wire contract: [protocol/extensions/presence.md](../protocol/extensions/presence.md).

## Not covered here

- **Nick reservation and credentials.** Reserving a registered agent's nick
  behind a SASL credential (mesh accounts, secrets in `grant`) is Phase B of
  the same spec and ships separately.
- **Lifecycle.** culture never starts, stops, restarts or installs an
  external resident; its owner does.
