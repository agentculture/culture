"""Agent subcommands: culture agent {create,join,start,stop,status,...}."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from culture.clients.acp.config import AgentConfig as ACPAgentConfig
    from culture.clients.codex.config import AgentConfig as CodexAgentConfig
    from culture.clients.copilot.config import AgentConfig as CopilotAgentConfig

from culture.config import (
    AgentConfig,
    DaemonConfig,
    ServerConfig,
    ServerConnConfig,
    SupervisorConfig,
    WebhookConfig,
    add_to_manifest,
    archive_manifest_agent,
    load_config,
    load_config_or_default,
    load_culture_yaml,
    remove_from_manifest,
    remove_manifest_agent,
    rename_manifest_agent,
    sanitize_agent_name,
    save_culture_yaml,
    save_server_config,
    unarchive_manifest_agent,
)
from culture.pidfile import (
    is_process_alive,
    read_pid,
    remove_pid,
    write_pid,
)

from .shared.constants import (
    _CONFIG_HELP,
    DEFAULT_CHANNEL,
    DEFAULT_CONFIG,
    DEFAULT_SERVER_CONFIG,
    LEGACY_CONFIG,
    LOG_DIR,
    NO_AGENTS_MSG,
)
from .shared.display import print_agent_detail, print_agents_overview, print_bot_listing
from .shared.ipc import agent_socket_path, get_observer, ipc_request
from .shared.process import stop_agent

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
    status_parser.add_argument("--all", action="store_true", help="Include archived agents")
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
    read_parser = agent_sub.add_parser(
        "read", help="Read DM history with an agent (not yet implemented)"
    )
    read_parser.add_argument("target", help="Agent nick")
    read_parser.add_argument("--limit", "-n", type=int, default=50, help="Number of messages")
    read_parser.add_argument("--config", default=DEFAULT_CONFIG, help=_CONFIG_HELP)

    # -- archive --------------------------------------------------------------
    archive_parser = agent_sub.add_parser("archive", help="Archive an agent (stop and retire)")
    archive_parser.add_argument("nick", help="Agent nick to archive")
    archive_parser.add_argument("--reason", default="", help="Reason for archiving")
    archive_parser.add_argument("--config", default=DEFAULT_CONFIG, help=_CONFIG_HELP)

    # -- unarchive ------------------------------------------------------------
    unarchive_parser = agent_sub.add_parser("unarchive", help="Restore an archived agent")
    unarchive_parser.add_argument("nick", help="Agent nick to unarchive")
    unarchive_parser.add_argument("--config", default=DEFAULT_CONFIG, help=_CONFIG_HELP)

    # -- delete ---------------------------------------------------------------
    delete_parser = agent_sub.add_parser("delete", help="Remove an agent from config entirely")
    delete_parser.add_argument("nick", help="Agent nick to delete")
    delete_parser.add_argument("--config", default=DEFAULT_CONFIG, help=_CONFIG_HELP)

    # -- register -------------------------------------------------------------
    register_parser = agent_sub.add_parser("register", help="Register agent directory")
    register_parser.add_argument(
        "path", nargs="?", default=None, help="Directory containing culture.yaml (default: cwd)"
    )
    register_parser.add_argument(
        "--suffix", default=None, help="Agent suffix (required for multi-agent culture.yaml)"
    )
    register_parser.add_argument("--config", default=DEFAULT_SERVER_CONFIG, help=_CONFIG_HELP)

    # -- unregister -----------------------------------------------------------
    unregister_parser = agent_sub.add_parser("unregister", help="Unregister agent")
    unregister_parser.add_argument("target", help="Agent suffix or full nick")
    unregister_parser.add_argument("--config", default=DEFAULT_SERVER_CONFIG, help=_CONFIG_HELP)

    # -- migrate --------------------------------------------------------------
    # Most repos have already migrated; the verb stays in the CLI surface
    # for completeness but the help text now signals it's a one-time
    # operation (#333 item 7).
    migrate_parser = agent_sub.add_parser(
        "migrate",
        help="Migrate legacy agents.yaml to server.yaml + culture.yaml (one-time, usually a no-op)",
    )
    migrate_parser.add_argument("--config", default=LEGACY_CONFIG, help="Legacy agents.yaml path")


def dispatch(args: argparse.Namespace) -> None:
    if not args.agent_command:
        print(
            "Usage: culture agent {create|join|start|stop|status|rename|assign|sleep|wake|learn|message|read|archive|unarchive|delete|register|unregister|migrate}",
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
        "archive": _cmd_archive,
        "unarchive": _cmd_unarchive,
        "delete": _cmd_delete,
        "register": _cmd_register,
        "unregister": _cmd_unregister,
        "migrate": _cmd_migrate,
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


def _create_codex_config(full_nick: str) -> CodexAgentConfig:
    """Build a CodexAgentConfig."""
    from culture.clients.codex.config import AgentConfig as CodexAgentConfig

    return CodexAgentConfig(
        nick=full_nick,
        agent="codex",
        directory=os.getcwd(),
        channels=[DEFAULT_CHANNEL],
    )


def _create_copilot_config(full_nick: str) -> CopilotAgentConfig:
    """Build a CopilotAgentConfig."""
    from culture.clients.copilot.config import AgentConfig as CopilotAgentConfig

    return CopilotAgentConfig(
        nick=full_nick,
        agent="copilot",
        directory=os.getcwd(),
        channels=[DEFAULT_CHANNEL],
    )


def _parse_acp_command(raw_command: str | None) -> list[str]:
    """Parse and validate the ACP command from CLI args."""
    import json as _json

    acp_cmd = ["opencode", "acp"]
    if raw_command:
        try:
            acp_cmd = _json.loads(raw_command)
        except _json.JSONDecodeError:
            acp_cmd = raw_command.split()
    if not isinstance(acp_cmd, list) or not acp_cmd or not all(isinstance(s, str) for s in acp_cmd):
        print("Error: --acp-command must be a non-empty list of strings", file=sys.stderr)
        sys.exit(1)
    return acp_cmd


def _create_acp_config(full_nick: str, args: argparse.Namespace) -> ACPAgentConfig:
    """Build an ACPAgentConfig."""
    from culture.clients.acp.config import AgentConfig as ACPAgentConfig

    acp_cmd = _parse_acp_command(args.acp_command)
    return ACPAgentConfig(
        nick=full_nick,
        agent="acp",
        acp_command=acp_cmd,
        directory=os.getcwd(),
        channels=[DEFAULT_CHANNEL],
    )


def _create_default_config(full_nick: str, backend: str) -> AgentConfig:
    """Build a default (claude) AgentConfig."""
    return AgentConfig(
        nick=full_nick,
        backend=backend,
        directory=os.getcwd(),
        channels=[DEFAULT_CHANNEL],
    )


def _create_agent_config(args: argparse.Namespace, full_nick: str) -> AgentConfig:
    """Build a backend-specific AgentConfig from CLI args."""
    factories = {
        "codex": lambda: _create_codex_config(full_nick),
        "copilot": lambda: _create_copilot_config(full_nick),
        "acp": lambda: _create_acp_config(full_nick, args),
    }
    factory = factories.get(args.agent)
    if factory:
        return factory()
    return _create_default_config(full_nick, args.agent)


def _check_existing_agent(config, full_nick: str, config_path: str) -> None:
    """Check for duplicate agent nick.  Removes archived duplicates; exits on active ones."""
    for existing in config.agents:
        if existing.nick != full_nick:
            continue
        if existing.archived:
            print(f"Replacing archived agent '{full_nick}'")
            remove_manifest_agent(config_path, full_nick)
            return
        channels = existing.channels if isinstance(existing.channels, list) else []
        print(f"Agent '{full_nick}' already exists in config", file=sys.stderr)
        print(f"  Directory: {existing.directory}", file=sys.stderr)
        print(f"  Backend:   {existing.agent}", file=sys.stderr)
        print(f"  Channels:  {', '.join(channels)}", file=sys.stderr)
        print(f"  Model:     {existing.model}", file=sys.stderr)
        print(f"  Config:    {config_path}", file=sys.stderr)
        print(file=sys.stderr)
        print(f"Start with: culture agent start {full_nick}", file=sys.stderr)
        sys.exit(1)


def _to_manifest_agent(raw_agent, suffix: str) -> AgentConfig:
    """Convert a backend-specific AgentConfig to a manifest-format AgentConfig."""
    backend = getattr(raw_agent, "backend", None) or getattr(raw_agent, "agent", "claude")
    extras = {}
    if hasattr(raw_agent, "acp_command") and backend == "acp":
        extras["acp_command"] = raw_agent.acp_command
    return AgentConfig(
        suffix=suffix,
        backend=backend,
        channels=raw_agent.channels,
        model=raw_agent.model,
        directory=raw_agent.directory,
        extras=extras,
    )


def _save_agent_to_directory(agent: AgentConfig) -> None:
    """Save agent to culture.yaml, merging with existing agents in the directory."""
    culture_yaml_path = Path(agent.directory) / "culture.yaml"
    if culture_yaml_path.exists():
        existing = load_culture_yaml(agent.directory)
        agents_to_save = [a for a in existing if a.suffix != agent.suffix]
        agents_to_save.append(agent)
    else:
        agents_to_save = [agent]
    save_culture_yaml(agent.directory, agents_to_save)


def _cmd_create(args: argparse.Namespace) -> None:
    config = load_config_or_default(args.config)

    server_name = args.server or config.server.name or "culture"
    suffix = args.nick or sanitize_agent_name(os.path.basename(os.getcwd()))
    full_nick = f"{server_name}-{suffix}"

    _check_existing_agent(config, full_nick, args.config)

    raw_agent = _create_agent_config(args, full_nick)
    agent = _to_manifest_agent(raw_agent, suffix)

    _save_agent_to_directory(agent)
    add_to_manifest(args.config, suffix, agent.directory)

    if args.server and args.server != config.server.name:
        config.server.name = args.server
        save_server_config(str(args.config), config)

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


def _get_active_agents(config) -> list:
    """Return non-archived agents."""
    return [a for a in config.agents if not a.archived]


def _resolve_by_nick(config, nick: str):
    """Look up a single agent by nick, exit on error."""
    agent = config.get_agent(nick)
    if not agent:
        print(f"Agent '{nick}' not found in config", file=sys.stderr)
        sys.exit(1)
    if agent.archived:
        print(f"Agent '{nick}' is archived. Unarchive first:", file=sys.stderr)
        print(f"  culture agent unarchive {nick}", file=sys.stderr)
        sys.exit(1)
    return agent


def _resolve_auto(config) -> list:
    """Auto-resolve agents when no nick or --all given, exit if ambiguous."""
    active = _get_active_agents(config)
    if len(active) == 1:
        return active
    if len(active) == 0:
        archived_count = sum(1 for a in config.agents if a.archived)
        if archived_count:
            print(
                f"No active agents ({archived_count} archived). "
                "Unarchive an agent or create a new one.",
                file=sys.stderr,
            )
        else:
            print("No agents configured. Run 'culture agent create' first.", file=sys.stderr)
        sys.exit(1)
    print("Multiple agents configured. Specify a nick or use --all.", file=sys.stderr)
    for a in active:
        print(f"  {a.nick}", file=sys.stderr)
    sys.exit(1)


def _resolve_agents_to_start(config, args) -> list:
    """Return the list of agents to start, or exit with an error message."""
    if args.all:
        agents = _get_active_agents(config)
    elif args.nick:
        agents = [_resolve_by_nick(config, args.nick)]
    else:
        agents = _resolve_auto(config)

    if not agents:
        print(NO_AGENTS_MSG, file=sys.stderr)
        sys.exit(1)
    return agents


def _probe_server_connection(host: str, port: int, server_name: str) -> None:
    """Check that the IRC server is reachable; exit with an error message if not."""
    import socket as _socket

    try:
        conn = _socket.create_connection((host, port), timeout=2)
        conn.close()
    except OSError:
        hint = ""
        server_pid = read_pid(f"server-{server_name}")
        if not server_pid or not is_process_alive(server_pid):
            hint = f"\nStart it with: culture server start --name {server_name}"
        print(
            f"Error: cannot connect to IRC server at {host}:{port}.{hint}",
            file=sys.stderr,
        )
        sys.exit(1)


def _start_foreground(config: DaemonConfig, agents: list[AgentConfig]) -> None:
    """Start a single agent in the foreground."""
    if len(agents) != 1:
        print("--foreground requires a single agent nick, not --all", file=sys.stderr)
        sys.exit(1)
    agent = agents[0]
    print(f"Starting agent {agent.nick} in foreground...")
    asyncio.run(_run_single_agent(config, agent))


def _start_background(config: DaemonConfig, agents: list[AgentConfig]) -> None:
    """Start agents in background mode (fork on Unix, single on Windows)."""
    if sys.platform == "win32":
        if len(agents) != 1:
            print(
                "Multi-agent daemon mode not supported on Windows. Start agents individually.",
                file=sys.stderr,
            )
            sys.exit(1)
        agent = agents[0]
        print(f"Starting agent {agent.nick}...")
        asyncio.run(_run_single_agent(config, agent))
    else:
        _run_multi_agents(config, agents)


def _cmd_start(args: argparse.Namespace) -> None:
    config = load_config(args.config)

    agents = _resolve_agents_to_start(config, args)

    server_name = config.server.name
    _probe_server_connection(config.server.host, config.server.port, server_name)

    if getattr(args, "foreground", False):
        _start_foreground(config, agents)
    else:
        _start_background(config, agents)


def _make_backend_config(config: DaemonConfig, backend_daemon_config_cls):
    """Build a backend-specific DaemonConfig from the base config."""
    return backend_daemon_config_cls(
        server=config.server,
        supervisor=config.supervisor,
        webhooks=config.webhooks,
        buffer_size=config.buffer_size,
        poll_interval=config.poll_interval,
        sleep_start=config.sleep_start,
        sleep_end=config.sleep_end,
        agents=config.agents,
    )


def _create_codex_daemon(config: DaemonConfig, agent: AgentConfig):
    """Create a Codex backend daemon."""
    from culture.clients.codex.config import DaemonConfig as CodexDaemonConfig
    from culture.clients.codex.daemon import CodexDaemon

    return CodexDaemon(_make_backend_config(config, CodexDaemonConfig), agent)


def _coerce_to_acp_agent(agent: AgentConfig):
    """Ensure agent is an ACPAgentConfig, converting if necessary."""
    from culture.clients.acp.config import AgentConfig as ACPAgentConfig

    if isinstance(agent, ACPAgentConfig):
        return agent
    return ACPAgentConfig(
        nick=agent.nick,
        agent="acp",
        acp_command=getattr(agent, "acp_command", None) or ["opencode", "acp"],
        directory=agent.directory,
        channels=agent.channels,
        model=agent.model,
        system_prompt=agent.system_prompt,
        tags=agent.tags,
        icon=agent.icon,
    )


def _create_acp_daemon(config: DaemonConfig, agent: AgentConfig):
    """Create an ACP backend daemon."""
    from culture.clients.acp.config import DaemonConfig as ACPDaemonConfig
    from culture.clients.acp.daemon import ACPDaemon

    return ACPDaemon(
        _make_backend_config(config, ACPDaemonConfig),
        _coerce_to_acp_agent(agent),
    )


def _create_copilot_daemon(config: DaemonConfig, agent: AgentConfig):
    """Create a Copilot backend daemon."""
    from culture.clients.copilot.config import DaemonConfig as CopilotDaemonConfig
    from culture.clients.copilot.daemon import CopilotDaemon

    return CopilotDaemon(_make_backend_config(config, CopilotDaemonConfig), agent)


def _create_claude_daemon(config: DaemonConfig, agent: AgentConfig):
    """Create the default Claude backend daemon."""
    from culture.clients.claude.daemon import AgentDaemon

    return AgentDaemon(config, agent)


_BACKEND_DAEMON_FACTORIES = {
    "codex": _create_codex_daemon,
    "acp": _create_acp_daemon,
    "opencode": _create_acp_daemon,
    "copilot": _create_copilot_daemon,
}


async def _run_single_agent(config: DaemonConfig, agent: AgentConfig) -> None:
    """Run a single agent daemon in the foreground."""
    backend = getattr(agent, "agent", "claude")

    factory = _BACKEND_DAEMON_FACTORIES.get(backend, _create_claude_daemon)
    daemon = factory(config, agent)

    stop_event = asyncio.Event()
    daemon.set_stop_event(stop_event)

    await daemon.start()
    logger.info("Agent %s started (backend=%s)", agent.nick, backend)

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda *_: stop_event.set())

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


def _resolve_agents_to_stop(config, args) -> list:
    """Resolve which agents should be stopped, or exit with an error."""
    if args.all:
        return config.agents
    if args.nick:
        agent = config.get_agent(args.nick)
        if not agent:
            print(f"Agent '{args.nick}' not found in config", file=sys.stderr)
            sys.exit(1)
        return [agent]
    if len(config.agents) == 1:
        return config.agents
    if len(config.agents) == 0:
        print(NO_AGENTS_MSG, file=sys.stderr)
        sys.exit(1)
    # Multiple agents: try to match by current working directory
    cwd_real = os.path.realpath(os.getcwd())
    cwd_matches = [a for a in config.agents if os.path.realpath(a.directory) == cwd_real]
    if len(cwd_matches) == 1:
        return cwd_matches
    print(
        "Multiple agents configured. Specify a nick or use --all.",
        file=sys.stderr,
    )
    for a in config.agents:
        print(f"  {a.nick}", file=sys.stderr)
    sys.exit(1)


def _cmd_stop(args: argparse.Namespace) -> None:
    config = load_config_or_default(args.config)
    agents = _resolve_agents_to_stop(config, args)
    for agent in agents:
        stop_agent(agent.nick)


# -----------------------------------------------------------------------
# Status
# -----------------------------------------------------------------------


def _print_archived_info(agent) -> None:
    """Print archive details for an agent."""
    if not agent.archived:
        return
    print(f"\n  [archived since {agent.archived_at}]")
    if agent.archived_reason:
        print(f"  Reason: {agent.archived_reason}")


def _no_agents_message(config, show_all: bool) -> str:
    """Return appropriate message when no agents to display."""
    if not show_all:
        archived_count = sum(1 for a in config.agents if a.archived)
        if archived_count:
            return f"No active agents ({archived_count} archived, use --all to show)"
    return NO_AGENTS_MSG


def _cmd_status(args: argparse.Namespace) -> None:
    config = load_config_or_default(args.config)

    if not config.agents:
        print(NO_AGENTS_MSG)
        return

    if args.nick:
        agent = config.get_agent(args.nick)
        if not agent:
            print(f"Agent '{args.nick}' not found in config", file=sys.stderr)
            sys.exit(1)
        print_agent_detail(agent, args.config, args)
        _print_archived_info(agent)
        return

    show_all = getattr(args, "all", False)
    agents = config.agents if show_all else _get_active_agents(config)

    if not agents:
        print(_no_agents_message(config, show_all))
        return

    print_agents_overview(agents, args.full, show_archived_marker=show_all)
    print_bot_listing(show_archived=show_all)


# -----------------------------------------------------------------------
# Rename / Assign
# -----------------------------------------------------------------------


def _cmd_rename(args: argparse.Namespace) -> None:
    """Rename an agent's suffix within the same server."""
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
        rename_manifest_agent(args.config, old_nick, new_nick)
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
        rename_manifest_agent(args.config, old_nick, new_nick)
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


def _resolve_ipc_targets(config, args, command_name: str) -> list:
    """Resolve which agents to send IPC messages to.

    Errors flow through argparse's standard ``error: ...`` formatter on
    stderr with exit code 2 — matching how missing positional args are
    reported elsewhere in the CLI (#333 item 12).
    """
    if args.nick and args.all:
        _argparse_error(
            f"culture agent {command_name}",
            "cannot specify both <nick> and --all",
        )
    if not args.nick and not args.all:
        _argparse_error(
            f"culture agent {command_name}",
            "the following arguments are required: <nick> or --all",
        )
    if args.all:
        return config.agents
    for a in config.agents:
        if a.nick == args.nick:
            return [a]
    _argparse_error(
        f"culture agent {command_name}",
        f"agent {args.nick!r} not found in config",
    )
    return []  # unreachable — _argparse_error sys.exits


def _argparse_error(prog: str, message: str) -> None:
    """Mimic ``argparse.ArgumentParser.error()`` for hand-rolled validation.

    Writes ``<prog>: error: <message>`` to stderr and exits 2 — the same
    exit code argparse uses for usage errors. Lets handlers like
    ``agent sleep`` route bad-input errors through the standard
    formatter instead of `print(..., file=stderr) + sys.exit(1)`.
    """
    print(f"{prog}: error: {message}", file=sys.stderr)
    sys.exit(2)


def _send_ipc(agent, msg_type: str, action_verb: str) -> None:
    """Send a single IPC message to an agent and print result."""
    socket_path = agent_socket_path(agent.nick)
    resp = asyncio.run(ipc_request(socket_path, msg_type))
    if resp and resp.get("ok"):
        print(f"{agent.nick}: {action_verb}")
    else:
        print(f"{agent.nick}: failed (not running?)", file=sys.stderr)


def _ipc_to_agents(
    args: argparse.Namespace, msg_type: str, action_verb: str, command_name: str
) -> None:
    """Send an IPC message (pause/resume) to one or all agents."""
    config = load_config_or_default(args.config)
    targets = _resolve_ipc_targets(config, args, command_name)
    for agent in targets:
        _send_ipc(agent, msg_type, action_verb)


def _cmd_sleep(args: argparse.Namespace) -> None:
    _ipc_to_agents(args, "pause", "paused", "sleep")


def _cmd_wake(args: argparse.Namespace) -> None:
    _ipc_to_agents(args, "resume", "resumed", "wake")


def _cmd_learn(args: argparse.Namespace) -> None:
    from culture.learn_prompt import generate_learn_prompt

    config = load_config_or_default(args.config)
    cwd = os.getcwd()

    if args.nick:
        agent = config.get_agent(args.nick)
        if not agent:
            print(f"Agent '{args.nick}' not found in config", file=sys.stderr)
            sys.exit(1)
    else:
        cwd_real = os.path.realpath(cwd)
        agent = next(
            (a for a in config.agents if os.path.realpath(a.directory) == cwd_real),
            None,
        )

    kwargs = {"server": config.server.name, "directory": cwd}
    if agent:
        kwargs.update(
            nick=agent.nick, directory=agent.directory, backend=agent.agent, channels=agent.channels
        )
    print(generate_learn_prompt(**kwargs))


# -----------------------------------------------------------------------
# Message / Read (mirrored in channel.py)
# -----------------------------------------------------------------------


def _cmd_message(args: argparse.Namespace) -> None:
    if not args.target.strip():
        print("Error: target nick cannot be empty", file=sys.stderr)
        sys.exit(1)
    if not args.text.strip():
        print("Error: message text cannot be empty", file=sys.stderr)
        sys.exit(1)
    # The IRC server (especially with federation) is the source of truth
    # for who is reachable on the mesh — not the local config. The
    # previous local-config gate blocked DMs to agents on federated peers
    # that we hadn't manually registered. The send now goes through
    # unconditionally; if the target nick is unknown to the server, the
    # IRC ``401 NOSUCHNICK`` numeric propagates through the observer
    # rather than being short-circuited by stale config. (#333 item 11.)
    observer = get_observer(args.config)
    asyncio.run(observer.send_message(args.target, args.text))
    print(f"Sent to {args.target}")


def _cmd_read(args: argparse.Namespace) -> None:
    print(
        "DM history is not yet implemented. The server does not store direct message history.",
        file=sys.stderr,
    )
    print("Use 'culture channel read <channel>' for channel history.", file=sys.stderr)
    sys.exit(1)


# -----------------------------------------------------------------------
# Archive / Unarchive
# -----------------------------------------------------------------------


def _cmd_archive(args: argparse.Namespace) -> None:
    """Archive an agent: stop if running, set archived flag."""
    config = load_config_or_default(args.config)
    agent = config.get_agent(args.nick)
    if not agent:
        print(f"Agent '{args.nick}' not found in config", file=sys.stderr)
        sys.exit(1)

    if agent.archived:
        print(f"Agent '{args.nick}' is already archived")
        return

    # Stop the agent if it's running
    pid = read_pid(f"agent-{args.nick}")
    if pid and is_process_alive(pid):
        stop_agent(args.nick)

    archive_manifest_agent(args.config, args.nick, reason=args.reason)

    print(f"Agent archived: {args.nick}")
    if args.reason:
        print(f"  Reason: {args.reason}")
    print(f"\nTo restore: culture agent unarchive {args.nick}")


def _cmd_unarchive(args: argparse.Namespace) -> None:
    """Restore an archived agent."""
    try:
        unarchive_manifest_agent(args.config, args.nick)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    print(f"Agent unarchived: {args.nick}")
    print(f"\nStart with: culture agent start {args.nick}")


def _cmd_delete(args: argparse.Namespace) -> None:
    """Remove an agent from config entirely."""
    config = load_config_or_default(args.config)
    agent = config.get_agent(args.nick)
    if not agent:
        print(f"Agent '{args.nick}' not found in config", file=sys.stderr)
        sys.exit(1)

    # Stop the agent if it's running
    pid = read_pid(f"agent-{args.nick}")
    if pid and is_process_alive(pid):
        stop_agent(args.nick)

    remove_manifest_agent(args.config, args.nick)
    print(f"Agent deleted: {args.nick}")


# -----------------------------------------------------------------------
# Register / Unregister
# -----------------------------------------------------------------------


def _cmd_register(args: argparse.Namespace) -> None:
    """Register a directory containing culture.yaml."""
    directory = args.path if args.path else os.getcwd()
    directory = str(Path(directory).resolve())

    try:
        agents = load_culture_yaml(directory)
    except FileNotFoundError:
        print(f"No culture.yaml found in {directory}", file=sys.stderr)
        sys.exit(1)

    if len(agents) > 1 and args.suffix is None:
        print(
            f"Multiple agents in {directory}/culture.yaml. " "Use --suffix to specify which one.",
            file=sys.stderr,
        )
        print("Available suffixes:", file=sys.stderr)
        for a in agents:
            print(f"  {a.suffix}", file=sys.stderr)
        sys.exit(1)

    targets = agents if args.suffix is None else [a for a in agents if a.suffix == args.suffix]
    if not targets:
        print(f"Suffix {args.suffix!r} not found in culture.yaml", file=sys.stderr)
        sys.exit(1)

    config = load_config_or_default(args.config)
    server_name = config.server.name

    for agent in targets:
        try:
            add_to_manifest(args.config, agent.suffix, directory)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"Registered: {server_name}-{agent.suffix} at {directory}")


def _cmd_unregister(args: argparse.Namespace) -> None:
    """Remove an agent from the manifest."""
    target = args.target
    config = load_config_or_default(args.config)

    prefix = f"{config.server.name}-"
    suffix = target.removeprefix(prefix) if target.startswith(prefix) else target

    try:
        remove_from_manifest(args.config, suffix)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Unregistered: {prefix}{suffix}")


# -----------------------------------------------------------------------
# Migrate
# -----------------------------------------------------------------------


def _cmd_migrate(args: argparse.Namespace) -> None:
    """Migrate from agents.yaml to server.yaml + per-directory culture.yaml."""
    from culture.config import _is_legacy_format, migrate_legacy_to_manifest

    legacy_path = Path(args.config)
    if not legacy_path.exists():
        print(f"No agents.yaml found at {legacy_path}", file=sys.stderr)
        sys.exit(1)

    if not _is_legacy_format(legacy_path):
        print(f"{legacy_path} is already in manifest format", file=sys.stderr)
        sys.exit(1)

    config = migrate_legacy_to_manifest(legacy_path)

    agent_count = len(config.manifest)
    dir_count = len(set(config.manifest.values()))
    print(f"\nMigration complete: {agent_count} agent(s) across {dir_count} directory(ies)")
    print(f"\nAgent commands now use {legacy_path} by default.")
    print("Verify with: culture agent status")
