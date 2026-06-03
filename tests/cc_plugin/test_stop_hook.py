"""Tests for the Stop hook (Phase 4.5).

The Stop hook implements the end-of-turn queue drain via
``decision: "block"``. Two critical invariants:

    1. Idempotency under ``stop_hook_active==true`` (no decision —
       otherwise CC infinite-loops).
    2. When the queue is non-empty, return ``decision: "block"`` with
       the queued events formatted as a system reminder.
"""

from __future__ import annotations

import json
import sys

import pytest

from culture.clients.claude.cc_plugin.hooks import stop as hook


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


class TestIdempotency:
    def test_stop_hook_active_true_returns_no_decision(self, env, monkeypatch, capsys):
        """When CC is already in a Stop-block continuation, the hook
        MUST exit 0 with no decision so we don't infinite-loop."""
        # Even if the queue is non-empty, the idempotency check wins.
        called = []

        def stub_drain(nick):
            called.append(nick)
            return [{"kind": "inbound_dm", "sender": "x", "text": "hi"}]

        monkeypatch.setattr(hook, "_drain_queue", stub_drain)
        monkeypatch.setattr(
            sys, "stdin", _StringIO(json.dumps({"stop_hook_active": True, "cwd": str(env)}))
        )
        rc = hook.main()
        assert rc == 0
        captured = capsys.readouterr().out.strip()
        # No decision block; the drain wasn't even attempted.
        assert captured == ""
        assert called == []


class TestQueueDrain:
    def test_non_empty_queue_returns_block(self, env, monkeypatch, capsys):
        entries = [
            {"kind": "inbound_dm", "sender": "peer", "target": "fork-rearch", "text": "hello"}
        ]
        monkeypatch.setattr(hook, "_drain_queue", lambda nick: entries)
        monkeypatch.setattr(
            sys, "stdin", _StringIO(json.dumps({"stop_hook_active": False, "cwd": str(env)}))
        )
        rc = hook.main()
        assert rc == 0
        out = json.loads(capsys.readouterr().out.strip())
        assert out["decision"] == "block"
        assert "hello" in out["reason"]
        assert "peer" in out["reason"]
        assert out["reason"].startswith("<system-reminder>")

    def test_empty_queue_returns_no_decision(self, env, monkeypatch, capsys):
        monkeypatch.setattr(hook, "_drain_queue", lambda nick: [])
        monkeypatch.setattr(sys, "stdin", _StringIO(json.dumps({"cwd": str(env)})))
        rc = hook.main()
        assert rc == 0
        assert capsys.readouterr().out.strip() == ""

    def test_missing_stop_hook_active_field_treated_as_false(self, env, monkeypatch, capsys):
        # If CC sends a payload without the field, default to the active path.
        monkeypatch.setattr(
            hook, "_drain_queue", lambda nick: [{"kind": "inbound_dm", "sender": "p", "text": "y"}]
        )
        monkeypatch.setattr(sys, "stdin", _StringIO(json.dumps({"cwd": str(env)})))
        rc = hook.main()
        assert rc == 0
        out = json.loads(capsys.readouterr().out.strip())
        assert out["decision"] == "block"


class TestFormatReason:
    def test_format_contains_kind_sender_text(self):
        entries = [
            {"kind": "inbound_mention", "sender": "x", "target": "#y", "text": "boo"},
        ]
        out = hook._format_reason(entries)
        assert "inbound_mention" in out
        assert "x" in out
        assert "boo" in out

    def test_format_truncates_large_lists(self):
        entries = [
            {"kind": "inbound_dm", "sender": "p", "target": "x", "text": f"m{i}"} for i in range(55)
        ]
        out = hook._format_reason(entries)
        assert "+5 more" in out
