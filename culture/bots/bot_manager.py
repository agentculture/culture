"""BotManager — central registry for bot lifecycle and webhook dispatch."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from opentelemetry import trace as _otel_trace

from culture.bots.bot import Bot
from culture.bots.config import (
    BOT_CONFIG_FILE,
    BOTS_DIR,
    BotConfig,
    load_bot_config,
    save_bot_config,
)
from culture.bots.filter_dsl import FilterParseError, compile_filter, evaluate

_FILTER_ERRORS = (FilterParseError, TypeError)

if TYPE_CHECKING:
    from culture.agentirc.ircd import IRCd

logger = logging.getLogger(__name__)


class BotManager:
    """Loads, starts, stops, and dispatches webhooks to bots."""

    def __init__(self, server: IRCd):
        self.server = server
        self.bots: dict[str, Bot] = {}  # name -> Bot

    async def load_bots(self) -> None:
        """Scan ~/.culture/bots/ and load all bot definitions."""
        if not BOTS_DIR.is_dir():
            return

        for bot_dir in sorted(BOTS_DIR.iterdir()):
            yaml_path = bot_dir / BOT_CONFIG_FILE
            if not yaml_path.is_file():
                continue
            try:
                config = load_bot_config(yaml_path)
                if config.archived:
                    logger.info("Skipping archived bot %s", config.name)
                    continue
                # Compile event filter at load time
                if config.trigger_type == "event" and config.event_filter:
                    try:
                        config._compiled_filter = compile_filter(config.event_filter)
                    except _FILTER_ERRORS as exc:
                        logger.error("Bot %s has invalid filter, skipping: %s", config.name, exc)
                        continue
                bot = Bot(config, self.server)
                self.bots[config.name] = bot
                await bot.start()
                logger.info("Loaded bot %s", config.name)
            except Exception:
                logger.exception("Failed to load bot from %s", bot_dir)

    def register_bot(self, config: BotConfig) -> Bot:
        """Register a bot from config (used by tests and system bot loader)."""
        if config.trigger_type == "event" and config.event_filter:
            try:
                config._compiled_filter = compile_filter(config.event_filter)
            except _FILTER_ERRORS as exc:
                raise ValueError(f"bot {config.name} has invalid filter: {exc}") from exc
        bot = Bot(config, self.server)
        self.bots[config.name] = bot
        return bot

    async def _try_start_bot(self, bot: Bot) -> bool:
        """Lazily start a bot on first matching event. Returns True if ready."""
        if bot.active:
            return True
        if getattr(bot, "_starting", False):
            return False
        bot._starting = True  # type: ignore[attr-defined]
        try:
            await bot.start()
            return True
        except Exception:
            logger.exception("Bot %s failed to start", bot.config.name)
            return False
        finally:
            bot._starting = False  # type: ignore[attr-defined]

    async def on_event(self, event) -> None:
        """Evaluate event-triggered bots against an event and dispatch matches."""
        # Snapshot: handle() may call emit_event() which re-enters on_event().
        ctx = {
            "type": event.type.value if hasattr(event.type, "value") else str(event.type),
            "channel": event.channel,
            "nick": event.nick,
            "data": dict(event.data),
        }
        for bot in list(self.bots.values()):
            if self._matches_event(bot, ctx):
                await self._dispatch_to_bot(bot, ctx)

    def _matches_event(self, bot: Bot, ctx: dict) -> bool:
        """True iff `bot` is event-triggered and its filter accepts `ctx`."""
        cfg = bot.config
        if cfg.trigger_type != "event":
            return False
        compiled = getattr(cfg, "_compiled_filter", None)
        if compiled is None:
            return False
        try:
            return bool(evaluate(compiled, ctx))
        except Exception:
            logger.exception("Filter evaluation failed for bot %s", cfg.name)
            return False

    async def _dispatch_to_bot(self, bot: Bot, ctx: dict) -> None:
        """Lazily start the bot and run handle() inside a bot.event.dispatch span."""
        if not await self._try_start_bot(bot):
            return
        cfg = bot.config
        event_type_str = ctx["type"]
        with _otel_trace.get_tracer("culture.agentirc").start_as_current_span(
            "bot.event.dispatch",
            attributes={"bot.name": cfg.name, "event.type": event_type_str},
        ) as span:
            outcome = "success"
            try:
                await bot.handle({"event": ctx})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                outcome = "error"
                span.set_status(_otel_trace.StatusCode.ERROR, str(exc))
                logger.exception("Bot %s handle() failed for event %s", cfg.name, ctx["type"])
            finally:
                self.server.metrics.bot_invocations.add(
                    1,
                    {
                        "bot": cfg.name,
                        "event.type": event_type_str,
                        "outcome": outcome,
                    },
                )

    def load_system_bots(self) -> None:
        """Discover and register system bots from the package."""
        from culture.bots.system import discover_system_bots

        server_name = self.server.config.name if self.server else "unknown"
        server_config = {}
        if self.server:
            raw = getattr(self.server.config, "system_bots", None)
            if raw:
                server_config = {"system_bots": raw}
        for cfg in discover_system_bots(server_name, server_config):
            if cfg.name in self.bots:
                logger.info("Skipping system bot %s — name already registered", cfg.name)
                continue
            try:
                self.register_bot(cfg)
            except Exception:
                logger.exception("Failed to register system bot %s", cfg.name)

    async def create_bot(self, config: BotConfig) -> Bot:
        """Create a new bot: write config to disk and start it."""
        bot_dir = BOTS_DIR / config.name
        save_bot_config(bot_dir / BOT_CONFIG_FILE, config)

        bot = Bot(config, self.server)
        self.bots[config.name] = bot
        await bot.start()
        return bot

    async def start_bot(self, name: str) -> None:
        """Start an existing stopped bot."""
        bot = self.bots.get(name)
        if not bot:
            # Try loading from disk
            yaml_path = BOTS_DIR / name / BOT_CONFIG_FILE
            if not yaml_path.is_file():
                raise ValueError(f"Bot {name!r} not found")
            config = load_bot_config(yaml_path)
            bot = Bot(config, self.server)
            self.bots[name] = bot

        await bot.start()

    async def stop_bot(self, name: str) -> None:
        """Stop a running bot."""
        bot = self.bots.get(name)
        if not bot:
            raise ValueError(f"Bot {name!r} not found")
        await bot.stop()

    async def stop_all(self) -> None:
        """Stop all active bots."""
        for bot in list(self.bots.values()):
            try:
                await bot.stop()
            except Exception:
                logger.exception("Failed to stop bot %s", bot.name)

    def get_bot(self, name: str) -> Bot | None:
        return self.bots.get(name)

    def list_bots(self, owner: str | None = None) -> list[Bot]:
        """List bots, optionally filtered by owner."""
        bots = self.bots.values()
        if owner:
            bots = [b for b in bots if b.config.owner == owner]
        return sorted(bots, key=lambda b: b.name)

    async def dispatch(self, bot_name: str, payload: dict) -> str:
        """Route an incoming webhook payload to the named bot.

        Returns the rendered message text.
        Raises ValueError if bot not found, RuntimeError if bot not active.
        """
        bot = self.bots.get(bot_name)
        if not bot:
            raise ValueError(f"Bot {bot_name!r} not found")
        if not bot.active:
            raise RuntimeError(f"Bot {bot_name!r} is not active")
        return await bot.handle(payload)
