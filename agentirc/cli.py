"""Unified CLI entry point for agentirc.

Subcommands:
    agentirc server start|stop|status   Manage the IRC server daemon
    agentirc init                       Register an agent for the current directory
    agentirc start [nick] [--all]       Start agent daemon(s)
    agentirc stop [nick] [--all]        Stop agent daemon(s)
    agentirc status [nick] [--full]     List running agents (--full queries activity)
    agentirc send <target> <message>    Send a message to a channel or agent
    agentirc read <channel>             Read recent channel messages
    agentirc who <channel>              List channel members
    agentirc channels                   List active channels
    agentirc learn [--nick X]            Print self-teaching prompt for your agent
    agentirc sleep [nick] [--all]       Pause agent(s) — stay connected but idle
    agentirc wake [nick] [--all]        Resume paused agent(s)
    agentirc overview [--room X] [--agent X] Show mesh overview
    agentirc setup [--config X] [--uninstall] Set up mesh from mesh.yaml
    agentirc update [--dry-run] [--skip-upgrade] Upgrade and restart mesh
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time

from agentirc.clients.claude.config import (
    AgentConfig,
    DaemonConfig,
    ServerConnConfig,
    add_agent_to_config,
    load_config,
    load_config_or_default,
    sanitize_agent_name,
)
from agentirc.pidfile import is_process_alive, read_pid, remove_pid, write_pid

logger = logging.getLogger("agentirc")


def _parse_link(value: str):
    """Parse a link spec: name:host:port:password[:trust]

    Trust is extracted from the end if it matches a known value.
    This allows passwords containing colons.
    """
    from agentirc.server.config import LinkConfig

    # Check if the last segment is a trust level
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

DEFAULT_CONFIG = os.path.expanduser("~/.agentirc/agents.yaml")
LOG_DIR = os.path.expanduser("~/.agentirc/logs")


# -----------------------------------------------------------------------
# Main entry point
# -----------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentirc",
        description="agentirc — AI agent IRC mesh",
    )
    sub = parser.add_subparsers(dest="command")

    # -- server subcommand -------------------------------------------------
    server_parser = sub.add_parser("server", help="Manage the IRC server")
    server_sub = server_parser.add_subparsers(dest="server_command")

    srv_start = server_sub.add_parser("start", help="Start the IRC server daemon")
    srv_start.add_argument("--name", default="agentirc", help="Server name")
    srv_start.add_argument("--host", default="0.0.0.0", help="Listen address")
    srv_start.add_argument("--port", type=int, default=6667, help="Listen port")
    srv_start.add_argument(
        "--link", type=_parse_link, action="append", default=[],
        help="Link to peer: name:host:port:password",
    )
    srv_start.add_argument(
        "--mesh-config", default=None,
        help="Read links from mesh.yaml + OS keyring (no passwords in CLI args)",
    )
    srv_start.add_argument(
        "--foreground", action="store_true",
        help="Run in foreground (for service managers)",
    )

    srv_stop = server_sub.add_parser("stop", help="Stop the IRC server daemon")
    srv_stop.add_argument("--name", default="agentirc", help="Server name")

    srv_status = server_sub.add_parser("status", help="Check server daemon status")
    srv_status.add_argument("--name", default="agentirc", help="Server name")

    # -- init subcommand ---------------------------------------------------
    init_parser = sub.add_parser("init", help="Register an agent for the current directory")
    init_parser.add_argument("--server", default=None, help="Server name prefix")
    init_parser.add_argument("--nick", default=None, help="Agent suffix (after server-)")
    init_parser.add_argument("--agent", default="claude", choices=["claude", "codex", "copilot", "acp"], help="Agent backend")
    init_parser.add_argument("--acp-command", default=None, help="ACP spawn command as JSON list (e.g. '[\"cline\",\"--acp\"]')")
    init_parser.add_argument("--config", default=DEFAULT_CONFIG, help="Config file path")

    # -- start subcommand --------------------------------------------------
    start_parser = sub.add_parser("start", help="Start agent daemon(s)")
    start_parser.add_argument("nick", nargs="?", help="Agent nick to start")
    start_parser.add_argument("--all", action="store_true", help="Start all agents")
    start_parser.add_argument("--config", default=DEFAULT_CONFIG, help="Config file path")
    start_parser.add_argument(
        "--foreground", action="store_true",
        help="Run in foreground (for service managers)",
    )

    # -- stop subcommand ---------------------------------------------------
    stop_parser = sub.add_parser("stop", help="Stop agent daemon(s)")
    stop_parser.add_argument("nick", nargs="?", help="Agent nick to stop")
    stop_parser.add_argument("--all", action="store_true", help="Stop all agents")
    stop_parser.add_argument("--config", default=DEFAULT_CONFIG, help="Config file path")

    # -- status subcommand -------------------------------------------------
    status_parser = sub.add_parser("status", help="List running agents")
    status_parser.add_argument("nick", nargs="?", help="Show detailed status for a specific agent")
    status_parser.add_argument("--full", action="store_true", help="Query agents for activity status")
    status_parser.add_argument("--config", default=DEFAULT_CONFIG, help="Config file path")

    # -- read subcommand ---------------------------------------------------
    read_parser = sub.add_parser("read", help="Read recent channel messages")
    read_parser.add_argument("channel", help="Channel name (e.g. #general)")
    read_parser.add_argument("--limit", "-n", type=int, default=50, help="Number of messages")
    read_parser.add_argument("--config", default=DEFAULT_CONFIG, help="Config file path")

    # -- who subcommand ----------------------------------------------------
    who_parser = sub.add_parser("who", help="List members of a channel")
    who_parser.add_argument("channel", help="Channel or nick target")
    who_parser.add_argument("--config", default=DEFAULT_CONFIG, help="Config file path")

    # -- send subcommand ---------------------------------------------------
    send_parser = sub.add_parser("send", help="Send a message to a channel or agent")
    send_parser.add_argument("target", help="Channel (e.g. #general) or agent nick")
    send_parser.add_argument("message", help="Message text to send")
    send_parser.add_argument("--config", default=DEFAULT_CONFIG, help="Config file path")

    # -- channels subcommand -----------------------------------------------
    channels_parser = sub.add_parser("channels", help="List active channels")
    channels_parser.add_argument("--config", default=DEFAULT_CONFIG, help="Config file path")

    # -- learn subcommand --------------------------------------------------
    learn_parser = sub.add_parser("learn", help="Print self-teaching prompt for your agent")
    learn_parser.add_argument("--nick", default=None, help="Agent nick (auto-detects from cwd)")
    learn_parser.add_argument("--config", default=DEFAULT_CONFIG, help="Config file path")

    # -- sleep subcommand --------------------------------------------------
    sleep_parser = sub.add_parser("sleep", help="Pause agent(s) — stay connected but idle")
    sleep_parser.add_argument("nick", nargs="?", help="Agent nick to pause")
    sleep_parser.add_argument("--all", action="store_true", help="Pause all agents")
    sleep_parser.add_argument("--config", default=DEFAULT_CONFIG, help="Config file path")

    # -- wake subcommand ---------------------------------------------------
    wake_parser = sub.add_parser("wake", help="Resume paused agent(s)")
    wake_parser.add_argument("nick", nargs="?", help="Agent nick to resume")
    wake_parser.add_argument("--all", action="store_true", help="Resume all agents")
    wake_parser.add_argument("--config", default=DEFAULT_CONFIG, help="Config file path")

    # -- skills subcommand -------------------------------------------------
    skills_parser = sub.add_parser("skills", help="Install IRC skills for AI agents")
    skills_sub = skills_parser.add_subparsers(dest="skills_command")
    skills_install = skills_sub.add_parser("install", help="Install IRC skill for an agent")
    skills_install.add_argument(
        "target", choices=["claude", "codex", "copilot", "acp", "opencode", "all"],
        help="Target agent: claude, codex, copilot, acp, opencode (alias of acp), or all",
    )

    # -- overview subcommand -----------------------------------------------
    overview_parser = sub.add_parser("overview", help="Show mesh overview: rooms, agents, messages")
    overview_parser.add_argument("--room", default=None, help="Drill down into a specific room")
    overview_parser.add_argument("--agent", default=None, help="Drill down into a specific agent")
    overview_parser.add_argument("--messages", "-n", type=int, default=4, help="Messages per room (default: 4, max: 20)")
    overview_parser.add_argument("--serve", action="store_true", help="Start live web dashboard")
    overview_parser.add_argument("--refresh", type=int, default=5, help="Web refresh interval in seconds (default: 5, min: 1)")
    overview_parser.add_argument("--config", default=DEFAULT_CONFIG)

    # -- setup subcommand --------------------------------------------------
    setup_parser = sub.add_parser("setup", help="Set up mesh from mesh.yaml")
    setup_parser.add_argument(
        "--config", default=os.path.expanduser("~/.agentirc/mesh.yaml"),
        help="Path to mesh.yaml",
    )
    setup_parser.add_argument(
        "--uninstall", action="store_true",
        help="Remove auto-start entries and stop services",
    )

    # -- update subcommand -------------------------------------------------
    update_parser = sub.add_parser("update", help="Upgrade and restart the mesh")
    update_parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would happen without executing",
    )
    update_parser.add_argument(
        "--skip-upgrade", action="store_true",
        help="Just restart, don't upgrade the package",
    )
    update_parser.add_argument(
        "--config", default=os.path.expanduser("~/.agentirc/mesh.yaml"),
        help="Path to mesh.yaml",
    )

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    try:
        dispatch = {
            "server": _cmd_server,
            "init": _cmd_init,
            "start": _cmd_start,
            "stop": _cmd_stop,
            "status": _cmd_status,
            "send": _cmd_send,
            "read": _cmd_read,
            "who": _cmd_who,
            "channels": _cmd_channels,
            "learn": _cmd_learn,
            "sleep": _cmd_sleep,
            "wake": _cmd_wake,
            "skills": _cmd_skills,
            "overview": _cmd_overview,
            "setup": _cmd_setup,
            "update": _cmd_update,
        }
        handler = dispatch.get(args.command)
        if handler:
            handler(args)
        else:
            parser.print_help()
            sys.exit(1)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


# -----------------------------------------------------------------------
# Server subcommands
# -----------------------------------------------------------------------

def _cmd_server(args: argparse.Namespace) -> None:
    if not args.server_command:
        print("Usage: agentirc server {start|stop|status}", file=sys.stderr)
        sys.exit(1)

    if args.server_command == "start":
        _server_start(args)
    elif args.server_command == "stop":
        _server_stop(args)
    elif args.server_command == "status":
        _server_status(args)


def _server_start(args: argparse.Namespace) -> None:
    pid_name = f"server-{args.name}"

    # Check if already running
    existing = read_pid(pid_name)
    if existing and is_process_alive(existing):
        print(f"Server '{args.name}' is already running (PID {existing})")
        sys.exit(1)

    # Resolve links: --mesh-config reads from mesh.yaml + OS keyring (no passwords in CLI)
    links = list(args.link)  # from --link args (may include passwords for manual use)
    if getattr(args, "mesh_config", None):
        links = _resolve_links_from_mesh(args.mesh_config)

    if getattr(args, "foreground", False):
        # Foreground mode — run directly (for service managers)
        write_pid(pid_name, os.getpid())
        os.makedirs(LOG_DIR, exist_ok=True)
        print(f"Server '{args.name}' starting in foreground (PID {os.getpid()})")
        print(f"  Listening on {args.host}:{args.port}")
        try:
            asyncio.run(_run_server(args.name, args.host, args.port, links))
        finally:
            remove_pid(pid_name)
        return

    if sys.platform == "win32":
        print("Daemon mode not supported on Windows. Use --foreground.", file=sys.stderr)
        sys.exit(1)

    # Fork to daemonize
    pid = os.fork()
    if pid > 0:
        time.sleep(0.2)
        if is_process_alive(pid):
            print(f"Server '{args.name}' started (PID {pid})")
            print(f"  Listening on {args.host}:{args.port}")
            print(f"  Logs: {LOG_DIR}/server-{args.name}.log")
        else:
            print(f"Server '{args.name}' failed to start", file=sys.stderr)
            sys.exit(1)
        return

    # Child: detach from parent session
    os.setsid()

    # Redirect stdout/stderr to log file
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, f"server-{args.name}.log")
    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
    os.close(log_fd)

    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)

    write_pid(pid_name, os.getpid())

    try:
        asyncio.run(_run_server(args.name, args.host, args.port, links))
    finally:
        remove_pid(pid_name)
        os._exit(0)


async def _run_server(name: str, host: str, port: int, links: list | None = None) -> None:
    """Run the IRC server (called in the daemon child process)."""
    from agentirc.server.config import ServerConfig
    from agentirc.server.ircd import IRCd

    config = ServerConfig(name=name, host=host, port=port, links=links or [])
    ircd = IRCd(config)
    await ircd.start()
    logger.info("Server '%s' listening on %s:%d", name, host, port)

    # Connect to configured peers
    for lc in config.links:
        try:
            await ircd.connect_to_peer(lc.host, lc.port, lc.password, lc.trust)
            logger.info("Linking to %s at %s:%d", lc.name, lc.host, lc.port)
        except Exception as e:
            logger.error("Failed to link to %s: %s — will retry", lc.name, e)
            ircd.maybe_retry_link(lc.name)

    stop_event = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    await stop_event.wait()
    logger.info("Server '%s' shutting down", name)
    await ircd.stop()


def _server_stop(args: argparse.Namespace) -> None:
    pid_name = f"server-{args.name}"
    pid = read_pid(pid_name)

    if pid is None:
        print(f"No PID file for server '{args.name}'")
        sys.exit(1)

    if not is_process_alive(pid):
        print(f"Server '{args.name}' is not running (stale PID {pid})")
        remove_pid(pid_name)
        return

    print(f"Stopping server '{args.name}' (PID {pid})...")
    os.kill(pid, signal.SIGTERM)

    # Wait up to 5 seconds for graceful shutdown
    for _ in range(50):
        if not is_process_alive(pid):
            print(f"Server '{args.name}' stopped")
            remove_pid(pid_name)
            return
        time.sleep(0.1)

    # Force kill
    if sys.platform == "win32":
        print(f"Server '{args.name}' did not stop gracefully, terminating")
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    else:
        print(f"Server '{args.name}' did not stop gracefully, sending SIGKILL")
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    remove_pid(pid_name)
    print(f"Server '{args.name}' killed")


def _server_status(args: argparse.Namespace) -> None:
    pid_name = f"server-{args.name}"
    pid = read_pid(pid_name)

    if pid is None:
        print(f"Server '{args.name}': not running (no PID file)")
        return

    if is_process_alive(pid):
        print(f"Server '{args.name}': running (PID {pid})")
    else:
        print(f"Server '{args.name}': not running (stale PID {pid})")
        remove_pid(pid_name)


# -----------------------------------------------------------------------
# Agent init
# -----------------------------------------------------------------------

def _cmd_init(args: argparse.Namespace) -> None:
    config = load_config_or_default(args.config)

    # Determine server name
    server_name = args.server or config.server.name or "agentirc"

    # Determine agent suffix
    if args.nick:
        suffix = args.nick
    else:
        dirname = os.path.basename(os.getcwd())
        suffix = sanitize_agent_name(dirname)

    full_nick = f"{server_name}-{suffix}"

    # Check for collision
    for existing in config.agents:
        if existing.nick == full_nick:
            channels = existing.channels if isinstance(existing.channels, list) else []
            print(f"Agent '{full_nick}' already exists in config",
                  file=sys.stderr)
            print(f"  Directory: {existing.directory}", file=sys.stderr)
            print(f"  Backend:   {existing.agent}", file=sys.stderr)
            print(f"  Channels:  {', '.join(channels)}", file=sys.stderr)
            print(f"  Model:     {existing.model}", file=sys.stderr)
            print(f"  Config:    {args.config}", file=sys.stderr)
            print(file=sys.stderr)
            print(f"Start with: agentirc start {full_nick}", file=sys.stderr)
            sys.exit(1)

    # Use backend-specific config for correct defaults
    if args.agent == "codex":
        from agentirc.clients.codex.config import AgentConfig as CodexAgentConfig
        agent = CodexAgentConfig(
            nick=full_nick,
            agent="codex",
            directory=os.getcwd(),
            channels=["#general"],
        )
    elif args.agent == "copilot":
        from agentirc.clients.copilot.config import AgentConfig as CopilotAgentConfig
        agent = CopilotAgentConfig(
            nick=full_nick,
            agent="copilot",
            directory=os.getcwd(),
            channels=["#general"],
        )
    elif args.agent == "acp":
        import json as _json
        from agentirc.clients.acp.config import AgentConfig as ACPAgentConfig
        acp_cmd = ["opencode", "acp"]
        if args.acp_command:
            try:
                acp_cmd = _json.loads(args.acp_command)
            except _json.JSONDecodeError:
                # Treat as a simple command name (e.g. "cline --acp")
                acp_cmd = args.acp_command.split()
        if not isinstance(acp_cmd, list) or not acp_cmd or not all(isinstance(s, str) for s in acp_cmd):
            print("Error: --acp-command must be a non-empty list of strings", file=sys.stderr)
            sys.exit(1)
        agent = ACPAgentConfig(
            nick=full_nick,
            agent="acp",
            acp_command=acp_cmd,
            directory=os.getcwd(),
            channels=["#general"],
        )
    else:
        agent = AgentConfig(
            nick=full_nick,
            agent=args.agent,
            directory=os.getcwd(),
            channels=["#general"],
        )

    add_agent_to_config(args.config, agent, server_name=server_name)

    print(f"Agent registered: {full_nick}")
    print(f"  Directory: {agent.directory}")
    print(f"  Channels: {', '.join(agent.channels)}")
    print(f"  Config: {args.config}")
    print()
    print(f"Start with: agentirc start {full_nick}")


# -----------------------------------------------------------------------
# Agent start
# -----------------------------------------------------------------------

def _cmd_start(args: argparse.Namespace) -> None:
    config = load_config(args.config)

    if args.all:
        agents = config.agents
    elif args.nick:
        agent = config.get_agent(args.nick)
        if not agent:
            print(f"Agent '{args.nick}' not found in config", file=sys.stderr)
            sys.exit(1)
        agents = [agent]
    else:
        # Auto-select if exactly one agent configured
        if len(config.agents) == 1:
            agents = config.agents
        elif len(config.agents) == 0:
            print("No agents configured. Run 'agentirc init' first.", file=sys.stderr)
            sys.exit(1)
        else:
            print(
                "Multiple agents configured. Specify a nick or use --all.",
                file=sys.stderr,
            )
            for a in config.agents:
                print(f"  {a.nick}", file=sys.stderr)
            sys.exit(1)

    if not agents:
        print("No agents configured", file=sys.stderr)
        sys.exit(1)

    # Best-effort check that the IRC server is reachable before starting agent(s)
    import socket as _socket

    server_name = config.server.name
    host, port = config.server.host, config.server.port
    try:
        with _socket.create_connection((host, port), timeout=2):
            pass
    except (ConnectionRefusedError, OSError):
        # TCP probe failed — add PID hint if available
        hint = ""
        server_pid = read_pid(f"server-{server_name}")
        if not server_pid or not is_process_alive(server_pid):
            hint = f"\nStart it with: agentirc server start --name {server_name}"
        print(
            f"Error: cannot connect to IRC server at {host}:{port}.{hint}",
            file=sys.stderr,
        )
        sys.exit(1)

    foreground = getattr(args, "foreground", False)

    if foreground:
        if len(agents) != 1:
            print("--foreground requires a single agent nick, not --all", file=sys.stderr)
            sys.exit(1)
        agent = agents[0]
        print(f"Starting agent {agent.nick} in foreground...")
        asyncio.run(_run_single_agent(config, agent))
    else:
        if sys.platform == "win32":
            if len(agents) == 1:
                # Windows has no fork — run single agent in foreground
                agent = agents[0]
                print(f"Starting agent {agent.nick}...")
                asyncio.run(_run_single_agent(config, agent))
            else:
                print("Multi-agent daemon mode not supported on Windows. Start agents individually.", file=sys.stderr)
                sys.exit(1)
        else:
            # Daemonize all agents (fork each into background)
            _run_multi_agents(config, agents)


async def _run_single_agent(config: DaemonConfig, agent: AgentConfig) -> None:
    """Run a single agent daemon in the foreground."""
    backend = getattr(agent, "agent", "claude")

    if backend == "codex":
        from agentirc.clients.codex.daemon import CodexDaemon
        from agentirc.clients.codex.config import (
            DaemonConfig as CodexDaemonConfig,
        )
        # Re-load config through Codex module for correct supervisor defaults
        codex_config = CodexDaemonConfig(
            server=config.server,
            webhooks=config.webhooks,
            buffer_size=config.buffer_size,
            agents=config.agents,
        )
        daemon = CodexDaemon(codex_config, agent)
    elif backend in ("acp", "opencode"):
        from agentirc.clients.acp.daemon import ACPDaemon
        from agentirc.clients.acp.config import (
            DaemonConfig as ACPDaemonConfig,
            AgentConfig as ACPAgentConfig,
        )
        # Re-load config through ACP module for correct supervisor defaults
        acp_config = ACPDaemonConfig(
            server=config.server,
            webhooks=config.webhooks,
            buffer_size=config.buffer_size,
            agents=config.agents,
        )
        # Backward compat: opencode -> acp with default command
        if not isinstance(agent, ACPAgentConfig):
            acp_agent = ACPAgentConfig(
                nick=agent.nick,
                agent="acp",
                acp_command=getattr(agent, "acp_command", None) or ["opencode", "acp"],
                directory=agent.directory,
                channels=list(agent.channels),
                model=agent.model,
                system_prompt=agent.system_prompt,
                tags=list(agent.tags),
            )
        else:
            acp_agent = agent
        daemon = ACPDaemon(acp_config, acp_agent)
    elif backend == "copilot":
        from agentirc.clients.copilot.daemon import CopilotDaemon
        from agentirc.clients.copilot.config import (
            DaemonConfig as CopilotDaemonConfig,
        )
        # Re-load config through Copilot module for correct supervisor defaults
        copilot_config = CopilotDaemonConfig(
            server=config.server,
            webhooks=config.webhooks,
            buffer_size=config.buffer_size,
            agents=config.agents,
        )
        daemon = CopilotDaemon(copilot_config, agent)
    else:
        from agentirc.clients.claude.daemon import AgentDaemon
        daemon = AgentDaemon(config, agent)

    stop_event = asyncio.Event()
    daemon.set_stop_event(stop_event)

    await daemon.start()
    logger.info("Agent %s started (backend=%s)", agent.nick, backend)

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    await stop_event.wait()
    logger.info("Shutting down %s", agent.nick)
    await daemon.stop()


def _run_multi_agents(config: DaemonConfig, agents: list[AgentConfig]) -> None:
    """Fork each agent into its own background process."""
    for agent in agents:
        pid = os.fork()
        if pid == 0:
            # Child: detach and run
            os.setsid()

            # Redirect output to log
            os.makedirs(LOG_DIR, exist_ok=True)
            log_path = os.path.join(LOG_DIR, f"agent-{agent.nick}.log")
            log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
            os.dup2(log_fd, 1)
            os.dup2(log_fd, 2)
            os.close(log_fd)

            devnull = os.open(os.devnull, os.O_RDONLY)
            os.dup2(devnull, 0)
            os.close(devnull)

            pid_name = f"agent-{agent.nick}"
            write_pid(pid_name, os.getpid())

            try:
                asyncio.run(_run_single_agent(config, agent))
            finally:
                remove_pid(pid_name)
                os._exit(0)
        else:
            print(f"Started {agent.nick} (PID {pid})")


# -----------------------------------------------------------------------
# Agent stop
# -----------------------------------------------------------------------

def _cmd_stop(args: argparse.Namespace) -> None:
    config = load_config_or_default(args.config)

    if args.all:
        agents = config.agents
    elif args.nick:
        agent = config.get_agent(args.nick)
        if not agent:
            print(f"Agent '{args.nick}' not found in config", file=sys.stderr)
            sys.exit(1)
        agents = [agent]
    else:
        if len(config.agents) == 1:
            agents = config.agents
        elif len(config.agents) == 0:
            print("No agents configured", file=sys.stderr)
            sys.exit(1)
        else:
            print(
                "Multiple agents configured. Specify a nick or use --all.",
                file=sys.stderr,
            )
            sys.exit(1)

    for agent in agents:
        _stop_agent(agent.nick)


def _stop_agent(nick: str) -> None:
    """Stop a single agent by trying IPC shutdown first, then PID file."""
    # Try Unix socket IPC shutdown
    socket_path = os.path.join(
        os.environ.get("XDG_RUNTIME_DIR", "/tmp"),
        f"agentirc-{nick}.sock",
    )

    if os.path.exists(socket_path):
        try:
            success = asyncio.run(_ipc_shutdown(socket_path))
            if success:
                print(f"Agent '{nick}' shutdown requested via IPC")
                # Wait for process to exit
                pid_name = f"agent-{nick}"
                pid = read_pid(pid_name)
                if pid:
                    for _ in range(50):
                        if not is_process_alive(pid):
                            remove_pid(pid_name)
                            print(f"Agent '{nick}' stopped")
                            return
                        time.sleep(0.1)
                    # If still alive after 5s, fall through to SIGTERM
                else:
                    print(f"Agent '{nick}' stopped")
                    return
        except Exception:
            pass  # Fall through to PID-based stop

    # Fall back to PID file
    pid_name = f"agent-{nick}"
    pid = read_pid(pid_name)

    if pid is None:
        print(f"No PID file for agent '{nick}'")
        return

    if not is_process_alive(pid):
        print(f"Agent '{nick}' is not running (stale PID {pid})")
        remove_pid(pid_name)
        return

    print(f"Stopping agent '{nick}' (PID {pid})...")
    os.kill(pid, signal.SIGTERM)

    for _ in range(50):
        if not is_process_alive(pid):
            print(f"Agent '{nick}' stopped")
            remove_pid(pid_name)
            return
        time.sleep(0.1)

    # Force kill
    if sys.platform == "win32":
        print(f"Agent '{nick}' did not stop gracefully, terminating")
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    else:
        print(f"Agent '{nick}' did not stop gracefully, sending SIGKILL")
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    remove_pid(pid_name)
    print(f"Agent '{nick}' killed")


async def _ipc_request(socket_path: str, msg_type: str, **kwargs) -> dict | None:
    """Send an IPC request via Unix socket and return the response."""
    from agentirc.clients.claude.ipc import decode_message, encode_message, make_request

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
        # Read lines until we get a response (skip whispers)
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


async def _ipc_shutdown(socket_path: str) -> bool:
    """Send a shutdown command via Unix socket IPC."""
    resp = await _ipc_request(socket_path, "shutdown")
    return resp is not None and resp.get("ok", False)


# -----------------------------------------------------------------------
# Agent status
# -----------------------------------------------------------------------

def _agent_socket_path(nick: str) -> str:
    return os.path.join(
        os.environ.get("XDG_RUNTIME_DIR", "/tmp"),
        f"agentirc-{nick}.sock",
    )


def _agent_process_status(agent) -> tuple[str, int | None]:
    """Return (status_str, pid_or_none) for an agent."""
    pid_name = f"agent-{agent.nick}"
    pid = read_pid(pid_name)
    if pid and is_process_alive(pid):
        socket_path = _agent_socket_path(agent.nick)
        if os.path.exists(socket_path):
            return "running", pid
        return "starting", pid
    if pid:
        remove_pid(pid_name)
    return "stopped", None


def _cmd_status(args: argparse.Namespace) -> None:
    config = load_config_or_default(args.config)

    if not config.agents:
        print("No agents configured")
        return

    # Single agent detailed view
    if args.nick:
        agent = None
        for a in config.agents:
            if a.nick == args.nick:
                agent = a
                break
        if not agent:
            print(f"Agent '{args.nick}' not found in config", file=sys.stderr)
            sys.exit(1)

        status, pid = _agent_process_status(agent)
        print(agent.nick)
        print(f"  Status:     {status}")
        print(f"  PID:        {pid or '-'}")

        # Query IPC for activity if running — ask the agent directly
        if status == "running":
            resp = asyncio.run(_ipc_request(
                _agent_socket_path(agent.nick), "status", query=True
            ))
            if resp and resp.get("ok"):
                data = resp.get("data", {})
                print(f"  Activity:   {data.get('description', 'nothing')}")
                print(f"  Turns:      {data.get('turn_count', 0)}")
                print(f"  Paused:     {'yes' if data.get('paused') else 'no'}")
            else:
                print(f"  Activity:   unknown (daemon may need restart)")
        else:
            print(f"  Activity:   -")

        channels = agent.channels if isinstance(agent.channels, list) else []
        print(f"  Directory:  {agent.directory}")
        print(f"  Backend:    {agent.agent}")
        print(f"  Channels:   {', '.join(channels)}")
        print(f"  Model:      {agent.model}")
        print(f"  Config:     {args.config}")
        return

    # All agents view
    show_activity = args.full

    if show_activity:
        print(f"{'NICK':<30} {'STATUS':<12} {'PID':<10} {'ACTIVITY'}")
        print("-" * 72)
    else:
        print(f"{'NICK':<30} {'STATUS':<12} {'PID':<10}")
        print("-" * 52)

    for agent in config.agents:
        status, pid = _agent_process_status(agent)
        activity = "-"

        if show_activity and status == "running":
            # Use cached description (no live query — too slow for all agents)
            resp = asyncio.run(_ipc_request(_agent_socket_path(agent.nick), "status"))
            if resp and resp.get("ok"):
                activity = resp.get("data", {}).get("description", "nothing")

        if show_activity:
            print(f"{agent.nick:<30} {status:<12} {str(pid or '-'):<10} {activity}")
        else:
            print(f"{agent.nick:<30} {status:<12} {str(pid or '-'):<10}")


# -----------------------------------------------------------------------
# Observation subcommands
# -----------------------------------------------------------------------

def _get_observer(config_path: str):
    """Create an IRCObserver from the config file."""
    from agentirc.observer import IRCObserver

    config = load_config_or_default(config_path)
    return IRCObserver(
        host=config.server.host,
        port=config.server.port,
        server_name=config.server.name,
    )


def _ipc_to_agents(args: argparse.Namespace, msg_type: str, action_verb: str) -> None:
    """Send an IPC message (pause/resume) to one or all agents."""
    config = load_config_or_default(args.config)

    if args.nick and args.all:
        print(f"Cannot specify both nick and --all", file=sys.stderr)
        sys.exit(1)

    if not args.nick and not args.all:
        print(f"Usage: agentirc {action_verb} <nick> or --all", file=sys.stderr)
        sys.exit(1)

    targets = config.agents if args.all else []
    if args.nick:
        for a in config.agents:
            if a.nick == args.nick:
                targets = [a]
                break
        else:
            print(f"Agent '{args.nick}' not found in config", file=sys.stderr)
            sys.exit(1)

    for agent in targets:
        socket_path = _agent_socket_path(agent.nick)
        resp = asyncio.run(_ipc_request(socket_path, msg_type))
        if resp and resp.get("ok"):
            print(f"{agent.nick}: {action_verb}")
        else:
            print(f"{agent.nick}: failed (not running?)", file=sys.stderr)


def _cmd_sleep(args: argparse.Namespace) -> None:
    _ipc_to_agents(args, "pause", "paused")


def _cmd_wake(args: argparse.Namespace) -> None:
    _ipc_to_agents(args, "resume", "resumed")


def _cmd_learn(args: argparse.Namespace) -> None:
    from agentirc.learn_prompt import generate_learn_prompt

    config = load_config_or_default(args.config)
    cwd = os.getcwd()

    # Find agent: by --nick flag, or by matching cwd to an agent's directory
    agent = None
    if args.nick:
        for a in config.agents:
            if a.nick == args.nick:
                agent = a
                break
        if not agent:
            print(f"Agent '{args.nick}' not found in config", file=sys.stderr)
            sys.exit(1)
    else:
        for a in config.agents:
            if os.path.realpath(a.directory) == os.path.realpath(cwd):
                agent = a
                break

    if agent:
        print(generate_learn_prompt(
            nick=agent.nick,
            server=config.server.name,
            directory=agent.directory,
            backend=agent.agent,
            channels=agent.channels,
        ))
    else:
        print(generate_learn_prompt(
            server=config.server.name,
            directory=cwd,
        ))


def _cmd_send(args: argparse.Namespace) -> None:
    observer = _get_observer(args.config)
    target = args.target if args.target.startswith("#") else args.target
    asyncio.run(observer.send_message(target, args.message))
    print(f"Sent to {target}")


def _cmd_read(args: argparse.Namespace) -> None:
    observer = _get_observer(args.config)
    channel = args.channel if args.channel.startswith("#") else f"#{args.channel}"
    messages = asyncio.run(observer.read_channel(channel, limit=args.limit))

    if not messages:
        print(f"No messages in {channel}")
        return

    for msg in messages:
        print(msg)


def _cmd_who(args: argparse.Namespace) -> None:
    observer = _get_observer(args.config)
    target = args.channel
    nicks = asyncio.run(observer.who(target))

    if not nicks:
        print(f"No users in {target}")
        return

    print(f"Users in {target}:")
    for nick in nicks:
        print(f"  {nick}")


def _cmd_channels(args: argparse.Namespace) -> None:
    observer = _get_observer(args.config)
    channels = asyncio.run(observer.list_channels())

    if not channels:
        print("No active channels")
        return

    print("Active channels:")
    for ch in channels:
        print(f"  {ch}")


# -----------------------------------------------------------------------
# Skills install
# -----------------------------------------------------------------------

def _get_bundled_admin_skill_path() -> str:
    """Return the path to the bundled admin SKILL.md in the installed package."""
    import agentirc
    return os.path.join(os.path.dirname(agentirc.__file__), "skills", "agentirc", "SKILL.md")


def _get_bundled_skill_path() -> str:
    """Return the path to the bundled SKILL.md in the installed package."""
    import agentirc
    return os.path.join(os.path.dirname(agentirc.__file__), "clients", "claude", "skill", "SKILL.md")


def _install_admin_skill(root_dir: str, label: str) -> None:
    """Install the admin/ops skill to the given root skills directory."""
    src = _get_bundled_admin_skill_path()
    dest_dir = os.path.join(os.path.expanduser(root_dir), "agentirc")
    dest = os.path.join(dest_dir, "SKILL.md")

    os.makedirs(dest_dir, exist_ok=True)
    import shutil
    shutil.copy2(src, dest)
    print(f"Installed {label} admin skill: {dest}")


def _install_skill_claude() -> None:
    """Install IRC skill for Claude Code."""
    src = _get_bundled_skill_path()
    dest_dir = os.path.expanduser("~/.claude/skills/irc")
    dest = os.path.join(dest_dir, "SKILL.md")

    os.makedirs(dest_dir, exist_ok=True)
    import shutil
    shutil.copy2(src, dest)
    print(f"Installed Claude Code messaging skill: {dest}")
    _install_admin_skill("~/.claude/skills", "Claude Code")


def _get_bundled_codex_skill_path() -> str:
    """Return the path to the bundled Codex SKILL.md in the installed package."""
    import agentirc
    return os.path.join(os.path.dirname(agentirc.__file__), "clients", "codex", "skill", "SKILL.md")


def _install_skill_codex() -> None:
    """Install IRC skill for Codex."""
    src = _get_bundled_codex_skill_path()
    dest_dir = os.path.expanduser("~/.agents/skills/agentirc-irc")
    dest = os.path.join(dest_dir, "SKILL.md")

    os.makedirs(dest_dir, exist_ok=True)
    import shutil
    shutil.copy2(src, dest)
    print(f"Installed Codex messaging skill: {dest}")
    _install_admin_skill("~/.agents/skills", "Codex")


def _get_bundled_copilot_skill_path() -> str:
    """Return the path to the bundled Copilot SKILL.md in the installed package."""
    import agentirc
    return os.path.join(os.path.dirname(agentirc.__file__), "clients", "copilot", "skill", "SKILL.md")


def _install_skill_copilot() -> None:
    """Install IRC skill for GitHub Copilot."""
    src = _get_bundled_copilot_skill_path()
    dest_dir = os.path.expanduser("~/.copilot_skills/agentirc-irc")
    dest = os.path.join(dest_dir, "SKILL.md")

    os.makedirs(dest_dir, exist_ok=True)
    import shutil
    shutil.copy2(src, dest)
    print(f"Installed Copilot messaging skill: {dest}")
    _install_admin_skill("~/.copilot_skills", "Copilot")


def _get_bundled_acp_skill_path() -> str:
    """Return the path to the bundled ACP SKILL.md in the installed package."""
    import agentirc
    return os.path.join(os.path.dirname(agentirc.__file__), "clients", "acp", "skill", "SKILL.md")


def _install_skill_acp() -> None:
    """Install IRC skill for ACP agents (Cline, OpenCode, etc.)."""
    src = _get_bundled_acp_skill_path()
    dest_dir = os.path.expanduser("~/.acp/skills/agentirc-irc")
    dest = os.path.join(dest_dir, "SKILL.md")

    os.makedirs(dest_dir, exist_ok=True)
    import shutil
    shutil.copy2(src, dest)
    print(f"Installed ACP messaging skill: {dest}")
    _install_admin_skill("~/.acp/skills", "ACP")


def _cmd_skills(args: argparse.Namespace) -> None:
    if not hasattr(args, "skills_command") or args.skills_command != "install":
        print("Usage: agentirc skills install <claude|codex|copilot|acp|all>", file=sys.stderr)
        sys.exit(1)

    target = args.target

    if target in ("claude", "all"):
        _install_skill_claude()
    if target in ("codex", "all"):
        _install_skill_codex()
    if target in ("copilot", "all"):
        _install_skill_copilot()
    if target in ("acp", "opencode", "all"):
        _install_skill_acp()

    if target == "all":
        print("\nSkills installed for Claude Code, Codex, Copilot, and ACP.")
    print(f"\nSet AGENTIRC_NICK in your shell profile to enable the skill.")


# -----------------------------------------------------------------------
# Overview subcommand
# -----------------------------------------------------------------------

def _cmd_overview(args: argparse.Namespace) -> None:
    """Show mesh overview."""
    from agentirc.overview.collector import collect_mesh_state
    from agentirc.overview.renderer_text import render_text

    config = load_config_or_default(args.config)
    message_limit = max(1, min(args.messages, 20))
    refresh_interval = max(1, args.refresh)

    if args.serve:
        from agentirc.overview.renderer_web import serve_web
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

    mesh = asyncio.run(collect_mesh_state(
        host=config.server.host,
        port=config.server.port,
        server_name=config.server.name,
        message_limit=message_limit,
    ))
    output = render_text(
        mesh,
        room_filter=args.room,
        agent_filter=args.agent,
        message_limit=message_limit,
    )
    print(output, end="")


# -----------------------------------------------------------------------
# Credential helpers
# -----------------------------------------------------------------------

def _resolve_links_from_mesh(mesh_config_path: str) -> list:
    """Load link configs from mesh.yaml, looking up passwords from OS keyring."""
    from agentirc.mesh_config import load_mesh_config
    from agentirc.credentials import lookup_credential
    from agentirc.server.config import LinkConfig

    mesh = load_mesh_config(mesh_config_path)
    links = []
    for lc in mesh.server.links:
        password = lookup_credential(lc.name)
        if not password:
            logger.warning(
                "No credential found for peer '%s' — link will not be established. "
                "Run 'agentirc setup' to store link passwords.", lc.name
            )
            continue
        links.append(LinkConfig(
            name=lc.name, host=lc.host, port=lc.port,
            password=password, trust=lc.trust,
        ))
    return links


# -----------------------------------------------------------------------
# Shared helpers for setup / update
# -----------------------------------------------------------------------

def _build_server_start_cmd(mesh, agentirc_bin: str, mesh_config_path: str) -> list[str]:
    """Build the server start command with --foreground and --mesh-config.

    Passwords are NOT included in the command — they come from the OS keyring
    at startup via --mesh-config.
    """
    return [
        agentirc_bin, "server", "start", "--foreground",
        "--name", mesh.server.name,
        "--host", mesh.server.host,
        "--port", str(mesh.server.port),
        "--mesh-config", mesh_config_path,
    ]


# -----------------------------------------------------------------------
# Setup — mesh.yaml → auto-start services
# -----------------------------------------------------------------------

def _cmd_setup(args: argparse.Namespace) -> None:
    import getpass
    from agentirc.mesh_config import load_mesh_config
    from agentirc.persistence import install_service, uninstall_service, list_services

    try:
        mesh = load_mesh_config(args.config)
    except FileNotFoundError:
        print(f"Mesh config not found: {args.config}", file=sys.stderr)
        print("Create it manually or ask your AI agent to generate it.", file=sys.stderr)
        sys.exit(1)

    server_name = mesh.server.name

    if args.uninstall:
        print("Uninstalling agentirc services...")
        for svc in list_services():
            print(f"  Removing {svc}")
            uninstall_service(svc)
        _server_stop_by_name(server_name)
        for agent in mesh.agents:
            full_nick = f"{server_name}-{agent.nick}"
            _stop_agent(full_nick)
        print("Done.")
        return

    # Prompt for link passwords and store in OS keyring (never in files)
    from agentirc.credentials import store_credential, lookup_credential

    for link in mesh.server.links:
        existing = lookup_credential(link.name)
        if existing:
            print(f"  Credential for '{link.name}' already in keyring")
        else:
            password = getpass.getpass(f"Link password for {link.name}: ")
            if store_credential(link.name, password):
                print(f"  Stored credential for '{link.name}' in OS keyring")
            else:
                print(f"  Warning: failed to store credential for '{link.name}'", file=sys.stderr)
                print(f"  You may need to install secret-tool (Linux) or check Keychain access (macOS)", file=sys.stderr)

    # Generate agents.yaml for each workdir
    from agentirc.clients.claude.config import (
        AgentConfig as BaseAgentConfig,
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
        config_path = os.path.join(workdir, ".agentirc", "agents.yaml")
        os.makedirs(os.path.dirname(config_path), exist_ok=True)

        agent_configs = []
        for a in agents:
            full_nick = f"{server_name}-{a.nick}"
            agent_configs.append(BaseAgentConfig(
                nick=full_nick,
                agent=a.type,
                directory=workdir,
                channels=list(a.channels),
            ))

        daemon_config = DaemonConfig(
            server=ServerConnConfig(name=server_name, host="localhost", port=mesh.server.port),
            agents=agent_configs,
        )
        save_config(config_path, daemon_config)
        print(f"  Wrote {config_path}")

    # Install auto-start services
    agentirc_bin = shutil.which("agentirc") or "agentirc"
    server_cmd = _build_server_start_cmd(mesh, agentirc_bin, args.config)
    svc_name = f"agentirc-server-{server_name}"
    path = install_service(svc_name, server_cmd, f"agentirc server {server_name}")
    print(f"  Installed {svc_name} → {path}")

    for agent in mesh.agents:
        full_nick = f"{server_name}-{agent.nick}"
        workdir = os.path.expanduser(agent.workdir)
        config_path = os.path.join(workdir, ".agentirc", "agents.yaml")
        agent_cmd = [agentirc_bin, "start", full_nick, "--foreground", "--config", config_path]
        agent_svc = f"agentirc-agent-{full_nick}"
        path = install_service(agent_svc, agent_cmd, f"agentirc agent {full_nick}")
        print(f"  Installed {agent_svc} → {path}")

    print(f"\nSetup complete for mesh node '{server_name}'.")
    print(f"Services installed. Start with your service manager or reboot.")


def _server_stop_by_name(name: str) -> None:
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

    # Escalate to SIGKILL (SIGTERM on Windows)
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
# Update — upgrade + restart
# -----------------------------------------------------------------------

def _cmd_update(args: argparse.Namespace) -> None:
    from agentirc.mesh_config import load_mesh_config

    try:
        mesh = load_mesh_config(args.config)
    except FileNotFoundError:
        print(f"Mesh config not found: {args.config}", file=sys.stderr)
        sys.exit(1)

    server_name = mesh.server.name

    if not args.skip_upgrade:
        if args.dry_run:
            print("[dry-run] Would run: uv tool upgrade agentirc-cli")
            print("[dry-run] Would re-exec with --skip-upgrade")
            return

        # Upgrade the package
        uv = shutil.which("uv")
        if uv:
            print("Upgrading via uv...")
            result = subprocess.run(
                [uv, "tool", "upgrade", "agentirc-cli"],
                capture_output=True, text=True,
            )
            print(result.stdout.strip() if result.stdout else "")
            if result.returncode != 0:
                print(f"uv upgrade failed: {result.stderr}", file=sys.stderr)
                sys.exit(1)
        else:
            pip = shutil.which("pip") or shutil.which("pip3")
            if pip:
                print("Upgrading via pip...")
                result = subprocess.run(
                    [pip, "install", "--upgrade", "agentirc-cli"],
                    capture_output=True, text=True,
                )
                if result.returncode != 0:
                    print(f"pip upgrade failed: {result.stderr}", file=sys.stderr)
                    sys.exit(1)
            else:
                print("Neither uv nor pip found", file=sys.stderr)
                sys.exit(1)

        # Re-exec with new binary so restart uses new code
        agentirc_bin = shutil.which("agentirc") or "agentirc"
        reexec_args = [agentirc_bin, "update", "--skip-upgrade", "--config", args.config]
        print("Re-executing with updated code...")
        if sys.platform == "win32":
            sys.exit(subprocess.run(reexec_args).returncode)
        else:
            os.execvp(agentirc_bin, reexec_args)

    # --skip-upgrade path: restart everything
    print(f"Restarting mesh node '{server_name}'...")

    if args.dry_run:
        for agent in mesh.agents:
            print(f"[dry-run] Would stop agent {server_name}-{agent.nick}")
        print(f"[dry-run] Would stop server {server_name}")
        print(f"[dry-run] Would regenerate auto-start entries")
        print(f"[dry-run] Would start server {server_name}")
        for agent in mesh.agents:
            print(f"[dry-run] Would start agent {server_name}-{agent.nick}")
        return

    # Stop agents
    for agent in mesh.agents:
        full_nick = f"{server_name}-{agent.nick}"
        print(f"  Stopping {full_nick}...")
        _stop_agent(full_nick)

    # Stop server
    print(f"  Stopping server {server_name}...")
    _server_stop_by_name(server_name)

    # Regenerate auto-start entries
    from agentirc.persistence import install_service

    agentirc_bin = shutil.which("agentirc") or "agentirc"
    server_cmd = _build_server_start_cmd(mesh, agentirc_bin, args.config)
    install_service(f"agentirc-server-{server_name}", server_cmd, f"agentirc server {server_name}")

    for agent in mesh.agents:
        full_nick = f"{server_name}-{agent.nick}"
        workdir = os.path.expanduser(agent.workdir)
        config_path = os.path.join(workdir, ".agentirc", "agents.yaml")
        agent_cmd = [agentirc_bin, "start", full_nick, "--foreground", "--config", config_path]
        install_service(f"agentirc-agent-{full_nick}", agent_cmd, f"agentirc agent {full_nick}")

    # Restart services via platform service manager
    from agentirc.persistence import restart_service

    server_svc = f"agentirc-server-{server_name}"
    print(f"  Restarting {server_svc}...")
    if not restart_service(server_svc):
        # Fallback: start via CLI if no service file installed
        print(f"  No service file found, starting via CLI...")
        subprocess.run([
            agentirc_bin, "server", "start",
            "--name", server_name,
            "--host", mesh.server.host,
            "--port", str(mesh.server.port),
            "--mesh-config", args.config,
        ], check=False)

    # Wait for server to be ready
    import socket as _socket
    for _ in range(50):
        try:
            with _socket.create_connection(("localhost", mesh.server.port), timeout=1):
                break
        except (ConnectionRefusedError, OSError):
            time.sleep(0.1)

    for agent in mesh.agents:
        full_nick = f"{server_name}-{agent.nick}"
        agent_svc = f"agentirc-agent-{full_nick}"
        print(f"  Restarting {agent_svc}...")
        if not restart_service(agent_svc):
            # Fallback: start via CLI
            workdir = os.path.expanduser(agent.workdir)
            config_path = os.path.join(workdir, ".agentirc", "agents.yaml")
            subprocess.run(
                [agentirc_bin, "start", full_nick, "--config", config_path],
                check=False,
            )

    print(f"\nUpdate complete. All services restarted.")
