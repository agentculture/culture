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

from culture.agentirc.config import TelemetryConfig

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


AGENT_STATE_ACTIVE = "active"
AGENT_STATE_ARCHIVED = "archived"
AGENT_STATES = {AGENT_STATE_ACTIVE, AGENT_STATE_ARCHIVED}


@dataclass
class AgentConfig:
    """Per-agent settings loaded from culture.yaml."""

    suffix: str = ""
    backend: str = "claude"
    channels: list[str] = field(default_factory=lambda: ["#general"])
    model: str = ""
    thinking: str = "high"
    system_prompt: str = ""
    tags: list[str] = field(default_factory=list)
    icon: str | None = None
    state: str = AGENT_STATE_ACTIVE
    archived: bool = False
    archived_at: str = ""
    archived_reason: str = ""
    # Free-text role declaration — how the orchestrator tracks who-does-what
    # in a channel with many agents. Examples: "qa-runner", "stack-dev",
    # "prd-author", "ori-qa", "plenty-dev". Surfaced on every agent card
    # in the dashboard. Set at spawn (``culture boss spawn --role "..."``)
    # or editable directly in culture.yaml. Per docs/task-model.md.
    role: str = ""
    extras: dict = field(default_factory=dict)

    # Computed at load time, not stored in YAML
    nick: str = ""
    directory: str = "."

    def __post_init__(self):
        """Sync state and archived for backward compatibility."""
        if self.archived and self.state == AGENT_STATE_ACTIVE:
            self.state = AGENT_STATE_ARCHIVED
        elif self.state == AGENT_STATE_ARCHIVED:
            self.archived = True

    @property
    def agent(self) -> str:
        """Backward compatibility alias for backend."""
        return self.backend

    @property
    def acp_command(self) -> list[str]:
        """ACP-specific: command to spawn the ACP process."""
        return self.extras.get("acp_command", ["opencode", "acp"])

    @property
    def boss(self) -> str:
        """Nick of the boss agent that owns this worker (empty if unmanaged).

        Set by ``culture boss spawn`` into the worker's culture.yaml; the Claude
        daemon DMs this nick on permission requests. Lives in ``extras`` like
        other backend-specific fields.
        """
        return self.extras.get("boss", "")

    @property
    def context_watch(self) -> dict:
        """Context-watermark handoff settings (Claude only), or {} for defaults.

        A mapping ``{enabled, high_water, low_water}`` from culture.yaml; the
        Claude daemon normalizes it. Lives in ``extras``.
        """
        cw = self.extras.get("context_watch", {})
        return cw if isinstance(cw, dict) else {}


@dataclass
class ServerConfig:
    """Server configuration from server.yaml."""

    server: ServerConnConfig = field(default_factory=ServerConnConfig)
    supervisor: SupervisorConfig = field(default_factory=SupervisorConfig)
    webhooks: WebhookConfig = field(default_factory=WebhookConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
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

    manifest = raw.get("agents") or {}
    if not isinstance(manifest, dict):
        manifest = {}

    return ServerConfig(
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


def resolve_agents(config: ServerConfig) -> None:
    """Resolve agent configs from manifest paths.

    v8.19.23: collapsed the per-missing-entry WARNING into ONE summary
    line. Previously, every CLI invocation flooded with 10+ "culture.yaml
    missing for <nick> at <path> — skipping" lines from stale worktree
    refs in the manifest — actively obscuring real output for a fresh
    orchestrator session. The per-entry detail is now DEBUG-level; the
    summary tells the operator how many entries point at missing paths
    and how to clean them up.
    """
    config.agents = []
    server_name = config.server.name

    missing_nicks: list[str] = []
    errored_nicks: list[str] = []

    for suffix, directory in config.manifest.items():
        try:
            agents = load_culture_yaml(directory, suffix=suffix)
        except FileNotFoundError:
            nick = f"{server_name}-{suffix}"
            missing_nicks.append(nick)
            logger.debug(
                "culture.yaml missing for %s at %s — skipping",
                nick,
                directory,
            )
            continue
        except ValueError as e:
            nick = f"{server_name}-{suffix}"
            errored_nicks.append(nick)
            logger.debug(
                "Error loading %s from %s: %s — skipping",
                nick,
                directory,
                e,
            )
            continue

        for agent in agents:
            agent.nick = f"{server_name}-{agent.suffix}"
            config.agents.append(agent)

    if missing_nicks:
        logger.warning(
            "%d manifest entries point at missing paths (%s%s). "
            "Run `culture agent unregister <nick>` to clean each up.",
            len(missing_nicks),
            ", ".join(missing_nicks[:3]),
            "..." if len(missing_nicks) > 3 else "",
        )
    if errored_nicks:
        logger.warning(
            "%d manifest entries failed to load (%s%s). " "Re-run with --verbose for details.",
            len(errored_nicks),
            ", ".join(errored_nicks[:3]),
            "..." if len(errored_nicks) > 3 else "",
        )


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
                "model": agent_raw.get("model", ""),
                "thinking": agent_raw.get("thinking", "high"),
                "system_prompt": agent_raw.get("system_prompt", ""),
                "tags": agent_raw.get("tags", []),
                "icon": agent_raw.get("icon"),
                "state": agent_raw.get("state", AGENT_STATE_ACTIVE),
                "archived": agent_raw.get("archived", False),
                "archived_at": agent_raw.get("archived_at", ""),
                "archived_reason": agent_raw.get("archived_reason", ""),
                "role": agent_raw.get("role", ""),
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
    if agent.state != AGENT_STATE_ACTIVE:
        data["state"] = agent.state
    if agent.archived:
        data["archived"] = agent.archived
        data["archived_at"] = agent.archived_at
        data["archived_reason"] = agent.archived_reason
    if agent.role:
        data["role"] = agent.role
    data.update(agent.extras)
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
    """Archive an agent: set state=archived (also flips legacy ``archived`` flag).

    Delegates to ``set_agent_state`` so the new ``state`` field and the legacy
    ``archived`` bool stay in lockstep — closes the audit finding that
    archive_manifest_agent set ``archived=True`` but left ``state=active``,
    causing in-memory inconsistency until reload triggered ``__post_init__``.
    """
    set_agent_state(config_path, nick, AGENT_STATE_ARCHIVED, reason=reason)


def unarchive_manifest_agent(config_path: str | Path, nick: str) -> None:
    """Restore an archived agent: set state=active (also clears legacy flag).

    Delegates to ``set_agent_state`` for the same lockstep reason as
    ``archive_manifest_agent``. Refuses if the agent is not currently
    archived (per the original semantics).
    """
    suffix, directory = _nick_to_suffix(config_path, nick)
    agents = load_culture_yaml(directory)
    for agent in agents:
        if agent.suffix == suffix:
            if agent.state != AGENT_STATE_ARCHIVED and not agent.archived:
                raise ValueError(f"Agent {nick!r} is not archived")
            break
    set_agent_state(config_path, nick, AGENT_STATE_ACTIVE)


def set_agent_state(config_path: str | Path, nick: str, new_state: str, reason: str = "") -> None:
    """Set an agent's lifecycle state in its culture.yaml.

    Valid transitions:
        active   -> archived (stop daemon, mark historical)
        archived -> active   (restore, ready to start)

    The legacy ``archived`` / ``archived_at`` / ``archived_reason`` fields
    are kept in lockstep with ``state`` so callers reading either source
    of truth see the same answer.
    """
    import time as _time

    if new_state not in AGENT_STATES:
        raise ValueError(f"Invalid state {new_state!r}; must be one of {AGENT_STATES}")
    suffix, directory = _nick_to_suffix(config_path, nick)
    agents = load_culture_yaml(directory)
    found = False
    for agent in agents:
        if agent.suffix == suffix:
            if agent.state == new_state:
                return  # no-op
            agent.state = new_state
            if new_state == AGENT_STATE_ARCHIVED:
                agent.archived = True
                agent.archived_at = _time.strftime("%Y-%m-%d")
                agent.archived_reason = reason
            else:
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


def rename_worker_boss_prefix(
    config_path: str | Path, old_prefix: str, new_prefix: str
) -> list[tuple[str, str, str]]:
    """Rewrite the ``boss:`` field in every worker's ``culture.yaml``
    where the current value starts with ``<old_prefix>-``.

    v9.1.6: BUG 2 — workers stored ``boss: local-foo`` (i.e. with the
    server-prefix-at-spawn-time baked in) and ``rename_manifest_server``
    only updated ``server.yaml::server.name`` — leaving every worker
    with a stale prefix. After a server rename (or in-place
    ``server.name`` edit) the ownership check
    ``caller == worker.boss`` would compare ``new-foo`` against
    stored ``old-foo`` and reject everything as "owned by another
    boss".

    The rewrite is safe across the AD-2 multi-project model: a worker
    whose stored ``boss`` happens to start with a DIFFERENT prefix
    (e.g. ``fork-rearch-qa-w1`` with ``boss: fork-rearch-qa``, when
    we're migrating ``local`` → ``plenty``) is not touched. Match is
    exact-prefix-plus-hyphen so ``local`` does not match ``local2``.

    Args:
        config_path: server.yaml path.
        old_prefix: bare prefix without trailing hyphen (e.g. ``"local"``).
        new_prefix: bare new prefix without trailing hyphen
            (e.g. ``"plenty"``).

    Returns:
        List of ``(directory, old_boss, new_boss)`` tuples — one per
        rewritten ``boss:`` field. Empty list when no rewrites needed
        (idempotent).
    """
    if old_prefix == new_prefix:
        return []

    config = load_server_config(config_path)
    old_match = f"{old_prefix}-"
    new_match_root = f"{new_prefix}-"

    rewrites: list[tuple[str, str, str]] = []
    # Each manifest directory may host more than one AgentConfig (legacy
    # multi-agent culture.yaml layout); load all + rewrite + save all.
    seen_dirs: set[str] = set()
    for _suffix, directory in config.manifest.items():
        if directory in seen_dirs:
            continue
        seen_dirs.add(directory)
        try:
            agents = load_culture_yaml(directory)
        except (FileNotFoundError, ValueError):
            continue
        changed = False
        for agent in agents:
            boss = agent.extras.get("boss", "") or ""
            if boss.startswith(old_match):
                # Replace the prefix BEFORE the first hyphen only —
                # leave any further hyphens in the agent half intact
                # (e.g. ``local-st4ck-boss`` → ``plenty-st4ck-boss``).
                new_boss = new_match_root + boss[len(old_match) :]
                agent.extras["boss"] = new_boss
                rewrites.append((directory, boss, new_boss))
                changed = True
        if changed:
            save_culture_yaml(directory, agents)
    return rewrites


def rename_manifest_server(
    config_path: str | Path, new_name: str
) -> tuple[str, list[tuple[str, str]]]:
    """Rename the server.

    Returns (old_name, [(old_nick, new_nick), ...]) — informational.

    v9.1.6: also rewrites the ``boss:`` field on every worker whose
    stored prefix matched the OLD server name. Pre-9.1.6 this step was
    missing — the manifest's ``server.name`` got updated, but
    individual worker ``culture.yaml`` files kept their stale
    ``boss: <old>-xxx`` strings, breaking every subsequent ownership
    check ("owned by another boss" errors). See ``rename_worker_boss_prefix``.
    """
    config = load_server_config(config_path)
    old_name = config.server.name

    if old_name == new_name:
        return old_name, []

    # v9.1.6 r2 (Qodo PR #58 #5) — migrate the worker culture.yaml
    # ``boss:`` fields BEFORE rewriting server.yaml. If the worker
    # migration raises mid-flight, server.yaml is still intact and
    # the operator can fix the underlying cause and retry. v9.1.6 r1
    # had the order reversed — server.yaml got saved first, then if
    # the worker migration crashed, the system was left with
    # ``server.name = new`` while every worker still had
    # ``boss: <old>-foo``, reintroducing the original BUG 2 state.
    #
    # Residual atomicity window: if the worker migration succeeds
    # but ``save_server_config`` then fails, workers have boss:
    # rewritten to the NEW prefix while server.yaml still records
    # the OLD name. Recovery path: ``culture server migrate-prefix
    # <new> <old>`` to roll the worker side back. The window is
    # narrow because ``save_server_config`` is atomic via
    # tmpfile+rename.
    rename_worker_boss_prefix(config_path, old_name, new_name)

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
