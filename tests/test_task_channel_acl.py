"""Tests for the unified channel-class ACL (v8.18.7 + 2026-06-03 rearch).

Verifies that:
- A worker can join its own #task-<suffix> channel.
- A foreign worker is refused from another worker's #task-<suffix> channel.
- #joint-* channels are always joinable by anyone (Phase 3 will add gating).
- The boss of a worker can join that worker's #task-<suffix> channel.
- #team is frozen — refused for every nick.
- #team-<project> admits the owning boss + that boss's workers, refuses
  workers from other projects.
"""

import os
from unittest.mock import patch

import pytest

from culture.agentirc.client import _task_channel_acl

# ---------------------------------------------------------------------------
# Unit tests for _task_channel_acl (no server needed)
# ---------------------------------------------------------------------------


def _mock_owner_map():
    """Simulated manifest: three workers across two bosses."""
    return {
        "local-worker-a": "local-boss",
        "local-worker-b": "local-boss",
        "local-worker-c": "local-boss2",
    }


def _mock_role_map():
    """Companion role map matching _mock_owner_map."""
    return {
        "local-worker-a": {"role": "worker", "boss": "local-boss", "project": "local-boss"},
        "local-worker-b": {"role": "worker", "boss": "local-boss", "project": "local-boss"},
        "local-worker-c": {"role": "worker", "boss": "local-boss2", "project": "local-boss2"},
        "local-boss": {"role": "boss", "boss": None, "project": "local-boss"},
        "local-boss2": {"role": "boss", "boss": None, "project": "local-boss2"},
    }


class TestTaskChannelAclUnit:
    """Unit tests for the ACL function itself."""

    def test_owner_allowed_own_task(self):
        """Worker can join its own task channel."""
        with patch(
            "culture.agentirc.client._load_role_map",
            return_value=_mock_role_map(),
        ):
            assert _task_channel_acl("local-worker-a", "#task-worker-a", "local") is True

    def test_foreign_worker_refused(self):
        """Worker cannot join another worker's task channel."""
        with (
            patch(
                "culture.agentirc.client._load_owner_map",
                return_value=_mock_owner_map(),
            ),
            patch(
                "culture.agentirc.client._load_role_map",
                return_value=_mock_role_map(),
            ),
        ):
            assert _task_channel_acl("local-worker-b", "#task-worker-a", "local") is False

    def test_boss_allowed_worker_task(self):
        """Boss can join its worker's task channel."""
        with (
            patch(
                "culture.agentirc.client._load_owner_map",
                return_value=_mock_owner_map(),
            ),
            patch(
                "culture.agentirc.client._load_role_map",
                return_value=_mock_role_map(),
            ),
        ):
            assert _task_channel_acl("local-boss", "#task-worker-a", "local") is True

    def test_wrong_boss_refused(self):
        """A different boss cannot join another boss's worker's task channel."""
        with (
            patch(
                "culture.agentirc.client._load_owner_map",
                return_value=_mock_owner_map(),
            ),
            patch(
                "culture.agentirc.client._load_role_map",
                return_value=_mock_role_map(),
            ),
        ):
            assert _task_channel_acl("local-boss2", "#task-worker-a", "local") is False

    def test_joint_channel_always_allowed(self):
        """#joint-* channels are open to everyone (Phase 3 will gate them)."""
        assert _task_channel_acl("local-worker-b", "#joint-fixes", "local") is True
        assert _task_channel_acl("local-random", "#joint-coordination", "local") is True

    def test_team_channel_refused_for_workers(self):
        """#team is frozen post-AD-4 — refused for every worker."""
        with patch(
            "culture.agentirc.client._load_role_map",
            return_value=_mock_role_map(),
        ):
            assert _task_channel_acl("local-worker-a", "#team", "local") is False

    def test_team_channel_refused_for_boss(self):
        """#team is frozen post-AD-4 — refused even for bosses."""
        with patch(
            "culture.agentirc.client._load_role_map",
            return_value=_mock_role_map(),
        ):
            assert _task_channel_acl("local-boss", "#team", "local") is False

    def test_team_channel_refused_for_human(self):
        """#team is frozen post-AD-4 — refused even for humans."""
        with patch(
            "culture.agentirc.client._load_role_map",
            return_value=_mock_role_map(),
        ):
            assert _task_channel_acl("edo", "#team", "local") is False

    def test_team_channel_refused_for_system(self):
        """#team is frozen post-AD-4 — refused for system clients too."""
        with patch(
            "culture.agentirc.client._load_role_map",
            return_value=_mock_role_map(),
        ):
            assert _task_channel_acl("system-local", "#team", "local") is False

    def test_system_channel_refuses_workers_admits_boss(self):
        """#system admits bosses + humans; workers refused."""
        with patch(
            "culture.agentirc.client._load_role_map",
            return_value=_mock_role_map(),
        ):
            assert _task_channel_acl("local-worker-a", "#system", "local") is False
            assert _task_channel_acl("local-boss", "#system", "local") is True
            assert _task_channel_acl("edo", "#system", "local") is True

    def test_team_project_admits_own_boss_and_workers(self):
        """#team-<project> admits the owning boss + that boss's workers."""
        with patch(
            "culture.agentirc.client._load_role_map",
            return_value=_mock_role_map(),
        ):
            # Boss for the project may join its own fireplace.
            assert _task_channel_acl("local-boss", "#team-local-boss", "local") is True
            # Workers under that boss may join.
            assert _task_channel_acl("local-worker-a", "#team-local-boss", "local") is True
            assert _task_channel_acl("local-worker-b", "#team-local-boss", "local") is True

    def test_team_project_refuses_other_project_workers(self):
        """Worker from a different project cannot join another project's #team-<project>."""
        with patch(
            "culture.agentirc.client._load_role_map",
            return_value=_mock_role_map(),
        ):
            assert _task_channel_acl("local-worker-c", "#team-local-boss", "local") is False

    def test_team_project_refuses_other_boss(self):
        """A different boss cannot join another boss's #team-<project>."""
        with patch(
            "culture.agentirc.client._load_role_map",
            return_value=_mock_role_map(),
        ):
            assert _task_channel_acl("local-boss2", "#team-local-boss", "local") is False

    def test_team_project_admits_humans(self):
        """Humans collaborate across the hierarchy and may join any #team-<project>."""
        with patch(
            "culture.agentirc.client._load_role_map",
            return_value=_mock_role_map(),
        ):
            assert _task_channel_acl("edo", "#team-local-boss", "local") is True

    def test_general_channel_unrestricted(self):
        """#general (which #team used to be alongside) remains an open default."""
        assert _task_channel_acl("local-worker-a", "#general", "local") is True

    def test_system_nick_always_allowed_for_task(self):
        """system-* nicks can join any task channel."""
        assert _task_channel_acl("system-local", "#task-worker-a", "local") is True

    def test_no_manifest_owner_still_joins_own(self):
        """Owner can join own channel even without manifest."""
        with (
            patch("culture.agentirc.client._load_owner_map", return_value={}),
            patch("culture.agentirc.client._load_role_map", return_value={}),
        ):
            assert _task_channel_acl("local-worker-a", "#task-worker-a", "local") is True

    def test_no_manifest_foreign_refused(self):
        """Foreign worker refused even without manifest (fail closed)."""
        with (
            patch("culture.agentirc.client._load_owner_map", return_value={}),
            patch("culture.agentirc.client._load_role_map", return_value={}),
        ):
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
    with (
        patch(
            "culture.agentirc.client._load_owner_map",
            return_value=_mock_owner_map(),
        ),
        patch(
            "culture.agentirc.client._load_role_map",
            return_value=_mock_role_map(),
        ),
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
    role_map = {
        "testserv-worker-a": {
            "role": "worker",
            "boss": "testserv-boss",
            "project": "testserv-boss",
        },
        "testserv-boss": {"role": "boss", "boss": None, "project": "testserv-boss"},
    }
    with (
        patch("culture.agentirc.client._load_owner_map", return_value=owner_map),
        patch("culture.agentirc.client._load_role_map", return_value=role_map),
    ):
        client = await make_client(nick="testserv-boss", user="boss")
        await client.send("JOIN #task-worker-a")
        lines = await client.recv_all(timeout=1.0)
        joined = " ".join(lines)
        assert "JOIN" in joined
        assert "#task-worker-a" in joined
        assert "353" in joined  # RPL_NAMREPLY


@pytest.mark.asyncio
async def test_team_channel_refused_for_all(server, make_client):
    """#team is frozen: every JOIN attempt returns 474 ERR_BANNEDFROMCHAN."""
    # Empty role_map → joiner is classified as a human; #team is still refused.
    with patch("culture.agentirc.client._load_role_map", return_value={}):
        client = await make_client(nick="testserv-worker-a", user="worker-a")
        await client.send("JOIN #team")
        response = await client.recv()
        assert "474" in response  # ERR_BANNEDFROMCHAN
        assert "#team" in response


@pytest.mark.asyncio
async def test_team_project_admits_own_worker(server, make_client):
    """A worker may JOIN its own project's #team-<project>."""
    role_map = {
        "testserv-worker-a": {
            "role": "worker",
            "boss": "testserv-boss",
            "project": "testserv-boss",
        },
        "testserv-boss": {"role": "boss", "boss": None, "project": "testserv-boss"},
    }
    with patch("culture.agentirc.client._load_role_map", return_value=role_map):
        client = await make_client(nick="testserv-worker-a", user="worker-a")
        await client.send("JOIN #team-testserv-boss")
        lines = await client.recv_all(timeout=1.0)
        joined = " ".join(lines)
        assert "JOIN" in joined
        assert "#team-testserv-boss" in joined
        assert "353" in joined


@pytest.mark.asyncio
async def test_team_project_refuses_other_project_worker(server, make_client):
    """A worker from another project cannot JOIN another's #team-<project>."""
    role_map = {
        "testserv-worker-c": {
            "role": "worker",
            "boss": "testserv-boss2",
            "project": "testserv-boss2",
        },
        "testserv-boss": {"role": "boss", "boss": None, "project": "testserv-boss"},
        "testserv-boss2": {"role": "boss", "boss": None, "project": "testserv-boss2"},
    }
    with patch("culture.agentirc.client._load_role_map", return_value=role_map):
        client = await make_client(nick="testserv-worker-c", user="worker-c")
        await client.send("JOIN #team-testserv-boss")
        response = await client.recv()
        assert "474" in response  # ERR_BANNEDFROMCHAN
        assert "#team-testserv-boss" in response


@pytest.mark.asyncio
async def test_team_project_admits_boss(server, make_client):
    """The owning boss may JOIN its own #team-<project>."""
    role_map = {
        "testserv-boss": {"role": "boss", "boss": None, "project": "testserv-boss"},
    }
    with patch("culture.agentirc.client._load_role_map", return_value=role_map):
        client = await make_client(nick="testserv-boss", user="boss")
        await client.send("JOIN #team-testserv-boss")
        lines = await client.recv_all(timeout=1.0)
        joined = " ".join(lines)
        assert "JOIN" in joined
        assert "#team-testserv-boss" in joined
        assert "353" in joined


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

    def test_cache_amortizes_repeated_calls(self):
        """The expensive load runs ONCE within the TTL even with N calls."""
        from culture.agentirc import client as ircd_client

        calls = {"n": 0}

        def fake_load():
            calls["n"] += 1
            return {"local-worker-a": "local-boss"}

        with patch(
            "culture.config.load_config_or_default", side_effect=AssertionError("should be cached")
        ):
            # Pre-seed the cache with the expected data + key so the TTL
            # check succeeds (load_config_or_default should never fire).
            import time as _t

            from culture.clients._perm_broker import culture_home

            ircd_client._owner_map_cache = {"local-worker-a": "local-boss"}
            ircd_client._owner_map_ts = _t.monotonic()
            ircd_client._owner_map_key = os.path.join(culture_home(), "server.yaml")
            for _ in range(50):
                got = ircd_client._load_owner_map()
                assert got == {"local-worker-a": "local-boss"}

    def test_cache_refreshes_after_ttl(self, monkeypatch):
        """Past the TTL, the next call re-reads the manifest."""
        from culture.agentirc import client as ircd_client

        fake_now = [1000.0]
        monkeypatch.setattr(ircd_client._time, "monotonic", lambda: fake_now[0])

        calls = {"n": 0}

        def fake_load(*_a, **_kw):
            calls["n"] += 1

            class _Cfg:
                agents = []

            return _Cfg()

        monkeypatch.setattr("culture.config.load_config_or_default", fake_load)
        ircd_client._load_owner_map()
        ircd_client._load_owner_map()
        assert calls["n"] == 1  # cached
        # Advance past TTL.
        fake_now[0] += ircd_client.OWNER_MAP_TTL_S + 0.1
        ircd_client._load_owner_map()
        assert calls["n"] == 2  # refreshed

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


# ---------------------------------------------------------------------------
# Role-map cache tests (2026-06-03 rearch) — parallel to the owner-map cache.
# ---------------------------------------------------------------------------


class TestRoleMapCache:
    """The role-map cache amortizes manifest reads for the unified ACL.

    Mirrors TestOwnerMapCache: same TTL constant, same key (server.yaml
    path), and ``_invalidate_owner_map_cache`` flushes both maps.
    """

    def setup_method(self):
        from culture.agentirc.client import _invalidate_owner_map_cache

        _invalidate_owner_map_cache()

    def teardown_method(self):
        from culture.agentirc.client import _invalidate_owner_map_cache

        _invalidate_owner_map_cache()

    def test_role_map_classifies_boss_and_worker(self, monkeypatch):
        """Manifest with a boss + worker yields the expected role records."""
        from culture.agentirc import client as ircd_client

        class _Boss:
            nick = "local-boss"
            tags = ["boss"]
            boss = ""

        class _Worker:
            nick = "local-w1"
            tags = []
            boss = "local-boss"

        class _Cfg:
            agents = [_Boss(), _Worker()]

        monkeypatch.setattr("culture.config.load_config_or_default", lambda *a, **kw: _Cfg())
        rm = ircd_client._load_role_map()
        assert rm["local-boss"] == {"role": "boss", "boss": None, "project": "local-boss"}
        assert rm["local-w1"] == {
            "role": "worker",
            "boss": "local-boss",
            "project": "local-boss",
        }

    def test_role_map_refreshes_after_ttl(self, monkeypatch):
        """Past the TTL, the role map re-reads the manifest."""
        from culture.agentirc import client as ircd_client

        fake_now = [1000.0]
        monkeypatch.setattr(ircd_client._time, "monotonic", lambda: fake_now[0])

        calls = {"n": 0}

        def fake_load(*_a, **_kw):
            calls["n"] += 1

            class _Cfg:
                agents = []

            return _Cfg()

        monkeypatch.setattr("culture.config.load_config_or_default", fake_load)
        ircd_client._load_role_map()
        ircd_client._load_role_map()
        assert calls["n"] == 1
        fake_now[0] += ircd_client.OWNER_MAP_TTL_S + 0.1
        ircd_client._load_role_map()
        assert calls["n"] == 2

    def test_invalidate_flushes_both_maps(self, monkeypatch):
        """_invalidate_owner_map_cache flushes the owner AND role maps."""
        from culture.agentirc import client as ircd_client

        calls = {"n": 0}

        def fake_load(*_a, **_kw):
            calls["n"] += 1

            class _Cfg:
                agents = []

            return _Cfg()

        monkeypatch.setattr("culture.config.load_config_or_default", fake_load)
        ircd_client._load_role_map()
        ircd_client._load_owner_map()
        # First call to each populates its cache.
        assert calls["n"] == 2
        ircd_client._load_role_map()
        ircd_client._load_owner_map()
        assert calls["n"] == 2  # both cached
        ircd_client._invalidate_owner_map_cache()
        ircd_client._load_role_map()
        ircd_client._load_owner_map()
        assert calls["n"] == 4  # both re-read


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
