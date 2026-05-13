"""Bot entity — ties together config, virtual client, and handler logic."""

from __future__ import annotations

import importlib.util
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentirc.protocol import Event, EventType
from opentelemetry import trace as _otel_trace

from culture.bots.config import BOTS_DIR, BotConfig
from culture.bots.template_engine import render_fallback, render_template
from culture.bots.virtual_client import VirtualClient
from culture.constants import EVENT_TYPE_RE

if TYPE_CHECKING:
    from agentirc.ircd import IRCd

logger = logging.getLogger(__name__)


class _DynamicEventType:
    """Lightweight stand-in for EventType when the event type is not in the enum."""

    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = value

    def __str__(self) -> str:
        return self.value


# ---------------------------------------------------------------------------
# Rate-limiter for fires_event — module-level state, 10 events/sec per bot
# ---------------------------------------------------------------------------

_RATE_MAX_PER_SEC = 10
_rate_state: dict[str, list[float]] = {}


def _check_rate(bot_name: str) -> bool:
    """Return True if the bot may fire an event; False if rate-limited."""
    now = time.monotonic()
    window = _rate_state.setdefault(bot_name, [])
    window[:] = [t for t in window if now - t < 1.0]
    if len(window) >= _RATE_MAX_PER_SEC:
        return False
    window.append(now)
    return True


def _render_data_values(data: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Render Jinja2 template strings inside an EmitEventSpec data dict."""
    from jinja2.sandbox import SandboxedEnvironment

    env = SandboxedEnvironment()
    result = {}
    for key, val in data.items():
        if isinstance(val, str):
            try:
                result[key] = env.from_string(val).render(ctx)
            except Exception:
                result[key] = val
        else:
            result[key] = val
    return result


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

        for channel_name in [ch.name for ch in self.virtual_client.channels]:
            await self.virtual_client.part_channel(channel_name)

        self.virtual_client = None
        self.active = False
        logger.info("Bot %s stopped", self.config.name)

    async def handle(self, payload: dict) -> str:
        """Process an incoming webhook payload.

        Returns the rendered message text.
        """
        # Span opens before the active-check so, if handle() is invoked for an
        # inactive bot, the failure still surfaces in tracing with ERROR
        # status and bot.name set instead of only raising.
        with _otel_trace.get_tracer("culture.agentirc").start_as_current_span(
            "bot.run",
            attributes={"bot.name": self.config.name},
        ) as span:
            if not self.active or not self.virtual_client:
                span.set_status(_otel_trace.StatusCode.ERROR, "bot not active")
                raise RuntimeError(f"Bot {self.config.name} is not active")

            message = await self._resolve_message(payload)
            if not message:
                span.set_attribute("bot.run.empty_message", True)
                return ""

            if self.config.mention:
                message = f"@{self.config.mention} {message}"

            await self._deliver(message, payload)
            await self._maybe_fire_event(payload)
            return message

    async def _resolve_message(self, payload: dict) -> str:
        """Render the message from custom handler or template."""
        handler_path = BOTS_DIR / self.config.name / "handler.py"
        if handler_path.is_file():
            result = await self._run_custom_handler(handler_path, payload)
            return "" if result is None else result
        return self._render_message(payload)

    async def _deliver(self, message: str, payload: dict) -> None:
        """Send message to channels and optionally DM the owner."""
        target_channels, dynamic = self._resolve_channels(payload)
        for channel in target_channels:
            if dynamic:
                await self.virtual_client.broadcast_to_channel(channel, message)
            else:
                ch_obj = self.server.channels.get(channel)
                if ch_obj is None or self.virtual_client not in ch_obj.members:
                    await self.virtual_client.join_channel(channel)
                await self.virtual_client.send_to_channel(channel, message)
        if self.config.dm_owner and self.config.owner:
            await self.virtual_client.send_dm(self.config.owner, message)

    def _resolve_channels(self, payload: dict) -> tuple[list[str], bool]:
        """Return (channels, is_dynamic) for message delivery."""
        channels = list(self.config.channels)
        if channels or self.config.trigger_type != "event":
            return channels, False
        event_ctx = payload.get("event", {})
        event_channel = event_ctx.get("channel") if isinstance(event_ctx, dict) else None
        if event_channel:
            return [event_channel], True
        return [], False

    async def _maybe_fire_event(self, payload: dict) -> None:
        """Emit a follow-on event if fires_event is configured on this bot."""
        spec = self.config.fires_event
        if spec is None:
            return

        if not EVENT_TYPE_RE.match(spec.type):
            logger.warning(
                "Bot %s has invalid fires_event.type %r — skipping", self.config.name, spec.type
            )
            return

        if not _check_rate(self.config.name):
            logger.warning("Bot %s rate-limited on fires_event", self.config.name)
            return

        rendered_data = _render_data_values(spec.data, payload)

        # Prefer a real EventType enum member; fall back to dynamic type for custom events.
        try:
            event_type = EventType(spec.type)
        except ValueError:
            event_type = _DynamicEventType(spec.type)

        try:
            await self.server.emit_event(
                Event(
                    type=event_type,
                    channel=None,
                    nick=self.config.name,
                    data=rendered_data,
                )
            )
        except Exception:
            logger.exception("Bot %s failed to emit fires_event", self.config.name)

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
        a trusted directory under ~/.culture/bots/.
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
