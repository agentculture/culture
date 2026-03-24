"""Codex supervisor — evaluates agent productivity via codex exec."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

SUPERVISOR_PROMPT = """You are a supervisor monitoring an AI coding agent on an IRC network.
Review the agent's recent activity and respond with exactly one verdict:

- OK — agent is productive, no action needed
- CORRECTION <message> — agent needs redirection, include guidance
- THINK_DEEPER <message> — agent should reflect more deeply
- ESCALATION <message> — agent is spiraling, humans need to be notified

Recent agent activity:
{transcript}

Your verdict (one line):"""


@dataclass
class SupervisorVerdict:
    action: str  # OK, CORRECTION, THINK_DEEPER, ESCALATION
    message: str

    @classmethod
    def parse(cls, text: str) -> SupervisorVerdict:
        text = text.strip()
        parts = text.split(None, 1)
        action = parts[0] if parts else "OK"
        if action not in ("OK", "CORRECTION", "THINK_DEEPER", "ESCALATION"):
            action = "OK"
        message = parts[1] if len(parts) > 1 else ""
        return cls(action=action, message=message)


class CodexSupervisor:
    """Supervisor that uses codex exec to evaluate agent behavior."""

    def __init__(
        self,
        model: str = "o3-mini",
        window_size: int = 20,
        eval_interval: int = 5,
        escalation_threshold: int = 3,
        on_whisper: Callable[[str, str], Awaitable[None]] | None = None,
        on_escalation: Callable[[str], Awaitable[None]] | None = None,
    ):
        self.model = model
        self.window_size = window_size
        self.eval_interval = eval_interval
        self.escalation_threshold = escalation_threshold
        self.on_whisper = on_whisper
        self.on_escalation = on_escalation

        self._turns: list[dict[str, Any]] = []
        self._turn_count = 0
        self._escalation_count = 0

    async def start(self) -> None:
        """Start the supervisor (no-op for polling-based supervisor)."""
        pass

    async def stop(self) -> None:
        """Stop the supervisor."""
        pass

    async def observe(self, turn: dict[str, Any]) -> None:
        """Feed a completed agent turn to the supervisor."""
        self._turns.append(turn)
        if len(self._turns) > self.window_size:
            self._turns = self._turns[-self.window_size:]

        self._turn_count += 1
        if self._turn_count % self.eval_interval == 0:
            await self._evaluate()

    async def _evaluate(self) -> None:
        """Run codex exec to evaluate the agent's recent activity."""
        transcript = self._format_transcript()
        prompt = SUPERVISOR_PROMPT.format(transcript=transcript)

        try:
            proc = await asyncio.create_subprocess_exec(
                "codex", "exec", "--full-auto", "-m", self.model,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(
                proc.communicate(prompt.encode()),
                timeout=30,
            )
            verdict = SupervisorVerdict.parse(stdout.decode())
        except Exception:
            logger.exception("Codex supervisor evaluation failed")
            return

        if verdict.action == "ESCALATION":
            self._escalation_count += 1
            if self._escalation_count >= self.escalation_threshold:
                if self.on_escalation:
                    await self.on_escalation(verdict.message)
        elif verdict.action in ("CORRECTION", "THINK_DEEPER"):
            self._escalation_count = 0
            if self.on_whisper:
                await self.on_whisper(verdict.message, verdict.action)
        else:
            self._escalation_count = 0

    def _format_transcript(self) -> str:
        """Format recent turns into a readable transcript."""
        lines = []
        for turn in self._turns[-self.window_size:]:
            content = turn.get("content", [])
            for block in content:
                if block.get("type") == "text":
                    lines.append(f"Agent: {block.get('text', '')[:200]}")
        return "\n".join(lines) if lines else "(no activity)"
