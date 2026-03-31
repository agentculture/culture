from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Awaitable

from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock

logger = logging.getLogger(__name__)

_SUPERVISOR_SYSTEM_PROMPT = """\
You are a supervisor monitoring an AI agent's work session.

Your job: detect when the agent is unproductive and intervene minimally.

Watch for:
- SPIRALING: Same approach retried 3+ times, no progress
- DRIFT: Work diverging from the original task
- STALLING: Long gaps with no meaningful output
- SHALLOW: Complex decisions made without sufficient reasoning

Respond with exactly one line:
- OK — agent is productive, no action needed
- CORRECTION <message> — agent needs redirection
- THINK_DEEPER <message> — agent should use extended thinking
- ESCALATION <message> — humans need to be notified

Be conservative. Only intervene when clearly warranted.
Most evaluations should return OK."""


@dataclass
class SupervisorVerdict:
    action: str  # OK, CORRECTION, THINK_DEEPER, ESCALATION
    message: str

    @classmethod
    def parse(cls, text: str) -> SupervisorVerdict:
        text = text.strip()
        if not text or text == "OK":
            return cls(action="OK", message="")
        parts = text.split(" ", 1)
        action = parts[0]
        if action not in ("OK", "CORRECTION", "THINK_DEEPER", "ESCALATION"):
            logger.warning("Unknown supervisor verdict %r, defaulting to OK", action)
            return cls(action="OK", message="")
        message = parts[1] if len(parts) > 1 else ""
        return cls(action=action, message=message)


EvaluateFn = Callable[[list[dict[str, Any]], str], Awaitable[SupervisorVerdict]]


class Supervisor:
    def __init__(self, window_size: int, eval_interval: int, escalation_threshold: int,
                 evaluate_fn: EvaluateFn,
                 on_whisper: Callable[[str, str], Awaitable[None]] | None,
                 on_escalation: Callable[[str], Awaitable[None]] | None,
                 task_description: str = ""):
        self.window_size = window_size
        self.eval_interval = eval_interval
        self.escalation_threshold = escalation_threshold
        self.evaluate_fn = evaluate_fn
        self.on_whisper = on_whisper
        self.on_escalation = on_escalation
        self.task_description = task_description
        self._window: deque[dict[str, Any]] = deque(maxlen=window_size)
        self._turn_count: int = 0
        self._consecutive_failures: int = 0
        self.paused: bool = False

    async def observe(self, turn: dict[str, Any]) -> None:
        self._window.append(turn)
        self._turn_count += 1
        if self._turn_count % self.eval_interval == 0:
            await self._evaluate()

    async def _evaluate(self) -> None:
        if self.paused:
            return
        try:
            verdict = await self.evaluate_fn(list(self._window), self.task_description)
        except Exception:
            logger.exception("Supervisor evaluation failed")
            return
        if verdict.action == "OK":
            self._consecutive_failures = 0
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.escalation_threshold:
            self.paused = True
            if self.on_escalation:
                await self.on_escalation(verdict.message)
        else:
            if self.on_whisper:
                await self.on_whisper(verdict.message, verdict.action)


def _format_window(window: list[dict[str, Any]], task: str) -> str:
    """Format the rolling window into a prompt for the supervisor."""
    lines = [f"Task: {task}" if task else "Task: (none provided)"]
    lines.append(f"\nRecent agent activity ({len(window)} turns):\n")
    for i, turn in enumerate(window, 1):
        turn_type = turn.get("type", "unknown")
        content = turn.get("content", "")
        if isinstance(content, list):
            content = json.dumps(content, default=str)
        lines.append(f"Turn {i} [{turn_type}]: {content}")
    lines.append("\nEvaluate the agent's productivity. Respond with your verdict.")
    return "\n".join(lines)


def make_sdk_evaluate_fn(
    model: str = "claude-sonnet-4-6",
    thinking: str | None = None,
    prompt_override: str = "",
) -> EvaluateFn:
    """Create an evaluate_fn that uses the Claude Agent SDK."""
    system_prompt = prompt_override or _SUPERVISOR_SYSTEM_PROMPT

    async def evaluate(window: list[dict[str, Any]], task: str) -> SupervisorVerdict:
        prompt = _format_window(window, task)
        result_text = ""
        opts = ClaudeAgentOptions(
            model=model,
            max_turns=1,
            system_prompt=system_prompt,
            tools=[],
        )
        if thinking:
            opts.effort = thinking
        async for message in query(prompt=prompt, options=opts):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        result_text += block.text
        return SupervisorVerdict.parse(result_text)

    return evaluate
