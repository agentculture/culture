"""Agent and server process management for culture CLI."""

from __future__ import annotations

import asyncio
import os
import sys
import time

from culture_core.pidfile import (
    classify_pidfile,
    is_process_alive,
    read_pid,
    remove_pid,
    terminate_verified,
)

from .ipc import agent_socket_path, ipc_shutdown


def stop_agent(nick: str) -> bool:
    """Stop a single agent by trying IPC shutdown first, then PID file."""
    socket_path = agent_socket_path(nick)

    if _try_ipc_shutdown(nick, socket_path):
        return True

    return _try_pid_shutdown(nick)


def _try_ipc_shutdown(nick: str, socket_path: str) -> bool:
    """Attempt graceful IPC shutdown. Return True if the agent stopped."""
    if not os.path.exists(socket_path):
        return False
    try:
        success = asyncio.run(ipc_shutdown(socket_path))
        if not success:
            return False
    except Exception:
        return False

    print(f"Agent '{nick}' shutdown requested via IPC")
    pid_name = f"agent-{nick}"
    pid = read_pid(pid_name)
    if not pid:
        print(f"Agent '{nick}' stopped")
        return True
    for _ in range(50):
        if not is_process_alive(pid):
            remove_pid(pid_name, pid)
            print(f"Agent '{nick}' stopped")
            return True
        time.sleep(0.1)
    return False


def _try_pid_shutdown(nick: str) -> bool:
    """Stop an agent via PID file with SIGTERM/SIGKILL fallback."""
    pid_name = f"agent-{nick}"
    pid = read_pid(pid_name)
    state = classify_pidfile(pid_name, pid)

    if pid is None:
        if state == "stale":
            print(f"No PID file for agent '{nick}'")
            return True
        print(f"Cannot verify agent '{nick}' PID file; leaving it in place")
        return False

    if pid <= 0:
        print(f"Invalid PID {pid} for agent '{nick}' — removing corrupt PID file")
        remove_pid(pid_name, pid)
        return True

    if state == "stale":
        print(f"Agent '{nick}' is not running (stale PID {pid})")
        remove_pid(pid_name, pid)
        return True

    if state != "current":
        print(f"Cannot verify agent '{nick}' (PID {pid}); leaving PID file and not signaling")
        return False

    print(f"Stopping agent '{nick}' (PID {pid})...")
    result = terminate_verified(pid_name, pid)
    if result == "refused":
        print(f"PID {pid} identity changed or cannot be verified — aborting kill")
        return False
    if result == "killed":
        if sys.platform == "win32":
            print(f"Agent '{nick}' did not stop gracefully, terminating")
        else:
            print(f"Agent '{nick}' did not stop gracefully, sending SIGKILL")
    remove_pid(pid_name, pid)
    print(f"Agent '{nick}' {'killed' if result == 'killed' else 'stopped'}")
    return True


def server_stop_by_name(name: str) -> bool:
    """Stop a server by name (helper for setup --uninstall and update)."""
    pid_name = f"server-{name}"
    pid = read_pid(pid_name)
    state = classify_pidfile(pid_name, pid)
    if pid is None:
        return state == "stale"
    if state == "stale":
        remove_pid(pid_name, pid)
        return True

    if state != "current":
        print(f"Cannot verify server '{name}' (PID {pid}); leaving PID file and not signaling")
        return False

    if terminate_verified(pid_name, pid) == "refused":
        print(f"PID {pid} identity changed or cannot be verified — aborting kill")
        return False
    remove_pid(pid_name, pid)
    return True
