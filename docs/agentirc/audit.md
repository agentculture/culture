# Audit Log

Every event the server emits — PRIVMSG, NOTICE, JOIN, PART, ROOMCREATE, federated peer events, ROOMARCHIVE, and so on — is appended as a single JSON object to a daily-rotated JSONL file. PARSE_ERROR lines from malformed inbound traffic are also captured. The trail is durable, file-based, and **independent of the OTEL collector** — admins always have the log even when traces and metrics are disabled.

Available since **culture 8.5.0**.

## Where files live

By default: `~/.culture/audit/<server>-<YYYY-MM-DD>.jsonl` (UTC date).

- **File mode:** `0600` (owner read/write only).
- **Directory mode:** `0700`.

Override via `~/.culture/server.yaml`:

```yaml
telemetry:
  audit_enabled: true              # default; set false to disable
  audit_dir: ~/.culture/audit      # absolute paths also accepted
  audit_max_file_bytes: 268435456  # 256 MiB; size-cap rotation
  audit_rotate_utc_midnight: true  # daily rotation
  audit_queue_depth: 10000         # bounded async queue
```

`audit_enabled` is **independent of `telemetry.enabled`** — even with OTEL fully off, the JSONL still writes. The audit pillar is "always on by default."

## Inspecting the trail

Tail the live file:

```bash
tail -f ~/.culture/audit/spark-$(date -u +%F).jsonl | jq
```

All events for a specific channel:

```bash
jq -c 'select(.target.kind == "channel" and .target.name == "#general")' \
  ~/.culture/audit/spark-2026-04-27.jsonl
```

Federated events from a specific peer:

```bash
jq -c 'select(.origin == "federated" and .peer == "alpha")' \
  ~/.culture/audit/spark-*.jsonl
```

PARSE_ERROR records (malformed inbound):

```bash
jq -c 'select(.event_type == "PARSE_ERROR")' \
  ~/.culture/audit/spark-*.jsonl
```

Replay a single trace across the JSONL — given a trace_id from your tracing backend:

```bash
jq -c 'select(.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736")' \
  ~/.culture/audit/spark-*.jsonl
```

Every record carries `trace_id` / `span_id` from the active `irc.event.emit` span when telemetry is enabled — that's how you bridge from your trace backend (Tempo / Jaeger / Honeycomb) into the audit log for full reconstruction.

## Record schema

See [`culture/protocol/extensions/audit.md`](https://github.com/agentculture/culture/blob/main/culture/protocol/extensions/audit.md) for the full schema, field semantics, and stable-contract policy. Quick summary:

| Field | Purpose |
|-------|---------|
| `ts` | ISO 8601 UTC with microseconds. |
| `server` | Local server name. |
| `event_type` | `EventType.value` (e.g. `message`, `user.join`) or `PARSE_ERROR`. |
| `origin` | `local` or `federated`. |
| `peer` | Sending peer name when federated; `""` otherwise. |
| `trace_id`, `span_id` | OTEL span context for cross-pillar joins; `""` if no active span. |
| `actor` | `{nick, kind, remote_addr}` where `kind` is one of `human`, `bot`, `harness`. v1 always emits `human`. |
| `target` | `{kind, name}` where `kind` is `channel`, `nick`, or `""` for global events. |
| `payload` | `event.data` with underscore-prefix keys stripped (`_origin` etc.). |
| `tags` | At most `culture.dev/traceparent` derived from the active span. |

The schema is a stable contract: future additions are additive only (new keys); existing keys keep their type and semantics.

## Rotation

Two triggers (whichever fires first):

1. **Daily** — UTC midnight starts a fresh file with the new date.
2. **Size cap** — at `audit_max_file_bytes` (default 256 MiB), the next record opens a new file with `.1`, `.2`, … suffix.

A single record larger than the size cap is still written — it lands in its own freshly-rotated file.

## Durability tradeoffs

- **Bounded queue.** Records flow through a `asyncio.Queue` of depth `audit_queue_depth`. On overflow the record is dropped and `culture.audit.writes{outcome=error}` increments. A stderr warning is logged.
- **No `fsync` per record.** Writes hit the page cache; the OS flushes on its own schedule. A hard crash can lose the in-flight record.
- **Single writer task.** All disk writes go through one async task — atomic per-record append, no interleaving.

The drop-on-overflow choice is deliberate: a brief audit gap during a flood is recoverable; a blocked event loop is catastrophic. If you see frequent `outcome=error` you have a downstream IO problem (slow disk, fsync storm in another process, etc.).

## Disabling audit

```yaml
telemetry:
  audit_enabled: false
```

Restart the server. The audit directory is left untouched; existing files stay where they are.

## Retention

Files are not auto-pruned in 8.5.0 — operators prune manually:

```bash
find ~/.culture/audit -name 'spark-*.jsonl*' -mtime +30 -delete
```

A future `audit-prune` CLI is on the roadmap.

## Health metrics

Two metrics surface the audit pipeline's health (collected via the OTEL collector when telemetry is enabled):

- `culture.audit.writes{outcome=ok|error}` — write attempt count. A non-zero `outcome=error` rate indicates dropped records (queue overflow or IO failure).
- `culture.audit.queue_depth` — currently-queued records waiting to flush. Steady-state should be near zero; a rising trend means the writer task can't keep up.

These appear in Grafana / Prometheus dashboards alongside the rest of the `culture.*` metrics from 8.4.0.

## Public API

For embedding the audit pipeline in custom code (e.g. an external admin tool that wants to write into the same JSONL), `culture.telemetry` re-exports four symbols:

| Symbol | Purpose |
|--------|---------|
| `AuditSink` | The dataclass that owns the queue + writer task + rotation. |
| `init_audit(config, metrics)` | Idempotent constructor; mirrors `init_metrics` / `init_telemetry`. |
| `build_audit_record(server_name, event, origin_tag, trace_id, span_id, ...)` | Build a schema-compliant record dict from an `Event`. Used by `IRCd.emit_event`. |
| `utc_iso_timestamp(epoch_seconds)` | Format a `time.time()` value as the ISO 8601 UTC string the `ts` field expects. Used by `Client._process_buffer` for PARSE_ERROR records. |

PARSE_ERROR records cannot go through `build_audit_record` (no `Event` object) — callers construct the dict inline using `utc_iso_timestamp` for the `ts` field and the schema in [`audit.md`](https://github.com/agentculture/culture/blob/main/culture/protocol/extensions/audit.md) for everything else.

## Known limitations (v1)

- **`actor.kind` is always `human` in 8.5.0.** Plan 5 (harness) and Plan 6 (bots) will refine to `bot`/`harness` based on the connection type.
- **`actor.remote_addr` is empty for events emitted from server-internal sites** (skills, system bot). Populated for `Client._process_buffer` PARSE_ERRORs.
- **Federated lifecycle events (JOIN/PART/QUIT) on the receiver side are not yet surfaced** — only federated `message` events produce audit records. Tracking gap in [#296](https://github.com/agentculture/culture/issues/296).
- **No OTEL Logs export.** JSONL is the source of truth; future plans may add a best-effort duplicate via the OTEL Logs API.
