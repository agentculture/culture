from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from dataclasses import dataclass
from typing import Awaitable, Callable

from culture.clients.codex.config import WebhookConfig

logger = logging.getLogger(__name__)


@dataclass
class AlertEvent:
    event_type: str
    nick: str
    message: str


class WebhookClient:
    def __init__(
        self, config: WebhookConfig, irc_send: Callable[[str, str], Awaitable[None]] | None = None
    ):
        self.config = config
        self.irc_send = irc_send

    async def fire(self, event: AlertEvent) -> None:
        if event.event_type not in self.config.events:
            return
        tasks = []
        if self.irc_send:
            tasks.append(self._send_irc(event))
        if self.config.url:
            tasks.append(self._send_http(event))
        if tasks:
            await asyncio.gather(*tasks)

    async def _send_irc(self, event: AlertEvent) -> None:
        try:
            await self.irc_send(self.config.irc_channel, event.message)
        except Exception:
            logger.exception("Failed to send IRC alert")

    async def _send_http(self, event: AlertEvent) -> None:
        try:
            await self._http_post(event)
        except Exception:
            logger.exception("Webhook POST failed to %s", self.config.url)

    async def _http_post(self, event: AlertEvent) -> None:
        payload = json.dumps({"content": event.message}).encode()

        def _post():
            req = urllib.request.Request(
                self.config.url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()

        await asyncio.to_thread(_post)
