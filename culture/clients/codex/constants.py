"""Codex backend timeout constants.

Cross-backend defaults are imported from ``culture._constants``;
codex-specific values live here. Step one toward YAML-driven runtime
config — call sites import names from this module instead of carrying
literals.
"""

from __future__ import annotations

from culture._constants import DEFAULT_TURN_TIMEOUT_SECONDS

__all__ = [
    "DEFAULT_TURN_TIMEOUT_SECONDS",
    "INNER_REQUEST_TIMEOUT_SECONDS",
    "PROCESS_TERMINATE_GRACE_SECONDS",
    "PROCESS_KILL_GRACE_SECONDS",
]


# Per JSON-RPC request timeout in ``_send_request``. SDK-tuned for the
# codex app-server's expected response time; the outer turn-timeout is
# the safety net if this misfires.
INNER_REQUEST_TIMEOUT_SECONDS: int = 30

# Time the runner waits after SIGTERM before escalating to SIGKILL.
PROCESS_TERMINATE_GRACE_SECONDS: int = 5

# Time the runner waits after SIGKILL before giving up on subprocess
# exit (returns -1).
PROCESS_KILL_GRACE_SECONDS: int = 1
