from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import TYPE_CHECKING, Any, AsyncIterable, Awaitable, Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    query,
)
from opentelemetry import trace as _otel_trace

from culture.clients._perm_broker import PermissionBroker, has_policy_file
from culture.clients.claude.telemetry import _HARNESS_TRACER_NAME, record_llm_call

if TYPE_CHECKING:
    from culture.clients.claude.telemetry import HarnessMetricsRegistry

logger = logging.getLogger(__name__)


async def _single_user_message_stream(text: str) -> AsyncIterable[dict[str, Any]]:
    """Yield one user message in the SDK's streaming-mode shape.

    Required by the Claude Agent SDK whenever ``can_use_tool`` is set
    (client.py raises ``ValueError`` if ``prompt`` is a plain string).
    """
    yield {
        "type": "user",
        "message": {"role": "user", "content": text},
    }


def _content_block_to_dict(block: Any) -> dict[str, Any]:
    """Convert an SDK content block to a plain dict for supervisor observation."""
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ToolUseBlock):
        return {"type": "tool_use", "name": block.name, "input": block.input}
    if isinstance(block, ToolResultBlock):
        return {"type": "tool_result", "content": block.content}
    if isinstance(block, ThinkingBlock):
        return {"type": "thinking", "text": block.thinking}
    return {"type": "unknown", "repr": repr(block)}


def _extract_usage(u: Any) -> dict[str, Any]:
    """Extract token counts from a usage object (dict or attr-style).

    Uses explicit branching so that a legitimate zero-token value is preserved
    rather than being silenced by the ``or``-fallback pattern.
    """
    if isinstance(u, dict):
        tokens_in = u.get("input_tokens")
        tokens_out = u.get("output_tokens")
    else:
        tokens_in = getattr(u, "input_tokens", None)
        tokens_out = getattr(u, "output_tokens", None)
    return {"tokens_input": tokens_in, "tokens_output": tokens_out}


class AgentRunner:
    """Manages a Claude Agent SDK session for a single agent nick.

    Replaces the previous subprocess-based runner with the SDK's ``query()``
    async generator, providing structured messages, session resume, and
    proper lifecycle management.
    """

    def __init__(
        self,
        model: str,
        directory: str,
        system_prompt: str = "",
        on_exit: Callable[[int], Awaitable[None]] | None = None,
        on_message: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        on_usage: Callable[[int | None], Awaitable[None]] | None = None,
        on_perm_request: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        metrics: HarnessMetricsRegistry | None = None,
        nick: str = "",
    ) -> None:
        self.model = model
        self.directory = directory
        self.system_prompt = system_prompt
        self.on_exit = on_exit
        self.on_message = on_message
        self.on_usage = on_usage
        self.on_perm_request = on_perm_request
        self._metrics = metrics
        self._nick = nick

        self._session_id: str | None = None
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._prompt_queue: asyncio.Queue[str] = asyncio.Queue()

        # Permission broker — wired only when a perm-policy/<nick>.yaml exists.
        # Standalone mesh agents (no policy file) keep today's bypassPermissions
        # semantics: can_use_tool=None and string-prompt path preserved.
        if nick and has_policy_file(nick):
            self._broker: PermissionBroker | None = PermissionBroker(
                nick=nick, on_request=on_perm_request
            )
            self._can_use_tool = self._broker.gate
        else:
            self._broker = None
            self._can_use_tool = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def start(self, initial_prompt: str = "") -> None:
        """Start the SDK session loop as a background task."""
        self._stopping = False
        if initial_prompt:
            self._prompt_queue.put_nowait(initial_prompt)
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Signal the session loop to exit gracefully.

        Enqueues a sentinel that causes _run_loop to break, then waits
        for the task to finish.  Falls back to cancellation if the loop
        does not exit within 5 seconds.
        """
        self._stopping = True
        # Unblock the queue so the loop sees _stopping
        self._prompt_queue.put_nowait("")
        if self._task and not self._task.done():
            try:
                async with asyncio.timeout(5.0):
                    await asyncio.shield(self._task)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def send_prompt(self, text: str) -> None:
        """Queue a prompt for the next SDK turn (e.g. /compact, /clear)."""
        await self._prompt_queue.put(text)

    @property
    def session_id(self) -> str | None:
        return self._session_id

    # ------------------------------------------------------------------
    # Internal session loop
    # ------------------------------------------------------------------

    def _make_options(self) -> ClaudeAgentOptions:
        opts = ClaudeAgentOptions(
            model=self.model,
            cwd=self.directory,
            permission_mode="bypassPermissions",
            # Inherit user-level skills, MCP servers, plugins from
            # ~/.claude/ when present.  Project + local still apply.
            setting_sources=["user", "project", "local"],
            # Conditional: only set when this helper has a perm-policy file.
            # Setting it unconditionally would hang non-supervised agents
            # (no boss watching the queue) forever on first non-auto-allow
            # tool call.
            can_use_tool=self._can_use_tool,
            # Expose the agent's own nick to its Bash tools so the IRC skill
            # (`culture channel …`) and the boss skill (`culture boss …`) can
            # resolve this daemon's socket. Without it an autonomous daemon
            # agent cannot address its own IRC connection.
            env=self._subprocess_env(),
        )
        if self.system_prompt:
            opts.system_prompt = self.system_prompt
        if self._session_id:
            opts.resume = self._session_id
        return opts

    def _subprocess_env(self) -> dict[str, str]:
        """Env for the SDK subprocess: inherit ours + pin CULTURE_NICK."""
        env = dict(os.environ)
        if self._nick:
            env["CULTURE_NICK"] = self._nick
        return env

    def _handle_result_message(self, msg: ResultMessage) -> None:
        """Handle a ResultMessage — track session and log errors."""
        self._session_id = msg.session_id
        if msg.is_error:
            logger.warning("SDK session error: %s", msg.result)

    async def _handle_assistant_message(self, msg: AssistantMessage) -> None:
        """Handle an AssistantMessage — convert and fire callback."""
        if self.on_message:
            msg_dict = self._assistant_to_dict(msg)
            await self.on_message(msg_dict)

    async def _process_turn(self, prompt: str) -> bool:
        """Run a single conversation turn. Returns False if a fatal error occurred."""
        tracer = _otel_trace.get_tracer(_HARNESS_TRACER_NAME)
        start_perf = time.perf_counter()
        outcome = "success"
        usage_dict: dict | None = None
        failed = False
        # The SDK requires AsyncIterable prompts whenever can_use_tool is
        # set (client.py:54-60 enforces this).  Wrap the queued string into
        # a one-shot async iterable in that branch; otherwise preserve the
        # legacy string-prompt path for standalone agents.
        prompt_arg: str | AsyncIterable[dict[str, Any]]
        if self._can_use_tool is None:
            prompt_arg = prompt
        else:
            prompt_arg = _single_user_message_stream(prompt)
        with tracer.start_as_current_span(
            "harness.llm.call",
            attributes={
                "harness.backend": "claude",
                "harness.model": self.model,
                "harness.nick": self._nick,
            },
        ):
            try:
                async for message in query(
                    prompt=prompt_arg,
                    options=self._make_options(),
                ):
                    if isinstance(message, ResultMessage):
                        self._handle_result_message(message)
                        # Extract usage if exposed by SDK; some ResultMessages have it
                        u = getattr(message, "usage", None)
                        if u is not None:
                            usage_dict = _extract_usage(u)
                    elif isinstance(message, AssistantMessage):
                        await self._handle_assistant_message(message)
            except Exception:
                outcome = "error"
                failed = True
                logger.exception("SDK session turn error")
                if not self._stopping and self.on_exit:
                    await self.on_exit(1)
        duration_ms = (time.perf_counter() - start_perf) * 1000.0
        if self._metrics is not None:
            record_llm_call(
                self._metrics,
                backend="claude",
                model=self.model,
                nick=self._nick,
                usage=usage_dict,
                duration_ms=duration_ms,
                outcome=outcome,
            )
        # Feed per-turn input-token usage to the context watcher (daemon-side).
        if not failed and self.on_usage is not None:
            tokens_input = usage_dict.get("tokens_input") if usage_dict else None
            await self.on_usage(tokens_input)
        if failed:
            return False
        return True

    async def _run_loop(self) -> None:
        """Main session loop: run turns, process prompt queue between turns."""
        try:
            while not self._stopping:
                prompt = await self._prompt_queue.get()
                if self._stopping:
                    break
                if not prompt:
                    continue
                if not await self._process_turn(prompt):
                    return

        except asyncio.CancelledError:
            raise

        if self.on_exit:
            await self.on_exit(0)

    @staticmethod
    def _assistant_to_dict(message: AssistantMessage) -> dict[str, Any]:
        """Convert an AssistantMessage to a dict for supervisor observation."""
        return {
            "type": "assistant",
            "model": message.model,
            "content": [_content_block_to_dict(b) for b in message.content],
        }
