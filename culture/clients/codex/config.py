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


@dataclass
class SupervisorConfig:
    """Supervisor sub-agent settings."""

    model: str = "gpt-5.4"
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
    agent: str = "codex"
    directory: str = "."
    channels: list[str] = field(default_factory=lambda: ["#general"])
    model: str = "gpt-5.4"
    system_prompt: str = ""
    tags: list[str] = field(default_factory=list)
    icon: str | None = None


@dataclass
class TelemetryConfig:
    """OpenTelemetry settings for the agent harness.

    ``enabled: false`` by default so freshly installed harnesses don't
    try to connect to a non-existent OTLP collector. Flip to ``true``
    once your collector is running.
    """

    enabled: bool = False
    service_name: str = "culture.harness.codex"
    otlp_endpoint: str = "http://localhost:4317"
    otlp_protocol: str = "grpc"  # grpc | http/protobuf (only grpc supported initially)
    otlp_timeout_ms: int = 5000
    otlp_compression: str = "gzip"
    traces_enabled: bool = True
    traces_sampler: str = "parentbased_always_on"
    metrics_enabled: bool = True
    metrics_export_interval_ms: int = 10000


@dataclass
class DaemonConfig:
    """Top-level daemon configuration."""

    server: ServerConnConfig = field(default_factory=ServerConnConfig)
    supervisor: SupervisorConfig = field(default_factory=SupervisorConfig)
    webhooks: WebhookConfig = field(default_factory=WebhookConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
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
    telemetry = TelemetryConfig(**raw.get("telemetry", {}))

    agents = []
    known_agent_fields = {f.name for f in AgentConfig.__dataclass_fields__.values()}
    for agent_raw in raw.get("agents", []):
        # Strip unknown fields (e.g. backend-specific fields from other backends)
        # so multi-backend configs don't crash on load.
        filtered = {k: v for k, v in agent_raw.items() if k in known_agent_fields}
        agents.append(AgentConfig(**filtered))

    return DaemonConfig(
        server=server,
        supervisor=supervisor,
        webhooks=webhooks,
        telemetry=telemetry,
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


def _load_raw_yaml(path: str | Path) -> dict:
    """Load raw YAML from a config file, returning empty dict if missing."""
    path = Path(path)
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _save_raw_yaml(path: str | Path, raw: dict) -> None:
    """Write raw YAML dict atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".yaml.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            yaml.dump(raw, f, default_flow_style=False, sort_keys=False)
        os.replace(tmp_path, str(path))
    except BaseException:
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
    Operates on raw YAML to preserve backend-specific fields.
    """
    raw = _load_raw_yaml(path)
    agents = raw.setdefault("agents", [])

    for existing in agents:
        if existing.get("nick") == agent.nick:
            raise ValueError(f"Agent with nick {agent.nick!r} already exists in config")

    if server_name is not None:
        raw.setdefault("server", {})["name"] = server_name

    agents.append(asdict(agent))
    _save_raw_yaml(path, raw)
    return load_config(path)


def rename_server(
    path: str | Path,
    new_name: str,
) -> tuple[str, list[tuple[str, str]]]:
    """Rename the server and update all agent nick prefixes.

    Returns (old_name, [(old_nick, new_nick), ...]).
    Operates on raw YAML to preserve backend-specific fields.
    """
    raw = _load_raw_yaml(path)
    server = raw.get("server", {})
    old_name = server.get("name", ServerConnConfig().name)

    if old_name == new_name:
        return old_name, []

    agents = raw.get("agents", [])
    prefix = f"{old_name}-"
    plan: list[tuple[int, str, str]] = []
    for i, agent_raw in enumerate(agents):
        nick = agent_raw.get("nick", "")
        if nick.startswith(prefix):
            new_nick = f"{new_name}-{nick[len(prefix):]}"
            plan.append((i, nick, new_nick))

    planned_nicks = {new_nick for _, _, new_nick in plan}
    existing_nicks = {a.get("nick", "") for a in agents} - {old for _, old, _ in plan}
    collisions = planned_nicks & existing_nicks
    if collisions:
        raise ValueError(
            f"renaming server {old_name!r} to {new_name!r} would create "
            f"duplicate nick(s): {', '.join(sorted(collisions))}"
        )

    server["name"] = new_name
    raw["server"] = server

    renamed: list[tuple[str, str]] = []
    for i, old_nick, new_nick in plan:
        agents[i]["nick"] = new_nick
        renamed.append((old_nick, new_nick))

    _save_raw_yaml(path, raw)
    return old_name, renamed


def rename_agent(
    path: str | Path,
    old_nick: str,
    new_nick: str,
) -> None:
    """Rename an agent's nick in the config.

    Raises ValueError if old_nick is not found or new_nick already exists.
    Operates on raw YAML to preserve backend-specific fields.
    """
    raw = _load_raw_yaml(path)
    agents = raw.get("agents", [])

    for agent_raw in agents:
        if agent_raw.get("nick") == new_nick:
            raise ValueError(f"Agent with nick {new_nick!r} already exists in config")

    for agent_raw in agents:
        if agent_raw.get("nick") == old_nick:
            agent_raw["nick"] = new_nick
            _save_raw_yaml(path, raw)
            return

    raise ValueError(f"Agent {old_nick!r} not found in config")


def remove_agent(
    path: str | Path,
    nick: str,
) -> None:
    """Remove an agent from config entirely.

    Operates on raw YAML to preserve backend-specific fields on other
    agents that the typed schema would strip.
    Raises ValueError if the agent is not found.
    """
    raw = _load_raw_yaml(path)
    if not raw:
        raise ValueError(f"Agent {nick!r} not found in config")

    agents = raw.get("agents", [])
    for i, agent_raw in enumerate(agents):
        if agent_raw.get("nick") == nick:
            agents.pop(i)
            _save_raw_yaml(path, raw)
            return
    raise ValueError(f"Agent {nick!r} not found in config")
