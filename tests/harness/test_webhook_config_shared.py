"""WebhookConfig is imported from culture.clients.shared.webhook_types
and remains accessible from each backend's config module via re-export."""

from __future__ import annotations

import pytest

from culture.clients.shared.webhook_types import WebhookConfig as SharedWebhookConfig


def test_default_webhook_config_values():
    cfg = SharedWebhookConfig()
    assert cfg.url is None
    assert cfg.irc_channel == "#alerts"
    assert cfg.events == [
        "agent_spiraling",
        "agent_error",
        "agent_question",
        "agent_timeout",
        "agent_complete",
    ]


@pytest.mark.parametrize("backend", ["claude", "codex", "copilot", "acp"])
def test_backend_config_reexports_webhook_config(backend: str):
    """Each backend's config.py re-exports WebhookConfig from the shared
    module, so existing `from culture.clients.<backend>.config import
    WebhookConfig` imports keep working."""
    mod = __import__(f"culture.clients.{backend}.config", fromlist=["WebhookConfig"])
    assert mod.WebhookConfig is SharedWebhookConfig
