"""Tests for CLI agent/bot status display helpers (issues #179, #180)."""

from __future__ import annotations

import argparse
from types import SimpleNamespace
from unittest.mock import patch

from culture.cli.shared.display import (
    _fetch_ipc_data,
    _format_agent_status,
    agent_process_status,
    print_agent_detail,
    print_agents_overview,
)


def _make_agent(nick="spark-claude", archived=False):
    """Create a minimal agent-like object for testing."""
    return SimpleNamespace(
        nick=nick,
        archived=archived,
        directory="/home/spark/git/culture",
        agent="claude",
        channels=["#general"],
        model="claude-opus-4-6",
    )


# --- agent_process_status ---


def test_agent_process_status_stopped():
    """No PID file → stopped."""
    agent = _make_agent()
    with patch("culture.cli.shared.display.read_pid", return_value=None):
        status, pid = agent_process_status(agent)
    assert status == "stopped"
    assert pid is None


def test_agent_process_status_running(tmp_path):
    """PID alive + socket exists → running."""
    agent = _make_agent()
    sock = tmp_path / "culture-spark-claude.sock"
    sock.touch()
    with (
        patch("culture.cli.shared.display.read_pid", return_value=12345),
        patch("culture.cli.shared.display.is_process_alive", return_value=True),
        patch("culture.cli.shared.display.agent_socket_path", return_value=str(sock)),
    ):
        status, pid = agent_process_status(agent)
    assert status == "running"
    assert pid == 12345


def test_agent_process_status_starting(tmp_path):
    """PID alive but no socket → starting."""
    agent = _make_agent()
    missing_sock = tmp_path / "culture-spark-claude.sock"  # not created
    with (
        patch("culture.cli.shared.display.read_pid", return_value=12345),
        patch("culture.cli.shared.display.is_process_alive", return_value=True),
        patch("culture.cli.shared.display.agent_socket_path", return_value=str(missing_sock)),
    ):
        status, pid = agent_process_status(agent)
    assert status == "starting"
    assert pid == 12345


# --- _format_agent_status ---


def test_format_agent_status_not_archived():
    assert _format_agent_status("running", False, False) == "running"


def test_format_agent_status_archived_with_marker():
    assert _format_agent_status("running", True, True) == "running (archived)"


def test_format_agent_status_archived_stopped():
    assert _format_agent_status("stopped", True, False) == "archived"


# --- _fetch_ipc_data ---


def test_fetch_ipc_data_success():
    agent = _make_agent()
    fake_resp = {"ok": True, "data": {"paused": True, "description": "paused"}}
    with (
        patch("culture.cli.shared.display.agent_socket_path", return_value="/tmp/test.sock"),
        patch("culture.cli.shared.display.ipc_request", return_value=fake_resp),
    ):
        data = _fetch_ipc_data(agent)
    assert data is not None
    assert data["paused"] is True


def test_fetch_ipc_data_failure():
    agent = _make_agent()
    with (
        patch("culture.cli.shared.display.agent_socket_path", return_value="/tmp/test.sock"),
        patch("culture.cli.shared.display.ipc_request", return_value=None),
    ):
        data = _fetch_ipc_data(agent)
    assert data is None


# --- print_agents_overview (issues #179 + #180) ---


def _mock_running_agent_overview(agents, ipc_data, capsys, show_activity=False):
    """Helper: mock agent as running and capture overview output."""
    with (
        patch("culture.cli.shared.display.read_pid", return_value=999),
        patch("culture.cli.shared.display.is_process_alive", return_value=True),
        patch("culture.cli.shared.display.agent_socket_path", return_value="/tmp/x.sock"),
        patch("os.path.exists", return_value=True),
        patch("culture.cli.shared.display.ipc_request", return_value=ipc_data),
    ):
        print_agents_overview(agents, show_activity=show_activity)
    return capsys.readouterr().out


def test_overview_shows_paused_status(capsys):
    """Issue #180: paused agents should show 'paused' in status column."""
    agent = _make_agent()
    ipc_resp = {
        "ok": True,
        "data": {"paused": True, "circuit_open": False, "description": "paused"},
    }
    out = _mock_running_agent_overview([agent], ipc_resp, capsys)
    assert "paused" in out


def test_overview_shows_circuit_open_status(capsys):
    """Issue #179: circuit-open agents should show 'circuit-open' in status column."""
    agent = _make_agent()
    ipc_resp = {
        "ok": True,
        "data": {"paused": False, "circuit_open": True, "description": "nothing"},
    }
    out = _mock_running_agent_overview([agent], ipc_resp, capsys)
    assert "circuit-open" in out


def test_overview_shows_running_when_healthy(capsys):
    """Healthy running agent should still show 'running'."""
    agent = _make_agent()
    ipc_resp = {
        "ok": True,
        "data": {"paused": False, "circuit_open": False, "description": "working"},
    }
    out = _mock_running_agent_overview([agent], ipc_resp, capsys)
    assert "running" in out


def test_overview_shows_activity_when_requested(capsys):
    """With show_activity=True, activity column should show description."""
    agent = _make_agent()
    ipc_resp = {
        "ok": True,
        "data": {"paused": False, "circuit_open": False, "description": "reviewing PR #42"},
    }
    out = _mock_running_agent_overview([agent], ipc_resp, capsys, show_activity=True)
    assert "reviewing PR #42" in out


# --- print_agent_detail (issues #179 + #180) ---


def _mock_agent_detail(agent, ipc_data, capsys, full=False):
    """Helper: mock agent as running and capture detail output."""
    args = argparse.Namespace(full=full)
    with (
        patch("culture.cli.shared.display.read_pid", return_value=999),
        patch("culture.cli.shared.display.is_process_alive", return_value=True),
        patch("culture.cli.shared.display.agent_socket_path", return_value="/tmp/x.sock"),
        patch("os.path.exists", return_value=True),
        patch("culture.cli.shared.display.ipc_request", return_value=ipc_data),
    ):
        print_agent_detail(agent, "/path/to/config", args)
    return capsys.readouterr().out


def test_detail_shows_paused_status(capsys):
    """Issue #180: detail view should show 'paused' status."""
    agent = _make_agent()
    ipc_resp = {
        "ok": True,
        "data": {"paused": True, "circuit_open": False, "description": "paused", "turn_count": 5},
    }
    out = _mock_agent_detail(agent, ipc_resp, capsys)
    assert "Status:     paused" in out
    assert "Paused:     yes" in out


def test_detail_shows_circuit_open_status(capsys):
    """Issue #179: detail view should show 'circuit-open' and Circuit line."""
    agent = _make_agent()
    ipc_resp = {
        "ok": True,
        "data": {"paused": False, "circuit_open": True, "description": "nothing", "turn_count": 0},
    }
    out = _mock_agent_detail(agent, ipc_resp, capsys)
    assert "Status:     circuit-open" in out
    assert "Circuit:    OPEN (not restarting)" in out


def test_detail_shows_circuit_closed(capsys):
    """Healthy agent detail shows Circuit: closed."""
    agent = _make_agent()
    ipc_resp = {
        "ok": True,
        "data": {
            "paused": False,
            "circuit_open": False,
            "description": "working",
            "turn_count": 10,
        },
    }
    out = _mock_agent_detail(agent, ipc_resp, capsys)
    assert "Status:     running" in out
    assert "Circuit:    closed" in out
