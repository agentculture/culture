"""NT-6 — DM spool drain on bridge reconnect.

Phase 3 of the 2026-06-03 mesh-rearch. End-to-end: peer DMs an offline
boss nick → IRCd spools to SQLite → bridge starts → CHATHISTORY drain
fires on welcome → CC sees inbound_dm via the bridge IPC whisper
queue.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile

import pytest
import pytest_asyncio

from culture.agentirc import client as ircd_client_mod
from culture.agentirc.dm_spool_store import default_spool_path
from culture.clients.bridge.daemon import AgentDaemon
from culture.clients.bridge.ipc import decode_message
from culture.clients.claude.config import (
    AgentConfig,
    DaemonConfig,
    ServerConnConfig,
)

_BOSS_NICK = "testserv-boss"
_PEER_NICK = "testserv-peer"


@pytest_asyncio.fixture
async def culture_home_with_boss(tmp_path, monkeypatch):
    """Point CULTURE_HOME at tmp_path and seed a manifest where
    ``testserv-boss`` is a boss-tagged agent. Invalidates the
    owner_map cache so the IRCd sees the manifest immediately.
    """
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    boss_dir = tmp_path / "boss_home"
    boss_dir.mkdir()
    # Manifest-format server.yaml: ``agents`` is a dict mapping
    # suffix → directory. server.name MUST match the test IRCd's name
    # ("testserv") so resolved nicks match what the IRCd checks.
    (tmp_path / "server.yaml").write_text(
        "server:\n" "  name: testserv\n" "agents:\n" f"  boss: {boss_dir}\n",
        encoding="utf-8",
    )
    # Per-agent culture.yaml under its directory.
    (boss_dir / "culture.yaml").write_text(
        "suffix: boss\n" "tags: [boss]\n" "channels: []\n",
        encoding="utf-8",
    )
    ircd_client_mod._invalidate_owner_map_cache()
    yield tmp_path
    ircd_client_mod._invalidate_owner_map_cache()


async def _send_dm_as_peer(server, sender: str, recipient: str, text: str) -> None:
    """Open a raw TCP connection, register a peer nick, DM, then quit."""
    reader, writer = await asyncio.open_connection("127.0.0.1", server.config.port)

    async def _send(line: str) -> None:
        writer.write(f"{line}\r\n".encode())
        await writer.drain()

    await _send(f"NICK {sender}")
    await _send(f"USER {sender} 0 * :{sender}")
    # Drain the welcome.
    try:
        async with asyncio.timeout(2.0):
            buf = b""
            while True:
                chunk = await reader.read(1024)
                if not chunk:
                    break
                buf += chunk
                if b" 005 " in buf or b" 004 " in buf:
                    # Welcome flow complete; we've at least seen MYINFO/ISUPPORT.
                    break
    except (asyncio.TimeoutError, TimeoutError):
        pass
    await _send(f"PRIVMSG {recipient} :{text}")
    # Brief pause so the IRCd processes the line before we close.
    await asyncio.sleep(0.1)
    await _send("QUIT :bye")
    writer.close()
    try:
        await writer.wait_closed()
    except ConnectionError:
        pass


@pytest.mark.asyncio
async def test_dm_to_offline_boss_lands_in_spool(culture_home_with_boss, server) -> None:
    """Spool-eligibility check (Task 3.2): peer DMs the boss nick while
    bridge is offline → entry persisted in dm_spool DB.
    """
    await _send_dm_as_peer(server, _PEER_NICK, _BOSS_NICK, "hello while offline")
    await asyncio.sleep(0.2)
    # Read the spool DB directly to verify the insert.
    spool_path = default_spool_path("testserv", str(culture_home_with_boss))
    assert os.path.exists(spool_path), f"spool DB missing at {spool_path}"
    import sqlite3 as _s

    conn = _s.connect(spool_path)
    rows = conn.execute(
        "SELECT sender, recipient, payload FROM dm_spool WHERE recipient = ?",
        (_BOSS_NICK,),
    ).fetchall()
    conn.close()
    assert len(rows) == 1, rows
    assert rows[0][0] == _PEER_NICK
    assert rows[0][1] == _BOSS_NICK
    assert rows[0][2] == "hello while offline"


@pytest.mark.asyncio
async def test_bridge_drains_spool_on_connect(culture_home_with_boss, server) -> None:
    """NT-6: a DM spooled before the bridge connects surfaces as an
    inbound_dm IPC whisper after the bridge welcomes.
    """
    # 1. Spool a DM while no bridge is connected.
    await _send_dm_as_peer(server, _PEER_NICK, _BOSS_NICK, "delivered-on-reconnect")
    await asyncio.sleep(0.2)
    # 2. Start the bridge.
    config = DaemonConfig(server=ServerConnConfig(host="127.0.0.1", port=server.config.port))
    agent = AgentConfig(nick=_BOSS_NICK, directory="/tmp/boss", channels=[], tags=["boss"])
    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)
    await daemon.start()
    try:
        # Allow welcome + CHATHISTORY round trip.
        await asyncio.sleep(0.6)
        # 3. Connect a CC-style IPC client; pull the whisper queue via
        # the socket. The bridge pushes whispers as ``{"type":"whisper",
        # "whisper_type":"inbound_dm", "message": "<json>"}`` frames.
        sock_path = os.path.join(sock_dir, f"culture-{_BOSS_NICK}.sock")
        reader, writer = await asyncio.open_unix_connection(sock_path)
        # On connect, the bridge's SocketServer drains its queued
        # whispers BEFORE processing any inbound request — so the
        # spooled DM (queued during bridge start) arrives first on
        # the wire. Read frames until we see the inbound_dm or
        # timeout.
        found_dm = None
        try:
            async with asyncio.timeout(3.0):
                while found_dm is None:
                    line = await reader.readline()
                    if not line:
                        break
                    try:
                        frame = decode_message(line)
                    except (ValueError, json.JSONDecodeError):
                        continue
                    if not isinstance(frame, dict):
                        continue
                    if frame.get("type") == "whisper" and frame.get("whisper_type") == "inbound_dm":
                        body = json.loads(frame.get("message", "{}"))
                        if body.get("text") == "delivered-on-reconnect":
                            found_dm = body
        except (asyncio.TimeoutError, TimeoutError):
            pass
        writer.close()
        try:
            await writer.wait_closed()
        except ConnectionError:
            pass
        assert found_dm is not None, "bridge did not push spooled DM via IPC"
        assert found_dm.get("sender") == _PEER_NICK
        assert found_dm.get("msg_id"), "inbound_dm payload must carry msg_id for ack"
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_dm_to_unknown_nick_still_yields_err_nosuchnick(
    culture_home_with_boss, server
) -> None:
    """Regression guard: only spool-eligible (boss) nicks bypass
    ERR_NOSUCHNICK. A DM to a random unknown nick still bounces."""
    reader, writer = await asyncio.open_connection("127.0.0.1", server.config.port)

    async def _send(line: str) -> None:
        writer.write(f"{line}\r\n".encode())
        await writer.drain()

    await _send(f"NICK {_PEER_NICK}")
    await _send(f"USER {_PEER_NICK} 0 * :{_PEER_NICK}")
    # Drain welcome.
    try:
        async with asyncio.timeout(1.0):
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
    await _send("PRIVMSG testserv-randoshmando :ping")
    saw_401 = False
    try:
        async with asyncio.timeout(1.0):
            while True:
                chunk = await reader.read(1024)
                if not chunk:
                    break
                if b" 401 " in chunk:
                    saw_401 = True
                    break
    except (asyncio.TimeoutError, TimeoutError):
        pass
    writer.close()
    try:
        await writer.wait_closed()
    except ConnectionError:
        pass
    assert saw_401, "DM to non-spool-eligible nick must return ERR_NOSUCHNICK"
