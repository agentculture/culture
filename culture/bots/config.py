"""Bot configuration dataclasses and YAML loading."""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

BOTS_DIR = Path(os.path.expanduser("~/.culture/bots"))
BOT_CONFIG_FILE = "bot.yaml"

# Dedup state: each config file that uses top-level `fires_event` should emit
# the canonical-location notice at most once per process. Keyed by resolved
# path (not bot name) so configs that happen to share a name still each get a
# notice, and malformed YAML names cannot raise TypeError on set membership.
_warned_top_level_fires_event: set[str] = set()


def reset_fires_event_warning_state() -> None:
    """Clear the per-process fires_event dedup set. Tests use this."""
    _warned_top_level_fires_event.clear()


@dataclass
class EmitEventSpec:
    """Specification for an event emitted by a bot after handling a trigger."""

    type: str = ""
    data: dict[str, Any] = field(default_factory=dict)


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
    event_filter: str | None = None
    fires_event: EmitEventSpec | None = None

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

    # Parse optional fires_event block. Canonical location is under `output:`;
    # top-level `fires_event:` is also accepted so configs authored against the
    # intuitive YAML shape still work (see issue #260).
    fires_event_raw = output_section.get("fires_event")
    fires_event_from_top = False
    if not isinstance(fires_event_raw, dict):
        top_level = raw.get("fires_event")
        if isinstance(top_level, dict):
            fires_event_raw = top_level
            fires_event_from_top = True
    fires_event: EmitEventSpec | None = None
    if isinstance(fires_event_raw, dict):
        fe_type = fires_event_raw.get("type", "")
        if not isinstance(fe_type, str):
            fe_type = str(fe_type)
        fe_data = fires_event_raw.get("data")
        if not isinstance(fe_data, dict):
            fe_data = {}
        fires_event = EmitEventSpec(type=fe_type, data=fe_data)
        if fires_event_from_top:
            # Dedup by config path so two bots that happen to share `bot.name`
            # each get their own deprecation notice, and a malformed `name`
            # (e.g. a YAML list) cannot raise TypeError when used as a set key.
            dedup_key = str(Path(path).resolve())
            if dedup_key not in _warned_top_level_fires_event:
                _warned_top_level_fires_event.add(dedup_key)
                bot_name = bot_section.get("name", "<unknown>")
                logger.info(
                    "Bot %s: top-level 'fires_event' accepted; "
                    "canonical location is under 'output:'",
                    bot_name,
                )

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
        event_filter=trigger_section.get("filter"),
        fires_event=fires_event,
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

    trigger_section: dict[str, Any] = {"type": config.trigger_type}
    if config.event_filter:
        trigger_section["filter"] = config.event_filter

    output_section: dict[str, Any] = {
        "channels": config.channels,
        "dm_owner": config.dm_owner,
        "mention": config.mention,
        "template": config.template,
        "fallback": config.fallback,
    }
    if config.fires_event:
        output_section["fires_event"] = {
            "type": config.fires_event.type,
            "data": config.fires_event.data,
        }

    data = {
        "bot": bot_section,
        "trigger": trigger_section,
        "output": output_section,
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
