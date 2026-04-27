"""Tests for culture channel CLI with IPC routing."""

import argparse
import asyncio
import json
import os
import tempfile

import pytest

from culture.cli.channel import (
    _require_ipc,
    _try_ipc,
    _valid_nick,
    dispatch,
    register,
)
from culture.cli.shared.ipc import ipc_request
from culture.clients.claude.ipc import encode_message, make_response

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_args(**kwargs):
    """Build a minimal Namespace for channel dispatch."""
    defaults = {"config": "~/.culture/server.yaml"}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


async def _mock_ipc_server(sock_path, handler):
    """Start a mock Unix socket server that handles one request."""

    async def _handler(reader, writer):
        data = await reader.readline()
        msg = json.loads(data)
        resp = handler(msg)
        writer.write(encode_message(resp))
        await writer.drain()
        writer.close()

    srv = await asyncio.start_unix_server(_handler, path=sock_path)
    return srv


# ---------------------------------------------------------------------------
# IPC routing tests
# ---------------------------------------------------------------------------


class TestMessageIpcRouting:
    """Issue #203: culture channel message should route through daemon IPC."""

    def test_message_routes_through_ipc_when_nick_set(self, monkeypatch, capsys):
        """When CULTURE_NICK is set and daemon is reachable, use IPC."""
        sock_dir = tempfile.mkdtemp()
        sock_path = os.path.join(sock_dir, "culture-spark-claude.sock")

        def handler(msg):
            assert msg["type"] == "irc_send"
            assert msg["channel"] == "#general"
            assert msg["message"] == "hello"
            return make_response(msg["id"], ok=True)

        async def _run():
            srv = await asyncio.start_unix_server(
                lambda r, w: _mock_ipc_server_handle(r, w, handler),
                path=sock_path,
            )
            try:
                resp = await ipc_request(sock_path, "irc_send", channel="#general", message="hello")
                assert resp is not None
                assert resp["ok"] is True
            finally:
                srv.close()
                await srv.wait_closed()
                os.unlink(sock_path)

        monkeypatch.setenv("CULTURE_NICK", "spark-claude")
        monkeypatch.setenv("XDG_RUNTIME_DIR", sock_dir)
        asyncio.run(_run())

    def test_message_falls_back_when_no_daemon(self, monkeypatch, capsys):
        """When CULTURE_NICK is set but daemon unreachable, _try_ipc returns None."""
        monkeypatch.setenv("CULTURE_NICK", "spark-claude")
        monkeypatch.setenv("XDG_RUNTIME_DIR", tempfile.mkdtemp())

        # _try_ipc should return None when socket doesn't exist
        result = _try_ipc("irc_send", channel="#general", message="hello")
        assert result is None
        # Issue #302: a stderr warning must accompany the silent fallback.
        capsys.readouterr()  # consume so it doesn't pollute the next test

    def test_message_uses_observer_when_no_nick(self, monkeypatch):
        """When CULTURE_NICK is not set, _try_ipc returns None."""
        monkeypatch.delenv("CULTURE_NICK", raising=False)
        result = _try_ipc("irc_send", channel="#general", message="hello")
        assert result is None


class TestSilentFallbackWarning:
    """Issue #302: warn loudly when CULTURE_NICK is set but IPC failed.

    Before the fix, a daemon/CLI socket-path mismatch on macOS caused
    `culture channel message` to silently fall back to an anonymous peek
    nick. The CLI printed `Sent to #general` either way, so the bug hid
    for two releases. The warning must always (a) name the nick, (b)
    point to the GitHub issue tracker for filing a regression.
    """

    def test_warns_when_daemon_unreachable(self, monkeypatch, capsys):
        """Unreachable daemon → stderr warning containing nick + issues URL."""
        monkeypatch.setenv("CULTURE_NICK", "spark-claude")
        monkeypatch.setenv("XDG_RUNTIME_DIR", tempfile.mkdtemp())

        result = _try_ipc("irc_send", channel="#general", message="hello")

        assert result is None
        err = capsys.readouterr().err
        assert "spark-claude" in err
        assert "unreachable" in err
        assert "https://github.com/agentculture/culture/issues" in err

    def test_warns_when_daemon_returns_not_ok(self, monkeypatch, capsys):
        """Daemon answered with ok=False → warning still fires (silent fallback bug class).

        Uses a stdlib socket server in a background thread so _try_ipc's own
        asyncio.run() does not nest inside an outer event loop.
        """
        import socket
        import threading

        sock_dir = tempfile.mkdtemp()
        sock_path = os.path.join(sock_dir, "culture-spark-claude.sock")

        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(sock_path)
        srv.listen(1)

        def _serve_one():
            conn, _ = srv.accept()
            try:
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                req = json.loads(buf)
                resp = make_response(req["id"], ok=False, error="not joined")
                conn.sendall(encode_message(resp))
            finally:
                conn.close()

        thread = threading.Thread(target=_serve_one, daemon=True)
        thread.start()

        monkeypatch.setenv("CULTURE_NICK", "spark-claude")
        monkeypatch.setenv("XDG_RUNTIME_DIR", sock_dir)
        try:
            result = _try_ipc("irc_send", channel="#general", message="hi")
        finally:
            thread.join(timeout=2.0)
            srv.close()
            os.unlink(sock_path)

        assert result is not None  # response is preserved for callers
        assert result["ok"] is False
        err = capsys.readouterr().err
        assert "spark-claude" in err
        assert "rejected" in err
        assert "not joined" in err
        assert "https://github.com/agentculture/culture/issues" in err

    def test_no_warning_when_nick_unset(self, monkeypatch, capsys):
        """Human use without CULTURE_NICK is the legitimate peek path — no warning."""
        monkeypatch.delenv("CULTURE_NICK", raising=False)

        result = _try_ipc("irc_send", channel="#general", message="hello")

        assert result is None
        assert capsys.readouterr().err == ""

    def test_no_warning_when_nick_invalid(self, monkeypatch, capsys):
        """Invalid nick falls into the same human-use bucket — no warning."""
        monkeypatch.setenv("CULTURE_NICK", "nodash")

        result = _try_ipc("irc_send", channel="#general", message="hello")

        assert result is None
        assert capsys.readouterr().err == ""


class TestNickValidation:
    """Issue #202 review: CULTURE_NICK must match <server>-<agent> format."""

    def test_valid_nick(self):
        assert _valid_nick("spark-claude") is True
        assert _valid_nick("thor-ori") is True
        assert _valid_nick("a-b") is True

    def test_invalid_nick_no_dash(self):
        assert _valid_nick("justanick") is False

    def test_invalid_nick_empty_parts(self):
        assert _valid_nick("-claude") is False
        assert _valid_nick("spark-") is False

    def test_try_ipc_rejects_invalid_nick(self, monkeypatch):
        monkeypatch.setenv("CULTURE_NICK", "nodash")
        result = _try_ipc("irc_send", channel="#general", message="hello")
        assert result is None

    def test_require_ipc_rejects_invalid_nick(self, monkeypatch):
        monkeypatch.setenv("CULTURE_NICK", "nodash")
        with pytest.raises(SystemExit) as exc_info:
            _require_ipc("irc_join", channel="#ops")
        assert exc_info.value.code == 1


class TestRequireIpc:
    """Commands that require CULTURE_NICK should error clearly."""

    def test_join_requires_culture_nick(self, monkeypatch):
        monkeypatch.delenv("CULTURE_NICK", raising=False)
        with pytest.raises(SystemExit) as exc_info:
            _require_ipc("irc_join", channel="#ops")
        assert exc_info.value.code == 1

    def test_compact_requires_culture_nick(self, monkeypatch):
        monkeypatch.delenv("CULTURE_NICK", raising=False)
        with pytest.raises(SystemExit) as exc_info:
            _require_ipc("compact")
        assert exc_info.value.code == 1

    def test_clear_requires_culture_nick(self, monkeypatch):
        monkeypatch.delenv("CULTURE_NICK", raising=False)
        with pytest.raises(SystemExit) as exc_info:
            _require_ipc("clear")
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Subcommand registration tests
# ---------------------------------------------------------------------------


class TestSubcommandRegistration:
    """Issue #202: all channel subcommands should be registered."""

    def test_all_subcommands_registered(self):
        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers()
        register(subs)

        expected = [
            "list",
            "read",
            "message",
            "who",
            "join",
            "part",
            "ask",
            "topic",
            "compact",
            "clear",
        ]
        # Parse each subcommand to verify it's registered
        for cmd in expected:
            # Build minimal args for each command
            argv = ["channel"]
            if cmd in ("list", "compact", "clear"):
                argv.append(cmd)
            elif cmd in ("read", "who", "join", "part"):
                argv.extend([cmd, "#general"])
            elif cmd == "message":
                argv.extend([cmd, "#general", "hello"])
            elif cmd == "ask":
                argv.extend([cmd, "#general", "question?"])
            elif cmd == "topic":
                argv.extend([cmd, "#general"])

            args = parser.parse_args(argv)
            assert args.channel_command == cmd, f"Subcommand {cmd} not registered"

    def test_dispatch_usage_lists_all_commands(self, capsys):
        args = _make_args(channel_command=None)
        with pytest.raises(SystemExit):
            dispatch(args)
        captured = capsys.readouterr()
        for cmd in [
            "list",
            "read",
            "message",
            "who",
            "join",
            "part",
            "ask",
            "topic",
            "compact",
            "clear",
        ]:
            assert cmd in captured.err, f"Missing {cmd} in usage output"


# ---------------------------------------------------------------------------
# Helper for async mock server
# ---------------------------------------------------------------------------


async def _mock_ipc_server_handle(reader, writer, handler):
    data = await reader.readline()
    msg = json.loads(data)
    resp = handler(msg)
    writer.write(encode_message(resp))
    await writer.drain()
    writer.close()
