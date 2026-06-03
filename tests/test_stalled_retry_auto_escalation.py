"""Tests for the ``stalled_in_retry_loop`` auto-escalation enrichment.

Phase 6.2 of the rearchitecture plan closes plenty's P1: when the
``stalled_in_retry_loop`` watchdog class fires, the boss DM should
name the failing tool + input + last exception text, instead of just
the bare stall message. The worker reads its OWN audit log
(``audit_path_for(<nick>)``), walks the most recent assistant turn
backwards, and surfaces the tool_use + tool_result context.
"""

from __future__ import annotations

import json
import os

from tests._sdk_stub import install_claude_sdk_stub

install_claude_sdk_stub()

from culture.clients.claude.daemon import AgentDaemon  # noqa: E402


def _make_daemon(nick="local-w"):
    """Build a minimal AgentDaemon — only ``_stall_message`` is exercised."""
    from culture.clients.claude.config import (
        AgentConfig,
        DaemonConfig,
        ServerConnConfig,
    )

    server_cfg = ServerConnConfig(host="127.0.0.1", port=6667)
    daemon_cfg = DaemonConfig(server=server_cfg)
    agent_cfg = AgentConfig(nick=nick, agent="claude", boss="local-boss")
    return AgentDaemon(daemon_cfg, agent_cfg, skip_claude=True)


def _write_audit_assistant(home, nick, tool_uses, tool_results):
    """Write one assistant-message line into ``audit/<nick>.jsonl``."""
    audit_dir = os.path.join(str(home), "audit")
    os.makedirs(audit_dir, exist_ok=True)
    path = os.path.join(audit_dir, f"{nick}.jsonl")
    record = {
        "ts": "2026-06-03T20:00:00.000Z",
        "nick": nick,
        "type": "assistant",
        "model": "claude-opus-4-8",
        "text": "",
        "thinking": "",
        "tool_uses": tool_uses,
        "tool_results": tool_results,
    }
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


class TestStalledInRetryLoopEnrichment:
    """Phase 6.2 — the boss DM names the failing tool + input + error."""

    def test_dm_surfaces_tool_name_input_and_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        daemon = _make_daemon(nick="local-w")
        _write_audit_assistant(
            tmp_path,
            "local-w",
            tool_uses=[
                {
                    "name": "Write",
                    "input": '{"file_path": "/tmp/output.txt", "content": "hello"}',
                    "input_digest": "sha256:deadbeef",
                }
            ],
            tool_results=[
                {
                    "name": "Write",
                    "content": "Stream closed: SDK CLI subprocess died mid-write",
                    "content_digest": "sha256:cafef00d",
                    "preview": "Stream closed: SDK CLI subprocess died mid-write",
                }
            ],
        )
        msg = daemon._stall_message("stalled_in_retry_loop", since=120)
        # Bare stall text still present.
        assert "AssistantMessages" in msg
        # Enrichment present.
        assert "[failing tool context]" in msg
        assert "tool: Write" in msg
        assert "/tmp/output.txt" in msg
        assert "Stream closed" in msg

    def test_dm_without_audit_falls_back_to_bare_message(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        daemon = _make_daemon(nick="local-no-audit")
        msg = daemon._stall_message("stalled_in_retry_loop", since=60)
        assert "[failing tool context]" not in msg
        assert "AssistantMessages" in msg

    def test_truncates_long_fields_at_500_chars(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        daemon = _make_daemon(nick="local-w")
        long_input = "x" * 2000
        long_error = "y" * 2000
        _write_audit_assistant(
            tmp_path,
            "local-w",
            tool_uses=[{"name": "Bash", "input": long_input}],
            tool_results=[{"name": "Bash", "content": long_error, "preview": long_error[:200]}],
        )
        msg = daemon._stall_message("stalled_in_retry_loop", since=180)
        # Each field is truncated at 500 chars + truncation tag.
        assert "truncated" in msg
        # The DM stays sub-3-KiB (well under typical IRC line budgets even
        # with both fields capped at 500 + headers/labels).
        assert len(msg) < 3000

    def test_walks_back_past_empty_assistant_messages(self, tmp_path, monkeypatch):
        # An assistant turn with text-only blocks (no tool calls) should
        # not prevent the helper from finding the most-recent tool call
        # in an earlier turn.
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        daemon = _make_daemon(nick="local-w")
        _write_audit_assistant(
            tmp_path,
            "local-w",
            tool_uses=[
                {"name": "Edit", "input": '{"file_path": "/a.py"}'},
            ],
            tool_results=[
                {"name": "Edit", "content": "EACCES: permission denied", "preview": "EACCES"},
            ],
        )
        # Later turn has no tools — pure text/thinking output.
        _write_audit_assistant(tmp_path, "local-w", tool_uses=[], tool_results=[])
        msg = daemon._stall_message("stalled_in_retry_loop", since=60)
        assert "tool: Edit" in msg
        assert "EACCES" in msg

    def test_non_assistant_lines_ignored(self, tmp_path, monkeypatch):
        # Audit JSONL may contain non-assistant records in the future;
        # the helper must ignore them.
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        daemon = _make_daemon(nick="local-w")
        audit_dir = os.path.join(str(tmp_path), "audit")
        os.makedirs(audit_dir, exist_ok=True)
        path = os.path.join(audit_dir, "local-w.jsonl")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": "x", "type": "system", "event": "noop"}) + "\n")
            fh.write(
                json.dumps(
                    {
                        "ts": "x",
                        "type": "assistant",
                        "tool_uses": [{"name": "Read", "input": '{"file_path": "/x"}'}],
                        "tool_results": [
                            {"name": "Read", "content": "NotFound: /x", "preview": "NotFound"}
                        ],
                    }
                )
                + "\n"
            )
        msg = daemon._stall_message("stalled_in_retry_loop", since=30)
        assert "tool: Read" in msg
        assert "NotFound" in msg

    def test_other_stall_classes_unenriched(self, tmp_path, monkeypatch):
        """Only ``stalled_in_retry_loop`` gets enrichment — the other
        classes keep their original messages so we don't churn output
        across the dashboard."""
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        daemon = _make_daemon(nick="local-w")
        # Even if audit data is present, the never_briefed / pre /
        # post / failed_retry classes don't pull context.
        _write_audit_assistant(
            tmp_path,
            "local-w",
            tool_uses=[{"name": "Bash", "input": "ls"}],
            tool_results=[{"name": "Bash", "content": "hung", "preview": "hung"}],
        )
        for cls in (
            "never_briefed",
            "stalled_pre_engagement",
            "stalled_post_engagement",
            "stalled_in_failed_retry",
        ):
            msg = daemon._stall_message(cls, since=60)
            assert "[failing tool context]" not in msg
