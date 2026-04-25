# Extension: Audit JSONL Sink

The audit log is a durable, file-based JSON-Lines (`.jsonl`) trail of every event the server
emits. It is **separate from OTEL traces / metrics / logs** — audit lands directly on local disk
and never depends on a running collector. Admin-only "who said what to whom, when, via what path."

## File Layout

- **Path:** `<audit_dir>/<server_name>-<YYYY-MM-DD>.jsonl` where `<audit_dir>` defaults to
  `~/.culture/audit/` (configurable via `telemetry.audit_dir`). The date is **UTC**.
- **File mode:** `0600` (owner read/write only).
- **Directory mode:** `0700` (owner only). Created on demand if missing; existing dir mode is
  left as-is.
- **Rotation suffix:** when the daily file hits the size cap, the next file gets `.1`, then
  `.2`, … same date. New day starts a fresh file with no suffix.

Example for server `spark` on 2026-04-27 with two size-cap rotations:

```text
~/.culture/audit/spark-2026-04-27.jsonl     # first 256 MiB
~/.culture/audit/spark-2026-04-27.1.jsonl   # next 256 MiB
~/.culture/audit/spark-2026-04-27.2.jsonl   # current
```

## Record Schema

Each line in the file is a single JSON object. Lines never wrap. Keys are lowercase with `_`
separators. Order is canonicalized at write time (stable across writes). Future schema additions
are additive only — consumers must tolerate unknown keys.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `ts` | string | yes | ISO 8601 UTC timestamp with microsecond precision and trailing `Z` (e.g. `2026-04-27T14:32:05.123456Z`). |
| `server` | string | yes | Server name from `ServerConfig.name`. |
| `event_type` | string | yes | `EventType.value` (e.g. `message`, `user.join`, `room.create`) or the special string `PARSE_ERROR` for malformed inbound lines. |
| `origin` | string | yes | `local` if the event originated on this server; `federated` if it arrived via a peer link. |
| `peer` | string | yes | Peer server name when `origin=federated`; empty string `""` otherwise. |
| `trace_id` | string | yes | OTEL trace-id (32 hex chars) of the active span at submit time, or `""` if no span. |
| `span_id` | string | yes | OTEL span-id (16 hex chars) of the active span, or `""`. |
| `actor` | object | yes | `{nick, kind, remote_addr}` describing who/what produced the event. |
| `actor.nick` | string | yes | The nick from the event (`event.nick`), or `""`. |
| `actor.kind` | string | yes | One of `human`, `bot`, `harness`. v1 always emits `human` — Plans 5/6 refine. |
| `actor.remote_addr` | string | yes | `"<ip>:<port>"` if known (PARSE_ERROR via Client; empty for server-internal sites). |
| `target` | object | yes | `{kind, name}` describing what the event affected. |
| `target.kind` | string | yes | `channel` (event.channel set), `nick` (DM target), or `""` for global events. |
| `target.name` | string | yes | The channel or nick; `""` for global. |
| `payload` | object | yes | `event.data` with all underscore-prefix keys (`_origin`, etc.) stripped. May include `nick` / `channel` defaulted from `event.nick`/`event.channel`. |
| `tags` | object | yes | IRCv3-style tag bag. v1 emits at most `culture.dev/traceparent` derived from the active span; empty `{}` if no span. |

## Rotation

Rotation fires when **either** condition is met, checked at the top of every record write:

1. The current UTC date differs from `current_date` (daily roll, controlled by
   `telemetry.audit_rotate_utc_midnight`).
2. The current file size + the about-to-be-written record size exceeds
   `telemetry.audit_max_file_bytes` (default 256 MiB).

The new file is opened with `O_WRONLY | O_APPEND | O_CREAT` mode `0600`. Writes use a single
`os.write` per record so partial-line interleaving is impossible.

## Durability

Records flow through a bounded `asyncio.Queue` (depth `telemetry.audit_queue_depth`, default
10000). A dedicated writer task drains the queue and writes each record. On queue overflow, the
record is **dropped** and `culture.audit.writes{outcome=error}` increments. A stderr warning is
logged.

This is a deliberate trade-off: dropping records is preferable to blocking `IRCd.emit_event`. A
real-world audit gap is rare and recoverable; a blocked event loop is catastrophic.

No `fsync` per record — writes hit the page cache and rely on the OS to flush. A hard crash can
lose the in-flight record.

## Retention

Files are not auto-pruned in v1. Operators prune manually:

```bash
find ~/.culture/audit -name 'spark-*.jsonl*' -mtime +30 -delete
```

A future `audit-prune` CLI is TODO.

## Compat

The schema is a stable contract:

- New fields can be added in future versions; old consumers must tolerate unknown keys.
- Existing keys keep their type and semantics across versions.
- If a future version needs a breaking change, a top-level `schema_version` integer will be
  added and bumped — until that exists, treat the schema as version 1.

## Example

PRIVMSG from `alpha-alice` (a federated client on the `alpha` peer) to channel `#general` on
the local server `spark`:

```json
{"ts":"2026-04-27T14:32:05.123456Z","server":"spark","event_type":"message","origin":"federated","peer":"alpha","trace_id":"4bf92f3577b34da6a3ce929d0e0e4736","span_id":"00f067aa0ba902b7","actor":{"nick":"alpha-alice","kind":"human","remote_addr":""},"target":{"kind":"channel","name":"#general"},"payload":{"text":"hi","nick":"alpha-alice","channel":"#general"},"tags":{"culture.dev/traceparent":"00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"}}
```
