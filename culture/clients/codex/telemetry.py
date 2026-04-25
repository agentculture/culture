"""OpenTelemetry bootstrap for Culture agent harnesses.

Why this file lives in ``packages/agent-harness/``
---------------------------------------------------
The culture codebase uses a **cite, don't import** pattern for the per-backend
agent harnesses in ``culture/clients/{claude,codex,copilot,acp}/``. Each backend
owns a verbatim copy of this file (with the ``service_name`` default changed to
``culture.harness.<backend>``). Backend-specific overrides are isolated to those
two sites; all other logic must stay identical so the all-backends parity test
(``tests/harness/test_all_backends_parity.py``) passes.

This is the codex citation of the reference module in
``packages/agent-harness/telemetry.py``.

Backend-specific edit sites (two, both must be updated on citation)
--------------------------------------------------------------------
1. ``TelemetryConfig.service_name`` in ``config.py`` — change the default from
   ``"culture.harness"`` to ``"culture.harness.<backend>"``.
2. ``_HARNESS_TRACER_NAME`` constant in this file — change the value from
   ``"culture.harness"`` to ``"culture.harness.<backend>"`` to match.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Counter, Histogram, Meter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import (
    ALWAYS_OFF,
    ALWAYS_ON,
    ParentBased,
    Sampler,
    TraceIdRatioBased,
)
from opentelemetry.trace import Tracer

logger = logging.getLogger(__name__)

# Module-level tracer name — cited backends replace "culture.harness" with
# "culture.harness.<backend>" to match their service.name.
_HARNESS_TRACER_NAME = "culture.harness.codex"

# Module-level globals — mirrors the server-side tracing.py / metrics.py pattern.
_initialized_for: dict | None = None
_tracer: Tracer | None = None
_meter_provider: MeterProvider | None = None
_registry: HarnessMetricsRegistry | None = None


@dataclass
class HarnessMetricsRegistry:
    """All harness-side LLM instruments, registered once during init_harness_telemetry.

    Parallel to the server's ``MetricsRegistry`` — different process, different
    provider, different ``service.name``. Plan 6's bot-side metrics follow the
    same parallel-registry pattern if bots are process-isolated.
    """

    llm_tokens_input: Counter
    """Per-LLM-call input token count by backend/model/nick."""

    llm_tokens_output: Counter
    """Per-LLM-call output token count by backend/model/nick."""

    llm_call_duration: Histogram
    """Per-LLM-call wall-clock duration in milliseconds."""

    llm_calls: Counter
    """Per-LLM-call count by backend/model/outcome."""


def reset_for_tests() -> None:
    """Reset module state so each test gets a fresh provider. Test-only.

    Mirrors ``culture.telemetry.metrics.reset_for_tests`` (MeterProvider
    shutdown + global clear) and ``culture.telemetry.tracing.reset_for_tests``
    (trace provider global clear). Both are needed because parallel xdist
    workers can leak global providers across module boundaries.
    """
    global _initialized_for, _tracer, _meter_provider, _registry

    if _meter_provider is not None:
        try:
            _meter_provider.shutdown()
        except Exception:  # noqa: BLE001
            pass
        _meter_provider = None

    _initialized_for = None
    _tracer = None
    _registry = None

    # Reset the OTEL metrics global — same path as server-side metrics.py.
    import opentelemetry.metrics._internal as _mi  # type: ignore[attr-defined]

    _mi._METER_PROVIDER = None
    _mi._METER_PROVIDER_SET_ONCE = _mi.Once()

    # Reset the OTEL trace global — same path as server-side tracing.py.
    trace._TRACER_PROVIDER = None  # type: ignore[attr-defined]
    trace._TRACER_PROVIDER_SET_ONCE = trace.Once()  # type: ignore[attr-defined]


def _build_sampler(name: str) -> Sampler:
    """Parse a ``traces_sampler`` string into an OTEL Sampler.

    Mirrors ``culture.telemetry.tracing._build_sampler`` exactly — same
    string grammar, same fallback warning.
    """
    if name == "parentbased_always_on":
        return ParentBased(ALWAYS_ON)
    if name.startswith("parentbased_traceidratio:"):
        ratio = float(name.split(":", 1)[1])
        return ParentBased(TraceIdRatioBased(ratio))
    if name == "always_off":
        return ALWAYS_OFF
    logger.error(
        "Unknown telemetry.traces_sampler %r, falling back to parentbased_always_on. "
        "Valid values: parentbased_always_on, parentbased_traceidratio:<0.0-1.0>, always_off",
        name,
    )
    return ParentBased(ALWAYS_ON)


def _build_registry(meter: Meter) -> HarnessMetricsRegistry:
    """Register all four harness LLM instruments against ``meter``."""
    return HarnessMetricsRegistry(
        llm_tokens_input=meter.create_counter(
            "culture.harness.llm.tokens.input",
            description="Per-LLM-call input token count by backend/model/nick",
        ),
        llm_tokens_output=meter.create_counter(
            "culture.harness.llm.tokens.output",
            description="Per-LLM-call output token count by backend/model/nick",
        ),
        llm_call_duration=meter.create_histogram(
            "culture.harness.llm.call.duration",
            unit="ms",
            description="Per-LLM-call wall-clock duration in milliseconds",
        ),
        llm_calls=meter.create_counter(
            "culture.harness.llm.calls",
            description="Per-LLM-call count by backend/model/outcome",
        ),
    )


def init_harness_telemetry(config) -> tuple[Tracer, HarnessMetricsRegistry]:
    """Initialize TracerProvider + MeterProvider for the harness. Idempotent.

    Returns a ``(Tracer, HarnessMetricsRegistry)`` pair. When
    ``telemetry.enabled`` is False the returned tracer is a no-op proxy (no
    SDK provider installed) and the registry instruments are bound to OTEL's
    proxy meter — call sites can ``add()`` / ``record()`` unconditionally
    without ``if`` guards.

    The idempotency snapshot includes both the full ``TelemetryConfig`` dict
    and the agent identity (nick or daemon server name) so that two daemons
    with identical config but different nicks get separate providers with the
    correct ``service.instance.id``.
    """
    global _initialized_for, _tracer, _meter_provider, _registry

    tcfg = config.telemetry

    # Build a stable identity string for service.instance.id.
    if config.agents:
        nick_identity = "-".join(a.nick for a in config.agents)
    else:
        nick_identity = config.server.name

    snapshot = {"telemetry": asdict(tcfg), "nick": nick_identity}
    if _initialized_for == snapshot and _tracer is not None and _registry is not None:
        return _tracer, _registry

    # Tear down the previous MeterProvider (has a background export thread).
    if _meter_provider is not None:
        try:
            _meter_provider.shutdown()
        except Exception:  # noqa: BLE001 - shutdown errors must not crash init
            logger.debug("HarnessMeterProvider shutdown failed", exc_info=True)
        _meter_provider = None

    # ------------------------------------------------------------------ #
    # No-op / disabled paths                                               #
    # ------------------------------------------------------------------ #
    traces_on = tcfg.enabled and tcfg.traces_enabled
    metrics_on = tcfg.enabled and tcfg.metrics_enabled

    if not traces_on:
        # No SDK provider → proxy no-op tracer.
        _tracer = trace.get_tracer(_HARNESS_TRACER_NAME)

    if not metrics_on:
        # Proxy meter — instruments work as stubs; no export thread.
        meter = metrics.get_meter(_HARNESS_TRACER_NAME)
        _registry = _build_registry(meter)

    if not tcfg.enabled:
        _initialized_for = snapshot
        return _tracer, _registry  # type: ignore[return-value]

    # ------------------------------------------------------------------ #
    # Full SDK init                                                         #
    # ------------------------------------------------------------------ #
    resource = Resource.create(
        {
            "service.name": tcfg.service_name,
            "service.instance.id": nick_identity,
        }
    )
    compression_val = None if tcfg.otlp_compression == "none" else tcfg.otlp_compression

    if traces_on:
        span_exporter = OTLPSpanExporter(
            endpoint=tcfg.otlp_endpoint,
            timeout=tcfg.otlp_timeout_ms / 1000.0,
            compression=compression_val,
        )
        tracer_provider = TracerProvider(
            resource=resource,
            sampler=_build_sampler(tcfg.traces_sampler),
        )
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        trace.set_tracer_provider(tracer_provider)
        _tracer = trace.get_tracer(_HARNESS_TRACER_NAME)
        logger.info(
            "OTEL harness tracing initialized: service=%s instance=%s endpoint=%s sampler=%s",
            tcfg.service_name,
            nick_identity,
            tcfg.otlp_endpoint,
            tcfg.traces_sampler,
        )

    if metrics_on:
        metric_exporter = OTLPMetricExporter(
            endpoint=tcfg.otlp_endpoint,
            timeout=tcfg.otlp_timeout_ms / 1000.0,
            compression=compression_val,
        )
        reader = PeriodicExportingMetricReader(
            exporter=metric_exporter,
            export_interval_millis=tcfg.metrics_export_interval_ms,
        )
        provider = MeterProvider(resource=resource, metric_readers=[reader])
        metrics.set_meter_provider(provider)
        _meter_provider = provider
        meter = metrics.get_meter(_HARNESS_TRACER_NAME)
        _registry = _build_registry(meter)
        logger.info(
            "OTEL harness metrics initialized: service=%s instance=%s endpoint=%s interval=%dms",
            tcfg.service_name,
            nick_identity,
            tcfg.otlp_endpoint,
            tcfg.metrics_export_interval_ms,
        )

    _initialized_for = snapshot
    return _tracer, _registry  # type: ignore[return-value]


def record_llm_call(
    registry: HarnessMetricsRegistry,
    *,
    backend: str,
    model: str,
    nick: str,
    usage: dict | None,
    duration_ms: float,
    outcome: str,
) -> None:
    """Record metrics for a single LLM call.

    Always increments ``llm_calls`` and records ``llm_call_duration``,
    regardless of whether ``usage`` is present.

    ``usage`` contract: may be ``None`` or contain partial keys; missing
    or ``None``-valued keys silently skip the corresponding token counter.
    This is intentional — codex (#298) and copilot (#299) do not expose
    token counts in their current SDK versions. All four backends call this
    helper; only claude and acp backends may have non-None usage dicts.

    Args:
        registry: The ``HarnessMetricsRegistry`` returned by
            ``init_harness_telemetry``.
        backend: Backend identifier string (e.g. ``"claude"``, ``"codex"``).
        model: LLM model name (e.g. ``"claude-opus-4-6"``).
        nick: Agent IRC nick (e.g. ``"spark-claude"``). Used as the
            ``harness.nick`` label on token counters.
        usage: Optional dict with optional keys ``tokens_input`` (int) and
            ``tokens_output`` (int). ``None`` and missing keys are both safe.
        duration_ms: Wall-clock duration of the LLM call in milliseconds.
        outcome: One of ``"success"``, ``"error"``, or ``"timeout"``.
    """
    call_attrs = {"backend": backend, "model": model, "outcome": outcome}
    registry.llm_calls.add(1, call_attrs)
    registry.llm_call_duration.record(duration_ms, call_attrs)

    if usage is not None:
        tokens_input = usage.get("tokens_input")
        if isinstance(tokens_input, int):
            registry.llm_tokens_input.add(
                tokens_input, {"backend": backend, "model": model, "harness.nick": nick}
            )
        tokens_output = usage.get("tokens_output")
        if isinstance(tokens_output, int):
            registry.llm_tokens_output.add(
                tokens_output, {"backend": backend, "model": model, "harness.nick": nick}
            )
