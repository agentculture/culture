"""Tests for the SessionStart hook script.

The hook is a standalone Python script designed to be invoked as a
subprocess by Claude Code. We test it via subprocess and via direct
module load.

The bridge IPC is mocked by monkey-patching
``_bridge_client.request`` — the hook doesn't need a live bridge to
verify its output shape.
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock

import pytest

# Import the hook module by path so the same parent-dir handling exercised
# at runtime is also covered.
from culture.clients.claude.cc_plugin import _bridge_client
from culture.clients.claude.cc_plugin.hooks import session_start as hook


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path / ".culture"))
    monkeypatch.delenv("CULTURE_BOSS_NICK", raising=False)
    monkeypatch.delenv("CULTURE_NICK", raising=False)
    return tmp_path


def _make_bridge_responder(spool: list[dict], roster: list[str]):
    """Build a fake ``_bridge_client.request`` that returns canned
    responses keyed by verb."""

    def fake_request(nick, verb, timeout=5.0, **payload):
        if verb == "inbox_drain":
            return {"ok": True, "data": {"entries": spool}}
        if verb == "list_owned_agents":
            return {"ok": True, "data": {"agents": []}}
        if verb == "cc_session_start":
            return {"ok": True, "data": {}}
        if verb == "irc_who":
            return {"ok": True, "data": {"nicks": roster}}
        return {"ok": True, "data": {}}

    return fake_request


class TestFormatAdditionalContext:
    def test_includes_nick_and_roster(self):
        out = hook._format_additional_context("fork-rearch", "", "alice, bob", [])
        assert "fork-rearch" in out
        assert "alice, bob" in out
        assert out.startswith("<system-reminder>")
        assert out.endswith("</system-reminder>")

    def test_lists_spool_entries(self):
        spool = [
            {"kind": "inbound_dm", "sender": "peer", "target": "fork-rearch", "text": "hi"},
            {"kind": "inbound_mention", "sender": "peer", "target": "#x", "text": "yo"},
        ]
        out = hook._format_additional_context("fork-rearch", "", "", spool)
        assert "inbound_dm" in out
        assert "peer" in out
        assert "hi" in out

    def test_truncates_long_spool(self):
        spool = [
            {"kind": "inbound_dm", "sender": "p", "target": "x", "text": f"m{i}"} for i in range(80)
        ]
        out = hook._format_additional_context("x", "", "", spool)
        assert "+30 more" in out


class TestDrainSpool:
    def test_returns_entries(self, monkeypatch):
        monkeypatch.setattr(
            _bridge_client, "request", _make_bridge_responder([{"kind": "inbound_dm"}], [])
        )
        # The hook imports _bridge_client at module load time, so patch its ref too.
        monkeypatch.setattr(hook._bridge_client, "request", _bridge_client.request, raising=False)
        out = hook._drain_spool("fork-rearch")
        assert out == [{"kind": "inbound_dm"}]

    def test_swallows_errors(self, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("bridge down")

        monkeypatch.setattr(hook._bridge_client, "request", boom)
        assert hook._drain_spool("x") == []


class TestReadMission:
    def test_reads_existing_mission(self, isolated_env):
        mission_dir = isolated_env / ".culture" / "mission"
        mission_dir.mkdir(parents=True)
        (mission_dir / "fork-rearch.md").write_text("# Mission\nDo the thing.\n")
        text = hook._read_mission("fork-rearch")
        assert "Do the thing" in text

    def test_returns_empty_when_missing(self, isolated_env):
        assert hook._read_mission("never-seen") == ""

    def test_path_traversal_neutralized(self, isolated_env):
        # ``../`` in the nick must not escape the mission dir. The
        # sanitization in _read_mission rewrites ``..`` to ``_``.
        assert hook._read_mission("../etc/passwd") == ""


class TestEndToEnd:
    def test_main_writes_additional_context(self, isolated_env, monkeypatch, capsys):
        # Stub the bridge IPC fully.
        fake = _make_bridge_responder(
            spool=[{"kind": "inbound_dm", "sender": "peer", "target": "x", "text": "ping"}],
            roster=["peer", "x"],
        )
        monkeypatch.setattr(hook._bridge_client, "request", fake)

        # Force bridge_running to return True so we skip the Popen branch.
        monkeypatch.setattr(hook._bridge_client, "bridge_running", lambda nick: True)

        # Feed stdin with a CC SessionStart payload.
        cwd = isolated_env / "fork-rearch"
        cwd.mkdir()
        payload = json.dumps({"cwd": str(cwd), "session_id": "sid1"})
        monkeypatch.setattr(sys, "stdin", _StringIO(payload))

        rc = hook.main()
        assert rc == 0
        captured = capsys.readouterr()
        out = json.loads(captured.out.strip())
        assert "hookSpecificOutput" in out
        ctx = out["hookSpecificOutput"]["additionalContext"]
        assert "fork-rearch" in ctx
        assert "ping" in ctx
        # CULTURE_NICK has been set in the env for downstream tools.
        assert os.environ["CULTURE_NICK"] == "fork-rearch"


class TestBridgeSpawnHonesty:
    """v9.1.2: when the bridge spawn cannot succeed, the
    additionalContext block MUST say so explicitly — it must NOT
    claim the session is on the mesh. (The original bug shipped a
    fire-and-forget Popen + an optimistic system-reminder; the lie
    cost a debugging round when nick-resolver and bridge-validator
    collided.)"""

    def test_invalid_nick_returns_error_string(self, monkeypatch):
        # bridge_client is present + bridge not running, but nick is
        # missing a hyphen → refuse to spawn.
        monkeypatch.setattr(hook._bridge_client, "bridge_running", lambda nick: False)
        err = hook._ensure_bridge_running("bareNick", "/tmp")
        assert err is not None
        assert "<server>-<agent>" in err
        assert "bareNick" in err

    def test_error_surfaces_in_additional_context(self):
        ctx = hook._format_additional_context(
            nick="bareNick",
            mission="",
            roster="",
            spool=[],
            bridge_error="refusing to spawn bridge: bad nick",
        )
        assert "BRIDGE SPAWN FAILED" in ctx
        # The optimistic "this CC session is X on the mesh" claim MUST
        # NOT appear when the spawn failed. (The negation phrase "is
        # NOT on the mesh" is allowed and intentional.)
        assert "this CC session is" not in ctx
        # The reason is surfaced.
        assert "refusing to spawn bridge: bad nick" in ctx
        # The fix instructions are present so the operator knows what
        # to do next.
        assert "culture bridge start" in ctx

    def test_success_path_unchanged(self):
        """Sanity: when bridge_error is None, the original happy-path
        context is produced and contains the expected nick + roster."""
        ctx = hook._format_additional_context(
            nick="local-fork",
            mission="",
            roster="peer1, peer2",
            spool=[],
            bridge_error=None,
        )
        assert "on the mesh" in ctx
        assert "local-fork" in ctx
        assert "peer1, peer2" in ctx
        assert "BRIDGE SPAWN FAILED" not in ctx


class _StringIO:
    """Lightweight stdin replacement — only needs ``.read()``."""

    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text
