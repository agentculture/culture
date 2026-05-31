from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import TYPE_CHECKING, Any, AsyncIterable, Awaitable, Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolPermissionContext,
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

# v8.19.25: per-message inactivity timeout for the SDK iteration.
# Real assistant turns (including thinking + long tool calls like full-page
# screenshots) finish well inside this budget; a silence longer than this
# means the SDK pipe is wedged (Stream-closed, hung MCP, dropped subprocess
# stream) and we should surface it as a turn failure so the daemon restarts
# the session. Configurable via CULTURE_SDK_INACTIVITY_TIMEOUT.
SDK_INACTIVITY_TIMEOUT_SECONDS = float(os.environ.get("CULTURE_SDK_INACTIVITY_TIMEOUT", "180"))


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
        on_turn_complete: Callable[[], Awaitable[None]] | None = None,
        on_turn_failed: Callable[[], Awaitable[None]] | None = None,
        metrics: HarnessMetricsRegistry | None = None,
        nick: str = "",
        boss: str = "",
        effort: str = "",
    ) -> None:
        self.model = model
        self.directory = directory
        self.system_prompt = system_prompt
        # v8.19.27: ``effort`` is culture's per-agent thinking-depth tier,
        # surfaced in the agent yaml as ``thinking:`` (legacy name kept for
        # backward compat). Maps directly to the SDK's ClaudeAgentOptions
        # ``effort`` field which the CLI forwards as ``--effort <tier>``.
        # Opus 4.7+ uses adaptive thinking + effort instead of explicit
        # budget_tokens; older models accept --effort gracefully so the
        # mapping is safe across the model fleet. Empty → SDK default.
        self.effort = effort
        self.on_exit = on_exit
        self.on_message = on_message
        self.on_usage = on_usage
        self.on_perm_request = on_perm_request
        # Fires AFTER a turn's `async for query()` loop ends cleanly — i.e.
        # the SDK yielded a final ResultMessage and the session is back in
        # the queue-wait state. Used by the daemon's stall watchdog to
        # detect "looping with no progress": a worker that keeps producing
        # AssistantMessages but never sees a turn complete is stuck in a
        # tool-retry loop (e.g. SDK CLI Stream-closed retries that fail
        # repeatedly without crashing the session).
        self.on_turn_complete = on_turn_complete
        # Companion to on_turn_complete — fires when ``_process_turn`` catches
        # an exception from the SDK ``query()`` loop (e.g. CLIConnectionError /
        # Stream closed). Lets the daemon track consecutive failures so the
        # watchdog can catch intermittent-success retry loops (v8.18.5 finding
        # from context-watch dogfood: alternating fail/Bash-workaround kept
        # v8.18.4's stalled_in_retry_loop silent).
        self.on_turn_failed = on_turn_failed
        self._metrics = metrics
        self._nick = nick
        self._boss = boss

        self._session_id: str | None = None
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._prompt_queue: asyncio.Queue[str] = asyncio.Queue()
        # Pause gate: set means "may process the next turn"; clear means
        # "paused, block before _process_turn". Defaults to set so a freshly
        # started runner is not paused.
        self._unpaused: asyncio.Event = asyncio.Event()
        self._unpaused.set()

        # Permission broker — wired only when a perm-policy/<nick>.yaml exists.
        # Standalone mesh agents (no policy file) keep today's bypassPermissions
        # semantics: can_use_tool=None and string-prompt path preserved.
        if nick and has_policy_file(nick):
            self._broker: PermissionBroker | None = PermissionBroker(
                nick=nick, on_request=on_perm_request, boss=boss
            )
            self._can_use_tool = self._broker.gate
        else:
            self._broker = None
            self._can_use_tool = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def set_paused(self, paused: bool) -> None:
        """Pause or resume turn processing inside _run_loop.

        When paused, the loop blocks AFTER ``_prompt_queue.get()`` and BEFORE
        calling _process_turn — handoff/compact/poll prompts already queued
        sit there until resume, instead of executing while the operator
        believes the worker is halted. Mirrors AgentDaemon._paused so the
        pause is authoritative end-to-end.
        """
        if paused:
            self._unpaused.clear()
        else:
            self._unpaused.set()

    async def start(self, initial_prompt: str = "") -> None:
        """Start the SDK session loop as a background task."""
        self._stopping = False
        if initial_prompt:
            self._prompt_queue.put_nowait(initial_prompt)
        self._task = asyncio.create_task(self._run_loop())
        # Catch the silent-task-death case: if _run_loop ends with an unhandled
        # exception (e.g. one escaping from a callback like on_message /
        # on_usage that lives outside _process_turn's try-except), is_running()
        # returns False but on_exit was never called and the daemon has no
        # signal to restart. The done_callback fires a fallback on_exit(1) so
        # crash recovery still kicks in.
        self._task.add_done_callback(self._on_task_done)

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

    async def _broker_pre_tool_use_hook(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,  # noqa: ARG002 — SDK hook signature
        context: Any,  # noqa: ARG002 — SDK HookContext, currently just abort signal
    ) -> dict[str, Any]:
        """SDK ``PreToolUse`` hook that defers to :class:`PermissionBroker`.

        Hooks are the SDK enforcement primitive that *actually* fires for every
        tool call (verified live during v8.18.1 dogfood: ``can_use_tool`` is
        not invoked for many tools even in ``permission_mode="default"``;
        ``PreToolUse`` hooks are). This wrapper translates between the
        broker's ``gate(tool_name, input, ToolPermissionContext)`` shape and
        the SDK hook contract.

        On allow → ``permissionDecision: "allow"``.
        On deny  → ``permissionDecision: "deny"`` with ``permissionDecisionReason``.

        Broker decisions can take a long time (boss may be slow to approve,
        up to ``_PERM_DECISION_TIMEOUT_SECONDS`` 600s before broker
        synthesises a deny). The HookMatcher timeout in ``_make_options`` is
        set generously to cover the broker's own bound.
        """
        if self._broker is None:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                }
            }
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {}) or {}
        fake_context = ToolPermissionContext(signal=None, suggestions=[])
        try:
            result = await self._broker.gate(tool_name, tool_input, fake_context)
        except Exception:  # noqa: BLE001 — fail closed; broker bug must not allow tool
            logger.exception("Broker gate raised for tool %s; failing closed", tool_name)
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": ("Broker raised; denying for safety. See logs."),
                }
            }
        if isinstance(result, PermissionResultAllow):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                }
            }
        # PermissionResultDeny — pass through the broker's message
        reason = getattr(result, "message", "denied by perm-broker")
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    def _make_options(self) -> ClaudeAgentOptions:
        # Only pass `model` to the SDK when explicitly set. An empty model means
        # "let the SDK pick the current Claude" — that's the inheritance chain
        # the boss/worker stack relies on (no hardcoded model strings in code
        # or yaml; new Claude versions are inherited automatically via the SDK's
        # own default tracking).
        #
        # ``permission_mode`` interaction with ``can_use_tool`` matters here:
        # ``"bypassPermissions"`` literally means "Allow all tools" per the SDK
        # docs (claude_agent_sdk/query.py:58). The CLI binary skips the
        # can_use_tool callback entirely in that mode — so a boss-supervised
        # worker with a wired-up PermissionBroker would have all tool calls
        # silently auto-allowed, defeating the broker, the ceiling, the
        # handoff anchor, the ownership gate, and the perm-gate timeout. Use
        # ``"default"`` ONLY when a broker is wired (the worker has a
        # perm-policy file); standalone agents keep the bypass semantics that
        # have always been their default.
        opts_kwargs: dict[str, Any] = {
            "cwd": self.directory,
            "permission_mode": (
                "default" if self._can_use_tool is not None else "bypassPermissions"
            ),
            "setting_sources": ["user", "project", "local"],
            "can_use_tool": self._can_use_tool,
            "env": self._subprocess_env(),
        }
        if self.model:
            opts_kwargs["model"] = self.model
        if self.effort:
            opts_kwargs["effort"] = self.effort
        # When the broker is wired, install a PreToolUse hook that defers
        # to broker.gate. v8.18.1's permission_mode='default' was necessary
        # but not sufficient — the CLI does not always invoke can_use_tool.
        # Hooks fire for every tool call, so this is the real enforcement
        # primitive. Generous timeout so the broker's own 600s perm-gate
        # bound has slack.
        if self._broker is not None:
            opts_kwargs["hooks"] = {
                "PreToolUse": [
                    HookMatcher(
                        matcher=None,  # match every tool
                        hooks=[self._broker_pre_tool_use_hook],
                        timeout=900.0,
                    )
                ]
            }
        opts = ClaudeAgentOptions(**opts_kwargs)
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
                # v8.19.25: per-message inactivity timeout. Without this, an SDK
                # async iteration that goes silent (Stream-closed, hung
                # tool result, dropped subprocess pipe) holds the daemon
                # in `async for` indefinitely — the daemon-log watchdog
                # observes the silence but cannot act on it. Wrapping the
                # `__anext__` in `wait_for` converts inactivity into the
                # existing `on_turn_failed` + `on_exit(1)` recovery path
                # so the daemon restarts the session cleanly.
                aiter = query(
                    prompt=prompt_arg,
                    options=self._make_options(),
                ).__aiter__()
                while True:
                    try:
                        message = await asyncio.wait_for(
                            aiter.__anext__(),
                            timeout=SDK_INACTIVITY_TIMEOUT_SECONDS,
                        )
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError as exc:
                        raise RuntimeError(
                            f"SDK stream inactivity timeout "
                            f"({SDK_INACTIVITY_TIMEOUT_SECONDS}s with no message)"
                        ) from exc
                    if isinstance(message, ResultMessage):
                        self._handle_result_message(message)
                        # Extract usage if exposed by SDK; some ResultMessages have it
                        u = getattr(message, "usage", None)
                        if u is not None:
                            usage_dict = _extract_usage(u)
                    elif isinstance(message, AssistantMessage):
                        await self._handle_assistant_message(message)
                # The async-for ended without raising → the turn completed
                # cleanly. Signal the daemon so the stall watchdog can
                # distinguish "engaged + completing turns" from "engaged
                # but stuck in a retry loop".
                if self.on_turn_complete is not None:
                    try:
                        await self.on_turn_complete()
                    except Exception:  # noqa: BLE001 — advisory callback; never break the turn loop
                        logger.exception("on_turn_complete callback raised")
            except Exception:
                outcome = "error"
                failed = True
                logger.exception("SDK session turn error")
                # Fire the failed-turn callback BEFORE on_exit. on_exit
                # triggers crash recovery (terminal); on_turn_failed signals
                # a non-fatal turn error so the daemon's watchdog can track
                # consecutive failures even when the session keeps running
                # (e.g. SDK CLI Stream-closed errors that don't propagate
                # to a crash).
                if self.on_turn_failed is not None:
                    try:
                        await self.on_turn_failed()
                    except Exception:  # noqa: BLE001 — advisory; never re-raise
                        logger.exception("on_turn_failed callback raised")
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
        # v8.19.21: append the per-turn token usage to the on-disk
        # ``~/.culture/usage/<nick>.jsonl`` so the dashboard can show
        # "tokens used" per agent + per channel (task) without depending
        # on a running OTLP collector. Best-effort — disk errors are
        # swallowed inside record_turn_usage; we never block the agent
        # loop on usage bookkeeping.
        if not failed and usage_dict is not None:
            from culture.clients._usage import record_turn_usage

            await record_turn_usage(
                self._nick,
                tokens_input=usage_dict.get("tokens_input"),
                tokens_output=usage_dict.get("tokens_output"),
                model=self.model,
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
                # Honor pause AFTER consuming the prompt — already-queued
                # handoff/compact/poll work waits until resume. Without this
                # gate, _paused at the daemon was a half-pause: new mentions
                # were blocked but in-flight queue items still ran.
                await self._unpaused.wait()
                if self._stopping:
                    break
                if not await self._process_turn(prompt):
                    return

        except asyncio.CancelledError:
            raise

        if self.on_exit:
            await self.on_exit(0)

    def _on_task_done(self, task: asyncio.Task) -> None:
        """Fallback exit signal when _run_loop dies with an unhandled exception.

        _process_turn catches its own exceptions and calls on_exit(1) inline,
        so the normal error path is already covered. This guards the
        out-of-band case where an exception escapes _run_loop entirely (e.g.
        from a callback that runs outside the inner try/except, or a bug in
        the queue-get path), leaving the task dead with no on_exit signal.
        """
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is None:
            return  # clean exit; on_exit(0) was already called
        logger.error("AgentRunner._run_loop terminated with unhandled exception", exc_info=exc)
        if self._stopping or self.on_exit is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # event loop is gone — nothing we can do
        loop.create_task(self._fallback_on_exit())

    async def _fallback_on_exit(self) -> None:
        """Best-effort on_exit(1) call when the run loop died unexpectedly."""
        if self.on_exit is None:
            return
        try:
            await self.on_exit(1)
        except Exception:  # noqa: BLE001 — fallback is best-effort
            logger.exception("Fallback on_exit raised; crash recovery may not run")

    @staticmethod
    def _assistant_to_dict(message: AssistantMessage) -> dict[str, Any]:
        """Convert an AssistantMessage to a dict for supervisor observation."""
        return {
            "type": "assistant",
            "model": message.model,
            "content": [_content_block_to_dict(b) for b in message.content],
        }
