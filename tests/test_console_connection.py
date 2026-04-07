"""Tests for server discovery and default server logic."""

import os
from unittest.mock import patch

import pytest

from culture.pidfile import (
    list_servers,
    read_default_server,
    write_default_server,
)


@pytest.fixture
def tmp_pid_dir(tmp_path):
    with patch("culture.pidfile.PID_DIR", str(tmp_path)):
        yield tmp_path


def test_list_servers_empty(tmp_pid_dir):
    assert list_servers() == []


def test_list_servers_finds_running(tmp_pid_dir):
    (tmp_pid_dir / "server-spark.pid").write_text(str(os.getpid()))
    (tmp_pid_dir / "server-spark.port").write_text("6667")
    with (
        patch("culture.pidfile.is_process_alive", return_value=True),
        patch("culture.pidfile.is_culture_process", return_value=True),
    ):
        result = list_servers()
    assert result == [{"name": "spark", "pid": os.getpid(), "port": 6667}]


def test_list_servers_skips_dead(tmp_pid_dir):
    (tmp_pid_dir / "server-dead.pid").write_text("99999")
    (tmp_pid_dir / "server-dead.port").write_text("6667")
    with patch("culture.pidfile.is_process_alive", return_value=False):
        assert list_servers() == []


def test_default_server_none_when_unset(tmp_pid_dir):
    assert read_default_server() is None


def test_write_and_read_default_server(tmp_pid_dir):
    write_default_server("spark")
    assert read_default_server() == "spark"


def test_resolve_server_zero_running(tmp_pid_dir):
    """No servers running should return empty list."""
    assert list_servers() == []


def test_resolve_server_one_running(tmp_pid_dir):
    (tmp_pid_dir / "server-spark.pid").write_text(str(os.getpid()))
    (tmp_pid_dir / "server-spark.port").write_text("6667")
    with (
        patch("culture.pidfile.is_process_alive", return_value=True),
        patch("culture.pidfile.is_culture_process", return_value=True),
    ):
        servers = list_servers()
    assert len(servers) == 1
    assert servers[0]["name"] == "spark"
