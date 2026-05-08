from __future__ import annotations

import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from culture.clients.codex.attention import (
    AttentionConfig,
    Band,
    BandSpec,
    default_bands,
)
from culture.clients.shared.webhook_types import WebhookConfig  # noqa: F401


# YAML representer for Band so asdict(DaemonConfig) round-trips through
# yaml.dump → yaml.safe_load. Without this, Band keys serialize as
# python/object/apply tags that SafeLoader rejects.
def _band_yaml_representer(dumper, band):  # type: ignore[no-untyped-def]
    return dumper.represent_str(band.name.lower())


yaml.SafeDumper.add_representer(Band, _band_yaml_representer)
yaml.Dumper.add_representer(Band, _band_yaml_representer)


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
    # Per-agent attention overrides; merged shallowly over daemon defaults.
    # None means "inherit fully."
    attention_overrides: dict | None = None


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
    attention: AttentionConfig = field(default_factory=AttentionConfig)

    def get_agent(self, nick: str) -> AgentConfig | None:
        for agent in self.agents:
            if agent.nick == nick:
                return agent
        return None


_BAND_NAMES = {
    "hot": Band.HOT,
    "warm": Band.WARM,
    "cool": Band.COOL,
    "idle": Band.IDLE,
}


def _parse_band_entry(name: str, raw_spec: dict) -> tuple[Band, BandSpec]:
    """Parse and validate a single band entry. Helper for _parse_bands."""
    if name not in _BAND_NAMES:
        raise ValueError(f"unknown band name: {name!r}")
    band = _BAND_NAMES[name]
    interval_s = raw_spec.get("interval_s")
    if interval_s is None or interval_s <= 0:
        raise ValueError(f"band {name}: interval_s must be > 0, got {interval_s!r}")
    if band == Band.IDLE:
        return band, BandSpec(interval_s=interval_s, hold_s=None)
    hold_s = raw_spec.get("hold_s")
    if hold_s is None or hold_s <= 0:
        raise ValueError(f"band {name}: hold_s must be > 0, got {hold_s!r}")
    return band, BandSpec(interval_s=interval_s, hold_s=hold_s)


def _parse_bands(raw_bands: dict, defaults: dict[Band, BandSpec]) -> dict[Band, BandSpec]:
    """Shallow-merge raw band dict over defaults. Validates each entry."""
    result = dict(defaults)
    for name, raw_spec in (raw_bands or {}).items():
        band, spec = _parse_band_entry(name, raw_spec)
        result[band] = spec
    return result


def _validate_attention(cfg: AttentionConfig) -> None:
    """Monotonicity, tick range. Raises ValueError on violation."""
    intervals = [cfg.bands[b].interval_s for b in (Band.HOT, Band.WARM, Band.COOL, Band.IDLE)]
    if intervals != sorted(intervals):
        raise ValueError(
            f"attention bands must be monotonic (HOT<=WARM<=COOL<=IDLE); "
            f"got intervals {intervals}"
        )
    if cfg.tick_s <= 0:
        raise ValueError(f"attention.tick_s must be > 0, got {cfg.tick_s!r}")
    if cfg.tick_s > min(intervals):
        raise ValueError(
            f"attention.tick_s ({cfg.tick_s}) must be <= "
            f"smallest band interval ({min(intervals)})"
        )


def _build_attention_config(raw: dict, legacy_poll_interval: int) -> AttentionConfig:
    raw_attention = raw.get("attention") or {}
    bands = _parse_bands(raw_attention.get("bands", {}), default_bands())
    if "attention" not in raw:
        # Legacy migration: when no ``attention`` block is configured, the
        # legacy poll_interval (default 60s if unset) drives IDLE polling so
        # operators upgrading from the previous release see no slowdown.
        # HOT/WARM/COOL also clamp to <= legacy so they never poll slower
        # than the legacy default.
        legacy = legacy_poll_interval
        for band in (Band.HOT, Band.WARM, Band.COOL):
            spec = bands[band]
            if spec.interval_s > legacy:
                bands[band] = BandSpec(interval_s=legacy, hold_s=spec.hold_s)
        bands[Band.IDLE] = BandSpec(interval_s=legacy, hold_s=None)
    cfg = AttentionConfig(
        enabled=raw_attention.get("enabled", True),
        tick_s=raw_attention.get("tick_s", 5),
        thread_window_s=raw_attention.get("thread_window_s", 1800),
        bands=bands,
    )
    _validate_attention(cfg)
    return cfg


def load_config(path: str | Path) -> DaemonConfig:
    """Load daemon config from a YAML file."""
    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    server = ServerConnConfig(**raw.get("server", {}))
    supervisor = SupervisorConfig(**raw.get("supervisor", {}))

    webhooks = WebhookConfig(**raw.get("webhooks", {}))
    telemetry = TelemetryConfig(**raw.get("telemetry", {}))

    legacy_poll_interval = raw.get("poll_interval", 60)
    attention = _build_attention_config(raw, legacy_poll_interval)

    agents = []
    known_agent_fields = {f.name for f in AgentConfig.__dataclass_fields__.values()}
    for agent_raw in raw.get("agents", []):
        # Strip unknown fields (e.g. backend-specific fields from other backends)
        # so multi-backend configs don't crash on load.
        # Accept both human-written ``attention:`` (YAML schema) and
        # round-tripped ``attention_overrides:`` (asdict() serialization);
        # prefer ``attention`` if both present.
        per_agent_attention = agent_raw.pop("attention", agent_raw.pop("attention_overrides", None))
        filtered = {k: v for k, v in agent_raw.items() if k in known_agent_fields}
        filtered.pop("attention_overrides", None)
        agents.append(AgentConfig(**filtered, attention_overrides=per_agent_attention))

    return DaemonConfig(
        server=server,
        supervisor=supervisor,
        webhooks=webhooks,
        telemetry=telemetry,
        buffer_size=raw.get("buffer_size", 500),
        poll_interval=legacy_poll_interval,
        sleep_start=raw.get("sleep_start", "23:00"),
        sleep_end=raw.get("sleep_end", "08:00"),
        agents=agents,
        attention=attention,
    )


def resolve_attention_config(daemon_cfg: DaemonConfig, agent_cfg: AgentConfig) -> AttentionConfig:
    """Merge per-agent attention overrides over daemon defaults."""
    if not agent_cfg.attention_overrides:
        return daemon_cfg.attention
    raw = agent_cfg.attention_overrides
    bands = _parse_bands(raw.get("bands", {}), daemon_cfg.attention.bands)
    merged = AttentionConfig(
        enabled=raw.get("enabled", daemon_cfg.attention.enabled),
        tick_s=raw.get("tick_s", daemon_cfg.attention.tick_s),
        thread_window_s=raw.get("thread_window_s", daemon_cfg.attention.thread_window_s),
        bands=bands,
    )
    _validate_attention(merged)
    return merged


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
