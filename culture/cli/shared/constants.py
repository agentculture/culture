"""Shared constants for culture CLI modules.

v9.1.5: ``DEFAULT_CONFIG`` / ``DEFAULT_SERVER_CONFIG`` / ``LEGACY_CONFIG``
were previously module-level strings computed at import time via
``os.path.expanduser("~/.culture/server.yaml")``. That bypassed the
``CULTURE_HOME`` env var test suites rely on for isolation — when a
test set ``CULTURE_HOME=<tmp>`` then invoked the CLI, any handler
whose argparse default was one of these constants wrote to the
real ``~/.culture/server.yaml`` instead of the tmp dir. A passing
boundary-acceptance test (``test_spawn_at_exact_limit_accepts``)
silently corrupted the operator's live ``server.yaml`` because of
this — and the corruption surfaced only when the operator opened
Mission Control and saw a phantom worker.

The fix is to resolve the path lazily via :func:`default_config_path`
(which honors ``CULTURE_HOME``) at handler-entry time. The legacy
constants are kept (as the same dynamic value) so any external
callers reading them continue to work — but argparse defaults
should now use ``None`` + handler-side resolution via
``default_config_path()``, NOT the constants.
"""

import os
import stat

from culture.bots.config import BOT_CONFIG_FILE  # noqa: F401


def culture_home() -> str:
    """Resolve the culture home dir, honoring ``CULTURE_HOME``.

    Duplicates ``culture/clients/_perm_broker.py::culture_home`` to
    avoid pulling the broker module into the CLI's import graph.
    Keep the two implementations in sync if either changes.
    """
    return os.environ.get("CULTURE_HOME") or os.path.expanduser("~/.culture")


def default_config_path() -> str:
    """Resolve the server.yaml path lazily.

    Argparse defaults must use ``None`` and dispatch handlers must
    fall back to this function so ``CULTURE_HOME`` isolation
    actually works at test time.
    """
    return os.path.join(culture_home(), "server.yaml")


def default_legacy_config_path() -> str:
    """``agents.yaml`` legacy path, honoring ``CULTURE_HOME``."""
    return os.path.join(culture_home(), "agents.yaml")


def default_log_dir() -> str:
    """``logs/`` directory, honoring ``CULTURE_HOME``."""
    return os.path.join(culture_home(), "logs")


# Backwards-compat: keep the legacy names readable as MODULE-LEVEL
# functions that act like properties via __getattr__. Anything that
# was doing ``from .constants import DEFAULT_CONFIG`` will now get
# the lazily-resolved value on every read. Pre-v9.1.5 callers that
# captured the value into a local would have to be updated; the
# critical caller class (argparse defaults) IS updated in this PR.


def __getattr__(name: str) -> str:
    """Module-level dynamic attribute for the legacy constants.

    Per PEP 562, ``module.__getattr__`` is consulted when an attribute
    is not found via normal lookup. We use it to make the historical
    string constants act like dynamic properties — every read honors
    the current ``CULTURE_HOME``. This means imports like
    ``from .constants import DEFAULT_CONFIG`` resolve at IMPORT time
    (still potentially stale if CULTURE_HOME changes after import),
    but at least the value is recomputed once per process startup
    instead of being baked at the constants-module level.
    """
    if name in {"DEFAULT_CONFIG", "DEFAULT_SERVER_CONFIG"}:
        return default_config_path()
    if name == "LEGACY_CONFIG":
        return default_legacy_config_path()
    if name == "LOG_DIR":
        return default_log_dir()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_CONFIG_HELP = "Config file path"
_SERVER_NAME_HELP = "Server name"
_BOT_NAME_HELP = "Bot name"

DEFAULT_CHANNEL = "#general"
NO_AGENTS_MSG = "No agents configured"
CULTURE_DIR = ".culture"
AGENTS_YAML = "agents.yaml"


def culture_runtime_dir() -> str:
    """Return a safe directory for culture daemon sockets.

    Uses ``$XDG_RUNTIME_DIR`` when available (user-private, set by
    systemd/logind).  Otherwise creates a user-private subdirectory
    under the system temp dir so sockets never live in a publicly
    writable location.

    .. note::
        This logic is duplicated in ``culture/clients/_socket_link.py``
        (``_cli_runtime_dir``) to avoid a CLI import dependency in the
        daemon.  If you change the resolution order here, update that
        copy too.
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
