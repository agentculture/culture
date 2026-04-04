"""Bot entity — ties together config, virtual client, and handler logic."""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from agentirc.bots.config import BOTS_DIR, BotConfig
from agentirc.bots.template_engine import render_fallback, render_template
from agentirc.bots.virtual_client import VirtualClient

if TYPE_CHECKING:
    from agentirc.server.ircd import IRCd

logger = logging.getLogger(__name__)


class Bot:
    """A single bot instance managed by the server."""

    def __init__(self, config: BotConfig, server: IRCd):
        self.config = config
        self.server = server
        self.virtual_client: VirtualClient | None = None
        self.active: bool = False

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def webhook_url(self) -> str:
        """Webhook URL always uses localhost since the listener binds to 127.0.0.1."""
        port = self.server.config.webhook_port
        return f"http://localhost:{port}/{self.config.name}"

    async def start(self) -> None:
        """Activate the bot: create virtual client and join channels."""
        if self.active:
            return

        # Check for nick collision
        if self.server.get_client(self.config.name):
            raise ValueError(f"Nick {self.config.name!r} already in use")

        self.virtual_client = VirtualClient(
            nick=self.config.name,
            user=self.config.name.split("-")[-1],
            server=self.server,
        )

        for channel in self.config.channels:
            await self.virtual_client.join_channel(channel)

        self.active = True
        logger.info("Bot %s started", self.config.name)

    async def stop(self) -> None:
        """Deactivate the bot: part channels and remove virtual client."""
        if not self.active or not self.virtual_client:
            return

        for channel_name in list(ch.name for ch in self.virtual_client.channels):
            await self.virtual_client.part_channel(channel_name)

        self.virtual_client = None
        self.active = False
        logger.info("Bot %s stopped", self.config.name)

    async def handle(self, payload: dict) -> str:
        """Process an incoming webhook payload.

        Returns the rendered message text.
        """
        if not self.active or not self.virtual_client:
            raise RuntimeError(f"Bot {self.config.name} is not active")

        # Try custom handler first
        handler_path = BOTS_DIR / self.config.name / "handler.py"
        if handler_path.is_file():
            message = await self._run_custom_handler(handler_path, payload)
            if message is None:
                return ""  # Handler chose to drop this event
        else:
            message = self._render_message(payload)

        if not message:
            return ""

        # Prepend @mention if configured
        if self.config.mention:
            message = f"@{self.config.mention} {message}"

        # Send to configured channels
        for channel in self.config.channels:
            await self.virtual_client.send_to_channel(channel, message)

        # DM the owner if configured
        if self.config.dm_owner and self.config.owner:
            await self.virtual_client.send_dm(self.config.owner, message)

        return message

    def _render_message(self, payload: dict) -> str:
        """Render message using template or fallback."""
        if self.config.template:
            rendered = render_template(self.config.template, payload)
            if rendered is not None:
                return rendered.strip()
        return render_fallback(payload, self.config.fallback)

    async def _run_custom_handler(
        self,
        handler_path: Path,
        payload: dict,
    ) -> str | None:
        """Load and execute a custom handler.py.

        Security: handler_path is always constructed as
        BOTS_DIR / self.config.name / "handler.py" — the bot name
        comes from a validated YAML config on disk, not from user input
        or webhook payloads. This is equivalent to loading a plugin from
        a trusted directory under ~/.agentirc/bots/.
        """
        # Verify the handler is inside the bots directory
        try:
            handler_path.resolve().relative_to(BOTS_DIR.resolve())
        except ValueError:
            logger.error("handler.py path %s is outside bots dir", handler_path)
            return self._render_message(payload)

        try:
            spec = importlib.util.spec_from_file_location(
                f"bot_handler_{self.config.name}",
                handler_path,
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # noqa: S102
            handle_fn = getattr(module, "handle", None)
            if handle_fn is None:
                logger.error("handler.py for %s has no handle() function", self.config.name)
                return self._render_message(payload)
            return await handle_fn(payload, self)
        except Exception:
            logger.exception("Custom handler failed for bot %s", self.config.name)
            return self._render_message(payload)
