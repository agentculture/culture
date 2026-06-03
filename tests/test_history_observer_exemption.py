"""Regression: loopback-observer exemption on HISTORY RECENT.

v8.18.2-B #1 hardened HISTORY RECENT to require the requesting client
be a current member of the channel — without that gate any registered
client could scrape every channel's full conversation log. That fix
broke the mesh-overview collector
(``culture/overview/collector.py``), which connects as an ephemeral
``<server>-_overview<hex>`` reader and queries HISTORY for every
channel discovered via LIST. The collector cannot JOIN before reading:
JOIN/PART pollute channel history with observer noise, and the
per-class ACL refuses observer JOINs on ``#task-*`` outright.

The remediation is a conjoined exemption (see
``HistorySkill._is_loopback_observer``):

  - the requesting nick matches ``<server>-_overview*`` AND
  - the connection's remote host is loopback.

These tests lock the contract in three directions:

  1. **Positive** — a loopback observer can drain HISTORY for a
     channel it never joined.
  2. **Negative (nick shape)** — a registered non-observer client
     still gets ERR_NOTONCHANNEL.
  3. **Negative (off-host)** — a client with the observer nick prefix
     but a non-loopback ``host`` cannot use the exemption (defense
     against impersonation if a real off-host link ever surfaces).
"""

from __future__ import annotations

import pytest

from culture.agentirc.skills.history import HistorySkill


class _FakeChannel:
    def __init__(self, members):
        self.members = members


class _FakeServer:
    def __init__(self, name: str):
        from types import SimpleNamespace

        self.config = SimpleNamespace(name=name)
        self.channels: dict = {}


class _FakeClient:
    def __init__(self, nick: str, host: str):
        self.nick = nick
        self.host = host


def _make_skill(server_name: str = "testserv") -> tuple[HistorySkill, _FakeServer]:
    skill = HistorySkill()
    server = _FakeServer(server_name)
    skill.server = server  # type: ignore[attr-defined]
    return skill, server


class TestObserverExemption:
    def test_loopback_observer_may_read_history_without_membership(self) -> None:
        """Positive: ephemeral overview observer bypasses the gate."""
        skill, server = _make_skill()
        server.channels["#anything"] = _FakeChannel(members=[])
        observer = _FakeClient(nick="testserv-_overviewab12", host="127.0.0.1")
        assert skill._client_may_read_history(observer, "#anything") is True

    def test_loopback_observer_ipv6_also_allowed(self) -> None:
        """``::1`` is a loopback address too — accept it."""
        skill, server = _make_skill()
        server.channels["#anything"] = _FakeChannel(members=[])
        observer = _FakeClient(nick="testserv-_overviewcd34", host="::1")
        assert skill._client_may_read_history(observer, "#anything") is True

    def test_non_observer_still_requires_membership(self) -> None:
        """Negative: a regular registered client gets the normal gate."""
        skill, server = _make_skill()
        ch = _FakeChannel(members=[])
        server.channels["#secret"] = ch
        peer = _FakeClient(nick="testserv-alice", host="127.0.0.1")
        assert skill._client_may_read_history(peer, "#secret") is False

    def test_observer_nick_offhost_blocked(self) -> None:
        """Negative: observer-shaped nick from off-host is NOT exempt.

        Defense against a future federation/proxy path that surfaces a
        remote IP — the conjoined gate must require BOTH conditions.
        """
        skill, server = _make_skill()
        server.channels["#anything"] = _FakeChannel(members=[])
        impersonator = _FakeClient(nick="testserv-_overview0001", host="10.0.0.5")
        assert skill._client_may_read_history(impersonator, "#anything") is False

    def test_loopback_but_wrong_nick_prefix_blocked(self) -> None:
        """Negative: localhost client without observer prefix is NOT exempt."""
        skill, server = _make_skill()
        server.channels["#anything"] = _FakeChannel(members=[])
        # No '-_overview' infix.
        regular = _FakeClient(nick="testserv-alice", host="127.0.0.1")
        assert skill._client_may_read_history(regular, "#anything") is False

    def test_observer_prefix_for_different_server_blocked(self) -> None:
        """Negative: ``otherserv-_overviewXX`` on our testserv mesh
        does NOT match — the prefix check binds the observer nick to
        THIS server's name. Cross-server federation must not let a
        peer's observer scrape this server."""
        skill, server = _make_skill(server_name="testserv")
        server.channels["#anything"] = _FakeChannel(members=[])
        peer_observer = _FakeClient(nick="otherserv-_overviewff", host="127.0.0.1")
        assert skill._client_may_read_history(peer_observer, "#anything") is False

    def test_unknown_channel_still_returns_false(self) -> None:
        """Even an observer cannot read a channel that does not exist —
        ``_client_may_read_history`` returns False for unknown channels
        regardless of who's asking."""
        skill, _server = _make_skill()
        observer = _FakeClient(nick="testserv-_overviewff", host="127.0.0.1")
        assert skill._client_may_read_history(observer, "#nonexistent") is False


@pytest.mark.asyncio
async def test_end_to_end_collector_sees_history_via_real_ircd(server, make_client):
    """End-to-end check that the original bug stays fixed.

    Reproduces the test_overview_collector failure mode without
    importing the collector — uses a raw TCP connection as the
    overview observer would. Pre-fix: HISTORY query returned 405
    ERR_NOTONCHANNEL because the observer was not a member.
    """
    import asyncio

    # Real client joins #observed and sends two messages.
    client = await make_client(nick="testserv-agent", user="agent")
    await client.send("JOIN #observed")
    await client.recv_all(timeout=0.5)
    await client.send("PRIVMSG #observed :first")
    await client.send("PRIVMSG #observed :second")
    await asyncio.sleep(0.3)

    # Observer connects loopback with the canonical observer nick.
    reader, writer = await asyncio.open_connection("127.0.0.1", server.config.port)
    try:
        writer.write(b"NICK testserv-_overview01\r\nUSER overview 0 * :overview\r\n")
        await writer.drain()
        # Drain welcome (until 001 — RPL_WELCOME)
        deadline = asyncio.get_event_loop().time() + 2.0
        while asyncio.get_event_loop().time() < deadline:
            line = await asyncio.wait_for(reader.readline(), timeout=2.0)
            if not line:
                break
            if b" 001 " in line:
                break
        # Drain the rest of the welcome burst.
        try:
            while True:
                await asyncio.wait_for(reader.readline(), timeout=0.2)
        except asyncio.TimeoutError:
            pass
        # Query HISTORY without joining.
        writer.write(b"HISTORY RECENT #observed 10\r\n")
        await writer.drain()
        collected: list[bytes] = []
        deadline = asyncio.get_event_loop().time() + 2.0
        while asyncio.get_event_loop().time() < deadline:
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=1.0)
            except asyncio.TimeoutError:
                break
            if not line:
                break
            collected.append(line)
            if b"HISTORYEND" in line:
                break
        joined = b"".join(collected).decode()
        assert "first" in joined, f"observer did not see history: {joined!r}"
        assert "second" in joined, f"observer did not see history: {joined!r}"
        assert "405" not in joined, f"got ERR_NOTONCHANNEL despite exemption: {joined!r}"
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except ConnectionError:
            pass
