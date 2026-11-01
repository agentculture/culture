"""Live presence for external residents in `culture agents status` (culture#506).

External agents (backend: external) own their own IRC connection, so their
pidfile/IPC socket never exist; status reads their state from one
``PRESENCE LIST`` query per invocation. Harness-agent rows must stay
byte-identical (snapshot tests below were captured before the change).
No server mocks: a real IRCd on a random port plus a raw TCP client that
publishes PRESENCE under the external agent's nick.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import time
from datetime import datetime, timezone

import pytest

from culture_core.cli.shared.display import print_agent_detail, print_agents_overview
from culture_core.config import AgentConfig, ServerConfig, ServerConnConfig


def _agent(nick: str, backend: str = "claude", **kw) -> AgentConfig:
    agent = AgentConfig(backend=backend, **kw)
    agent.nick = nick
    return agent


def _server_config(port: int) -> ServerConfig:
    return ServerConfig(server=ServerConnConfig(name="testserv", host="127.0.0.1", port=port))


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(autouse=True)
def _isolated_pids(tmp_path, monkeypatch):
    """No pidfiles anywhere: every harness agent reads as stopped."""
    monkeypatch.setattr("culture_core.cli.shared.display.read_pid", lambda name: None)


# --- Snapshots: harness rows byte-identical to pre-change output ------------

SNAP_PLAIN = (
    "NICK                           STATUS       PID       \n"
    "----------------------------------------------------\n"
    "spark-a                        stopped      -         \n"
    "spark-b                        archived     -         \n"
)
SNAP_FULL = (
    "NICK                           STATUS       PID        ACTIVITY\n"
    "------------------------------------------------------------------------\n"
    "spark-a                        stopped      -          -\n"
    "spark-b                        stopped (archived) -          -\n"
)
SNAP_DETAIL = (
    "spark-a\n"
    "  Status:     stopped\n"
    "  PID:        -\n"
    "  Activity:   -\n"
    "  Directory:  /tmp/a\n"
    "  Backend:    claude\n"
    "  Channels:   #general\n"
    "  Model:      m\n"
    "  Config:     cfg.yaml\n"
)


def _harness_agents():
    a = _agent("spark-a", directory="/tmp/a", model="m", channels=["#general"])
    b = _agent("spark-b", archived=True)
    return [a, b]


def test_snapshot_harness_overview_plain(capsys):
    print_agents_overview(_harness_agents(), False)
    assert capsys.readouterr().out == SNAP_PLAIN


def test_snapshot_harness_overview_full_marker(capsys):
    print_agents_overview(_harness_agents(), True, show_archived_marker=True)
    assert capsys.readouterr().out == SNAP_FULL


def test_snapshot_harness_detail(capsys):
    print_agent_detail(_harness_agents()[0], "cfg.yaml", argparse.Namespace(full=False))
    assert capsys.readouterr().out == SNAP_DETAIL


def test_harness_rows_unchanged_even_with_server_config(capsys):
    """Passing a server config with no external agent never queries or alters output."""
    print_agents_overview(_harness_agents(), False, server_config=_server_config(_free_port()))
    assert capsys.readouterr().out == SNAP_PLAIN


# --- External rows -----------------------------------------------------------


def _row(out: str, nick: str) -> list[str]:
    return next(line.split() for line in out.splitlines() if line.startswith(nick))


async def _publish(port: int, nick: str, state: str):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"NICK {nick}\r\nUSER {nick} 0 * :{nick}\r\n".encode())
    await asyncio.sleep(0.3)
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = json.dumps({"state": state, "since": since})
    writer.write(f"PRESENCE :{payload}\r\n".encode())
    await writer.drain()
    return writer


@pytest.mark.asyncio
async def test_external_row_shows_published_state_within_5s(server, capsys):
    port = server.config.port
    agents = _harness_agents()[:1] + [
        _agent("testserv-gwen", backend="external", owner="gwen.service")
    ]
    writer = await _publish(port, "testserv-gwen", "idle")
    try:
        start = time.monotonic()
        await asyncio.to_thread(
            print_agents_overview, agents, False, server_config=_server_config(port)
        )
        assert time.monotonic() - start < 5
    finally:
        writer.close()
    out = capsys.readouterr().out
    assert _row(out, "testserv-gwen")[:2] == ["testserv-gwen", "idle"]
    assert _row(out, "spark-a")[:2] == ["spark-a", "stopped"]


@pytest.mark.asyncio
async def test_external_without_presence_row_is_offline(server, capsys):
    agents = [_agent("testserv-ghost", backend="external")]
    await asyncio.to_thread(
        print_agents_overview, agents, False, server_config=_server_config(server.config.port)
    )
    assert _row(capsys.readouterr().out, "testserv-ghost")[:2] == ["testserv-ghost", "offline"]


@pytest.mark.asyncio
async def test_detail_shows_live_state_and_owner(server, capsys):
    port = server.config.port
    agent = _agent("testserv-gwen", backend="external", owner="gwen.service")
    writer = await _publish(port, "testserv-gwen", "thinking")
    try:
        await asyncio.to_thread(
            print_agent_detail,
            agent,
            "cfg.yaml",
            argparse.Namespace(full=False),
            server_config=_server_config(port),
        )
    finally:
        writer.close()
    out = capsys.readouterr().out
    assert "  Status:     thinking\n" in out
    assert "gwen.service" in out


def test_unreachable_server_external_unknown_harness_identical(capsys):
    agents = _harness_agents() + [_agent("testserv-gwen", backend="external")]
    print_agents_overview(agents, False, server_config=_server_config(_free_port()))
    out = capsys.readouterr().out
    assert _row(out, "testserv-gwen")[:2] == ["testserv-gwen", "unknown"]
    assert out.startswith(SNAP_PLAIN)  # harness rows byte-identical (external row appended)


def test_no_server_config_external_unknown(capsys):
    print_agents_overview([_agent("x-gwen", backend="external")], False)
    assert _row(capsys.readouterr().out, "x-gwen")[:2] == ["x-gwen", "unknown"]


def test_one_query_per_status_call(monkeypatch, capsys):
    calls = []

    async def fake_fetch(config, *, parent_nick=None):
        calls.append(1)
        return []

    monkeypatch.setattr("culture_core.cli.shared.display.fetch_residents_async", fake_fetch)
    agents = [_agent(f"s-e{i}", backend="external") for i in range(3)] + _harness_agents()
    print_agents_overview(agents, False, server_config=_server_config(1))
    assert len(calls) == 1
    # and none when no externals
    print_agents_overview(_harness_agents(), False, server_config=_server_config(1))
    assert len(calls) == 1
