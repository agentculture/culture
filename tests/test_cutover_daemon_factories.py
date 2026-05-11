"""Smoke tests for the daemon factory functions in ``culture/cli/agent.py``.

After the 11.0.0 cultureagent extraction these factories do nothing but
import the daemon class from ``cultureagent.clients.<backend>.daemon``
and instantiate it. The factories are dispatched from
``_BACKEND_DAEMON_FACTORIES`` inside ``_run_single_agent`` / system unit
file rendering, so they cannot be reached purely through the
existing integration tests (which import daemon classes directly to
build the daemon-under-test).

These tests construct each daemon via its factory without starting it
and assert (a) the import path resolves to ``cultureagent`` (catching
accidental shim-vs-direct drift on a dep bump) and (b) the resulting
object is an instance of the expected class. No network or socket
side effects.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def minimal_daemon_config():
    """Return a ``DaemonConfig`` with enough fields for daemon construction
    but no real server endpoint. The factories don't open sockets at
    construction time — that happens in ``daemon.start()`` which we don't
    call."""
    from culture.clients.claude.config import (  # shim -> cultureagent
        DaemonConfig,
        ServerConnConfig,
        WebhookConfig,
    )

    return DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=6667),
        webhooks=WebhookConfig(url=None),
    )


@pytest.fixture
def minimal_agent_config(tmp_path):
    """Return an ``AgentConfig`` with just enough to construct a daemon."""
    from culture.clients.claude.config import AgentConfig  # shim -> cultureagent

    return AgentConfig(
        nick="testserv-bot",
        directory=str(tmp_path),
        channels=["#general"],
    )


def test_claude_daemon_factory_resolves_via_cultureagent(
    minimal_daemon_config, minimal_agent_config
):
    """``_create_claude_daemon`` instantiates the claude AgentDaemon from
    cultureagent and returns an instance of that class."""
    from cultureagent.clients.claude.daemon import AgentDaemon

    from culture.cli.agent import _create_claude_daemon

    daemon = _create_claude_daemon(minimal_daemon_config, minimal_agent_config)
    assert isinstance(daemon, AgentDaemon)


def test_codex_daemon_factory_resolves_via_cultureagent(
    minimal_daemon_config, minimal_agent_config
):
    """``_create_codex_daemon`` instantiates the codex CodexDaemon from
    cultureagent and returns an instance of that class."""
    from cultureagent.clients.codex.daemon import CodexDaemon

    from culture.cli.agent import _create_codex_daemon

    daemon = _create_codex_daemon(minimal_daemon_config, minimal_agent_config)
    assert isinstance(daemon, CodexDaemon)


def test_copilot_daemon_factory_resolves_via_cultureagent(
    minimal_daemon_config, minimal_agent_config
):
    """``_create_copilot_daemon`` instantiates the copilot CopilotDaemon
    from cultureagent and returns an instance of that class."""
    pytest.importorskip("copilot")
    from cultureagent.clients.copilot.daemon import CopilotDaemon

    from culture.cli.agent import _create_copilot_daemon

    daemon = _create_copilot_daemon(minimal_daemon_config, minimal_agent_config)
    assert isinstance(daemon, CopilotDaemon)


def test_acp_daemon_factory_resolves_via_cultureagent(minimal_daemon_config, minimal_agent_config):
    """``_create_acp_daemon`` instantiates the acp ACPDaemon from
    cultureagent and returns an instance of that class. Verifies that
    ``_coerce_to_acp_agent`` round-trips a generic AgentConfig into
    ACPAgentConfig as well."""
    from cultureagent.clients.acp.daemon import ACPDaemon

    from culture.cli.agent import _create_acp_daemon

    daemon = _create_acp_daemon(minimal_daemon_config, minimal_agent_config)
    assert isinstance(daemon, ACPDaemon)
