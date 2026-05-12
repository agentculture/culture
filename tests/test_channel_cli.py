"""Tests for culture channel CLI with IPC routing."""

import argparse
import asyncio
import json
import os
import sys
import tempfile

import pytest

from culture.cli.channel import (
    _require_ipc,
    _try_ipc,
    _valid_nick,
    _warn_observer_fallback,
    dispatch,
    register,
)
from culture.cli.shared.ipc import ipc_request
from culture.clients.shared.ipc import encode_message, make_response

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

    def test_message_falls_back_when_no_daemon(self, monkeypatch):
        """When CULTURE_NICK is set but daemon unreachable, _try_ipc returns None."""
        monkeypatch.setenv("CULTURE_NICK", "spark-claude")
        monkeypatch.setenv("XDG_RUNTIME_DIR", tempfile.mkdtemp())

        # _try_ipc itself stays silent — the warning is emitted by the
        # observer-fallback caller (see TestObserverFallbackWarning).
        result = _try_ipc("irc_send", channel="#general", message="hello")
        assert result is None

    def test_message_uses_observer_when_no_nick(self, monkeypatch):
        """When CULTURE_NICK is not set, _try_ipc returns None."""
        monkeypatch.delenv("CULTURE_NICK", raising=False)
        result = _try_ipc("irc_send", channel="#general", message="hello")
        assert result is None


class TestObserverFallbackWarning:
    """Issue #302: the three observer-fallback callers must warn loudly.

    Before the fix, a daemon/CLI socket-path mismatch on macOS caused
    `culture channel message` to silently fall back to an anonymous peek
    connection. The CLI printed `Sent to #general` either way, so the
    bug hid for two releases. The warning must (a) name the nick that
    was attempted, (b) name the operation, and (c) point at the GitHub
    issue tracker so the next reproducer takes seconds to file.

    The warning lives in `_warn_observer_fallback`, called only by
    `_cmd_message`, `_cmd_list`, and `_cmd_read`. `_topic_read` and the
    `_require_ipc` commands exit on failure instead, so no spurious
    "falling back" notice contradicts their actual error.
    """

    def test_warns_when_nick_set_and_daemon_unreachable(self, monkeypatch, capsys):
        """CULTURE_NICK set + IPC down → stderr warning naming op + nick + issues URL."""
        monkeypatch.setenv("CULTURE_NICK", "spark-claude")
        monkeypatch.setenv("XDG_RUNTIME_DIR", tempfile.mkdtemp())

        _warn_observer_fallback("channel message")

        err = capsys.readouterr().err
        assert "spark-claude" in err
        assert "channel message" in err
        assert "Falling back to observer" in err
        assert "https://github.com/agentculture/culture/issues" in err

    def test_warning_text_is_operation_specific(self, monkeypatch, capsys):
        """Each caller passes its own operation name; helper renders it verbatim."""
        monkeypatch.setenv("CULTURE_NICK", "spark-claude")
        monkeypatch.setenv("XDG_RUNTIME_DIR", tempfile.mkdtemp())

        _warn_observer_fallback("channel list")
        err = capsys.readouterr().err
        assert "channel list" in err
        assert "channel message" not in err  # no leakage from sibling callers

    def test_no_warning_when_nick_unset(self, monkeypatch, capsys):
        """Human use without CULTURE_NICK is the legitimate observer path — no warning."""
        monkeypatch.delenv("CULTURE_NICK", raising=False)

        _warn_observer_fallback("channel message")

        assert capsys.readouterr().err == ""

    def test_no_warning_when_nick_invalid(self, monkeypatch, capsys):
        """Invalid nick falls into the same human-use bucket — no warning."""
        monkeypatch.setenv("CULTURE_NICK", "nodash")

        _warn_observer_fallback("channel message")

        assert capsys.readouterr().err == ""

    def test_topic_read_does_not_warn(self, monkeypatch, capsys):
        """_topic_read uses _try_ipc but exits on failure — no fallback, no warning.

        Regression guard against the Qodo/Copilot review on PR #304: the
        previous design auto-warned inside `_try_ipc` and printed a
        misleading 'falling back' notice for `topic` (which actually exits).
        """
        from culture.cli.channel import _topic_read

        monkeypatch.setenv("CULTURE_NICK", "spark-claude")
        monkeypatch.setenv("XDG_RUNTIME_DIR", tempfile.mkdtemp())

        with pytest.raises(SystemExit):
            _topic_read("#general")

        err = capsys.readouterr().err
        assert "Falling back" not in err
        assert "topic query requires" in err


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


# ---------------------------------------------------------------------------
# Phase 3a — handler coverage (channel.py was 44%, target ~80%)
# ---------------------------------------------------------------------------
#
# The tests above pin down the IPC routing/fallback policy; the additions
# below cover the body of every _cmd_* handler so the project-wide coverage
# floor can rise. They use the same _make_args helper and the existing
# CULTURE_NICK + XDG_RUNTIME_DIR monkeypatch pattern for IPC paths, plus a
# small _StubObserver class for observer-fallback paths so we don't have to
# stand up a peek connection.


class _StubObserver:
    """In-memory stand-in for IRCObserver — records calls and returns canned data."""

    def __init__(
        self,
        channels=None,
        messages=None,
        who_nicks=None,
        sends=None,
    ):
        self._channels = channels or []
        self._messages = messages or []
        self._who_nicks = who_nicks or []
        self._sends: list[tuple[str, str]] = [] if sends is None else sends

    async def list_channels(self):
        return list(self._channels)

    async def read_channel(self, channel, limit=50):
        return [f"<{m['nick']}> {m['text']}" for m in self._messages]

    async def who(self, target):
        return list(self._who_nicks)

    async def send_message(self, channel, text):
        self._sends.append((channel, text))


def _stub_ipc(monkeypatch, *, try_resp=None, require_resp=None, capture=None):
    """Patch `_try_ipc` and `_require_ipc` at the channel module boundary.

    `_cmd_*` handlers call `asyncio.run(ipc_request(...))` internally, which
    cannot be re-entered from a test-owned event loop — so we patch the
    explicit IPC boundary instead of standing up a Unix-socket fake. The
    recorded requests live in ``capture`` (a list); ``try_resp`` /
    ``require_resp`` can be a dict or a callable ``(msg_type, **kwargs) -> dict``.
    """
    from culture.cli import channel as ch_mod

    captured = capture if capture is not None else []

    def _fake_try(msg_type, **kwargs):
        captured.append((msg_type, kwargs))
        return try_resp(msg_type, **kwargs) if callable(try_resp) else try_resp

    def _fake_require(msg_type, **kwargs):
        captured.append((msg_type, kwargs))
        result = require_resp(msg_type, **kwargs) if callable(require_resp) else require_resp
        if result is None:
            # mirror real _require_ipc behavior on unreachable daemon
            print("Error: cannot reach agent daemon", file=sys.stderr)
            raise SystemExit(1)
        return result

    monkeypatch.setattr(ch_mod, "_try_ipc", _fake_try)
    monkeypatch.setattr(ch_mod, "_require_ipc", _fake_require)
    return captured


class TestIsConnectionError:
    def test_classifies_known_oserror_strings(self):
        from culture.cli.channel import _is_connection_error

        assert _is_connection_error("Timed out") is True
        assert _is_connection_error("Connection refused") is True
        assert _is_connection_error("Connect call failed: [Errno 111]") is True
        assert _is_connection_error("") is True  # empty msg → likely network

    def test_does_not_match_unrelated_errors(self):
        from culture.cli.channel import _is_connection_error

        assert _is_connection_error("Permission denied") is False
        assert _is_connection_error("File not found") is False


class TestInterpretEscapes:
    def test_passthrough_when_no_backslashes(self):
        from culture.cli.channel import _interpret_escapes

        assert _interpret_escapes("hello world") == "hello world"

    def test_converts_n_and_t_sequences(self):
        from culture.cli.channel import _interpret_escapes

        assert _interpret_escapes("a\\nb\\tc") == "a\nb\tc"

    def test_double_backslash_stays_literal(self):
        from culture.cli.channel import _interpret_escapes

        assert _interpret_escapes("\\\\n") == "\\n"

    def test_unknown_escape_is_preserved_verbatim(self):
        from culture.cli.channel import _interpret_escapes

        # \x is not in the supported set — neither half is consumed
        assert _interpret_escapes("a\\xb") == "a\\xb"


class TestDispatchWrapsConnectionError:
    def test_handler_oserror_with_connection_message_exits_with_hint(self, monkeypatch, capsys):
        from culture.cli import channel as ch_mod

        def _boom(_args):
            raise OSError("Connection refused")

        monkeypatch.setitem(
            (
                ch_mod.dispatch.__globals__["handlers"]
                if "handlers" in ch_mod.dispatch.__globals__
                else {}
            ),
            "list",
            _boom,
        )

        # Easier: temporarily replace _cmd_list with a raising stub.
        monkeypatch.setattr(ch_mod, "_cmd_list", _boom)

        with pytest.raises(SystemExit) as exc:
            ch_mod.dispatch(_make_args(channel_command="list"))

        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "cannot connect to IRC server" in err
        assert "culture server start" in err

    def test_handler_raises_unrelated_oserror_propagates(self, monkeypatch):
        from culture.cli import channel as ch_mod

        def _boom(_args):
            raise OSError("Permission denied")

        monkeypatch.setattr(ch_mod, "_cmd_list", _boom)

        with pytest.raises(OSError, match="Permission denied"):
            ch_mod.dispatch(_make_args(channel_command="list"))

    def test_unknown_command_exits(self, capsys):
        from culture.cli.channel import dispatch

        with pytest.raises(SystemExit) as exc:
            dispatch(_make_args(channel_command="frobnicate"))
        assert exc.value.code == 1
        assert "Unknown channel command" in capsys.readouterr().err


class TestCmdListBody:
    def test_ipc_path_renders_channel_list(self, monkeypatch, capsys):
        from culture.cli.channel import _cmd_list

        captured = _stub_ipc(
            monkeypatch,
            try_resp={"ok": True, "data": {"channels": ["#ops", "#general"]}},
        )

        _cmd_list(_make_args(channel_command="list"))

        assert captured == [("irc_channels", {})]
        out = capsys.readouterr().out
        assert "Active channels" in out and "#ops" in out and "#general" in out

    def test_ipc_path_empty_list(self, monkeypatch, capsys):
        from culture.cli.channel import _cmd_list

        _stub_ipc(monkeypatch, try_resp={"ok": True, "data": {"channels": []}})
        _cmd_list(_make_args(channel_command="list"))
        assert "No active channels" in capsys.readouterr().out

    def test_observer_fallback_when_no_nick(self, monkeypatch, capsys):
        from culture.cli import channel as ch_mod

        monkeypatch.delenv("CULTURE_NICK", raising=False)
        monkeypatch.setattr(
            ch_mod, "get_observer", lambda _cfg: _StubObserver(channels=["#a", "#b"])
        )

        ch_mod._cmd_list(_make_args(channel_command="list"))

        out = capsys.readouterr().out
        assert "#a" in out and "#b" in out

    def test_observer_fallback_empty(self, monkeypatch, capsys):
        from culture.cli import channel as ch_mod

        monkeypatch.delenv("CULTURE_NICK", raising=False)
        monkeypatch.setattr(ch_mod, "get_observer", lambda _cfg: _StubObserver())

        ch_mod._cmd_list(_make_args(channel_command="list"))

        assert "No active channels" in capsys.readouterr().out


class TestCmdReadBody:
    def test_rejects_empty_target(self, capsys):
        from culture.cli.channel import _cmd_read

        with pytest.raises(SystemExit) as exc:
            _cmd_read(_make_args(channel_command="read", target="   ", limit=50))
        assert exc.value.code == 1
        assert "channel name cannot be empty" in capsys.readouterr().err

    def test_ipc_path_prepends_hash_to_target(self, monkeypatch, capsys):
        from culture.cli.channel import _cmd_read

        captured = _stub_ipc(
            monkeypatch,
            try_resp={"ok": True, "data": {"messages": [{"nick": "ada", "text": "hi"}]}},
        )

        _cmd_read(_make_args(channel_command="read", target="ops", limit=50))

        assert captured == [("irc_read", {"channel": "#ops", "limit": 50})]
        assert "<ada> hi" in capsys.readouterr().out

    def test_ipc_path_empty_messages(self, monkeypatch, capsys):
        from culture.cli.channel import _cmd_read

        _stub_ipc(monkeypatch, try_resp={"ok": True, "data": {"messages": []}})
        _cmd_read(_make_args(channel_command="read", target="#ops", limit=10))
        assert "No messages in #ops" in capsys.readouterr().out

    def test_observer_fallback_when_no_nick(self, monkeypatch, capsys):
        from culture.cli import channel as ch_mod

        monkeypatch.delenv("CULTURE_NICK", raising=False)
        obs = _StubObserver(messages=[{"nick": "ada", "text": "hi"}])
        monkeypatch.setattr(ch_mod, "get_observer", lambda _cfg: obs)

        ch_mod._cmd_read(_make_args(channel_command="read", target="#ops", limit=50))

        assert "<ada> hi" in capsys.readouterr().out

    def test_observer_fallback_empty(self, monkeypatch, capsys):
        from culture.cli import channel as ch_mod

        monkeypatch.delenv("CULTURE_NICK", raising=False)
        monkeypatch.setattr(ch_mod, "get_observer", lambda _cfg: _StubObserver())

        ch_mod._cmd_read(_make_args(channel_command="read", target="#ops", limit=50))

        assert "No messages in #ops" in capsys.readouterr().out


class TestCmdMessageBody:
    def test_rejects_empty_target(self, capsys):
        from culture.cli.channel import _cmd_message

        with pytest.raises(SystemExit) as exc:
            _cmd_message(
                _make_args(channel_command="message", target="   ", text="hello", create=False)
            )
        assert exc.value.code == 1

    def test_rejects_empty_text(self, capsys):
        from culture.cli.channel import _cmd_message

        with pytest.raises(SystemExit) as exc:
            _cmd_message(
                _make_args(channel_command="message", target="#ops", text="   ", create=False)
            )
        assert exc.value.code == 1
        assert "message text cannot be empty" in capsys.readouterr().err

    def test_rejects_text_with_only_escaped_newlines(self, capsys):
        from culture.cli.channel import _cmd_message

        with pytest.raises(SystemExit) as exc:
            _cmd_message(
                _make_args(channel_command="message", target="#ops", text="\\n\\n", create=False)
            )
        assert exc.value.code == 1
        assert "no non-empty line" in capsys.readouterr().err

    def test_rejects_nonexistent_channel_without_create(self, monkeypatch, capsys):
        from culture.cli import channel as ch_mod

        monkeypatch.setattr(ch_mod, "get_observer", lambda _cfg: _StubObserver(channels=[]))

        with pytest.raises(SystemExit) as exc:
            ch_mod._cmd_message(
                _make_args(channel_command="message", target="#typo", text="hello", create=False)
            )
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "does not exist" in err
        assert "--create" in err

    def test_ipc_send_happy_path(self, monkeypatch, capsys):
        from culture.cli import channel as ch_mod

        monkeypatch.setattr(ch_mod, "get_observer", lambda _cfg: _StubObserver(channels=["#ops"]))
        captured = _stub_ipc(monkeypatch, try_resp={"ok": True})

        ch_mod._cmd_message(
            _make_args(channel_command="message", target="#ops", text="hello", create=False)
        )

        assert captured == [("irc_send", {"channel": "#ops", "message": "hello"})]
        assert "Sent to #ops" in capsys.readouterr().out

    def test_create_flag_skips_channel_existence_check(self, monkeypatch, capsys):
        """With --create, we don't probe the observer; the send proceeds."""
        from culture.cli import channel as ch_mod

        # If the implementation regresses and probes the observer with --create,
        # the test fails noisily (asserts False).
        def _observer_should_not_be_called(_cfg):  # pragma: no cover - guard
            raise AssertionError("observer should not be consulted with --create")

        monkeypatch.setattr(ch_mod, "get_observer", _observer_should_not_be_called)
        _stub_ipc(monkeypatch, try_resp={"ok": True})

        ch_mod._cmd_message(
            _make_args(channel_command="message", target="#typo", text="hi", create=True)
        )

        assert "Sent to #typo" in capsys.readouterr().out

    def test_observer_send_fallback_when_no_nick(self, monkeypatch, capsys):
        from culture.cli import channel as ch_mod

        monkeypatch.delenv("CULTURE_NICK", raising=False)
        obs = _StubObserver(channels=["#ops"])
        monkeypatch.setattr(ch_mod, "get_observer", lambda _cfg: obs)

        ch_mod._cmd_message(
            _make_args(channel_command="message", target="#ops", text="hi", create=False)
        )

        assert obs._sends == [("#ops", "hi")]
        assert "Sent to #ops" in capsys.readouterr().out


class TestCmdWho:
    def test_rejects_empty_target(self, capsys):
        from culture.cli.channel import _cmd_who

        with pytest.raises(SystemExit) as exc:
            _cmd_who(_make_args(channel_command="who", target="   "))
        assert exc.value.code == 1

    def test_renders_user_list(self, monkeypatch, capsys):
        from culture.cli import channel as ch_mod

        obs = _StubObserver(who_nicks=["ada", "bob"])
        monkeypatch.setattr(ch_mod, "get_observer", lambda _cfg: obs)

        ch_mod._cmd_who(_make_args(channel_command="who", target="#ops"))

        out = capsys.readouterr().out
        assert "Users in #ops" in out and "ada" in out and "bob" in out

    def test_empty_channel(self, monkeypatch, capsys):
        from culture.cli import channel as ch_mod

        monkeypatch.setattr(ch_mod, "get_observer", lambda _cfg: _StubObserver())

        ch_mod._cmd_who(_make_args(channel_command="who", target="#empty"))

        assert "No users in #empty" in capsys.readouterr().out


class TestCmdJoinPart:
    def test_join_happy_path(self, monkeypatch, capsys):
        from culture.cli.channel import _cmd_join

        captured = _stub_ipc(monkeypatch, require_resp={"ok": True})

        _cmd_join(_make_args(channel_command="join", target="ops"))

        assert captured == [("irc_join", {"channel": "#ops"})]
        assert "Joined #ops" in capsys.readouterr().out

    def test_join_failure_exits_with_error(self, monkeypatch, capsys):
        from culture.cli.channel import _cmd_join

        _stub_ipc(monkeypatch, require_resp={"ok": False, "error": "banned"})

        with pytest.raises(SystemExit) as exc:
            _cmd_join(_make_args(channel_command="join", target="#ops"))
        assert exc.value.code == 1
        assert "banned" in capsys.readouterr().err

    def test_part_happy_path(self, monkeypatch, capsys):
        from culture.cli.channel import _cmd_part

        captured = _stub_ipc(monkeypatch, require_resp={"ok": True})

        _cmd_part(_make_args(channel_command="part", target="#ops"))

        assert captured == [("irc_part", {"channel": "#ops"})]
        assert "Left #ops" in capsys.readouterr().out

    def test_part_failure_exits_with_error(self, monkeypatch, capsys):
        from culture.cli.channel import _cmd_part

        _stub_ipc(monkeypatch, require_resp={"ok": False, "error": "not in channel"})

        with pytest.raises(SystemExit) as exc:
            _cmd_part(_make_args(channel_command="part", target="#ops"))
        assert exc.value.code == 1
        assert "not in channel" in capsys.readouterr().err


class TestCmdAsk:
    def test_rejects_empty_target(self, capsys):
        from culture.cli.channel import _cmd_ask

        with pytest.raises(SystemExit) as exc:
            _cmd_ask(_make_args(channel_command="ask", target="   ", text="?", timeout=30))
        assert exc.value.code == 1

    def test_rejects_empty_text(self, capsys):
        from culture.cli.channel import _cmd_ask

        with pytest.raises(SystemExit) as exc:
            _cmd_ask(_make_args(channel_command="ask", target="#ops", text="   ", timeout=30))
        assert exc.value.code == 1
        assert "question text cannot be empty" in capsys.readouterr().err

    def test_happy_path_prints_json(self, monkeypatch, capsys):
        from culture.cli.channel import _cmd_ask

        captured = _stub_ipc(
            monkeypatch,
            require_resp={"ok": True, "data": {"answer": "42"}},
        )

        _cmd_ask(_make_args(channel_command="ask", target="#ops", text="huh?", timeout=30))

        assert captured == [
            ("irc_ask", {"channel": "#ops", "message": "huh?", "timeout": 30}),
        ]
        out = capsys.readouterr().out
        assert '"ok": true' in out  # json.dumps output
        assert '"answer"' in out

    def test_failure_exits(self, monkeypatch, capsys):
        from culture.cli.channel import _cmd_ask

        _stub_ipc(monkeypatch, require_resp={"ok": False, "error": "no answer"})
        with pytest.raises(SystemExit) as exc:
            _cmd_ask(_make_args(channel_command="ask", target="#ops", text="?", timeout=30))
        assert exc.value.code == 1


class TestCmdTopic:
    def test_topic_rejects_empty_target(self, capsys):
        from culture.cli.channel import _cmd_topic

        with pytest.raises(SystemExit) as exc:
            _cmd_topic(_make_args(channel_command="topic", target="   ", text=None))
        assert exc.value.code == 1

    def test_topic_set_routes_to_topic_set(self, monkeypatch, capsys):
        from culture.cli.channel import _cmd_topic

        captured = _stub_ipc(monkeypatch, require_resp={"ok": True})

        _cmd_topic(_make_args(channel_command="topic", target="#ops", text="new topic"))

        assert captured == [("irc_topic", {"channel": "#ops", "topic": "new topic"})]
        assert "Topic set for #ops" in capsys.readouterr().out

    def test_topic_set_failure_exits(self, monkeypatch, capsys):
        from culture.cli.channel import _topic_set

        _stub_ipc(monkeypatch, require_resp={"ok": False, "error": "not op"})
        with pytest.raises(SystemExit) as exc:
            _topic_set("#ops", "anything")
        assert exc.value.code == 1
        assert "not op" in capsys.readouterr().err

    def test_topic_read_returns_topic_value(self, monkeypatch, capsys):
        from culture.cli.channel import _cmd_topic

        _stub_ipc(monkeypatch, try_resp={"ok": True, "data": {"topic": "release week"}})

        _cmd_topic(_make_args(channel_command="topic", target="#ops", text=None))

        assert "Topic for #ops: release week" in capsys.readouterr().out

    def test_topic_read_no_topic_set(self, monkeypatch, capsys):
        from culture.cli.channel import _topic_read

        _stub_ipc(monkeypatch, try_resp={"ok": True, "data": {"topic": ""}})
        _topic_read("#ops")
        assert "No topic set for #ops" in capsys.readouterr().out

    def test_topic_read_async_pending(self, monkeypatch, capsys):
        from culture.cli.channel import _topic_read

        # daemon ACKs but response carries no topic — async result expected
        _stub_ipc(monkeypatch, try_resp={"ok": True, "data": {}})
        _topic_read("#ops")
        assert "result arrives asynchronously" in capsys.readouterr().out

    def test_topic_read_exits_when_daemon_unreachable(self, monkeypatch, capsys):
        """_topic_read uses _try_ipc but exits on None response (not silent fallback)."""
        from culture.cli.channel import _topic_read

        _stub_ipc(monkeypatch, try_resp=None)
        with pytest.raises(SystemExit) as exc:
            _topic_read("#ops")
        assert exc.value.code == 1
        assert "topic query requires" in capsys.readouterr().err


class TestCmdCompactClear:
    def test_compact_happy_path(self, monkeypatch, capsys):
        from culture.cli.channel import _cmd_compact

        captured = _stub_ipc(monkeypatch, require_resp={"ok": True})

        _cmd_compact(_make_args(channel_command="compact"))

        assert captured == [("compact", {})]
        assert "Context window compacted" in capsys.readouterr().out

    def test_compact_failure_exits(self, monkeypatch, capsys):
        from culture.cli.channel import _cmd_compact

        _stub_ipc(monkeypatch, require_resp={"ok": False, "error": "busy"})
        with pytest.raises(SystemExit) as exc:
            _cmd_compact(_make_args(channel_command="compact"))
        assert exc.value.code == 1

    def test_clear_happy_path(self, monkeypatch, capsys):
        from culture.cli.channel import _cmd_clear

        captured = _stub_ipc(monkeypatch, require_resp={"ok": True})
        _cmd_clear(_make_args(channel_command="clear"))
        assert captured == [("clear", {})]
        assert "Context window cleared" in capsys.readouterr().out

    def test_clear_failure_exits(self, monkeypatch, capsys):
        from culture.cli.channel import _cmd_clear

        _stub_ipc(monkeypatch, require_resp={"ok": False, "error": "not ready"})
        with pytest.raises(SystemExit) as exc:
            _cmd_clear(_make_args(channel_command="clear"))
        assert exc.value.code == 1


class TestRequireIpcDaemonUnreachable:
    def test_require_ipc_exits_when_daemon_response_is_none(self, monkeypatch, capsys):
        """CULTURE_NICK is valid but no socket exists → ipc_request returns None."""
        from culture.cli.channel import _require_ipc

        monkeypatch.setenv("CULTURE_NICK", "spark-claude")
        monkeypatch.setenv("XDG_RUNTIME_DIR", tempfile.mkdtemp())  # no daemon

        with pytest.raises(SystemExit) as exc:
            _require_ipc("irc_join", channel="#ops")
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "cannot reach agent daemon" in err
        assert "culture agent status spark-claude" in err
