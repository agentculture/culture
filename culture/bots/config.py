"""Bot configuration dataclasses and YAML loading."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

BOTS_DIR = Path(os.path.expanduser("~/.culture/bots"))
BOT_CONFIG_FILE = "bot.yaml"


@dataclass
class BotConfig:
    """Configuration for a single bot."""

    name: str = ""
    owner: str = ""
    description: str = ""
    created: str = ""
    trigger_type: str = "webhook"
    channels: list[str] = field(default_factory=list)
    dm_owner: bool = False
    mention: str | None = None
    template: str | None = None
    fallback: str = "json"
    archived: bool = False
    archived_at: str = ""
    archived_reason: str = ""

    @property
    def has_handler(self) -> bool:
        """Whether a custom handler.py exists for this bot."""
        bot_dir = BOTS_DIR / self.name
        return (bot_dir / "handler.py").is_file()


def load_bot_config(path: Path) -> BotConfig:
    """Load a bot config from a bot.yaml file."""
    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    bot_section = raw.get("bot", {})
    trigger_section = raw.get("trigger", {})
    output_section = raw.get("output", {})

    return BotConfig(
        name=bot_section.get("name", ""),
        owner=bot_section.get("owner", ""),
        description=bot_section.get("description", ""),
        created=bot_section.get("created", ""),
        trigger_type=trigger_section.get("type", "webhook"),
        channels=output_section.get("channels", []),
        dm_owner=output_section.get("dm_owner", False),
        mention=output_section.get("mention"),
        template=output_section.get("template"),
        fallback=output_section.get("fallback", "json"),
        archived=bot_section.get("archived", False),
        archived_at=bot_section.get("archived_at", ""),
        archived_reason=bot_section.get("archived_reason", ""),
    )


def save_bot_config(path: Path, config: BotConfig) -> None:
    """Serialize a BotConfig to YAML and write atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)

    bot_section = {
        "name": config.name,
        "owner": config.owner,
        "description": config.description,
        "created": config.created,
    }
    if config.archived:
        bot_section["archived"] = config.archived
        bot_section["archived_at"] = config.archived_at
        bot_section["archived_reason"] = config.archived_reason

    data = {
        "bot": bot_section,
        "trigger": {
            "type": config.trigger_type,
        },
        "output": {
            "channels": config.channels,
            "dm_owner": config.dm_owner,
            "mention": config.mention,
            "template": config.template,
            "fallback": config.fallback,
        },
    }

    yaml_str = yaml.dump(data, default_flow_style=False)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        suffix=".yaml.tmp",
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(yaml_str)
        os.replace(tmp_path, str(path))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
