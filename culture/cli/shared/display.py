"""Agent and bot status display helpers for culture CLI."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os

from culture.pidfile import is_process_alive, read_pid, remove_pid

from .constants import BOT_CONFIG_FILE
from .ipc import agent_socket_path, ipc_request

logger = logging.getLogger("culture")


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


def _print_running_detail(agent, args, pid) -> None:
    """Print status detail for a running agent with IPC data."""
    query = getattr(args, "full", False)
    resp = asyncio.run(ipc_request(agent_socket_path(agent.nick), "status", query=query))
    if resp and resp.get("ok"):
        data = resp.get("data", {})
        status = "running"
        if data.get("circuit_open"):
            status = "circuit-open"
        elif data.get("paused"):
            status = "paused"
        print(f"  Status:     {status}")
        print(f"  PID:        {pid or '-'}")
        print(f"  Activity:   {data.get('description', 'nothing')}")
        print(f"  Turns:      {data.get('turn_count', 0)}")
        print(f"  Paused:     {'yes' if data.get('paused') else 'no'}")
        print(f"  Circuit:    {'OPEN (not restarting)' if data.get('circuit_open') else 'closed'}")
    else:
        print("  Status:     running")
        print(f"  PID:        {pid or '-'}")
        print("  Activity:   unknown (daemon may need restart)")


def print_agent_detail(agent, config_path: str, args: argparse.Namespace) -> None:
    """Print detailed status for a single agent, including live IPC activity query."""
    status, pid = agent_process_status(agent)
    print(agent.nick)

    if status == "running":
        _print_running_detail(agent, args, pid)
    else:
        print(f"  Status:     {status}")
        print(f"  PID:        {pid or '-'}")
        print("  Activity:   -")

    channels = agent.channels if isinstance(agent.channels, list) else []
    print(f"  Directory:  {agent.directory}")
    print(f"  Backend:    {agent.agent}")
    print(f"  Channels:   {', '.join(channels)}")
    print(f"  Model:      {agent.model}")
    print(f"  Config:     {config_path}")


def _format_agent_status(base_status: str, archived: bool, show_archived_marker: bool) -> str:
    """Format the display status string for an agent."""
    if not archived:
        return base_status
    if show_archived_marker:
        return f"{base_status} (archived)"
    if base_status == "stopped":
        return "archived"
    return base_status


def _fetch_ipc_data(agent) -> dict | None:
    """Fetch full IPC status data from a running agent."""
    resp = asyncio.run(ipc_request(agent_socket_path(agent.nick), "status"))
    if resp and resp.get("ok"):
        return resp.get("data", {})
    return None


def _agent_overview_row(
    agent, show_activity: bool, show_archived_marker: bool
) -> tuple[str, str, str, str]:
    """Build (nick, status, pid_str, activity) for one agent row."""
    base_status, pid = agent_process_status(agent)
    activity = "-"
    if base_status == "running":
        ipc_data = _fetch_ipc_data(agent)
        if ipc_data:
            if ipc_data.get("circuit_open"):
                base_status = "circuit-open"
            elif ipc_data.get("paused"):
                base_status = "paused"
            if show_activity:
                activity = ipc_data.get("description", "nothing")
    status = _format_agent_status(
        base_status, getattr(agent, "archived", False), show_archived_marker
    )
    return agent.nick, status, str(pid or "-"), activity


def print_agents_overview(
    agents: list, show_activity: bool, show_archived_marker: bool = False
) -> None:
    """Print a table of all agents with status, PID, and optionally activity."""
    if show_activity:
        print(f"{'NICK':<30} {'STATUS':<12} {'PID':<10} {'ACTIVITY'}")
        print("-" * 72)
    else:
        print(f"{'NICK':<30} {'STATUS':<12} {'PID':<10}")
        print("-" * 52)

    for agent in agents:
        nick, status, pid_str, activity = _agent_overview_row(
            agent, show_activity, show_archived_marker
        )
        if show_activity:
            print(f"{nick:<30} {status:<12} {pid_str:<10} {activity}")
        else:
            print(f"{nick:<30} {status:<12} {pid_str:<10}")


def _load_bot_configs(*, show_archived: bool = False) -> list:
    """Load valid bot configs from the bots directory.

    Filters out archived bots by default (matching `culture bot list`) and
    skips configs with empty names so malformed entries don't leak into the
    UI.
    """
    from culture.bots.config import BOTS_DIR, load_bot_config

    if not BOTS_DIR.is_dir():
        return []
    configs = []
    for bot_dir in sorted(BOTS_DIR.iterdir()):
        yaml_path = bot_dir / BOT_CONFIG_FILE
        if not yaml_path.is_file():
            continue
        try:
            config = load_bot_config(yaml_path)
        except Exception as exc:
            logger.warning("Failed to load bot config %s: %s", yaml_path, exc)
            continue
        if not config.name:
            continue
        if config.archived and not show_archived:
            continue
        configs.append(config)
    return configs


def print_bot_listing(*, show_archived: bool = False) -> None:
    """Print a table of configured bots (if any exist)."""
    bot_configs = _load_bot_configs(show_archived=show_archived)
    if not bot_configs:
        return
    print()
    print(f"{'BOT':<30} {'TRIGGER':<12} {'CHANNELS'}")
    print("-" * 60)
    for bc in bot_configs:
        channels = ", ".join(bc.channels) if bc.channels else "-"
        name = f"{bc.name} [archived]" if show_archived and bc.archived else bc.name
        print(f"{name:<30} {bc.trigger_type:<12} {channels}")
