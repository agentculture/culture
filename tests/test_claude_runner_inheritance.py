"""Tests for the Claude AgentRunner's setting_sources widening and the
conditional can_use_tool wiring tied to perm-policy file existence."""

from __future__ import annotations

from tests._sdk_stub import install_claude_sdk_stub

install_claude_sdk_stub()

import tempfile  # noqa: E402

import pytest  # noqa: E402

from culture.clients._perm_broker import write_default_policy  # noqa: E402
from culture.clients.claude.agent_runner import AgentRunner  # noqa: E402


@pytest.fixture
def culture_root(tmp_path, monkeypatch):
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    return tmp_path


def _runner(nick: str) -> AgentRunner:
    return AgentRunner(
        model="claude-opus-4-7",
        directory=tempfile.mkdtemp(prefix="culture-test-claude-"),
        nick=nick,
    )


class TestSettingSources:
    def test_setting_sources_widened(self, culture_root):
        runner = _runner(nick="local-foo")
        opts = runner._make_options()
        assert opts.setting_sources == ["user", "project", "local"]

    def test_permission_mode_still_bypass(self, culture_root):
        runner = _runner(nick="local-foo")
        opts = runner._make_options()
        assert opts.permission_mode == "bypassPermissions"


class TestConditionalCanUseTool:
    def test_no_policy_file_no_callback(self, culture_root):
        runner = _runner(nick="local-no-policy")
        opts = runner._make_options()
        assert opts.can_use_tool is None
        assert runner._broker is None

    def test_policy_file_present_wires_callback(self, culture_root):
        write_default_policy("local-with-policy")
        runner = _runner(nick="local-with-policy")
        opts = runner._make_options()
        assert opts.can_use_tool is not None
        assert callable(opts.can_use_tool)
        assert runner._broker is not None
        assert runner._broker.nick == "local-with-policy"

    def test_empty_nick_no_callback(self, culture_root):
        # Even with a policy file present, empty nick means no broker.
        runner = _runner(nick="")
        opts = runner._make_options()
        assert opts.can_use_tool is None
        assert runner._broker is None


class TestStreamingPromptWrapper:
    @pytest.mark.asyncio
    async def test_single_user_message_stream_shape(self):
        from culture.clients.claude.agent_runner import _single_user_message_stream

        items = [item async for item in _single_user_message_stream("hello world")]
        assert items == [
            {"type": "user", "message": {"role": "user", "content": "hello world"}}
        ]
