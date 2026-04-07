"""Copilot agent runner — manages a GitHub Copilot SDK session."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from typing import Any, Awaitable, Callable

from culture.aio import maybe_await

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
    ) -> None:
        self.model = model
        self.directory = directory
        self.system_prompt = system_prompt
        self.skill_directories = skill_directories or []
        self.on_exit = on_exit
        self.on_message = on_message
        self.on_turn_error = on_turn_error

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

        # Isolate from host config
        self._isolated_home = tempfile.mkdtemp(prefix="culture-copilot-")
        isolated_env = {**os.environ, "HOME": self._isolated_home}
        isolated_env.pop("XDG_CONFIG_HOME", None)

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
        try:
            response = await self._session.send_and_wait(text, timeout=120.0)
            await self._handle_turn_response(response)
        except Exception:
            return await self._handle_turn_error()
        return False

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
