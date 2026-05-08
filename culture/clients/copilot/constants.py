"""Copilot backend timeout constants.

Cross-backend defaults are imported from ``culture._constants``;
copilot-specific values live here. Step one toward YAML-driven
runtime config — call sites import names from this module instead of
carrying literals.
"""

from __future__ import annotations

from culture._constants import DEFAULT_TURN_TIMEOUT_SECONDS

__all__ = ["DEFAULT_TURN_TIMEOUT_SECONDS", "INNER_SDK_TIMEOUT_SECONDS"]


# The github-copilot-sdk's own per-call timeout passed to
# ``session.send_and_wait``. SDK-tuned for typical Copilot turn
# duration; the outer turn-timeout is the safety net if the SDK
# ignores its own.
INNER_SDK_TIMEOUT_SECONDS: float = 120.0
