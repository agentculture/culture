"""Phase 3 IDOR guard — peer cannot drain another nick's spool.

A peer issuing ``CHATHISTORY other-nick`` MUST receive
``ERR_NOPRIVILEGES (481)`` and zero spooled messages. The only nick
permitted to drain a spool is the recipient itself.

This is the security backstop reviewed as iter-4 B-3 (Phase 3.3):
without it, any registered client can enumerate every boss's
unread DMs.
"""

from __future__ import annotations

import asyncio

import pytest

from culture.agentirc import client as ircd_client_mod

_BOSS_NICK = "testserv-boss"
_PEER_NICK = "testserv-eve"


@pytest.fixture(autouse=True)
def _isolate_culture_home(tmp_path, monkeypatch):
    """Point CULTURE_HOME at tmp_path and seed a manifest where
    ``testserv-boss`` is a boss-tagged agent (so DMs to it land in
    the spool instead of bouncing)."""
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    boss_dir = tmp_path / "boss_home"
    boss_dir.mkdir()
    (tmp_path / "server.yaml").write_text(
        "server:\n" "  name: testserv\n" "agents:\n" f"  boss: {boss_dir}\n",
        encoding="utf-8",
    )
    (boss_dir / "culture.yaml").write_text(
        "suffix: boss\n" "tags: [boss]\n" "channels: []\n",
        encoding="utf-8",
    )
    ircd_client_mod._invalidate_owner_map_cache()
    yield
    ircd_client_mod._invalidate_owner_map_cache()


async def _register_peer(server, nick: str):
    reader, writer = await asyncio.open_connection("127.0.0.1", server.config.port)

    async def _send(line: str) -> None:
        writer.write(f"{line}\r\n".encode())
        await writer.drain()

    await _send(f"NICK {nick}")
    await _send(f"USER {nick} 0 * :{nick}")
    # Drain welcome.
    try:
        async with asyncio.timeout(2.0):
            buf = b""
            while True:
                chunk = await reader.read(1024)
                if not chunk:
                    break
                buf += chunk
                if b" 005 " in buf or b" 004 " in buf:
                    break
    except (asyncio.TimeoutError, TimeoutError):
        pass
    return reader, writer


@pytest.mark.asyncio
async def test_cross_nick_chathistory_returns_err_noprivileges(server) -> None:
    """A peer issuing CHATHISTORY against another nick gets 481."""
    # Spool a DM to the boss so there's something concrete to NOT see.
    boss_reader, boss_writer = await _register_peer(server, "testserv-sender")
    boss_writer.write(f"PRIVMSG {_BOSS_NICK} :secret\r\n".encode())
    await boss_writer.drain()
    await asyncio.sleep(0.2)
    boss_writer.close()
    try:
        await boss_writer.wait_closed()
    except ConnectionError:
        pass

    # Now Eve tries to drain the boss's spool.
    reader, writer = await _register_peer(server, _PEER_NICK)
    try:
        writer.write(f"CHATHISTORY {_BOSS_NICK} 100\r\n".encode())
        await writer.drain()
        saw_481 = False
        saw_secret = False
        try:
            async with asyncio.timeout(2.0):
                while not saw_481:
                    chunk = await reader.read(2048)
                    if not chunk:
                        break
                    text = chunk.decode("utf-8", "replace")
                    if " 481 " in text:
                        saw_481 = True
                    if "secret" in text:
                        saw_secret = True
        except (asyncio.TimeoutError, TimeoutError):
            pass
        assert saw_481, "cross-nick CHATHISTORY MUST return ERR_NOPRIVILEGES (481)"
        assert not saw_secret, "cross-nick CHATHISTORY MUST NOT leak spool contents"
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except ConnectionError:
            pass


@pytest.mark.asyncio
async def test_own_nick_chathistory_succeeds(server) -> None:
    """As a baseline, the boss draining ITS OWN spool works.

    Note: the regular IRCd nick policy refuses nicks whose suffix doesn't
    match an agent's culture.yaml, but ``testserv-boss`` was registered
    in the manifest by the autouse fixture so the policy admits it.
    """
    # Pre-spool a DM.
    sender_reader, sender_writer = await _register_peer(server, "testserv-pal")
    sender_writer.write(f"PRIVMSG {_BOSS_NICK} :for the boss\r\n".encode())
    await sender_writer.drain()
    await asyncio.sleep(0.2)
    sender_writer.close()
    try:
        await sender_writer.wait_closed()
    except ConnectionError:
        pass

    # Connect AS the boss and drain its own spool.
    reader, writer = await _register_peer(server, _BOSS_NICK)
    try:
        writer.write(f"CHATHISTORY {_BOSS_NICK} 100\r\n".encode())
        await writer.drain()
        got_payload = False
        try:
            async with asyncio.timeout(2.0):
                while not got_payload:
                    chunk = await reader.read(2048)
                    if not chunk:
                        break
                    text = chunk.decode("utf-8", "replace")
                    if "for the boss" in text:
                        got_payload = True
        except (asyncio.TimeoutError, TimeoutError):
            pass
        assert got_payload, "boss MUST receive its own spool entries"
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except ConnectionError:
            pass


@pytest.mark.asyncio
async def test_chathistory_delete_cross_nick_refused(server) -> None:
    """A peer cannot mark another nick's spool entries delivered."""
    # Pre-spool a DM for boss.
    sender_reader, sender_writer = await _register_peer(server, "testserv-alice")
    sender_writer.write(f"PRIVMSG {_BOSS_NICK} :hi\r\n".encode())
    await sender_writer.drain()
    await asyncio.sleep(0.2)
    sender_writer.close()
    try:
        await sender_writer.wait_closed()
    except ConnectionError:
        pass

    # Find the msg_id by draining the boss's spool through the boss's
    # own client (legit path).
    boss_reader, boss_writer = await _register_peer(server, _BOSS_NICK)
    boss_writer.write(f"CHATHISTORY {_BOSS_NICK} 100\r\n".encode())
    await boss_writer.drain()
    msg_id = None
    try:
        async with asyncio.timeout(2.0):
            buf = b""
            while msg_id is None:
                chunk = await boss_reader.read(2048)
                if not chunk:
                    break
                buf += chunk
                text = buf.decode("utf-8", "replace")
                # Find any ``msgid=...`` token in the batch tags.
                import re

                m = re.search(r"msgid=([0-9a-fA-F]+)", text)
                if m:
                    msg_id = m.group(1)
    except (asyncio.TimeoutError, TimeoutError):
        pass
    boss_writer.close()
    try:
        await boss_writer.wait_closed()
    except ConnectionError:
        pass
    assert msg_id, "boss CHATHISTORY must surface the spooled msgid"

    # Now Eve tries to mark THAT id delivered.
    eve_reader, eve_writer = await _register_peer(server, _PEER_NICK)
    try:
        eve_writer.write(f"CHATHISTORY DELETE {msg_id}\r\n".encode())
        await eve_writer.drain()
        saw_481 = False
        try:
            async with asyncio.timeout(2.0):
                while not saw_481:
                    chunk = await eve_reader.read(2048)
                    if not chunk:
                        break
                    if b" 481 " in chunk:
                        saw_481 = True
        except (asyncio.TimeoutError, TimeoutError):
            pass
        assert saw_481, "CHATHISTORY DELETE for another nick's msg_id MUST be refused"
    finally:
        eve_writer.close()
        try:
            await eve_writer.wait_closed()
        except ConnectionError:
            pass
