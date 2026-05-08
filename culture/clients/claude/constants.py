"""Claude backend timeout constants. See `culture/_constants.py` for cross-backend defaults."""

from __future__ import annotations

from culture._constants import (  # noqa: F401  # pylint: disable=unused-import
    DEFAULT_TURN_TIMEOUT_SECONDS,
)

# AgentRunner.stop() grace period before the run-loop task is cancelled.
STOP_GRACE_SECONDS: float = 5.0
