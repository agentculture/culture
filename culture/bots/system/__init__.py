"""System bot loader."""

from __future__ import annotations

import logging
from pathlib import Path

from culture.bots.config import load_bot_config
from culture.constants import SYSTEM_USER_PREFIX

logger = logging.getLogger(__name__)


def discover_system_bots(server_name: str, config: dict | None = None) -> list:
    """Return BotConfigs for enabled system bots."""
    root = Path(__file__).parent
    found = []
    sb_config = (config or {}).get("system_bots", {})
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        yaml_path = entry / "bot.yaml"
        if not yaml_path.is_file():
            continue
        enabled = sb_config.get(entry.name, {}).get("enabled", True)
        if not enabled:
            continue
        try:
            cfg = load_bot_config(yaml_path)
        except Exception:
            logger.warning("Failed to load system bot %s", entry.name, exc_info=True)
            continue
        cfg.name = f"{SYSTEM_USER_PREFIX}{server_name}-{entry.name}"
        cfg.owner = "system"
        found.append(cfg)
    return found
