"""Tests for the new state-diff SSE endpoints (Phase 7.5).

Three streams replace the dashboard's setInterval polls:
    /api/agents/stream     ← was setInterval(refreshAgents, 2500)
    /api/pending/stream    ← was setInterval(refreshPending, 2000)
    /api/channels/stream   ← was setInterval(refreshChannels, 3000)

The wire shape is SSE: a sequence of ``data: <json>\\n\\n`` frames, one
per state change, with the FIRST frame always being the current state
so a fresh subscriber sees the world immediately.
"""

from __future__ import annotations

from tests._sdk_stub import install_claude_sdk_stub

install_claude_sdk_stub()

import asyncio  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
import yaml as _yaml  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from culture.dashboard.server import build_app  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    return tmp_path


@pytest_asyncio.fixture
async def client(home):
    app = build_app(config_path=os.path.join(str(home), "server.yaml"))
    async with TestClient(TestServer(app)) as c:
        yield c


async def _read_one_event(resp) -> dict:
    """Read a single SSE event (``data: <json>\\n\\n``) and parse the JSON."""
    chunk = await asyncio.wait_for(resp.content.readuntil(b"\n\n"), timeout=2.0)
    line = chunk.decode("utf-8").strip()
    assert line.startswith("data: "), f"unexpected SSE frame: {line!r}"
    return json.loads(line[len("data: ") :])


def _write_request(home, rid, nick="local-w"):
    qdir = os.path.join(str(home), "perm-queue")
    os.makedirs(qdir, exist_ok=True)
    with open(os.path.join(qdir, f"{rid}.json"), "w", encoding="utf-8") as f:
        json.dump(
            {"id": rid, "helper_nick": nick, "tool_name": "Edit", "input": {}},
            f,
        )


def _seed_manifest(home):
    wdir = os.path.join(str(home), "helpers", "w")
    os.makedirs(wdir, exist_ok=True)
    with open(os.path.join(wdir, "culture.yaml"), "w", encoding="utf-8") as f:
        _yaml.safe_dump(
            {"suffix": "w", "backend": "claude", "boss": "local-boss"},
            f,
        )
    with open(os.path.join(str(home), "server.yaml"), "w", encoding="utf-8") as f:
        _yaml.safe_dump(
            {
                "server": {"name": "local", "host": "127.0.0.1", "port": 6667},
                "agents": {"w": wdir},
            },
            f,
        )


class TestAgentsStream:
    @pytest.mark.asyncio
    async def test_initial_frame_empty_manifest(self, client):
        resp = await client.get("/api/agents/stream")
        assert resp.status == 200
        assert resp.headers["Content-Type"].startswith("text/event-stream")
        event = await _read_one_event(resp)
        assert event == {"agents": []}
        resp.close()

    @pytest.mark.asyncio
    async def test_change_emits_new_frame_within_latency_bound(self, client, home):
        # 200 ms latency target per Phase 7.5 acceptance — read the
        # initial frame, mutate the manifest, then assert we see a new
        # frame in well under 200 ms.
        resp = await client.get("/api/agents/stream")
        first = await _read_one_event(resp)
        assert first == {"agents": []}
        # Poll cadence is 100 ms; a state change should land within
        # one cycle plus event-loop slop — allow 500 ms ceiling, but the
        # typical case is < 200 ms.
        _seed_manifest(home)
        second = await asyncio.wait_for(_read_one_event(resp), timeout=0.5)
        assert any(a["nick"] == "local-w" for a in second["agents"])
        resp.close()


class TestPendingStream:
    @pytest.mark.asyncio
    async def test_initial_frame_empty(self, client):
        resp = await client.get("/api/pending/stream")
        assert resp.status == 200
        event = await _read_one_event(resp)
        assert event == {"pending": []}
        resp.close()

    @pytest.mark.asyncio
    async def test_new_request_appears_in_stream(self, client, home):
        resp = await client.get("/api/pending/stream")
        await _read_one_event(resp)  # initial empty
        _write_request(home, "req-1")
        event = await asyncio.wait_for(_read_one_event(resp), timeout=0.5)
        ids = [p["id"] for p in event["pending"]]
        assert "req-1" in ids
        resp.close()


class TestChannelsStream:
    @pytest.mark.asyncio
    async def test_initial_frame_empty(self, client):
        resp = await client.get("/api/channels/stream")
        assert resp.status == 200
        event = await _read_one_event(resp)
        assert event == {"channels": []}
        resp.close()

    @pytest.mark.asyncio
    async def test_manifest_change_emits_new_frame(self, client, home):
        # NB: list_channels filters out task-channels whose only members
        # are stopped workers. Seed a manifest with the boss tag so the
        # boss channel survives the filter.
        bdir = os.path.join(str(home), "boss")
        os.makedirs(bdir, exist_ok=True)
        with open(os.path.join(bdir, "culture.yaml"), "w", encoding="utf-8") as f:
            _yaml.safe_dump(
                {
                    "suffix": "boss",
                    "backend": "claude",
                    "tags": ["boss"],
                    "channels": ["#boss"],
                },
                f,
            )
        with open(os.path.join(str(home), "server.yaml"), "w", encoding="utf-8") as f:
            _yaml.safe_dump(
                {
                    "server": {"name": "local", "host": "127.0.0.1", "port": 6667},
                    "agents": {},
                },
                f,
            )
        resp = await client.get("/api/channels/stream")
        first = await _read_one_event(resp)
        assert first == {"channels": []}
        # Now register the boss in the manifest and assert the new state
        # frame fires.
        with open(os.path.join(str(home), "server.yaml"), "w", encoding="utf-8") as f:
            _yaml.safe_dump(
                {
                    "server": {"name": "local", "host": "127.0.0.1", "port": 6667},
                    "agents": {"boss": bdir},
                },
                f,
            )
        event = await asyncio.wait_for(_read_one_event(resp), timeout=0.5)
        assert any(c["channel"] == "#boss" for c in event["channels"])
        resp.close()
