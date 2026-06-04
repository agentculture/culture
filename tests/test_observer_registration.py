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
async def test_observer_raises_on_unparseable_432(unused_tcp_port):
    """When the IRCd sends ``432`` with a reason text the v9.1.7
    drift parser CANNOT extract a valid prefix from, the observer
    surfaces the original ``RegistrationRejected`` immediately —
    NO infinite retry, NO bare "timed out waiting for welcome".

    v9.1.6 behavior (raise on 432) is preserved as the FALLBACK for
    cases where the 432 text isn't the IRCd's canonical
    ``"Nickname must start with X-"`` shape. v9.1.7 layers
    auto-recovery on top for the canonical shape only.
    """

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        nick, _ = await _drain_registration_lines(reader)
        # NON-canonical 432 — parser returns None, so no recovery
        # is attempted and the original behavior applies.
        writer.write(f":local 432 {nick} :Your nick is funky\r\n".encode())
        await writer.drain()
        await asyncio.sleep(0.1)
        writer.close()

    server, host, port = await _start_fake_ircd(handler)
    try:
        observer = IRCObserver(host=host, port=port, server_name="plenty")
        with pytest.raises(RegistrationRejected) as exc:
            await observer._connect_and_register()

        assert exc.value.numeric == "432"
        assert exc.value.attempted_nick.startswith("plenty-_peek")
        assert exc.value.server_name == "plenty"
        # The hint still points operators at the recovery tool even
        # when auto-recovery couldn't fire.
        assert "migrate-prefix" in str(exc.value)
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_observer_auto_recovers_from_canonical_432(caplog):
    """v9.1.7 — when the IRCd sends a canonical 432
    ``Nickname must start with local-`` and ``self.server_name``
    is ``plenty``, the observer parses ``local``, retries ONCE
    with the corrected prefix, completes registration, and logs
    ONE warning naming the migration command.

    This is the BUG 1 root-cause fix the user reported.
    """
    attempts: list[str] = []

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        nick, _ = await _drain_registration_lines(reader)
        attempts.append(nick)
        if nick.startswith("plenty-"):
            # First attempt: reject with canonical 432.
            writer.write(f":local 432 {nick} :Nickname must start with local-\r\n".encode())
            await writer.drain()
            await asyncio.sleep(0.05)
            writer.close()
            return
        # Second attempt (corrected prefix): accept.
        writer.write(f":local 001 {nick} :Welcome\r\n".encode())
        await writer.drain()
        # Hold open so the observer can return the streams cleanly.
        try:
            await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass

    import logging as _logging

    caplog.set_level(_logging.WARNING)
    server, host, port = await _start_fake_ircd(handler)
    try:
        observer = IRCObserver(host=host, port=port, server_name="plenty")
        reader, writer, nick = await observer._connect_and_register()
        writer.close()

        # Two attempts: the first under the stale 'plenty' prefix,
        # the second under the auto-corrected 'local' prefix.
        assert len(attempts) == 2
        assert attempts[0].startswith("plenty-_peek")
        assert attempts[1].startswith("local-_peek")
        # The returned nick reflects the actual successful one.
        assert nick.startswith("local-_peek")
        # self.server_name is NOT mutated — config-of-record stays
        # whatever's on disk so the operator notices the drift.
        assert observer.server_name == "plenty"

        # Exactly one warning, with the actionable command.
        drift_warns = [r for r in caplog.records if "drift" in r.message.lower()]
        assert len(drift_warns) == 1
        assert "culture server migrate-prefix" in drift_warns[0].message
        assert "plenty" in drift_warns[0].message
        assert "local" in drift_warns[0].message
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_observer_does_not_retry_more_than_once():
    """Hard cap: a SECOND 432 (e.g. the IRCd lied or the prefix
    changed between attempts) surfaces as RegistrationRejected.
    No infinite loop. _MAX_DRIFT_RETRIES = 1."""
    attempts: list[str] = []

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        nick, _ = await _drain_registration_lines(reader)
        attempts.append(nick)
        # Always reject — first with one prefix, then with another.
        if len(attempts) == 1:
            writer.write(f":local 432 {nick} :Nickname must start with local-\r\n".encode())
        else:
            writer.write(f":local 432 {nick} :Nickname must start with other-\r\n".encode())
        await writer.drain()
        await asyncio.sleep(0.05)
        writer.close()

    server, host, port = await _start_fake_ircd(handler)
    try:
        observer = IRCObserver(host=host, port=port, server_name="plenty")
        with pytest.raises(RegistrationRejected) as exc:
            await observer._connect_and_register()
        # We tried exactly twice — the original + one drift retry.
        assert len(attempts) == 2
        # The surfaced rejection is the SECOND one (after retry).
        assert exc.value.numeric == "432"
        assert "other-" in exc.value.server_text
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


# ---------------------------------------------------------------------------
# v9.1.7 — drift-prefix parser unit tests + IRCd source contract
# ---------------------------------------------------------------------------


from culture.observer import _parse_expected_prefix  # noqa: E402


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Nickname must start with local-", "local"),
        ("Nickname must start with plenty-", "plenty"),
        ("Nickname must start with fork-rearch-", "fork-rearch"),
        # Trailing whitespace is tolerated (some IRCds pad).
        ("Nickname must start with thor-   ", "thor"),
    ],
)
def test_parse_expected_prefix_canonical_shapes(text, expected):
    assert _parse_expected_prefix(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Your nick is funky",
        "Bad nickname",
        # No trailing hyphen — not the IRCd's canonical shape.
        "Nickname must start with local",
        # Mid-string match would be a bug — the regex is anchored.
        "Suffix only: Nickname must start with X- and other stuff",
    ],
)
def test_parse_expected_prefix_rejects_non_canonical(text):
    assert _parse_expected_prefix(text) is None


@pytest.mark.parametrize(
    "text",
    [
        # Uppercase forbidden — IRC server names are lowercase ASCII.
        "Nickname must start with LOCAL-",
        # Unicode + shell metacharacters — hostile-IRCd defense.
        "Nickname must start with $(rm -rf /)-",
        "Nickname must start with foo;ls-",
        "Nickname must start with " + "a" * 33 + "-",
    ],
)
def test_parse_expected_prefix_rejects_hostile_shapes(text):
    """Defensive: even if the parser regex matches, the result must
    pass the charset + length check or be rejected. A hostile IRCd
    never influences our nick choice."""
    assert _parse_expected_prefix(text) is None


@pytest.mark.asyncio
async def test_parser_contract_round_trip_against_real_ircd():
    """Contract test (v9.1.7 r2 — Qodo PR #59 #4): the v9.1.7 r1
    version of this test grepped the IRCd source for the literal
    substring ``"Nickname must start with "`` — a future commit
    that drops the keyword while leaving the substring in a comment
    or a removed string would silently break runtime auto-recovery
    without failing CI.

    The r2 version spins up a REAL ``AgentIRCd`` instance in-process
    with ``--name=someserver``, connects a raw TCP client, sends a
    ``NICK`` with the wrong prefix, captures the IRCd's 432 reply
    verbatim from the wire, and feeds the reason text through
    ``_parse_expected_prefix``. The byte-for-byte round-trip catches
    any wording change at the IRCd that would break the parser —
    not at runtime in production, here in CI at the same commit.
    """
    from culture.agentirc.config import ServerConfig as IRCdServerConfig
    from culture.agentirc.ircd import IRCd

    cfg = IRCdServerConfig(name="someserver", host="127.0.0.1", port=0)
    ircd = IRCd(cfg)
    await ircd.start()
    try:
        # Resolve the port the IRCd actually bound to.
        port = ircd._server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(b"NICK badprefix-test\r\nUSER c 0 * :c\r\n")
            await writer.drain()

            captured_reason = None
            for _ in range(20):
                line = await asyncio.wait_for(reader.readline(), timeout=1.0)
                if not line:
                    break
                decoded = line.decode().strip()
                if " 432 " in decoded:
                    # Format: ":server 432 <nick> :<reason>"
                    captured_reason = decoded.split(":", 2)[-1]
                    break
            assert captured_reason is not None, (
                "IRCd did not send a 432 within 20 lines — registration "
                "behavior changed; update the parser AND this test together."
            )

            parsed = _parse_expected_prefix(captured_reason)
            assert parsed == "someserver", (
                f"Parser failed to extract expected prefix from real "
                f"IRCd reason {captured_reason!r}. Either the IRCd's "
                f"reason text changed in culture/agentirc/client.py OR "
                f"the parser regex in culture/observer.py::_DRIFT_PARSE_RE "
                f"diverged. Update them in lockstep."
            )
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
    finally:
        await ircd.stop()
