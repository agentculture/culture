"""NT-4 — bridge 474 ERR_BANNEDFROMCHAN handler drops the channel.

When the IRCd refuses our JOIN (e.g. task-channel ACL), the bridge
must clear the channel from ``self.channels`` so a stale "we joined"
state can't silently swallow subsequent PRIVMSGs (the silent-drop bug
class fixed in v8.19.42 + extended in the mesh re-architecture).

The 474 handler is on the bridge's copy of ``IRCTransport`` — we feed
the raw numeric line directly so the test stays decoupled from any
specific server-side ACL behaviour.
"""

from __future__ import annotations

import pytest

from culture.clients.bridge.irc_transport import IRCTransport
from culture.clients.bridge.message_buffer import MessageBuffer
from culture.protocol.message import Message


def _make_transport(nick: str = "testserv-boss") -> IRCTransport:
    buf = MessageBuffer()
    return IRCTransport(
        host="127.0.0.1",
        port=0,
        nick=nick,
        user="boss",
        channels=[],
        buffer=buf,
    )


class Test474Handler:
    def test_474_drops_channel_from_tracking(self) -> None:
        transport = _make_transport()
        # Simulate that we'd optimistically (or via a confirmed JOIN
        # somewhere earlier) recorded the channel.
        transport.channels.append("#task-other")

        # Server-side 474 line shape: ``:server 474 <our_nick> #task-other
        # :Cannot join channel (banned/refused by ACL)``
        msg = Message(
            prefix="server",
            command="474",
            params=[transport.nick, "#task-other", "Cannot join channel"],
        )
        transport._on_bannedfromchan(msg)

        assert "#task-other" not in transport.channels

    def test_474_for_never_joined_channel_is_safe(self) -> None:
        """If we never added the channel, the handler must not raise."""
        transport = _make_transport()
        assert "#never-joined" not in transport.channels
        msg = Message(
            prefix="server",
            command="474",
            params=[transport.nick, "#never-joined", "Cannot join"],
        )
        # Should NOT raise — handler is idempotent w.r.t. missing channels.
        transport._on_bannedfromchan(msg)
        assert "#never-joined" not in transport.channels

    def test_474_routed_via_cmd_handlers_table(self) -> None:
        """The handler must be wired into ``_cmd_handlers`` under '474'
        so the read-loop's dispatch reaches it without manual wiring."""
        transport = _make_transport()
        assert "474" in transport._cmd_handlers
        # Bound-method identity differs across attribute lookups in Python,
        # so compare by ``__func__`` (the underlying function object).
        handler = transport._cmd_handlers["474"]
        assert getattr(handler, "__func__", handler) is (
            IRCTransport._on_bannedfromchan
        )

    def test_474_handler_ignores_short_params(self) -> None:
        """A malformed 474 (no channel) must not crash."""
        transport = _make_transport()
        msg = Message(prefix="server", command="474", params=[transport.nick])
        # No exception expected.
        transport._on_bannedfromchan(msg)


@pytest.mark.asyncio
async def test_474_via_raw_message_parse() -> None:
    """End-to-end through ``_handle`` — feed the raw IRC line, parse it,
    and confirm the channel is removed from tracking.
    """
    transport = _make_transport(nick="testserv-bridge")
    transport.channels.append("#task-other")
    raw = f":testserv 474 {transport.nick} #task-other :Cannot join channel"
    msg = Message.parse(raw)
    await transport._handle(msg)
    assert "#task-other" not in transport.channels
