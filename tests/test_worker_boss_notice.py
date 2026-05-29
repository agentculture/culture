"""Tests for the worker daemon's boss permission-notice DM (boss-agent layer)."""

from __future__ import annotations

from tests._sdk_stub import install_claude_sdk_stub

install_claude_sdk_stub()

import json  # noqa: E402
import os  # noqa: E402
from unittest.mock import AsyncMock  # noqa: E402

import pytest  # noqa: E402

import culture.clients.claude.daemon as daemon_mod  # noqa: E402
from culture.clients.claude.config import AgentConfig, DaemonConfig  # noqa: E402
from culture.clients.claude.daemon import AgentDaemon  # noqa: E402


def _daemon(boss: str) -> AgentDaemon:
    config = DaemonConfig()
    agent = AgentConfig(
        nick="local-worker", directory="/tmp", channels=["#team", "#task-worker"], boss=boss
    )
    return AgentDaemon(config, agent, socket_dir="/tmp", skip_claude=True)


class TestOnPermRequest:
    @pytest.mark.asyncio
    async def test_dms_boss_when_configured(self):
        d = _daemon(boss="local-boss")
        d._transport = AsyncMock()
        await d._on_perm_request(
            {"id": "req-1", "tool_name": "Edit", "input": {"file_path": "/etc/hosts"}}
        )
        d._transport.send_privmsg.assert_awaited_once()
        target, text = d._transport.send_privmsg.await_args.args
        assert target == "local-boss"
        assert "req-1" in text
        assert "Edit" in text
        assert "/etc/hosts" in text

    @pytest.mark.asyncio
    async def test_no_boss_sends_nothing(self):
        d = _daemon(boss="")
        d._transport = AsyncMock()
        await d._on_perm_request({"id": "req-2", "tool_name": "Bash", "input": {"command": "ls"}})
        d._transport.send_privmsg.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_transport_sends_nothing(self):
        d = _daemon(boss="local-boss")
        d._transport = None
        # Must not raise even though there's a boss but no transport yet.
        await d._on_perm_request({"id": "req-3", "tool_name": "Bash", "input": {"command": "ls"}})


class TestIdleWatchdog:
    @pytest.mark.asyncio
    async def test_dms_boss_when_never_engaged(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        monkeypatch.setattr(daemon_mod, "IDLE_GRACE_SECONDS", 0)
        d = _daemon(boss="local-boss")
        d._transport = AsyncMock()
        d._agent_runner = AsyncMock()  # simulate a running runner
        d._engaged = False
        await d._idle_watchdog()
        d._transport.send_privmsg.assert_awaited_once()
        target, text = d._transport.send_privmsg.await_args.args
        assert target == "local-boss"
        assert "idle" in text.lower() and "local-worker" in text

    @pytest.mark.asyncio
    async def test_no_dm_when_engaged(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        monkeypatch.setattr(daemon_mod, "IDLE_GRACE_SECONDS", 0)
        d = _daemon(boss="local-boss")
        d._transport = AsyncMock()
        d._agent_runner = AsyncMock()
        d._engaged = True  # produced a turn → not idle
        await d._idle_watchdog()
        d._transport.send_privmsg.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_dm_when_activated_but_slow_first_turn(self, tmp_path, monkeypatch):
        # A worker that WAS triggered/briefed but hasn't finished a slow first turn
        # (extended thinking / long first tool call) must NOT be flagged idle.
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        monkeypatch.setattr(daemon_mod, "IDLE_GRACE_SECONDS", 0)
        d = _daemon(boss="local-boss")
        d._transport = AsyncMock()
        d._agent_runner = AsyncMock()
        d._engaged = False
        d._last_activation = 12345.0  # was activated (mentioned/briefed), still working
        await d._idle_watchdog()
        d._transport.send_privmsg.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_dm_when_paused(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        monkeypatch.setattr(daemon_mod, "IDLE_GRACE_SECONDS", 0)
        d = _daemon(boss="local-boss")
        d._transport = AsyncMock()
        d._agent_runner = AsyncMock()
        d._engaged = False
        d._paused = True
        await d._idle_watchdog()
        d._transport.send_privmsg.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_engagement_flag_and_engaged_record_on_first_turn(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        d = _daemon(boss="local-boss")
        d._supervisor = None
        assert d._engaged is False
        await d._on_agent_message({"type": "assistant", "text": "hi", "tool_uses": []})
        await d._on_agent_message({"type": "assistant", "text": "more", "tool_uses": []})
        assert d._engaged is True
        # `engaged` is recorded exactly once (first turn), so the dashboard idle
        # signal clears authoritatively without depending on audit size.
        log_path = os.path.join(str(tmp_path), "daemon-log", "local-worker.jsonl")
        with open(log_path, encoding="utf-8") as f:
            actions = [json.loads(line)["action"] for line in f if line.strip()]
        assert actions.count("engaged") == 1

    @pytest.mark.asyncio
    async def test_poll_dispatch_counts_as_activation(self, tmp_path, monkeypatch):
        # A worker driven by the channel poll (boss posts task context WITHOUT an
        # @mention) must count as activated, so it isn't falsely flagged idle.
        from culture.clients.claude.message_buffer import MessageBuffer

        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        d = _daemon(boss="local-boss")
        d._agent_runner = AsyncMock()
        d._buffer = MessageBuffer()
        d._buffer.add("#task-worker", "local-boss", "here is the task context (no mention)")
        assert d._last_activation is None
        d._send_channel_poll("#task-worker")
        assert d._last_activation is not None


class TestPermInputPreview:
    def test_bash_preview(self):
        assert (
            AgentDaemon._perm_input_preview("Bash", {"command": "git push origin main"})
            == "git push origin main"
        )

    def test_edit_preview(self):
        assert AgentDaemon._perm_input_preview("Write", {"file_path": "/a/b.py"}) == "/a/b.py"

    def test_mcp_preview_is_json(self):
        out = AgentDaemon._perm_input_preview("mcp__gmail__send", {"to": "x@y.z"})
        assert "x@y.z" in out

    def test_preview_truncated_to_80(self):
        long_cmd = "echo " + "a" * 200
        out = AgentDaemon._perm_input_preview("Bash", {"command": long_cmd})
        assert len(out) <= 80
