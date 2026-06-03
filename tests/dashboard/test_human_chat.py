"""Tests for the human-chat panel backend (Phase 7.5 / AD-5).

The dashboard sends a PRIVMSG from a HUMAN nick to any agent on the
mesh. The wire-level helper opens a transient IRC connection registered
as the human's nick; tests inject a fake via ``_human_dm_sender`` so we
don't need a running IRCd.
"""

from __future__ import annotations

from tests._sdk_stub import install_claude_sdk_stub

install_claude_sdk_stub()

import os  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
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


class _FakeBridge:
    """Records DM sends instead of opening an IRC connection."""

    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []
        self.raise_next: BaseException | None = None

    async def __call__(self, human_nick, target_nick, text):
        if self.raise_next is not None:
            exc, self.raise_next = self.raise_next, None
            raise exc
        self.sent.append((human_nick, target_nick, text))


class TestHumanChatPanel:
    @pytest.mark.asyncio
    async def test_dm_sends_with_human_as_sender(self, client, monkeypatch):
        from culture.dashboard import server

        bridge = _FakeBridge()
        monkeypatch.setattr(server, "_human_dm_sender", lambda req: bridge)
        resp = await client.post(
            "/api/mesh/dm",
            json={"human_nick": "edo", "target_nick": "local-w", "text": "hello"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True
        assert data["from"] == "edo"
        assert data["to"] == "local-w"
        # Wire-level assertion: the bridge was called once with the human's
        # nick as the source — that's the AD-5 contract (humans are first-
        # class on the mesh; their dashboard DMs are sourced as themselves).
        assert bridge.sent == [("edo", "local-w", "hello")]

    @pytest.mark.asyncio
    async def test_dm_missing_human_nick_400(self, client):
        resp = await client.post(
            "/api/mesh/dm",
            json={"target_nick": "local-w", "text": "x"},
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_dm_missing_target_nick_400(self, client):
        resp = await client.post(
            "/api/mesh/dm",
            json={"human_nick": "edo", "text": "x"},
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_dm_empty_text_400(self, client):
        resp = await client.post(
            "/api/mesh/dm",
            json={"human_nick": "edo", "target_nick": "local-w", "text": "   "},
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_dm_invalid_human_nick_400(self, client):
        # Same nick-validation regex as every other path that builds a
        # filesystem / IRC target from a user-supplied nick.
        resp = await client.post(
            "/api/mesh/dm",
            json={"human_nick": "../etc", "target_nick": "local-w", "text": "x"},
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_dm_invalid_target_nick_400(self, client):
        resp = await client.post(
            "/api/mesh/dm",
            json={"human_nick": "edo", "target_nick": "../etc", "text": "x"},
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_dm_bridge_failure_502(self, client, monkeypatch):
        from culture.dashboard import server

        bridge = _FakeBridge()
        bridge.raise_next = ConnectionError("mesh down")
        monkeypatch.setattr(server, "_human_dm_sender", lambda req: bridge)
        resp = await client.post(
            "/api/mesh/dm",
            json={"human_nick": "edo", "target_nick": "local-w", "text": "hi"},
        )
        assert resp.status == 502
        data = await resp.json()
        assert "could not reach" in data["error"]

    @pytest.mark.asyncio
    async def test_dm_cross_origin_blocked(self, client):
        # Same CSRF / DNS-rebinding gate as every other POST.
        resp = await client.post(
            "/api/mesh/dm",
            json={"human_nick": "edo", "target_nick": "local-w", "text": "x"},
            headers={"Origin": "http://evil.com"},
        )
        assert resp.status == 403


class TestCRLFInjectionGuard:
    """RC-3: the project has a documented history of CRLF-injection bugs
    (commit 317a591). Phase 7.5 added a new IRC-line emission path
    (_send_human_dm) — make sure CR / LF / NUL in the text field can't
    escape the PRIVMSG and inject arbitrary IRC commands."""

    @pytest.mark.asyncio
    async def test_text_with_cr_rejected_400(self, client, monkeypatch):
        from culture.dashboard import server

        bridge = _FakeBridge()
        monkeypatch.setattr(server, "_human_dm_sender", lambda req: bridge)
        # Classic CRLF injection: the attacker tries to terminate the
        # PRIVMSG and start a KICK from the human_nick session.
        resp = await client.post(
            "/api/mesh/dm",
            json={
                "human_nick": "edo",
                "target_nick": "local-w",
                "text": "hi\r\nKICK #boss someone :pwned",
            },
        )
        assert resp.status == 400
        data = await resp.json()
        assert "forbidden control characters" in data["error"]
        # And nothing reached the bridge.
        assert bridge.sent == []

    @pytest.mark.asyncio
    async def test_text_with_lf_only_rejected_400(self, client, monkeypatch):
        from culture.dashboard import server

        bridge = _FakeBridge()
        monkeypatch.setattr(server, "_human_dm_sender", lambda req: bridge)
        # Bare \n is also forbidden (some IRC servers/clients are lenient
        # and accept bare LF as a line terminator).
        resp = await client.post(
            "/api/mesh/dm",
            json={
                "human_nick": "edo",
                "target_nick": "local-w",
                "text": "hi\nJOIN #secret",
            },
        )
        assert resp.status == 400
        assert bridge.sent == []

    @pytest.mark.asyncio
    async def test_text_with_nul_rejected_400(self, client, monkeypatch):
        from culture.dashboard import server

        bridge = _FakeBridge()
        monkeypatch.setattr(server, "_human_dm_sender", lambda req: bridge)
        # NUL byte rejected (RFC 2812 forbids it; some IRCd dispatchers
        # split on NUL).
        resp = await client.post(
            "/api/mesh/dm",
            json={
                "human_nick": "edo",
                "target_nick": "local-w",
                "text": "hi\x00mode +o evil",
            },
        )
        assert resp.status == 400
        assert bridge.sent == []

    @pytest.mark.asyncio
    async def test_text_over_400_chars_truncated_not_rejected(
        self, client, monkeypatch
    ):
        from culture.dashboard import server

        bridge = _FakeBridge()
        monkeypatch.setattr(server, "_human_dm_sender", lambda req: bridge)
        # RFC 2812 caps IRC lines at 512 bytes total. After
        # "PRIVMSG <nick> :" overhead, we cap text at 400 chars and
        # TRUNCATE rather than reject (UX choice — paste-heavy users).
        long_text = "x" * 600
        resp = await client.post(
            "/api/mesh/dm",
            json={
                "human_nick": "edo",
                "target_nick": "local-w",
                "text": long_text,
            },
        )
        assert resp.status == 200
        assert len(bridge.sent) == 1
        sent_text = bridge.sent[0][2]
        assert len(sent_text) == 400
        assert sent_text == "x" * 400

    @pytest.mark.asyncio
    async def test_defense_in_depth_send_helper_refuses_tainted_text(
        self, home, monkeypatch
    ):
        """Even if a future refactor bypasses the route handler's check,
        the _send_human_dm helper itself re-validates with
        assert_safe_irc_line as a last line of defense."""
        from culture.dashboard import server as srv

        class _FakeReq:
            app: dict = {srv._CONFIG_PATH: os.path.join(str(home), "server.yaml")}

            def __init__(self):
                pass

        # Skip the route handler entirely; call the helper directly.
        with pytest.raises(ConnectionError, match="CRLF guard"):
            await srv._send_human_dm(
                _FakeReq(), "edo", "local-w", "tainted\r\nKICK #boss x"
            )
