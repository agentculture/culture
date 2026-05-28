"""Tests for the worker daemon's boss permission-notice DM (boss-agent layer)."""

from __future__ import annotations

from tests._sdk_stub import install_claude_sdk_stub

install_claude_sdk_stub()

from unittest.mock import AsyncMock  # noqa: E402

import pytest  # noqa: E402

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
