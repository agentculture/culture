"""End-to-end IRC transport behavior — IRCv3 message-tag inbound propagation.

Sends a tagged PRIVMSG with a W3C traceparent through the real
``agentirc.IRCd``, observes the daemon's harness tracer emit a child span
parented to the inbound trace_id with ``culture.trace.origin: "remote"``.
Replaces the integration-shaped half of
``tests/harness/test_irc_transport_propagation.py`` (the harness unit
test moves to cultureagent in Phase 1).

**Reconnect coverage** lives in
``tests/test_irc_transport.py::test_reconnect_retries_after_connection_error``
— it already drives ``IRCTransport._reconnect()`` against the real
``server`` fixture, so a daemon-level duplicate would be churn. The
audit's Task 4 reconnect ask cites that exact file.
"""

import asyncio

import pytest

from culture.clients.claude.config import (
    AgentConfig,
    DaemonConfig,
    ServerConnConfig,
    WebhookConfig,
)
from culture.clients.claude.daemon import AgentDaemon

# Standard W3C traceparent: version-trace_id-span_id-flags. See
# https://www.w3.org/TR/trace-context/#traceparent-header.
VALID_TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
EXPECTED_TRACE_ID = int("4bf92f3577b34da6a3ce929d0e0e4736", 16)


def _redirect_pidfile(monkeypatch, tmp_path):
    """Redirect ``culture.pidfile.PID_DIR`` so daemons don't write into the
    real ``~/.culture/pids`` from a unit test."""
    monkeypatch.setattr("culture.pidfile.PID_DIR", str(tmp_path / "pids"))


async def _wait_for_daemon_joined(server, channel, nick, timeout=5.0):
    """Poll until the daemon's IRC nick appears in ``server.channels[channel].members``.

    ``ch.members`` is a ``set[Client]``, so we compare ``m.nick``. Avoids
    racy fixed sleeps for the welcome → JOIN handshake — the server
    processes JOIN synchronously on receipt, so observing the daemon in
    the membership set is a deterministic readiness signal.
    """
    async with asyncio.timeout(timeout):
        while True:
            ch = server.channels.get(channel)
            if ch is not None and any(getattr(m, "nick", None) == nick for m in ch.members):
                return
            await asyncio.sleep(0.05)


async def _wait_for_span(exporter, predicate, timeout=5.0):
    """Bounded poll on ``exporter.get_finished_spans()`` for the first span
    matching ``predicate``. The conftest ``tracing_exporter`` fixture
    installs a ``SimpleSpanProcessor`` so finished spans land in the
    exporter synchronously, but the test still has to wait for the
    daemon's read loop to handle the inbound line."""
    async with asyncio.timeout(timeout):
        while True:
            for span in exporter.get_finished_spans():
                if predicate(span):
                    return span
            await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_inbound_traceparent_creates_remote_origin_span(
    server, make_client, tracing_exporter, tmp_path, monkeypatch
):
    """A PRIVMSG with @culture.dev/traceparent=<valid> creates a span with
    ``culture.trace.origin: "remote"`` whose trace_id matches the inbound
    traceparent's trace-id."""
    _redirect_pidfile(monkeypatch, tmp_path)

    # Reset the harness telemetry module's cached _tracer so this test's
    # tracing_exporter provider — not a stale one from a sibling worker —
    # is what daemon.start() picks up. The reset also clears the OTel
    # global, so we must re-install the test provider afterwards.
    from opentelemetry import trace as _otel_trace

    from culture.clients.shared import telemetry as harness_tel

    captured_provider = _otel_trace.get_tracer_provider()
    harness_tel.reset_for_tests()
    _otel_trace.set_tracer_provider(captured_provider)

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
        # Complete the CAP negotiation handshake before sending tagged
        # PRIVMSGs. The server doesn't strip tags from inbound non-CAP
        # clients today, but the explicit REQ + ACK + END pattern
        # matches tests/test_irc_transport_tags.py and avoids relying
        # on that internal behavior. Wait for the ACK line so the
        # capability is actually negotiated before END.
        await human.send("CAP REQ :message-tags")
        ack = await human.recv_until("ACK")
        assert "message-tags" in ack, f"expected CAP ACK :message-tags, got {ack!r}"
        await human.send("CAP END")
        await human.recv_all(timeout=0.3)
        await human.send("JOIN #general")
        await human.recv_all(timeout=0.3)

        line = f"@culture.dev/traceparent={VALID_TRACEPARENT} PRIVMSG #general :traced hello"
        await human.send(line)

        span = await _wait_for_span(
            tracing_exporter,
            lambda s: (
                s.name == "harness.irc.message.handle"
                and s.attributes.get("irc.command") == "PRIVMSG"
                and s.attributes.get("culture.trace.origin") == "remote"
                and s.context.trace_id == EXPECTED_TRACE_ID
            ),
        )
        assert span is not None
        # Sanity: the dropped_reason attribute must be absent on the
        # valid-traceparent path (only set when status is malformed/too_long).
        assert "culture.trace.dropped_reason" not in span.attributes
    finally:
        await daemon.stop()
