"""Codex backend timeout constants. See `culture/_constants.py` for cross-backend defaults."""

from __future__ import annotations

from culture._constants import (  # noqa: F401  # pylint: disable=unused-import
    DEFAULT_TURN_TIMEOUT_SECONDS,
)

# Per JSON-RPC request budget in `_send_request` (codex app-server). The outer
# turn-timeout wraps this if it misfires.
INNER_REQUEST_TIMEOUT_SECONDS: int = 30

# SIGTERM grace before SIGKILL escalation in `_terminate_process`.
PROCESS_TERMINATE_GRACE_SECONDS: int = 5

# SIGKILL grace before giving up (`_await_process_exit` returns -1).
PROCESS_KILL_GRACE_SECONDS: int = 1
