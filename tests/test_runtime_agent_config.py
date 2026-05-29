"""Regression: the runtime AgentConfig (culture.config) must expose boss +
context_watch from culture.yaml extras, and the Claude daemon must normalize
either config flavor. Caught live: the daemon read agent.context_watch on the
runtime config (which lacked it) and crashed every Claude agent on startup.
"""

from __future__ import annotations

from tests._sdk_stub import install_claude_sdk_stub

install_claude_sdk_stub()

from culture.config import AgentConfig as RuntimeAgentConfig  # noqa: E402


class TestRuntimeAgentConfigProperties:
    def test_boss_from_extras(self):
        a = RuntimeAgentConfig(nick="local-w", extras={"boss": "local-boss"})
        assert a.boss == "local-boss"

    def test_boss_default_empty(self):
        assert RuntimeAgentConfig(nick="local-w").boss == ""

    def test_context_watch_from_extras(self):
        a = RuntimeAgentConfig(
            nick="local-w", extras={"context_watch": {"high_water": 0.8, "enabled": False}}
        )
        assert a.context_watch == {"high_water": 0.8, "enabled": False}

    def test_context_watch_default_empty_dict(self):
        assert RuntimeAgentConfig(nick="local-w").context_watch == {}


class TestDaemonNormalizers:
    def test_context_watch_state_from_dict(self):
        from culture.clients.claude.daemon import _context_watch_state

        a = RuntimeAgentConfig(
            nick="local-w", extras={"context_watch": {"high_water": 0.8, "low_water": 0.4}}
        )
        st = _context_watch_state(a)
        assert st.high_water == 0.8 and st.low_water == 0.4 and st.enabled is True

    def test_context_watch_state_empty_defaults(self):
        from culture.clients.claude.daemon import _context_watch_state

        st = _context_watch_state(RuntimeAgentConfig(nick="local-w"))
        assert st.high_water == 0.90 and st.low_water == 0.50 and st.enabled is True

    def test_context_watch_string_thresholds_coerced(self):
        # A quoted YAML number (str) must coerce to float, not crash evaluate().
        from culture.clients.claude.daemon import _context_watch_state

        a = RuntimeAgentConfig(
            nick="local-w",
            extras={"context_watch": {"high_water": "0.8", "low_water": "0.4"}},
        )
        st = _context_watch_state(a)
        assert st.high_water == 0.8 and st.low_water == 0.4
        assert isinstance(st.high_water, float) and isinstance(st.low_water, float)

    def test_context_watch_bad_threshold_falls_back(self):
        from culture.clients.claude.daemon import _context_watch_state

        a = RuntimeAgentConfig(nick="local-w", extras={"context_watch": {"high_water": "abc"}})
        st = _context_watch_state(a)
        assert st.high_water == 0.90  # bad value → default, no crash

    def test_context_watch_string_enabled_false_disables(self):
        from culture.clients.claude.daemon import _context_watch_state

        a = RuntimeAgentConfig(nick="local-w", extras={"context_watch": {"enabled": "false"}})
        assert _context_watch_state(a).enabled is False

    def test_context_watch_state_from_object(self):
        # The backend-specific config exposes a ContextWatchConfig object.
        from culture.clients.claude.config import AgentConfig as ClaudeAgentConfig
        from culture.clients.claude.daemon import _context_watch_state

        a = ClaudeAgentConfig(nick="local-w")
        a.context_watch.high_water = 0.95
        st = _context_watch_state(a)
        assert st.high_water == 0.95

    def test_boss_nick_runtime_and_default(self):
        from culture.clients.claude.daemon import _boss_nick

        assert (
            _boss_nick(RuntimeAgentConfig(nick="w", extras={"boss": "local-boss"})) == "local-boss"
        )
        assert _boss_nick(RuntimeAgentConfig(nick="w")) == ""
