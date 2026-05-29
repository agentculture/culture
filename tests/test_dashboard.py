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


@pytest_asyncio.fixture
async def auth_client(home):
    app = build_app(
        config_path=os.path.join(str(home), "server.yaml"),
        auth_token="s3cret",
        trusted_hosts=["mymac.ts.net"],
    )
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
    async def test_agents_expose_team_fields(self, client, home):
        # The dashboard groups agents into teams from is_boss + boss; assert the
        # API contract those rely on (a boss + a worker owned by it).
        bdir = os.path.join(str(home), "boss")
        wdir = os.path.join(str(home), "helpers", "w")
        os.makedirs(bdir, exist_ok=True)
        os.makedirs(wdir, exist_ok=True)
        import yaml as _yaml

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
        data = await (await client.get("/api/agents")).json()
        by_nick = {a["nick"]: a for a in data["agents"]}
        assert by_nick["local-boss"]["is_boss"] is True
        assert by_nick["local-w"]["boss"] == "local-boss"  # groups under local-boss's team

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


class _FakeObserver:
    def __init__(self):
        self.sent = []

    async def send_message(self, target, text):
        self.sent.append((target, text))

    async def read_channel(self, channel, limit=50):
        return [f"[1m ago] <local-w> on it", f"[now] <observer> @local-w hello"]


class TestChat:
    @pytest.mark.asyncio
    async def test_message_sends_prefixed_to_task_channel(self, client, monkeypatch):
        from culture.dashboard import server

        fake = _FakeObserver()
        monkeypatch.setattr(server, "get_observer", lambda cfg: fake)
        resp = await client.post("/api/message", json={"nick": "local-w", "text": "do the thing"})
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True and data["channel"] == "#task-w"
        # Nick is prefixed so the agent's mention detector fires (mirrors boss brief).
        assert fake.sent == [("#task-w", "@local-w do the thing")]

    @pytest.mark.asyncio
    async def test_message_missing_nick_400(self, client):
        resp = await client.post("/api/message", json={"text": "x"})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_message_invalid_nick_400(self, client):
        resp = await client.post("/api/message", json={"nick": "../etc", "text": "x"})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_message_empty_text_400(self, client):
        resp = await client.post("/api/message", json={"nick": "local-w", "text": "   "})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_message_mesh_unreachable_502(self, client, monkeypatch):
        from culture.dashboard import server

        class _Boom:
            async def send_message(self, *a):
                raise OSError("mesh down")

        monkeypatch.setattr(server, "get_observer", lambda cfg: _Boom())
        resp = await client.post("/api/message", json={"nick": "local-w", "text": "x"})
        assert resp.status == 502

    @pytest.mark.asyncio
    async def test_message_cross_origin_blocked(self, client):
        resp = await client.post(
            "/api/message",
            json={"nick": "local-w", "text": "x"},
            headers={"Origin": "http://evil.com"},
        )
        assert resp.status == 403

    @pytest.mark.asyncio
    async def test_channel_read_returns_messages(self, client, monkeypatch):
        from culture.dashboard import server

        monkeypatch.setattr(server, "get_observer", lambda cfg: _FakeObserver())
        resp = await client.get("/api/channel/local-w")
        assert resp.status == 200
        data = await resp.json()
        assert data["channel"] == "#task-w"
        assert any("hello" in m for m in data["messages"])

    @pytest.mark.asyncio
    async def test_channel_read_unreachable_is_empty_not_500(self, client, monkeypatch):
        from culture.dashboard import server

        class _Boom:
            async def read_channel(self, *a, **k):
                raise OSError("mesh down")

        monkeypatch.setattr(server, "get_observer", lambda cfg: _Boom())
        resp = await client.get("/api/channel/local-w")
        assert resp.status == 200
        assert (await resp.json())["messages"] == []

    @pytest.mark.asyncio
    async def test_channel_read_traversal_nick_400(self, client):
        resp = await client.get("/api/channel/bad.nick")
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


class TestAuth:
    @pytest.mark.asyncio
    async def test_no_token_api_401(self, auth_client):
        resp = await auth_client.get("/api/agents")
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_bootstrap_sets_cookie_and_redirects(self, auth_client):
        resp = await auth_client.get("/?token=s3cret", allow_redirects=False)
        assert resp.status == 302
        assert "culture_dash=s3cret" in resp.headers.get("Set-Cookie", "")

    @pytest.mark.asyncio
    async def test_bad_token_401(self, auth_client):
        resp = await auth_client.get("/?token=wrong", allow_redirects=False)
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_valid_cookie_allows_api(self, auth_client):
        # Bootstrap sets the cookie on the shared client jar; the next call carries it.
        await auth_client.get("/?token=s3cret")
        resp = await auth_client.get("/api/agents")
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_trusted_host_allowed_with_cookie(self, auth_client):
        await auth_client.get("/?token=s3cret")
        resp = await auth_client.get("/api/agents", headers={"Host": "mymac.ts.net"})
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_untrusted_host_forbidden(self, auth_client):
        resp = await auth_client.get("/api/agents", headers={"Host": "evil.com"})
        assert resp.status == 403

    @pytest.mark.asyncio
    async def test_auth_disabled_by_default(self, client):
        # The plain client has no auth token → endpoints work without a cookie.
        resp = await client.get("/api/agents")
        assert resp.status == 200

    def test_empty_host_rejected_once_trusted_configured(self):
        # A headerless request must not bypass the host gate in remote mode.
        from culture.dashboard.server import _host_allowed

        assert _host_allowed("", frozenset()) is True  # pure-loopback: tolerated
        assert _host_allowed("", frozenset(["mymac.ts.net"])) is False  # remote: rejected
        assert _host_allowed("mymac.ts.net", frozenset(["mymac.ts.net"])) is True
        assert _host_allowed("evil.com", frozenset(["mymac.ts.net"])) is False


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
    async def test_approve_non_string_id_is_400_not_500(self, client):
        # A non-string id must be a clean 400, not an uncaught TypeError -> 500.
        for bad in (123, True, ["x"], {"a": 1}):
            resp = await client.post("/api/approve", json={"id": bad})
            assert resp.status == 400, (bad, resp.status)

    @pytest.mark.asyncio
    async def test_deny_non_string_id_is_400_not_500(self, client):
        resp = await client.post("/api/deny", json={"id": 123, "reason": "x"})
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
