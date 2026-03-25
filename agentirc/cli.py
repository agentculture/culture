"""Unified CLI entry point for agentirc.

Subcommands:
    agentirc server start|stop|status   Manage the IRC server daemon
    agentirc init                       Register an agent for the current directory
    agentirc start [nick] [--all]       Start agent daemon(s)
    agentirc stop [nick] [--all]        Stop agent daemon(s)
    agentirc status                     List running agents
    agentirc read <channel>             Read recent channel messages
    agentirc who <channel>              List channel members
    agentirc channels                   List active channels
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
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

def main() -> None:
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

    srv_stop = server_sub.add_parser("stop", help="Stop the IRC server daemon")
    srv_stop.add_argument("--name", default="agentirc", help="Server name")

    srv_status = server_sub.add_parser("status", help="Check server daemon status")
    srv_status.add_argument("--name", default="agentirc", help="Server name")

    # -- init subcommand ---------------------------------------------------
    init_parser = sub.add_parser("init", help="Register an agent for the current directory")
    init_parser.add_argument("--server", default=None, help="Server name prefix")
    init_parser.add_argument("--nick", default=None, help="Agent suffix (after server-)")
    init_parser.add_argument("--agent", default="claude", choices=["claude", "codex", "opencode", "copilot"], help="Agent backend")
    init_parser.add_argument("--config", default=DEFAULT_CONFIG, help="Config file path")

    # -- start subcommand --------------------------------------------------
    start_parser = sub.add_parser("start", help="Start agent daemon(s)")
    start_parser.add_argument("nick", nargs="?", help="Agent nick to start")
    start_parser.add_argument("--all", action="store_true", help="Start all agents")
    start_parser.add_argument("--config", default=DEFAULT_CONFIG, help="Config file path")

    # -- stop subcommand ---------------------------------------------------
    stop_parser = sub.add_parser("stop", help="Stop agent daemon(s)")
    stop_parser.add_argument("nick", nargs="?", help="Agent nick to stop")
    stop_parser.add_argument("--all", action="store_true", help="Stop all agents")
    stop_parser.add_argument("--config", default=DEFAULT_CONFIG, help="Config file path")

    # -- status subcommand -------------------------------------------------
    status_parser = sub.add_parser("status", help="List running agents")
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

    # -- channels subcommand -----------------------------------------------
    channels_parser = sub.add_parser("channels", help="List active channels")
    channels_parser.add_argument("--config", default=DEFAULT_CONFIG, help="Config file path")

    # -- skills subcommand -------------------------------------------------
    skills_parser = sub.add_parser("skills", help="Install IRC skills for AI agents")
    skills_sub = skills_parser.add_subparsers(dest="skills_command")
    skills_install = skills_sub.add_parser("install", help="Install IRC skill for an agent")
    skills_install.add_argument(
        "target", choices=["claude", "codex", "opencode", "copilot", "all"],
        help="Target agent: claude, codex, opencode, copilot, or all",
    )

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
            "read": _cmd_read,
            "who": _cmd_who,
            "channels": _cmd_channels,
            "skills": _cmd_skills,
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

    # Fork to daemonize
    pid = os.fork()
    if pid > 0:
        # Parent: wait briefly to check child started, then exit
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

    # Close stdin
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)

    # Write PID file
    write_pid(pid_name, os.getpid())

    # Run the server
    try:
        asyncio.run(_run_server(args.name, args.host, args.port, args.link))
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
            logger.error("Failed to link to %s: %s", lc.name, e)

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
            print(f"Agent '{full_nick}' already exists in config")
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
    elif args.agent == "opencode":
        from agentirc.clients.opencode.config import AgentConfig as OpenCodeAgentConfig
        agent = OpenCodeAgentConfig(
            nick=full_nick,
            agent="opencode",
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

    if len(agents) == 1:
        # Run in foreground (single agent)
        agent = agents[0]
        print(f"Starting agent {agent.nick}...")
        asyncio.run(_run_single_agent(config, agent))
    else:
        # Fork each agent into background
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
    elif backend == "opencode":
        from agentirc.clients.opencode.daemon import OpenCodeDaemon
        from agentirc.clients.opencode.config import (
            DaemonConfig as OpenCodeDaemonConfig,
        )
        # Re-load config through OpenCode module for correct supervisor defaults
        opencode_config = OpenCodeDaemonConfig(
            server=config.server,
            webhooks=config.webhooks,
            buffer_size=config.buffer_size,
            agents=config.agents,
        )
        daemon = OpenCodeDaemon(opencode_config, agent)
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
    print(f"Agent '{nick}' did not stop gracefully, sending SIGKILL")
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    remove_pid(pid_name)
    print(f"Agent '{nick}' killed")


async def _ipc_shutdown(socket_path: str) -> bool:
    """Send a shutdown command via Unix socket IPC."""
    from agentirc.clients.claude.ipc import decode_message, encode_message, make_request

    reader, writer = await asyncio.wait_for(
        asyncio.open_unix_connection(socket_path),
        timeout=3.0,
    )
    try:
        req = make_request("shutdown")
        writer.write(encode_message(req))
        await writer.drain()
        data = await asyncio.wait_for(reader.readline(), timeout=3.0)
        resp = decode_message(data)
        return resp is not None and resp.get("ok", False)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, BrokenPipeError, OSError):
            pass


# -----------------------------------------------------------------------
# Agent status
# -----------------------------------------------------------------------

def _cmd_status(args: argparse.Namespace) -> None:
    config = load_config_or_default(args.config)

    if not config.agents:
        print("No agents configured")
        return

    print(f"{'NICK':<30} {'STATUS':<12} {'PID':<10}")
    print("-" * 52)

    for agent in config.agents:
        pid_name = f"agent-{agent.nick}"
        pid = read_pid(pid_name)
        status = "stopped"

        if pid and is_process_alive(pid):
            # Also check if socket is connectable
            socket_path = os.path.join(
                os.environ.get("XDG_RUNTIME_DIR", "/tmp"),
                f"agentirc-{agent.nick}.sock",
            )
            if os.path.exists(socket_path):
                status = "running"
            else:
                status = "starting"
            print(f"{agent.nick:<30} {status:<12} {pid:<10}")
        elif pid:
            remove_pid(pid_name)
            print(f"{agent.nick:<30} {'stopped':<12} {'-':<10}")
        else:
            print(f"{agent.nick:<30} {'stopped':<12} {'-':<10}")


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

def _get_bundled_skill_path() -> str:
    """Return the path to the bundled SKILL.md in the installed package."""
    import agentirc
    return os.path.join(os.path.dirname(agentirc.__file__), "clients", "claude", "skill", "SKILL.md")


def _install_skill_claude() -> None:
    """Install IRC skill for Claude Code."""
    src = _get_bundled_skill_path()
    dest_dir = os.path.expanduser("~/.claude/skills/irc")
    dest = os.path.join(dest_dir, "SKILL.md")

    os.makedirs(dest_dir, exist_ok=True)
    import shutil
    shutil.copy2(src, dest)
    print(f"Installed Claude Code skill: {dest}")


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
    print(f"Installed Codex skill: {dest}")


def _get_bundled_opencode_skill_path() -> str:
    """Return the path to the bundled OpenCode SKILL.md in the installed package."""
    import agentirc
    return os.path.join(os.path.dirname(agentirc.__file__), "clients", "opencode", "skill", "SKILL.md")


def _install_skill_opencode() -> None:
    """Install IRC skill for OpenCode."""
    src = _get_bundled_opencode_skill_path()
    dest_dir = os.path.expanduser("~/.opencode/skills/agentirc-irc")
    dest = os.path.join(dest_dir, "SKILL.md")

    os.makedirs(dest_dir, exist_ok=True)
    import shutil
    shutil.copy2(src, dest)
    print(f"Installed OpenCode skill: {dest}")


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
    print(f"Installed Copilot skill: {dest}")


def _cmd_skills(args: argparse.Namespace) -> None:
    if not hasattr(args, "skills_command") or args.skills_command != "install":
        print("Usage: agentirc skills install <claude|codex|opencode|copilot|all>", file=sys.stderr)
        sys.exit(1)

    target = args.target

    if target in ("claude", "all"):
        _install_skill_claude()
    if target in ("codex", "all"):
        _install_skill_codex()
    if target in ("opencode", "all"):
        _install_skill_opencode()
    if target in ("copilot", "all"):
        _install_skill_copilot()

    if target == "all":
        print("\nSkills installed for Claude Code, Codex, OpenCode, and Copilot.")
    print(f"\nSet AGENTIRC_NICK in your shell profile to enable the skill.")
