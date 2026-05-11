"""End-to-end harness telemetry — `harness.irc.connect` span fires on
daemon connect; `culture.attention.transitions` counter fires when a
mention promotes a target to HOT.

Replaces the integration-shaped portions of
``tests/harness/test_telemetry_module.py`` and
``tests/harness/test_daemon_telemetry.py`` (the harness unit tests move
to cultureagent in Phase 1).

The `harness.irc.message.handle` span is covered separately by
``tests/test_integration_irc_transport.py``; LLM counters and the
call-duration histogram fire inside the agent runner via
``record_llm_call`` and belong in Task 8.
"""

import asyncio

import pytest
from cultureagent.clients.claude.daemon import AgentDaemon
from cultureagent.clients.shared import telemetry as harness_tel

from culture.clients.claude.config import (
    AgentConfig,
    DaemonConfig,
    ServerConnConfig,
    WebhookConfig,
)
from culture.clients.shared.attention import Band

CONNECT_SPAN = "harness.irc.connect"
TRANSITIONS_METRIC = "culture.attention.transitions"


def _redirect_pidfile(monkeypatch, tmp_path):
    """Redirect ``culture.pidfile.PID_DIR`` so daemons don't write into the
    real ``~/.culture/pids`` from a unit test."""
    monkeypatch.setattr("culture.pidfile.PID_DIR", str(tmp_path / "pids"))


def _invalidate_harness_telemetry_cache():
    """Force the next ``init_harness_telemetry`` to re-init from the current
    OTel globals.

    ``culture.clients.shared.telemetry`` keeps module-level
    ``_initialized_for`` / ``_tracer`` / ``_registry`` so the function is
    idempotent in production. Under pytest-xdist a sibling worker can
    cache the registry against a previous test's no-op providers; without
    invalidation the new test then routes spans/metrics nowhere. We only
    clear the cache keys (NOT the OTel globals) — the conftest
    ``tracing_exporter`` / ``metrics_reader`` fixtures own the globals and
    have already installed their providers by the time the test body
    runs, so ``trace.get_tracer`` / ``metrics.get_meter`` resolve to them.

    Calling ``harness_tel.reset_for_tests()`` would also work but it
    additionally clears OTel's own globals (and on `--xdist` workers can
    race with the fixture's setup), so we use the narrower cache poke.
    """
    harness_tel._initialized_for = None
    harness_tel._tracer = None
    harness_tel._registry = None


async def _wait_for_daemon_joined(server, channel, nick, timeout=5.0):
    async with asyncio.timeout(timeout):
        while True:
            ch = server.channels.get(channel)
            if ch is not None and any(getattr(m, "nick", None) == nick for m in ch.members):
                return
            await asyncio.sleep(0.05)


async def _wait_for_band(daemon, target, expected, timeout=5.0):
    async with asyncio.timeout(timeout):
        while True:
            state = daemon._attention.snapshot().get(target)
            if state is not None and state.band == expected:
                return
            await asyncio.sleep(0.05)


def _find_metric(metrics_reader, name):
    """Return the first metric with the given name across all scopes, or None."""
    data = metrics_reader.get_metrics_data()
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name == name:
                    return metric
    return None


@pytest.mark.asyncio
async def test_daemon_connect_emits_harness_irc_connect_span(
    server, tracing_exporter, tmp_path, monkeypatch
):
    """``daemon.start()`` → ``IRCTransport._do_connect`` emits the connect
    span with the configured backend/nick/server attrs."""
    _redirect_pidfile(monkeypatch, tmp_path)
    _invalidate_harness_telemetry_cache()

    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        webhooks=WebhookConfig(url=None),
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general"])
    sock_dir = tmp_path / "sock"
    sock_dir.mkdir()
    daemon = AgentDaemon(config, agent, socket_dir=str(sock_dir), skip_claude=True)
    await daemon.start()
    try:
        await _wait_for_daemon_joined(server, "#general", agent.nick)

        connect_spans = [s for s in tracing_exporter.get_finished_spans() if s.name == CONNECT_SPAN]
        assert connect_spans, (
            "expected at least one harness.irc.connect span; saw "
            f"{[s.name for s in tracing_exporter.get_finished_spans()]}"
        )
        attrs = connect_spans[0].attributes
        assert attrs.get("harness.nick") == "testserv-bot"
        assert attrs.get("harness.server") == f"127.0.0.1:{server.config.port}"
        assert "harness.backend" in attrs
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_attention_transition_emits_counter(
    server, make_client, metrics_reader, tracing_exporter, tmp_path, monkeypatch
):
    """A mention IDLE→HOT transition increments
    ``culture.attention.transitions`` with ``from_band=IDLE``,
    ``to_band=HOT``, ``cause=direct`` attributes (and matching
    ``agent``/``target``)."""
    _redirect_pidfile(monkeypatch, tmp_path)
    _invalidate_harness_telemetry_cache()

    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        webhooks=WebhookConfig(url=None),
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general"])
    sock_dir = tmp_path / "sock"
    sock_dir.mkdir()
    daemon = AgentDaemon(config, agent, socket_dir=str(sock_dir), skip_claude=True)
    await daemon.start()
    try:
        await _wait_for_daemon_joined(server, "#general", agent.nick)
        human = await make_client(nick="testserv-ori", user="ori")
        await human.send("JOIN #general")
        await human.recv_all(timeout=0.3)
        await human.send("PRIVMSG #general :@testserv-bot ping")
        await _wait_for_band(daemon, "#general", Band.HOT)
    finally:
        await daemon.stop()

    metric = _find_metric(metrics_reader, TRANSITIONS_METRIC)
    assert metric is not None, f"counter {TRANSITIONS_METRIC} not emitted"
    points = list(metric.data.data_points)
    assert points, "expected at least one data point on attention.transitions"
    direct = [
        p
        for p in points
        if p.attributes.get("from_band") == "IDLE"
        and p.attributes.get("to_band") == "HOT"
        and p.attributes.get("cause") == "direct"
        and p.attributes.get("target") == "#general"
        and p.attributes.get("agent") == "testserv-bot"
    ]
    assert direct, f"no IDLE→HOT direct transition; saw {[p.attributes for p in points]}"
    assert sum(p.value for p in direct) >= 1
