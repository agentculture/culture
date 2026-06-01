"""Tests for #task-* channel ACL enforcement (v8.18.7).

Verifies that:
- A worker can join its own #task-<suffix> channel.
- A foreign worker is refused from another worker's #task-<suffix> channel.
- #joint-* channels are always joinable by anyone.
- The boss of a worker can join that worker's #task-<suffix> channel.
- System channels (#team, #system) are unrestricted.
"""

import os
from unittest.mock import patch

import pytest

from culture.agentirc.client import _task_channel_acl

# ---------------------------------------------------------------------------
# Unit tests for _task_channel_acl (no server needed)
# ---------------------------------------------------------------------------


def _mock_owner_map():
    """Simulated manifest: two workers owned by one boss."""
    return {
        "local-worker-a": "local-boss",
        "local-worker-b": "local-boss",
        "local-worker-c": "local-boss2",
    }


class TestTaskChannelAclUnit:
    """Unit tests for the ACL function itself."""

    def test_owner_allowed_own_task(self):
        """Worker can join its own task channel."""
        assert _task_channel_acl("local-worker-a", "#task-worker-a", "local") is True

    def test_foreign_worker_refused(self):
        """Worker cannot join another worker's task channel."""
        with patch(
            "culture.agentirc.client._load_owner_map",
            return_value=_mock_owner_map(),
        ):
            assert _task_channel_acl("local-worker-b", "#task-worker-a", "local") is False

    def test_boss_allowed_worker_task(self):
        """Boss can join its worker's task channel."""
        with patch(
            "culture.agentirc.client._load_owner_map",
            return_value=_mock_owner_map(),
        ):
            assert _task_channel_acl("local-boss", "#task-worker-a", "local") is True

    def test_wrong_boss_refused(self):
        """A different boss cannot join another boss's worker's task channel."""
        with patch(
            "culture.agentirc.client._load_owner_map",
            return_value=_mock_owner_map(),
        ):
            assert _task_channel_acl("local-boss2", "#task-worker-a", "local") is False

    def test_joint_channel_always_allowed(self):
        """#joint-* channels are open to everyone."""
        assert _task_channel_acl("local-worker-b", "#joint-fixes", "local") is True
        assert _task_channel_acl("local-random", "#joint-coordination", "local") is True

    def test_regular_channel_allowed(self):
        """Regular channels like #team are unrestricted."""
        assert _task_channel_acl("local-worker-a", "#team", "local") is True
        assert _task_channel_acl("local-worker-a", "#system", "local") is True
        assert _task_channel_acl("local-worker-a", "#general", "local") is True

    def test_system_nick_always_allowed(self):
        """system-* nicks can join any task channel."""
        assert _task_channel_acl("system-local", "#task-worker-a", "local") is True

    def test_no_manifest_owner_still_joins_own(self):
        """Owner can join own channel even without manifest."""
        with patch("culture.agentirc.client._load_owner_map", return_value={}):
            assert _task_channel_acl("local-worker-a", "#task-worker-a", "local") is True

    def test_no_manifest_foreign_refused(self):
        """Foreign worker refused even without manifest (fail closed)."""
        with patch("culture.agentirc.client._load_owner_map", return_value={}):
            assert _task_channel_acl("local-worker-b", "#task-worker-a", "local") is False


# ---------------------------------------------------------------------------
# Integration tests -- real IRCd, real TCP connections
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_joins_own_task_channel(server, make_client):
    """Worker can JOIN its own #task-<suffix> channel."""
    client = await make_client(nick="testserv-worker-a", user="worker-a")
    await client.send("JOIN #task-worker-a")
    lines = await client.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "JOIN" in joined
    assert "#task-worker-a" in joined
    assert "353" in joined  # RPL_NAMREPLY


@pytest.mark.asyncio
async def test_foreign_worker_refused_task_channel(server, make_client):
    """Foreign worker gets 474 (ERR_BANNEDFROMCHAN) on another's #task-*."""
    with patch(
        "culture.agentirc.client._load_owner_map",
        return_value=_mock_owner_map(),
    ):
        client = await make_client(nick="testserv-worker-b", user="worker-b")
        await client.send("JOIN #task-worker-a")
        response = await client.recv()
        assert "474" in response  # ERR_BANNEDFROMCHAN
        assert "#task-worker-a" in response


@pytest.mark.asyncio
async def test_joint_channel_always_joinable(server, make_client):
    """Any client can join a #joint-* channel."""
    client = await make_client(nick="testserv-worker-b", user="worker-b")
    await client.send("JOIN #joint-fixes")
    lines = await client.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "JOIN" in joined
    assert "#joint-fixes" in joined


@pytest.mark.asyncio
async def test_boss_joins_worker_task_channel(server, make_client):
    """Boss can JOIN its worker's #task-* channel."""
    owner_map = {"testserv-worker-a": "testserv-boss"}
    with patch("culture.agentirc.client._load_owner_map", return_value=owner_map):
        client = await make_client(nick="testserv-boss", user="boss")
        await client.send("JOIN #task-worker-a")
        lines = await client.recv_all(timeout=1.0)
        joined = " ".join(lines)
        assert "JOIN" in joined
        assert "#task-worker-a" in joined
        assert "353" in joined  # RPL_NAMREPLY


@pytest.mark.asyncio
async def test_regular_channels_unrestricted(server, make_client):
    """#team and other normal channels remain open."""
    client = await make_client(nick="testserv-worker-a", user="worker-a")
    await client.send("JOIN #team")
    lines = await client.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "JOIN" in joined
    assert "#team" in joined


# ---------------------------------------------------------------------------
# Owner-map cache tests (v8.18.7 — closes audit HIGH on disk I/O per JOIN)
# ---------------------------------------------------------------------------


class TestOwnerMapCache:
    """The owner-map cache amortizes manifest reads across burst JOINs.

    Closes the HIGH audit finding that a 20-agent mesh did ~21 file
    reads per #task-* JOIN on the asyncio event loop. The cache holds
    for ``OWNER_MAP_TTL_S`` seconds and refreshes past that.
    """

    def setup_method(self):
        # Always start with a clean cache.
        from culture.agentirc.client import _invalidate_owner_map_cache

        _invalidate_owner_map_cache()

    def teardown_method(self):
        from culture.agentirc.client import _invalidate_owner_map_cache

        _invalidate_owner_map_cache()

    def test_cache_amortizes_repeated_calls(self, monkeypatch, tmp_path):
        """v8.19.44: cache is keyed by manifest (path, mtime_ns) — repeated
        calls return cached data as long as the file's mtime is unchanged."""
        from culture.agentirc import client as ircd_client

        # Real manifest at tmp_path/server.yaml so os.stat works.
        server_yaml = tmp_path / "server.yaml"
        server_yaml.write_text("server:\n  name: local\nagents: {}\n")
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))

        calls = {"n": 0}

        def fake_load(*_a, **_kw):
            calls["n"] += 1

            class _Cfg:
                agents = []

            return _Cfg()

        monkeypatch.setattr("culture.config.load_config_or_default", fake_load)
        for _ in range(50):
            ircd_client._load_owner_map()
        # 50 calls, manifest mtime unchanged → exactly ONE load.
        assert calls["n"] == 1

    def test_cache_refreshes_when_manifest_mtime_changes(self, monkeypatch, tmp_path):
        """v8.19.44: a write to server.yaml invalidates the cache on the
        very next read — no TTL race window."""
        from culture.agentirc import client as ircd_client

        server_yaml = tmp_path / "server.yaml"
        server_yaml.write_text("server:\n  name: local\nagents: {}\n")
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))

        calls = {"n": 0}

        def fake_load(*_a, **_kw):
            calls["n"] += 1

            class _Cfg:
                agents = []

            return _Cfg()

        monkeypatch.setattr("culture.config.load_config_or_default", fake_load)
        ircd_client._load_owner_map()
        ircd_client._load_owner_map()
        assert calls["n"] == 1  # cached on stable mtime

        # Mutate the manifest → mtime ticks → next call refreshes.
        import time as _t

        _t.sleep(0.02)  # ensure mtime_ns advances even on coarse FS
        server_yaml.write_text("server:\n  name: local\nagents:\n  newworker: /tmp/path\n")
        ircd_client._load_owner_map()
        assert calls["n"] == 2  # refreshed because mtime changed

    def test_invalidate_forces_refresh(self, monkeypatch):
        """_invalidate_owner_map_cache bypasses the TTL on demand."""
        from culture.agentirc import client as ircd_client

        calls = {"n": 0}

        def fake_load(*_a, **_kw):
            calls["n"] += 1

            class _Cfg:
                agents = []

            return _Cfg()

        monkeypatch.setattr("culture.config.load_config_or_default", fake_load)
        ircd_client._load_owner_map()
        ircd_client._load_owner_map()
        assert calls["n"] == 1
        ircd_client._invalidate_owner_map_cache()
        ircd_client._load_owner_map()
        assert calls["n"] == 2


class TestHyphenatedServerName:
    """Qodo finding #2: server names with hyphens broke ACL suffix parsing."""

    def test_hyphenated_server_owner_allowed(self):
        """Owner on a hyphenated server can join its own task channel."""
        assert _task_channel_acl("my-server-worker-a", "#task-worker-a", "my-server") is True

    def test_hyphenated_server_foreign_refused(self):
        """Foreign worker on hyphenated server is refused."""
        with patch(
            "culture.agentirc.client._load_owner_map",
            return_value={"my-server-worker-a": "my-server-boss"},
        ):
            assert _task_channel_acl("my-server-worker-b", "#task-worker-a", "my-server") is False

    def test_hyphenated_server_boss_allowed(self):
        """Boss on hyphenated server can join worker's task channel."""
        with patch(
            "culture.agentirc.client._load_owner_map",
            return_value={"my-server-worker-a": "my-server-boss"},
        ):
            assert _task_channel_acl("my-server-boss", "#task-worker-a", "my-server") is True

    def test_triple_hyphen_server(self):
        """Server name with multiple hyphens still works."""
        assert _task_channel_acl("a-b-c-worker", "#task-worker", "a-b-c") is True

    def test_no_server_name_fallback(self):
        """Without server_name, falls back to split('-', 1) for compat."""
        # "local-worker-a" split on first '-' -> suffix "worker-a"
        assert _task_channel_acl("local-worker-a", "#task-worker-a") is True


class TestCacheKeyedByCultureHome:
    """Qodo finding #3: cache must be keyed by CULTURE_HOME path."""

    def setup_method(self):
        from culture.agentirc.client import _invalidate_owner_map_cache

        _invalidate_owner_map_cache()

    def teardown_method(self):
        from culture.agentirc.client import _invalidate_owner_map_cache

        _invalidate_owner_map_cache()

    def test_culture_home_change_invalidates_cache(self, monkeypatch, tmp_path):
        """Changing CULTURE_HOME causes a cache miss even within TTL."""
        from culture.agentirc import client as ircd_client

        calls = {"n": 0, "homes": []}

        def fake_load(*_a, **_kw):
            calls["n"] += 1

            class _Cfg:
                agents = []

            return _Cfg()

        monkeypatch.setattr("culture.config.load_config_or_default", fake_load)

        # First call with home_a
        home_a = tmp_path / "home_a"
        home_a.mkdir()
        monkeypatch.setenv("CULTURE_HOME", str(home_a))
        ircd_client._load_owner_map()
        assert calls["n"] == 1

        # Second call same home — cached
        ircd_client._load_owner_map()
        assert calls["n"] == 1

        # Switch CULTURE_HOME — should miss cache
        home_b = tmp_path / "home_b"
        home_b.mkdir()
        monkeypatch.setenv("CULTURE_HOME", str(home_b))
        ircd_client._load_owner_map()
        assert calls["n"] == 2
