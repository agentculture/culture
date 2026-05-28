"""Tests for the per-agent daemon-action log."""

from __future__ import annotations

from tests._sdk_stub import install_claude_sdk_stub

install_claude_sdk_stub()

import asyncio  # noqa: E402
import json  # noqa: E402

import pytest  # noqa: E402

from culture.clients._daemon_log import DaemonLog, daemon_log_path_for  # noqa: E402


@pytest.fixture
def culture_root(tmp_path, monkeypatch):
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    return tmp_path


class TestDaemonLog:
    def test_path_under_culture_home(self, culture_root):
        assert daemon_log_path_for("local-foo").startswith(str(culture_root))

    @pytest.mark.asyncio
    async def test_record_appends_line(self, culture_root):
        log = DaemonLog(nick="local-foo")
        await log.record("agent_start", model="claude-opus-4-7", directory="/tmp/x")
        with open(log.path) as f:
            lines = f.readlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["action"] == "agent_start"
        assert rec["nick"] == "local-foo"
        assert rec["detail"]["model"] == "claude-opus-4-7"
        assert rec["ts"].endswith("Z")

    @pytest.mark.asyncio
    async def test_detail_roundtrip(self, culture_root):
        log = DaemonLog(nick="local-foo")
        await log.record("compact", trigger="context_watermark", pct=0.91)
        with open(log.path) as f:
            rec = json.loads(f.readlines()[0])
        assert rec["detail"] == {"trigger": "context_watermark", "pct": 0.91}

    @pytest.mark.asyncio
    async def test_multiple_records_ordered(self, culture_root):
        log = DaemonLog(nick="local-foo")
        for action in ("agent_start", "compact", "agent_stop"):
            await log.record(action)
        with open(log.path) as f:
            recs = [json.loads(line) for line in f.readlines()]
        assert [r["action"] for r in recs] == ["agent_start", "compact", "agent_stop"]

    @pytest.mark.asyncio
    async def test_concurrent_records_serialize(self, culture_root):
        log = DaemonLog(nick="local-foo")
        await asyncio.gather(*(log.record("tick", i=i) for i in range(20)))
        with open(log.path) as f:
            lines = f.readlines()
        assert len(lines) == 20
        for line in lines:
            json.loads(line)

    @pytest.mark.asyncio
    async def test_empty_nick_rejected(self, culture_root):
        with pytest.raises(ValueError):
            DaemonLog(nick="")
