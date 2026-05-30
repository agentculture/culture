"""Tests for the culture agent compact <nick> <reason> CLI command (v8.19.5).

Per docs/task-model.md — the explicit task-switch pattern. The
orchestrator triggers a compact with a reason; the daemon fires
pre/post-compact prompts so the agent captures the transition.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests._sdk_stub import install_claude_sdk_stub

install_claude_sdk_stub()

import asyncio  # noqa: E402

# Import the CLI module under test.
from culture.cli import agent as agent_cli  # noqa: E402


class TestCompactCli:
    def test_compact_refuses_when_not_running(self, monkeypatch, capsys):
        """If the daemon isn't running, the command must hard-fail."""
        # Force "not running" by stubbing read_pid -> None.
        monkeypatch.setattr(agent_cli, "read_pid", lambda *_a, **_kw: None)

        class _Args:
            nick = "local-agent"
            reason = "switching to new task"
            config = "/tmp/never-exists.yaml"

        with pytest.raises(SystemExit) as exc:
            agent_cli._cmd_compact(_Args())
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "not running" in err

    def test_compact_sends_ipc_with_reason(self, monkeypatch):
        """Happy path: send compact IPC with the reason."""
        monkeypatch.setattr(agent_cli, "read_pid", lambda *_a, **_kw: 12345)
        monkeypatch.setattr(agent_cli, "is_process_alive", lambda _pid: True)

        sent = {}

        async def _fake_ipc(sock, action, **payload):
            sent["sock"] = sock
            sent["action"] = action
            sent.update(payload)
            return {"ok": True}

        monkeypatch.setattr(agent_cli, "ipc_request", _fake_ipc)

        class _Args:
            nick = "local-agent"
            reason = "switching to new task"
            config = "/tmp/never-exists.yaml"

        agent_cli._cmd_compact(_Args())
        assert sent.get("action") == "compact"
        assert sent.get("reason") == "switching to new task"
        assert "local-agent" in sent.get("sock", "")

    def test_compact_exits_on_ipc_failure(self, monkeypatch, capsys):
        """When the daemon returns ok=False, the CLI hard-fails with the error."""
        monkeypatch.setattr(agent_cli, "read_pid", lambda *_a, **_kw: 12345)
        monkeypatch.setattr(agent_cli, "is_process_alive", lambda _pid: True)

        async def _fail_ipc(_sock, _action, **_payload):
            return {"ok": False, "error": "agent runner gone"}

        monkeypatch.setattr(agent_cli, "ipc_request", _fail_ipc)

        class _Args:
            nick = "local-agent"
            reason = "x"
            config = "/tmp/never-exists.yaml"

        with pytest.raises(SystemExit) as exc:
            agent_cli._cmd_compact(_Args())
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "agent runner gone" in err
