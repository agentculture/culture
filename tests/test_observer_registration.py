"""Observer registration error surfacing (v9.1.6 BUG 1 fix).

Pre-9.1.6, ``IRCObserver._connect_and_register`` only handled IRC
numerics ``001`` (welcome) and ``433`` (nick in use). Any other
rejection numeric — most importantly ``432`` (ERR_ERRONEUSNICKNAME)
which the IRCd sends when the observer's nick doesn't match the
server's expected ``<server_name>-`` prefix — was silently dropped.
The loop continued reading until ``RECV_TIMEOUT`` and surfaced as
"Timed out waiting for server welcome" with zero diagnostic value.

These tests pin the v9.1.6 contract: a fatal registration numeric
raises :class:`RegistrationRejected` immediately with the server's
verbatim reason text, attempted nick, and connection metadata. The
``TimeoutError`` path additionally reports the last lines actually
received so a NOVEL rejection numeric (one not in the fatal set)
still gives operators a clue.
"""

from __future__ import annotations

import asyncio
import socket

import pytest

from culture.observer import (
    _FATAL_REGISTRATION_NUMERICS,
    IRCObserver,
    RegistrationRejected,
)

# ---------------------------------------------------------------------------
# Fake IRCd helpers — minimal TCP servers that send fixed responses to
# the observer's NICK/USER and then either close or hang.
# ---------------------------------------------------------------------------


async def _drain_registration_lines(reader: asyncio.StreamReader) -> tuple[str, str]:
    """Read NICK + USER from the connecting client (any order)."""
    nick = ""
    user = ""
    while not (nick and user):
        line = await reader.readline()
        if not line:
            break
        decoded = line.decode().strip()
        if decoded.upper().startswith("NICK "):
            nick = decoded.split(" ", 1)[1]
        elif decoded.upper().startswith("USER "):
            user = decoded
    return nick, user


async def _start_fake_ircd(handler):
    """Start a TCP server that delegates to ``handler(reader, writer)``.
    Returns ``(server, host, port)``."""
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    sock = server.sockets[0]
    host, port = sock.getsockname()[:2]
    return server, host, port


# ---------------------------------------------------------------------------
# Fatal numeric — server rejects nick prefix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_observer_raises_on_erroneous_nick(unused_tcp_port):
    """When the IRCd sends ``432`` rejecting the nick prefix, the
    observer raises ``RegistrationRejected`` IMMEDIATELY — no wait for
    RECV_TIMEOUT, no bare "Timed out waiting for welcome" message."""

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        nick, _ = await _drain_registration_lines(reader)
        # IRCd ERR_ERRONEUSNICKNAME shape: ":server 432 <nick> :<reason>"
        writer.write(f":local 432 {nick} :Nickname must start with local-\r\n".encode())
        await writer.drain()
        # Hold the connection open briefly so the observer sees the line
        # before the close. Pre-9.1.6 the observer would have ignored the
        # 432, waited for 001, and hit RECV_TIMEOUT.
        await asyncio.sleep(0.1)
        writer.close()

    server, host, port = await _start_fake_ircd(handler)
    try:
        observer = IRCObserver(host=host, port=port, server_name="plenty")
        with pytest.raises(RegistrationRejected) as exc:
            await observer._connect_and_register()

        # Structured fields the operator needs to diagnose.
        assert exc.value.numeric == "432"
        assert "Nickname must start with local-" in exc.value.server_text
        assert exc.value.attempted_nick.startswith("plenty-_peek")
        assert exc.value.server_name == "plenty"
        # Verbose message includes the migration hint.
        assert "culture migrate boss-prefix" in str(exc.value) or "migrate-prefix" in str(exc.value)
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "numeric,reason",
    [
        ("432", "Bad nick"),
        ("464", "Password required"),
        ("465", "You are banned"),
    ],
)
async def test_observer_fatal_numerics_all_surface(numeric, reason):
    """Every numeric in ``_FATAL_REGISTRATION_NUMERICS`` triggers the
    rejection path."""

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        nick, _ = await _drain_registration_lines(reader)
        writer.write(f":local {numeric} {nick} :{reason}\r\n".encode())
        await writer.drain()
        await asyncio.sleep(0.1)
        writer.close()

    server, host, port = await _start_fake_ircd(handler)
    try:
        observer = IRCObserver(host=host, port=port, server_name="local")
        with pytest.raises(RegistrationRejected) as exc:
            await observer._connect_and_register()
        assert exc.value.numeric == numeric
        assert reason in exc.value.server_text
    finally:
        server.close()
        await server.wait_closed()


# ---------------------------------------------------------------------------
# 433 (nick in use) is NOT fatal — retried with a fresh nick
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_observer_433_retries_not_fatal():
    """A 433 (nick in use) makes the observer mint a NEW temp nick and
    re-NICK. The server's subsequent 001 completes registration. Pre-
    AND post-9.1.6 behavior — regression guard."""
    nick_attempts: list[str] = []

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        nick1, _ = await _drain_registration_lines(reader)
        nick_attempts.append(nick1)
        writer.write(f":local 433 {nick1} :Nickname is already in use\r\n".encode())
        await writer.drain()

        # Read the retry NICK.
        line = await reader.readline()
        nick2 = line.decode().strip().split(" ", 1)[1]
        nick_attempts.append(nick2)
        writer.write(f":local 001 {nick2} :Welcome\r\n".encode())
        await writer.drain()

    server, host, port = await _start_fake_ircd(handler)
    try:
        observer = IRCObserver(host=host, port=port, server_name="local")
        reader, writer, nick = await observer._connect_and_register()
        writer.close()
        assert nick == nick_attempts[-1]
        assert len(nick_attempts) == 2
        assert nick_attempts[0] != nick_attempts[1]
    finally:
        server.close()
        await server.wait_closed()


# ---------------------------------------------------------------------------
# Timeout path — surfaces the last lines received (novel rejection guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_observer_timeout_includes_received_tail(monkeypatch):
    """If the IRCd sends some NOVEL numeric the observer doesn't
    recognize as fatal AND doesn't send 001, the observer eventually
    times out — but the error must include the line(s) actually
    received so an operator can identify the unknown numeric."""

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        nick, _ = await _drain_registration_lines(reader)
        # Send a NOTICE — not in the fatal set, not 001/433, completely
        # opaque to the registration loop.
        writer.write(
            f":local NOTICE * :Hello, please wait while we authenticate {nick}\r\n".encode()
        )
        await writer.drain()
        # Then hold open without sending 001 → observer hits RECV_TIMEOUT.
        await asyncio.sleep(10.0)

    # Shorten RECV_TIMEOUT for speed.
    import culture.observer as obs

    monkeypatch.setattr(obs, "RECV_TIMEOUT", 0.3)

    server, host, port = await _start_fake_ircd(handler)
    try:
        observer = IRCObserver(host=host, port=port, server_name="local")
        with pytest.raises(ConnectionError) as exc:
            await observer._connect_and_register()
        msg = str(exc.value)
        assert "Timed out" in msg
        assert "Last lines received" in msg
        assert "please wait while we authenticate" in msg
        assert observer.server_name in msg
    finally:
        server.close()
        await server.wait_closed()


# ---------------------------------------------------------------------------
# Fatal-numerics set sanity check
# ---------------------------------------------------------------------------


def test_fatal_numerics_includes_erroneous_nick():
    """432 is the load-bearing case for BUG 1 — without it in the set,
    the in-place server.name drift would still silently wedge to
    RECV_TIMEOUT."""
    assert "432" in _FATAL_REGISTRATION_NUMERICS


def test_fatal_numerics_does_not_include_001_or_433():
    """001 (welcome) and 433 (nick in use) are NOT fatal — the loop
    has explicit handling for both."""
    assert "001" not in _FATAL_REGISTRATION_NUMERICS
    assert "433" not in _FATAL_REGISTRATION_NUMERICS
