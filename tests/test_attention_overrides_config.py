"""Regression tests for ``attention_overrides`` on the central ``AgentConfig``.

Verifies that the central ``culture_core.config.AgentConfig`` carries the
``attention_overrides`` field so that claude/codex/copilot daemon factories
no longer raise ``AttributeError`` on startup, and that the culture.yaml
loader maps both ``attention:`` and ``attention_overrides:`` keys onto the
field correctly.  Closes culture-core#9.
"""

from __future__ import annotations

import dataclasses
import textwrap

import pytest

# ------------------------------------------------------------------
# Task 1: field presence
# ------------------------------------------------------------------


def test_attention_overrides_field_exists():
    """``AgentConfig`` must declare ``attention_overrides`` defaulting to None."""
    from culture_core.config import AgentConfig

    fields = {f.name: f for f in dataclasses.fields(AgentConfig)}
    assert "attention_overrides" in fields
    assert fields["attention_overrides"].default is None


# ------------------------------------------------------------------
# Task 2: each backend resolves attention from a central AgentConfig
# without raising AttributeError.
#
# The crash is at runtime (_poll_loop -> _resolve_attention_config ->
# resolve_attention_config(self.config, self.agent)), NOT at construction,
# so these tests build the daemon AND drive _resolve_attention_config() —
# the real crash site. This bites pre-fix for every backend:
#   - codex/copilot: agent_cfg.attention_overrides is missing;
#   - claude: also daemon_cfg.attention is missing, because
#     _create_claude_daemon used to pass the bare central ServerConfig
#     (unlike codex/copilot/acp, which wrap it). (culture-core#9)
# ------------------------------------------------------------------


@pytest.fixture
def central_agent_config(tmp_path):
    """Central ``AgentConfig`` with no attention block (the regression scenario)."""
    from culture_core.config import AgentConfig

    return AgentConfig(
        suffix="bot",
        backend="claude",
        channels=["#general"],
        directory=str(tmp_path),
    )


@pytest.fixture
def central_daemon_config():
    """A minimal central ``DaemonConfig`` (carries no daemon-level attention)."""
    from culture_core.config import DaemonConfig, ServerConnConfig, WebhookConfig

    return DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=6667),
        webhooks=WebhookConfig(url=None),
    )


def _assert_resolves(daemon):
    """The daemon's runtime attention resolution must return a config, not raise."""
    resolved = daemon._resolve_attention_config()
    assert resolved is not None
    # An AttentionConfig — duck-typed so we don't pin the import path.
    assert hasattr(resolved, "enabled")


def test_claude_daemon_resolves_attention(central_daemon_config, central_agent_config):
    """A claude daemon built from a central config resolves attention without
    raising — needs both the AgentConfig field AND the config wrap."""
    from culture_core.cli.agents import _create_claude_daemon

    _assert_resolves(_create_claude_daemon(central_daemon_config, central_agent_config))


def test_codex_daemon_resolves_attention(central_daemon_config, central_agent_config):
    """A codex daemon built from a central config resolves attention without raising."""
    from culture_core.cli.agents import _create_codex_daemon

    _assert_resolves(_create_codex_daemon(central_daemon_config, central_agent_config))


def test_copilot_daemon_resolves_attention(central_daemon_config, central_agent_config):
    """A copilot daemon built from a central config resolves attention without raising."""
    from culture_core.cli.agents import _create_copilot_daemon

    _assert_resolves(_create_copilot_daemon(central_daemon_config, central_agent_config))


# ------------------------------------------------------------------
# Task 3: resolve_attention_config works with central AgentConfig
# ------------------------------------------------------------------


def test_resolve_attention_config_with_central_config_none_overrides():
    """``resolve_attention_config`` returns daemon defaults when the central
    ``AgentConfig`` has ``attention_overrides=None``."""
    from cultureagent.clients.claude.config import (
        DaemonConfig,
        resolve_attention_config,
    )

    from culture_core.config import AgentConfig

    agent_cfg = AgentConfig(suffix="bot", directory=".")
    assert agent_cfg.attention_overrides is None

    # Use cultureagent's DaemonConfig (has .attention) — the central
    # ServerConfig alias does not carry attention, so we can't use it
    # here. The point is to prove the central AgentConfig has the field.
    daemon_cfg = DaemonConfig()
    result = resolve_attention_config(daemon_cfg, agent_cfg)
    assert result is not None


# ------------------------------------------------------------------
# Task 4: loader maps attention: / attention_overrides: correctly
# ------------------------------------------------------------------


def test_loader_maps_attention_block(tmp_path):
    """A culture.yaml with an ``attention:`` block yields a non-None
    ``attention_overrides`` dict and does NOT leak into extras."""
    from culture_core.config import load_culture_yaml

    yaml_path = tmp_path / "culture.yaml"
    yaml_path.write_text(textwrap.dedent("""\
            suffix: bot
            backend: claude
            attention:
              enabled: true
              tick_s: 30
            """))
    agents = load_culture_yaml(str(tmp_path))
    assert len(agents) == 1
    agent = agents[0]
    assert agent.attention_overrides is not None
    assert agent.attention_overrides == {"enabled": True, "tick_s": 30}
    assert "attention" not in agent.extras


def test_loader_no_attention_yields_none(tmp_path):
    """An agent entry with no attention key yields ``attention_overrides is
    None`` (backward compatible)."""
    from culture_core.config import load_culture_yaml

    yaml_path = tmp_path / "culture.yaml"
    yaml_path.write_text(textwrap.dedent("""\
            suffix: bot
            backend: claude
            """))
    agents = load_culture_yaml(str(tmp_path))
    assert agents[0].attention_overrides is None


def test_loader_maps_round_trip_key(tmp_path):
    """The round-tripped ``attention_overrides:`` key also maps onto the
    field (not extras)."""
    from culture_core.config import load_culture_yaml

    yaml_path = tmp_path / "culture.yaml"
    yaml_path.write_text(textwrap.dedent("""\
            suffix: bot
            backend: claude
            attention_overrides:
              enabled: false
            """))
    agents = load_culture_yaml(str(tmp_path))
    assert agents[0].attention_overrides == {"enabled": False}
    assert "attention_overrides" not in agents[0].extras


def test_loader_attention_takes_precedence_over_round_trip(tmp_path):
    """When both ``attention:`` and ``attention_overrides:`` are present,
    ``attention:`` wins."""
    from culture_core.config import load_culture_yaml

    yaml_path = tmp_path / "culture.yaml"
    yaml_path.write_text(textwrap.dedent("""\
            suffix: bot
            backend: claude
            attention:
              enabled: true
            attention_overrides:
              enabled: false
            """))
    agents = load_culture_yaml(str(tmp_path))
    # attention: wins
    assert agents[0].attention_overrides == {"enabled": True}
    assert "attention" not in agents[0].extras
    assert "attention_overrides" not in agents[0].extras


# ------------------------------------------------------------------
# Round-trip on save: attention must survive rewrites (archive/rename).
# _parse_agent_entry moves attention out of extras into the typed field,
# so _agent_to_yaml_dict must emit it back or rewrites silently drop it.
# (qodo PR #10 bug 1)
# ------------------------------------------------------------------


def test_save_preserves_attention_round_trip(tmp_path):
    """load -> save -> reload must preserve attention_overrides (and serialize
    it under the author-facing ``attention:`` key)."""
    from culture_core.config import load_culture_yaml, save_culture_yaml

    yaml_path = tmp_path / "culture.yaml"
    yaml_path.write_text(textwrap.dedent("""\
            suffix: bot
            backend: claude
            attention:
              enabled: true
              tick_s: 30
            """))
    agents = load_culture_yaml(str(tmp_path))
    save_culture_yaml(str(tmp_path), agents)

    assert "attention:" in yaml_path.read_text()
    reloaded = load_culture_yaml(str(tmp_path))
    assert reloaded[0].attention_overrides == {"enabled": True, "tick_s": 30}


def test_archive_rewrite_preserves_attention(tmp_path):
    """The archive rewrite path (load -> mutate -> save) must not drop attention."""
    from culture_core.config import load_culture_yaml, save_culture_yaml

    yaml_path = tmp_path / "culture.yaml"
    yaml_path.write_text(textwrap.dedent("""\
            suffix: bot
            backend: claude
            attention:
              enabled: false
            """))
    agents = load_culture_yaml(str(tmp_path))
    agents[0].archived = True  # what archive_manifest_agent does before saving
    save_culture_yaml(str(tmp_path), agents)

    reloaded = load_culture_yaml(str(tmp_path))
    assert reloaded[0].archived is True
    assert reloaded[0].attention_overrides == {"enabled": False}


def test_save_omits_attention_when_none(tmp_path):
    """An agent with no attention overrides must not grow an ``attention:`` key."""
    from culture_core.config import load_culture_yaml, save_culture_yaml

    yaml_path = tmp_path / "culture.yaml"
    yaml_path.write_text("suffix: bot\nbackend: claude\n")
    agents = load_culture_yaml(str(tmp_path))
    save_culture_yaml(str(tmp_path), agents)
    assert "attention" not in yaml_path.read_text()


# ------------------------------------------------------------------
# ACP parity: the coercion + daemon must honor attention overrides too,
# not just claude/codex/copilot (all-backends rule). (qodo PR #10 bug 2)
# ------------------------------------------------------------------


def test_acp_coercion_forwards_attention_overrides():
    """``_coerce_to_acp_agent`` must carry attention_overrides onto the rebuilt
    ACPAgentConfig (else ACP silently ignores culture.yaml ``attention:``)."""
    from culture_core.cli.agents import _coerce_to_acp_agent
    from culture_core.config import AgentConfig

    agent = AgentConfig(
        suffix="bot", backend="acp", directory=".", attention_overrides={"enabled": False}
    )
    coerced = _coerce_to_acp_agent(agent)
    assert coerced.attention_overrides == {"enabled": False}


def test_acp_daemon_honors_attention_override(central_daemon_config):
    """An ACP daemon built from a central config with an attention override
    resolves to that override at runtime (not the daemon default)."""
    from culture_core.cli.agents import _create_acp_daemon
    from culture_core.config import AgentConfig

    agent = AgentConfig(
        suffix="bot", backend="acp", directory=".", attention_overrides={"enabled": False}
    )
    daemon = _create_acp_daemon(central_daemon_config, agent)
    resolved = daemon._resolve_attention_config()
    assert resolved.enabled is False
