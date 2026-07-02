"""Tests for mesh update readiness probe (issue #201)."""

import socket
import threading

import pytest

from culture_core.cli.mesh import _wait_for_server_port


def _start_listener():
    """Start a TCP listener on a random port, return (port, server_socket)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def _accept_loop():
        try:
            while True:
                conn, _ = srv.accept()
                conn.close()
        except OSError:
            pass

    t = threading.Thread(target=_accept_loop, daemon=True)
    t.start()
    return port, srv


class TestWaitForServerPort:
    def test_tcp_only_no_server_name(self):
        """Without server_name, TCP probe alone determines success."""
        port, srv = _start_listener()
        try:
            assert _wait_for_server_port("127.0.0.1", port, retries=5) is True
        finally:
            srv.close()

    def test_tcp_unreachable(self):
        """Returns False when port is not listening."""
        assert _wait_for_server_port("127.0.0.1", 1, retries=2, interval=0.01) is False

    def test_with_valid_culture_pid(self, monkeypatch):
        """TCP ok + valid culture PID → True."""
        import os

        port, srv = _start_listener()
        try:
            # Mock read_pid to return current process PID
            # Mock is_culture_process to return True
            monkeypatch.setattr(
                "culture_core.pidfile.read_pid",
                lambda name: os.getpid(),
            )
            monkeypatch.setattr(
                "culture_core.pidfile.is_culture_process",
                lambda pid: True,
            )
            assert _wait_for_server_port("127.0.0.1", port, retries=5, server_name="test") is True
        finally:
            srv.close()

    def test_with_wrong_process_pid(self, monkeypatch):
        """TCP ok + wrong process on PID → False."""
        import os

        port, srv = _start_listener()
        try:
            monkeypatch.setattr(
                "culture_core.pidfile.read_pid",
                lambda name: os.getpid(),
            )
            monkeypatch.setattr(
                "culture_core.pidfile.is_culture_process",
                lambda pid: False,
            )
            assert _wait_for_server_port("127.0.0.1", port, retries=5, server_name="test") is False
        finally:
            srv.close()

    def test_pid_file_missing_still_succeeds(self, monkeypatch):
        """TCP ok + no PID file → True (graceful, PID file may not exist yet)."""
        port, srv = _start_listener()
        try:
            monkeypatch.setattr(
                "culture_core.pidfile.read_pid",
                lambda name: None,
            )
            assert _wait_for_server_port("127.0.0.1", port, retries=5, server_name="test") is True
        finally:
            srv.close()
