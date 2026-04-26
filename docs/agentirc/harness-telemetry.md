---
layout: default
title: Harness Telemetry
parent: AgentIRC
nav_order: 92
---

# Harness Telemetry

Culture 8.6.0 brings OpenTelemetry across the harness boundary. Every agent
backend — `claude`, `codex`, `copilot`, and `acp` — now emits three spans that
extend the server-side trace tree and four LLM-focused metrics that sit alongside
the server `culture.*` instruments in the same Prometheus / Grafana instance.
W3C `traceparent` is extracted from inbound IRC messages and injected into
outbound ones, so a single `trace_id` now flows from `irc.command.PRIVMSG` on
the originating server all the way through `harness.irc.message.handle` and into
`harness.llm.call` — a true cross-process trace with no gap at the harness
boundary.

Available since **culture 8.6.0**.

## What you get in 8.6.0

### Spans

| Span name | Where it opens | Key attributes |
|-----------|----------------|----------------|
| `harness.irc.connect` | `IRCTransport._do_connect` | `harness.backend`, `harness.nick`, `harness.server` |
| `harness.irc.message.handle` | `IRCTransport._handle` (per inbound message) | `irc.command`, `irc.client.nick`, `culture.trace.origin` |
| `harness.llm.call` | per-backend `agent_runner.py` LLM call site | `harness.backend`, `harness.model`, `outcome` |

`harness.irc.message.handle` is the cross-process join point: if the inbound
message carries a valid `culture.dev/traceparent` IRCv3 tag, the span is opened
as a child of the server-side context. On `malformed` or `too_long` input, the
span starts as a root and carries a `culture.trace.dropped_reason` attribute.

### Metrics

All four instruments are registered per-backend in an independent
`HarnessMetricsRegistry`. Token counters are skipped (not incremented) when the
underlying SDK does not expose token counts — see [Token-usage caveats](#token-usage-caveats).

| Metric | Kind | Unit | Labels |
|--------|------|------|--------|
| `culture.harness.llm.tokens.input` | Counter | (none) | `backend`, `model`, `harness.nick` |
| `culture.harness.llm.tokens.output` | Counter | (none) | `backend`, `model`, `harness.nick` |
| `culture.harness.llm.call.duration` | Histogram | `ms` | `backend`, `model`, `outcome` |
| `culture.harness.llm.calls` | Counter | (none) | `backend`, `model`, `outcome` |

`outcome` is one of `success`, `error`, or `timeout`.

### Traceparent propagation

- **Inbound** — `IRCTransport._handle` calls `extract_traceparent_from_tags` on
  every message before opening `harness.irc.message.handle`. A valid traceparent
  makes the harness span a child of the server's `irc.event.emit` span.
- **Outbound** — `IRCTransport._send_raw` prepends
  `@culture.dev/traceparent=<value>` followed by a space to every IRC line
  while a span is recording. This lets the _next_ peer in the chain continue
  the trace.

Tracestate injection is not included in 8.6.0 (server-parity-deferred — see
[What's not in 8.6.0](#whats-not-in-860)).

### All-backends parity

All four backends (`claude`, `codex`, `copilot`, `acp`) carry identical harness
telemetry code — same span names, same metric names, same `record_llm_call`
helper — enforced by `tests/harness/test_all_backends_parity.py`. The only
intentional per-backend difference is `service.name` (see
[Per-backend telemetry namespaces](#per-backend-telemetry-namespaces)).

## Configuration

Add a `telemetry:` block to the harness `culture.yaml` (the template lives in
`packages/agent-harness/culture.yaml`; each backend's copy is in
`culture/clients/<backend>/culture.yaml`):

```yaml
telemetry:
  enabled: false                          # master switch — flip to true to start exporting
  service_name: culture.harness.claude    # set per-backend; see namespaces section
  otlp_endpoint: http://localhost:4317    # OTLP/gRPC receiver endpoint
  otlp_protocol: grpc                     # grpc or http/protobuf
  otlp_timeout_ms: 5000                   # export request timeout in milliseconds
  otlp_compression: gzip                  # gzip | none
  traces_enabled: true                    # enable distributed tracing
  traces_sampler: parentbased_always_on   # honor the server's sampling decision
  metrics_enabled: true                   # enable LLM call metrics export
  metrics_export_interval_ms: 10000       # how often to push metric batches (ms)
```

Field notes:

- **`enabled: false`** is the default. Operators must opt in. When disabled, the
  SDK is not initialised — no export, no overhead. Traceparent tags on inbound
  messages are still parsed.
- **`service_name`** is the value that appears as `service.name` in your tracing
  backend. Set it to the backend-specific value from the table in
  [Per-backend telemetry namespaces](#per-backend-telemetry-namespaces) rather
  than the default `culture.harness` placeholder.
- **`otlp_endpoint`** / **`otlp_protocol`** point at your collector. The default
  matches `otelcol-contrib` started with `docs/agentirc/otelcol-template.yaml`.
- **`traces_sampler: parentbased_always_on`** honours the server's sampling
  decision: if the server sampled a trace and passed `traceparent` to the harness,
  the harness samples the child spans too. Alternative values match the server's
  semantics: `parentbased_traceidratio:0.1` for 10 % sampling, `always_off` to
  suppress entirely.
- **`metrics_export_interval_ms`** sets the push cadence. 10 s matches the server
  default so metric series align in time in Grafana.

Standard OpenTelemetry env vars override YAML: `OTEL_SERVICE_NAME`,
`OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_TRACES_SAMPLER`.

## Per-backend telemetry namespaces

Each harness backend runs as an independent process with its own
`MeterProvider` and `TracerProvider`. In Grafana / Prometheus they appear as
separate services:

| Backend | `service.name` | `service.instance.id` |
|---------|----------------|-----------------------|
| claude | `culture.harness.claude` | agent nick (e.g. `spark-claude`) |
| codex | `culture.harness.codex` | agent nick (e.g. `spark-codex`) |
| copilot | `culture.harness.copilot` | agent nick (e.g. `spark-copilot`) |
| acp | `culture.harness.acp` | agent nick (e.g. `spark-acp`) |

`service.instance.id` is the agent's IRC nick, derived from the nick(s) in
`culture.yaml`. If multiple agents share a daemon, the nicks are joined with
`-`. This lets you distinguish two `culture.harness.claude` processes running
on different machines (e.g. `spark-claude` vs `thor-claude`) in a single
Grafana instance.

The tracer name also equals `service.name` (e.g. `culture.harness.claude`) —
don't mix backends in the same provider.

## Token-usage caveats

`culture.harness.llm.calls{outcome=*}` and `culture.harness.llm.call.duration`
work for all four backends because they depend only on the LLM call completing
(or failing), not on token-count data in the response.

Token counters (`culture.harness.llm.tokens.input` and
`culture.harness.llm.tokens.output`) depend on the backend SDK exposing usage
data:

- **claude** — `ResultMessage.usage` carries `input_tokens` / `output_tokens`.
  Both counters increment correctly.
- **acp** — token counts arrive in the `session/update` `stopReason` payload when
  the backing agent exposes them. Both counters increment when the data is present.
- **codex** — the `turn/completed` notification does not currently expose token
  counts. Both token counters stay at zero. Tracked in
  [#298](https://github.com/agentculture/culture/issues/298).
- **copilot** — the current SDK does not expose `input_tokens` / `output_tokens`
  on the response. Both token counters stay at zero. Tracked in
  [#299](https://github.com/agentculture/culture/issues/299).

If you see `culture.harness.llm.tokens.input` flat for codex or copilot, your
dashboards are not broken — the data is simply not yet available from those SDKs.

## What's not in 8.6.0

- **Bot-side OTEL instrumentation** — Plan 6.
- **Tracestate injection** — server-parity-deferred. Both server-side
  `client.py` / `server_link.py` and the harness currently pass `tracestate=None`
  when injecting. A future plan will add `current_tracestate()` to
  `culture.telemetry.context` and thread it through both sides simultaneously.
- **`culture.clients.connected{kind}` refinement to `kind=harness`** — still
  `kind=human` until a server-side detection signal (CAP token, new culture verb,
  or USER-suffix convention) lands.
- **Audit `actor.kind` refinement** — same blocker. Stays `"human"` in v1.
- **Token-usage extraction for codex / copilot** — tracked via
  [#298](https://github.com/agentculture/culture/issues/298) (codex) and
  [#299](https://github.com/agentculture/culture/issues/299) (copilot).

## Manual end-to-end test

This recipe walks the full cross-process trace from a PRIVMSG on the originating
server all the way into a harness LLM call.

**1. Start the collector:**

```bash
otelcol-contrib --config=docs/agentirc/otelcol-template.yaml
```

The template uses a `debug` exporter — spans and metrics print to stdout.

**2. Start the server with telemetry enabled:**

```bash
# ~/.culture/server.yaml must have telemetry.enabled: true
culture server start --name spark
```

**3. Start the claude harness with telemetry enabled:**

```bash
# culture/clients/claude/culture.yaml must have telemetry.enabled: true
# and service_name: culture.harness.claude
culture start spark-claude
```

**4. Send a PRIVMSG that mentions the harness:**

```text
(in weechat or irssi, connected to spark)
/msg #general @spark-claude hi
```

**5. Verify traces in the collector output:**

Look for a single `trace_id` that covers all four spans in parent-child order:

```text
irc.command.PRIVMSG           (service: culture.agentirc)
└── irc.event.emit             (service: culture.agentirc)
    └── harness.irc.message.handle  (service: culture.harness.claude)
        └── harness.llm.call        (service: culture.harness.claude)
```

**6. Verify the LLM call counter:**

In the collector metric output, confirm:

```text
culture.harness.llm.calls{backend="claude", model="...", outcome="success"} 1
```

Also check that `culture.harness.llm.tokens.input` and
`culture.harness.llm.tokens.output` carry non-zero values (claude exposes token
counts via `ResultMessage.usage`).
