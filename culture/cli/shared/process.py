"""Agent and server process management for culture CLI."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time

from culture.pidfile import (
    is_culture_process,
    is_process_alive,
    read_pid,
    remove_pid,
)

from .ipc import agent_socket_path, ipc_shutdown


def stop_agent(nick: str) -> None:
    """Stop a single agent by trying IPC shutdown first, then PID file."""
    socket_path = agent_socket_path(nick)

    if _try_ipc_shutdown(nick, socket_path):
        return

    _try_pid_shutdown(nick)


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
            remove_pid(pid_name)
            print(f"Agent '{nick}' stopped")
            return True
        time.sleep(0.1)
    return False


def _try_pid_shutdown(nick: str) -> None:
    """Stop an agent via PID file with SIGTERM/SIGKILL fallback."""
    pid_name = f"agent-{nick}"
    pid = read_pid(pid_name)

    if pid is None:
        print(f"No PID file for agent '{nick}'")
        return

    if pid <= 0:
        print(f"Invalid PID {pid} for agent '{nick}' — removing corrupt PID file")
        remove_pid(pid_name)
        return

    if not is_process_alive(pid):
        print(f"Agent '{nick}' is not running (stale PID {pid})")
        remove_pid(pid_name)
        return

    if not is_culture_process(pid):
        print(f"PID {pid} is not a culture process — removing stale PID file")
        remove_pid(pid_name)
        return

    print(f"Stopping agent '{nick}' (PID {pid})...")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        remove_pid(pid_name)
        return

    for _ in range(50):
        if not is_process_alive(pid):
            print(f"Agent '{nick}' stopped")
            remove_pid(pid_name)
            return
        time.sleep(0.1)

    if not is_culture_process(pid):
        print(f"PID {pid} is no longer a culture process — aborting kill")
        remove_pid(pid_name)
        return

    if sys.platform == "win32":
        print(f"Agent '{nick}' did not stop gracefully, terminating")
        sig = signal.SIGTERM
    else:
        print(f"Agent '{nick}' did not stop gracefully, sending SIGKILL")
        sig = signal.SIGKILL
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass
    remove_pid(pid_name)
    print(f"Agent '{nick}' killed")


def server_stop_by_name(name: str) -> None:
    """Stop a server by name (helper for setup --uninstall and update)."""
    pid_name = f"server-{name}"
    pid = read_pid(pid_name)
    if not pid or not is_process_alive(pid):
        if pid:
            remove_pid(pid_name)
        return

    if not is_culture_process(pid):
        remove_pid(pid_name)
        return

    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        if not is_process_alive(pid):
            remove_pid(pid_name)
            return
        time.sleep(0.1)

    if sys.platform == "win32":
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    remove_pid(pid_name)
