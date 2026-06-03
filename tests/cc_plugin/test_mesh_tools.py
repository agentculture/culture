"""NT-9 — CC plugin ``mesh ...`` tools route through bridge IPC.

Every public ``mesh_*`` function in ``cc_plugin.tools`` ultimately calls
``_bridge_client.request(nick, verb, **payload)``. These tests monkey-
patch that single seam, capture each call, and assert the verb +
payload the bridge sees. They deliberately do not start a bridge — the
contract under test is the plugin-side shape, not the bridge side.
"""

from __future__ import annotations

from typing import Any

import pytest

from culture.clients.claude.cc_plugin import tools as mesh_tools


_FAKE_NICK = "test-cc"


@pytest.fixture
def capture(monkeypatch):
    """Monkey-patch ``_bridge_client.request`` and ``_own_nick`` so calls
    are recorded instead of opening a real Unix socket.

    Returns a list — each entry is ``(nick, verb, payload)`` for one
    ``_bridge_client.request`` call.
    """
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def _fake_request(nick: str, verb: str, **payload: Any) -> dict[str, Any]:
        calls.append((nick, verb, payload))
        return {"ok": True, "id": "fake-id"}

    monkeypatch.setattr(mesh_tools._bridge_client, "request", _fake_request)
    monkeypatch.setattr(mesh_tools, "_own_nick", lambda: _FAKE_NICK)
    return calls


class TestMeshDM:
    def test_mesh_dm_routes_to_bridge(self, capture):
        result = mesh_tools.mesh_dm("peer-nick", "hello world")
        assert result == {"ok": True, "id": "fake-id"}
        assert len(capture) == 1
        nick, verb, payload = capture[0]
        assert nick == _FAKE_NICK
        # The bridge's DM verb is ``irc_send`` with the target nick as
        # the ``channel`` slot — matches the implementation in tools.py.
        assert verb == "irc_send"
        assert payload == {"channel": "peer-nick", "message": "hello world"}


class TestMeshSend:
    def test_mesh_send_routes_to_bridge(self, capture):
        result = mesh_tools.mesh_send("#general", "hi everyone")
        assert result == {"ok": True, "id": "fake-id"}
        assert len(capture) == 1
        nick, verb, payload = capture[0]
        assert nick == _FAKE_NICK
        assert verb == "irc_send"
        assert payload == {"channel": "#general", "message": "hi everyone"}

    def test_mesh_send_rejects_non_channel(self, capture):
        """Channels MUST start with ``#`` — local-validation rejection
        must NOT touch the bridge."""
        result = mesh_tools.mesh_send("general", "hi")
        assert result == {"ok": False, "error": "channel must start with '#'"}
        assert capture == []


class TestMeshInbox:
    def test_mesh_inbox_routes_to_bridge(self, capture):
        mesh_tools.mesh_inbox()
        assert len(capture) == 1
        nick, verb, payload = capture[0]
        assert nick == _FAKE_NICK
        assert verb == "inbox_drain"
        assert payload == {}


class TestMeshStatus:
    def test_mesh_status_routes_to_bridge(self, capture):
        mesh_tools.mesh_status()
        assert len(capture) == 1
        nick, verb, payload = capture[0]
        assert nick == _FAKE_NICK
        assert verb == "status"
        assert payload == {}


class TestMeshApprove:
    def test_mesh_approve_once(self, capture):
        mesh_tools.mesh_approve("req-123")
        assert len(capture) == 1
        nick, verb, payload = capture[0]
        assert nick == _FAKE_NICK
        assert verb == "perm_approve"
        assert payload == {"id": "req-123", "scope": "once"}

    def test_mesh_approve_with_input_regex(self, capture):
        mesh_tools.mesh_approve("req-123", input_regex="^git ", scope="always")
        assert len(capture) == 1
        _nick, verb, payload = capture[0]
        assert verb == "perm_approve"
        assert payload == {"id": "req-123", "scope": "always", "input_regex": "^git "}
