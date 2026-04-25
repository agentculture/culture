# Plan 5 — OTEL Harness-side Tracing & LLM Metrics

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development`. Branch `feature/otel-harness`. Tracking issues for token-usage gaps: codex #298, copilot #299.

## Context

Plans 1–4 shipped the **server side** of OTEL: traces (cmd/event/federation spans), the metrics pillar (15 server instruments + audit health), and the audit JSONL sink. Plan 5 crosses the **harness boundary** — the per-backend agent runner that bridges the IRC mesh to an LLM. After this ships, a single `trace_id` flows from `irc.command.PRIVMSG` on the originating server, across federation, into the receiving server's `irc.event.emit`, into the harness's `harness.irc.message.handle`, and finally into `harness.llm.call` — and four new LLM metrics (`culture.harness.llm.tokens.input/output`, `culture.harness.llm.call.duration`, `culture.harness.llm.calls`) appear next to the server-side metrics in the same Grafana / Prometheus instance.

Spec coverage: `docs/superpowers/specs/2026-04-24-otel-observability-design.md`

- :22-44 Architecture (harness telemetry module + per-backend citation)
- :97-107 Instrumentation points (transport boundary spans + LLM call span)
- :152-157 Harness LLM metrics catalog (4 instruments, all with `backend`/`model`/`outcome` labels)
- :230-247 Harness config block (cited into each backend's `culture.yaml` template)
- :283 All-backends parity test

This is the first plan that touches the **citation pattern** at scale — `packages/agent-harness/` is the reference, and each of `culture/clients/{claude,codex,copilot,acp}/` carries a copy. The CLAUDE.md "all-backends rule" applies in full: a feature in only one backend is a bug.

## Critical files to read before implementing

**Spec:**

- `docs/superpowers/specs/2026-04-24-otel-observability-design.md` lines 22-44, 97-107, 152-157, 230-247, 283.

**Server-side patterns to mirror:**

- `culture/telemetry/tracing.py` — idempotency snapshot, `reset_for_tests`, no-op when disabled. Plan 5's `init_harness_telemetry` mirrors this exactly.
- `culture/telemetry/metrics.py` — `MetricsRegistry` dataclass + `_build_registry` + `init_metrics`. Plan 5 builds a parallel `HarnessMetricsRegistry` (it is *not* a sub-registry of the server's; harness runs in a separate process with its own provider).
- `culture/telemetry/context.py` — `extract_traceparent_from_tags`, `inject_traceparent`, `current_traceparent`, `context_from_traceparent`. **Reusable as-is from `culture.telemetry`** — both server and harness import these. Don't duplicate them in `packages/agent-harness/`.
- `culture/agentirc/ircd.py` — `init_metrics(config)` then `init_audit(config, metrics)` ordering at `__init__`; `metrics_reader` fixture pattern in tests.
- `culture/agentirc/client.py:127-160` — outbound `_send_raw` injection site; inbound `_process_buffer` extraction site. Pattern to mirror in `IRCTransport`.

**Reference + citations:**

- `packages/agent-harness/irc_transport.py` — reference. `_send_raw` (l. 155) is the injection site; `_read_loop` / `_handle` (l. 158, 197) are the extraction sites; `_do_connect` (l. 66) is the connect-span site.
- `packages/agent-harness/config.py` — reference `DaemonConfig`. Add `TelemetryConfig` dataclass + field; teach `load_config` to parse `telemetry:`.
- `packages/agent-harness/culture.yaml` — reference template. Add `telemetry:` block.
- `packages/agent-harness/daemon.py` — reference `AgentDaemon.start()`. Plan 5 adds an `init_harness_telemetry(config)` call early.
- `culture/clients/claude/agent_runner.py` — claude backend; LLM call sits inside `query()` async generator at `_process_turn` (l. 131). `ResultMessage.usage` carries token counts.
- `culture/clients/codex/agent_runner.py` — codex backend; LLM turn sits inside `_send_request("turn/start", ...)` at `_execute_single_turn` (l. 336). Token usage arrives via `turn/completed` notification.
- `culture/clients/copilot/agent_runner.py` — copilot backend; LLM call is `self._session.send_and_wait(text, timeout=120.0)` at `_execute_single_turn` (l. 169). Response shape: `response.data.content` (no usage exposed in current SDK; see "Out of scope" below).
- `culture/clients/acp/agent_runner.py` — ACP backend; LLM turn sits inside `_send_request("session/prompt", ...)` at `_send_prompt_with_retry` (l. 399). Token usage arrives in the `session/update` `stopReason` payload (varies by backend).

**Tests:**

- `tests/conftest.py` — `tracing_exporter` and `metrics_reader` fixture pattern. Plan 5's harness fixtures mirror these but install harness-side providers.
- `tests/telemetry/test_audit_module.py` — Plan 4 isolation pattern: build a small fake without spinning up an IRCd. Mirror this for the harness telemetry module tests.

## Approach

### 1. Reference: `packages/agent-harness/telemetry.py` (new)

A self-contained module with:

- `HarnessMetricsRegistry` — dataclass with 4 fields:
  - `llm_tokens_input: Counter` (`culture.harness.llm.tokens.input`, no unit, labels `backend`/`model`/`harness.nick`)
  - `llm_tokens_output: Counter` (`culture.harness.llm.tokens.output`, same labels)
  - `llm_call_duration: Histogram` (`culture.harness.llm.call.duration`, unit `ms`, labels `backend`/`model`/`outcome`)
  - `llm_calls: Counter` (`culture.harness.llm.calls`, labels `backend`/`model`/`outcome`)
- `init_harness_telemetry(config) -> tuple[Tracer, HarnessMetricsRegistry]` — idempotent. Snapshot is `{"telemetry": asdict(tcfg), "nick": <agent.nick or daemon identity>}`. When `enabled: false` returns no-op tracer + proxy-bound registry. When on, installs an SDK TracerProvider + MeterProvider with `service.name=culture.harness.<backend>` and `service.instance.id=<nick>`.
- `record_llm_call(registry, backend, model, nick, usage, duration_ms, outcome)` — single helper called from each backend's `agent_runner.py`. `usage` is `dict | None` with optional `tokens_input` / `tokens_output` keys; missing keys are skipped (some SDKs don't expose token counts). Always increments `llm_calls` and records `llm_call_duration`.
- `reset_for_tests()` — mirrors `culture.telemetry.tracing.reset_for_tests` and `culture.telemetry.metrics.reset_for_tests`.

The module imports `extract_traceparent_from_tags`, `inject_traceparent`, `current_traceparent`, `context_from_traceparent`, `TRACEPARENT_TAG`, `TRACESTATE_TAG` from `culture.telemetry` — the harness is in the same Python package install, so a real `from culture.telemetry import …` works (no duplication).

### 2. Reference config: `packages/agent-harness/config.py`

Add a `TelemetryConfig` dataclass mirroring the server's (subset — no audit, since the server owns audit per spec line 250):

```python
@dataclass
class TelemetryConfig:
    enabled: bool = False
    service_name: str = "culture.harness"
    otlp_endpoint: str = "http://localhost:4317"
    otlp_protocol: str = "grpc"
    otlp_timeout_ms: int = 5000
    otlp_compression: str = "gzip"
    traces_enabled: bool = True
    traces_sampler: str = "parentbased_always_on"
    metrics_enabled: bool = True
    metrics_export_interval_ms: int = 10000
```

Attach to `DaemonConfig.telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)`. Teach `load_config` to parse the new `telemetry:` block (defaulting to defaults when absent — backwards compat).

### 3. Reference template: `packages/agent-harness/culture.yaml`

Add a `telemetry:` block at the top level (alongside `suffix:`, `backend:`, etc.) — mostly comments and defaults. Plan 5 ships with `enabled: false` so freshly installed harnesses don't try to talk to a non-existent collector.

### 4. Reference transport: `packages/agent-harness/irc_transport.py`

Three new spans + the inject/extract pair:

- `_do_connect` (l. 66) — wrap in `harness.irc.connect` span. Attrs: `harness.backend` (from caller), `harness.nick` (from `self.nick`), `harness.server` (from `self.host:self.port`).
- `_send_raw` (l. 155) — when a span is recording, prepend `@culture.dev/traceparent=<value>;culture.dev/tracestate=<value> ` to the line as IRCv3 tag prefix. Stays a string operation — minimal blast radius, matches server-side `client.py`, wire grammar identical (IRCv3 tags are space-prefixed before the prefix or verb). Don't refactor every send-helper to construct `Message` objects.
- `_handle` (l. 197) — wrap in `harness.irc.message.handle` span. Before opening the span, run `extract_traceparent_from_tags(msg, peer=None)`; if `status="valid"`, parent the span to the extracted context via `context_from_traceparent(tp)`. Attrs: `irc.command`, `irc.client.nick`, `culture.trace.origin=remote|local`. On `malformed`/`too_long`, attach `culture.trace.dropped_reason` and start a root.
- Per-backend tracer name: `culture.harness.<backend>` matching `service.name`.

`_handle` is async — wrap with `with self._tracer.start_as_current_span(...)` only when `self._tracer is not None`. Tracer is passed in via the constructor (new optional kwarg `tracer: Tracer | None = None`); when absent, all the propagation code is gated `if self._tracer:` and the no-op fast path stays clean. **All four backends pass it from `daemon.py` after `init_harness_telemetry(config)`.**

### 5. Reference daemon: `packages/agent-harness/daemon.py`

Early in `start()` (before constructing `IRCTransport`):

```python
from culture.clients.BACKEND.telemetry import init_harness_telemetry
self._tracer, self._metrics = init_harness_telemetry(self.config)
```

Then pass `self._tracer` and `self._metrics` to `IRCTransport(...)` as kwargs. Leave existing kwargs untouched.

`record_llm_call` is invoked from each `agent_runner.py`, not from `daemon.py` — but `daemon.py` is responsible for stashing the metrics registry on the runner (via constructor injection or a setter) so the runner can call into it.

### 6. Per-backend citations (the all-backends rule)

For each of `claude`, `codex`, `copilot`, `acp`:

1. **`telemetry.py`** — copy `packages/agent-harness/telemetry.py`. Replace `culture.harness` default `service_name` with `culture.harness.<backend>`. **No other diffs.** This is the citation-parity invariant tested in Step 8.
2. **`config.py`** — add the same `TelemetryConfig` dataclass + field on `DaemonConfig`. Teach `load_config` to parse `telemetry:`.
3. **`culture.yaml`** — add the `telemetry:` block (commented `enabled: false` by default).
4. **`irc_transport.py`** — add the constructor `tracer`/`metrics` kwargs + `harness.irc.*` spans + traceparent inject/extract calls.
5. **`daemon.py`** — call `init_harness_telemetry(config)` in `start()` before `IRCTransport` construction; pass results to transport + agent runner.
6. **`agent_runner.py`** — wrap LLM call in `harness.llm.call` span. Fire `record_llm_call(...)` after. Backend-specific `usage` extraction:
   - **claude:** `_handle_result_message` already has `ResultMessage` — check `getattr(msg, "usage", None)` (claude-agent-sdk exposes `{"input_tokens": int, "output_tokens": int}` on ResultMessage).
   - **codex:** `_handle_notification` on `turn/completed` — params may carry `usage` per the codex app-server protocol (TBD; if absent, record duration + count only with `usage=None`).
   - **copilot:** `send_and_wait` response — current SDK does not expose token counts; record duration + count only with `usage=None`.
   - **acp:** `session/update` final notification — `update` may carry token counts depending on the backing agent; record what's available.
   - **All four:** wrap the LLM call site in a `with self._tracer.start_as_current_span("harness.llm.call", attributes={"harness.backend": ..., "harness.model": self.model})` block. Outcome label: `success` (turn completed), `error` (exception), `timeout` (asyncio.TimeoutError).

Each backend's `agent_runner.py` constructor takes a new `metrics: HarnessMetricsRegistry | None = None` kwarg; when `None`, all metric calls become guarded no-ops.

### 7. Server-side touchpoint (small)

The `culture.clients.connected{kind=...}` label was Plan-3-deferred at `kind=human`. It stays that way for now — refining to `kind=harness` requires the IRCd to detect harness clients via a connection-time signal (a CAP token, USER suffix, or new culture verb). **Defer to a follow-up.** Plan 5 does not change server-side label semantics.

Same applies to the audit `actor.kind` field (Plan 4) — stays `"human"` for v1. Plan 6 (bots) refines to `"bot"`. A future server-side update can refine to `"harness"` once the IRCd has a way to detect.

### 8. All-backends parity test: `tests/harness/test_all_backends_parity.py`

Walks `culture/clients/{claude,codex,copilot,acp}/` and asserts:

- `telemetry.py` exists.
- The file contents match `packages/agent-harness/telemetry.py` modulo the `service_name` default — diff after stripping the `service_name=...` line should be empty.
- `config.py` defines a `TelemetryConfig` dataclass with the 10 expected fields.
- `culture.yaml` parses and has a `telemetry:` block.
- `agent_runner.py` imports `record_llm_call` (or its module).
- `irc_transport.py` constructor accepts `tracer` / `metrics` kwargs.

This test fails the build if any backend drifts, enforcing the all-backends rule.

### 9. Other tests

- `tests/harness/test_telemetry_module.py` — unit-test `init_harness_telemetry` (idempotent snapshot, no-op when disabled, real provider when on), `record_llm_call` (4 metrics emitted, missing-`usage`-key paths skip the right counters).
- `tests/harness/test_irc_transport_propagation.py` — fake `StreamReader`/`StreamWriter`; build an `IRCTransport` with a test tracer; emit a line with `@culture.dev/traceparent=<valid>` prefix; assert `_handle` opens a child span linked to that traceparent. Then assert outbound `send_privmsg` while a span is recording carries `culture.dev/traceparent` in the wire bytes.
- `tests/harness/test_record_llm_call.py` — fake `HarnessMetricsRegistry`; call `record_llm_call` with various `usage` shapes; assert every code path records the right metrics and labels.
- `tests/harness/test_agent_runner_<backend>.py` (4 files) — mock the LLM call layer; assert the `harness.llm.call` span opens, `record_llm_call` is invoked with correct `outcome=success|error|timeout`, and `harness.backend` / `harness.model` attrs are present.

### 10. Documentation

- New `docs/agentirc/harness-telemetry.md` — operator guide. Pages parallel to `docs/agentirc/audit.md`: "What you get in 8.6.0" / "Configuring it" / "Per-backend telemetry namespaces" / "What's not in 8.6.0".
- Extend `docs/agentirc/telemetry.md` with a "What you get in 8.6.0" subsection: harness-side OTEL, 4 LLM metrics, traceparent across the harness boundary, all-backends parity.
- Extend `packages/agent-harness/README.md` (small) — point at the new telemetry config block in the template.

### 11. Version bump

`/version-bump minor` → `8.5.0` → `8.6.0`. CHANGELOG entry summarizing the 4 new harness metrics + the harness-boundary trace-context propagation.

## Files to modify / create

**New:**

- `packages/agent-harness/telemetry.py` — reference module.
- `culture/clients/claude/telemetry.py` — citation.
- `culture/clients/codex/telemetry.py` — citation.
- `culture/clients/copilot/telemetry.py` — citation.
- `culture/clients/acp/telemetry.py` — citation.
- `docs/agentirc/harness-telemetry.md` — operator guide.
- `tests/harness/__init__.py`
- `tests/harness/test_telemetry_module.py`
- `tests/harness/test_irc_transport_propagation.py`
- `tests/harness/test_record_llm_call.py`
- `tests/harness/test_all_backends_parity.py`
- `tests/harness/test_agent_runner_claude.py`
- `tests/harness/test_agent_runner_codex.py`
- `tests/harness/test_agent_runner_copilot.py`
- `tests/harness/test_agent_runner_acp.py`

**Modified:**

- `packages/agent-harness/config.py` — `TelemetryConfig` dataclass + field + load/save.
- `packages/agent-harness/culture.yaml` — `telemetry:` block.
- `packages/agent-harness/irc_transport.py` — `tracer`/`metrics` kwargs + `harness.irc.*` spans + traceparent inject/extract.
- `packages/agent-harness/daemon.py` — `init_harness_telemetry` call + transport/runner wiring.
- `packages/agent-harness/README.md` — pointer to telemetry block.
- `culture/clients/{claude,codex,copilot,acp}/config.py` — `TelemetryConfig` mirror.
- `culture/clients/{claude,codex,copilot,acp}/culture.yaml` — `telemetry:` block.
- `culture/clients/{claude,codex,copilot,acp}/irc_transport.py` — span/inject mirror.
- `culture/clients/{claude,codex,copilot,acp}/daemon.py` — init call + wiring mirror.
- `culture/clients/{claude,codex,copilot,acp}/agent_runner.py` — `harness.llm.call` span + `record_llm_call` invocation.
- `docs/agentirc/telemetry.md` — "What you get in 8.6.0" subsection.
- `pyproject.toml`, `CHANGELOG.md` — version bump.

(Server-side `culture/agentirc/`, `culture/telemetry/`, server tests: **not modified**. Plan 5 is harness-only.)

## Tests

`bash ~/.claude/skills/run-tests/scripts/test.sh -p tests/harness/` exercises the new module + all-backends parity. Full-suite run via the same skill.

Test fixtures (added to `tests/conftest.py`):

- `harness_tracing_exporter` — analogous to `tracing_exporter` but installs the harness-side provider (different reset path since `init_harness_telemetry` lives in the harness module).
- `harness_metrics_reader` — analogous to `metrics_reader`.

Both reset their respective module state between tests.

## Verification

1. `bash ~/.claude/skills/run-tests/scripts/test.sh -p` — full suite green (current baseline 1051).
2. `bash ~/.claude/skills/run-tests/scripts/test.sh -p tests/harness/` — harness suite green.
3. `Agent(subagent_type="doc-test-alignment", ...)` before first push.
4. `Agent(subagent_type="superpowers:code-reviewer", ...)` on staged diff — touches transport boundaries (`_send_raw`-style I/O) and the citation pattern across 4 backends, exactly the choke-point CLAUDE.md flags.
5. **Manual end-to-end** (per spec line 313): start `culture server` with `telemetry.enabled=true`. Start `claude` harness with `telemetry.enabled=true`. Send a PRIVMSG mentioning the harness from weechat. In the otelcol-contrib `debug` exporter output, confirm:
   - A `trace_id` covers `irc.command.PRIVMSG` (server) → `irc.event.emit` (server) → `harness.irc.message.handle` (harness) → `harness.llm.call` (harness).
   - `culture.harness.llm.calls{backend=claude, model=…, outcome=success}` increments by 1.
   - `culture.harness.llm.tokens.input` / `output` carry the SDK-reported token counts.
6. `bash ~/.claude/skills/sonarclaude/scripts/sonar.sh status` on the branch before declaring PR ready.
7. `bash ~/.claude/skills/pr-review/scripts/pr-status.sh <PR>` after push.

## Out of scope (future plans / follow-ups)

- **Refining `culture.clients.connected{kind=human}`** to `kind=harness` for harness-originated connections. Requires a server-side detection signal (CAP token, new culture verb, or USER-suffix convention). Track as a follow-up.
- **Refining audit `actor.kind`** from `"human"` to `"harness"` for harness-originated emit_event paths. Same blocker. Track as a follow-up.
- **Copilot token-usage metrics** — current `copilot` SDK does not expose `input_tokens` / `output_tokens` on the response. We record duration + call count only; token counters stay at zero for the copilot backend until the SDK exposes the data. Document this in `harness-telemetry.md` so operators don't think their dashboards are broken.
- **Codex token-usage metrics** — depend on the codex app-server's `turn/completed` notification carrying `usage`. If absent in current versions, treat the same as copilot (duration + count only).
- **Bot-side OTEL instrumentation** — Plan 6.
- **Federated-lifecycle audit gap (#296)** — orthogonal, will be addressed in a follow-up that flips the tombstone test in `test_audit_federation.py`.
- **`audit-prune` CLI** — still deferred from Plan 4.
- **OTEL Logs export** of audit records — still deferred from Plan 4.

## Carry-forward notes (for future plans / compaction survivors)

- **Reuse `culture.telemetry.context`.** Both server and harness `IRCTransport` import the same `extract_traceparent_from_tags` / `inject_traceparent` helpers — don't duplicate them in the harness reference. The harness Python install includes `culture.telemetry`.
- **`HarnessMetricsRegistry` is parallel to `MetricsRegistry`, not a subclass.** Different process, different provider, different `service.name`. Plan 6's bot-side metrics (if process-isolated) will follow the same parallel-registry pattern; if bots stay in-process with the IRCd, they extend `MetricsRegistry`.
- **Citation-parity test is the hard fence.** Drift between `packages/agent-harness/telemetry.py` and any backend's copy fails the test. When updating the reference, update all four citations in the same commit.
- **Tracer name = `culture.harness.<backend>`** matches `service.name`. Don't mix.
- **`_send_raw` tag injection is string-prefix.** Don't refactor every send-helper to construct `Message` objects — pre-pending `@culture.dev/traceparent=...;culture.dev/tracestate=... ` to the wire line is wire-grammar valid and minimizes blast radius.
- **`record_llm_call` accepts `usage=None`** — backends that don't expose token counts still record `llm_calls` + `llm_call_duration`. The Counter for token counts simply doesn't increment. Document this contract on the helper.
- **Outcome labels: `success`, `error`, `timeout`.** Standardized across backends. `success` = LLM turn completed; `error` = exception caught; `timeout` = `asyncio.TimeoutError` (codex 300s, copilot 120s, acp 300s, claude no explicit timeout — treat any cancellation/timeout as `error`).
- **Test fixtures must be reset before AND after.** Like the server-side fixtures, harness fixtures call `harness_telemetry.reset_for_tests()` in setup and teardown to avoid global-provider leakage between parallel xdist workers.
- **The harness has its own MeterProvider.** Don't share with the server in tests — `metrics_reader` and `harness_metrics_reader` install separate providers; tests targeting the harness use the latter.

## Phasing (suggested task breakdown for subagent execution)

1. **Task 1**: `packages/agent-harness/telemetry.py` reference module + `TelemetryConfig` in `packages/agent-harness/config.py` + `culture.yaml` template block. Isolated unit tests (`test_telemetry_module.py`, `test_record_llm_call.py`).
2. **Task 2**: `packages/agent-harness/irc_transport.py` — tracer kwarg, `harness.irc.*` spans, traceparent inject/extract. Tests (`test_irc_transport_propagation.py`).
3. **Task 3**: `packages/agent-harness/daemon.py` — `init_harness_telemetry` call + transport/runner wiring.
4. **Task 4**: Cite into `culture/clients/claude/` (telemetry.py + config.py + culture.yaml + irc_transport.py + daemon.py + agent_runner.py with `harness.llm.call` span + `record_llm_call`). Test (`test_agent_runner_claude.py`).
5. **Task 5**: Cite into `culture/clients/codex/`. Test (`test_agent_runner_codex.py`).
6. **Task 6**: Cite into `culture/clients/copilot/`. Test (`test_agent_runner_copilot.py`).
7. **Task 7**: Cite into `culture/clients/acp/`. Test (`test_agent_runner_acp.py`).
8. **Task 8**: All-backends parity test (`test_all_backends_parity.py`). Intentionally last — locks down the citation invariant once all four are in.
9. **Task 9**: Docs — `docs/agentirc/harness-telemetry.md` + extend `docs/agentirc/telemetry.md` "What you get in 8.6.0".
10. **Task 10**: Version bump 8.5.0 → 8.6.0.
11. **Task 11**: Pre-push verification (doc-test-alignment, code-reviewer on the multi-backend diff, run-tests, sonar status) + open PR.

## Branch & worktree

- Branch from up-to-date `main`: `git checkout -b feature/otel-harness`.
- No worktree — same-session subagent-driven execution per the user's preference and the pattern from Plans 1–4.
