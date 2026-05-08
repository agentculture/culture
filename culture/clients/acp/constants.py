"""ACP backend timeout constants. See `culture/_constants.py` for cross-backend defaults."""

from __future__ import annotations

from culture._constants import (  # noqa: F401  # pylint: disable=unused-import
    DEFAULT_TURN_TIMEOUT_SECONDS,
)

# Per JSON-RPC request budget in `_send_request`, used twice by
# `_send_prompt_with_retry` (original + retry).
INNER_REQUEST_TIMEOUT_SECONDS: int = 300

# Subprocess SIGTERM grace before SIGKILL escalation.
PROCESS_TERMINATE_GRACE_SECONDS: int = 5

# Subprocess SIGKILL grace before `_await_process_exit` returns -1.
PROCESS_KILL_GRACE_SECONDS: int = 1
