"""Unified configuration for culture agents and servers.

Handles both server.yaml (machine-level config + agent manifest)
and culture.yaml (per-directory agent definitions).
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger("culture")


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
    """Per-agent settings loaded from culture.yaml."""

    suffix: str = ""
    backend: str = "claude"
    channels: list[str] = field(default_factory=lambda: ["#general"])
    model: str = "claude-opus-4-6"
    thinking: str = "medium"
    system_prompt: str = ""
    tags: list[str] = field(default_factory=list)
    icon: str | None = None
    archived: bool = False
    archived_at: str = ""
    archived_reason: str = ""
    extras: dict = field(default_factory=dict)

    # Computed at load time, not stored in YAML
    nick: str = ""
    directory: str = "."

    @property
    def agent(self) -> str:
        """Backward compatibility alias for backend."""
        return self.backend

    @property
    def acp_command(self) -> list[str]:
        """ACP-specific: command to spawn the ACP process."""
        return self.extras.get("acp_command", ["opencode", "acp"])


@dataclass
class ServerConfig:
    """Server configuration from server.yaml."""

    server: ServerConnConfig = field(default_factory=ServerConnConfig)
    supervisor: SupervisorConfig = field(default_factory=SupervisorConfig)
    webhooks: WebhookConfig = field(default_factory=WebhookConfig)
    buffer_size: int = 500
    poll_interval: int = 60
    sleep_start: str = "23:00"
    sleep_end: str = "08:00"
    manifest: dict[str, str] = field(default_factory=dict)
    agents: list[AgentConfig] = field(default_factory=list)

    def get_agent(self, nick: str) -> AgentConfig | None:
        for agent in self.agents:
            if agent.nick == nick:
                return agent
        return None


# Backward compatibility alias
DaemonConfig = ServerConfig


CULTURE_YAML = "culture.yaml"

# Fields that are typed on AgentConfig (not extras)
_KNOWN_AGENT_FIELDS = {f.name for f in AgentConfig.__dataclass_fields__.values()} - {
    "nick",
    "directory",
    "extras",
}


def _parse_agent_entry(raw: dict, directory: str) -> AgentConfig:
    """Parse a single agent entry from culture.yaml."""
    known = {}
    extras = {}
    for k, v in raw.items():
        if k in _KNOWN_AGENT_FIELDS:
            known[k] = v
        else:
            extras[k] = v
    agent = AgentConfig(**known, extras=extras, directory=directory)
    return agent


def load_culture_yaml(directory: str, suffix: str | None = None) -> list[AgentConfig]:
    """Load agent definitions from a culture.yaml file.

    Args:
        directory: Path to directory containing culture.yaml.
        suffix: If provided, return only the agent matching this suffix.

    Returns:
        List of AgentConfig objects with directory set.

    Raises:
        FileNotFoundError: If culture.yaml doesn't exist.
        ValueError: If suffix is specified but not found.
    """
    path = Path(directory) / CULTURE_YAML
    if not path.exists():
        raise FileNotFoundError(f"No culture.yaml found at {path}")

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    directory = str(Path(directory).resolve())

    # Multi-agent format: top-level "agents" list
    if "agents" in raw and isinstance(raw["agents"], list):
        agents = [_parse_agent_entry(entry, directory) for entry in raw["agents"]]
    else:
        # Single-agent format: top-level fields
        agents = [_parse_agent_entry(raw, directory)]

    if suffix is not None:
        filtered = [a for a in agents if a.suffix == suffix]
        if not filtered:
            raise ValueError(f"Agent with suffix {suffix!r} not found in {path}")
        return filtered

    return agents


def sanitize_agent_name(dirname: str) -> str:
    """Sanitize a directory name into a valid agent/server name."""
    name = dirname.lower()
    name = re.sub(r"[^a-z0-9-]", "-", name)
    name = re.sub(r"-+", "-", name)
    name = name.strip("-")
    if not name:
        raise ValueError(f"sanitized name is empty for input: {dirname!r}")
    return name


def load_server_config(path: str | Path) -> ServerConfig:
    """Load server configuration from server.yaml."""
    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    server = ServerConnConfig(**raw.get("server", {}))
    supervisor = SupervisorConfig(**raw.get("supervisor", {}))
    webhooks = WebhookConfig(**raw.get("webhooks", {}))

    manifest = raw.get("agents", {})
    if isinstance(manifest, list):
        manifest = {}

    return ServerConfig(
        server=server,
        supervisor=supervisor,
        webhooks=webhooks,
        buffer_size=raw.get("buffer_size", 500),
        poll_interval=raw.get("poll_interval", 60),
        sleep_start=raw.get("sleep_start", "23:00"),
        sleep_end=raw.get("sleep_end", "08:00"),
        manifest=manifest,
    )


def resolve_agents(config: ServerConfig) -> None:
    """Resolve agent configs from manifest paths."""
    config.agents = []
    server_name = config.server.name

    for suffix, directory in config.manifest.items():
        try:
            agents = load_culture_yaml(directory, suffix=suffix)
        except FileNotFoundError:
            logger.warning(
                "culture.yaml missing for %s-%s at %s — skipping",
                server_name,
                suffix,
                directory,
            )
            continue
        except ValueError as e:
            logger.warning(
                "Error loading %s-%s from %s: %s — skipping",
                server_name,
                suffix,
                directory,
                e,
            )
            continue

        for agent in agents:
            agent.nick = f"{server_name}-{agent.suffix}"
            config.agents.append(agent)


def _load_legacy_config(path: str | Path) -> ServerConfig:
    """Load legacy agents.yaml format into ServerConfig."""
    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    server = ServerConnConfig(**raw.get("server", {}))
    supervisor = SupervisorConfig(**raw.get("supervisor", {}))
    webhooks = WebhookConfig(**raw.get("webhooks", {}))

    agents = []
    known = _KNOWN_AGENT_FIELDS | {"nick", "directory"}
    for agent_raw in raw.get("agents", []):
        known_fields = {}
        extras = {}
        for k, v in agent_raw.items():
            if k == "agent":
                # Legacy field name -> new field name
                known_fields["backend"] = v
            elif k in known:
                known_fields[k] = v
            else:
                extras[k] = v
        agents.append(AgentConfig(**known_fields, extras=extras))

    return ServerConfig(
        server=server,
        supervisor=supervisor,
        webhooks=webhooks,
        buffer_size=raw.get("buffer_size", 500),
        poll_interval=raw.get("poll_interval", 60),
        sleep_start=raw.get("sleep_start", "23:00"),
        sleep_end=raw.get("sleep_end", "08:00"),
        agents=agents,
    )


def load_config(path: str | Path) -> ServerConfig:
    """Load config, auto-detecting format (server.yaml vs legacy agents.yaml)."""
    path = Path(path)
    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    agents_val = raw.get("agents")
    is_legacy = (
        isinstance(agents_val, list)
        and agents_val
        and isinstance(agents_val[0], dict)
        and "nick" in agents_val[0]
    )

    if is_legacy:
        return _load_legacy_config(path)

    config = load_server_config(path)
    resolve_agents(config)
    return config


def load_config_or_default(path: str | Path) -> ServerConfig:
    """Load config from path, returning default ServerConfig if missing."""
    path = Path(path)
    if not path.exists():
        return ServerConfig()
    return load_config(path)


def save_server_config(path: str | Path, config: ServerConfig) -> None:
    """Write server.yaml atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "server": asdict(config.server),
        "supervisor": asdict(config.supervisor),
        "webhooks": asdict(config.webhooks),
        "buffer_size": config.buffer_size,
        "poll_interval": config.poll_interval,
        "sleep_start": config.sleep_start,
        "sleep_end": config.sleep_end,
        "agents": config.manifest,
    }

    fd, tmp_path_str = tempfile.mkstemp(dir=str(path.parent), suffix=".yaml.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        os.replace(tmp_path_str, str(path))
    except BaseException:
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise
