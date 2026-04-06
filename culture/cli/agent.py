"""Agent subcommands: culture agent {create,join,start,stop,status,...}."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys

from culture.clients.claude.config import (
    AgentConfig,
    DaemonConfig,
    add_agent_to_config,
    load_config,
    load_config_or_default,
    sanitize_agent_name,
)
from culture.pidfile import (
    is_process_alive,
    read_pid,
    remove_pid,
    write_pid,
)

from ._helpers import (
    _CONFIG_HELP,
    DEFAULT_CONFIG,
    LOG_DIR,
    agent_socket_path,
    get_observer,
    ipc_request,
    print_agent_detail,
    print_agents_overview,
    print_bot_listing,
    stop_agent,
)

logger = logging.getLogger("culture")

NAME = "agent"


def register(subparsers: argparse._SubParsersAction) -> None:
    agent_parser = subparsers.add_parser("agent", help="Manage AI agents")
    agent_sub = agent_parser.add_subparsers(dest="agent_command")

    # -- create ---------------------------------------------------------------
    _agent_args = [
        ("--server", {"default": None, "help": "Server name prefix"}),
        ("--nick", {"default": None, "help": "Agent suffix (after server-)"}),
        (
            "--agent",
            {
                "default": "claude",
                "choices": ["claude", "codex", "copilot", "acp"],
                "help": "Agent backend",
            },
        ),
        (
            "--acp-command",
            {
                "default": None,
                "help": 'ACP spawn command as JSON list (e.g. \'["cline","--acp"]\')',
            },
        ),
        ("--config", {"default": DEFAULT_CONFIG, "help": _CONFIG_HELP}),
    ]

    create_parser = agent_sub.add_parser("create", help="Create an agent for the current directory")
    for flag, kwargs in _agent_args:
        create_parser.add_argument(flag, **kwargs)

    join_parser = agent_sub.add_parser("join", help="Join an educated agent to the culture mesh")
    for flag, kwargs in _agent_args:
        join_parser.add_argument(flag, **kwargs)

    # -- start ----------------------------------------------------------------
    start_parser = agent_sub.add_parser("start", help="Start agent daemon(s)")
    start_parser.add_argument("nick", nargs="?", help="Agent nick to start")
    start_parser.add_argument("--all", action="store_true", help="Start all agents")
    start_parser.add_argument("--config", default=DEFAULT_CONFIG, help=_CONFIG_HELP)
    start_parser.add_argument(
        "--foreground",
        action="store_true",
        help="Run in foreground (for service managers)",
    )

    # -- stop -----------------------------------------------------------------
    stop_parser = agent_sub.add_parser("stop", help="Stop agent daemon(s)")
    stop_parser.add_argument("nick", nargs="?", help="Agent nick to stop")
    stop_parser.add_argument("--all", action="store_true", help="Stop all agents")
    stop_parser.add_argument("--config", default=DEFAULT_CONFIG, help=_CONFIG_HELP)

    # -- status ---------------------------------------------------------------
    status_parser = agent_sub.add_parser("status", help="List running agents")
    status_parser.add_argument("nick", nargs="?", help="Show detailed status for a specific agent")
    status_parser.add_argument(
        "--full", action="store_true", help="Query agents for activity status"
    )
    status_parser.add_argument("--config", default=DEFAULT_CONFIG, help=_CONFIG_HELP)

    # -- rename ---------------------------------------------------------------
    rename_parser = agent_sub.add_parser("rename", help="Rename an agent (same server)")
    rename_parser.add_argument("nick", help="Current agent nick (e.g. spark-culture)")
    rename_parser.add_argument("new_name", help="New agent name suffix (e.g. claude)")
    rename_parser.add_argument("--config", default=DEFAULT_CONFIG, help=_CONFIG_HELP)

    # -- assign ---------------------------------------------------------------
    assign_parser = agent_sub.add_parser("assign", help="Move an agent to a different server")
    assign_parser.add_argument("nick", help="Current agent nick (e.g. culture-culture)")
    assign_parser.add_argument("server", help="Target server name (e.g. spark)")
    assign_parser.add_argument("--config", default=DEFAULT_CONFIG, help=_CONFIG_HELP)

    # -- sleep ----------------------------------------------------------------
    sleep_parser = agent_sub.add_parser("sleep", help="Pause agent(s) — stay connected but idle")
    sleep_parser.add_argument("nick", nargs="?", help="Agent nick to pause")
    sleep_parser.add_argument("--all", action="store_true", help="Pause all agents")
    sleep_parser.add_argument("--config", default=DEFAULT_CONFIG, help=_CONFIG_HELP)

    # -- wake -----------------------------------------------------------------
    wake_parser = agent_sub.add_parser("wake", help="Resume paused agent(s)")
    wake_parser.add_argument("nick", nargs="?", help="Agent nick to resume")
    wake_parser.add_argument("--all", action="store_true", help="Resume all agents")
    wake_parser.add_argument("--config", default=DEFAULT_CONFIG, help=_CONFIG_HELP)

    # -- learn ----------------------------------------------------------------
    learn_parser = agent_sub.add_parser("learn", help="Print self-teaching prompt for your agent")
    learn_parser.add_argument("--nick", default=None, help="Agent nick (auto-detects from cwd)")
    learn_parser.add_argument("--config", default=DEFAULT_CONFIG, help=_CONFIG_HELP)

    # -- message --------------------------------------------------------------
    message_parser = agent_sub.add_parser("message", help="Send a message to an agent")
    message_parser.add_argument("target", help="Agent nick")
    message_parser.add_argument("text", help="Message text to send")
    message_parser.add_argument("--config", default=DEFAULT_CONFIG, help=_CONFIG_HELP)

    # -- read -----------------------------------------------------------------
    read_parser = agent_sub.add_parser("read", help="Read DM history with an agent")
    read_parser.add_argument("target", help="Agent nick")
    read_parser.add_argument("--limit", "-n", type=int, default=50, help="Number of messages")
    read_parser.add_argument("--config", default=DEFAULT_CONFIG, help=_CONFIG_HELP)


def dispatch(args: argparse.Namespace) -> None:
    if not args.agent_command:
        print(
            "Usage: culture agent {create|join|start|stop|status|rename|assign|sleep|wake|learn|message|read}",
            file=sys.stderr,
        )
        sys.exit(1)

    handlers = {
        "create": _cmd_create,
        "join": _cmd_join,
        "start": _cmd_start,
        "stop": _cmd_stop,
        "status": _cmd_status,
        "rename": _cmd_rename,
        "assign": _cmd_assign,
        "sleep": _cmd_sleep,
        "wake": _cmd_wake,
        "learn": _cmd_learn,
        "message": _cmd_message,
        "read": _cmd_read,
    }
    handler = handlers.get(args.agent_command)
    if handler:
        handler(args)
    else:
        print(f"Unknown agent command: {args.agent_command}", file=sys.stderr)
        sys.exit(1)


# -----------------------------------------------------------------------
# Create / Join
# -----------------------------------------------------------------------


def _create_agent_config(args: argparse.Namespace, full_nick: str) -> AgentConfig:
    """Build a backend-specific AgentConfig from CLI args."""
    if args.agent == "codex":
        from culture.clients.codex.config import AgentConfig as CodexAgentConfig

        return CodexAgentConfig(
            nick=full_nick,
            agent="codex",
            directory=os.getcwd(),
            channels=["#general"],
        )
    if args.agent == "copilot":
        from culture.clients.copilot.config import AgentConfig as CopilotAgentConfig

        return CopilotAgentConfig(
            nick=full_nick,
            agent="copilot",
            directory=os.getcwd(),
            channels=["#general"],
        )
    if args.agent == "acp":
        import json as _json

        from culture.clients.acp.config import AgentConfig as ACPAgentConfig

        acp_cmd = ["opencode", "acp"]
        if args.acp_command:
            try:
                acp_cmd = _json.loads(args.acp_command)
            except _json.JSONDecodeError:
                acp_cmd = args.acp_command.split()
        if (
            not isinstance(acp_cmd, list)
            or not acp_cmd
            or not all(isinstance(s, str) for s in acp_cmd)
        ):
            print("Error: --acp-command must be a non-empty list of strings", file=sys.stderr)
            sys.exit(1)
        return ACPAgentConfig(
            nick=full_nick,
            agent="acp",
            acp_command=acp_cmd,
            directory=os.getcwd(),
            channels=["#general"],
        )
    return AgentConfig(
        nick=full_nick,
        agent=args.agent,
        directory=os.getcwd(),
        channels=["#general"],
    )


def _cmd_create(args: argparse.Namespace) -> None:
    config = load_config_or_default(args.config)

    server_name = args.server or config.server.name or "culture"

    if args.nick:
        suffix = args.nick
    else:
        dirname = os.path.basename(os.getcwd())
        suffix = sanitize_agent_name(dirname)

    full_nick = f"{server_name}-{suffix}"

    for existing in config.agents:
        if existing.nick == full_nick:
            channels = existing.channels if isinstance(existing.channels, list) else []
            print(f"Agent '{full_nick}' already exists in config", file=sys.stderr)
            print(f"  Directory: {existing.directory}", file=sys.stderr)
            print(f"  Backend:   {existing.agent}", file=sys.stderr)
            print(f"  Channels:  {', '.join(channels)}", file=sys.stderr)
            print(f"  Model:     {existing.model}", file=sys.stderr)
            print(f"  Config:    {args.config}", file=sys.stderr)
            print(file=sys.stderr)
            print(f"Start with: culture agent start {full_nick}", file=sys.stderr)
            sys.exit(1)

    agent = _create_agent_config(args, full_nick)

    add_agent_to_config(args.config, agent, server_name=server_name)

    print(f"Agent created: {full_nick}")
    print(f"  Directory: {agent.directory}")
    print(f"  Channels: {', '.join(agent.channels)}")
    print(f"  Config: {args.config}")
    print()
    print(f"Start with: culture agent start {full_nick}")


def _cmd_join(args: argparse.Namespace) -> None:
    """Create and start an agent — shorthand for 'create' + 'start'."""
    _cmd_create(args)
    config = load_config_or_default(args.config)
    server_name = args.server or config.server.name or "culture"
    suffix = args.nick if args.nick else sanitize_agent_name(os.path.basename(os.getcwd()))
    full_nick = f"{server_name}-{suffix}"
    args.nick = full_nick
    args.all = False
    _cmd_start(args)


# -----------------------------------------------------------------------
# Start / Stop
# -----------------------------------------------------------------------


def _resolve_agents_to_start(config, args) -> list:
    """Return the list of agents to start, or exit with an error message."""
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
            print("No agents configured. Run 'culture agent create' first.", file=sys.stderr)
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

    return agents


def _probe_server_connection(host: str, port: int, server_name: str) -> None:
    """Check that the IRC server is reachable; exit with an error message if not."""
    import socket as _socket

    try:
        with _socket.create_connection((host, port), timeout=2):
            pass
    except (ConnectionRefusedError, OSError):
        hint = ""
        server_pid = read_pid(f"server-{server_name}")
        if not server_pid or not is_process_alive(server_pid):
            hint = f"\nStart it with: culture server start --name {server_name}"
        print(
            f"Error: cannot connect to IRC server at {host}:{port}.{hint}",
            file=sys.stderr,
        )
        sys.exit(1)


def _cmd_start(args: argparse.Namespace) -> None:
    config = load_config(args.config)

    agents = _resolve_agents_to_start(config, args)

    server_name = config.server.name
    _probe_server_connection(config.server.host, config.server.port, server_name)

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
                agent = agents[0]
                print(f"Starting agent {agent.nick}...")
                asyncio.run(_run_single_agent(config, agent))
            else:
                print(
                    "Multi-agent daemon mode not supported on Windows. Start agents individually.",
                    file=sys.stderr,
                )
                sys.exit(1)
        else:
            _run_multi_agents(config, agents)


async def _run_single_agent(config: DaemonConfig, agent: AgentConfig) -> None:
    """Run a single agent daemon in the foreground."""
    backend = getattr(agent, "agent", "claude")

    if backend == "codex":
        from culture.clients.codex.config import DaemonConfig as CodexDaemonConfig
        from culture.clients.codex.daemon import CodexDaemon

        codex_config = CodexDaemonConfig(
            server=config.server,
            webhooks=config.webhooks,
            buffer_size=config.buffer_size,
            agents=config.agents,
        )
        daemon = CodexDaemon(codex_config, agent)
    elif backend in ("acp", "opencode"):
        from culture.clients.acp.config import AgentConfig as ACPAgentConfig
        from culture.clients.acp.config import DaemonConfig as ACPDaemonConfig
        from culture.clients.acp.daemon import ACPDaemon

        acp_config = ACPDaemonConfig(
            server=config.server,
            webhooks=config.webhooks,
            buffer_size=config.buffer_size,
            agents=config.agents,
        )
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
        from culture.clients.copilot.config import DaemonConfig as CopilotDaemonConfig
        from culture.clients.copilot.daemon import CopilotDaemon

        copilot_config = CopilotDaemonConfig(
            server=config.server,
            webhooks=config.webhooks,
            buffer_size=config.buffer_size,
            agents=config.agents,
        )
        daemon = CopilotDaemon(copilot_config, agent)
    else:
        from culture.clients.claude.daemon import AgentDaemon

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
            os.setsid()

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
        stop_agent(agent.nick)


# -----------------------------------------------------------------------
# Status
# -----------------------------------------------------------------------


def _cmd_status(args: argparse.Namespace) -> None:
    config = load_config_or_default(args.config)

    if not config.agents:
        print("No agents configured")
        return

    if args.nick:
        agent = None
        for a in config.agents:
            if a.nick == args.nick:
                agent = a
                break
        if not agent:
            print(f"Agent '{args.nick}' not found in config", file=sys.stderr)
            sys.exit(1)

        print_agent_detail(agent, args.config, args)
        return

    print_agents_overview(config.agents, args.full)
    print_bot_listing()


# -----------------------------------------------------------------------
# Rename / Assign
# -----------------------------------------------------------------------


def _cmd_rename(args: argparse.Namespace) -> None:
    """Rename an agent's suffix within the same server."""
    from culture.clients.claude.config import (
        load_config_or_default,
        rename_agent,
        sanitize_agent_name,
    )
    from culture.pidfile import rename_pid

    config = load_config_or_default(args.config)
    old_nick = args.nick
    server_name = config.server.name
    expected_prefix = f"{server_name}-"

    if not old_nick.startswith(expected_prefix):
        print(
            f"Agent '{old_nick}' does not belong to server '{server_name}'",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        new_suffix = sanitize_agent_name(args.new_name)
    except ValueError:
        print(f"Invalid agent name: {args.new_name!r}", file=sys.stderr)
        sys.exit(1)

    new_nick = f"{server_name}-{new_suffix}"

    if old_nick == new_nick:
        print(f"Agent is already named '{old_nick}'")
        return

    try:
        rename_agent(args.config, old_nick, new_nick)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    rename_pid(f"agent-{old_nick}", f"agent-{new_nick}")

    print(f"Agent renamed: {old_nick} → {new_nick}")
    print()
    print("Restart the agent for the new nick to take effect:")
    print(f"  culture agent stop {old_nick}   # if still running under old name")
    print(f"  culture agent start {new_nick}")


def _cmd_assign(args: argparse.Namespace) -> None:
    """Move an agent to a different server (change nick prefix)."""
    from culture.clients.claude.config import (
        load_config_or_default,
        rename_agent,
        sanitize_agent_name,
    )
    from culture.pidfile import rename_pid

    config = load_config_or_default(args.config)
    old_nick = args.nick
    server_name = config.server.name
    expected_prefix = f"{server_name}-"

    if not old_nick.startswith(expected_prefix):
        print(
            f"Agent '{old_nick}' does not belong to server '{server_name}'",
            file=sys.stderr,
        )
        sys.exit(1)

    suffix = old_nick[len(expected_prefix) :]

    try:
        new_server = sanitize_agent_name(args.server)
    except ValueError:
        print(f"Invalid server name: {args.server!r}", file=sys.stderr)
        sys.exit(1)

    new_nick = f"{new_server}-{suffix}"

    if old_nick == new_nick:
        print(f"Agent already belongs to server '{new_server}'")
        return

    try:
        rename_agent(args.config, old_nick, new_nick)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    rename_pid(f"agent-{old_nick}", f"agent-{new_nick}")

    print(f"Agent reassigned: {old_nick} → {new_nick}")
    print()
    print("Restart the agent for the new nick to take effect:")
    print(f"  culture agent stop {old_nick}   # if still running under old name")
    print(f"  culture agent start {new_nick}")


# -----------------------------------------------------------------------
# Sleep / Wake / Learn
# -----------------------------------------------------------------------


def _ipc_to_agents(args: argparse.Namespace, msg_type: str, action_verb: str) -> None:
    """Send an IPC message (pause/resume) to one or all agents."""
    config = load_config_or_default(args.config)

    if args.nick and args.all:
        print("Cannot specify both nick and --all", file=sys.stderr)
        sys.exit(1)

    if not args.nick and not args.all:
        print(f"Usage: culture agent {action_verb} <nick> or --all", file=sys.stderr)
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
        socket_path = agent_socket_path(agent.nick)
        resp = asyncio.run(ipc_request(socket_path, msg_type))
        if resp and resp.get("ok"):
            print(f"{agent.nick}: {action_verb}")
        else:
            print(f"{agent.nick}: failed (not running?)", file=sys.stderr)


def _cmd_sleep(args: argparse.Namespace) -> None:
    _ipc_to_agents(args, "pause", "paused")


def _cmd_wake(args: argparse.Namespace) -> None:
    _ipc_to_agents(args, "resume", "resumed")


def _cmd_learn(args: argparse.Namespace) -> None:
    from culture.learn_prompt import generate_learn_prompt

    config = load_config_or_default(args.config)
    cwd = os.getcwd()

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
        print(
            generate_learn_prompt(
                nick=agent.nick,
                server=config.server.name,
                directory=agent.directory,
                backend=agent.agent,
                channels=agent.channels,
            )
        )
    else:
        print(
            generate_learn_prompt(
                server=config.server.name,
                directory=cwd,
            )
        )


# -----------------------------------------------------------------------
# Message / Read (mirrored in channel.py)
# -----------------------------------------------------------------------


def _cmd_message(args: argparse.Namespace) -> None:
    observer = get_observer(args.config)
    asyncio.run(observer.send_message(args.target, args.text))
    print(f"Sent to {args.target}")


def _cmd_read(args: argparse.Namespace) -> None:
    observer = get_observer(args.config)
    messages = asyncio.run(observer.read_channel(args.target, limit=args.limit))

    if not messages:
        print(f"No messages for {args.target}")
        return

    for msg in messages:
        print(msg)
