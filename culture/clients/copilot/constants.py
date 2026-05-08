"""Copilot backend timeout constants. See `culture/_constants.py` for cross-backend defaults."""

from __future__ import annotations

from culture._constants import (  # noqa: F401  # pylint: disable=unused-import
    DEFAULT_TURN_TIMEOUT_SECONDS,
)

# github-copilot-sdk's own `session.send_and_wait` budget. The outer
# turn-timeout wraps this if the SDK ignores its own.
INNER_SDK_TIMEOUT_SECONDS: float = 120.0
