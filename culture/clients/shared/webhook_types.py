"""Shared types for webhook alerting.

Lives in culture/clients/shared/ rather than per-backend config.py because
the dataclass is byte-identical across all four backends and has no
backend-specific behavior. Each backend's config.py re-exports it for
in-tree callers via:

    from culture.clients.shared.webhook_types import WebhookConfig  # noqa: F401

See docs/architecture/shared-vs-cited.md for the shared-vs-cited rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
