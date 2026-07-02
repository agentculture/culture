"""OpenTelemetry TracerProvider bootstrap for Culture.

`init_telemetry(config)` is idempotent — safe to call from multiple places
(e.g. IRCd.__init__ and ServerLink.__init__ for independent test servers).
When `config.telemetry.enabled` is False, returns a no-op tracer without
touching the global provider.
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from agentirc.config import ServerConfig, TelemetryConfig
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
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

_CULTURE_TRACER_NAME = "culture.agentirc"
# Snapshot of the TelemetryConfig that was used at init time. Stored as a
# dict so in-place mutation of the original TelemetryConfig still triggers
# re-initialization on the next call (not just identity/equality on a
# reference to the same mutable object).
_initialized_for: dict | None = None
_tracer: Tracer | None = None


def reset_for_tests() -> None:
    """Reset module state so each test gets a fresh provider. Test-only."""
    global _initialized_for, _tracer
    _initialized_for = None
    _tracer = None
    # Reset the global OTEL provider too, so one test's SDK doesn't leak.
    trace._TRACER_PROVIDER = None  # type: ignore[attr-defined]
    trace._TRACER_PROVIDER_SET_ONCE = trace.Once()  # type: ignore[attr-defined]


def _build_sampler(name: str) -> Sampler:
    """Parse `sampler` string from TelemetryConfig into an OTEL Sampler."""
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


def init_telemetry(config: ServerConfig) -> Tracer:
    """Initialize the TracerProvider from a ServerConfig. Idempotent.

    Returns a Tracer bound to the "culture" instrumentation name. When
    `config.telemetry.enabled` is False, returns a no-op tracer and does
    not install an SDK provider — this keeps tests, and servers that opt
    out of telemetry, from paying any SDK cost.
    """
    global _initialized_for, _tracer

    tcfg = config.telemetry
    # Compare against an immutable snapshot so in-place mutation of the
    # caller's TelemetryConfig is detected (the dataclass is not frozen).
    # Include config.name so two IRCd instances with identical TelemetryConfig
    # but different names each get their own tracer and correct
    # service.instance.id resource attribute. Mirrors metrics.py for parity.
    snapshot = {"telemetry": asdict(tcfg), "instance": config.name}
    if _initialized_for == snapshot and _tracer is not None:
        return _tracer

    if not tcfg.enabled or not tcfg.traces_enabled:
        _tracer = trace.get_tracer(_CULTURE_TRACER_NAME)  # no-op when no provider set
        _initialized_for = snapshot
        return _tracer

    resource = Resource.create(
        {
            "service.name": tcfg.service_name,
            "service.instance.id": config.name,
        }
    )
    provider = TracerProvider(resource=resource, sampler=_build_sampler(tcfg.traces_sampler))
    exporter = OTLPSpanExporter(
        endpoint=tcfg.otlp_endpoint,
        timeout=tcfg.otlp_timeout_ms / 1000.0,
        compression=(None if tcfg.otlp_compression == "none" else tcfg.otlp_compression),
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    _tracer = trace.get_tracer(_CULTURE_TRACER_NAME)
    _initialized_for = snapshot
    logger.info(
        "OTEL tracing initialized: service=%s instance=%s endpoint=%s sampler=%s",
        tcfg.service_name,
        config.name,
        tcfg.otlp_endpoint,
        tcfg.traces_sampler,
    )
    return _tracer
