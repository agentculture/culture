"""Agent subcommands: culture agents {create,join,start,stop,status,...}."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import logging
import os
import signal
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from culture_core.clients.acp.config import AgentConfig as ACPAgentConfig
    from culture_core.clients.codex.config import AgentConfig as CodexAgentConfig
    from culture_core.clients.colleague.config import AgentConfig as ColleagueAgentConfig
    from culture_core.clients.copilot.config import AgentConfig as CopilotAgentConfig

from culture_core.cli._errors import (
    EXIT_ENV_ERROR,
    EXIT_USER_ERROR,
    CultureError,
    classify_daemon_exit,
)
from culture_core.cli._passthrough import _translate_exit
from culture_core.config import (
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
from culture_core.pidfile import (
    is_process_alive,
    read_pid,
    remove_pid,
    write_pid,
)

from .shared.cli_tracing import cli_tracer, inject_traceparent_env, shutdown_cli_tracing
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

NAME = "agents"

# Verbs forwarded verbatim to steward.cli.main. Registered as thin REMAINDER
# subparsers and short-circuited before argparse (see _maybe_forward_to_steward
# in culture_core.cli.__init__) so --help reaches steward's own parser.
_STEWARD_FORWARDED_VERBS = ("doctor", "show", "overview")

_NICK_HELP = "Agent suffix or full nick"


def register(subparsers: argparse._SubParsersAction) -> None:
    agent_parser = subparsers.add_parser("agents", help="Manage AI agents")
    agent_sub = agent_parser.add_subparsers(dest="agents_command")

    for verb in _STEWARD_FORWARDED_VERBS:
        fwd = agent_sub.add_parser(verb, help=f"(forwarded to steward {verb})", add_help=False)
        fwd.add_argument("argv", nargs=argparse.REMAINDER)

    # -- create ---------------------------------------------------------------
    _agent_args = [
        ("--server", {"default": None, "help": "Server name prefix"}),
        ("--nick", {"default": None, "help": "Agent suffix (after server-)"}),
        (
            "--agent",
            {
                "default": "claude",
                "choices": ["claude", "codex", "colleague", "copilot", "acp"],
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
    unregister_parser.add_argument("target", help=_NICK_HELP)
    unregister_parser.add_argument("--config", default=DEFAULT_SERVER_CONFIG, help=_CONFIG_HELP)

    # -- install --------------------------------------------------------------
    install_parser = agent_sub.add_parser(
        "install",
        help="Install systemd/launchd/scheduled-task unit for a single agent",
    )
    install_parser.add_argument("nick", help=_NICK_HELP)
    install_parser.add_argument("--config", default=DEFAULT_SERVER_CONFIG, help=_CONFIG_HELP)
    install_parser.add_argument(
        "--allow-dev-interpreter",
        action="store_true",
        help=(
            "Bake a dev/worktree-virtualenv interpreter into the unit anyway. "
            "By default install refuses a fragile (.venv/venv) interpreter that "
            "would crash-loop the service if the checkout is removed."
        ),
    )

    # -- uninstall ------------------------------------------------------------
    uninstall_parser = agent_sub.add_parser(
        "uninstall",
        help="Remove the systemd/launchd/scheduled-task unit for a single agent",
    )
    uninstall_parser.add_argument("nick", help=_NICK_HELP)
    uninstall_parser.add_argument("--config", default=DEFAULT_SERVER_CONFIG, help=_CONFIG_HELP)

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
    if not args.agents_command:
        print(
            "Usage: culture agents {create|join|start|stop|status|rename|assign|sleep|wake|learn|message|read|archive|unarchive|delete|register|unregister|install|uninstall|migrate|doctor|show|overview}",
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
        "install": _cmd_install,
        "uninstall": _cmd_uninstall,
        "migrate": _cmd_migrate,
    }
    handler = handlers.get(args.agents_command)
    if handler:
        handler(args)
    else:
        raise CultureError(
            EXIT_USER_ERROR,
            f"Unknown agent command: {args.agents_command}",
            "run 'culture agents --help' to list the available verbs",
        )


# -----------------------------------------------------------------------
# Create / Join
# -----------------------------------------------------------------------


def _create_codex_config(full_nick: str) -> CodexAgentConfig:
    """Build a CodexAgentConfig."""
    from culture_core.clients.codex.config import AgentConfig as CodexAgentConfig

    return CodexAgentConfig(
        nick=full_nick,
        agent="codex",
        directory=os.getcwd(),
        channels=[DEFAULT_CHANNEL],
    )


def _create_copilot_config(full_nick: str) -> CopilotAgentConfig:
    """Build a CopilotAgentConfig."""
    from culture_core.clients.copilot.config import AgentConfig as CopilotAgentConfig

    return CopilotAgentConfig(
        nick=full_nick,
        agent="copilot",
        directory=os.getcwd(),
        channels=[DEFAULT_CHANNEL],
    )


def _create_colleague_config(full_nick: str) -> ColleagueAgentConfig:
    """Build a ColleagueAgentConfig."""
    from culture_core.clients.colleague.config import AgentConfig as ColleagueAgentConfig

    return ColleagueAgentConfig(
        nick=full_nick,
        agent="colleague",
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
        raise CultureError(
            EXIT_USER_ERROR,
            f"--acp-command must be a non-empty list of strings (got: {raw_command!r})",
            'pass a JSON list of strings, e.g. --acp-command \'["opencode","acp"]\'',
        )
    return acp_cmd


def _create_acp_config(full_nick: str, args: argparse.Namespace) -> ACPAgentConfig:
    """Build an ACPAgentConfig."""
    from culture_core.clients.acp.config import AgentConfig as ACPAgentConfig

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
        "colleague": lambda: _create_colleague_config(full_nick),
        "acp": lambda: _create_acp_config(full_nick, args),
    }
    factory = factories.get(args.agent)
    if factory:
        return factory()
    return _create_default_config(full_nick, args.agent)


def _check_existing_agent(config, full_nick: str, config_path: str) -> None:
    """Check for duplicate agent nick.  Removes archived duplicates; errors on active ones."""
    for existing in config.agents:
        if existing.nick != full_nick:
            continue
        if existing.archived:
            print(f"Replacing archived agent '{full_nick}'")
            remove_manifest_agent(config_path, full_nick)
            return
        raise CultureError(
            EXIT_USER_ERROR,
            f"Agent '{full_nick}' already exists in config "
            f"(directory: {existing.directory}, backend: {existing.agent}, "
            f"config: {config_path})",
            f"start it with 'culture agents start {full_nick}', "
            "or pass --nick to create an agent with a different name",
        )


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
    print(f"Start with: culture agents start {full_nick}")


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
    """Look up a single agent by nick; raise CultureError with candidates."""
    agent = config.get_agent(nick)
    if not agent:
        configured = ", ".join(sorted(a.nick for a in config.agents)) or "none"
        raise CultureError(
            EXIT_USER_ERROR,
            f"Agent '{nick}' not found in config (configured nicks: {configured})",
            "run 'culture agents status' to list agents, or 'culture agents create' to add one",
        )
    if agent.archived:
        raise CultureError(
            EXIT_USER_ERROR,
            f"Agent '{nick}' is archived",
            f"unarchive first: culture agents unarchive {nick}",
        )
    return agent


def _resolve_auto(config) -> list:
    """Auto-resolve agents when no nick or --all given, raise if ambiguous."""
    active = _get_active_agents(config)
    if len(active) == 1:
        return active
    if len(active) == 0:
        archived = sorted(a.nick for a in config.agents if a.archived)
        if archived:
            raise CultureError(
                EXIT_USER_ERROR,
                f"No active agents ({len(archived)} archived: {', '.join(archived)})",
                f"unarchive one with 'culture agents unarchive {archived[0]}', "
                "or create a new agent with 'culture agents create'",
            )
        raise CultureError(
            EXIT_USER_ERROR,
            "No agents configured",
            "run 'culture agents create' to add one",
        )
    nicks = sorted(a.nick for a in active)
    raise CultureError(
        EXIT_USER_ERROR,
        f"Multiple agents configured ({', '.join(nicks)}) — specify a nick or use --all",
        f"pick one, e.g. 'culture agents start {nicks[0]}', or run 'culture agents start --all'",
    )


def _resolve_agents_to_start(config, args) -> list:
    """Return the list of agents to start, or raise with an error message."""
    if args.all:
        agents = _get_active_agents(config)
    elif args.nick:
        agents = [_resolve_by_nick(config, args.nick)]
    else:
        agents = _resolve_auto(config)

    if not agents:
        raise CultureError(
            EXIT_USER_ERROR,
            NO_AGENTS_MSG,
            "run 'culture agents create' to add an agent, then 'culture agents start --all'",
        )
    return agents


def _probe_server_connection(host: str, port: int, server_name: str) -> None:
    """Check that the IRC server is reachable; raise with an error message if not."""
    import socket as _socket

    try:
        conn = _socket.create_connection((host, port), timeout=2)
        conn.close()
    except OSError as exc:
        server_pid = read_pid(f"server-{server_name}")
        if not server_pid or not is_process_alive(server_pid):
            remediation = f"start the server first: culture server start --name {server_name}"
        else:
            remediation = (
                f"the server process (PID {server_pid}) is alive but not accepting "
                f"connections on {host}:{port} — check 'culture server status "
                f"--name {server_name}' and the server logs"
            )
        raise CultureError(
            EXIT_USER_ERROR,
            f"cannot connect to IRC server at {host}:{port}",
            remediation,
        ) from exc


def _start_foreground(config: DaemonConfig, agents: list[AgentConfig]) -> None:
    """Start a single agent in the foreground."""
    if len(agents) != 1:
        example = agents[0].nick if agents else "<nick>"
        raise CultureError(
            EXIT_USER_ERROR,
            "--foreground requires a single agent nick, not --all",
            f"start one agent, e.g. 'culture agents start {example} --foreground', "
            "or drop --foreground to start all agents in the background",
        )
    agent = agents[0]
    print(f"Starting agent {agent.nick} in foreground...")
    asyncio.run(_run_single_agent(config, agent))


def _start_background(config: DaemonConfig, agents: list[AgentConfig]) -> None:
    """Start agents in background mode (fork on Unix, single on Windows)."""
    if sys.platform == "win32":
        if len(agents) != 1:
            example = agents[0].nick if agents else "<nick>"
            raise CultureError(
                EXIT_USER_ERROR,
                "Multi-agent daemon mode not supported on Windows",
                f"start agents individually, e.g. 'culture agents start {example}'",
            )
        agent = agents[0]
        print(f"Starting agent {agent.nick}...")
        asyncio.run(_run_single_agent(config, agent))
    else:
        _run_multi_agents(config, agents)


def _cmd_start(args: argparse.Namespace) -> None:
    config = load_config(args.config)

    # Span covers the CLI-side start phases (resolve, probe). It ends —
    # and the local provider is torn down — BEFORE any fork so no
    # exporter state crosses into daemon children; the daemon joins the
    # trace via the TRACEPARENT env injected below (cultureagent#43).
    foreground = getattr(args, "foreground", False)
    tracer = cli_tracer(config)
    try:
        with tracer.start_as_current_span("culture.cli.agents.start") as span:
            span.set_attribute("culture.cli.mode", "foreground" if foreground else "background")
            agents = _resolve_agents_to_start(config, args)
            span.set_attribute("culture.agent.nicks", [a.nick for a in agents])
            span.set_attribute(
                "culture.agent.backends",
                sorted({getattr(a, "agent", "claude") for a in agents}),
            )

            server_name = config.server.name
            _probe_server_connection(config.server.host, config.server.port, server_name)
            inject_traceparent_env()
    finally:
        # Also on sys.exit from resolve/probe: flush + drop the provider
        # so no exporter state survives into forks or later verbs.
        shutdown_cli_tracing()

    if foreground:
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


# Backend -> (extra name, SDK modules that extra provides). The SDKs are
# optional extras since Phase C of #462; every factory probes before building
# its daemon (all-backends rule) because the failure mode differs per backend:
# claude/acp break at daemon import (top-level SDK imports in cultureagent),
# copilot only lazily at session start. codex declares no SDK.
_BACKEND_SDK_PROBES = {
    "claude": ("claude", ("claude_agent_sdk", "anthropic")),
    "acp": ("acp", ("claude_agent_sdk", "anthropic")),
    "copilot": ("copilot", ("copilot",)),
    "codex": (None, ()),
    # colleague wraps colleague[culture]; the ColleagueDaemon resolves the
    # ``colleague`` package lazily (like copilot), so probe it before building
    # to fail fast with a 'pip install culture[colleague]' hint.
    "colleague": ("colleague", ("colleague",)),
}


def _require_backend_sdk(backend: str) -> None:
    """Fail fast with a remediation hint when the backend's SDK extra is missing."""
    extra, modules = _BACKEND_SDK_PROBES[backend]
    if not extra:
        return
    for module in modules:
        # An already-imported module is available by definition (and may carry
        # __spec__=None — e.g. a test stub — which makes find_spec raise);
        # sys.modules[module] is None is the import-system marker for "blocked".
        if module in sys.modules:
            found = sys.modules[module] is not None
        else:
            try:
                found = importlib.util.find_spec(module) is not None
            except (ImportError, ValueError):
                found = False
        if not found:
            raise CultureError(
                code=EXIT_ENV_ERROR,
                message=(
                    f"the {backend} backend needs the '{extra}' extra (missing module: {module})"
                ),
                remediation=(
                    f"pip install 'culture[{extra}]' (or: uv tool install 'culture[{extra}]')"
                ),
            )


def _create_codex_daemon(config: DaemonConfig, agent: AgentConfig):
    """Create a Codex backend daemon."""
    _require_backend_sdk("codex")
    from cultureagent.clients.codex.daemon import CodexDaemon

    from culture_core.clients.codex.config import DaemonConfig as CodexDaemonConfig

    return CodexDaemon(_make_backend_config(config, CodexDaemonConfig), agent)


def _coerce_to_acp_agent(agent: AgentConfig):
    """Ensure agent is an ACPAgentConfig, converting if necessary."""
    from culture_core.clients.acp.config import AgentConfig as ACPAgentConfig

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
        # Forward attention overrides so ACP honors culture.yaml ``attention:``
        # like claude/codex/copilot (all-backends rule). (culture-core#9)
        attention_overrides=getattr(agent, "attention_overrides", None),
    )


def _create_acp_daemon(config: DaemonConfig, agent: AgentConfig):
    """Create an ACP backend daemon."""
    _require_backend_sdk("acp")
    from cultureagent.clients.acp.daemon import ACPDaemon

    from culture_core.clients.acp.config import DaemonConfig as ACPDaemonConfig

    return ACPDaemon(
        _make_backend_config(config, ACPDaemonConfig),
        _coerce_to_acp_agent(agent),
    )


def _create_copilot_daemon(config: DaemonConfig, agent: AgentConfig):
    """Create a Copilot backend daemon."""
    _require_backend_sdk("copilot")
    from cultureagent.clients.copilot.daemon import CopilotDaemon

    from culture_core.clients.copilot.config import DaemonConfig as CopilotDaemonConfig

    return CopilotDaemon(_make_backend_config(config, CopilotDaemonConfig), agent)


def _create_colleague_daemon(config: DaemonConfig, agent: AgentConfig):
    """Create a colleague backend daemon (ColleagueHarness wrapped by cultureagent)."""
    # backend-specific: initial colleague backend landing — claude/codex already
    # present, colleague joins at parity (three-minds t1, spec 2026-07-02); there
    # is no claude/codex edit to propagate this to.
    _require_backend_sdk("colleague")
    from cultureagent.clients.colleague.daemon import ColleagueDaemon

    from culture_core.clients.colleague.config import DaemonConfig as ColleagueDaemonConfig

    return ColleagueDaemon(_make_backend_config(config, ColleagueDaemonConfig), agent)


def _create_claude_daemon(config: DaemonConfig, agent: AgentConfig):
    """Create the default Claude backend daemon."""
    _require_backend_sdk("claude")
    from cultureagent.clients.claude.daemon import AgentDaemon

    from culture_core.clients.claude.config import DaemonConfig as ClaudeDaemonConfig

    # Wrap the central config into the claude backend DaemonConfig (which carries
    # the daemon-level ``attention`` defaults), mirroring codex/copilot/acp.
    # Passing the bare central ServerConfig made claude crash at runtime in
    # resolve_attention_config -> ``daemon_cfg.attention`` ('ServerConfig' has no
    # 'attention'), even after AgentConfig gained attention_overrides. (culture-core#9)
    return AgentDaemon(_make_backend_config(config, ClaudeDaemonConfig), agent)


_BACKEND_DAEMON_FACTORIES = {
    "claude": _create_claude_daemon,
    "codex": _create_codex_daemon,
    "colleague": _create_colleague_daemon,
    "acp": _create_acp_daemon,
    "opencode": _create_acp_daemon,  # alias for acp
    "copilot": _create_copilot_daemon,
}


def _resolve_daemon_factory(backend: str | None):
    """Resolve the daemon factory for *backend*, failing loudly on unknowns.

    An unset/empty backend keeps the historical claude default. An
    explicitly set unknown value raises :class:`CultureError` naming the
    valid backends — the old ``.get(backend, _create_claude_daemon)``
    fallback silently ran unknown backends as claude (observed in
    production: an agent configured with a not-yet-existing backend ran
    as a claude agent unnoticed).
    """
    if not backend:
        return _create_claude_daemon
    factory = _BACKEND_DAEMON_FACTORIES.get(backend)
    if factory is None:
        valid = ", ".join(sorted(_BACKEND_DAEMON_FACTORIES))
        raise CultureError(
            EXIT_USER_ERROR,
            f"unknown agent backend '{backend}' (valid backends: {valid})",
            "fix the 'backend:' value in the agent's culture.yaml "
            "(or omit it to default to claude)",
        )
    return factory


async def _run_single_agent(config: DaemonConfig, agent: AgentConfig) -> None:
    """Run a single agent daemon in the foreground."""
    backend = getattr(agent, "agent", "claude")

    factory = _resolve_daemon_factory(backend)
    daemon = factory(config, agent)

    stop_event = asyncio.Event()
    daemon.set_stop_event(stop_event)

    await daemon.start()
    logger.info("Agent %s started (backend=%s)", agent.nick, backend)

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except RuntimeError:
            signal.signal(sig, lambda *_: stop_event.set())

    await stop_event.wait()
    logger.info("Shutting down %s", agent.nick)
    await daemon.stop()


def _agent_daemon_child(config: DaemonConfig, agent: AgentConfig) -> None:
    """Run one forked agent daemon child to completion. Never returns.

    Follows the daemon exit contract (#15): clean shutdown exits 0,
    transient crashes exit EXIT_DAEMON_TRANSIENT so the service manager
    restarts us, permanent config/user errors exit EXIT_DAEMON_PERMANENT
    so it parks the unit instead of restart-looping. The old
    `finally: os._exit(0)` masked every agent-daemon crash as success.
    """
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

    exit_code = 0
    try:
        asyncio.run(_run_single_agent(config, agent))
    except KeyboardInterrupt:
        # Clean shutdown path — SIGINT.
        pass
    except SystemExit as exc:  # NOSONAR S5754 — child must report sys.exit codes
        # sys.exit() inside the child must not fall through to
        # os._exit(0): mirror _passthrough._translate_exit's policy.
        exit_code, _ = _translate_exit(exc.code)
        if exit_code:
            logger.exception(
                "agent daemon %s called sys.exit(%r); exiting %d",
                agent.nick,
                exc.code,
                exit_code,
            )
    except Exception as exc:  # noqa: BLE001 - crash must reach the OS as non-zero exit
        exit_code = classify_daemon_exit(exc)
        logger.exception(
            "agent daemon %s crashed; exiting with status %d",
            agent.nick,
            exit_code,
        )
    finally:
        remove_pid(pid_name)
        os._exit(exit_code)


def _run_multi_agents(config: DaemonConfig, agents: list[AgentConfig]) -> None:
    """Fork each agent into its own background process."""
    for agent in agents:
        pid = os.fork()
        if pid == 0:
            _agent_daemon_child(config, agent)
        else:
            print(f"Started {agent.nick} (PID {pid})")


def _resolve_agents_to_stop(config, args) -> list:
    """Resolve which agents should be stopped, or raise with an error."""
    if args.all:
        return config.agents
    if args.nick:
        agent = config.get_agent(args.nick)
        if not agent:
            configured = ", ".join(sorted(a.nick for a in config.agents)) or "none"
            raise CultureError(
                EXIT_USER_ERROR,
                f"Agent '{args.nick}' not found in config (configured nicks: {configured})",
                "run 'culture agents status' to list agents",
            )
        return [agent]
    if len(config.agents) == 1:
        return config.agents
    if len(config.agents) == 0:
        raise CultureError(
            EXIT_USER_ERROR,
            NO_AGENTS_MSG,
            "run 'culture agents create' to add an agent",
        )
    # Multiple agents: try to match by current working directory
    cwd_real = os.path.realpath(os.getcwd())
    cwd_matches = [a for a in config.agents if os.path.realpath(a.directory) == cwd_real]
    if len(cwd_matches) == 1:
        return cwd_matches
    nicks = sorted(a.nick for a in config.agents)
    raise CultureError(
        EXIT_USER_ERROR,
        f"Multiple agents configured ({', '.join(nicks)}) — specify a nick or use --all",
        f"pick one, e.g. 'culture agents stop {nicks[0]}', or run 'culture agents stop --all'",
    )


def _cmd_stop(args: argparse.Namespace) -> None:
    config = load_config_or_default(args.config)
    tracer = cli_tracer(config)
    try:
        with tracer.start_as_current_span("culture.cli.agents.stop") as span:
            agents = _resolve_agents_to_stop(config, args)
            span.set_attribute("culture.agent.nicks", [a.nick for a in agents])
            for agent in agents:
                stop_agent(agent.nick)
    finally:
        shutdown_cli_tracing()


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
            configured = ", ".join(sorted(a.nick for a in config.agents)) or "none"
            raise CultureError(
                EXIT_USER_ERROR,
                f"Agent '{args.nick}' not found in config (configured nicks: {configured})",
                "run 'culture agents status' without a nick to list all agents",
            )
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
    from culture_core.pidfile import rename_pid

    config = load_config_or_default(args.config)
    old_nick = args.nick
    server_name = config.server.name
    expected_prefix = f"{server_name}-"

    if not old_nick.startswith(expected_prefix):
        raise CultureError(
            EXIT_USER_ERROR,
            f"Agent '{old_nick}' does not belong to server '{server_name}' "
            f"(nicks here start with '{expected_prefix}')",
            "run 'culture agents status' to list this server's agents",
        )

    try:
        new_suffix = sanitize_agent_name(args.new_name)
    except ValueError:
        raise CultureError(
            EXIT_USER_ERROR,
            f"Invalid agent name: {args.new_name!r}",
            "use a name containing letters, digits, or hyphens, "
            f"e.g. 'culture agents rename {old_nick} my-agent'",
        ) from None

    new_nick = f"{server_name}-{new_suffix}"

    if old_nick == new_nick:
        print(f"Agent is already named '{old_nick}'")
        return

    try:
        rename_manifest_agent(args.config, old_nick, new_nick)
    except ValueError as exc:
        raise CultureError(
            EXIT_USER_ERROR,
            str(exc),
            "run 'culture agents status --all' to see configured agents and pick an unused name",
        ) from exc

    rename_pid(f"agent-{old_nick}", f"agent-{new_nick}")

    print(f"Agent renamed: {old_nick} → {new_nick}")
    print()
    print("Restart the agent for the new nick to take effect:")
    print(f"  culture agents stop {old_nick}   # if still running under old name")
    print(f"  culture agents start {new_nick}")


def _cmd_assign(args: argparse.Namespace) -> None:
    """Move an agent to a different server (change nick prefix)."""
    from culture_core.pidfile import rename_pid

    config = load_config_or_default(args.config)
    old_nick = args.nick
    server_name = config.server.name
    expected_prefix = f"{server_name}-"

    if not old_nick.startswith(expected_prefix):
        raise CultureError(
            EXIT_USER_ERROR,
            f"Agent '{old_nick}' does not belong to server '{server_name}' "
            f"(nicks here start with '{expected_prefix}')",
            "run 'culture agents status' to list this server's agents",
        )

    suffix = old_nick[len(expected_prefix) :]

    try:
        new_server = sanitize_agent_name(args.server)
    except ValueError:
        raise CultureError(
            EXIT_USER_ERROR,
            f"Invalid server name: {args.server!r}",
            "use a server name containing letters, digits, or hyphens, "
            f"e.g. 'culture agents assign {old_nick} spark'",
        ) from None

    new_nick = f"{new_server}-{suffix}"

    if old_nick == new_nick:
        print(f"Agent already belongs to server '{new_server}'")
        return

    try:
        rename_manifest_agent(args.config, old_nick, new_nick)
    except ValueError as exc:
        raise CultureError(
            EXIT_USER_ERROR,
            str(exc),
            "run 'culture agents status --all' to see configured agents and pick an unused name",
        ) from exc

    rename_pid(f"agent-{old_nick}", f"agent-{new_nick}")

    print(f"Agent reassigned: {old_nick} → {new_nick}")
    print()
    print("Restart the agent for the new nick to take effect:")
    print(f"  culture agents stop {old_nick}   # if still running under old name")
    print(f"  culture agents start {new_nick}")


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
            f"culture agents {command_name}",
            "cannot specify both <nick> and --all",
        )
    if not args.nick and not args.all:
        _argparse_error(
            f"culture agents {command_name}",
            "the following arguments are required: <nick> or --all",
        )
    if args.all:
        return config.agents
    for a in config.agents:
        if a.nick == args.nick:
            return [a]
    _argparse_error(
        f"culture agents {command_name}",
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
    from culture_core.learn_prompt import generate_learn_prompt

    config = load_config_or_default(args.config)
    cwd = os.getcwd()

    if args.nick:
        agent = config.get_agent(args.nick)
        if not agent:
            configured = ", ".join(sorted(a.nick for a in config.agents)) or "none"
            raise CultureError(
                EXIT_USER_ERROR,
                f"Agent '{args.nick}' not found in config (configured nicks: {configured})",
                "run 'culture agents status' to list agents, "
                "or omit --nick to auto-detect from the current directory",
            )
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
        raise CultureError(
            EXIT_USER_ERROR,
            "target nick cannot be empty",
            "pass the agent nick, e.g. 'culture agents message spark-claude \"hello\"'",
        )
    if not args.text.strip():
        raise CultureError(
            EXIT_USER_ERROR,
            "message text cannot be empty",
            f"pass a non-empty message, e.g. 'culture agents message {args.target} \"hello\"'",
        )
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
    raise CultureError(
        EXIT_USER_ERROR,
        "DM history is not yet implemented — the server does not store direct message history",
        "use 'culture channel read <channel>' for channel history",
    )


# -----------------------------------------------------------------------
# Archive / Unarchive
# -----------------------------------------------------------------------


def _cmd_archive(args: argparse.Namespace) -> None:
    """Archive an agent: stop if running, set archived flag."""
    config = load_config_or_default(args.config)
    agent = config.get_agent(args.nick)
    if not agent:
        configured = ", ".join(sorted(a.nick for a in config.agents)) or "none"
        raise CultureError(
            EXIT_USER_ERROR,
            f"Agent '{args.nick}' not found in config (configured nicks: {configured})",
            "run 'culture agents status --all' to list agents, including archived ones",
        )

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
    print(f"\nTo restore: culture agents unarchive {args.nick}")


def _cmd_unarchive(args: argparse.Namespace) -> None:
    """Restore an archived agent."""
    try:
        unarchive_manifest_agent(args.config, args.nick)
    except ValueError as exc:
        raise CultureError(
            EXIT_USER_ERROR,
            str(exc),
            "run 'culture agents status --all' to list archived agents",
        ) from exc

    print(f"Agent unarchived: {args.nick}")
    print(f"\nStart with: culture agents start {args.nick}")


def _cmd_delete(args: argparse.Namespace) -> None:
    """Remove an agent from config entirely."""
    config = load_config_or_default(args.config)
    agent = config.get_agent(args.nick)
    if not agent:
        configured = ", ".join(sorted(a.nick for a in config.agents)) or "none"
        raise CultureError(
            EXIT_USER_ERROR,
            f"Agent '{args.nick}' not found in config (configured nicks: {configured})",
            "run 'culture agents status --all' to list agents",
        )

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
        raise CultureError(
            EXIT_USER_ERROR,
            f"No culture.yaml found in {directory}",
            "run 'culture agents create' in that directory to generate one, "
            "or pass the directory that contains culture.yaml: culture agents register <path>",
        ) from None

    if len(agents) > 1 and args.suffix is None:
        suffixes = ", ".join(a.suffix for a in agents)
        raise CultureError(
            EXIT_USER_ERROR,
            f"Multiple agents in {directory}/culture.yaml (suffixes: {suffixes}) — "
            "use --suffix to specify which one",
            f"rerun with a suffix, e.g. 'culture agents register {directory} "
            f"--suffix {agents[0].suffix}'",
        )

    targets = agents if args.suffix is None else [a for a in agents if a.suffix == args.suffix]
    if not targets:
        available = ", ".join(a.suffix for a in agents) or "none"
        raise CultureError(
            EXIT_USER_ERROR,
            f"Suffix {args.suffix!r} not found in culture.yaml (available: {available})",
            "rerun --suffix with one of the suffixes listed in the error",
        )

    config = load_config_or_default(args.config)
    server_name = config.server.name

    for agent in targets:
        try:
            add_to_manifest(args.config, agent.suffix, directory)
        except ValueError as e:
            raise CultureError(
                EXIT_USER_ERROR,
                str(e),
                f"unregister the existing entry first with 'culture agents unregister "
                f"{agent.suffix}' if you want to move it here",
            ) from e
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
        raise CultureError(
            EXIT_USER_ERROR,
            str(e),
            "run 'culture agents status' to list registered agents",
        ) from e
    print(f"Unregistered: {prefix}{suffix}")


# -----------------------------------------------------------------------
# Install / Uninstall — per-agent service unit
# -----------------------------------------------------------------------


def _resolve_manifest_suffix(config: ServerConfig, nick: str) -> str:
    """Accept suffix or full <server>-<suffix> nick and return the suffix
    if it's in the manifest. Raise CultureError (exit 1) if not found.

    Disambiguation: try the input as a bare suffix first. Only fall back
    to stripping the `<server>-` prefix if the bare form isn't in the
    manifest — otherwise a legitimate suffix like `spark-claude` (with
    server `spark`) would be silently rewritten to `claude`.
    """
    server_name = config.server.name
    if nick in config.manifest:
        return nick
    prefix = f"{server_name}-"
    if nick.startswith(prefix):
        stripped = nick.removeprefix(prefix)
        if stripped in config.manifest:
            return stripped
    display = nick if nick.startswith(prefix) else f"{prefix}{nick}"
    registered = ", ".join(sorted(config.manifest)) or "none"
    raise CultureError(
        EXIT_USER_ERROR,
        f"{display} not in manifest (registered suffixes: {registered})",
        "register it first: culture agents register <directory-with-culture.yaml>",
    )


def _cmd_install(args: argparse.Namespace) -> None:
    """Install a systemd/launchd/scheduled-task unit for one agent."""
    from culture_core.persistence import install_service

    config = load_config_or_default(args.config)
    suffix = _resolve_manifest_suffix(config, args.nick)
    full_nick = f"{config.server.name}-{suffix}"

    culture_cmd = [sys.executable, "-m", "culture_core"]
    # No --config: defer to `culture agents start`'s argparse default
    # (~/.culture/server.yaml). Pinning a per-workdir path here would
    # re-crashloop deployments — see PR #344 and the regression test
    # tests/test_setup_update_cli.py::test_install_mesh_services_omits_legacy_config_path.
    agent_cmd = [*culture_cmd, "agents", "start", full_nick, "--foreground"]
    svc = f"culture-agent-{full_nick}"
    # After=/Wants= the server unit so a reboot brings the mesh up
    # server-first (durable mesh, docs/durable-mesh.md). The server name
    # resolves from the same config the agent's nick resolves from.
    path = install_service(
        svc,
        agent_cmd,
        f"culture-core agents {full_nick}",
        after=f"culture-server-{config.server.name}.service",
        allow_dev_interpreter=getattr(args, "allow_dev_interpreter", False),
    )
    print(f"Installed {svc} → {path}")


def _cmd_uninstall(args: argparse.Namespace) -> None:
    """Remove a per-agent service unit. Graceful no-op if not installed."""
    from culture_core.persistence import uninstall_service

    config = load_config_or_default(args.config)
    suffix = _resolve_manifest_suffix(config, args.nick)
    full_nick = f"{config.server.name}-{suffix}"

    svc = f"culture-agent-{full_nick}"
    uninstall_service(svc)
    print(f"Uninstalled {svc}")


# -----------------------------------------------------------------------
# Migrate
# -----------------------------------------------------------------------


def _cmd_migrate(args: argparse.Namespace) -> None:
    """Migrate from agents.yaml to server.yaml + per-directory culture.yaml."""
    from culture_core.config import _is_legacy_format, migrate_legacy_to_manifest

    legacy_path = Path(args.config)
    if not legacy_path.exists():
        raise CultureError(
            EXIT_USER_ERROR,
            f"No agents.yaml found at {legacy_path}",
            "nothing to migrate — pass the legacy file with --config if it lives elsewhere",
        )

    if not _is_legacy_format(legacy_path):
        raise CultureError(
            EXIT_USER_ERROR,
            f"{legacy_path} is already in manifest format",
            "no migration needed — run 'culture agents status' to see your agents",
        )

    config = migrate_legacy_to_manifest(legacy_path)

    agent_count = len(config.manifest)
    dir_count = len(set(config.manifest.values()))
    print(f"\nMigration complete: {agent_count} agent(s) across {dir_count} directory(ies)")
    print(f"\nAgent commands now use {legacy_path} by default.")
    print("Verify with: culture agents status")
