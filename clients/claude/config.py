from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ServerConnConfig:
    """IRC server connection settings."""
    host: str = "localhost"
    port: int = 6667


@dataclass
class SupervisorConfig:
    """Supervisor sub-agent settings."""
    model: str = "claude-sonnet-4-6"
    thinking: str = "medium"
    window_size: int = 20
    eval_interval: int = 5
    escalation_threshold: int = 3


@dataclass
class WebhookConfig:
    """Webhook alerting settings."""
    url: str | None = None
    irc_channel: str = "#alerts"
    events: list[str] = field(default_factory=lambda: [
        "agent_spiraling", "agent_error", "agent_question",
        "agent_timeout", "agent_complete",
    ])


@dataclass
class AgentConfig:
    """Per-agent settings."""
    nick: str = ""
    directory: str = "."
    channels: list[str] = field(default_factory=lambda: ["#general"])
    model: str = "claude-opus-4-6"
    thinking: str = "medium"


@dataclass
class DaemonConfig:
    """Top-level daemon configuration."""
    server: ServerConnConfig = field(default_factory=ServerConnConfig)
    supervisor: SupervisorConfig = field(default_factory=SupervisorConfig)
    webhooks: WebhookConfig = field(default_factory=WebhookConfig)
    buffer_size: int = 500
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
    for agent_raw in raw.get("agents", []):
        agents.append(AgentConfig(**agent_raw))

    return DaemonConfig(
        server=server,
        supervisor=supervisor,
        webhooks=webhooks,
        buffer_size=raw.get("buffer_size", 500),
        agents=agents,
    )
