"""Shared helpers for culture CLI modules."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
import time

from culture.clients.claude.config import (
    load_config,
    load_config_or_default,
)
from culture.pidfile import (
    is_culture_process,
    is_process_alive,
    read_pid,
    remove_pid,
    write_pid,
)

logger = logging.getLogger("culture")

DEFAULT_CONFIG = os.path.expanduser("~/.culture/agents.yaml")
_CONFIG_HELP = "Config file path"
LOG_DIR = os.path.expanduser("~/.culture/logs")


# -----------------------------------------------------------------------
# Link / credential helpers
# -----------------------------------------------------------------------


def parse_link(value: str):
    """Parse a link spec: name:host:port:password[:trust]

    Trust is extracted from the end if it matches a known value.
    This allows passwords containing colons.
    """
    from culture.server.config import LinkConfig

    trust = "full"
    if value.endswith(":full") or value.endswith(":restricted"):
        value, trust = value.rsplit(":", 1)

    parts = value.split(":", 3)
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            f"Link must be name:host:port:password[:trust], got: {value}"
        )
    name, host, port_str, password = parts
    try:
        port = int(port_str)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid port: {port_str}")
    return LinkConfig(name=name, host=host, port=port, password=password, trust=trust)


def resolve_links_from_mesh(mesh_config_path: str) -> list:
    """Load link configs from mesh.yaml, looking up passwords from OS keyring."""
    from culture.credentials import lookup_credential
    from culture.mesh_config import load_mesh_config
    from culture.server.config import LinkConfig

    mesh = load_mesh_config(mesh_config_path)
    links = []
    for lc in mesh.server.links:
        password = lookup_credential(lc.name)
        if not password:
            logger.warning(
                "No credential found for peer '%s' — link will not be established. "
                "Run 'culture mesh setup' to store link passwords.",
                lc.name,
            )
            continue
        links.append(
            LinkConfig(
                name=lc.name,
                host=lc.host,
                port=lc.port,
                password=password,
                trust=lc.trust,
            )
        )
    return links


# -----------------------------------------------------------------------
# IPC helpers
# -----------------------------------------------------------------------


def agent_socket_path(nick: str) -> str:
    return os.path.join(
        os.environ.get("XDG_RUNTIME_DIR", "/tmp"),
        f"culture-{nick}.sock",
    )


async def ipc_request(socket_path: str, msg_type: str, **kwargs) -> dict | None:
    """Send an IPC request via Unix socket and return the response."""
    from culture.clients.claude.ipc import decode_message, encode_message, make_request

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(socket_path),
            timeout=3.0,
        )
    except (ConnectionRefusedError, FileNotFoundError, OSError):
        return None
    try:
        req = make_request(msg_type, **kwargs)
        writer.write(encode_message(req))
        await writer.drain()
        deadline = asyncio.get_event_loop().time() + 3.0
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                return None
            data = await asyncio.wait_for(reader.readline(), timeout=remaining)
            msg = decode_message(data)
            if msg and msg.get("type") == "response":
                return msg
    except (asyncio.TimeoutError, ConnectionError, BrokenPipeError, OSError):
        return None
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, BrokenPipeError, OSError):
            pass


async def ipc_shutdown(socket_path: str) -> bool:
    """Send a shutdown command via Unix socket IPC."""
    resp = await ipc_request(socket_path, "shutdown")
    return resp is not None and resp.get("ok", False)


# -----------------------------------------------------------------------
# Agent stop helpers (used by agent.py and mesh.py)
# -----------------------------------------------------------------------


def stop_agent(nick: str) -> None:
    """Stop a single agent by trying IPC shutdown first, then PID file."""
    socket_path = os.path.join(
        os.environ.get("XDG_RUNTIME_DIR", "/tmp"),
        f"culture-{nick}.sock",
    )

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


# -----------------------------------------------------------------------
# Server stop helper (used by mesh.py)
# -----------------------------------------------------------------------


def server_stop_by_name(name: str) -> None:
    """Stop a server by name (helper for setup --uninstall and update)."""
    pid_name = f"server-{name}"
    pid = read_pid(pid_name)
    if not pid or not is_process_alive(pid):
        if pid:
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


# -----------------------------------------------------------------------
# Mesh config helpers (used by server.py and mesh.py)
# -----------------------------------------------------------------------


def generate_mesh_from_agents(mesh_config_path: str):
    """Fall back to generating mesh.yaml from agents.yaml when mesh.yaml is missing."""
    from culture.mesh_config import from_daemon_config, save_mesh_config

    if not os.path.isfile(DEFAULT_CONFIG):
        print(f"Mesh config not found: {mesh_config_path}", file=sys.stderr)
        print(f"Agent config not found either: {DEFAULT_CONFIG}", file=sys.stderr)
        return None

    daemon_config = load_config(DEFAULT_CONFIG)
    mesh = from_daemon_config(daemon_config)
    save_mesh_config(mesh, mesh_config_path)
    print(f"No mesh.yaml found — generated from {DEFAULT_CONFIG}")
    return mesh


def build_server_start_cmd(mesh, culture_bin: str, mesh_config_path: str) -> list[str]:
    """Build the server start command with --foreground and --mesh-config."""
    return [
        culture_bin,
        "server",
        "start",
        "--foreground",
        "--name",
        mesh.server.name,
        "--host",
        mesh.server.host,
        "--port",
        str(mesh.server.port),
        "--mesh-config",
        mesh_config_path,
    ]


# -----------------------------------------------------------------------
# Observer helper (used by channel.py and agent.py)
# -----------------------------------------------------------------------


def get_observer(config_path: str):
    """Create an IRCObserver from the config file."""
    from culture.observer import IRCObserver

    config = load_config_or_default(config_path)
    return IRCObserver(
        host=config.server.host,
        port=config.server.port,
        server_name=config.server.name,
    )


# -----------------------------------------------------------------------
# Agent status display helpers
# -----------------------------------------------------------------------


def agent_process_status(agent) -> tuple[str, int | None]:
    """Return (status_str, pid_or_none) for an agent."""
    pid_name = f"agent-{agent.nick}"
    pid = read_pid(pid_name)
    if pid and is_process_alive(pid):
        socket_path = agent_socket_path(agent.nick)
        if os.path.exists(socket_path):
            return "running", pid
        return "starting", pid
    if pid:
        remove_pid(pid_name)
    return "stopped", None


def print_agent_detail(agent, config_path: str, args: argparse.Namespace) -> None:
    """Print detailed status for a single agent, including live IPC activity query."""
    status, pid = agent_process_status(agent)
    print(agent.nick)
    print(f"  Status:     {status}")
    print(f"  PID:        {pid or '-'}")

    if status == "running":
        resp = asyncio.run(ipc_request(agent_socket_path(agent.nick), "status", query=True))
        if resp and resp.get("ok"):
            data = resp.get("data", {})
            print(f"  Activity:   {data.get('description', 'nothing')}")
            print(f"  Turns:      {data.get('turn_count', 0)}")
            print(f"  Paused:     {'yes' if data.get('paused') else 'no'}")
        else:
            print("  Activity:   unknown (daemon may need restart)")
    else:
        print("  Activity:   -")

    channels = agent.channels if isinstance(agent.channels, list) else []
    print(f"  Directory:  {agent.directory}")
    print(f"  Backend:    {agent.agent}")
    print(f"  Channels:   {', '.join(channels)}")
    print(f"  Model:      {agent.model}")
    print(f"  Config:     {config_path}")


def print_agents_overview(agents: list, show_activity: bool) -> None:
    """Print a table of all agents with status, PID, and optionally activity."""
    if show_activity:
        print(f"{'NICK':<30} {'STATUS':<12} {'PID':<10} {'ACTIVITY'}")
        print("-" * 72)
    else:
        print(f"{'NICK':<30} {'STATUS':<12} {'PID':<10}")
        print("-" * 52)

    for agent in agents:
        status, pid = agent_process_status(agent)
        activity = "-"

        if show_activity and status == "running":
            resp = asyncio.run(ipc_request(agent_socket_path(agent.nick), "status"))
            if resp and resp.get("ok"):
                activity = resp.get("data", {}).get("description", "nothing")

        if show_activity:
            print(f"{agent.nick:<30} {status:<12} {str(pid or '-'):<10} {activity}")
        else:
            print(f"{agent.nick:<30} {status:<12} {str(pid or '-'):<10}")


def print_bot_listing() -> None:
    """Print a table of configured bots (if any exist)."""
    from culture.bots.config import BOTS_DIR, load_bot_config

    if BOTS_DIR.is_dir():
        bot_configs = []
        for bot_dir in sorted(BOTS_DIR.iterdir()):
            yaml_path = bot_dir / "bot.yaml"
            if yaml_path.is_file():
                try:
                    bot_configs.append(load_bot_config(yaml_path))
                except Exception:
                    pass
        if bot_configs:
            print()
            print(f"{'BOT':<30} {'TRIGGER':<12} {'CHANNELS'}")
            print("-" * 60)
            for bc in bot_configs:
                channels = ", ".join(bc.channels) if bc.channels else "-"
                print(f"{bc.name:<30} {bc.trigger_type:<12} {channels}")
