"""OpenTelemetry TracerProvider bootstrap for Culture.

`init_telemetry(config)` is idempotent — safe to call from multiple places
(e.g. IRCd.__init__ and ServerLink.__init__ for independent test servers).
When `config.telemetry.enabled` is False, returns a no-op tracer without
touching the global provider.
"""

from __future__ import annotations

import logging

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

from culture.agentirc.config import ServerConfig, TelemetryConfig

logger = logging.getLogger(__name__)

_CULTURE_TRACER_NAME = "culture"
_initialized_for: TelemetryConfig | None = None
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
    # Structural equality, not identity — catches silent bypass if a caller
    # mutates TelemetryConfig between calls (the dataclass is not frozen).
    if _initialized_for == tcfg and _tracer is not None:
        return _tracer

    if not tcfg.enabled or not tcfg.traces_enabled:
        _tracer = trace.get_tracer(_CULTURE_TRACER_NAME)  # no-op when no provider set
        _initialized_for = tcfg
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
    _initialized_for = tcfg
    logger.info(
        "OTEL tracing initialized: service=%s instance=%s endpoint=%s sampler=%s",
        tcfg.service_name,
        config.name,
        tcfg.otlp_endpoint,
        tcfg.traces_sampler,
    )
    return _tracer
