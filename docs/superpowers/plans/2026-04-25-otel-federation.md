# OTEL Federation Trace-Context Relay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend Plan 1's trace context across the federation boundary so a single `trace_id` spans every hop of a federated message and produces a parent-per-hop span tree, while sweeping in two open issues (#290, #291) that naturally fall in the touched files.

**Architecture:** Hoist trace-context helpers from `client.py` into the public `culture.telemetry.context` surface, instrument `culture/agentirc/server_link.py` at three points (`handle` session span, `_dispatch` per-verb spans with inbound mitigation, `relay_event` re-sign-per-hop span), and inject `culture.dev/traceparent` at the single send choke point (`ServerLink.send_raw`). Fold in #290 (client session + JOIN/PART spans) and #291 (`event.type` enum-vs-string fast-path bug).

**Tech Stack:** OpenTelemetry SDK (`opentelemetry-api`, `opentelemetry-sdk`), pytest + pytest-asyncio + pytest-xdist, existing `linked_servers` + `tracing_exporter` test fixtures.

---

## Context

Plan 1 (PR #292, merged as `9aea96a`) shipped server-side tracing on `IRCd.emit_event` and `Client._dispatch`, plus W3C trace-context extraction/injection on the **client-facing** boundary (`culture/agentirc/client.py` + helpers in `culture/telemetry/context.py`). The federation boundary (`culture/agentirc/server_link.py`) is currently **untouched** — when a PRIVMSG hops from `alpha` to `beta`, the trace breaks at the wire.

Plan 2 closes that gap. After it ships, a single `trace_id` will span every hop of a federated message: client → originating server → S2S relay → receiving server → bot/skill, producing a parent-per-hop span tree as documented in `culture/protocol/extensions/tracing.md`.

The same branch also sweeps in two open issues that naturally fall in `server_link.py` / `client.py`:

- **#290** — `Client.handle` session span + `_handle_join`/`_handle_part` spans (deferred from Plan 1).
- **#291** — `server_link.py:821` `EventType` comparison bug (string-typed federated events skip the typed fast path). One-line fix.

`culture.trace.inbound{result, peer}` metric stays deferred to **Plan 3** (Metrics pillar) — instrument inbound mitigation only with span attributes here, not counters.

## Critical files to read before implementing

- `docs/superpowers/specs/2026-04-24-otel-observability-design.md` — sections "Protocol: IRCv3 trace-context tags", "Instrumentation points → server_link.py", "Metrics catalog → Trace-context hygiene".
- `docs/superpowers/plans/2026-04-24-otel-foundation.md` — Plan 1 reference for tone, TDD discipline, task granularity.
- `culture/protocol/extensions/tracing.md` — protocol contract Plan 2 must honor (already documents re-sign-per-hop; Plan 2 extends only the example block).
- `culture/agentirc/server_link.py` (886 lines) — the file Plan 2 instruments.
- `culture/agentirc/client.py:27-60` — `_TRACER_NAME`, `_ATTR_*` constants, `_context_from_traceparent`, `_current_traceparent` — to be hoisted to `culture/telemetry/context.py`.
- `culture/telemetry/context.py` — `extract_traceparent_from_tags`, `inject_traceparent`, `ExtractResult`, length caps and regex.
- `tests/conftest.py` — `linked_servers` (yields `(server_a, server_b)` post-handshake), `make_client_a`/`make_client_b` (nicks `alpha-<name>`/`beta-<name>`), `tracing_exporter` (`InMemorySpanExporter` + `SimpleSpanProcessor`). All three compose.
- `tests/test_federation.py` — pattern reference for two-server tests.

## Approach

### 1. Hoist trace-context helpers to `culture/telemetry/context.py`

`server_link.py` needs `_context_from_traceparent` (build OTEL Context from W3C string) and `_current_traceparent` (W3C string from active span). They live in `client.py:34-60` today. Hoist them — both files end up importing from `culture.telemetry.context`.

- Move `_context_from_traceparent` → `context_from_traceparent` (public; drops underscore).
- Move `_current_traceparent` → `current_traceparent` (public).
- Add to `culture/telemetry/__init__.py` `__all__`.
- Update `client.py` to import from `culture.telemetry`.

### 2. Single choke point for traceparent injection: `ServerLink.send_raw`

Federation has **11 typed `_relay_*` handlers** plus a fallback SEVENT path, all calling `send_raw(line: str)` — a single async method (line 64). Inject at the choke point rather than per call site:

```python
async def send_raw(self, line: str) -> None:
    tp = current_traceparent()
    if tp:
        line = _prepend_trace_tags(line, tp)
    try:
        self.writer.write(f"{line}\r\n".encode("utf-8"))
        await self.writer.drain()
    except OSError:
        pass
```

`_prepend_trace_tags(line, tp)` is a tiny string helper that handles two cases:

- Line has no `@`-prefix tag block → prepend `@culture.dev/traceparent=<tp> `.
- Line already has `@`-prefix tag block (rare; today no relay path emits one but be safe) → merge `culture.dev/traceparent=<tp>` into existing block.

Rationale: matches the spec ("re-sign per hop"), gives one place to test, and preserves the property that **PASS/SERVER handshake messages ARE tagged** (the session span is open by then) — additive IRCv3, peer parsers ignore unknown tags.

Tracestate is intentionally not propagated by Plan 2's federation injector (Plan 1's `inject_traceparent` does support it but the relay is span-driven, not message-copy). If observability needs vendor `tracestate` propagation, lift it in a follow-up — current spec only requires `traceparent`.

### 3. Wrap `ServerLink.handle` with `irc.s2s.session` span

Open span before `_send_handshake` so handshake messages carry traceparent. Direction is known at construction (`self.initiator`). Peer name only known after handshake — use `span.set_attribute("s2s.peer", self.peer_name)` from inside `_try_complete_handshake` (line 191) once `peer_name` is set.

```python
async def handle(self, initial_msg: str | None = None) -> None:
    tracer = otel_trace.get_tracer(_TRACER_NAME)
    direction = "outbound" if self.initiator else "inbound"
    with tracer.start_as_current_span(
        "irc.s2s.session",
        attributes={"s2s.direction": direction},
    ) as span:
        self._session_span = span  # for late peer attr assignment
        try:
            ...existing body...
        finally:
            ...existing teardown...
```

`_try_complete_handshake` adds: `if self._session_span: self._session_span.set_attribute("s2s.peer", self.peer_name)`.

### 4. Wrap `ServerLink._dispatch` for inbound mitigation + per-verb spans

Apply the spec's mitigation rules in one place. Open `irc.s2s.<VERB>` span; if traceparent extracts as `valid`, the span is parented under the remote context; if `malformed`/`too_long`, set attrs but still open as root. Set `culture.trace.origin=remote` and `culture.federation.peer=<peer_name>` always (federation = remote by definition).

```python
async def _dispatch(self, msg: Message) -> None:
    verb = msg.command.upper()
    handler = getattr(self, f"_handle_{msg.command.lower()}", None)
    tracer = otel_trace.get_tracer(_TRACER_NAME)

    extracted = extract_traceparent_from_tags(msg, peer=self.peer_name)
    parent_ctx = None
    if extracted.status == "valid":
        parent_ctx = context_from_traceparent(extracted.traceparent)

    attrs = {
        "irc.command": verb,
        "culture.trace.origin": "remote",
        "culture.federation.peer": self.peer_name or "",
    }
    if extracted.status in ("malformed", "too_long"):
        attrs["culture.trace.dropped_reason"] = extracted.status

    with tracer.start_as_current_span(
        f"irc.s2s.{verb}", context=parent_ctx, attributes=attrs
    ):
        if handler:
            await maybe_await(handler(msg))
```

Note: Plan 1's `_dispatch` in `client.py` uses the same pattern — mirror it. `peer=None` for client, `peer=self.peer_name` for federation.

### 5. Wrap `ServerLink.relay_event` with `irc.s2s.relay` span

The current span context determines what `send_raw` injects, so a fresh `irc.s2s.relay` span here means **child spans on the peer side parent under this relay span, not under the inbound span** — exactly the re-sign-per-hop rule.

```python
async def relay_event(self, event: Event) -> None:
    tracer = otel_trace.get_tracer(_TRACER_NAME)
    event_type_str = event.type.value if hasattr(event.type, "value") else str(event.type)
    attrs = {
        "event.type": event_type_str,
        "s2s.peer": self.peer_name or "",
    }
    with tracer.start_as_current_span("irc.s2s.relay", attributes=attrs):
        ...existing body...
```

### 6. Issue #291 fix — `_replay_event` line 821

```python
event_type_str = event.type.value if hasattr(event.type, "value") else str(event.type)
if event_type_str == EventType.MESSAGE.value:
    ...
```

### 7. Issue #290 fold-in — `Client.handle` session span + JOIN/PART spans

In `client.py`:

- Wrap `Client.handle` (the connection-loop entry point) with `irc.client.session` span. Attrs: `irc.client.remote_addr` set at entry (peername known); `irc.client.nick` set lazily once `self.nick` is assigned (mirror the `_session_span` field pattern from §3 — set in `_handle_nick` after assignment, line 305).
- In `_handle_join` (line 350): wrap the body with `irc.join` span, attrs `irc.channel`, `irc.client.nick`. Open span before the `_registered` guard so we still see the rejection if it happens.
- In `_handle_part` (line 398): wrap with `irc.part` span, same attr shape.

These spans nest naturally under `irc.command.JOIN` / `irc.command.PART` already opened by Plan 1's `_dispatch`. Two-level nesting is intentional: the outer span is verb-level (covers parse-error edge cases via `add_event`), inner span is JOIN/PART semantics with channel attr.

### 8. Documentation

Extend `culture/protocol/extensions/tracing.md` example block with a two-line SEVENT relay illustration showing the trace-id is preserved while parent-id changes per hop. Existing rules already cover the contract.

### 9. Version bump

`/version-bump minor` → `8.2.0` → `8.3.0`. Updates `pyproject.toml`, `culture/__init__.py`, `CHANGELOG.md`. Stage `uv.lock` if it moves.

## Files to modify

- `culture/telemetry/context.py` — add public `context_from_traceparent`, `current_traceparent`.
- `culture/telemetry/__init__.py` — re-export the new helpers.
- `culture/agentirc/client.py` — replace local `_context_from_traceparent` / `_current_traceparent` with imports; add `Client.handle` session span + `_handle_join` / `_handle_part` spans (#290).
- `culture/agentirc/server_link.py` — session span on `handle`, dispatch wrapper, relay span, traceparent injection in `send_raw`, late peer-attr in `_try_complete_handshake`, fix line 821 (#291).
- `culture/protocol/extensions/tracing.md` — extend example block.
- `tests/telemetry/test_federation_propagation.py` — **new**, federation propagation tests (see "Tests" below).
- `tests/telemetry/test_session_span.py` — **new**, `Client.handle` + `_handle_join` / `_handle_part` span coverage for #290.
- `tests/test_federation.py` (extend) — regression for #291.
- `pyproject.toml`, `culture/__init__.py`, `CHANGELOG.md` — version bump 8.2.0 → 8.3.0.

## Tests

Use `tracing_exporter` + `linked_servers` together (verified compatible in conftest.py).

**Federation propagation (`test_federation_propagation.py`):**

1. `test_inbound_valid_traceparent_creates_child_span` — peer sends `SMSG` with valid traceparent tag; assert one of the recorded spans on the receiver has matching `trace_id` and an attr `culture.trace.origin=remote` + `culture.federation.peer=<peer>`.
2. `test_inbound_missing_traceparent_starts_root` — same setup, no tag; receiver span has no remote parent (root) and `culture.trace.origin=remote`.
3. `test_inbound_malformed_traceparent_dropped` — receiver span attr `culture.trace.dropped_reason=malformed`.
4. `test_inbound_oversize_tracestate_dropped` — `culture.trace.dropped_reason=too_long`.
5. `test_relay_reinjects_per_hop` — local PRIVMSG on `alpha`, captured wire bytes on the link to `beta` carry a `culture.dev/traceparent` whose **parent-id differs** from any client-side span's id (it's the relay span's id, not the originating client span's id). `trace_id` matches across.
6. `test_two_server_propagation_e2e` — `linked_servers` + `tracing_exporter`; client on `alpha` sends PRIVMSG to channel `beta-bob` is in; assert all spans recorded share one `trace_id`, span tree includes both `service.instance.id` resource attrs (`alpha`, `beta`).
7. `test_session_span_records_peer` — open link, complete handshake, assert one `irc.s2s.session` span has attrs `s2s.peer` + `s2s.direction=inbound|outbound`.
8. `test_relay_no_active_span_no_inject` — `init_telemetry(enabled=False)`; outbound relay carries no `culture.dev/traceparent` tag.

**#290 spans (`test_session_span.py`):**

9. `test_client_session_span_lifetime` — open client connection, `irc.client.session` span exists, attrs include `irc.client.remote_addr` + (after NICK) `irc.client.nick`.
10. `test_join_span_emits_with_channel_attr` — JOIN `#general`, an `irc.join` span exists nested under `irc.command.JOIN`, attrs `irc.channel=#general`, `irc.client.nick`.
11. `test_part_span_emits_with_channel_attr` — same shape for PART.

**#291 regression (extend `test_federation.py`):**

12. `test_replay_unknown_event_type_no_crash` — federated event arrives with a wire `type` not in our `EventType` enum; `_replay_event` does not raise, falls through to generic SEVENT relay rather than the typed fast path. (One assertion: relay completes; second: typed path does not fire.)

## Verification

1. `bash ~/.claude/skills/run-tests/scripts/test.sh -p` — full suite green.
2. `bash ~/.claude/skills/run-tests/scripts/test.sh -p tests/telemetry/` — telemetry suite green.
3. `bash ~/.claude/skills/run-tests/scripts/test.sh -p tests/test_federation.py tests/telemetry/test_federation_propagation.py` — federation + propagation green together (catches `event.type` regression class).
4. Manual: start two local servers linked, attach weechat to each, send PRIVMSG across; with `telemetry.enabled=true` and a debug-exporter `otelcol-contrib`, confirm one `trace_id` spans both servers' spans.
5. `Agent(subagent_type="doc-test-alignment", ...)` before first push — flags any new public API surface (`culture.telemetry.context_from_traceparent` / `current_traceparent` are new public API).
6. `Agent(subagent_type="superpowers:code-reviewer", ...)` on staged diff before first push — `server_link.py` is a transport choke point exactly per CLAUDE.md guidance.
7. `/sonarclaude` on the branch before declaring PR ready (cognitive-complexity hotspots likely on the new `_dispatch` wrapper).

## Out of scope (future plans)

- `culture.trace.inbound{result, peer}` metric → **Plan 3** (Metrics pillar). Span attrs in §4 already record the same data; the counter is a Plan-3 view over them.
- Audit JSONL emission of S2S relays → **Plan 4**.
- Harness-side trace propagation across `IRCTransport` → **Plan 5**.
- Bot webhook outbound traceparent → **Plan 6** (aiohttp auto-instrument handles it).

## Carry-forward notes (for compaction / future plans)

- `event.type` is `EventType | str` everywhere it crosses the federation boundary (`_parse_event_type` returns either). EVERY new code path that reads `event.type` must use `event.type.value if hasattr(event.type, "value") else str(event.type)`. This caught Plan 1 in pre-push tests and is the root of #291.
- `tracing_exporter` fixture rebuilds the global TracerProvider per test. Never cache a tracer at module scope — always `otel_trace.get_tracer(_TRACER_NAME)` per call site.
- `_initialized_for` snapshot in `tracing.py` uses `dataclasses.asdict(tcfg)` on the dataclass to detect mutation. Plan 3 must mirror this if it adds `init_metrics(config)`.
- Plan-1 lesson: protocol docs go to `culture/protocol/extensions/`, not `protocol/extensions/`. Confirm with `ls culture/protocol/extensions/` before placing.
- SonarCloud hotspots on `http://localhost:4317` literal in tests are routinely flagged; mark REVIEWED/SAFE via the SonarCloud API after each plan.
- For client-side tests, `FakeWriter` in `tests/telemetry/_fakes.py` is the convention — server-side tests use real TCP via `linked_servers` (no mocks for the IRCd).
