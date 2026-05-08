"""Project-wide constants used across backends and the YAML schema.

Cross-backend defaults live here. Backend-specific timeouts (e.g.
copilot's inner SDK budget, codex's JSON-RPC request budget) live in
each backend's ``culture/clients/<backend>/constants.py`` so the
citation pattern keeps each backend self-contained.

These names are the first step toward a YAML-driven runtime config:
once the constants are addressable by name everywhere, the loader can
override them from ``~/.culture/server.yaml`` or a sibling
``timeouts.yaml`` without touching call sites.
"""

from __future__ import annotations

# Outer per-turn safety-net timeout for the SDK call in any backend's
# ``agent_runner.py``. On expiry the runner records ``outcome=timeout``
# and triggers crash-recovery via ``on_exit(1)`` (claude/copilot) or
# subprocess termination (codex/acp). ``0`` (or any non-positive
# number) disables the wrap.
DEFAULT_TURN_TIMEOUT_SECONDS: float = 600.0
