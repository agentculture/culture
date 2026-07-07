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
from agentirc.config import TelemetryConfig

from culture_core._constants import DEFAULT_TURN_TIMEOUT_SECONDS

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
class PresenceConfig:
    """Mesh-wide presence policy from the ``presence`` section of server.yaml.

    ``heartbeat_interval_seconds`` is how often a busy resident refreshes its
    PRESENCE signal; ``stale_after_seconds`` (stale-T) is how long the server
    waits without a heartbeat before flagging a busy resident presumed-hung.
    ``stale_after_seconds`` must be strictly greater than
    ``heartbeat_interval_seconds`` so a live resident heartbeating on schedule
    is never flagged between beats. The defaults (30/90) are open tuning
    values (plan risk r1) — expect them to move once the mesh gathers real
    heartbeat data. See protocol/extensions/presence.md and
    docs/resident-presence.md.
    """

    heartbeat_interval_seconds: int = 30
    stale_after_seconds: int = 90


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
    # Outer safety-net timeout for one SDK turn. 0 disables.
    # Backends may have their own SDK-tuned inner timeouts; this wraps
    # them so a wedged stream cannot block _run_loop indefinitely.
    # Default lives in culture/_constants.py for cross-backend reuse.
    turn_timeout_seconds: float = DEFAULT_TURN_TIMEOUT_SECONDS
    # Tokens per UTC day this agent is expected to stay under. WARN-ONLY:
    # a breach only sets a warning flag in the resource view — nothing in
    # v1 enforces, blocks, or defers work on it (docs/resident-presence.md).
    # None disables budget warnings entirely.
    token_budget: int | None = None
    # Percent of token_budget at which the resource view starts warning.
    token_budget_warn_pct: int = 80
    extras: dict = field(default_factory=dict)
    # Per-agent attention overrides, merged shallowly over daemon defaults by the
    # backend daemons (cultureagent's resolve_attention_config). Falsy => inherit
    # daemon defaults fully. The per-backend AgentConfigs already carry this; the
    # central config must too, or claude/codex/copilot daemons raise
    # "'AgentConfig' object has no attribute 'attention_overrides'". (culture-core#9)
    attention_overrides: dict | None = None

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
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    presence: PresenceConfig = field(default_factory=PresenceConfig)
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
_YAML_TMP_SUFFIX = ".yaml.tmp"

# Fields that are typed on AgentConfig (not extras)
_KNOWN_AGENT_FIELDS = {f.name for f in AgentConfig.__dataclass_fields__.values()} - {
    "nick",
    "directory",
    "extras",
}


def _new_config_error(message: str, remediation: str) -> Exception:
    """Build a :class:`CultureError` for an invalid config value.

    Imported lazily: ``culture_core.cli`` imports this module at load time,
    so a module-level import here would be circular.
    """
    from culture_core.cli._errors import EXIT_USER_ERROR, CultureError

    return CultureError(EXIT_USER_ERROR, message, remediation)


def _is_plain_int(value: object) -> bool:
    """True only for real ints (bool is an int subclass — reject it)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_agent_budget(agent: AgentConfig, source: str) -> None:
    """Sanitize the warn-only token-budget fields parsed from culture.yaml.

    Budget fields are warn-only observability config
    (docs/resident-presence.md): an invalid value must never stop an agent
    from loading. Raising here would drop the agent from the manifest in
    :func:`resolve_agents` and escape the doctor's per-entry catch tuples
    (``culture_core/doctor/checks.py`` / ``discovery.py``), aborting a whole
    doctor run over a budget typo. Instead: warn — naming the file, the
    offending key/value, and the valid range — and reset the field to its
    default, so only the budget warnings degrade, never the agent.
    """
    defaults = AgentConfig()
    budget = agent.token_budget
    if budget is not None and not (_is_plain_int(budget) and budget >= 1):
        logger.warning(
            "Invalid token_budget in %s: %r — must be a positive integer "
            "(tokens per UTC day); ignoring it (budget warnings disabled "
            "for this agent)",
            source,
            budget,
        )
        agent.token_budget = defaults.token_budget
    pct = agent.token_budget_warn_pct
    if not (_is_plain_int(pct) and 1 <= pct <= 100):
        logger.warning(
            "Invalid token_budget_warn_pct in %s: %r — must be an integer "
            "between 1 and 100; using the default (%d)",
            source,
            pct,
            defaults.token_budget_warn_pct,
        )
        agent.token_budget_warn_pct = defaults.token_budget_warn_pct


def _parse_presence_section(raw: dict, source: str) -> PresenceConfig:
    """Parse and validate the optional ``presence`` section of server.yaml.

    Mesh policy fails fast by design: unlike the warn-only agent budget
    fields, an invalid presence section raises a :class:`CultureError`.
    Only a missing key (or an explicit YAML null) means "use defaults" —
    a falsy non-mapping (``presence: false`` / ``[]`` / ``0``) is rejected
    like any other non-mapping instead of silently coercing to defaults.
    """
    section = raw.get("presence")
    if section is None:
        return PresenceConfig()
    if not isinstance(section, dict):
        raise _new_config_error(
            f"Invalid presence section in {source}: {section!r} — must be a mapping",
            "edit server.yaml: presence takes nested keys "
            "heartbeat_interval_seconds and stale_after_seconds",
        )
    try:
        presence = PresenceConfig(**section)
    except TypeError:
        known = ", ".join(f.name for f in PresenceConfig.__dataclass_fields__.values())
        # map(str, ...): YAML permits non-string keys (an unquoted `30:`
        # parses as an int) and sorting mixed-type keys raises TypeError —
        # stringify first so the structured error always builds.
        raise _new_config_error(
            f"Unknown key in presence section of {source}: "
            f"{sorted(map(str, section))!r} — known keys are {known}",
            "edit server.yaml and remove or rename the unknown presence key",
        ) from None
    for key in ("heartbeat_interval_seconds", "stale_after_seconds"):
        value = getattr(presence, key)
        if not (_is_plain_int(value) and value >= 1):
            raise _new_config_error(
                f"Invalid presence.{key} in {source}: {value!r} — "
                "must be a positive integer (seconds)",
                f"edit server.yaml and set presence.{key} to an integer >= 1",
            )
    if presence.stale_after_seconds <= presence.heartbeat_interval_seconds:
        raise _new_config_error(
            f"Invalid presence.stale_after_seconds in {source}: "
            f"{presence.stale_after_seconds} — must be strictly greater than "
            f"heartbeat_interval_seconds ({presence.heartbeat_interval_seconds})",
            "edit server.yaml so stale_after_seconds > heartbeat_interval_seconds — "
            "otherwise a live resident would be flagged stale between heartbeats",
        )
    return presence


def _parse_agent_entry(raw: dict, directory: str) -> AgentConfig:
    """Parse a single agent entry from culture.yaml."""
    raw = dict(raw)
    # ``attention:`` is the author-facing key; ``attention_overrides:`` is the
    # round-tripped (asdict) form. Mirror cultureagent's loaders: accept both,
    # ``attention`` wins. Pop both so neither leaks into extras. (culture-core#9)
    attention_overrides = raw.pop("attention", raw.pop("attention_overrides", None))
    known = {}
    extras = {}
    for k, v in raw.items():
        if k in _KNOWN_AGENT_FIELDS:
            known[k] = v
        else:
            extras[k] = v
    agent = AgentConfig(
        **known,
        attention_overrides=attention_overrides,
        extras=extras,
        directory=directory,
    )
    _validate_agent_budget(agent, str(Path(directory) / CULTURE_YAML))
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

    for agent in agents:
        if not agent.suffix:
            raise ValueError(f"Agent entry in {path} is missing a 'suffix' field")

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
    telemetry = TelemetryConfig(**raw.get("telemetry", {}))
    presence = _parse_presence_section(raw, str(path))

    manifest = raw.get("agents") or {}
    if not isinstance(manifest, dict):
        manifest = {}

    return ServerConfig(
        server=server,
        supervisor=supervisor,
        webhooks=webhooks,
        telemetry=telemetry,
        presence=presence,
        buffer_size=raw.get("buffer_size", 500),
        poll_interval=raw.get("poll_interval", 60),
        sleep_start=raw.get("sleep_start", "23:00"),
        sleep_end=raw.get("sleep_end", "08:00"),
        manifest=manifest,
    )


# Dedup state for manifest-load warnings: a CLI invocation calls
# resolve_agents many times, but each broken (server, suffix) entry should
# emit at most one warning per process.
_warned_manifest_entries: set[tuple[str, str]] = set()


def reset_manifest_warning_state() -> None:
    """Clear the per-process manifest-warning dedup set. Tests use this."""
    _warned_manifest_entries.clear()


def _warn_manifest_entry_once(server_name: str, suffix: str, message: str, *args) -> None:
    """Log a manifest-load warning at most once per (server, suffix) per process."""
    key = (server_name, suffix)
    if key in _warned_manifest_entries:
        return
    _warned_manifest_entries.add(key)
    logger.warning(message, *args)


def resolve_agents(config: ServerConfig) -> None:
    """Resolve agent configs from manifest paths."""
    # Lazy: culture_core.cli imports this module at load time.
    from culture_core.cli._errors import CultureError

    config.agents = []
    server_name = config.server.name

    for suffix, directory in config.manifest.items():
        try:
            agents = load_culture_yaml(directory, suffix=suffix)
        except FileNotFoundError:
            _warn_manifest_entry_once(
                server_name,
                suffix,
                "culture.yaml missing for %s-%s at %s — run "
                "'culture agents unregister %s' to remove this stale manifest entry",
                server_name,
                suffix,
                directory,
                suffix,
            )
            continue
        except (ValueError, CultureError) as e:
            _warn_manifest_entry_once(
                server_name,
                suffix,
                "Error loading %s-%s from %s: %s — run "
                "'culture agents unregister %s' if this entry is stale",
                server_name,
                suffix,
                directory,
                e,
                suffix,
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
    telemetry = TelemetryConfig(**raw.get("telemetry", {}))

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
        telemetry=telemetry,
        buffer_size=raw.get("buffer_size", 500),
        poll_interval=raw.get("poll_interval", 60),
        sleep_start=raw.get("sleep_start", "23:00"),
        sleep_end=raw.get("sleep_end", "08:00"),
        agents=agents,
    )


def _is_legacy_format(path: str | Path) -> bool:
    """Check if config file uses legacy agents.yaml format (list-of-dicts)."""
    path = Path(path)
    if not path.exists():
        return False
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    agents_val = raw.get("agents")
    return (
        isinstance(agents_val, list)
        and bool(agents_val)
        and isinstance(agents_val[0], dict)
        and "nick" in agents_val[0]
    )


def migrate_legacy_to_manifest(path: str | Path) -> ServerConfig:
    """Auto-migrate legacy agents.yaml format to manifest format in place.

    Reads the legacy YAML from *path*, groups agents by directory, writes a
    ``culture.yaml`` file in each directory, converts the ``agents`` list to a
    manifest dict, and overwrites *path* with the manifest-format server
    config.  Returns the loaded ``ServerConfig``.
    """
    path = Path(path)
    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    server_name = raw.get("server", {}).get("name", "culture")
    prefix = f"{server_name}-"

    by_dir: dict[str, list[tuple[str, dict]]] = {}
    for agent_raw in raw.get("agents", []):
        nick = agent_raw.get("nick", "")
        suffix = nick.removeprefix(prefix) if nick.startswith(prefix) else nick
        directory = str(Path(agent_raw.get("directory", ".")).resolve())
        by_dir.setdefault(directory, []).append((suffix, agent_raw))

    manifest: dict[str, str] = {}
    for directory, entries in by_dir.items():
        agents: list[AgentConfig] = []
        for suffix, agent_raw in entries:
            backend = agent_raw.get("agent", "claude")
            known_fields = {
                "suffix": suffix,
                "backend": backend,
                "channels": agent_raw.get("channels", ["#general"]),
                "model": agent_raw.get("model", "claude-opus-4-6"),
                "thinking": agent_raw.get("thinking", "medium"),
                "system_prompt": agent_raw.get("system_prompt", ""),
                "tags": agent_raw.get("tags", []),
                "icon": agent_raw.get("icon"),
                "archived": agent_raw.get("archived", False),
                "archived_at": agent_raw.get("archived_at", ""),
                "archived_reason": agent_raw.get("archived_reason", ""),
            }
            skip_keys = set(known_fields.keys()) | {"nick", "directory", "agent"}
            extras = {k: v for k, v in agent_raw.items() if k not in skip_keys}
            agents.append(AgentConfig(**known_fields, extras=extras))
            manifest[suffix] = directory

        dir_path = Path(directory)
        dir_path.mkdir(parents=True, exist_ok=True)
        save_culture_yaml(directory, agents)

    server = ServerConnConfig(**raw.get("server", {}))
    supervisor = SupervisorConfig(**raw.get("supervisor", {}))
    webhooks = WebhookConfig(**raw.get("webhooks", {}))
    telemetry = TelemetryConfig(**raw.get("telemetry", {}))
    config = ServerConfig(
        server=server,
        supervisor=supervisor,
        webhooks=webhooks,
        telemetry=telemetry,
        buffer_size=raw.get("buffer_size", 500),
        poll_interval=raw.get("poll_interval", 60),
        sleep_start=raw.get("sleep_start", "23:00"),
        sleep_end=raw.get("sleep_end", "08:00"),
        manifest=manifest,
    )
    save_server_config(str(path), config)

    logger.info("Auto-migrated legacy config %s to manifest format", path)

    resolve_agents(config)
    return config


def load_config(path: str | Path) -> ServerConfig:
    """Load config, auto-detecting format (server.yaml vs legacy agents.yaml).

    Legacy format is automatically migrated to manifest format on first load.
    """
    path = Path(path)

    if _is_legacy_format(path):
        return migrate_legacy_to_manifest(path)

    config = load_server_config(path)
    resolve_agents(config)
    return config


def load_config_or_default(path: str | Path, fallback: str | Path | None = None) -> ServerConfig:
    """Load config from path, returning default ServerConfig if missing.

    If *path* does not exist and *fallback* is given, try the fallback path.
    If neither is given, check the legacy ~/.culture/agents.yaml location.
    (The CLI entry point owns the fresh-operator "no config yet" notice —
    see ``culture_core.cli._notice_first_run``; the library stays silent.)
    """
    path = Path(path)
    if path.exists():
        return load_config(path)

    # Try legacy fallback
    if fallback is None:
        fallback = Path(os.path.expanduser("~/.culture/agents.yaml"))
    else:
        fallback = Path(fallback)
    if fallback.exists():
        return load_config(fallback)

    return ServerConfig()


def _agent_to_yaml_dict(agent: AgentConfig) -> dict:
    """Convert AgentConfig to a dict suitable for YAML serialization."""
    data = {
        "suffix": agent.suffix,
        "backend": agent.backend,
    }
    defaults = AgentConfig()
    if agent.channels != defaults.channels:
        data["channels"] = agent.channels
    if agent.model != defaults.model:
        data["model"] = agent.model
    if agent.thinking != defaults.thinking:
        data["thinking"] = agent.thinking
    if agent.system_prompt:
        data["system_prompt"] = agent.system_prompt
    if agent.tags:
        data["tags"] = agent.tags
    if agent.icon is not None:
        data["icon"] = agent.icon
    if agent.archived:
        data["archived"] = agent.archived
        data["archived_at"] = agent.archived_at
        data["archived_reason"] = agent.archived_reason
    if agent.token_budget is not None:
        data["token_budget"] = agent.token_budget
    if agent.token_budget_warn_pct != defaults.token_budget_warn_pct:
        data["token_budget_warn_pct"] = agent.token_budget_warn_pct
    data.update(agent.extras)
    # Round-trip the typed attention field back to the author-facing ``attention:``
    # key (written after extras so the typed field stays authoritative). Without
    # this, _parse_agent_entry pops attention out of extras into this field, and any
    # rewrite path (archive/unarchive/rename) would silently drop it. (culture-core#9)
    if agent.attention_overrides:
        data["attention"] = agent.attention_overrides
    return data


def save_culture_yaml(directory: str, agents: list[AgentConfig]) -> None:
    """Write culture.yaml atomically. Single-agent uses flat format."""
    path = Path(directory) / CULTURE_YAML
    path.parent.mkdir(parents=True, exist_ok=True)

    if len(agents) == 1:
        data = _agent_to_yaml_dict(agents[0])
    else:
        data = {"agents": [_agent_to_yaml_dict(a) for a in agents]}

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=_YAML_TMP_SUFFIX)
    try:
        with os.fdopen(fd, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_server_raw(path: str | Path) -> dict:
    """Load raw server.yaml YAML."""
    path = Path(path)
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _save_server_raw(path: str | Path, raw: dict) -> None:
    """Write raw server.yaml atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=_YAML_TMP_SUFFIX)
    try:
        with os.fdopen(fd, "w") as f:
            yaml.dump(raw, f, default_flow_style=False, sort_keys=False)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def add_to_manifest(path: str | Path, suffix: str, directory: str) -> None:
    """Add an agent to the server.yaml manifest. Raises ValueError if suffix exists."""
    raw = _load_server_raw(path)
    agents = raw.setdefault("agents", {})
    if not isinstance(agents, dict):
        agents = {}
        raw["agents"] = agents
    if suffix in agents:
        raise ValueError(f"Agent suffix {suffix!r} already registered at {agents[suffix]}")
    agents[suffix] = str(Path(directory).resolve())
    _save_server_raw(path, raw)


def remove_from_manifest(path: str | Path, suffix: str) -> None:
    """Remove an agent from the server.yaml manifest. Raises ValueError if not found."""
    raw = _load_server_raw(path)
    agents = raw.get("agents", {})
    if not isinstance(agents, dict) or suffix not in agents:
        raise ValueError(f"Agent suffix {suffix!r} not found in manifest")
    del agents[suffix]
    _save_server_raw(path, raw)


def save_server_config(path: str | Path, config: ServerConfig) -> None:
    """Write server.yaml atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "server": asdict(config.server),
        "supervisor": asdict(config.supervisor),
        "webhooks": asdict(config.webhooks),
        "telemetry": asdict(config.telemetry),
        "presence": asdict(config.presence),
        "buffer_size": config.buffer_size,
        "poll_interval": config.poll_interval,
        "sleep_start": config.sleep_start,
        "sleep_end": config.sleep_end,
        "agents": config.manifest,
    }

    fd, tmp_path_str = tempfile.mkstemp(dir=str(path.parent), suffix=_YAML_TMP_SUFFIX)
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


# -----------------------------------------------------------------------
# Manifest-aware agent CRUD
# -----------------------------------------------------------------------


def _nick_to_suffix(config_path: str | Path, nick: str) -> tuple[str, str]:
    """Extract suffix and directory from a nick using the manifest.

    Returns (suffix, directory).  Raises ValueError if not found.
    """
    config = load_server_config(config_path)
    server_name = config.server.name
    prefix = f"{server_name}-"
    if not nick.startswith(prefix):
        raise ValueError(f"Nick {nick!r} does not match server {server_name!r}")
    suffix = nick[len(prefix) :]
    directory = config.manifest.get(suffix)
    if directory is None:
        raise ValueError(f"Agent {nick!r} not found in manifest")
    return suffix, directory


def remove_manifest_agent(config_path: str | Path, nick: str) -> None:
    """Remove an agent from the manifest by nick."""
    suffix, _directory = _nick_to_suffix(config_path, nick)
    remove_from_manifest(config_path, suffix)


def archive_manifest_agent(config_path: str | Path, nick: str, reason: str = "") -> None:
    """Archive an agent: set archived flag in its culture.yaml."""
    import time as _time

    suffix, directory = _nick_to_suffix(config_path, nick)
    agents = load_culture_yaml(directory)
    found = False
    for agent in agents:
        if agent.suffix == suffix:
            agent.archived = True
            agent.archived_at = _time.strftime("%Y-%m-%d")
            agent.archived_reason = reason
            found = True
            break
    if not found:
        raise ValueError(f"Agent {nick!r} not found in {directory}/culture.yaml")
    save_culture_yaml(directory, agents)


def unarchive_manifest_agent(config_path: str | Path, nick: str) -> None:
    """Unarchive an agent: clear archived flag in its culture.yaml."""
    suffix, directory = _nick_to_suffix(config_path, nick)
    agents = load_culture_yaml(directory)
    found = False
    for agent in agents:
        if agent.suffix == suffix:
            if not agent.archived:
                raise ValueError(f"Agent {nick!r} is not archived")
            agent.archived = False
            agent.archived_at = ""
            agent.archived_reason = ""
            found = True
            break
    if not found:
        raise ValueError(f"Agent {nick!r} not found in {directory}/culture.yaml")
    save_culture_yaml(directory, agents)


def rename_manifest_agent(config_path: str | Path, old_nick: str, new_nick: str) -> None:
    """Rename an agent: update suffix in manifest and culture.yaml."""
    config = load_server_config(config_path)
    server_name = config.server.name
    old_prefix = f"{server_name}-"
    if not old_nick.startswith(old_prefix):
        raise ValueError(f"Nick {old_nick!r} does not match server {server_name!r}")
    old_suffix = old_nick[len(old_prefix) :]

    # Strip the known server prefix to get the new suffix, handling
    # hyphenated server names correctly (e.g. "my-server-bot" → "bot").
    if new_nick.startswith(old_prefix):
        new_suffix = new_nick[len(old_prefix) :]
    elif "-" in new_nick:
        new_suffix = new_nick.split("-", 1)[1]
    else:
        new_suffix = new_nick

    directory = config.manifest.get(old_suffix)
    if directory is None:
        raise ValueError(f"Agent {old_nick!r} not found in manifest")
    if old_suffix != new_suffix and new_suffix in config.manifest:
        raise ValueError(f"Agent with suffix {new_suffix!r} already exists in manifest")

    # Update manifest atomically (single write instead of remove + add)
    if old_suffix != new_suffix:
        config.manifest = {
            (new_suffix if s == old_suffix else s): d for s, d in config.manifest.items()
        }
        save_server_config(str(config_path), config)

    # Update suffix in culture.yaml
    agents = load_culture_yaml(directory)
    for agent in agents:
        if agent.suffix == old_suffix:
            agent.suffix = new_suffix
            break
    save_culture_yaml(directory, agents)


def rename_manifest_server(
    config_path: str | Path, new_name: str
) -> tuple[str, list[tuple[str, str]]]:
    """Rename the server. Nicks are computed at load time, so only server.name changes.

    Returns (old_name, [(old_nick, new_nick), ...]) for informational purposes.
    """
    config = load_server_config(config_path)
    old_name = config.server.name

    if old_name == new_name:
        return old_name, []

    config.server.name = new_name
    save_server_config(str(config_path), config)

    renamed = [(f"{old_name}-{suffix}", f"{new_name}-{suffix}") for suffix in config.manifest]
    return old_name, renamed


def archive_manifest_server(config_path: str | Path, reason: str = "") -> list[str]:
    """Archive all agents on the server via their culture.yaml files.

    Returns list of archived nicks.
    """
    import time as _time

    config = load_server_config(config_path)
    server_name = config.server.name
    archived_nicks = []

    for suffix, directory in config.manifest.items():
        try:
            agents = load_culture_yaml(directory, suffix=suffix)
        except (FileNotFoundError, ValueError):
            continue
        for agent in agents:
            if agent.suffix == suffix and not agent.archived:
                agent.archived = True
                agent.archived_at = _time.strftime("%Y-%m-%d")
                agent.archived_reason = reason
                archived_nicks.append(f"{server_name}-{suffix}")
        # Save all agents in that directory (may include others)
        all_agents = load_culture_yaml(directory)
        for a in all_agents:
            if a.suffix == suffix and not a.archived:
                a.archived = True
                a.archived_at = _time.strftime("%Y-%m-%d")
                a.archived_reason = reason
        save_culture_yaml(directory, all_agents)

    # Also archive the server itself
    config.server.archived = True
    config.server.archived_at = _time.strftime("%Y-%m-%d")
    config.server.archived_reason = reason
    save_server_config(str(config_path), config)

    return archived_nicks


def unarchive_manifest_server(
    config_path: str | Path,
) -> list[str]:
    """Unarchive all agents on the server via their culture.yaml files.

    Returns list of unarchived nicks.
    """
    config = load_server_config(config_path)
    server_name = config.server.name
    unarchived_nicks = []

    for suffix, directory in config.manifest.items():
        try:
            all_agents = load_culture_yaml(directory)
        except (FileNotFoundError, ValueError):
            continue
        for a in all_agents:
            if a.suffix == suffix and a.archived:
                a.archived = False
                a.archived_at = ""
                a.archived_reason = ""
                unarchived_nicks.append(f"{server_name}-{suffix}")
        save_culture_yaml(directory, all_agents)

    config.server.archived = False
    config.server.archived_at = ""
    config.server.archived_reason = ""
    save_server_config(str(config_path), config)

    return unarchived_nicks
