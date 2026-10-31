"""Tests for external-agent config support (culture#506, plan external-residents-506).

Covers:

- ``AgentConfig.owner`` — optional ``str | None`` field (default ``None``);
  no other ``AgentConfig`` field changes.
- ``backend: external`` + ``owner`` round-trip through ``load_culture_yaml``
  and ``save_culture_yaml`` (the ``_agent_to_yaml_dict`` serialization path)
  unchanged; ``backend`` stays a free ``str``.
- ``culture_core.cli.shared.external.is_external(agent)`` /
  ``owner_message(agent)`` helpers.
"""

from __future__ import annotations

import pytest

from culture_core.config import AgentConfig, load_culture_yaml, save_culture_yaml


class TestOwnerField:
    def test_owner_defaults_to_none(self):
        assert AgentConfig(suffix="a").owner is None

    def test_other_fields_unchanged(self):
        """Adding owner must not alter any existing AgentConfig field."""
        agent = AgentConfig(suffix="a")
        defaults = AgentConfig()
        assert agent.suffix == "a"
        assert agent.backend == defaults.backend
        assert agent.channels == defaults.channels
        assert agent.model == defaults.model
        assert agent.thinking == defaults.thinking
        assert agent.system_prompt == defaults.system_prompt
        assert agent.tags == defaults.tags
        assert agent.icon is None
        assert agent.archived is False
        assert agent.archived_at == ""
        assert agent.archived_reason == ""
        assert agent.token_budget is None
        assert agent.token_budget_warn_pct == defaults.token_budget_warn_pct
        assert agent.extras == {}
        assert agent.attention_overrides is None
        # owner is a dataclass field (so it parses from YAML), not extras
        assert "owner" in AgentConfig.__dataclass_fields__
        assert "owner" not in agent.extras


class TestExternalBackendRoundTrip:
    def test_load_external_backend_with_owner(self, tmp_path):
        (tmp_path / "culture.yaml").write_text("suffix: scout\nbackend: external\nowner: ops-bot\n")
        agents = load_culture_yaml(str(tmp_path))
        assert len(agents) == 1
        assert agents[0].suffix == "scout"
        assert agents[0].backend == "external"
        assert agents[0].owner == "ops-bot"

    def test_load_without_owner(self, tmp_path):
        (tmp_path / "culture.yaml").write_text("suffix: scout\nbackend: external\n")
        agents = load_culture_yaml(str(tmp_path))
        assert agents[0].backend == "external"
        assert agents[0].owner is None

    def test_save_and_reload_round_trip(self, tmp_path):
        agent = AgentConfig(suffix="scout", backend="external", owner="ops-bot")
        save_culture_yaml(str(tmp_path), [agent])

        loaded = load_culture_yaml(str(tmp_path))
        assert len(loaded) == 1
        assert loaded[0].suffix == "scout"
        assert loaded[0].backend == "external"
        assert loaded[0].owner == "ops-bot"

    def test_save_omits_unset_owner(self, tmp_path):
        """owner=None must not be serialized (same pattern as icon)."""
        agent = AgentConfig(suffix="scout", backend="external")
        save_culture_yaml(str(tmp_path), [agent])

        raw = (tmp_path / "culture.yaml").read_text()
        assert "owner" not in raw

        loaded = load_culture_yaml(str(tmp_path))
        assert loaded[0].owner is None

    def test_multi_agent_round_trip(self, tmp_path):
        agents = [
            AgentConfig(suffix="culture", backend="claude"),
            AgentConfig(suffix="scout", backend="external", owner="ops-bot"),
        ]
        save_culture_yaml(str(tmp_path), agents)

        loaded = load_culture_yaml(str(tmp_path))
        assert len(loaded) == 2
        assert loaded[0].owner is None
        assert loaded[1].backend == "external"
        assert loaded[1].owner == "ops-bot"

    def test_backend_stays_free_str(self):
        """Any backend value loads into the plain str field, not just known ones."""
        agent = AgentConfig(suffix="x", backend="some-typo-backend")
        assert agent.backend == "some-typo-backend"
        assert agent.agent == "some-typo-backend"


class TestIsExternal:
    def test_external_backend_is_external(self):
        from culture_core.cli.shared.external import is_external

        assert is_external(AgentConfig(suffix="a", backend="external")) is True

    def test_known_backends_are_not_external(self):
        from culture_core.cli.shared.external import is_external

        for backend in ("claude", "codex", "copilot", "acp"):
            assert is_external(AgentConfig(suffix="a", backend=backend)) is False

    def test_unknown_backend_is_not_external(self):
        from culture_core.cli.shared.external import is_external

        assert is_external(AgentConfig(suffix="a", backend="colleague")) is False


class TestOwnerMessage:
    def test_owner_set(self):
        from culture_core.cli.shared.external import owner_message

        agent = AgentConfig(suffix="a", backend="external", owner="ops-bot")
        assert owner_message(agent) == "owned by ops-bot"

    def test_owner_unset(self):
        from culture_core.cli.shared.external import owner_message

        agent = AgentConfig(suffix="a", backend="external")
        assert owner_message(agent) == "owned by an external process"

    def test_owner_empty_string_treated_as_unset(self):
        from culture_core.cli.shared.external import owner_message

        agent = AgentConfig(suffix="a", backend="external", owner="")
        assert owner_message(agent) == "owned by an external process"


def test_shared_external_module_location():
    """The helper lives at culture_core/cli/shared/external.py."""
    import culture_core.cli.shared.external as mod

    assert hasattr(mod, "is_external")
    assert hasattr(mod, "owner_message")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
