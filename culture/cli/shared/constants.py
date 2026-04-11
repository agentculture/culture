"""Shared constants for culture CLI modules."""

import os
import stat

from culture.bots.config import BOT_CONFIG_FILE  # noqa: F401

DEFAULT_CONFIG = os.path.expanduser("~/.culture/server.yaml")
LOG_DIR = os.path.expanduser("~/.culture/logs")

_CONFIG_HELP = "Config file path"
_SERVER_NAME_HELP = "Server name"
_BOT_NAME_HELP = "Bot name"

DEFAULT_CHANNEL = "#general"
NO_AGENTS_MSG = "No agents configured"
CULTURE_DIR = ".culture"
AGENTS_YAML = "agents.yaml"

DEFAULT_SERVER_CONFIG = os.path.expanduser("~/.culture/server.yaml")
LEGACY_CONFIG = os.path.expanduser("~/.culture/agents.yaml")


def culture_runtime_dir() -> str:
    """Return a safe directory for culture daemon sockets.

    Uses ``$XDG_RUNTIME_DIR`` when available (user-private, set by
    systemd/logind).  Otherwise creates a user-private subdirectory
    under the system temp dir so sockets never live in a publicly
    writable location.
    """
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return xdg
    fallback = os.path.join(
        os.path.expanduser("~"),
        ".culture",
        "run",
    )
    os.makedirs(fallback, mode=0o700, exist_ok=True)
    # Enforce permissions even if the directory already existed
    os.chmod(fallback, stat.S_IRWXU)
    return fallback
