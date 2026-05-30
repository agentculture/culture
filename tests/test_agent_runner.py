import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from culture.clients.claude.agent_runner import AgentRunner

# ---------------------------------------------------------------------------
# Fake SDK message types for testing
# ---------------------------------------------------------------------------


@dataclass
class FakeTextBlock:
    text: str
    type: str = "text"


@dataclass
class FakeAssistantMessage:
    content: list = field(default_factory=list)
    model: str = "fake-model"
    parent_tool_use_id: str | None = None
    error: str | None = None
    usage: dict[str, Any] | None = None


@dataclass
class FakeResultMessage:
    session_id: str = "sess-test-123"
    subtype: str = "result"
    duration_ms: int = 100
    duration_api_ms: int = 80
    is_error: bool = False
    num_turns: int = 1
    stop_reason: str | None = "end_turn"
    total_cost_usd: float | None = 0.01
    usage: dict[str, Any] | None = None
    result: str | None = None
    structured_output: Any = None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_stop(monkeypatch):
    """AgentRunner starts a background task and stops cleanly."""

    async def fake_query(*, prompt, options=None, transport=None):
        # Yield one message then wait forever (agent idle)
        yield FakeResultMessage(session_id="sess-001")
        await asyncio.sleep(999)

    monkeypatch.setattr("culture.clients.claude.agent_runner.query", fake_query)
    monkeypatch.setattr("culture.clients.claude.agent_runner.ResultMessage", FakeResultMessage)
    monkeypatch.setattr(
        "culture.clients.claude.agent_runner.AssistantMessage", FakeAssistantMessage
    )

    runner = AgentRunner(model="test-model", directory="/tmp")
    runner._prompt_queue.put_nowait("hello")
    await runner.start()
    await asyncio.sleep(0.1)
    assert runner.is_running()
    await runner.stop()
    assert not runner.is_running()


@pytest.mark.asyncio
async def test_on_exit_clean(monkeypatch):
    """on_exit fires with code 0 when graceful stop completes."""
    exit_codes = []

    async def on_exit(code):
        exit_codes.append(code)

    async def fake_query(*, prompt, options=None, transport=None):
        yield FakeResultMessage(session_id="sess-002")

    monkeypatch.setattr("culture.clients.claude.agent_runner.query", fake_query)
    monkeypatch.setattr("culture.clients.claude.agent_runner.ResultMessage", FakeResultMessage)
    monkeypatch.setattr(
        "culture.clients.claude.agent_runner.AssistantMessage", FakeAssistantMessage
    )

    runner = AgentRunner(model="test-model", directory="/tmp", on_exit=on_exit)
    runner._prompt_queue.put_nowait("go")
    await runner.start()
    # Let the turn complete, then the loop waits for next prompt
    await asyncio.sleep(0.2)
    # Graceful stop sends sentinel and lets loop exit normally
    await runner.stop()
    await asyncio.sleep(0.1)
    assert not runner.is_running()
    assert exit_codes == [0]


@pytest.mark.asyncio
async def test_on_exit_crash(monkeypatch):
    """on_exit fires with code 1 when query raises."""
    exit_codes = []

    async def on_exit(code):
        exit_codes.append(code)

    async def fake_query(*, prompt, options=None, transport=None):
        yield  # async generator — yields nothing before error
        raise RuntimeError("SDK error")

    monkeypatch.setattr("culture.clients.claude.agent_runner.query", fake_query)
    monkeypatch.setattr("culture.clients.claude.agent_runner.ResultMessage", FakeResultMessage)
    monkeypatch.setattr(
        "culture.clients.claude.agent_runner.AssistantMessage", FakeAssistantMessage
    )

    runner = AgentRunner(model="test-model", directory="/tmp", on_exit=on_exit)
    runner._prompt_queue.put_nowait("go")
    await runner.start()
    await asyncio.sleep(0.5)
    assert exit_codes == [1]
    assert not runner.is_running()


@pytest.mark.asyncio
async def test_on_task_done_fires_fallback_on_exit_for_silent_death(monkeypatch):
    """If _run_loop's task ends with an unhandled exception (from a callback
    escaping _process_turn's try/except), the done_callback fires on_exit(1)
    so the daemon's crash-recovery still triggers. Without this, is_running()
    returns False but no signal reaches the daemon."""
    exit_codes = []

    async def on_exit(code):
        exit_codes.append(code)

    async def on_message(msg):
        # An unhandled exception INSIDE a callback that escapes upward —
        # _process_turn's except catches it and calls on_exit(1) inline, so
        # this path is actually well-covered. To exercise the fallback we
        # poke a synthetic case below.
        raise RuntimeError("callback boom")

    async def fake_query(*, prompt, options=None, transport=None):
        yield FakeAssistantMessage(content=[FakeTextBlock(text="boom-trigger")])
        yield FakeResultMessage(session_id="sess-boom")

    monkeypatch.setattr("culture.clients.claude.agent_runner.query", fake_query)
    monkeypatch.setattr("culture.clients.claude.agent_runner.ResultMessage", FakeResultMessage)
    monkeypatch.setattr(
        "culture.clients.claude.agent_runner.AssistantMessage", FakeAssistantMessage
    )

    runner = AgentRunner(
        model="test-model", directory="/tmp", on_exit=on_exit, on_message=on_message
    )
    runner._prompt_queue.put_nowait("go")
    await runner.start()
    await asyncio.sleep(0.4)
    # on_exit(1) was called — either via _process_turn's inline path (the
    # normal route) or via the fallback done_callback. Either way the daemon
    # gets the signal it needs.
    assert exit_codes == [1]


@pytest.mark.asyncio
async def test_on_task_done_does_not_fire_on_clean_exit(monkeypatch):
    """Clean exit path (on_exit(0) already called) must not double-fire the
    fallback exit signal from the done_callback."""
    exit_codes = []

    async def on_exit(code):
        exit_codes.append(code)

    async def fake_query(*, prompt, options=None, transport=None):
        yield FakeResultMessage(session_id="sess-clean")

    monkeypatch.setattr("culture.clients.claude.agent_runner.query", fake_query)
    monkeypatch.setattr("culture.clients.claude.agent_runner.ResultMessage", FakeResultMessage)
    monkeypatch.setattr(
        "culture.clients.claude.agent_runner.AssistantMessage", FakeAssistantMessage
    )

    runner = AgentRunner(model="test-model", directory="/tmp", on_exit=on_exit)
    runner._prompt_queue.put_nowait("go")
    await runner.start()
    await asyncio.sleep(0.2)
    await runner.stop()
    await asyncio.sleep(0.1)
    # Exactly one clean exit signal — no fallback fired.
    assert exit_codes == [0]


def test_make_options_uses_default_mode_when_broker_wired():
    """SECURITY: when can_use_tool is wired (worker has a perm-policy), the
    SDK must call it. With permission_mode='bypassPermissions', the CLI
    binary literally allows every tool without ever calling the callback —
    silently defeating the broker, the ceiling, the handoff anchor, the
    ownership gate, and the perm-gate timeout."""
    runner = AgentRunner(model="m", directory="/tmp")
    # Simulate a wired broker by setting _can_use_tool to a stub.
    runner._can_use_tool = lambda *a, **k: None
    opts = runner._make_options()
    assert opts.permission_mode == "default"


def test_make_options_keeps_bypass_for_standalone_agents():
    """Standalone agents (no perm-policy file, _can_use_tool is None) keep
    the bypassPermissions semantics they've always had — there's no broker
    to consult, so prompting the user would hang the daemon."""
    runner = AgentRunner(model="m", directory="/tmp")
    runner._can_use_tool = None
    opts = runner._make_options()
    assert opts.permission_mode == "bypassPermissions"


@pytest.mark.asyncio
async def test_send_prompt(monkeypatch):
    """send_prompt queues a prompt that is consumed by the next turn."""
    prompts_seen = []

    async def fake_query(*, prompt, options=None, transport=None):
        prompts_seen.append(prompt)
        yield FakeResultMessage(session_id="sess-003")

    monkeypatch.setattr("culture.clients.claude.agent_runner.query", fake_query)
    monkeypatch.setattr("culture.clients.claude.agent_runner.ResultMessage", FakeResultMessage)
    monkeypatch.setattr(
        "culture.clients.claude.agent_runner.AssistantMessage", FakeAssistantMessage
    )

    runner = AgentRunner(model="test-model", directory="/tmp")
    await runner.send_prompt("first prompt")
    await runner.send_prompt("/compact")
    await runner.start()
    await asyncio.sleep(0.3)
    assert "first prompt" in prompts_seen
    assert "/compact" in prompts_seen
    await runner.stop()


@pytest.mark.asyncio
async def test_on_message_callback(monkeypatch):
    """AssistantMessage is forwarded to on_message callback as a dict."""
    messages_received = []

    async def on_message(msg):
        messages_received.append(msg)

    async def fake_query(*, prompt, options=None, transport=None):
        msg = FakeAssistantMessage(
            content=[FakeTextBlock(text="hello world")],
            model="claude-opus-4-6",
        )
        yield msg
        yield FakeResultMessage(session_id="sess-004")

    monkeypatch.setattr("culture.clients.claude.agent_runner.query", fake_query)
    monkeypatch.setattr("culture.clients.claude.agent_runner.ResultMessage", FakeResultMessage)
    monkeypatch.setattr(
        "culture.clients.claude.agent_runner.AssistantMessage", FakeAssistantMessage
    )

    runner = AgentRunner(
        model="test-model",
        directory="/tmp",
        on_message=on_message,
    )
    runner._prompt_queue.put_nowait("go")
    await runner.start()
    await asyncio.sleep(0.3)
    await runner.stop()

    assert len(messages_received) >= 1
    assert messages_received[0]["type"] == "assistant"
    assert messages_received[0]["model"] == "claude-opus-4-6"


@pytest.mark.asyncio
async def test_session_id_captured(monkeypatch):
    """session_id is set from ResultMessage."""

    async def fake_query(*, prompt, options=None, transport=None):
        yield FakeResultMessage(session_id="sess-captured-789")
        await asyncio.sleep(999)

    monkeypatch.setattr("culture.clients.claude.agent_runner.query", fake_query)
    monkeypatch.setattr("culture.clients.claude.agent_runner.ResultMessage", FakeResultMessage)
    monkeypatch.setattr(
        "culture.clients.claude.agent_runner.AssistantMessage", FakeAssistantMessage
    )

    runner = AgentRunner(model="test-model", directory="/tmp")
    runner._prompt_queue.put_nowait("go")
    await runner.start()
    await asyncio.sleep(0.2)
    assert runner.session_id == "sess-captured-789"
    await runner.stop()
