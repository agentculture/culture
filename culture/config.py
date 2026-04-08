"""Unified configuration for culture agents and servers.

Handles both server.yaml (machine-level config + agent manifest)
and culture.yaml (per-directory agent definitions).
"""

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


def sanitize_agent_name(dirname: str) -> str:
    """Sanitize a directory name into a valid agent/server name."""
    name = dirname.lower()
    name = re.sub(r"[^a-z0-9-]", "-", name)
    name = re.sub(r"-+", "-", name)
    name = name.strip("-")
    if not name:
        raise ValueError(f"sanitized name is empty for input: {dirname!r}")
    return name
