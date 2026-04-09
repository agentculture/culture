"""Mesh subcommands: culture mesh {overview,setup,update,console}."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import subprocess
import sys
import time

from culture.config import ServerConfig, load_config, load_config_or_default

from .shared.constants import AGENTS_YAML, CULTURE_DIR, DEFAULT_CONFIG
from .shared.mesh import build_server_start_cmd, generate_mesh_from_agents
from .shared.process import server_stop_by_name, stop_agent

logger = logging.getLogger("culture")

NAME = "mesh"


def register(subparsers: argparse._SubParsersAction) -> None:
    mesh_parser = subparsers.add_parser(
        "mesh", help="Mesh operations (overview, setup, update, console)"
    )
    mesh_sub = mesh_parser.add_subparsers(dest="mesh_command")

    # -- overview -------------------------------------------------------------
    overview_parser = mesh_sub.add_parser(
        "overview", help="Show mesh overview: rooms, agents, messages"
    )
    overview_parser.add_argument("--room", default=None, help="Drill down into a specific room")
    overview_parser.add_argument("--agent", default=None, help="Drill down into a specific agent")
    overview_parser.add_argument(
        "--messages",
        "-n",
        type=int,
        default=4,
        help="Messages per room (default: 4, max: 20)",
    )
    overview_parser.add_argument("--serve", action="store_true", help="Start live web dashboard")
    overview_parser.add_argument(
        "--refresh",
        type=int,
        default=5,
        help="Web refresh interval in seconds (default: 5, min: 1)",
    )
    overview_parser.add_argument("--config", default=DEFAULT_CONFIG)

    # -- setup ----------------------------------------------------------------
    setup_parser = mesh_sub.add_parser("setup", help="Set up mesh from mesh.yaml")
    setup_parser.add_argument(
        "--config",
        default=os.path.expanduser("~/.culture/mesh.yaml"),
        help="Path to mesh.yaml",
    )
    setup_parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove auto-start entries and stop services",
    )

    # -- update ---------------------------------------------------------------
    update_parser = mesh_sub.add_parser("update", help="Upgrade and restart the mesh")
    update_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without executing",
    )
    update_parser.add_argument(
        "--skip-upgrade",
        action="store_true",
        help="Just restart, don't upgrade the package",
    )
    update_parser.add_argument(
        "--config",
        default=os.path.expanduser("~/.culture/mesh.yaml"),
        help="Path to mesh.yaml",
    )

    # -- console --------------------------------------------------------------
    console_parser = mesh_sub.add_parser("console", help="Interactive admin console")
    console_parser.add_argument(
        "server_name",
        nargs="?",
        default=None,
        help="Server to connect to (auto-detects if omitted)",
    )
    console_parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help="Config file path",
    )


def dispatch(args: argparse.Namespace) -> None:
    if not args.mesh_command:
        print("Usage: culture mesh {overview|setup|update|console}", file=sys.stderr)
        sys.exit(1)

    handlers = {
        "overview": _cmd_overview,
        "setup": _cmd_setup,
        "update": _cmd_update,
        "console": _cmd_console,
    }
    handler = handlers.get(args.mesh_command)
    if handler:
        handler(args)
    else:
        print(f"Unknown mesh command: {args.mesh_command}", file=sys.stderr)
        sys.exit(1)


# -----------------------------------------------------------------------
# Overview
# -----------------------------------------------------------------------


def _collect_mesh_data(host: str, port: int, server_name: str, message_limit: int):
    """Collect mesh state, exiting with an error message on connection failure."""
    from culture.overview.collector import collect_mesh_state

    try:
        return asyncio.run(
            collect_mesh_state(
                host=host,
                port=port,
                server_name=server_name,
                message_limit=message_limit,
            )
        )
    except ConnectionRefusedError:
        print(
            f"Error: could not connect to {host}:{port} — is the server running?",
            file=sys.stderr,
        )
        sys.exit(1)
    except TimeoutError:
        print(
            f"Error: server at {host}:{port} not responding — it may still be starting up",
            file=sys.stderr,
        )
        sys.exit(1)
    except OSError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _cmd_overview(args: argparse.Namespace) -> None:
    """Show mesh overview."""
    from culture.overview.renderer_text import render_text

    config = load_config_or_default(args.config)
    message_limit = max(1, min(args.messages, 20))
    refresh_interval = max(1, args.refresh)

    if args.serve:
        from culture.overview.renderer_web import serve_web

        serve_web(
            host=config.server.host,
            port=config.server.port,
            server_name=config.server.name,
            room_filter=args.room,
            agent_filter=args.agent,
            message_limit=message_limit,
            refresh_interval=refresh_interval,
        )
        return

    mesh = _collect_mesh_data(
        config.server.host, config.server.port, config.server.name, message_limit
    )
    output = render_text(
        mesh,
        room_filter=args.room,
        agent_filter=args.agent,
        message_limit=message_limit,
    )
    print(output, end="")


# -----------------------------------------------------------------------
# Console
# -----------------------------------------------------------------------


def _resolve_server(server_name: str | None) -> tuple[str, int] | None:
    """Resolve server name and port from running servers."""
    from culture.pidfile import list_servers, read_default_server, read_port

    if server_name:
        p = read_port(server_name)
        port = p if p else 6667
        return server_name, port

    servers = list_servers()
    if not servers:
        return None

    if len(servers) == 1:
        return servers[0]["name"], servers[0]["port"]

    default = read_default_server()
    if default:
        match = [s for s in servers if s["name"] == default]
        if match:
            return match[0]["name"], match[0]["port"]

    return servers[0]["name"], servers[0]["port"]


def _resolve_console_nick() -> str:
    """Resolve the human nick: git username -> OS user -> config override."""
    import re
    import subprocess

    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0 and result.stdout.strip():
            name = result.stdout.strip().lower()
            name = re.sub(r"[^a-z0-9-]", "", name.replace(" ", "-"))
            if name:
                return name
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    import os

    return os.environ.get("USER", "human")


def _cmd_console(args: argparse.Namespace) -> None:
    """Launch the interactive console TUI."""
    result = _resolve_server(args.server_name)
    if result is None:
        print("No culture servers running. Start one with: culture server start")
        return

    server_name, port = result
    host = "127.0.0.1"

    nick_suffix = _resolve_console_nick()
    nick = f"{server_name}-{nick_suffix}"

    from culture.console.app import ConsoleApp
    from culture.console.client import ConsoleIRCClient

    client = ConsoleIRCClient(host=host, port=port, nick=nick, mode="H")

    async def run():
        await client.connect()
        app = ConsoleApp(irc_client=client, server_name=server_name)
        await app.run_async()

    asyncio.run(run())


# -----------------------------------------------------------------------
# Setup
# -----------------------------------------------------------------------


def _store_mesh_credentials(mesh) -> None:
    """Prompt for link passwords and store them in the OS keyring (never in files)."""
    import getpass

    from culture.credentials import lookup_credential, store_credential

    for link in mesh.server.links:
        existing = lookup_credential(link.name)
        if existing:
            print(f"  Credential for '{link.name}' already in keyring")
        else:
            password = getpass.getpass(f"Link password for {link.name}: ")
            if store_credential(link.name, password):
                print(f"  Stored credential for '{link.name}' in OS keyring")
            else:
                print(
                    f"  Warning: failed to store credential for '{link.name}'",
                    file=sys.stderr,
                )
                print(
                    "  You may need to install secret-tool (Linux)"
                    " or check Keychain access (macOS)",
                    file=sys.stderr,
                )


def _generate_agent_configs(mesh, server_name: str) -> None:
    """Generate agents.yaml for each agent workdir defined in the mesh config."""
    from culture.clients.claude.config import AgentConfig as BaseAgentConfig
    from culture.clients.claude.config import (
        DaemonConfig,
        ServerConnConfig,
        save_config,
    )

    workdir_agents: dict[str, list] = {}
    for agent in mesh.agents:
        workdir = os.path.expanduser(agent.workdir)
        workdir_agents.setdefault(workdir, []).append(agent)

    for workdir, agents in workdir_agents.items():
        os.makedirs(workdir, exist_ok=True)
        config_path = os.path.join(workdir, CULTURE_DIR, AGENTS_YAML)
        os.makedirs(os.path.dirname(config_path), exist_ok=True)

        agent_configs = []
        for a in agents:
            full_nick = f"{server_name}-{a.nick}"
            agent_configs.append(
                BaseAgentConfig(
                    nick=full_nick,
                    agent=a.type,
                    directory=workdir,
                    channels=a.channels,
                )
            )

        daemon_config = DaemonConfig(
            server=ServerConnConfig(name=server_name, host="localhost", port=mesh.server.port),
            agents=agent_configs,
        )
        save_config(config_path, daemon_config)
        print(f"  Wrote {config_path}")


def _install_mesh_services(mesh, server_name: str, culture_bin: str, config_path: str) -> None:
    """Install auto-start service entries for the server and all agents."""
    from culture.persistence import install_service

    server_cmd = build_server_start_cmd(mesh, culture_bin, config_path)
    svc_name = f"culture-server-{server_name}"
    path = install_service(svc_name, server_cmd, f"culture server {server_name}")
    print(f"  Installed {svc_name} → {path}")

    for agent in mesh.agents:
        full_nick = f"{server_name}-{agent.nick}"
        workdir = os.path.expanduser(agent.workdir)
        agent_config_path = os.path.join(workdir, CULTURE_DIR, AGENTS_YAML)
        agent_cmd = [
            culture_bin,
            "agent",
            "start",
            full_nick,
            "--foreground",
            "--config",
            agent_config_path,
        ]
        agent_svc = f"culture-agent-{full_nick}"
        path = install_service(agent_svc, agent_cmd, f"culture agent {full_nick}")
        print(f"  Installed {agent_svc} → {path}")


def _cmd_setup(args: argparse.Namespace) -> None:
    from culture.mesh_config import load_mesh_config
    from culture.persistence import list_services, uninstall_service

    try:
        mesh = load_mesh_config(args.config)
    except FileNotFoundError:
        mesh = generate_mesh_from_agents(args.config)
        if mesh is None:
            sys.exit(1)

    server_name = mesh.server.name

    if args.uninstall:
        print("Uninstalling culture services...")
        expected = {f"culture-server-{server_name}"}
        for agent in mesh.agents:
            expected.add(f"culture-agent-{server_name}-{agent.nick}")
        for svc in list_services():
            if svc in expected:
                print(f"  Removing {svc}")
                uninstall_service(svc)
        server_stop_by_name(server_name)
        for agent in mesh.agents:
            full_nick = f"{server_name}-{agent.nick}"
            stop_agent(full_nick)
        print("Done.")
        return

    _store_mesh_credentials(mesh)

    _generate_agent_configs(mesh, server_name)

    culture_bin = shutil.which("culture") or "culture"
    _install_mesh_services(mesh, server_name, culture_bin, args.config)

    print(f"\nSetup complete for mesh node '{server_name}'.")
    print("Services installed. Start with your service manager or reboot.")


# -----------------------------------------------------------------------
# Update
# -----------------------------------------------------------------------


def _find_upgrade_tool() -> tuple[str, list[str]] | None:
    """Return (tool_name, command_args) for the first available upgrade tool, or None."""
    uv = shutil.which("uv")
    if uv:
        return "uv", [uv, "tool", "upgrade", "culture"]

    pip = shutil.which("pip") or shutil.which("pip3")
    if pip:
        return "pip", [pip, "install", "--upgrade", "culture"]

    return None


def _run_upgrade(tool_name: str, cmd: list[str]) -> None:
    """Run the upgrade subprocess and exit on failure."""
    print(f"Upgrading via {tool_name}...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if tool_name == "uv":
        print(result.stdout.strip() if result.stdout else "")
    if result.returncode != 0:
        print(f"{tool_name} upgrade failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)


def _upgrade_culture_package(args: argparse.Namespace) -> bool:
    """Upgrade the culture package via uv or pip, then re-exec with --skip-upgrade."""
    if args.skip_upgrade:
        return True

    if args.dry_run:
        print("[dry-run] Would run: uv tool upgrade culture")
        print("[dry-run] Would re-exec with --skip-upgrade")
        return False

    tool = _find_upgrade_tool()
    if tool is None:
        print("Neither uv nor pip found", file=sys.stderr)
        sys.exit(1)

    _run_upgrade(*tool)

    culture_bin = shutil.which("culture") or "culture"
    reexec_args = [culture_bin, "mesh", "update", "--skip-upgrade", "--config", args.config]
    print("Re-executing with updated code...")
    if sys.platform == "win32":
        sys.exit(subprocess.run(reexec_args).returncode)
    else:
        os.execvp(culture_bin, reexec_args)


def _wait_for_server_port(port: int, retries: int = 50, interval: float = 0.1) -> None:
    """Poll until *port* accepts a TCP connection."""
    import socket as _socket

    for _ in range(retries):
        try:
            with _socket.create_connection(("localhost", port), timeout=1):
                return
        except OSError:
            time.sleep(interval)


def _dry_run_restart(mesh, server_name: str) -> None:
    """Print what a restart would do without executing."""
    for agent in mesh.agents:
        print(f"[dry-run] Would stop agent {server_name}-{agent.nick}")
    print(f"[dry-run] Would stop server {server_name}")
    print("[dry-run] Would regenerate auto-start entries")
    print(f"[dry-run] Would start server {server_name}")
    for agent in mesh.agents:
        print(f"[dry-run] Would start agent {server_name}-{agent.nick}")


def _restart_single_service(svc_name: str, fallback_cmd: list[str], restart_service_fn) -> None:
    """Restart a service, falling back to a CLI command if no service file exists."""
    print(f"  Restarting {svc_name}...")
    if restart_service_fn(svc_name):
        return
    if sys.platform == "win32":
        print(
            "  No service file found. Run 'culture mesh setup' to install services; starting via CLI...",
            file=sys.stderr,
        )
    else:
        print("  No service file found, starting via CLI...")
    subprocess.run(fallback_cmd, check=False)


def _restart_mesh_services(
    mesh, server_name: str, culture_bin: str, config_path: str, dry_run: bool
) -> None:
    """Stop agents and server, regenerate service entries, then restart everything."""
    print(f"Restarting mesh node '{server_name}'...")

    if dry_run:
        _dry_run_restart(mesh, server_name)
        return

    for agent in mesh.agents:
        full_nick = f"{server_name}-{agent.nick}"
        print(f"  Stopping {full_nick}...")
        stop_agent(full_nick)

    print(f"  Stopping server {server_name}...")
    server_stop_by_name(server_name)

    from culture.persistence import install_service, restart_service

    server_cmd = build_server_start_cmd(mesh, culture_bin, config_path)
    install_service(f"culture-server-{server_name}", server_cmd, f"culture server {server_name}")

    for agent in mesh.agents:
        full_nick = f"{server_name}-{agent.nick}"
        workdir = os.path.expanduser(agent.workdir)
        agent_config_path = os.path.join(workdir, CULTURE_DIR, AGENTS_YAML)
        agent_cmd = [
            culture_bin,
            "agent",
            "start",
            full_nick,
            "--foreground",
            "--config",
            agent_config_path,
        ]
        install_service(f"culture-agent-{full_nick}", agent_cmd, f"culture agent {full_nick}")

    server_svc = f"culture-server-{server_name}"
    server_fallback = [
        culture_bin,
        "server",
        "start",
        "--name",
        server_name,
        "--host",
        mesh.server.host,
        "--port",
        str(mesh.server.port),
        "--mesh-config",
        config_path,
    ]
    _restart_single_service(server_svc, server_fallback, restart_service)

    _wait_for_server_port(mesh.server.port)

    for agent in mesh.agents:
        full_nick = f"{server_name}-{agent.nick}"
        agent_svc = f"culture-agent-{full_nick}"
        workdir = os.path.expanduser(agent.workdir)
        agent_config_path = os.path.join(workdir, CULTURE_DIR, AGENTS_YAML)
        agent_fallback = [
            culture_bin,
            "agent",
            "start",
            full_nick,
            "--config",
            agent_config_path,
        ]
        _restart_single_service(agent_svc, agent_fallback, restart_service)

    print()


def _resolve_mesh_for_server(server_name: str, config_path: str):
    """Find or build a MeshConfig for *server_name*.

    Resolution order:
    1. mesh.yaml — use directly if its server.name matches.
    2. agents.yaml — build via from_daemon_config(), preserving host, port,
       and links from the old mesh.yaml. Saves the updated mesh.yaml so
       future runs are consistent.
    """
    from culture.mesh_config import (
        from_daemon_config,
        load_mesh_config,
        merge_links,
        save_mesh_config,
    )

    old_server = None
    try:
        old_mesh = load_mesh_config(config_path)
        if old_mesh.server.name == server_name:
            return old_mesh
        old_server = old_mesh.server
    except FileNotFoundError:
        pass

    if os.path.isfile(DEFAULT_CONFIG):
        daemon_config = load_config(DEFAULT_CONFIG)
        if daemon_config.server.name == server_name:
            mesh = from_daemon_config(daemon_config)
            if old_server is not None:
                mesh.server.host = old_server.host
                mesh.server.port = old_server.port
                merge_links(mesh, old_server.links)
            save_mesh_config(mesh, config_path)
            return mesh

    return None


def _cmd_update(args: argparse.Namespace) -> None:
    from culture.mesh_config import load_mesh_config
    from culture.pidfile import list_servers

    if not _upgrade_culture_package(args):
        return

    culture_bin = shutil.which("culture") or "culture"

    running = list_servers()

    if running:
        for srv in running:
            mesh = _resolve_mesh_for_server(srv["name"], args.config)
            if mesh is None:
                print(
                    f"  Warning: no config found for server '{srv['name']}', skipping",
                    file=sys.stderr,
                )
                continue
            _restart_mesh_services(mesh, srv["name"], culture_bin, args.config, args.dry_run)
    else:
        try:
            mesh = load_mesh_config(args.config)
        except FileNotFoundError:
            mesh = generate_mesh_from_agents(args.config)
            if mesh is None:
                sys.exit(1)
        _restart_mesh_services(mesh, mesh.server.name, culture_bin, args.config, args.dry_run)

    print("Update complete. All services restarted.")
