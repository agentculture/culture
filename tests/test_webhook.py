import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from culture.clients.claude.config import WebhookConfig
from culture.clients.claude.webhook import AlertEvent, WebhookClient


class WebhookCapture(BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(length))
        WebhookCapture.received.append(body)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        """Suppress HTTP server log output during tests."""


@pytest.mark.asyncio
async def test_webhook_http_post():
    WebhookCapture.received.clear()
    http = HTTPServer(("127.0.0.1", 0), WebhookCapture)
    port = http.server_address[1]
    thread = threading.Thread(target=http.handle_request, daemon=True)
    thread.start()
    config = WebhookConfig(
        url=f"http://127.0.0.1:{port}/webhook",
        irc_channel="#alerts",
        events=["agent_error"],
    )
    client = WebhookClient(config, irc_send=None)
    event = AlertEvent(
        event_type="agent_error",
        nick="spark-culture",
        message="[ERROR] spark-culture crashed: exit code 1",
    )
    await client.fire(event)
    thread.join(timeout=2.0)
    assert len(WebhookCapture.received) == 1
    assert "spark-culture" in WebhookCapture.received[0]["content"]
    http.server_close()


@pytest.mark.asyncio
async def test_webhook_irc_fallback():
    sent_messages = []

    async def mock_irc_send(channel, text):
        sent_messages.append((channel, text))

    config = WebhookConfig(url=None, irc_channel="#alerts", events=["agent_error"])
    client = WebhookClient(config, irc_send=mock_irc_send)
    event = AlertEvent(
        event_type="agent_error",
        nick="spark-culture",
        message="[ERROR] spark-culture crashed",
    )
    await client.fire(event)
    assert len(sent_messages) == 1
    assert sent_messages[0][0] == "#alerts"
    assert "spark-culture" in sent_messages[0][1]


@pytest.mark.asyncio
async def test_webhook_skips_unconfigured_events():
    sent_messages = []

    async def mock_irc_send(channel, text):
        sent_messages.append((channel, text))

    config = WebhookConfig(url=None, irc_channel="#alerts", events=["agent_error"])
    client = WebhookClient(config, irc_send=mock_irc_send)
    event = AlertEvent(
        event_type="agent_complete",
        nick="spark-culture",
        message="[COMPLETE] done",
    )
    await client.fire(event)
    assert len(sent_messages) == 0
