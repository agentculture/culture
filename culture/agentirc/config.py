"""Re-export shim for the IRCd config dataclasses.

The dataclass definitions live in `agentirc.config` (the published
`agentirc-cli` PyPI package). This module re-exports them so
existing `from culture.agentirc.config import ...` call sites keep
working during the Track A migration. New code should import from
`agentirc.config` directly.

Removed alongside the rest of `culture/agentirc/` in Phase A3.
"""

from agentirc.config import LinkConfig, ServerConfig, TelemetryConfig

__all__ = ["LinkConfig", "ServerConfig", "TelemetryConfig"]
