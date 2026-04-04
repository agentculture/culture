"""Companion HTTP server for receiving inbound webhook POSTs."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from agentirc.bots.bot_manager import BotManager

logger = logging.getLogger(__name__)


class HttpListener:
    """Lightweight HTTP server that routes webhook POSTs to bots."""

    def __init__(self, bot_manager: BotManager, host: str, port: int):
        self.bot_manager = bot_manager
        self.host = host
        self.port = port
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        self._app = web.Application()
        self._app.router.add_get("/health", self._handle_health)
        self._app.router.add_post("/{bot_name}", self._handle_webhook)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        logger.info("Webhook HTTP listener started on %s:%d", self.host, self.port)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            self._app = None

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        bot_name = request.match_info["bot_name"]

        # Parse JSON body
        try:
            payload = await request.json()
        except (json.JSONDecodeError, Exception):
            return web.json_response(
                {"error": "invalid JSON"},
                status=400,
            )

        # Dispatch to bot
        try:
            message = await self.bot_manager.dispatch(bot_name, payload)
            return web.json_response({"ok": True, "message": message})
        except ValueError:
            return web.json_response(
                {"error": "bot not found"},
                status=404,
            )
        except RuntimeError:
            return web.json_response(
                {"error": "bot not active"},
                status=503,
            )
        except Exception:
            logger.exception("Webhook handler error for bot %s", bot_name)
            return web.json_response(
                {"error": "internal error"},
                status=500,
            )
