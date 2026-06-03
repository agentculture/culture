"""Tests for crash-resilient worker session checkpointing.

Phase 6.4 of the rearchitecture plan (partial fix for plenty's P0b).
The SDK CLI ``Stream closed`` bug crashes workers mid-task; without
session resume, the worker restarts from scratch and the operator
loses minutes of work. The checkpoint at ``~/.culture/sessions/<nick>.json``
records the last clean turn's ``session_id`` so a worker daemon
restart resumes the prior SDK conversation via
``ClaudeAgentOptions.resume = sid`` instead of starting fresh.

Caveat per the plan: this is "partial fix" — the upstream Stream-closed
bug remains. Checkpointing means crash recovery resumes from last clean
turn rather than fresh.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any

import pytest

from culture.clients.claude.agent_runner import AgentRunner


@dataclass
class FakeTextBlock:
    text: str
    type: str = "text"


@dataclass
class FakeAssistantMessage:
    content: list = field(default_factory=list)
    model: str = "fake-model"
    parent_tool_use_id: str | None = None
    error: str | None = None
    usage: dict[str, Any] | None = None


@dataclass
class FakeResultMessage:
    session_id: str = "sess-test-123"
    subtype: str = "result"
    duration_ms: int = 100
    duration_api_ms: int = 80
    is_error: bool = False
    num_turns: int = 1
    stop_reason: str | None = "end_turn"
    total_cost_usd: float | None = 0.01
    usage: dict[str, Any] | None = field(
        default_factory=lambda: {"input_tokens": 100, "output_tokens": 50}
    )
    result: str | None = None
    structured_output: Any = None


def _checkpoint_path(home, nick):
    return os.path.join(str(home), "sessions", f"{nick}.json")


class TestCheckpointWrite:
    """A clean turn completion writes the session checkpoint to disk."""

    @pytest.mark.asyncio
    async def test_turn_completion_writes_checkpoint(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))

        async def fake_query(*, prompt, options=None, transport=None):
            yield FakeResultMessage(session_id="sess-clean-001")
            # Natural end-of-iteration → on_turn_complete fires.
            return

        monkeypatch.setattr("culture.clients.claude.agent_runner.query", fake_query)
        monkeypatch.setattr("culture.clients.claude.agent_runner.ResultMessage", FakeResultMessage)
        monkeypatch.setattr(
            "culture.clients.claude.agent_runner.AssistantMessage", FakeAssistantMessage
        )

        runner = AgentRunner(model="test-model", directory="/tmp", nick="local-w")
        runner._prompt_queue.put_nowait("go")
        await runner.start()
        await asyncio.sleep(0.3)
        await runner.stop()

        path = _checkpoint_path(tmp_path, "local-w")
        assert os.path.exists(path), "checkpoint file should exist after a clean turn"
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        assert payload["last_session_id"] == "sess-clean-001"
        assert payload["nick"] == "local-w"
        assert payload["schema"] == 1
        assert isinstance(payload.get("last_turn_completed_at"), (int, float))
        assert payload["total_tokens"]["input"] == 100
        assert payload["total_tokens"]["output"] == 50

    @pytest.mark.asyncio
    async def test_no_checkpoint_when_nick_empty(self, tmp_path, monkeypatch):
        """A runner without a nick has no stable identity → no checkpoint."""
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))

        async def fake_query(*, prompt, options=None, transport=None):
            yield FakeResultMessage(session_id="sess-anon")

        monkeypatch.setattr("culture.clients.claude.agent_runner.query", fake_query)
        monkeypatch.setattr("culture.clients.claude.agent_runner.ResultMessage", FakeResultMessage)
        monkeypatch.setattr(
            "culture.clients.claude.agent_runner.AssistantMessage", FakeAssistantMessage
        )

        runner = AgentRunner(model="test-model", directory="/tmp")  # no nick
        runner._prompt_queue.put_nowait("go")
        await runner.start()
        await asyncio.sleep(0.3)
        await runner.stop()
        # sessions dir may exist but the file must not.
        sessions_dir = os.path.join(str(tmp_path), "sessions")
        if os.path.exists(sessions_dir):
            assert os.listdir(sessions_dir) == []

    @pytest.mark.asyncio
    async def test_checkpoint_file_chmod_600(self, tmp_path, monkeypatch):
        """The on-disk checkpoint contains the session_id — restrict to owner-only."""
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))

        async def fake_query(*, prompt, options=None, transport=None):
            yield FakeResultMessage(session_id="sess-private")

        monkeypatch.setattr("culture.clients.claude.agent_runner.query", fake_query)
        monkeypatch.setattr("culture.clients.claude.agent_runner.ResultMessage", FakeResultMessage)
        monkeypatch.setattr(
            "culture.clients.claude.agent_runner.AssistantMessage", FakeAssistantMessage
        )

        runner = AgentRunner(model="test-model", directory="/tmp", nick="local-w")
        runner._prompt_queue.put_nowait("go")
        await runner.start()
        await asyncio.sleep(0.3)
        await runner.stop()
        path = _checkpoint_path(tmp_path, "local-w")
        mode = os.stat(path).st_mode & 0o777
        assert mode == 0o600


class TestCheckpointResume:
    """A worker restart with a present checkpoint resumes via
    ClaudeAgentOptions.resume = last_session_id."""

    @pytest.mark.asyncio
    async def test_load_checkpoint_populates_session_id(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        # Hand-write a prior checkpoint.
        sessions_dir = os.path.join(str(tmp_path), "sessions")
        os.makedirs(sessions_dir, exist_ok=True)
        with open(os.path.join(sessions_dir, "local-w.json"), "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "schema": 1,
                    "nick": "local-w",
                    "last_session_id": "sess-resumed-xyz",
                    "last_turn_completed_at": 1234567890.0,
                    "cursor": 0,
                    "total_tokens": {"input": 200, "output": 100},
                },
                fh,
            )
        runner = AgentRunner(model="test-model", directory="/tmp", nick="local-w")
        assert runner.session_id is None
        runner._load_checkpoint()
        assert runner.session_id == "sess-resumed-xyz"
        assert runner._total_input_tokens == 200
        assert runner._total_output_tokens == 100

    @pytest.mark.asyncio
    async def test_start_loads_checkpoint_before_first_turn(self, tmp_path, monkeypatch):
        """``start()`` calls ``_load_checkpoint`` so the SDK's first
        ``_make_options`` sees the restored session_id and sets
        ``opts.resume = sid``."""
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        # Pre-seed checkpoint.
        sessions_dir = os.path.join(str(tmp_path), "sessions")
        os.makedirs(sessions_dir, exist_ok=True)
        with open(os.path.join(sessions_dir, "local-w.json"), "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "schema": 1,
                    "nick": "local-w",
                    "last_session_id": "sess-pre-crash",
                    "last_turn_completed_at": 1234567890.0,
                    "cursor": 0,
                    "total_tokens": {"input": 50, "output": 25},
                },
                fh,
            )
        captured: dict[str, Any] = {}

        async def fake_query(*, prompt, options=None, transport=None):
            captured["options"] = options
            yield FakeResultMessage(session_id="sess-new-after-resume")

        monkeypatch.setattr("culture.clients.claude.agent_runner.query", fake_query)
        monkeypatch.setattr("culture.clients.claude.agent_runner.ResultMessage", FakeResultMessage)
        monkeypatch.setattr(
            "culture.clients.claude.agent_runner.AssistantMessage", FakeAssistantMessage
        )

        runner = AgentRunner(model="test-model", directory="/tmp", nick="local-w")
        runner._prompt_queue.put_nowait("continue")
        await runner.start()
        await asyncio.sleep(0.3)
        await runner.stop()
        # The first query call's options must carry resume=<prior>.
        opts = captured.get("options")
        assert opts is not None
        assert getattr(opts, "resume", None) == "sess-pre-crash"

    @pytest.mark.asyncio
    async def test_load_checkpoint_silent_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        runner = AgentRunner(model="test-model", directory="/tmp", nick="brand-new")
        # No checkpoint file exists; load should be a no-op.
        runner._load_checkpoint()
        assert runner.session_id is None

    @pytest.mark.asyncio
    async def test_load_checkpoint_tolerates_malformed_json(self, tmp_path, monkeypatch):
        """A corrupt checkpoint must not crash the runner — start fresh."""
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        sessions_dir = os.path.join(str(tmp_path), "sessions")
        os.makedirs(sessions_dir, exist_ok=True)
        with open(os.path.join(sessions_dir, "local-w.json"), "w", encoding="utf-8") as fh:
            fh.write("{ not valid json")
        runner = AgentRunner(model="test-model", directory="/tmp", nick="local-w")
        runner._load_checkpoint()
        assert runner.session_id is None
