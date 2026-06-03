"""Tests for the hierarchical /api/agents/tree endpoint (Phase 7.5 / AD-5).

The flat /api/agents shape is preserved for backward-compat
(tests/test_dashboard.py asserts it); this file covers the additional
tree shape only.
"""

from __future__ import annotations

from tests._sdk_stub import install_claude_sdk_stub

install_claude_sdk_stub()

import json  # noqa: E402
import os  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
import yaml as _yaml  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from culture.dashboard.server import build_app, list_agents_tree  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    return tmp_path


@pytest_asyncio.fixture
async def client(home):
    app = build_app(config_path=os.path.join(str(home), "server.yaml"))
    async with TestClient(TestServer(app)) as c:
        yield c


def _write_request(home, rid, helper_nick, tool="Edit", input_dict=None):
    qdir = os.path.join(str(home), "perm-queue")
    os.makedirs(qdir, exist_ok=True)
    with open(os.path.join(qdir, f"{rid}.json"), "w", encoding="utf-8") as f:
        json.dump(
            {"id": rid, "helper_nick": helper_nick, "tool_name": tool, "input": input_dict or {}},
            f,
        )


def _write_boss_and_worker(home):
    """Seed a manifest with one boss + one worker owned by it."""
    bdir = os.path.join(str(home), "boss")
    wdir = os.path.join(str(home), "helpers", "w")
    os.makedirs(bdir, exist_ok=True)
    os.makedirs(wdir, exist_ok=True)
    with open(os.path.join(bdir, "culture.yaml"), "w", encoding="utf-8") as f:
        _yaml.safe_dump({"suffix": "boss", "backend": "claude", "tags": ["boss"]}, f)
    with open(os.path.join(wdir, "culture.yaml"), "w", encoding="utf-8") as f:
        _yaml.safe_dump({"suffix": "w", "backend": "claude", "boss": "local-boss"}, f)
    with open(os.path.join(str(home), "server.yaml"), "w", encoding="utf-8") as f:
        _yaml.safe_dump(
            {
                "server": {"name": "local", "host": "127.0.0.1", "port": 6667},
                "agents": {"boss": bdir, "w": wdir},
            },
            f,
        )


class TestTreeShape:
    @pytest.mark.asyncio
    async def test_tree_empty(self, client):
        resp = await client.get("/api/agents/tree")
        assert resp.status == 200
        data = await resp.json()
        assert data == {"projects": [], "peer_bosses": []}

    @pytest.mark.asyncio
    async def test_tree_groups_workers_under_boss(self, client, home):
        _write_boss_and_worker(home)
        resp = await client.get("/api/agents/tree")
        assert resp.status == 200
        data = await resp.json()
        assert len(data["projects"]) == 1
        proj = data["projects"][0]
        # project_nick is the boss-nick suffix (legacy <server>-<agent> form
        # strips the server prefix).
        assert proj["project_nick"] == "boss"
        assert proj["boss"]["nick"] == "local-boss"
        assert proj["boss"]["is_boss"] is True
        assert [w["nick"] for w in proj["workers"]] == ["local-w"]
        assert proj["pending_perm_count"] == 0
        assert data["peer_bosses"] == []

    @pytest.mark.asyncio
    async def test_tree_aggregates_pending_perm_count(self, client, home):
        _write_boss_and_worker(home)
        _write_request(home, "req-1", "local-w")
        _write_request(home, "req-2", "local-w")
        _write_request(home, "req-3", "local-boss")
        data = await (await client.get("/api/agents/tree")).json()
        proj = data["projects"][0]
        # Boss queue + every worker queue rolls up into one count.
        assert proj["pending_perm_count"] == 3

    @pytest.mark.asyncio
    async def test_tree_peer_boss_when_worker_unowned(self, client, home):
        # A worker whose ``boss`` field names a NICK NOT present locally
        # surfaces under peer_bosses (read-only observation).
        wdir = os.path.join(str(home), "helpers", "remote-w")
        os.makedirs(wdir, exist_ok=True)
        with open(os.path.join(wdir, "culture.yaml"), "w", encoding="utf-8") as f:
            _yaml.safe_dump(
                {"suffix": "remote-w", "backend": "claude", "boss": "peer-boss"},
                f,
            )
        with open(os.path.join(str(home), "server.yaml"), "w", encoding="utf-8") as f:
            _yaml.safe_dump(
                {
                    "server": {"name": "local", "host": "127.0.0.1", "port": 6667},
                    "agents": {"remote-w": wdir},
                },
                f,
            )
        data = await (await client.get("/api/agents/tree")).json()
        assert data["projects"] == []
        assert len(data["peer_bosses"]) == 1
        peer = data["peer_bosses"][0]
        assert peer["nick"] == "peer-boss"
        assert peer["is_boss"] is True
        assert [w["nick"] for w in peer["workers"]] == ["local-remote-w"]


class TestTreeHelper:
    def test_list_agents_tree_pure(self, home):
        # The helper is a pure function over the manifest + perm queue;
        # it must work without an HTTP client.
        _write_boss_and_worker(home)
        tree = list_agents_tree(os.path.join(str(home), "server.yaml"))
        assert set(tree.keys()) == {"projects", "peer_bosses"}
        assert len(tree["projects"]) == 1
        assert tree["projects"][0]["project_nick"] == "boss"

    def test_list_agents_tree_running_first(self, home, monkeypatch):
        # Sort order: running bosses before stopped bosses.
        bdir1 = os.path.join(str(home), "boss1")
        bdir2 = os.path.join(str(home), "boss2")
        os.makedirs(bdir1, exist_ok=True)
        os.makedirs(bdir2, exist_ok=True)
        with open(os.path.join(bdir1, "culture.yaml"), "w", encoding="utf-8") as f:
            _yaml.safe_dump({"suffix": "alpha", "backend": "claude", "tags": ["boss"]}, f)
        with open(os.path.join(bdir2, "culture.yaml"), "w", encoding="utf-8") as f:
            _yaml.safe_dump({"suffix": "zulu", "backend": "claude", "tags": ["boss"]}, f)
        with open(os.path.join(str(home), "server.yaml"), "w", encoding="utf-8") as f:
            _yaml.safe_dump(
                {
                    "server": {"name": "local", "host": "127.0.0.1", "port": 6667},
                    "agents": {"alpha": bdir1, "zulu": bdir2},
                },
                f,
            )
        # Force zulu to look running, alpha stopped.
        from culture.dashboard import server as srv

        def _fake_state(nick):
            return "running" if nick == "local-zulu" else "stopped"

        monkeypatch.setattr(srv, "_agent_state", _fake_state)
        tree = list_agents_tree(os.path.join(str(home), "server.yaml"))
        ordered = [p["boss"]["nick"] for p in tree["projects"]]
        # zulu is running so it precedes alpha despite alphabetical order.
        assert ordered == ["local-zulu", "local-alpha"]
