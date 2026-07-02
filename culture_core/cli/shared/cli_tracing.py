"""CLI-side tracing for operator lifecycle verbs (#17).

``cli_tracer(config)`` returns a tracer for short-lived spans around
CLI verbs (``culture agents start`` / ``stop``) so daemon startup
latency can be correlated with server-side traces.

Two deliberate differences from the server-side bootstrap
(:mod:`culture_core.telemetry.tracing`):

* The SDK provider is **local** — never installed as the OTEL global.
  ``culture agents start`` forks daemon children; a globally-installed
  provider would be inherited by the child and block the daemon's own
  ``init_harness_telemetry`` from installing its provider (OTEL refuses
  to override a set global).
* Spans export through a ``SimpleSpanProcessor`` (synchronous, no
  background thread) so there is no exporter worker thread alive across
  ``os.fork()``.

When telemetry is disabled the tracer comes from the global OTEL API
(``trace.get_tracer``): a no-op proxy in production, and a real tracer
under the test suite's ``tracing_exporter`` fixture — which is the test
seam for asserting CLI span behavior without an OTLP endpoint.

``inject_traceparent_env()`` writes the current span context into
``os.environ["TRACEPARENT"]`` so daemon children (forked or exec'd by a
service unit replay) can parent their startup spans to the CLI span —
the join point for agentculture/cultureagent#43.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from opentelemetry import propagate, trace
from opentelemetry.trace import Tracer

if TYPE_CHECKING:
    from culture_core.config import ServerConfig

logger = logging.getLogger(__name__)

_CLI_TRACER_NAME = "culture.cli"

_provider = None  # local SDK TracerProvider when telemetry is enabled


def cli_tracer(config: "ServerConfig") -> Tracer:
    """Return a tracer for CLI verb spans, honoring ``config.telemetry``.

    Enabled + traces_enabled → a tracer bound to a private SDK provider
    exporting synchronously over OTLP. Otherwise → the global-API tracer
    (no-op unless a provider is installed, e.g. by tests).
    """
    global _provider

    tcfg = config.telemetry
    if not (tcfg.enabled and tcfg.traces_enabled):
        return trace.get_tracer(_CLI_TRACER_NAME)

    if _provider is None:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        resource = Resource.create(
            {
                "service.name": tcfg.service_name,
                "service.instance.id": config.server.name,
            }
        )
        exporter = OTLPSpanExporter(
            endpoint=tcfg.otlp_endpoint,
            timeout=tcfg.otlp_timeout_ms / 1000.0,
            compression=(None if tcfg.otlp_compression == "none" else tcfg.otlp_compression),
        )
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        _provider = provider
        logger.debug(
            "CLI tracing initialized (local provider): service=%s endpoint=%s",
            tcfg.service_name,
            tcfg.otlp_endpoint,
        )
    return _provider.get_tracer(_CLI_TRACER_NAME)


def shutdown_cli_tracing() -> None:
    """Flush and drop the local provider (call after the verb completes)."""
    global _provider
    if _provider is not None:
        try:
            _provider.shutdown()
        except Exception:  # noqa: BLE001 - teardown must not mask the verb's outcome
            logger.debug("CLI tracer provider shutdown failed", exc_info=True)
        _provider = None


def inject_traceparent_env() -> None:
    """Copy the current span context into ``os.environ['TRACEPARENT']``.

    Must be called while the CLI verb span is current, before forking or
    starting the daemon: children inherit the environment, and the
    daemon-side lifecycle spans (cultureagent#43) use it to join this
    trace. No-op when there is no recording span context to propagate.
    """
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    traceparent = carrier.get("traceparent")
    if traceparent:
        os.environ["TRACEPARENT"] = traceparent
