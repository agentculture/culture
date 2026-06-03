"""NT-7 — DM spool durability across IRCd restart.

A DM spooled by one IRCd instance must be visible to a freshly-started
IRCd (or to any reader of the on-disk SQLite). Mirrors the durability
contract of ``HistoryStore``.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from culture.agentirc import client as ircd_client_mod
from culture.agentirc.config import ServerConfig, TelemetryConfig
from culture.agentirc.dm_spool_store import default_spool_path
from culture.agentirc.ircd import IRCd

_BOSS_NICK = "testserv-boss"


@pytest.mark.asyncio
async def test_spool_survives_ircd_restart(tmp_path, monkeypatch) -> None:
    """Insert via one IRCd; stop it; open another IRCd → row still present."""
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

    # First IRCd instance — insert one spool row.
    config_a = ServerConfig(
        name="testserv",
        host="127.0.0.1",
        port=0,
        webhook_port=0,
        telemetry=TelemetryConfig(audit_dir=str(tmp_path / "audit_a")),
    )
    ircd_a = IRCd(config_a)
    await ircd_a.start()
    try:
        assert ircd_a.dm_spool is not None
        ircd_a.dm_spool.insert(
            msg_id="durable-1",
            sender="testserv-peer",
            recipient=_BOSS_NICK,
            ts=1700000000.0,
            payload="must survive",
            tags="msgid=durable-1",
        )
    finally:
        await ircd_a.stop()

    # Verify file exists on disk under the expected path.
    spool_path = default_spool_path("testserv", str(tmp_path))
    assert os.path.exists(spool_path)

    # Second IRCd instance — same DB. Row should still be present.
    config_b = ServerConfig(
        name="testserv",
        host="127.0.0.1",
        port=0,
        webhook_port=0,
        telemetry=TelemetryConfig(audit_dir=str(tmp_path / "audit_b")),
    )
    ircd_b = IRCd(config_b)
    await ircd_b.start()
    try:
        assert ircd_b.dm_spool is not None
        entries = ircd_b.dm_spool.query_for_nick(_BOSS_NICK)
        assert len(entries) == 1
        assert entries[0]["msg_id"] == "durable-1"
        assert entries[0]["payload"] == "must survive"
    finally:
        await ircd_b.stop()


@pytest.mark.asyncio
async def test_spool_db_has_required_indexes(tmp_path, monkeypatch) -> None:
    """The schema must create the two named indexes (recipient_ts + gc)."""
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    config = ServerConfig(
        name="testserv",
        host="127.0.0.1",
        port=0,
        webhook_port=0,
        telemetry=TelemetryConfig(audit_dir=str(tmp_path / "audit")),
    )
    ircd = IRCd(config)
    await ircd.start()
    try:
        spool_path = default_spool_path("testserv", str(tmp_path))
        conn = sqlite3.connect(spool_path)
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
        names = {row[0] for row in cur.fetchall()}
        conn.close()
        assert "idx_dm_spool_recipient_ts" in names
        assert "idx_dm_spool_gc" in names
    finally:
        await ircd.stop()
