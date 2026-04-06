"""BotManager — central registry for bot lifecycle and webhook dispatch."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from culture.bots.bot import Bot
from culture.bots.config import BOTS_DIR, BotConfig, load_bot_config, save_bot_config

if TYPE_CHECKING:
    from culture.server.ircd import IRCd

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
            yaml_path = bot_dir / "bot.yaml"
            if not yaml_path.is_file():
                continue
            try:
                config = load_bot_config(yaml_path)
                if config.archived:
                    logger.info("Skipping archived bot %s", config.name)
                    continue
                bot = Bot(config, self.server)
                self.bots[config.name] = bot
                await bot.start()
                logger.info("Loaded bot %s", config.name)
            except Exception:
                logger.exception("Failed to load bot from %s", bot_dir)

    async def create_bot(self, config: BotConfig) -> Bot:
        """Create a new bot: write config to disk and start it."""
        bot_dir = BOTS_DIR / config.name
        save_bot_config(bot_dir / "bot.yaml", config)

        bot = Bot(config, self.server)
        self.bots[config.name] = bot
        await bot.start()
        return bot

    async def start_bot(self, name: str) -> None:
        """Start an existing stopped bot."""
        bot = self.bots.get(name)
        if not bot:
            # Try loading from disk
            yaml_path = BOTS_DIR / name / "bot.yaml"
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
        bots = list(self.bots.values())
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
