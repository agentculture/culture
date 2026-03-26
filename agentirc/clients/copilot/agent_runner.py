"""Copilot agent runner — manages a GitHub Copilot SDK session."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class CopilotAgentRunner:
    """Manages a GitHub Copilot SDK session for the agentirc daemon."""

    def __init__(
        self,
        model: str,
        directory: str,
        system_prompt: str = "",
        skill_directories: list[str] | None = None,
        on_exit: Callable[[int], Awaitable[None]] | None = None,
        on_message: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self.model = model
        self.directory = directory
        self.system_prompt = system_prompt
        self.skill_directories = skill_directories or []
        self.on_exit = on_exit
        self.on_message = on_message

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
        self._isolated_home = tempfile.mkdtemp(prefix="agentirc-copilot-")
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
                self.model, self._session_id,
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
            try:
                await self._task
            except asyncio.CancelledError:
                pass
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

    async def _prompt_loop(self) -> None:
        """Process queued prompts one at a time using send_and_wait."""
        try:
            while self._running:
                text = await self._prompt_queue.get()
                if not self._running or self._session is None:
                    break

                try:
                    response = await self._session.send_and_wait(
                        text, timeout=120.0
                    )

                    # Extract text from SDK response
                    content_text = ""
                    if response is not None:
                        if hasattr(response, "data") and hasattr(response.data, "content"):
                            content_text = response.data.content or ""
                        elif isinstance(response, dict):
                            content_text = response.get("data", {}).get("content", "")

                    if content_text and self.on_message:
                        msg_dict = {
                            "type": "assistant",
                            "model": self.model,
                            "content": [{"type": "text", "text": content_text}],
                        }
                        await self.on_message(msg_dict)

                except Exception:
                    logger.exception("Copilot session turn error")
                    if not self._stopping:
                        self._running = False
                        if self.on_exit:
                            await self.on_exit(1)
                        return

        except asyncio.CancelledError:
            pass

        if not self._stopping and self.on_exit:
            await self.on_exit(0)
