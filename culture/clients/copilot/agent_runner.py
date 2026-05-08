"""Copilot agent runner — manages a GitHub Copilot SDK session."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from opentelemetry import trace as _otel_trace

from culture.aio import maybe_await
from culture.clients.copilot.constants import (
    DEFAULT_TURN_TIMEOUT_SECONDS,
    INNER_SDK_TIMEOUT_SECONDS,
)
from culture.clients.copilot.telemetry import _HARNESS_TRACER_NAME, record_llm_call

if TYPE_CHECKING:
    from culture.clients.copilot.telemetry import HarnessMetricsRegistry

logger = logging.getLogger(__name__)


class CopilotAgentRunner:
    """Manages a GitHub Copilot SDK session for the culture daemon."""

    def __init__(
        self,
        model: str,
        directory: str,
        system_prompt: str = "",
        skill_directories: list[str] | None = None,
        on_exit: Callable[[int], Awaitable[None]] | None = None,
        on_message: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        on_turn_error: Callable[[], Awaitable[None] | None] | None = None,
        metrics: HarnessMetricsRegistry | None = None,
        nick: str = "",
        turn_timeout_seconds: float = DEFAULT_TURN_TIMEOUT_SECONDS,
    ) -> None:
        self.model = model
        self.directory = directory
        self.system_prompt = system_prompt
        self.skill_directories = skill_directories or []
        self.on_exit = on_exit
        self.on_message = on_message
        self.on_turn_error = on_turn_error
        self._metrics = metrics
        self._nick = nick
        # Outer safety net wrapping send_and_wait. The inner 120s
        # is the SDK's own timeout for normal slow-but-progressing
        # turns; this fires if the SDK hangs without firing its own.
        self._turn_timeout = turn_timeout_seconds

        self._isolated_home: str | None = None
        self._client: Any = None
        self._session: Any = None
        self._session_id: str | None = None
        self._running = False
        self._stopping = False
        self._prompt_queue: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def is_running(self) -> bool:
        return self._running

    @property
    def session_id(self) -> str | None:
        return self._session_id

    async def start(self, initial_prompt: str = "") -> None:
        """Start the Copilot client and create a session."""
        self._stopping = False

        # Lazy import — github-copilot-sdk is only needed at runtime
        from copilot import CopilotClient, PermissionHandler, SubprocessConfig

        # Isolate data/state dirs to prevent session interference, but
        # preserve HOME and XDG_CONFIG_HOME so copilot finds auth tokens.
        self._isolated_home = tempfile.mkdtemp(prefix="culture-copilot-")
        isolated_env = dict(os.environ)
        isolated_env["XDG_DATA_HOME"] = os.path.join(self._isolated_home, ".local", "share")
        isolated_env["XDG_STATE_HOME"] = os.path.join(self._isolated_home, ".local", "state")

        try:
            # Create and start the CopilotClient (spawns copilot CLI process)
            subprocess_config = SubprocessConfig(cwd=self.directory, env=isolated_env)
            self._client = CopilotClient(config=subprocess_config)
            await self._client.start()

            # Create a session with model and permissions.
            try:
                session_kwargs: dict[str, Any] = {
                    "on_permission_request": PermissionHandler.approve_all,
                    "model": self.model,
                }
                if self.system_prompt:
                    session_kwargs["system_message"] = {"content": self.system_prompt}
                if self.skill_directories:
                    session_kwargs["skill_directories"] = self.skill_directories

                self._session = await self._client.create_session(**session_kwargs)
            except Exception:
                await self._client.stop()
                self._client = None
                raise
            self._session_id = getattr(self._session, "id", None)
            self._running = True

            logger.info(
                "CopilotAgentRunner started (model=%s, session=%s)",
                self.model,
                self._session_id,
            )

            # Start the prompt processing loop
            self._task = asyncio.create_task(self._prompt_loop())

            if initial_prompt:
                await self.send_prompt(initial_prompt)
        except Exception:
            shutil.rmtree(self._isolated_home, ignore_errors=True)
            self._isolated_home = None
            raise

    async def stop(self) -> None:
        """Stop the Copilot session and client."""
        self._stopping = True
        self._running = False

        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

        if self._session:
            try:
                await self._session.destroy()
            except Exception:
                logger.debug("Session destroy error (ignoring)", exc_info=True)
            self._session = None

        if self._client:
            try:
                await self._client.stop()
            except Exception:
                logger.debug("Client stop error (ignoring)", exc_info=True)
            self._client = None

        if self._isolated_home:
            shutil.rmtree(self._isolated_home, ignore_errors=True)
            self._isolated_home = None

    async def send_prompt(self, text: str) -> None:
        """Queue a prompt for the agent."""
        await self._prompt_queue.put(text)

    # ------------------------------------------------------------------
    # Internal: prompt processing loop
    # ------------------------------------------------------------------

    async def _handle_turn_response(self, response) -> None:
        """Convert a Copilot response to a message dict and fire callback."""
        content_text = self._extract_response_text(response)
        if not content_text or not self.on_message:
            return
        msg_dict = {
            "type": "assistant",
            "model": self.model,
            "content": [{"type": "text", "text": content_text}],
        }
        await self.on_message(msg_dict)

    async def _handle_turn_error(self) -> bool:
        """Handle a turn error. Returns True if the loop should exit."""
        logger.exception("Copilot session turn error")
        if self.on_turn_error:
            await maybe_await(self.on_turn_error())
        if self._stopping:
            return False
        self._running = False
        if self.on_exit:
            await self.on_exit(1)
        return True

    async def _execute_single_turn(self, text: str) -> bool:
        """Send one prompt and handle the response.

        Returns True if the loop should exit due to a fatal error.
        """
        start_perf = time.perf_counter()
        outcome = "success"
        tracer = _otel_trace.get_tracer(_HARNESS_TRACER_NAME)
        exit_signal = False
        try:
            with tracer.start_as_current_span(
                "harness.llm.call",
                attributes={
                    "harness.backend": "copilot",
                    "harness.model": self.model,
                    "harness.nick": self._nick,
                },
            ):
                try:
                    # Outer safety net: if send_and_wait's own 120s
                    # timeout doesn't fire (SDK ignores it, hangs
                    # before that, or wedges in a different layer),
                    # this wraps the whole turn so the runner can
                    # restart cleanly.
                    if self._turn_timeout > 0:
                        response = await asyncio.wait_for(
                            self._session.send_and_wait(text, timeout=INNER_SDK_TIMEOUT_SECONDS),
                            timeout=self._turn_timeout,
                        )
                    else:
                        response = await self._session.send_and_wait(
                            text, timeout=INNER_SDK_TIMEOUT_SECONDS
                        )
                    await self._handle_turn_response(response)
                except asyncio.TimeoutError:
                    outcome = "timeout"
                    exit_signal = await self._handle_turn_error()
                except Exception:
                    outcome = "error"
                    exit_signal = await self._handle_turn_error()
        finally:
            duration_ms = (time.perf_counter() - start_perf) * 1000.0
            if self._metrics is not None:
                # Copilot token usage tracking — issue #299 (currently usage=None;
                # github-copilot-sdk does not expose token counts on responses).
                record_llm_call(
                    self._metrics,
                    backend="copilot",
                    model=self.model,
                    nick=self._nick,
                    usage=None,
                    duration_ms=duration_ms,
                    outcome=outcome,
                )
        return exit_signal

    async def _prompt_loop(self) -> None:
        """Process queued prompts one at a time using send_and_wait."""
        try:
            while self._running:
                text = await self._prompt_queue.get()
                if not self._running or self._session is None:
                    break
                if await self._execute_single_turn(text):
                    return
        except asyncio.CancelledError:
            raise

        if not self._stopping and self.on_exit:
            await self.on_exit(0)

    @staticmethod
    def _extract_response_text(response) -> str:
        """Extract text content from a Copilot SDK response."""
        if response is None:
            return ""
        if hasattr(response, "data") and hasattr(response.data, "content"):
            return response.data.content or ""
        if isinstance(response, dict):
            return response.get("data", {}).get("content", "")
        return ""
