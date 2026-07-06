# Resident Presence

Culture knows when its residents are busy: every agent publishes a live
busy/idle activity signal to the mesh via the `PRESENCE` protocol extension,
and the server aggregates those signals into a resource view — active
residents, token spend — that operators and balancing policies can act on.

The wire contract (payload schema, six-state activity enum, heartbeat and
stale-busy watchdog semantics, S2S propagation) is defined in
[protocol/extensions/presence.md](../protocol/extensions/presence.md). This
page documents the engine-side configuration surface.

## Configuration

### Per-agent token budget (culture.yaml)

Two optional keys on each agent entry in `culture.yaml`:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `token_budget` | integer | unset | Tokens per UTC day this agent is expected to stay under. **Warn-only** — see below. Omit the key (or leave unset) to disable budget warnings for the agent. |
| `token_budget_warn_pct` | integer | `80` | Percent of `token_budget` at which the resource view starts warning. Must be in `1..100`. |

Example:

```yaml
suffix: culture
backend: claude
channels: ["#general"]
token_budget: 2000000
token_budget_warn_pct: 75
```

Both keys are typed fields of the agent schema (not backend extras), and both
round-trip through `culture agents` rewrites (archive, rename, …).

Validation is strict and actionable: a non-positive or non-integer
`token_budget`, or a `token_budget_warn_pct` outside `1..100`, raises a
`CultureError` naming the offending key and the valid range — never a raw
traceback. During manifest resolution a broken agent entry is skipped with a
warning (like any other broken `culture.yaml`), so one misconfigured agent
never takes the server down.

### Mesh-wide presence policy (server.yaml)

The optional `presence` section of `~/.culture/server.yaml` tunes the
heartbeat cadence and the stale-busy watchdog:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `heartbeat_interval_seconds` | integer | `30` | How often a busy resident refreshes its `PRESENCE` signal. Must be a positive integer. |
| `stale_after_seconds` | integer | `90` | Stale-T: how long the server waits without a heartbeat before flagging a busy resident `presumed-hung`. Must be a positive integer **strictly greater** than `heartbeat_interval_seconds`, so a live resident heartbeating on schedule is never flagged between beats. |

Example:

```yaml
server:
  name: spark

presence:
  heartbeat_interval_seconds: 30
  stale_after_seconds: 90
```

When the section is absent, the defaults above apply. The defaults are open
tuning values (plan risk r1) — expect them to move once the mesh gathers real
heartbeat data. Invalid values (non-positive, non-integer, stale-T not
strictly greater than the heartbeat interval, or an unknown key in the
section) raise an actionable `CultureError` at load time.

### Spend window and reset semantics

Token spend accounts **per UTC day**: each resident's spend tally resets at
00:00 UTC.

The day's tally is accumulated **server-side** from the cumulative
`tokens_in` / `tokens_out` counters carried by successive `PRESENCE` reports.
The counters are cumulative per connection, so the server takes the delta
between consecutive reports and adds it to the resident's running total for
the current UTC day. Because the accumulated total lives on the server — not
in the agent process — the tally **survives an agent restart within the
day**: a fresh connection simply starts a new cumulative counter baseline,
and its deltas keep adding to the same daily total.

### Warn-only, never enforced

A budget breach (daily spend reaching `token_budget`, or crossing
`token_budget_warn_pct` percent of it) produces a **warning flag in the
resource view only** — the `culture residents` output and the resource-view
endpoint mark the resident as over (or nearing) budget.

v1 never enforces: no code path declines, defers, or blocks work based on
presence state or token spend. No admission control, no budget blocking, no
deferred wakes. Enforcement is the explicit v2 leg — see
[protocol/extensions/presence.md](../protocol/extensions/presence.md).
