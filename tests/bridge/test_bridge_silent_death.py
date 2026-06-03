"""Tests for the bridge's silent-death watchdog (Phase 6.1).

The watchdog moved from ``culture/clients/claude/daemon.py`` to
``culture/clients/bridge/daemon.py`` per EL-11 of the rearchitecture
plan: it's a pure filesystem operation with no SDK dependency and the
bridge is the long-lived persistent process under CC-as-boss.

Original boss-tag-startup tests for the claude daemon's watchdog live
in ``tests/test_silent_death_watchdog.py``; this file covers the bridge
copy plus the NEW persistence behavior (warned-set survives bridge
restarts so already-known dead workers aren't re-DM'd to CC every time
the bridge bounces).
"""

from __future__ import annotations

import json
import os
import tempfile

from culture.clients.bridge.daemon import AgentDaemon
from culture.clients.claude.config import (
    AgentConfig,
    DaemonConfig,
    ServerConnConfig,
)


def _make_bridge_daemon(socket_dir, tags=None, nick="local-boss"):
    """Build a minimal bridge AgentDaemon for unit-testing helpers."""
    config = DaemonConfig(server=ServerConnConfig(host="127.0.0.1", port=6667))
    agent = AgentConfig(nick=nick, directory="/tmp", channels=[], tags=tags or [])
    return AgentDaemon(config, agent, socket_dir=socket_dir, skip_claude=True)


def _write_daemon_log_action(home, nick, action):
    """Write a single daemon-log line for *nick* with *action* as the last record."""
    log_dir = os.path.join(str(home), "daemon-log")
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, f"{nick}.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        f.write(
            json.dumps({"ts": "2026-06-03T20:00:00.000Z", "nick": nick, "action": action}) + "\n"
        )


class TestBridgeDaemonLogIndicatesCleanExit:
    """The clean-exit detector is preserved verbatim on the bridge —
    a shared primitive per the Phase 6.5 plan note."""

    def test_agent_exit_returns_true(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        sock = tempfile.mkdtemp()
        daemon = _make_bridge_daemon(sock, tags=["boss"])
        _write_daemon_log_action(tmp_path, "local-worker", "agent_start")
        _write_daemon_log_action(tmp_path, "local-worker", "engaged")
        _write_daemon_log_action(tmp_path, "local-worker", "agent_exit")
        assert daemon._daemon_log_indicates_clean_exit("local-worker") is True

    def test_agent_stop_returns_true(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        sock = tempfile.mkdtemp()
        daemon = _make_bridge_daemon(sock, tags=["boss"])
        _write_daemon_log_action(tmp_path, "local-worker", "agent_start")
        _write_daemon_log_action(tmp_path, "local-worker", "agent_stop")
        assert daemon._daemon_log_indicates_clean_exit("local-worker") is True

    def test_missing_log_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        sock = tempfile.mkdtemp()
        daemon = _make_bridge_daemon(sock, tags=["boss"])
        assert daemon._daemon_log_indicates_clean_exit("never-started") is False

    def test_engaged_only_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        sock = tempfile.mkdtemp()
        daemon = _make_bridge_daemon(sock, tags=["boss"])
        _write_daemon_log_action(tmp_path, "local-worker", "agent_start")
        _write_daemon_log_action(tmp_path, "local-worker", "engaged")
        assert daemon._daemon_log_indicates_clean_exit("local-worker") is False


class TestBridgeSilentDeathInit:
    """The watchdog state fields are initialized on every bridge,
    boss-tagged or not — start() decides whether to spawn the task."""

    def test_state_fields_initialized(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        sock = tempfile.mkdtemp()
        daemon = _make_bridge_daemon(sock, tags=["boss"])
        # Constructor never spawns the task; ``start()`` does.
        assert daemon._silent_death_task is None
        assert daemon._silent_death_warned == set()
        # The persistence path is resolved by start(), not __init__.
        assert daemon._silent_death_warned_path is None

    def test_non_boss_no_watchdog_state(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        sock = tempfile.mkdtemp()
        daemon = _make_bridge_daemon(sock, tags=[])
        # Even on a non-boss bridge the field exists (always-init pattern)
        # so cleanup in stop() never KeyErrors.
        assert daemon._silent_death_task is None
        assert daemon._silent_death_warned == set()


class TestBridgeSilentDeathPersistence:
    """Phase 6.1 NEW: warned-set survives bridge restarts so workers
    already classified as silently dead aren't re-DM'd to CC every
    bounce."""

    def test_persistence_path_resolves_under_culture_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        path = AgentDaemon._resolve_silent_death_warned_path("local-boss")
        assert path.endswith("silent-death-warned-local-boss.json")
        assert str(tmp_path) in path
        assert os.path.isdir(os.path.dirname(path))

    def test_persistence_path_sanitizes_traversal(self, tmp_path, monkeypatch):
        """Path-traversal characters in nick are scrubbed before the
        filename is built."""
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        path = AgentDaemon._resolve_silent_death_warned_path("..")
        # The "../" is scrubbed to "_" (no escape).
        assert ".." not in os.path.basename(path)

    def test_save_and_load_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        sock = tempfile.mkdtemp()
        daemon = _make_bridge_daemon(sock, tags=["boss"])
        daemon._silent_death_warned_path = AgentDaemon._resolve_silent_death_warned_path(
            "local-boss"
        )
        daemon._silent_death_warned.add("local-worker-1")
        daemon._silent_death_warned.add("local-worker-2")
        daemon._save_silent_death_warned()
        # Verify on-disk JSON shape.
        with open(daemon._silent_death_warned_path, encoding="utf-8") as fh:
            payload = json.load(fh)
        assert payload["schema"] == 1
        assert sorted(payload["warned"]) == ["local-worker-1", "local-worker-2"]
        # Restart simulation — new daemon, same path, restored set.
        daemon2 = _make_bridge_daemon(sock, tags=["boss"])
        daemon2._silent_death_warned_path = daemon._silent_death_warned_path
        daemon2._load_silent_death_warned()
        assert daemon2._silent_death_warned == {"local-worker-1", "local-worker-2"}

    def test_load_missing_file_is_silent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        sock = tempfile.mkdtemp()
        daemon = _make_bridge_daemon(sock, tags=["boss"])
        daemon._silent_death_warned_path = os.path.join(
            str(tmp_path), "bridge", "silent-death-warned-missing.json"
        )
        # No file → no exception, no warned entries.
        daemon._load_silent_death_warned()
        assert daemon._silent_death_warned == set()

    def test_load_malformed_json_starts_fresh(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        sock = tempfile.mkdtemp()
        daemon = _make_bridge_daemon(sock, tags=["boss"])
        bridge_dir = os.path.join(str(tmp_path), "bridge")
        os.makedirs(bridge_dir, exist_ok=True)
        bad_path = os.path.join(bridge_dir, "silent-death-warned-broken.json")
        with open(bad_path, "w", encoding="utf-8") as fh:
            fh.write("{ corrupt")
        daemon._silent_death_warned_path = bad_path
        daemon._load_silent_death_warned()
        assert daemon._silent_death_warned == set()

    def test_save_atomic_mode_0o600(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        sock = tempfile.mkdtemp()
        daemon = _make_bridge_daemon(sock, tags=["boss"])
        daemon._silent_death_warned_path = AgentDaemon._resolve_silent_death_warned_path(
            "local-boss"
        )
        daemon._silent_death_warned.add("local-worker-x")
        daemon._save_silent_death_warned()
        mode = os.stat(daemon._silent_death_warned_path).st_mode & 0o777
        assert mode == 0o600
