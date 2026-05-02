"""OpenTelemetry MeterProvider bootstrap for Culture.

`init_metrics(config)` is idempotent — safe to call from multiple places.
When `config.telemetry.enabled` or `metrics_enabled` is False, returns a
MetricsRegistry whose instruments are bound to OTEL's proxy meter that
becomes a real meter only if a provider is later installed. In production
this is effectively no-op (no provider is installed). In tests, callers
MUST `reset_for_tests()` between disabled-init and the `metrics_reader`
fixture, otherwise the cached proxy instruments would forward to the
freshly-installed test provider.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

from agentirc.config import ServerConfig
from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.metrics import Counter, Histogram, Meter, UpDownCounter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

logger = logging.getLogger(__name__)

_CULTURE_METER_NAME = "culture.agentirc"
_initialized_for: dict | None = None
_meter_provider: "MeterProvider | None" = None
_registry: "MetricsRegistry | None" = None


@dataclass
class MetricsRegistry:
    """All Plan-3 server-side instruments, registered once during init_metrics.

    Plan 4/5/6 will extend by adding fields for audit / harness / bots.
    Keep this a single dataclass and grow it — don't spawn parallel
    registries per category.
    """

    # Message flow
    irc_bytes_sent: Counter
    irc_bytes_received: Counter
    irc_message_size: Histogram
    privmsg_delivered: Counter
    # Events
    events_emitted: Counter
    events_render_duration: Histogram
    # Federation
    s2s_messages: Counter
    s2s_relay_latency: Histogram
    s2s_links_active: UpDownCounter
    s2s_link_events: Counter
    # Clients & sessions
    clients_connected: UpDownCounter
    client_session_duration: Histogram
    client_command_duration: Histogram
    # Trace-context hygiene
    trace_inbound: Counter
    # Audit (Plan 4)
    audit_writes: Counter
    audit_queue_depth: UpDownCounter
    # Bots (Plan 7)
    bot_invocations: Counter
    bot_webhook_duration: Histogram


def reset_for_tests() -> None:
    """Reset module state so each test gets a fresh provider. Test-only."""
    global _initialized_for, _meter_provider, _registry
    if _meter_provider is not None:
        try:
            _meter_provider.shutdown()
        except Exception:  # noqa: BLE001
            pass
        _meter_provider = None
    _initialized_for = None
    _registry = None
    # _METER_PROVIDER and Once live on the _internal sub-package, not the
    # top-level metrics module (unlike the trace API which exposes them directly).
    import opentelemetry.metrics._internal as _mi  # type: ignore[attr-defined]

    _mi._METER_PROVIDER = None
    _mi._METER_PROVIDER_SET_ONCE = _mi.Once()


def _build_registry(meter: Meter) -> MetricsRegistry:
    """Single source of truth for instrument names / units / descriptions.

    Names and units must match docs/superpowers/specs/2026-04-24-otel-observability-design.md
    Metrics catalog section.
    """
    return MetricsRegistry(
        # Message flow
        irc_bytes_sent=meter.create_counter(
            "culture.irc.bytes_sent",
            unit="By",
            description="Bytes written to client/peer sockets",
        ),
        irc_bytes_received=meter.create_counter(
            "culture.irc.bytes_received",
            unit="By",
            description="Bytes read from client/peer sockets",
        ),
        irc_message_size=meter.create_histogram(
            "culture.irc.message.size",
            unit="By",
            description="Per-message byte size at parse time",
        ),
        privmsg_delivered=meter.create_counter(
            "culture.privmsg.delivered",
            description="Per-PRIVMSG delivery count, labeled by kind=dm|channel",
        ),
        # Events
        events_emitted=meter.create_counter(
            "culture.events.emitted",
            description="Events through IRCd.emit_event, labeled by type and origin",
        ),
        events_render_duration=meter.create_histogram(
            "culture.events.render.duration",
            unit="ms",
            description="Time spent in skill hooks + bot dispatch + surfacing",
        ),
        # Federation
        s2s_messages=meter.create_counter(
            "culture.s2s.messages",
            description="Inbound S2S messages by verb and peer (outbound deferred)",
        ),
        s2s_relay_latency=meter.create_histogram(
            "culture.s2s.relay_latency",
            unit="ms",
            description="Per-event relay duration in ServerLink.relay_event",
        ),
        s2s_links_active=meter.create_up_down_counter(
            "culture.s2s.links_active",
            description="Currently active federation links",
        ),
        s2s_link_events=meter.create_counter(
            "culture.s2s.link_events",
            description="Federation lifecycle events: connect/disconnect/auth_fail/backfill_*",
        ),
        # Clients & sessions
        clients_connected=meter.create_up_down_counter(
            "culture.clients.connected",
            description="Currently connected clients by kind=human|bot|harness",
        ),
        client_session_duration=meter.create_histogram(
            "culture.client.session.duration",
            unit="s",
            description="Per-client connection lifetime",
        ),
        client_command_duration=meter.create_histogram(
            "culture.client.command.duration",
            unit="ms",
            description="Per-command dispatch duration by verb",
        ),
        # Trace-context hygiene
        trace_inbound=meter.create_counter(
            "culture.trace.inbound",
            description="Inbound traceparent extraction outcome by result and peer",
        ),
        # Audit (Plan 4)
        audit_writes=meter.create_counter(
            "culture.audit.writes",
            description="Audit JSONL write outcomes (ok|error)",
        ),
        audit_queue_depth=meter.create_up_down_counter(
            "culture.audit.queue_depth",
            description="Audit queue depth (records waiting to flush to disk)",
        ),
        # Bots (Plan 7)
        bot_invocations=meter.create_counter(
            "culture.bot.invocations",
            description="Bot dispatch invocations by bot, event.type, outcome=success|error",
        ),
        bot_webhook_duration=meter.create_histogram(
            "culture.bot.webhook.duration",
            unit="s",
            description="Webhook request handler duration by bot and status_class=2xx|3xx|4xx|5xx",
        ),
    )


def init_metrics(config: ServerConfig) -> MetricsRegistry:
    """Initialize MeterProvider + register instruments. Idempotent.

    Returns a MetricsRegistry. When telemetry is disabled or
    metrics_enabled is False, instruments are bound to OTEL's proxy meter
    — call sites can `instrument.add(...)` / `.record(...)` unconditionally
    without guards. Production never installs a provider in this case.
    """
    global _initialized_for, _meter_provider, _registry

    tcfg = config.telemetry
    # Include config.name so two IRCd instances with identical TelemetryConfig
    # but different names each get their own registry and correct
    # service.instance.id resource attribute.
    snapshot = {"telemetry": asdict(tcfg), "instance": config.name}
    if _initialized_for == snapshot and _registry is not None:
        return _registry

    # Tear down the previous SDK provider before installing a new one.
    # PeriodicExportingMetricReader spawns a background thread that needs an
    # explicit shutdown call; otherwise tests leak workers and may double-export.
    if _meter_provider is not None:
        try:
            _meter_provider.shutdown()
        except Exception:  # noqa: BLE001 - shutdown errors must not crash init
            logger.debug("MeterProvider shutdown failed", exc_info=True)
        _meter_provider = None

    if not tcfg.enabled or not tcfg.metrics_enabled:
        meter = metrics.get_meter(_CULTURE_METER_NAME)
        _registry = _build_registry(meter)
        _initialized_for = snapshot
        return _registry

    resource = Resource.create(
        {
            "service.name": tcfg.service_name,
            "service.instance.id": config.name,
        }
    )
    exporter = OTLPMetricExporter(
        endpoint=tcfg.otlp_endpoint,
        timeout=tcfg.otlp_timeout_ms / 1000.0,
        compression=(None if tcfg.otlp_compression == "none" else tcfg.otlp_compression),
    )
    reader = PeriodicExportingMetricReader(
        exporter=exporter,
        export_interval_millis=tcfg.metrics_export_interval_ms,
    )
    provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(provider)
    _meter_provider = provider

    meter = metrics.get_meter(_CULTURE_METER_NAME)
    _registry = _build_registry(meter)
    _initialized_for = snapshot
    logger.info(
        "OTEL metrics initialized: service=%s instance=%s endpoint=%s interval=%dms",
        tcfg.service_name,
        config.name,
        tcfg.otlp_endpoint,
        tcfg.metrics_export_interval_ms,
    )
    return _registry
