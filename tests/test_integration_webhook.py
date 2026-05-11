"""End-to-end webhook fanout — daemon's irc_ask IPC fires an
agent_question AlertEvent; WebhookClient POSTs JSON to the configured URL.

Replaces the integration-shaped portion of ``tests/test_webhook.py`` (the
unit test moves to cultureagent in Phase 1).

**IRC alert channel coverage** lives in
``tests/test_integration_layer5.py::test_webhook_fires_on_question`` —
that test already exercises the ``WebhookConfig(irc_channel="#alerts")``
path against the real ``server`` fixture. Audit row #7 is met by that
file; no daemon-level duplicate here.
"""

import asyncio
import json
import os
import queue
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from cultureagent.clients.claude.daemon import AgentDaemon
from cultureagent.clients.shared.skill_irc_client import SkillClient

from culture.clients.claude.config import (
    AgentConfig,
    DaemonConfig,
    ServerConnConfig,
    WebhookConfig,
)


def _redirect_pidfile(monkeypatch, tmp_path):
    """Redirect ``culture.pidfile.PID_DIR`` so daemons don't write into the
    real ``~/.culture/pids`` from a unit test."""
    monkeypatch.setattr("culture.pidfile.PID_DIR", str(tmp_path / "pids"))


async def _wait_for_daemon_joined(server, channel, nick, timeout=5.0):
    """Poll ``server.channels[channel].members`` (a ``set[Client]``) for
    ``nick`` so the test never sends to the channel before the daemon's
    welcome → JOIN handshake completes."""
    async with asyncio.timeout(timeout):
        while True:
            ch = server.channels.get(channel)
            if ch is not None and any(getattr(m, "nick", None) == nick for m in ch.members):
                return
            await asyncio.sleep(0.05)


def _make_capture_server():
    """Build a stdlib ``HTTPServer`` on a random port that captures POSTed
    JSON bodies into a thread-safe ``queue.SimpleQueue``. Returns
    ``(httpd, received_queue, port)``.

    Uses stdlib instead of aiohttp.web to match the existing pattern in
    ``tests/test_webhook.py``. The capture queue replaces a plain list
    so the cross-thread handle-thread → asyncio-test handoff is
    explicitly synchronized (CPython's GIL makes a list "work" today
    but the queue removes the implicit reliance on it).
    """
    received: queue.SimpleQueue = queue.SimpleQueue()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            body = json.loads(self.rfile.read(length))
            received.put(body)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            """Silence stderr noise during tests."""

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    return httpd, received, port


@pytest.mark.asyncio
async def test_irc_ask_triggers_http_webhook_post(server, make_client, tmp_path, monkeypatch):
    """``skill.irc_ask`` fires the ``agent_question`` AlertEvent;
    ``WebhookClient`` POSTs ``{"content": "[QUESTION] ..."}`` JSON to the
    configured URL."""
    _redirect_pidfile(monkeypatch, tmp_path)

    httpd, received, capture_port = _make_capture_server()
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    try:
        config = DaemonConfig(
            server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
            webhooks=WebhookConfig(url=f"http://127.0.0.1:{capture_port}/hook"),
        )
        agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general"])
        sock_dir = tmp_path / "sock"
        sock_dir.mkdir()
        daemon = AgentDaemon(config, agent, socket_dir=str(sock_dir), skip_claude=True)
        await daemon.start()
        try:
            await _wait_for_daemon_joined(server, "#general", agent.nick)

            sock_path = os.path.join(str(sock_dir), "culture-testserv-bot.sock")
            skill = SkillClient(sock_path)
            await skill.connect()
            try:
                # irc_ask fires agent_question webhook (default in WebhookConfig.events).
                # Bound the IPC call ourselves: skill.irc_ask's `timeout` is
                # echoed in the IPC message but the daemon ignores it, and
                # SkillClient._request awaits the response with no deadline.
                async with asyncio.timeout(5.0):
                    result = await skill.irc_ask("#general", "what cmake flags?", timeout=1)
                assert result["ok"]
                # Block until the capture server's handler thread has put
                # one body on the queue, with an explicit timeout. The
                # queue handoff replaces a polled-list pattern that
                # implicitly relied on CPython's GIL for visibility.
                payload = await asyncio.to_thread(received.get, True, 5.0)
            finally:
                await skill.close()
        finally:
            await daemon.stop()
    finally:
        httpd.shutdown()
        server_thread.join(timeout=2.0)
        httpd.server_close()

    # Webhook payload shape from culture/clients/shared/webhook.py:_http_post
    assert "content" in payload
    assert "[QUESTION]" in payload["content"]
    assert "testserv-bot" in payload["content"]
    assert "what cmake flags?" in payload["content"]
    # No further POSTs expected — the agent_question event fires once per ask.
    assert received.empty()
