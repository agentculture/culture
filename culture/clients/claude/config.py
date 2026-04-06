from __future__ import annotations

import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml


@dataclass
class ServerConnConfig:
    """IRC server connection settings."""

    name: str = "culture"
    host: str = "localhost"
    port: int = 6667
    archived: bool = False
    archived_at: str = ""
    archived_reason: str = ""


@dataclass
class SupervisorConfig:
    """Supervisor sub-agent settings."""

    model: str = "claude-sonnet-4-6"
    thinking: str = "medium"
    window_size: int = 20
    eval_interval: int = 5
    escalation_threshold: int = 3
    prompt_override: str = ""


@dataclass
class WebhookConfig:
    """Webhook alerting settings."""

    url: str | None = None
    irc_channel: str = "#alerts"
    events: list[str] = field(
        default_factory=lambda: [
            "agent_spiraling",
            "agent_error",
            "agent_question",
            "agent_timeout",
            "agent_complete",
        ]
    )


@dataclass
class AgentConfig:
    """Per-agent settings."""

    nick: str = ""
    agent: str = "claude"
    directory: str = "."
    channels: list[str] = field(default_factory=lambda: ["#general"])
    model: str = "claude-opus-4-6"
    thinking: str = "medium"
    system_prompt: str = ""
    tags: list[str] = field(default_factory=list)
    icon: str | None = None
    archived: bool = False
    archived_at: str = ""
    archived_reason: str = ""


@dataclass
class DaemonConfig:
    """Top-level daemon configuration."""

    server: ServerConnConfig = field(default_factory=ServerConnConfig)
    supervisor: SupervisorConfig = field(default_factory=SupervisorConfig)
    webhooks: WebhookConfig = field(default_factory=WebhookConfig)
    buffer_size: int = 500
    poll_interval: int = 60
    sleep_start: str = "23:00"
    sleep_end: str = "08:00"
    agents: list[AgentConfig] = field(default_factory=list)

    def get_agent(self, nick: str) -> AgentConfig | None:
        for agent in self.agents:
            if agent.nick == nick:
                return agent
        return None


def load_config(path: str | Path) -> DaemonConfig:
    """Load daemon config from a YAML file."""
    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    server = ServerConnConfig(**raw.get("server", {}))
    supervisor = SupervisorConfig(**raw.get("supervisor", {}))

    webhooks = WebhookConfig(**raw.get("webhooks", {}))

    agents = []
    known_agent_fields = {f.name for f in AgentConfig.__dataclass_fields__.values()}
    for agent_raw in raw.get("agents", []):
        # Strip unknown fields (e.g. acp_command from ACP backend configs)
        # so multi-backend configs don't crash on load.
        filtered = {k: v for k, v in agent_raw.items() if k in known_agent_fields}
        agents.append(AgentConfig(**filtered))

    return DaemonConfig(
        server=server,
        supervisor=supervisor,
        webhooks=webhooks,
        buffer_size=raw.get("buffer_size", 500),
        poll_interval=raw.get("poll_interval", 60),
        sleep_start=raw.get("sleep_start", "23:00"),
        sleep_end=raw.get("sleep_end", "08:00"),
        agents=agents,
    )


def sanitize_agent_name(dirname: str) -> str:
    """Sanitize a directory name into a valid agent/server name.

    Lowercase, replace non-alphanumeric chars with hyphens, collapse
    multiple hyphens, strip leading/trailing hyphens.
    """
    name = dirname.lower()
    name = re.sub(r"[^a-z0-9-]", "-", name)
    name = re.sub(r"-+", "-", name)
    name = name.strip("-")
    if not name:
        raise ValueError(f"sanitized name is empty for input: {dirname!r}")
    return name


def load_config_or_default(path: str | Path) -> DaemonConfig:
    """Load config from path, returning a default DaemonConfig if file is missing."""
    path = Path(path)
    if not path.exists():
        return DaemonConfig()
    return load_config(path)


def save_config(path: str | Path, config: DaemonConfig) -> None:
    """Serialize a DaemonConfig to YAML and write atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = asdict(config)
    yaml_str = yaml.dump(data, default_flow_style=False)

    # Atomic write: write to temp file in same dir, then rename
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        suffix=".yaml.tmp",
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(yaml_str)
        os.replace(tmp_path, str(path))
    except BaseException:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def add_agent_to_config(
    path: str | Path,
    agent: AgentConfig,
    server_name: str | None = None,
) -> DaemonConfig:
    """Add an agent to a config file, creating it if needed.

    If server_name is provided, updates config.server.name.
    Raises ValueError if an agent with the same nick already exists.
    """
    config = load_config_or_default(path)

    if server_name is not None:
        config.server.name = server_name

    # Check for nick collision
    for existing in config.agents:
        if existing.nick == agent.nick:
            raise ValueError(f"agent with nick {agent.nick!r} already exists in config")

    config.agents.append(agent)
    save_config(path, config)
    return config


def rename_server(
    path: str | Path,
    new_name: str,
) -> tuple[str, list[tuple[str, str]]]:
    """Rename the server and update all agent nick prefixes.

    Returns (old_name, [(old_nick, new_nick), ...]).
    """
    config = load_config_or_default(path)
    old_name = config.server.name

    if old_name == new_name:
        return old_name, []

    # Plan renames and check for collisions before mutating
    prefix = f"{old_name}-"
    plan: list[tuple[int, str, str]] = []
    for i, agent in enumerate(config.agents):
        if agent.nick.startswith(prefix):
            new_nick = f"{new_name}-{agent.nick[len(prefix):]}"
            plan.append((i, agent.nick, new_nick))

    planned_nicks = {new_nick for _, _, new_nick in plan}
    existing_nicks = {a.nick for a in config.agents} - {old for _, old, _ in plan}
    collisions = planned_nicks & existing_nicks
    if collisions:
        raise ValueError(
            f"renaming server {old_name!r} to {new_name!r} would create "
            f"duplicate nick(s): {', '.join(sorted(collisions))}"
        )

    config.server.name = new_name

    renamed: list[tuple[str, str]] = []
    for i, old_nick, new_nick in plan:
        config.agents[i].nick = new_nick
        renamed.append((old_nick, new_nick))

    save_config(path, config)
    return old_name, renamed


def rename_agent(
    path: str | Path,
    old_nick: str,
    new_nick: str,
) -> None:
    """Rename an agent's nick in the config.

    Raises ValueError if old_nick is not found or new_nick already exists.
    """
    config = load_config_or_default(path)

    # Check new nick doesn't collide
    for agent in config.agents:
        if agent.nick == new_nick:
            raise ValueError(f"agent with nick {new_nick!r} already exists in config")

    # Find and rename
    for agent in config.agents:
        if agent.nick == old_nick:
            agent.nick = new_nick
            save_config(path, config)
            return

    raise ValueError(f"agent {old_nick!r} not found in config")


def archive_agent(
    path: str | Path,
    nick: str,
    reason: str = "",
) -> None:
    """Set the archived flag on an agent.

    Raises ValueError if the agent is not found.
    """
    import time

    config = load_config_or_default(path)
    for agent in config.agents:
        if agent.nick == nick:
            agent.archived = True
            agent.archived_at = time.strftime("%Y-%m-%d")
            agent.archived_reason = reason
            save_config(path, config)
            return
    raise ValueError(f"agent {nick!r} not found in config")


def unarchive_agent(
    path: str | Path,
    nick: str,
) -> None:
    """Clear the archived flag on an agent.

    Raises ValueError if not found or not currently archived.
    """
    config = load_config_or_default(path)
    for agent in config.agents:
        if agent.nick == nick:
            if not agent.archived:
                raise ValueError(f"agent {nick!r} is not archived")
            agent.archived = False
            agent.archived_at = ""
            agent.archived_reason = ""
            save_config(path, config)
            return
    raise ValueError(f"agent {nick!r} not found in config")


def archive_server(
    path: str | Path,
    reason: str = "",
) -> list[str]:
    """Set the archived flag on the server and all its agents.

    Returns a list of archived agent nicks.
    """
    import time

    config = load_config_or_default(path)
    today = time.strftime("%Y-%m-%d")

    config.server.archived = True
    config.server.archived_at = today
    config.server.archived_reason = reason

    archived_nicks = []
    for agent in config.agents:
        agent.archived = True
        agent.archived_at = today
        agent.archived_reason = reason
        archived_nicks.append(agent.nick)

    save_config(path, config)
    return archived_nicks


def unarchive_server(
    path: str | Path,
) -> list[str]:
    """Clear the archived flag on the server and all its agents.

    Returns a list of unarchived agent nicks.
    """
    config = load_config_or_default(path)

    config.server.archived = False
    config.server.archived_at = ""
    config.server.archived_reason = ""

    unarchived_nicks = []
    for agent in config.agents:
        if agent.archived:
            agent.archived = False
            agent.archived_at = ""
            agent.archived_reason = ""
            unarchived_nicks.append(agent.nick)

    save_config(path, config)
    return unarchived_nicks
