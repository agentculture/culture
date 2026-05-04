"""Tests for the _wait_for_port readiness helper."""

from __future__ import annotations

import socket
import threading
import time

from culture.cli.chat import _wait_for_port


def _start_listener(port_holder: list, delay: float = 0) -> socket.socket:
    """Start a TCP listener, optionally after *delay* seconds.

    Stores the assigned port in *port_holder[0]*.
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    def _bind():
        if delay:
            time.sleep(delay)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port_holder.append(srv.getsockname()[1])

    t = threading.Thread(target=_bind, daemon=True)
    t.start()
    t.join(timeout=delay + 2)
    return srv


def test_success_immediate():
    """Port already open — should return immediately."""
    port_holder: list[int] = []
    srv = _start_listener(port_holder)
    try:
        ok, err = _wait_for_port(
            "127.0.0.1",
            port_holder[0],
            # Use our own PID (always alive)
            pid=threading.current_thread().native_id or 1,
            timeout=5,
        )
        assert ok
        assert err == ""
    finally:
        srv.close()


def test_timeout_no_listener():
    """No listener — should time out."""
    # Bind then close to get a free port that nothing listens on
    tmp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tmp.bind(("127.0.0.1", 0))
    port = tmp.getsockname()[1]
    tmp.close()

    import os

    ok, err = _wait_for_port("127.0.0.1", port, pid=os.getpid(), timeout=1)
    assert not ok
    assert "not yet accepting connections" in err


def test_process_dies():
    """Dead PID — should fail fast."""
    import subprocess

    # Spawn a process that exits immediately to get a dead PID
    p = subprocess.Popen(["true"])
    p.wait()

    ok, err = _wait_for_port("127.0.0.1", 1, pid=p.pid, timeout=5)
    assert not ok
    assert "failed to start" in err


def test_host_0000_uses_localhost():
    """0.0.0.0 host should probe 127.0.0.1."""
    port_holder: list[int] = []
    srv = _start_listener(port_holder)
    try:
        import os

        ok, _ = _wait_for_port("0.0.0.0", port_holder[0], pid=os.getpid(), timeout=5)
        assert ok
    finally:
        srv.close()
