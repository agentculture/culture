"""Tests for the user-settings hook installer.

The installer writes a ``culture-bridge`` block into
``~/.claude/settings.json``. Critical invariants:

    1. Idempotent — repeat installs produce the same on-disk shape.
    2. Preserves unrelated hooks / MCP servers / settings.
    3. Uninstall removes only the ``culture-bridge`` block.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from culture.clients.claude.cc_plugin import install as installer


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    # Force the installer to recompute the settings path with the new HOME.
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return tmp_path


def _read(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class TestInstall:
    def test_writes_block_into_fresh_settings(self, home):
        path = installer.install()
        assert path == str(home / ".claude" / "settings.json")
        data = _read(path)
        assert "hooks" in data
        for event in installer.HOOK_EVENTS:
            entries = data["hooks"][event]
            assert len(entries) == 1
            entry = entries[0]
            assert entry["_culture_bridge"] is True
            assert entry["hooks"][0]["type"] == "command"
            assert "python3" in entry["hooks"][0]["command"]
            if event == "PreToolUse":
                assert entry["matcher"] == "*"

    def test_session_end_hook_registered(self, home):
        """Regression: SessionEnd was historically missing from
        HOOK_EVENTS even though ``session_end.py`` exists. Without it
        the spool-drain / cleanup on session-end never fires.

        SessionEnd has no matcher (unlike PreToolUse) so the assertion
        is simpler than the full loop above.
        """
        path = installer.install()
        data = _read(path)
        assert "SessionEnd" in data["hooks"]
        entries = data["hooks"]["SessionEnd"]
        assert len(entries) == 1
        entry = entries[0]
        assert entry["_culture_bridge"] is True
        assert "matcher" not in entry
        assert "session_end.py" in entry["hooks"][0]["command"]

    def test_install_is_idempotent(self, home):
        first = installer.install()
        before = _read(first)
        second = installer.install()
        after = _read(second)
        assert first == second
        assert before == after

    def test_install_preserves_unrelated_hooks(self, home):
        settings_path = str(home / ".claude" / "settings.json")
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        # Pre-existing operator hook + MCP setting must survive install.
        prior = {
            "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "echo operator"}]}]},
            "mcpServers": {"foo": {"command": "/usr/local/bin/foo"}},
            "theme": "dark",
        }
        with open(settings_path, "w", encoding="utf-8") as fh:
            json.dump(prior, fh)
        installer.install()
        data = _read(settings_path)
        # Operator's MCP + theme untouched
        assert data["mcpServers"] == {"foo": {"command": "/usr/local/bin/foo"}}
        assert data["theme"] == "dark"
        # Operator's PreToolUse entry still there alongside culture-bridge.
        pretool_entries = data["hooks"]["PreToolUse"]
        commands = [e["hooks"][0]["command"] for e in pretool_entries]
        assert any("echo operator" in c for c in commands)
        assert any("pre_tool_use.py" in c for c in commands)

    def test_install_overwrites_prior_culture_bridge_block(self, home):
        installer.install()
        # Tamper with the block — install must restore the canonical shape.
        data = _read(str(home / ".claude" / "settings.json"))
        data["hooks"]["Stop"][0]["hooks"][0]["command"] = "STALE"
        with open(str(home / ".claude" / "settings.json"), "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        installer.install()
        data2 = _read(str(home / ".claude" / "settings.json"))
        cmd = data2["hooks"]["Stop"][0]["hooks"][0]["command"]
        assert "STALE" not in cmd
        assert "stop.py" in cmd

    def test_install_chmod_600(self, home):
        path = installer.install()
        # 0o600 = file mode bits we want; mask the rest of the stat result.
        mode = os.stat(path).st_mode & 0o777
        assert mode == 0o600

    def test_install_explicit_path_override(self, home):
        explicit = str(home / "custom" / "settings.json")
        path = installer.install(settings_path_override=explicit)
        assert path == explicit
        assert os.path.exists(explicit)


class TestUninstall:
    def test_uninstall_removes_block(self, home):
        installer.install()
        installer.uninstall()
        path = str(home / ".claude" / "settings.json")
        data = _read(path)
        # No hooks key, OR hooks is empty.
        assert not data.get("hooks")

    def test_uninstall_preserves_unrelated(self, home):
        settings_path = str(home / ".claude" / "settings.json")
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        prior = {
            "mcpServers": {"foo": {"command": "/usr/local/bin/foo"}},
        }
        with open(settings_path, "w", encoding="utf-8") as fh:
            json.dump(prior, fh)
        installer.install()
        installer.uninstall()
        data = _read(settings_path)
        assert data.get("mcpServers") == {"foo": {"command": "/usr/local/bin/foo"}}

    def test_uninstall_when_no_settings_is_noop(self, home):
        # No settings.json yet → uninstall is a no-op.
        path = installer.uninstall()
        # Path is returned but file may not exist.
        assert path.endswith("settings.json")


class TestSettingsPathResolution:
    def test_default_path_uses_home(self, home):
        assert installer.settings_path() == str(home / ".claude" / "settings.json")

    def test_claude_config_dir_override(self, home, monkeypatch, tmp_path):
        override = tmp_path / "elsewhere"
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(override))
        assert installer.settings_path() == str(override / "settings.json")


class TestMarkerStripping:
    def test_strip_removes_marker_entries(self):
        hooks = {
            "Stop": [
                {"hooks": [{"command": "kept"}]},
                {"hooks": [{"command": "go"}], "_culture_bridge": True},
            ]
        }
        cleaned = installer._strip_culture_bridge_entries(hooks)
        assert len(cleaned["Stop"]) == 1
        assert cleaned["Stop"][0]["hooks"][0]["command"] == "kept"

    def test_strip_drops_empty_event_lists(self):
        hooks = {
            "Stop": [
                {"hooks": [{"command": "go"}], "_culture_bridge": True},
            ]
        }
        cleaned = installer._strip_culture_bridge_entries(hooks)
        assert "Stop" not in cleaned
