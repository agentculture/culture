"""OpenTelemetry integration for Culture.

Public surface re-exported here; call sites import from `culture.telemetry`.
"""

from culture.telemetry.context import (
    TRACEPARENT_TAG,
    TRACESTATE_TAG,
    ExtractResult,
    context_from_traceparent,
    current_traceparent,
    extract_traceparent_from_tags,
    inject_traceparent,
)
from culture.telemetry.metrics import MetricsRegistry, init_metrics
from culture.telemetry.tracing import init_telemetry

__all__ = [
    "ExtractResult",
    "MetricsRegistry",
    "TRACEPARENT_TAG",
    "TRACESTATE_TAG",
    "context_from_traceparent",
    "current_traceparent",
    "extract_traceparent_from_tags",
    "init_metrics",
    "init_telemetry",
    "inject_traceparent",
]
