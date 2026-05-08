"""Agent harness template — timeout constants.

Self-contained on purpose — citation reference at
`packages/agent-harness/`. New backends copy this directory and own
their copy. (Project CLAUDE.md: "Code in `packages/` is reference
implementation — copied, not imported.")
"""

from __future__ import annotations

# Outer per-turn safety-net timeout. `0` disables the wrap.
DEFAULT_TURN_TIMEOUT_SECONDS: float = 600.0

# `stop()` grace before the run-loop task is cancelled.
STOP_GRACE_SECONDS: float = 5.0
