import asyncio
import pytest
from clients.claude.supervisor import Supervisor, SupervisorVerdict


def test_verdict_parsing():
    assert SupervisorVerdict.parse("OK") == SupervisorVerdict(action="OK", message="")
    assert SupervisorVerdict.parse("CORRECTION You're spiraling") == SupervisorVerdict(
        action="CORRECTION", message="You're spiraling")
    assert SupervisorVerdict.parse("THINK_DEEPER This needs more thought") == SupervisorVerdict(
        action="THINK_DEEPER", message="This needs more thought")
    assert SupervisorVerdict.parse("ESCALATION Still stuck") == SupervisorVerdict(
        action="ESCALATION", message="Still stuck")


@pytest.mark.asyncio
async def test_rolling_window():
    whispers = []
    async def on_whisper(msg, wtype):
        whispers.append((msg, wtype))
    async def mock_eval(window, task):
        return SupervisorVerdict(action="OK", message="")
    sup = Supervisor(
        window_size=5, eval_interval=3, escalation_threshold=3,
        evaluate_fn=mock_eval, on_whisper=on_whisper, on_escalation=None,
        task_description="test task",
    )
    for i in range(6):
        await sup.observe({"turn": i, "type": "response", "content": f"turn {i}"})
    assert len(sup._window) == 5
    assert len(whispers) == 0


@pytest.mark.asyncio
async def test_whisper_on_correction():
    whispers = []
    async def on_whisper(msg, wtype):
        whispers.append((msg, wtype))
    async def mock_eval(window, task):
        return SupervisorVerdict(action="CORRECTION", message="Stop retrying")
    sup = Supervisor(
        window_size=20, eval_interval=2, escalation_threshold=3,
        evaluate_fn=mock_eval, on_whisper=on_whisper, on_escalation=None,
        task_description="test task",
    )
    for i in range(2):
        await sup.observe({"turn": i})
    assert len(whispers) == 1
    assert whispers[0] == ("Stop retrying", "CORRECTION")


@pytest.mark.asyncio
async def test_escalation_after_threshold():
    whispers = []
    escalated = []
    async def on_whisper(msg, wtype):
        whispers.append((msg, wtype))
    async def on_escalation(msg):
        escalated.append(msg)
    call_count = 0
    async def mock_eval(window, task):
        nonlocal call_count
        call_count += 1
        return SupervisorVerdict(action="CORRECTION", message=f"Attempt {call_count}")
    sup = Supervisor(
        window_size=20, eval_interval=1, escalation_threshold=3,
        evaluate_fn=mock_eval, on_whisper=on_whisper, on_escalation=on_escalation,
        task_description="test task",
    )
    for i in range(3):
        await sup.observe({"turn": i})
    assert len(whispers) == 2
    assert len(escalated) == 1


@pytest.mark.asyncio
async def test_ok_resets_escalation_counter():
    whispers = []
    escalated = []
    async def on_whisper(msg, wtype):
        whispers.append((msg, wtype))
    async def on_escalation(msg):
        escalated.append(msg)
    verdicts = iter([
        SupervisorVerdict(action="CORRECTION", message="warn1"),
        SupervisorVerdict(action="CORRECTION", message="warn2"),
        SupervisorVerdict(action="OK", message=""),
        SupervisorVerdict(action="CORRECTION", message="warn3"),
        SupervisorVerdict(action="CORRECTION", message="warn4"),
    ])
    async def mock_eval(window, task):
        return next(verdicts)
    sup = Supervisor(
        window_size=20, eval_interval=1, escalation_threshold=3,
        evaluate_fn=mock_eval, on_whisper=on_whisper, on_escalation=on_escalation,
        task_description="test task",
    )
    for i in range(5):
        await sup.observe({"turn": i})
    assert len(escalated) == 0
    assert len(whispers) == 4
