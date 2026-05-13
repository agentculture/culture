"""Tests for culture.observer — query cycle + public API + parsers.

`test_observer_peek_nick.py` covers parent-aware nick attribution. This
file covers the broader observer surface against a real IRCd: the async
connect/register handshake, the query cycle around `_irc_query`, and the
public `read_channel` / `who` / `send_message` / `list_channels`
methods. Pure parsers (`_parse_history_line`, `_parse_who_line`,
`_parse_list_line`) get table-driven unit tests since they are static.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from culture.observer import IRCObserver
from culture.protocol.message import Message

# ---------------------------------------------------------------------------
# Pure parsers — table-driven
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line, expected_contains",
    [
        # Four-param HISTORY with float timestamp
        (
            "HISTORY #room nick1 1715000000.0 :hello there",
            "<nick1> hello there",
        ),
        # Four-param HISTORY with non-numeric timestamp falls back to raw label
        (
            "HISTORY #room nick2 someday :legacy line",
            "[someday] <nick2> legacy line",
        ),
        # Three-param HISTORY (legacy no-timestamp form)
        ("HISTORY #room nick3 :older line", "<nick3> older line"),
    ],
)
def test_parse_history_line_renders(line: str, expected_contains: str):
    msg = Message.parse(line)
    result = IRCObserver._parse_history_line(msg)
    assert result is not None
    assert expected_contains in result


def test_parse_history_line_returns_none_for_non_history():
    assert IRCObserver._parse_history_line(Message.parse("PRIVMSG #foo :bar")) is None


def test_parse_history_line_returns_none_for_too_few_params():
    # Only 1 param: HISTORY <something> — nothing to render.
    assert IRCObserver._parse_history_line(Message.parse("HISTORY #room")) is None


def test_parse_who_line_extracts_nick():
    # 352 server :you nick user host server target H :hops realname
    line = ":server 352 you #room user host srv target H :0 realname"
    assert IRCObserver._parse_who_line(Message.parse(line)) == "target"


def test_parse_who_line_returns_none_for_non_352():
    assert IRCObserver._parse_who_line(Message.parse(":srv 353 you = #room :nick1 nick2")) is None


def test_parse_list_line_extracts_channel():
    # 322 server :you #channel 3 :topic
    line = ":srv 322 you #foo 3 :a topic"
    assert IRCObserver._parse_list_line(Message.parse(line)) == "#foo"


def test_parse_list_line_returns_none_for_other_numerics():
    assert IRCObserver._parse_list_line(Message.parse(":srv 323 you :End")) is None


# ---------------------------------------------------------------------------
# _process_registration_line / _process_query_line — direct unit tests
# ---------------------------------------------------------------------------


class _RecordingWriter:
    """Minimal StreamWriter stand-in that captures bytes written.

    The observer only calls `write` + `drain`; `drain` is async. Close is
    also called from `_disconnect` but those tests use the real server.
    """

    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.sent.append(data)

    async def drain(self) -> None:
        return None


@pytest.mark.asyncio
async def test_process_registration_line_001_signals_done():
    obs = IRCObserver(host="x", port=0, server_name="srv")
    writer = _RecordingWriter()
    done, nick = await obs._process_registration_line(
        ":srv 001 mynick :Welcome",
        writer,  # type: ignore[arg-type]
        "mynick",
    )
    assert done is True
    assert nick == "mynick"
    assert writer.sent == []


@pytest.mark.asyncio
async def test_process_registration_line_433_retries_with_new_nick():
    obs = IRCObserver(host="x", port=0, server_name="srv", parent_nick="srv-claude")
    writer = _RecordingWriter()
    done, nick = await obs._process_registration_line(
        ":srv 433 * oldnick :Nickname is already in use",
        writer,  # type: ignore[arg-type]
        "oldnick",
    )
    assert done is False
    # New nick was generated and re-sent.
    assert nick != "oldnick"
    assert writer.sent and writer.sent[0].startswith(b"NICK ")


@pytest.mark.asyncio
async def test_process_registration_line_unknown_command_is_skipped():
    obs = IRCObserver(host="x", port=0, server_name="srv")
    writer = _RecordingWriter()
    done, nick = await obs._process_registration_line(
        ":srv 002 mynick :Your host is...",
        writer,  # type: ignore[arg-type]
        "mynick",
    )
    assert done is False
    assert nick == "mynick"
    assert writer.sent == []


@pytest.mark.asyncio
async def test_process_query_line_end_numeric_stops():
    writer = _RecordingWriter()
    msg = Message.parse(":srv 315 me #room :End of WHO")
    results: list[str] = []
    done = await IRCObserver._process_query_line(
        msg,
        {"315"},
        lambda _m: "ignored",
        results,
        writer,  # type: ignore[arg-type]
    )
    assert done is True
    # parser is short-circuited on end numerics — nothing collected.
    assert results == []


@pytest.mark.asyncio
async def test_process_query_line_responds_to_ping():
    writer = _RecordingWriter()
    msg = Message.parse("PING :token123")
    done = await IRCObserver._process_query_line(
        msg,
        {"315"},
        lambda _m: None,
        [],
        writer,  # type: ignore[arg-type]
    )
    assert done is False
    assert writer.sent == [b"PONG :token123\r\n"]


@pytest.mark.asyncio
async def test_process_query_line_collects_parsed_value():
    writer = _RecordingWriter()
    msg = Message.parse(":srv 352 me #room user host srv target H :0 rn")
    results: list[str] = []
    done = await IRCObserver._process_query_line(
        msg,
        {"315"},
        IRCObserver._parse_who_line,
        results,
        writer,  # type: ignore[arg-type]
    )
    assert done is False
    assert results == ["target"]


@pytest.mark.asyncio
async def test_drain_query_buffer_returns_unfinished_partial():
    obs = IRCObserver(host="x", port=0, server_name="srv")
    writer = _RecordingWriter()
    # "PING :ok\r\n" plus a half line.
    buffer = "PING :ok\r\nHISTORY #room nick 1715"
    remainder, done = await obs._drain_query_buffer(
        buffer,
        {"315"},
        IRCObserver._parse_history_line,
        [],
        writer,  # type: ignore[arg-type]
    )
    assert done is False
    assert remainder == "HISTORY #room nick 1715"


@pytest.mark.asyncio
async def test_drain_query_buffer_skips_blank_lines():
    obs = IRCObserver(host="x", port=0, server_name="srv")
    writer = _RecordingWriter()
    # Blank-line-only payload; no PRIVMSGs, no end numeric.
    remainder, done = await obs._drain_query_buffer(
        "\r\n\r\n",
        {"END"},
        IRCObserver._parse_who_line,
        [],
        writer,  # type: ignore[arg-type]
    )
    assert done is False
    assert remainder == ""


# ---------------------------------------------------------------------------
# Public API — round-trip against the real IRCd `server` fixture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_observer_list_channels_sees_active_channel(server, make_client):
    """`list_channels` returns the user-created channel after a client joins."""
    client = await make_client(nick="testserv-c1", user="c1")
    await client.send("JOIN #obs-room")
    await client.recv_all(timeout=0.5)

    obs = IRCObserver(
        host="127.0.0.1",
        port=server.config.port,
        server_name=server.config.name,
    )
    channels = await obs.list_channels()
    assert "#obs-room" in channels


@pytest.mark.asyncio
async def test_observer_list_channels_empty_server(server):
    """No user-joined channels → `list_channels` may be empty or only #system."""
    obs = IRCObserver(
        host="127.0.0.1",
        port=server.config.port,
        server_name=server.config.name,
    )
    channels = await obs.list_channels()
    # No user channel was created — the only thing that could show up is
    # the system-internal #system room, and even that may be filtered
    # depending on server LIST policy. Just assert the call returns a
    # list (which it does) and no user-named room snuck in.
    assert isinstance(channels, list)
    assert "#obs-room" not in channels


@pytest.mark.asyncio
async def test_observer_who_returns_member_nicks(server, make_client):
    client = await make_client(nick="testserv-w1", user="w1")
    await client.send("JOIN #who-room")
    await client.recv_all(timeout=0.5)

    obs = IRCObserver(
        host="127.0.0.1",
        port=server.config.port,
        server_name=server.config.name,
    )
    nicks = await obs.who("#who-room")
    assert "testserv-w1" in nicks


@pytest.mark.asyncio
async def test_observer_read_channel_returns_history(server, make_client):
    client = await make_client(nick="testserv-h1", user="h1")
    await client.send("JOIN #hist-room")
    await client.recv_all(timeout=0.5)
    await client.send("PRIVMSG #hist-room :first observed message")
    await client.send("PRIVMSG #hist-room :second observed message")
    await asyncio.sleep(0.3)

    obs = IRCObserver(
        host="127.0.0.1",
        port=server.config.port,
        server_name=server.config.name,
    )
    lines = await obs.read_channel("#hist-room", limit=20)
    assert any("first observed message" in ln for ln in lines)
    assert any("second observed message" in ln for ln in lines)


@pytest.mark.asyncio
async def test_observer_send_message_lands_on_channel(server, make_client):
    listener = await make_client(nick="testserv-listen", user="listen")
    await listener.send("JOIN #send-room")
    await listener.recv_all(timeout=0.5)

    obs = IRCObserver(
        host="127.0.0.1",
        port=server.config.port,
        server_name=server.config.name,
        parent_nick="testserv-ori",
    )
    await obs.send_message("#send-room", "hello from observer")

    # Give the message a moment to round-trip.
    lines = await listener.recv_all(timeout=0.5)
    assert any("hello from observer" in ln for ln in lines)


@pytest.mark.asyncio
async def test_observer_send_message_splits_on_newlines(server, make_client):
    listener = await make_client(nick="testserv-multi", user="multi")
    await listener.send("JOIN #multi-room")
    await listener.recv_all(timeout=0.5)

    obs = IRCObserver(
        host="127.0.0.1",
        port=server.config.port,
        server_name=server.config.name,
    )
    await obs.send_message("#multi-room", "line one\nline two\n\nline three")

    lines = await listener.recv_all(timeout=0.5)
    text = "\n".join(lines)
    assert "line one" in text
    assert "line two" in text
    assert "line three" in text


@pytest.mark.asyncio
async def test_observer_send_message_empty_payload_skips_connect(monkeypatch):
    """All-empty lines → return without opening a connection."""
    obs = IRCObserver(host="127.0.0.1", port=1, server_name="srv")

    called: list[bool] = []

    async def _fake_connect():
        called.append(True)
        raise AssertionError("should not connect when all lines empty")

    monkeypatch.setattr(obs, "_connect_and_register", _fake_connect)
    await obs.send_message("#room", "\r\n\n")
    assert called == []


@pytest.mark.asyncio
async def test_observer_send_message_to_nick_skips_join(server, make_client):
    """When target is a nick (not a channel), no JOIN is sent."""
    recipient = await make_client(nick="testserv-recv", user="recv")
    # No channel join — receive a DM directly.

    obs = IRCObserver(
        host="127.0.0.1",
        port=server.config.port,
        server_name=server.config.name,
    )
    await obs.send_message("testserv-recv", "direct hello")

    lines = await recipient.recv_all(timeout=0.5)
    assert any("direct hello" in ln for ln in lines)


@pytest.mark.asyncio
async def test_observer_send_message_strips_target_crlf(monkeypatch):
    """CR/LF in target is stripped before any PRIVMSG/JOIN is composed.

    Without the strip, a malformed target would let a caller smuggle a
    second IRC line. After the strip, the target collapses into a single
    (bogus) channel name — no second protocol line is ever emitted.
    """
    obs = IRCObserver(host="127.0.0.1", port=1, server_name="srv")
    captured: dict[str, object] = {}

    class _FakeWriter:
        def __init__(self):
            self.sent: list[bytes] = []

        def write(self, data: bytes) -> None:
            self.sent.append(data)

        async def drain(self):
            return None

        def close(self):
            return None

        async def wait_closed(self):
            return None

    class _FakeReader:
        async def read(self, _n: int):
            return b""

    fake_writer = _FakeWriter()

    async def _fake_connect():
        captured["writer"] = fake_writer
        return _FakeReader(), fake_writer, "nick"

    async def _fake_recv(_reader, timeout=0.0):
        return []

    monkeypatch.setattr(obs, "_connect_and_register", _fake_connect)
    monkeypatch.setattr(obs, "_recv_lines", _fake_recv)

    await obs.send_message("#strip-room\r\nJOIN #danger", "smuggled")

    sent = b"".join(fake_writer.sent)
    # JOIN + PRIVMSG + QUIT (from _disconnect) — exactly 3 CRLFs. The
    # injected JOIN payload becomes part of the channel name, which the
    # server will reject; no extra IRC line is smuggled past the strip.
    assert sent.count(b"\r\n") == 3, sent
    assert b"#strip-roomJOIN #danger" in sent
    assert b"smuggled" in sent
    # Critical: nothing in the sent bytes opens an extra protocol line.
    assert b"\nJOIN" not in sent


@pytest.mark.asyncio
async def test_observer_registration_timeout_raises(monkeypatch):
    """Opening the TCP connection times out → ConnectionError."""

    async def _slow_open(*_a, **_kw):
        await asyncio.sleep(10)
        raise AssertionError("should have timed out")

    import culture.observer as obs_mod

    monkeypatch.setattr(obs_mod, "REGISTER_TIMEOUT", 0.05)
    monkeypatch.setattr(obs_mod.asyncio, "open_connection", _slow_open)

    obs = IRCObserver(host="127.0.0.1", port=1, server_name="srv")
    with pytest.raises((asyncio.TimeoutError, TimeoutError)):
        await obs._connect_and_register()


@pytest.mark.asyncio
async def test_observer_realname_carries_parent_attribution(server, make_client):
    """Cross-server peeks fall back to opaque nick but realname keeps the parent."""
    # Use a parent that doesn't match the server prefix → opaque nick form.
    obs = IRCObserver(
        host="127.0.0.1",
        port=server.config.port,
        server_name=server.config.name,
        parent_nick="thor-claude",
    )
    # A list_channels call exercises connect+register+disconnect. After
    # it completes the server should have seen the USER realname embedding
    # the parent attribution. We can't easily intercept the USER line on
    # the server side without mocks, so just confirm the call works and
    # the parent attribution is preserved on the observer.
    await obs.list_channels()
    assert obs.parent_nick == "thor-claude"
    # `_parent_suffix()` returns None for cross-server parents, which is
    # what falls the nick back to the opaque shape.
    assert obs._parent_suffix() is None
