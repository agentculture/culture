"""Tests for the PreToolUse hook's recursion-avoidance pattern (Phase 4.7).

The CRITICAL invariant: ``mesh ...`` tool calls MUST pass through the
hook without consulting the perm queue, otherwise approving a perm
request would re-fire the same hook and infinite-loop.
"""

from __future__ import annotations

import json
import sys

import pytest

from culture.clients.claude.cc_plugin.hooks import pre_tool_use as hook


class _StringIO:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CULTURE_NICK", "fork-rearch")
    return tmp_path


class TestIsMeshTool:
    @pytest.mark.parametrize(
        "tool",
        [
            "mesh approve",
            "mesh deny",
            "mesh send",
            "mesh dm",
            "mesh inbox",
            "mesh status",
            "mesh grant",
            "mesh whatever-new",  # any "mesh "-prefixed verb
        ],
    )
    def test_mesh_tools_pass_through(self, tool):
        assert hook._is_mesh_tool(tool) is True

    @pytest.mark.parametrize(
        "tool",
        [
            "Bash",
            "Edit",
            "Write",
            "Read",
            "meshapp",  # not a mesh verb — no trailing space
            "",
        ],
    )
    def test_non_mesh_tools_do_not_pass_through(self, tool):
        assert hook._is_mesh_tool(tool) is False


class TestMainRecursionGuard:
    def test_mesh_approve_passes_through_with_queue_pending(self, env, monkeypatch, capsys):
        """When CC calls ``mesh approve`` to action the queued request,
        the PreToolUse hook MUST NOT block. Otherwise infinite recursion."""
        # Even though the queue has a perm request, the recursion guard wins.
        drained = []

        def stub_drain(nick):
            drained.append(nick)
            return [{"id": "r1", "helper_nick": "w", "tool_name": "Bash"}]

        monkeypatch.setattr(hook, "_drain_perm_requests", stub_drain)
        monkeypatch.setattr(
            sys, "stdin", _StringIO(json.dumps({"tool_name": "mesh approve", "cwd": str(env)}))
        )
        rc = hook.main()
        assert rc == 0
        # Nothing on stdout means no decision; the drain was NOT consulted.
        assert capsys.readouterr().out.strip() == ""
        assert drained == []

    def test_non_mesh_tool_with_queue_pending_blocks(self, env, monkeypatch, capsys):
        entries = [
            {"id": "r1", "helper_nick": "w", "tool_name": "Bash", "input": {"command": "ls"}}
        ]
        monkeypatch.setattr(hook, "_drain_perm_requests", lambda nick: entries)
        monkeypatch.setattr(
            sys, "stdin", _StringIO(json.dumps({"tool_name": "Bash", "cwd": str(env)}))
        )
        rc = hook.main()
        assert rc == 0
        out = json.loads(capsys.readouterr().out.strip())
        assert out["decision"] == "block"
        assert "r1" in out["reason"]
        assert "w" in out["reason"]

    def test_non_mesh_tool_with_empty_queue_passes(self, env, monkeypatch, capsys):
        monkeypatch.setattr(hook, "_drain_perm_requests", lambda nick: [])
        monkeypatch.setattr(
            sys, "stdin", _StringIO(json.dumps({"tool_name": "Bash", "cwd": str(env)}))
        )
        rc = hook.main()
        assert rc == 0
        assert capsys.readouterr().out.strip() == ""


class TestFormatReason:
    def test_reason_lists_each_pending_request(self):
        entries = [
            {"id": "r1", "helper_nick": "w1", "tool_name": "Bash", "input": {"command": "ls"}},
            {"id": "r2", "helper_nick": "w2", "tool_name": "Edit", "input": {"file": "x"}},
        ]
        out = hook._format_reason(entries)
        assert "r1" in out
        assert "r2" in out
        assert "w1" in out
        assert "Bash" in out
        assert "Edit" in out
        assert "mesh approve" in out
        assert "mesh deny" in out
