"""Tests for #17: CLI lifecycle spans around ``culture agents start/stop``.

``cli_tracer`` falls back to the global OTEL tracer API when telemetry
is disabled, so the ``tracing_exporter`` fixture (which installs a
global in-memory SDK provider) captures the CLI spans without an OTLP
endpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import os

import pytest

import culture_core.cli.agents as agent_mod
from culture_core.config import ServerConfig, ServerConnConfig, save_server_config


def _write_config(tmp_path, host: str = "127.0.0.1", port: int = 6667):
    """Minimal server.yaml manifest with one claude agent (spark-claude)."""
    workdir = tmp_path / "proj"
    workdir.mkdir(exist_ok=True)
    (workdir / "culture.yaml").write_text("agents:\n  - suffix: claude\n    backend: claude\n")
    server_yaml = tmp_path / "server.yaml"
    config = ServerConfig(
        server=ServerConnConfig(name="spark", host=host, port=port),
        manifest={"claude": str(workdir.resolve())},
    )
    save_server_config(str(server_yaml), config)
    return server_yaml


def _spans_named(exporter, name):
    return [s for s in exporter.get_finished_spans() if s.name == name]


@pytest.mark.asyncio
async def test_cmd_start_emits_span_and_traceparent(tracing_exporter, tmp_path, monkeypatch):
    """`culture agents start` produces a joinable span: the span is
    exported AND its trace id is handed to daemon children via the
    TRACEPARENT env var (the cultureagent#43 join seam)."""

    async def _accept(reader, writer):
        writer.close()

    probe_server = await asyncio.start_server(_accept, "127.0.0.1", 0)
    port = probe_server.sockets[0].getsockname()[1]
    server_yaml = _write_config(tmp_path, port=port)

    started: list = []
    monkeypatch.setattr(agent_mod, "_start_background", lambda cfg, agents: started.append(agents))
    os.environ.pop("TRACEPARENT", None)

    args = argparse.Namespace(nick=None, all=True, config=str(server_yaml), foreground=False)
    try:
        agent_mod._cmd_start(args)

        spans = _spans_named(tracing_exporter, "culture.cli.agents.start")
        assert spans, "no culture.cli.agents.start span recorded"
        span = spans[-1]
        attrs = dict(span.attributes or {})
        assert attrs.get("culture.cli.mode") == "background"
        assert list(attrs.get("culture.agent.nicks")) == ["spark-claude"]
        assert list(attrs.get("culture.agent.backends")) == ["claude"]

        assert started and started[0][0].nick == "spark-claude"

        traceparent = os.environ.get("TRACEPARENT", "")
        assert traceparent, "TRACEPARENT not injected for daemon children"
        assert format(span.context.trace_id, "032x") in traceparent
    finally:
        os.environ.pop("TRACEPARENT", None)
        probe_server.close()
        await probe_server.wait_closed()


@pytest.mark.asyncio
async def test_cmd_stop_emits_span(tracing_exporter, tmp_path, monkeypatch):
    server_yaml = _write_config(tmp_path)

    stopped: list[str] = []
    monkeypatch.setattr(agent_mod, "stop_agent", stopped.append)

    args = argparse.Namespace(nick="spark-claude", all=False, config=str(server_yaml))
    agent_mod._cmd_stop(args)

    spans = _spans_named(tracing_exporter, "culture.cli.agents.stop")
    assert spans, "no culture.cli.agents.stop span recorded"
    attrs = dict(spans[-1].attributes or {})
    assert list(attrs.get("culture.agent.nicks")) == ["spark-claude"]
    assert stopped == ["spark-claude"]


@pytest.mark.asyncio
async def test_cmd_start_foreground_mode_attribute(tracing_exporter, tmp_path, monkeypatch):
    """--foreground routes to _start_foreground with mode recorded on the span."""

    async def _accept(reader, writer):
        writer.close()

    probe_server = await asyncio.start_server(_accept, "127.0.0.1", 0)
    port = probe_server.sockets[0].getsockname()[1]
    server_yaml = _write_config(tmp_path, port=port)

    started: list = []
    monkeypatch.setattr(agent_mod, "_start_foreground", lambda cfg, agents: started.append(agents))

    args = argparse.Namespace(nick=None, all=True, config=str(server_yaml), foreground=True)
    try:
        agent_mod._cmd_start(args)
    finally:
        os.environ.pop("TRACEPARENT", None)
        probe_server.close()
        await probe_server.wait_closed()

    spans = _spans_named(tracing_exporter, "culture.cli.agents.start")
    assert spans and dict(spans[-1].attributes or {}).get("culture.cli.mode") == "foreground"
    assert started and started[0][0].nick == "spark-claude"


# ---------------------------------------------------------------------------
# cli_tracer — telemetry-enabled path (local SDK provider, never global)
# ---------------------------------------------------------------------------


def _enabled_config():
    from culture_core.agentirc.config import TelemetryConfig

    tcfg = TelemetryConfig(
        enabled=True,
        traces_enabled=True,
        # Closed port + tiny timeout: exports fail fast and are swallowed
        # by the exporter — the test only exercises the provider wiring.
        otlp_endpoint="http://127.0.0.1:1",
        otlp_timeout_ms=100,
        otlp_compression="none",
    )
    return ServerConfig(server=ServerConnConfig(name="spark"), telemetry=tcfg)


def test_cli_tracer_enabled_builds_local_provider_and_shutdown_resets():
    from opentelemetry import trace

    from culture_core.cli.shared import cli_tracing

    config = _enabled_config()
    try:
        tracer = cli_tracing.cli_tracer(config)
        assert cli_tracing._provider is not None
        # The provider is local — the OTEL global stays a proxy/no-op.
        assert trace.get_tracer_provider() is not cli_tracing._provider
        # Second call reuses the provider instead of stacking exporters.
        provider_first = cli_tracing._provider
        cli_tracing.cli_tracer(config)
        assert cli_tracing._provider is provider_first

        os.environ.pop("TRACEPARENT", None)
        with tracer.start_as_current_span("culture.cli.agents.start") as span:
            cli_tracing.inject_traceparent_env()
        traceparent = os.environ.get("TRACEPARENT", "")
        assert format(span.context.trace_id, "032x") in traceparent
    finally:
        os.environ.pop("TRACEPARENT", None)
        cli_tracing.shutdown_cli_tracing()

    assert cli_tracing._provider is None
    # Idempotent: a second shutdown is a no-op.
    cli_tracing.shutdown_cli_tracing()


def test_cli_tracer_shutdown_swallows_provider_errors():
    from culture_core.cli.shared import cli_tracing

    config = _enabled_config()
    try:
        cli_tracing.cli_tracer(config)

        class _Boom(Exception):
            pass

        def _raise():
            raise _Boom("shutdown failed")

        cli_tracing._provider.shutdown = _raise
        cli_tracing.shutdown_cli_tracing()  # must not raise
        assert cli_tracing._provider is None
    finally:
        cli_tracing._provider = None


def test_inject_traceparent_env_noop_without_recording_span(monkeypatch):
    from culture_core.cli.shared.cli_tracing import inject_traceparent_env

    monkeypatch.delenv("TRACEPARENT", raising=False)
    inject_traceparent_env()  # no current span context → env untouched
    assert "TRACEPARENT" not in os.environ
