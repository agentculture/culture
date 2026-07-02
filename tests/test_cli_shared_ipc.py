"""Tests for culture_core.cli.shared.ipc — Unix socket IPC + observer factory.

Per CLAUDE.md: "No mocks in server/integration tests — tests spin up real
Unix sockets". These tests use `asyncio.start_unix_server` in tmp_path
to exercise the IPC happy/error paths.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from culture_core.cli.shared.ipc import (
    agent_socket_path,
    get_observer,
    ipc_request,
    ipc_shutdown,
)

# ---------------------------------------------------------------------------
# agent_socket_path
# ---------------------------------------------------------------------------


def test_agent_socket_path_uses_runtime_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "culture_core.cli.shared.constants.culture_runtime_dir", lambda: str(tmp_path)
    )
    assert agent_socket_path("spark-claude") == str(tmp_path / "culture-spark-claude.sock")


# ---------------------------------------------------------------------------
# ipc_request — real Unix socket server
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ipc_request_happy_response(tmp_path):
    """Real Unix socket server returns a response → ipc_request decodes it."""
    sock_path = tmp_path / "happy.sock"

    async def handler(reader, writer):
        from culture_core.clients.shared.ipc import decode_message, encode_message

        try:
            data = await reader.readline()
            req = decode_message(data)
            assert req is not None
            writer.write(
                encode_message({"type": "response", "ok": True, "data": {"echo": req.get("type")}})
            )
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_unix_server(handler, path=str(sock_path))
    try:
        resp = await ipc_request(str(sock_path), "ping")
        assert resp is not None
        assert resp["ok"] is True
        assert resp["data"] == {"echo": "ping"}
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_ipc_request_socket_missing_returns_none(tmp_path):
    """Connecting to a non-existent socket → return None (no raise)."""
    sock_path = tmp_path / "missing.sock"
    assert await ipc_request(str(sock_path), "ping") is None


@pytest.mark.asyncio
async def test_ipc_request_server_drops_returns_none(tmp_path):
    """Peer reads the request then closes without responding (EOF) →
    `ipc_request` returns None promptly via the EOF guard in the read
    loop (`if not data: return None`).

    The handler reads the request first so the client's `drain()`
    succeeds — that way the connection isn't broken on the write side,
    and the client genuinely reaches `readline()` and sees b"" (EOF).
    Without the EOF guard `ipc_request` would busy-loop until its 15s
    deadline; this test wraps it in a 3s `wait_for` to pin that the
    early-return path is taken.
    """
    sock_path = tmp_path / "drop.sock"

    async def handler(reader, writer):
        # Read the client's request first so its write/drain succeeds,
        # then close the writer to deliver EOF on the read side.
        try:
            await reader.readline()
        finally:
            writer.close()

    server = await asyncio.start_unix_server(handler, path=str(sock_path))
    try:
        resp = await asyncio.wait_for(ipc_request(str(sock_path), "ping"), timeout=3.0)
        assert resp is None
    finally:
        server.close()
        await server.wait_closed()


# ---------------------------------------------------------------------------
# ipc_shutdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ipc_shutdown_returns_true_when_response_ok(tmp_path):
    sock_path = tmp_path / "shutdown.sock"

    async def handler(reader, writer):
        from culture_core.clients.shared.ipc import encode_message

        try:
            await reader.readline()
            writer.write(encode_message({"type": "response", "ok": True, "data": {}}))
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_unix_server(handler, path=str(sock_path))
    try:
        assert await ipc_shutdown(str(sock_path)) is True
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_ipc_shutdown_returns_false_when_response_not_ok(tmp_path):
    sock_path = tmp_path / "shutdown-fail.sock"

    async def handler(reader, writer):
        from culture_core.clients.shared.ipc import encode_message

        try:
            await reader.readline()
            writer.write(encode_message({"type": "response", "ok": False, "data": {}}))
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_unix_server(handler, path=str(sock_path))
    try:
        assert await ipc_shutdown(str(sock_path)) is False
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_ipc_shutdown_returns_false_when_socket_missing(tmp_path):
    sock_path = tmp_path / "ghost.sock"
    assert await ipc_shutdown(str(sock_path)) is False


# ---------------------------------------------------------------------------
# get_observer — config-driven IRCObserver factory
# ---------------------------------------------------------------------------


def _write_minimal_server_config(path: Path) -> None:
    path.write_text("server:\n" "  name: testserv\n" "  host: 127.0.0.1\n" "  port: 6667\n")


def test_get_observer_constructs_with_config_and_culture_nick(monkeypatch, tmp_path):
    cfg_path = tmp_path / "server.yaml"
    _write_minimal_server_config(cfg_path)
    monkeypatch.setenv("CULTURE_NICK", "testserv-claude")

    obs = get_observer(str(cfg_path))
    assert obs.host == "127.0.0.1"
    assert obs.port == 6667
    assert obs.server_name == "testserv"
    assert obs.parent_nick == "testserv-claude"


def test_get_observer_without_culture_nick(monkeypatch, tmp_path):
    cfg_path = tmp_path / "server.yaml"
    _write_minimal_server_config(cfg_path)
    monkeypatch.delenv("CULTURE_NICK", raising=False)

    obs = get_observer(str(cfg_path))
    assert obs.parent_nick is None


def test_get_observer_empty_culture_nick_falls_back_to_none(monkeypatch, tmp_path):
    cfg_path = tmp_path / "server.yaml"
    _write_minimal_server_config(cfg_path)
    monkeypatch.setenv("CULTURE_NICK", "   ")

    obs = get_observer(str(cfg_path))
    assert obs.parent_nick is None


# ---------------------------------------------------------------------------
# ipc_request — structured failure logging (#17)
# ---------------------------------------------------------------------------

_IPC_LOGGER = "culture_core.cli.shared.ipc"


def _failure_records(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "ipc failure:" in r.getMessage()]


def _assert_failure_shape(msg: str, *, operation: str, nick: str, socket_path: str) -> None:
    """Every IPC failure log line carries the same structured fields."""
    assert f"operation={operation}" in msg
    assert f"nick={nick}" in msg
    assert f"socket_path={socket_path}" in msg
    assert "failure=" in msg
    assert "elapsed=" in msg


@pytest.mark.asyncio
async def test_ipc_failure_log_on_connect_refused(tmp_path, caplog):
    """Nonexistent socket → connect failure logged with nick/socket/operation."""
    sock_path = tmp_path / "culture-spark-claude.sock"

    with caplog.at_level("WARNING", logger=_IPC_LOGGER):
        resp = await ipc_request(str(sock_path), "status")

    assert resp is None
    records = _failure_records(caplog)
    assert records, "expected a structured ipc failure log line"
    _assert_failure_shape(
        records[-1], operation="status", nick="spark-claude", socket_path=str(sock_path)
    )
    assert "failure=connect:" in records[-1]


@pytest.mark.asyncio
async def test_ipc_failure_log_on_eof(tmp_path, caplog):
    """Peer closes without responding → eof failure logged."""
    sock_path = tmp_path / "culture-spark-codex.sock"

    async def handler(reader, writer):
        await reader.readline()
        writer.close()

    server = await asyncio.start_unix_server(handler, path=str(sock_path))
    try:
        with caplog.at_level("WARNING", logger=_IPC_LOGGER):
            resp = await ipc_request(str(sock_path), "pause")
    finally:
        server.close()
        await server.wait_closed()

    assert resp is None
    records = _failure_records(caplog)
    assert records, "expected a structured ipc failure log line"
    _assert_failure_shape(
        records[-1], operation="pause", nick="spark-codex", socket_path=str(sock_path)
    )
    assert "failure=eof" in records[-1]


@pytest.mark.asyncio
async def test_ipc_failure_log_on_response_timeout(tmp_path, caplog, monkeypatch):
    """Peer accepts but never responds → response_timeout failure logged."""
    import culture_core.cli.shared.ipc as ipc_mod

    monkeypatch.setattr(ipc_mod, "RESPONSE_TIMEOUT", 0.2)
    sock_path = tmp_path / "culture-spark-acp.sock"
    hold = asyncio.Event()

    async def handler(reader, writer):
        await reader.readline()
        await hold.wait()  # never respond until test teardown
        writer.close()

    server = await asyncio.start_unix_server(handler, path=str(sock_path))
    try:
        with caplog.at_level("WARNING", logger=_IPC_LOGGER):
            resp = await ipc_request(str(sock_path), "resume")
    finally:
        hold.set()
        server.close()
        await server.wait_closed()

    assert resp is None
    records = _failure_records(caplog)
    assert records, "expected a structured ipc failure log line"
    _assert_failure_shape(
        records[-1], operation="resume", nick="spark-acp", socket_path=str(sock_path)
    )
    assert "failure=response_timeout" in records[-1]


def test_nick_from_socket_path_fallback_for_foreign_names():
    from culture_core.cli.shared.ipc import _nick_from_socket_path

    assert _nick_from_socket_path("/run/user/1000/culture-spark-ada.sock") == "spark-ada"
    assert _nick_from_socket_path("/run/user/1000/other.sock") == ""
