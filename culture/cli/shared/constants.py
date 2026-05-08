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

DEFAULT_SERVER_CONFIG = os.path.expanduser("~/.culture/server.yaml")
LEGACY_CONFIG = os.path.expanduser("~/.culture/agents.yaml")


def culture_runtime_dir() -> str:
    """Return a user-private directory for culture daemon sockets.

    Resolution order:

    1. ``$XDG_RUNTIME_DIR`` when set (Linux/systemd default — already
       user-private at ``/run/user/<uid>``).
    2. ``~/.culture/run/`` otherwise (typical macOS path), created mode
       0700 if missing and re-tightened to 0700 on every call so a
       hand-created or pre-existing dir cannot leak sockets.

    Raises ``RuntimeError`` when neither ``XDG_RUNTIME_DIR`` nor a
    resolvable home directory is available — silently writing a literal
    ``~/.culture/run`` directory in CWD would surprise callers and the
    daemons (which now route through this resolver) would fail at
    socket-bind time anyway.
    """
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return xdg
    home = os.path.expanduser("~")
    if not home or home == "~" or not os.path.isabs(home):
        raise RuntimeError(
            "culture_runtime_dir(): cannot resolve a home directory "
            "(os.path.expanduser('~') returned %r). Set $HOME or "
            "$XDG_RUNTIME_DIR before running culture commands." % home
        )
    fallback = os.path.join(home, ".culture", "run")
    os.makedirs(fallback, mode=0o700, exist_ok=True)
    os.chmod(fallback, stat.S_IRWXU)
    return fallback
