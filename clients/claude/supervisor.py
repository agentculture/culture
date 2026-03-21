from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)


@dataclass
class SupervisorVerdict:
    action: str  # OK, CORRECTION, THINK_DEEPER, ESCALATION
    message: str

    @classmethod
    def parse(cls, text: str) -> SupervisorVerdict:
        text = text.strip()
        if text == "OK":
            return cls(action="OK", message="")
        parts = text.split(" ", 1)
        action = parts[0]
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
