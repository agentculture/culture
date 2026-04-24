# OTEL Observability for Culture — Design Spec

## Context

Culture is an IRC-based agent mesh (IRCd + federation + agent harnesses + bots) with only stdlib `logging` today. As the mesh grows, three observability questions are already hard to answer: (a) when a multi-agent flow stalls or errors, where did it stall across client → server → federation → harness → LLM, (b) is the mesh healthy — message rates, federation link health, harness reconnects, bot webhook failures, (c) who said what to whom, when, via what path, as an admin-only audit trail.

This spec adds OpenTelemetry for traces, metrics, and logs, plus a separate durable audit JSONL sink. Full three-pillar scope. Trace context propagates across federation via a new IRCv3 tag. Message bodies are captured in full (single-admin private mesh, admin-only access). Destination is OTLP/gRPC to a local collector the operator runs; audit is a separate always-on file sink.

## Decisions (from brainstorming Q&A)

1. **Signals:** all three pillars — traces, metrics, logs — plus audit trail.
2. **PII / bodies:** full capture by default. Single-admin private server; admin-only access; OTEL pipeline is part of the trusted boundary.
3. **Destination:** OTLP/gRPC → local `otelcol-contrib` on `localhost:4317`. Audit gets its own JSONL sink independent of the collector.
4. **Trace-context propagation:** W3C `traceparent` / `tracestate` carried as IRCv3 message tags (`culture.dev/traceparent`, `culture.dev/tracestate`). Inbound mitigation: length caps, malformed-drop, peer attribution.
5. **Code placement:** `culture/telemetry/` package for server code. `packages/agent-harness/telemetry.py` reference module cited into each of the four backends (claude, codex, copilot, acp). All-backends rule applies.

## Architecture

**New server-side package `culture/telemetry/`:**

- `tracing.py` — OTEL TracerProvider init, W3C propagator wired to IRCv3 tag extract/inject. `init_telemetry(config)` called from `IRCd.__init__` and `ServerLink.__init__`.
- `metrics.py` — MeterProvider init, single place that registers every instrument (counters / histograms / UpDownCounters / gauges).
- `audit.py` — JSONL sink with size + daily rotation, bounded `asyncio.Queue` + dedicated writer task.
- `context.py` — `extract_traceparent_from_tags(message, peer)` and `inject_traceparent_into_tags(message, span)` helpers.
- `__init__.py` — public re-exports.

**New harness-side reference `packages/agent-harness/telemetry.py`:**

- Mirror of `tracing.py` + `context.py` scoped to harness needs.
- MeterProvider init — harness metrics added for LLM usage (see Metrics catalog).
- `record_llm_call(backend, model, usage, duration_ms, outcome)` helper — each backend's `agent_runner.py` calls it with backend-specific `usage` shape.
- Cited into each `culture/clients/<backend>/telemetry.py`; all-backends rule.

**Local OTel Collector (operator-managed, not Culture code):**

- `otelcol-contrib` as a systemd unit, config at `~/.culture/otelcol.yaml`.
- Culture ships a minimal template (OTLP receiver on 4317, debug exporter) as documentation. Operators wire real backends (Tempo/Prometheus/Loki/Grafana Cloud) when they want them.

**New protocol extension `protocol/extensions/tracing.md`:**

Defines `culture.dev/traceparent` and `culture.dev/tracestate` IRCv3 tags (W3C Trace Context over IRC), documents the inbound mitigation rules and the re-sign-per-hop federation relay behavior.

**New doc `protocol/extensions/audit.md`:**

Documents the audit JSONL schema and file layout as a stable contract.

## Protocol: IRCv3 trace-context tags

**Tags:**

- `culture.dev/traceparent` — W3C `traceparent` string (55 chars, `00-<trace-id>-<parent-id>-<flags>`). MUST match the W3C regex; servers MUST silently drop the tag if it doesn't.
- `culture.dev/tracestate` — W3C `tracestate` key/value list. Optional. MUST be ≤ 512 bytes after IRCv3 unescape; drop if longer.

**Scope:** outbound on every client-originated IRC message when a span context is active (PRIVMSG, NOTICE, JOIN, PART, KICK, MODE, plus culture verbs SEVENT/SMSG/SNOTICE). On federation relay, tags are re-injected from the current server's span (not copied from the received message) — produces a proper parent-per-hop span tree.

**Inbound mitigation (server-side `_dispatch` and federation-side `_dispatch`):**

1. Tag absent → start new root span, attr `culture.trace.origin=local`.
2. Tag present and valid → child span linked to extracted context, attrs `culture.trace.origin=remote`, `culture.federation.peer=<peer>`.
3. Tag present but malformed or too long → drop tag, start root span, attrs `culture.trace.origin=remote`, `culture.trace.dropped_reason=<malformed|too_long>`, `culture.federation.peer=<peer>`. Rate-limited warning log. Metric `culture.trace.inbound{result=...}` incremented.

**Length caps** (hard-coded, not configurable): `traceparent` = 55 chars, `tracestate` = 512 bytes post-unescape.

**Non-goal:** trace context is not authenticated. Federation peer trust is the authorization mechanism; tracing is observability only.

**Compat:** additive IRCv3 tag. Peers that don't recognize it pass it through on relay per standard IRCv3 tag behavior. No wire version bump; project version bumps as minor per CLAUDE.md.

## Instrumentation points

### `culture/agentirc/ircd.py`

- `IRCd.__init__` — `telemetry.init_telemetry(config)` once. Resource attrs: `service.name=culture.agentirc`, `service.instance.id=<server_name>`, `service.version=<pkg_version>`.
- `IRCd.emit_event` (l. 169–198) — span `irc.event.emit`, attrs `event.type`, `event.channel`, `event.origin`. Metric `culture.events.emitted`. Audit write for every event.

### `culture/agentirc/client.py`

- `Client.handle` (l. 95) — span `irc.client.session` for connection lifetime. Attrs `irc.client.nick`, `irc.client.remote_addr`.
- `Client._process_buffer` / `_dispatch` (l. 85, 112) — span `irc.command.<VERB>`. Parented by extracted traceparent or session span. Attrs `irc.command`, `irc.prefix_nick`. Histogram `culture.irc.message.size`.
  - **Parse-error compensation:** `Message.parse(...)` wrapped in try/except here. On exception, `span.add_event("irc.parse_error", {"line_preview": line[:64], "error": type(e).__name__, "peer": peer_ident})`. Mirror to audit as `event_type: "PARSE_ERROR"`.
- `Client.send` / `send_raw` (l. 47–63) — inject traceparent tag into outbound message pre-write. Metric `culture.irc.bytes_sent`. Format-error compensation: try/except + `span.add_event("irc.format_error", ...)` on the active span.
- `_handle_privmsg` (l. 659), `_send_to_client` (l. 633), `_send_to_channel` — spans `irc.privmsg.dispatch`, `irc.privmsg.deliver.dm`, `irc.privmsg.deliver.channel`. Full body in attr `irc.message.body`. Metric `culture.privmsg.delivered{kind=dm|channel}`.
- `_handle_join` (l. 251), `_handle_part` (l. 299) — spans `irc.join` / `irc.part`, attrs `irc.channel`, `irc.client.nick`. Audit rides on the `emit_event` for `JOIN`/`PART`.

### `culture/agentirc/server_link.py`

- `ServerLink.handle` (l. 88) — span `irc.s2s.session`, attrs `s2s.peer`, `s2s.direction=inbound|outbound`.
- `ServerLink._dispatch` (l. 120+) — per-verb span `irc.s2s.<VERB>`. Inbound traceparent mitigation applied here.
- `ServerLink.relay_event` (l. 834) — span `irc.s2s.relay`, attrs `event.type`, `s2s.peer`. Re-injects traceparent from current span before `_send_raw`.
- Metrics: `culture.s2s.messages`, `culture.s2s.relay_latency`, `culture.s2s.links_active`, `culture.s2s.link_events`.

### `culture/bots/`

- `BotManager.on_event` — span `bot.event.dispatch` per matched bot, attrs `bot.name`, `event.type`. Metric `culture.bot.invocations{bot, event.type, outcome}`.
- `http_listener` webhook sender — enable `opentelemetry-instrumentation-aiohttp-client`. Outbound HTTP becomes child spans with traceparent sent as HTTP header automatically. Post-span hook maps status to `status_class` label for `culture.bot.webhook.duration` histogram.
- `Bot` execution — span `bot.run`.

### `packages/agent-harness/irc_transport.py` (reference, cited into each backend)

- `IRCTransport.connect` / `_do_connect` — span `harness.irc.connect`, attrs `harness.backend`, `harness.nick`, `harness.server`.
- Outbound write path — inject traceparent.
- Inbound read path — extract traceparent; open `harness.irc.message.handle` child span. Stitches server span → harness span → LLM call span.
- Each backend's `agent_runner.py` wraps its LLM call in span `harness.llm.call`, attrs `harness.model`, `llm.tokens.in`, `llm.tokens.out`, `llm.latency_ms`, then calls `telemetry.record_llm_call(...)` for metrics.

### Deliberately NOT instrumented

- `culture/protocol/message.py` `Message.parse` / `Message.format` — too hot. Parse-error visibility covered via span events at callers (see compensation above).
- Per-byte socket-read loop inside `Client.handle` 4096-byte chunking — covered by session-level span and byte counters.

## Metrics catalog

Registered once in `culture/telemetry/metrics.py` (server) and `packages/agent-harness/telemetry.py` (harness).

**Message flow (server):**

- `culture.irc.bytes_sent` — Counter, `By`. Labels: `direction=c2s|s2c|s2s`.
- `culture.irc.bytes_received` — Counter, `By`. Labels: `direction=c2s|s2c|s2s`.
- `culture.irc.message.size` — Histogram, `By`. Labels: `verb`, `direction`.
- `culture.privmsg.delivered` — Counter. Labels: `kind=dm|channel`, `channel` (unset when dm).

**Events (server):**

- `culture.events.emitted` — Counter. Labels: `event.type`, `origin=local|federated`.
- `culture.events.render.duration` — Histogram, `ms`. Labels: `event.type`.

**Federation (server):**

- `culture.s2s.messages` — Counter. Labels: `verb`, `direction`, `peer`.
- `culture.s2s.relay_latency` — Histogram, `ms`. Labels: `event.type`, `peer`.
- `culture.s2s.links_active` — UpDownCounter. Labels: `peer`, `direction`.
- `culture.s2s.link_events` — Counter. Labels: `peer`, `event=connect|disconnect|auth_fail|backfill_start|backfill_complete`.

**Clients & sessions (server):**

- `culture.clients.connected` — UpDownCounter. Labels: `kind=human|bot|harness`.
- `culture.client.session.duration` — Histogram, `s`. Labels: `kind`.
- `culture.client.command.duration` — Histogram, `ms`. Labels: `verb`.

**Bots (server):**

- `culture.bot.invocations` — Counter. Labels: `bot`, `event.type`, `outcome=success|error|timeout`.
- `culture.bot.webhook.duration` — Histogram, `ms`. Labels: `bot`, `status_class=2xx|3xx|4xx|5xx|timeout`.

**Trace-context hygiene (server):**

- `culture.trace.inbound` — Counter. Labels: `result=valid|missing|malformed|too_long`, `peer`.

**Audit sink health (server):**

- `culture.audit.writes` — Counter. Labels: `outcome=ok|error`.
- `culture.audit.queue_depth` — UpDownCounter.

**Harness LLM metrics (harness-side, all backends):**

- `culture.harness.llm.tokens.input` — Counter. Labels: `backend`, `model`, `harness.nick`.
- `culture.harness.llm.tokens.output` — Counter. Same labels.
- `culture.harness.llm.call.duration` — Histogram, `ms`. Labels: `backend`, `model`, `outcome=success|error|timeout`.
- `culture.harness.llm.calls` — Counter. Labels: `backend`, `model`, `outcome`.

**Deferred (no Phase-1 block, no ideological objection):**

- Per-channel gauges — cardinality fine given bounded mesh; add when a dashboard wants them.
- Process / GC / memory metrics — use `opentelemetry-instrumentation-system-metrics` or collector-side host metrics receiver.

## Audit log

**Sink:** `~/.culture/audit/<server>-YYYY-MM-DD.jsonl`. Rotated daily on UTC midnight and by size at 256 MiB (whichever first). File mode `0600`, directory `0700`.

**Durability:** bounded `asyncio.Queue` (depth 10 000), dedicated writer task. On queue overflow: drop record, increment `culture.audit.writes{outcome=error}`, stderr warning. Dropping is better than blocking `emit_event`.

**Record schema (documented in `protocol/extensions/audit.md` as a stable contract):**

```json
{
  "ts": "2026-04-24T14:32:05.123456Z",
  "server": "spark",
  "event_type": "PRIVMSG",
  "origin": "local|federated",
  "peer": "thor",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7",
  "actor": {"nick": "spark-ori", "kind": "human|bot|harness", "remote_addr": "127.0.0.1:53241"},
  "target": {"kind": "channel|nick", "name": "#general"},
  "payload": { /* full event dict, body included */ },
  "tags": {"culture.dev/traceparent": "...", "...": "..."}
}
```

**Scope:** every event through `IRCd.emit_event`, plus `PARSE_ERROR` synthetic records from the Section-3 compensation. Trust/auth events (NICK collision, PASS failure, supervisor actions) are included via the event stream.

**Retention:** no auto-deletion in Phase 1. Operators prune manually. TODO in `audit.md` for a future `audit-prune` CLI.

**OTEL log pipeline:** audit records also emitted as OTEL log records (best-effort duplicate). JSONL is source of truth. If OTEL export fails, JSONL still writes.

## Config surface

### Server (`~/.culture/server.yaml`)

New `TelemetryConfig` dataclass in `culture/config.py`, attached to `ServerConfig.telemetry`.

```yaml
telemetry:
  enabled: false
  service_name: culture.agentirc
  otlp:
    endpoint: http://localhost:4317
    protocol: grpc
    headers: {}
    timeout_ms: 5000
    compression: gzip
  traces:
    enabled: true
    sampler: parentbased_always_on
  metrics:
    enabled: true
    export_interval_ms: 10000
  logs:
    enabled: true
  audit:
    enabled: true
    dir: ~/.culture/audit
    max_file_bytes: 268435456
    rotate_utc_midnight: true
    queue_depth: 10000
```

- `telemetry.enabled: false` → no SDK init; `inject_traceparent` is a no-op; `extract_traceparent` still parses inbound for validation + metric counting.
- `audit.enabled: true` independent — audit always on, even when OTEL off.
- Standard OTEL env vars (`OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`, `OTEL_TRACES_SAMPLER`) override YAML.

### Harness (`culture.yaml` in each backend's working directory)

Reference in `packages/agent-harness/culture.yaml`, cited into each backend.

```yaml
telemetry:
  enabled: false
  service_name: culture.harness.<backend>
  otlp:
    endpoint: http://localhost:4317
    protocol: grpc
  traces:
    enabled: true
    sampler: parentbased_always_on
  metrics:
    enabled: true
    export_interval_ms: 10000
```

- `sampler: parentbased_always_on` is required so harnesses honor the server's sampling decision.
- No `audit:` block — server owns audit. Harness-side audit is `journalctl`.

### Collector (not Culture code)

`~/.culture/otelcol.yaml` template shipped as documentation. Receives OTLP/gRPC on 4317, exports to `debug` exporter by default.

### Not configurable

- Tag names (`culture.dev/traceparent`, `culture.dev/tracestate`) — protocol, not config.
- Inbound length caps (55 / 512) — protocol constants.
- Metric names — stability contract.

## Critical files to modify

- `culture/config.py` — add `TelemetryConfig` dataclass + field on `ServerConfig`.
- `culture/agentirc/ircd.py` — init telemetry; wrap `emit_event`.
- `culture/agentirc/client.py` — wrap `handle` / `_dispatch` / PRIVMSG handlers / JOIN / PART / `send_raw`; parse-error compensation.
- `culture/agentirc/server_link.py` — wrap `handle` / `_dispatch` / `relay_event`; inbound traceparent mitigation.
- `culture/bots/bot_manager.py`, `culture/bots/http_listener.py`, `culture/bots/bot.py` — spans + metrics; aiohttp auto-instrumentation.
- `packages/agent-harness/irc_transport.py` — extract/inject at transport boundary.
- `packages/agent-harness/telemetry.py` — **new**, reference module.
- `packages/agent-harness/culture.yaml` — add `telemetry:` block to template.
- `culture/clients/{claude,codex,copilot,acp}/telemetry.py` — **new**, cited from reference.
- `culture/clients/{claude,codex,copilot,acp}/culture.yaml` — add `telemetry:` block.
- Each backend's `agent_runner.py` — wrap LLM call span + `record_llm_call(...)`.
- `culture/telemetry/` — **new package**: `tracing.py`, `metrics.py`, `audit.py`, `context.py`, `__init__.py`.
- `protocol/extensions/tracing.md` — **new**.
- `protocol/extensions/audit.md` — **new**.
- `docs/agentirc/telemetry.md` — **new** feature doc.
- `docs/agentirc/otelcol-template.yaml` — **new** collector starter config.
- `pyproject.toml` — add `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp`, `opentelemetry-instrumentation-aiohttp-client`. Bump version minor. Update `uv.lock`.
- `tests/conftest.py` — add `telemetry` + `audit_dir` fixtures.
- `tests/telemetry/` — **new** directory with `test_tracing.py`, `test_propagation.py`, `test_metrics.py`, `test_audit.py`, `test_harness.py`, `test_config.py`.
- `tests/test_all_backends_telemetry.py` — **new**, enforces citation parity.
- `CHANGELOG.md` — via `/version-bump minor`.

## Reusable existing surface

- `Message.tags` field + `parse()`/`format()` IRCv3 escape (`culture/protocol/message.py`) — used as-is for tag injection.
- `IRCd.emit_event` (`culture/agentirc/ircd.py:169`) — the single instrumentation hub; existing event registry drives audit coverage.
- `EventType` enum (`culture/agentirc/events.py`) — metric labels (`event.type`).
- `linked_servers` / `fires_event` / `event_filter` / `make_client_a|b` fixtures (`tests/conftest.py`) — test harness is already sufficient for two-server propagation tests.
- Existing `ServerConfig` loader in `culture/config.py` — `TelemetryConfig` slots in as one more field.

## Verification

**Automated (pytest, via `/run-tests`):**

- Unit: `test_tracing.py` — W3C roundtrip through IRCv3 escape/unescape, length-cap enforcement, malformed-drop + metric increment, peer attribution.
- Integration: `test_propagation.py` — two linked servers (`linked_servers` fixture), PRIVMSG from A to B, assert trace tree on B is child of A with re-parent-per-hop.
- Integration: `test_metrics.py` — N PRIVMSGs → `reader.collect()` → assert `culture.privmsg.delivered` == N. Same for s2s.
- Integration: `test_audit.py` — JSONL contents match fired events, rotation by monkey-patched `max_file_bytes`, queue overflow → `outcome=error` + stderr.
- Harness: `test_harness.py` — fake `IRCTransport` peer; outbound carries traceparent when span active; `record_llm_call()` emits all four metrics.
- Config: `test_config.py` — `enabled: false` asserts no spans, SDK not initialized, federation still parses inbound traceparents (off-switch invariant).
- All-backends: `test_all_backends_telemetry.py` — each `clients/<backend>/telemetry.py` citation matches reference (modulo `service_name`).

**Manual end-to-end (in the feature doc):**

1. Start server with `telemetry.enabled: true` and `otelcol-contrib` using the template config (`debug` exporter).
2. Connect weechat, send PRIVMSG to a channel with a bot subscriber.
3. Confirm three spans share a `trace_id`: `irc.command.PRIVMSG` → `irc.privmsg.deliver.channel` → `bot.event.dispatch`.
4. Tail `~/.culture/audit/<server>-*.jsonl`; PRIVMSG present with body + `trace_id` + `span_id`.
5. Link a second server; cross-server PRIVMSG; confirm single `trace_id` spans two `service.instance.id` resources.
6. Run `claude` harness; send prompt; confirm `culture.harness.llm.calls` + `culture.harness.llm.tokens.output` in collector output.

**Pre-push checklist (per CLAUDE.md):**

- `doc-test-alignment` subagent (new protocol extensions + exceptions + config fields).
- `superpowers:code-reviewer` on staged diff (touches transport and `_send_raw`-style I/O; exactly the choke-point rule).
- `/version-bump minor`.
- `/run-tests` (default parallel).
- `/sonarclaude` on the branch before declaring PR ready.

## Phasing (suggestion for the implementation plan)

The `writing-plans` skill will decide exact sequencing. A reasonable order of phases:

1. `culture/telemetry/` package skeleton (config loading, no-op providers when `enabled: false`) + dependencies + version bump + `tracing.md` / `audit.md` extension docs.
2. Server-side tracing: `emit_event`, client `_dispatch` / PRIVMSG handlers, parse-error compensation. Tests for tracing + propagation (single server).
3. Federation tracing: `ServerLink._dispatch` / `relay_event` + inbound mitigation. Two-server propagation tests.
4. Metrics pillar (server). Tests.
5. Audit sink + log pipeline. Tests.
6. Harness reference module + per-backend citation + `record_llm_call` + LLM spans/metrics. All-backends test.
7. Bot instrumentation (aiohttp auto-instrument + span hooks).
8. Feature doc, collector template, manual E2E checklist, `/sonarclaude`, PR.
