"""Tests for the Mission Control dashboard backend (aiohttp)."""

from __future__ import annotations

from tests._sdk_stub import install_claude_sdk_stub

install_claude_sdk_stub()

import json  # noqa: E402
import os  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from culture.dashboard.server import build_app, serve_dashboard  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    return tmp_path


def _write_request(home, rid, tool, input_dict, nick="local-w"):
    qdir = os.path.join(str(home), "perm-queue")
    os.makedirs(qdir, exist_ok=True)
    with open(os.path.join(qdir, f"{rid}.json"), "w", encoding="utf-8") as f:
        json.dump({"id": rid, "helper_nick": nick, "tool_name": tool, "input": input_dict}, f)


def _decision(home, rid):
    path = os.path.join(str(home), "perm-decisions", f"{rid}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@pytest_asyncio.fixture
async def client(home):
    app = build_app(config_path=os.path.join(str(home), "server.yaml"))
    async with TestClient(TestServer(app)) as c:
        yield c


class TestReadEndpoints:
    @pytest.mark.asyncio
    async def test_agents_empty(self, client):
        resp = await client.get("/api/agents")
        assert resp.status == 200
        data = await resp.json()
        assert data == {"agents": []}

    @pytest.mark.asyncio
    async def test_pending_lists_requests(self, client, home):
        _write_request(home, "req-1", "Edit", {"file_path": "/a.py"})
        resp = await client.get("/api/pending")
        data = await resp.json()
        ids = [p["id"] for p in data["pending"]]
        assert "req-1" in ids

    @pytest.mark.asyncio
    async def test_index_served(self, client):
        resp = await client.get("/")
        assert resp.status == 200
        body = await resp.text()
        assert "mission control" in body.lower()


class TestControlEndpoints:
    @pytest.mark.asyncio
    async def test_approve_writes_decision_no_ceiling(self, client, home):
        # Human (dashboard) is top authority — even an above-boss-ceiling tool
        # like an MCP send is approvable here.
        _write_request(home, "req-mcp", "mcp__gmail__send", {"to": "x@y.z"})
        resp = await client.post("/api/approve", json={"id": "req-mcp"})
        assert resp.status == 200
        d = _decision(home, "req-mcp")
        assert d["verdict"] == "allow" and d["decided_by"] == "dashboard"

    @pytest.mark.asyncio
    async def test_approve_always(self, client, home):
        _write_request(home, "req-a", "Edit", {"file_path": "/a"})
        resp = await client.post("/api/approve", json={"id": "req-a", "always": True})
        assert resp.status == 200
        assert _decision(home, "req-a")["scope"] == "always"

    @pytest.mark.asyncio
    async def test_deny_with_reason(self, client, home):
        _write_request(home, "req-d", "Bash", {"command": "rm -rf /"})
        resp = await client.post("/api/deny", json={"id": "req-d", "reason": "nope"})
        assert resp.status == 200
        d = _decision(home, "req-d")
        assert d["verdict"] == "deny" and d["reason"] == "nope"

    @pytest.mark.asyncio
    async def test_approve_missing_id(self, client):
        resp = await client.post("/api/approve", json={})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_double_decision_conflict(self, client, home):
        _write_request(home, "req-dup", "Edit", {"file_path": "/a"})
        first = await client.post("/api/approve", json={"id": "req-dup"})
        assert first.status == 200
        second = await client.post("/api/deny", json={"id": "req-dup", "reason": "x"})
        assert second.status == 409

    @pytest.mark.asyncio
    async def test_policy_get_put_roundtrip(self, client, home):
        policy = {"auto_allow": [{"tool": "Read"}], "auto_deny": []}
        put = await client.put("/api/policy/local-w", json={"policy": policy})
        assert put.status == 200
        get = await client.get("/api/policy/local-w")
        data = await get.json()
        assert data["policy"] == policy

    @pytest.mark.asyncio
    async def test_pause_missing_nick(self, client):
        resp = await client.post("/api/pause", json={})
        assert resp.status == 400


class TestStream:
    @pytest.mark.asyncio
    async def test_audit_stream_emits_backlog(self, client, home):
        # Seed an audit log line, then connect — backlog should be delivered.
        adir = os.path.join(str(home), "audit")
        os.makedirs(adir, exist_ok=True)
        rec = {"ts": "2026-05-29T00:00:00Z", "type": "assistant", "text": "hello", "tool_uses": []}
        with open(os.path.join(adir, "local-w.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        resp = await client.get("/api/stream/audit/local-w")
        assert resp.status == 200
        # Read one SSE event from the backlog.
        chunk = await resp.content.readuntil(b"\n\n")
        assert b"hello" in chunk
        resp.close()

    @pytest.mark.asyncio
    async def test_unknown_stream_kind_404(self, client):
        resp = await client.get("/api/stream/bogus/local-w")
        assert resp.status == 404


class TestServeGuard:
    def test_refuses_non_loopback_bind(self):
        with pytest.raises(ValueError):
            serve_dashboard(host="0.0.0.0", port=8787)


class TestSecurity:
    @pytest.mark.asyncio
    async def test_nick_path_traversal_rejected_stream(self, client):
        # A nick that isn't <server>-<agent> form must be rejected (path safety).
        resp = await client.get("/api/stream/audit/bad.nick")
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_nick_path_traversal_rejected_policy_put(self, client):
        resp = await client.put("/api/policy/evil.path", json={"policy": {}})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_invalid_nick_rejected_control(self, client):
        resp = await client.post("/api/close", json={"nick": "../etc"})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_approve_rejects_traversal_id(self, client):
        # Qodo: request-id path traversal — write_decision validates the id.
        resp = await client.post("/api/approve", json={"id": "../../evil"})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_cross_origin_blocked(self, client):
        # A malicious page (DNS-rebinding) sends a non-loopback Origin → 403.
        resp = await client.post(
            "/api/approve",
            json={"id": "x"},
            headers={"Origin": "http://evil.com"},
        )
        assert resp.status == 403

    @pytest.mark.asyncio
    async def test_loopback_origin_allowed(self, client, home):
        _write_request(home, "req-ok", "Edit", {"file_path": "/a"})
        resp = await client.post(
            "/api/approve",
            json={"id": "req-ok"},
            headers={"Origin": "http://localhost:8787"},
        )
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_non_loopback_host_blocked(self, client):
        resp = await client.post(
            "/api/stop-all",
            json={"mode": "pause"},
            headers={"Host": "evil.com"},
        )
        assert resp.status == 403
