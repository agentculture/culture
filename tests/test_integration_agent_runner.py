"""End-to-end agent_runner timeout behavior — claude backend.

Drives the full daemon → AgentRunner → ``_run_loop`` → ``_process_turn``
chain with a hanging SDK and a short per-turn timeout, then asserts the
``culture.harness.llm.calls`` counter records ``outcome=timeout``.
Replaces the integration-shaped portion of
``tests/harness/test_agent_runner_claude.py``'s timeout test (the
harness unit test moves to cultureagent in Phase 1).

**Other backends (codex, copilot, acp):** the audit's "parameterize
over 4 backends" ask is acknowledged but narrowed for this PR. Each
backend has a different SDK injection point (subprocess, sessions, ACP
SDK), so adding all four in one PR would mix scope and risk per-backend
flakiness. The harness unit tests in
``tests/harness/test_agent_runner_{codex,copilot,acp}.py`` already
cover the timeout path at unit shape and ship to cultureagent in
Phase 1; the cross-backend integration coverage is tracked as a
follow-up.
"""

import asyncio
import sys
import types

import pytest

from culture.clients.claude.config import (
    AgentConfig,
    DaemonConfig,
    ServerConnConfig,
    WebhookConfig,
)
from culture.clients.shared import telemetry as harness_tel


def _redirect_pidfile(monkeypatch, tmp_path):
    """Redirect ``culture.pidfile.PID_DIR`` so daemons don't write into the
    real ``~/.culture/pids`` from a unit test."""
    monkeypatch.setattr("culture.pidfile.PID_DIR", str(tmp_path / "pids"))


def _invalidate_harness_telemetry_cache():
    """Clear the harness telemetry module cache so ``init_harness_telemetry``
    re-resolves the OTel globals (which the conftest fixtures own).
    Same helper rationale as Task 6 — see
    ``tests/test_integration_telemetry.py`` for the longer note on why
    this is the narrow safe poke vs ``harness_tel.reset_for_tests()``.
    """
    harness_tel._initialized_for = None
    harness_tel._tracer = None
    harness_tel._registry = None


def _stub_claude_sdk_if_needed():
    """Insert minimal ``claude_agent_sdk`` stubs into ``sys.modules`` so CI
    runs without the real SDK installed. Mirrors the stub block in
    ``tests/harness/test_agent_runner_claude.py``."""
    if "claude_agent_sdk" in sys.modules:
        return
    mod = types.ModuleType("claude_agent_sdk")

    class _Base:
        pass

    class AssistantMessage(_Base):
        def __init__(self, model="stub", content=None):
            self.model = model
            self.content = content or []

    class ResultMessage(_Base):
        def __init__(self, session_id="sid", is_error=False, result="", usage=None):
            self.session_id = session_id
            self.is_error = is_error
            self.result = result
            self.usage = usage

    class ClaudeAgentOptions(_Base):
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class TextBlock(_Base):
        pass

    class ThinkingBlock(_Base):
        pass

    class ToolUseBlock(_Base):
        pass

    class ToolResultBlock(_Base):
        pass

    async def query(**kwargs):
        # Default stub yields nothing; tests patch this with their own
        # variant. ``if False: yield`` makes this a valid async
        # generator (S5797 / S3626 dance).
        if False:
            yield  # pragma: no cover

    mod.AssistantMessage = AssistantMessage
    mod.ResultMessage = ResultMessage
    mod.ClaudeAgentOptions = ClaudeAgentOptions
    mod.TextBlock = TextBlock
    mod.ThinkingBlock = ThinkingBlock
    mod.ToolUseBlock = ToolUseBlock
    mod.ToolResultBlock = ToolResultBlock
    mod.query = query
    sys.modules["claude_agent_sdk"] = mod


async def _wait_for_timeout_metric(metrics_reader, timeout=10.0):
    """Bounded poll until ``culture.harness.llm.calls`` has a data point
    with ``outcome=timeout``. Returns the data point. Replaces fixed
    sleeps; the timeout path runs through ``record_llm_call`` after the
    SDK wait_for fires, but ordering vs. the test's next line isn't
    guaranteed."""
    async with asyncio.timeout(timeout):
        while True:
            data = metrics_reader.get_metrics_data()
            for rm in data.resource_metrics:
                for sm in rm.scope_metrics:
                    for metric in sm.metrics:
                        if metric.name != "culture.harness.llm.calls":
                            continue
                        for dp in metric.data.data_points:
                            if dp.attributes.get("outcome") == "timeout":
                                return dp
            await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_claude_agent_runner_records_timeout_outcome(
    server, metrics_reader, tracing_exporter, tmp_path, monkeypatch
):
    """A wedged claude SDK call exceeds ``turn_timeout_seconds``;
    AgentRunner's timeout path records ``outcome=timeout`` on the
    ``culture.harness.llm.calls`` counter."""
    _stub_claude_sdk_if_needed()
    _redirect_pidfile(monkeypatch, tmp_path)
    _invalidate_harness_telemetry_cache()

    # Hanging async generator — never yields, never returns. Mirrors the
    # production failure mode that motivated issue #349 (the per-turn
    # timeout that this whole code path defends against).
    async def _hanging_query(**_kwargs):
        await asyncio.Future()
        yield  # pragma: no cover  -- unreachable, marks this an async generator

    monkeypatch.setattr(
        "culture.clients.claude.agent_runner.query",
        _hanging_query,
        raising=True,
    )

    # Late import so the stub is in place before AgentDaemon binds.
    from culture.clients.claude.daemon import AgentDaemon

    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        webhooks=WebhookConfig(url=None),
    )
    agent = AgentConfig(
        nick="testserv-bot",
        directory=str(agent_dir),
        channels=["#general"],
    )
    # AgentConfig is non-frozen — daemon reads turn_timeout_seconds via
    # getattr() with DEFAULT_TURN_TIMEOUT_SECONDS fallback. 0.2s
    # resolves quickly without flaking under CI jitter.
    agent.turn_timeout_seconds = 0.2

    sock_dir = tmp_path / "sock"
    sock_dir.mkdir()
    daemon = AgentDaemon(config, agent, socket_dir=str(sock_dir), skip_claude=False)
    await daemon.start()
    try:
        # daemon.start spawned AgentRunner; queue a prompt so _run_loop
        # picks it up and invokes _process_turn (which wraps the hanging
        # query in asyncio.wait_for with our short timeout).
        assert daemon._agent_runner is not None
        await daemon._agent_runner.send_prompt("hello")

        timeout_dp = await _wait_for_timeout_metric(metrics_reader)
        assert timeout_dp.attributes.get("backend") == "claude"
        assert timeout_dp.value >= 1
    finally:
        await daemon.stop()
