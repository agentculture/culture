from unittest.mock import patch

import pytest

from culture.agentirc.client import Client
from culture.agentirc.config import ServerConfig, TelemetryConfig
from culture.agentirc.ircd import IRCd
from culture.protocol.message import Message
from tests.telemetry._fakes import FakeWriter


@pytest.mark.asyncio
async def test_parse_error_surfaces_as_span_event(tracing_exporter):
    cfg = ServerConfig(name="spark", telemetry=TelemetryConfig(enabled=False))
    ircd = IRCd(cfg)
    client = Client(reader=None, writer=FakeWriter(), server=ircd)  # type: ignore[arg-type]
    client.nick = "spark-alice"

    def _boom(_line):
        raise ValueError("bad line")

    # _process_buffer opens its own span "irc.client.process_buffer"; parse
    # errors from Message.parse are attached as events on that span.
    with patch.object(Message, "parse", side_effect=_boom):
        await client._process_buffer("GARBAGE\n")

    spans = tracing_exporter.get_finished_spans()
    buf_span = next(s for s in spans if s.name == "irc.client.process_buffer")
    event_names = [e.name for e in buf_span.events]
    assert "irc.parse_error" in event_names
    parse_evt = next(e for e in buf_span.events if e.name == "irc.parse_error")
    assert parse_evt.attributes["error"] == "ValueError"
    assert parse_evt.attributes["line_preview"].startswith("GARBAGE")
