"""Claude backend timeout constants.

Cross-backend defaults are imported from ``culture._constants``;
claude-specific values live here. Step one toward YAML-driven runtime
config — call sites import names from this module instead of carrying
literals.
"""

from __future__ import annotations

from culture._constants import DEFAULT_TURN_TIMEOUT_SECONDS

__all__ = ["DEFAULT_TURN_TIMEOUT_SECONDS", "STOP_GRACE_SECONDS"]


# Time AgentRunner.stop() waits for the run-loop to finish after
# enqueueing the sentinel. Beyond this, the task is cancelled.
STOP_GRACE_SECONDS: float = 5.0
