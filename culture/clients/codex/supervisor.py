"""Codex supervisor — evaluates agent productivity via codex exec."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
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
        prompt_override: str = "",
        on_whisper: Callable[[str, str], Awaitable[None]] | None = None,
        on_escalation: Callable[[str], Awaitable[None]] | None = None,
    ):
        self.model = model
        self.window_size = window_size
        self.eval_interval = eval_interval
        self.escalation_threshold = escalation_threshold
        self.prompt_override = prompt_override
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
            self._turns = self._turns[-self.window_size :]

        self._turn_count += 1
        if self._turn_count % self.eval_interval == 0:
            await self._evaluate()

    @staticmethod
    async def _kill_process(proc: asyncio.subprocess.Process | None) -> None:
        """Terminate a subprocess safely, ignoring races."""
        if proc is None:
            return
        try:
            proc.kill()
        except ProcessLookupError:
            return
        await proc.wait()

    async def _run_supervisor_process(self, prompt: str) -> SupervisorVerdict | None:
        """Run codex exec and return the parsed verdict, or None on failure."""
        isolated_home = tempfile.mkdtemp(prefix="culture-codex-sv-")
        isolated_env = dict(os.environ)
        isolated_env["HOME"] = isolated_home
        isolated_env.pop("CODEX_HOME", None)
        isolated_env.pop("XDG_CONFIG_HOME", None)

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "codex",
                "exec",
                "--full-auto",
                "-m",
                self.model,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=isolated_env,
            )
            stdout, _ = await asyncio.wait_for(
                proc.communicate(prompt.encode()),
                timeout=30,
            )
            return SupervisorVerdict.parse(stdout.decode())
        except asyncio.TimeoutError:
            logger.warning("Codex supervisor timed out, killing process")
            await self._kill_process(proc)
            return None
        except Exception:
            logger.exception("Codex supervisor evaluation failed")
            if proc and proc.returncode is None:
                await self._kill_process(proc)
            return None
        finally:
            shutil.rmtree(isolated_home, ignore_errors=True)

    async def _process_verdict(self, verdict: SupervisorVerdict) -> None:
        """Handle a supervisor verdict by dispatching whispers or escalations."""
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

    async def _evaluate(self) -> None:
        """Run codex exec to evaluate the agent's recent activity."""
        transcript = self._format_transcript()
        template = self.prompt_override or SUPERVISOR_PROMPT
        try:
            prompt = template.format(transcript=transcript)
        except (KeyError, IndexError, ValueError) as exc:
            logger.warning("Invalid prompt_override template, falling back to default: %s", exc)
            prompt = SUPERVISOR_PROMPT.format(transcript=transcript)

        verdict = await self._run_supervisor_process(prompt)
        if verdict is None:
            return
        await self._process_verdict(verdict)

    def _format_transcript(self) -> str:
        """Format recent turns into a readable transcript."""
        lines = []
        for turn in self._turns[-self.window_size :]:
            content = turn.get("content", [])
            for block in content:
                if block.get("type") == "text":
                    lines.append(f"Agent: {block.get('text', '')[:200]}")
        return "\n".join(lines) if lines else "(no activity)"
