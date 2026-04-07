"""Shared constants for culture CLI modules."""

import os

from culture.bots.config import BOT_CONFIG_FILE  # noqa: F401

DEFAULT_CONFIG = os.path.expanduser("~/.culture/agents.yaml")
LOG_DIR = os.path.expanduser("~/.culture/logs")

_CONFIG_HELP = "Config file path"
_SERVER_NAME_HELP = "Server name"
_BOT_NAME_HELP = "Bot name"

DEFAULT_CHANNEL = "#general"
NO_AGENTS_MSG = "No agents configured"
CULTURE_DIR = ".culture"
AGENTS_YAML = "agents.yaml"
