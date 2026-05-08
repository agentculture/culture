"""Test configuration for tests/harness/.

Adds ``packages/agent-harness/`` to ``sys.path`` so tests can import the
remaining reference modules (``config``, ``constants``, ``daemon``) directly.
This mirrors how a cited backend copy works in practice for the cited tier —
each backend owns its copy as a plain Python module file, not as part of an
installed package.

Telemetry was lifted into ``culture/clients/shared/telemetry.py`` (no longer
cited per backend); ``reset_for_tests`` is imported from that shared module.

Shared fixtures:
- ``harness_metrics_reader`` — InMemoryMetricReader + SdkMeterProvider for
  harness LLM metrics tests.
- ``harness_tracing_exporter`` — InMemorySpanExporter + SdkTracerProvider for
  harness tracing tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from opentelemetry import metrics as otel_metrics
from opentelemetry import trace
from opentelemetry.sdk.metrics import MeterProvider as SdkMeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider as SdkTracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from culture.clients.shared.telemetry import reset_for_tests as _reset_harness

# Insert the agent-harness reference directory so tests can do:
#   from config import DaemonConfig, TelemetryConfig, ...
_HARNESS_REF = str(Path(__file__).parent.parent.parent / "packages" / "agent-harness")
if _HARNESS_REF not in sys.path:
    sys.path.insert(0, _HARNESS_REF)


@pytest.fixture
def harness_metrics_reader():
    """Install an InMemoryMetricReader against a fresh SdkMeterProvider.

    Returns the reader so tests can call ``reader.get_metrics_data()``.
    Resets harness module state before install and after teardown.

    Shared across test_telemetry_module.py and test_record_llm_call.py — both
    used to define this inline; this canonical version lives here so future
    harness tests can reuse it without duplication.
    """
    _reset_harness()
    reader = InMemoryMetricReader()
    provider = SdkMeterProvider(
        resource=Resource.create({"service.name": "test-harness"}),
        metric_readers=[reader],
    )
    otel_metrics.set_meter_provider(provider)
    yield reader
    _reset_harness()


@pytest.fixture
def harness_tracing_exporter():
    """Install an InMemorySpanExporter against a fresh SdkTracerProvider.

    Returns the exporter so tests can call ``exporter.get_finished_spans()``.
    Resets harness module state before install and after teardown so parallel
    xdist workers don't leak providers.

    Usage::

        def test_something(harness_tracing_exporter):
            exporter, tracer_provider = harness_tracing_exporter
            tracer = tracer_provider.get_tracer("test")
            # ... use tracer, then:
            spans = exporter.get_finished_spans()
    """
    _reset_harness()
    exporter = InMemorySpanExporter()
    provider = SdkTracerProvider(
        resource=Resource.create({"service.name": "test-harness-tracing"}),
    )
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    yield exporter, provider
    provider.shutdown()
    _reset_harness()
