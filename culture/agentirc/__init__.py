"""A1 re-export shim — kept through the 9.x line, removed in 10.0.0.

After Phase A3 (culture 9.0.0) this directory holds only:

- ``config.py`` — re-exports ``ServerConfig`` / ``LinkConfig`` /
  ``TelemetryConfig`` from the public ``agentirc.config`` module
  (the A1 shim, introduced in culture 8.8.0 / PR #309).
- ``__init__.py`` (this file) — re-exports the same three symbols
  so legacy ``from culture.agentirc import ServerConfig`` keeps
  resolving while the in-tree shim survives.
- ``CLAUDE.md`` — pointer to where the IRCd actually lives now
  (``agentirc-cli`` PyPI package).
- ``docs/`` — the AgentIRC docs that CI copies to ``docs/agentirc/``
  for the culture.dev site. Kept here for now; can be revisited.

All other modules (the bundled IRCd) are gone — culture's runtime
imports them from ``agentirc.*`` (`agentirc.ircd`,
`agentirc.virtual_client`, `agentirc.protocol`, etc.) directly.
"""

from culture.agentirc.config import LinkConfig, ServerConfig, TelemetryConfig

__all__ = ["LinkConfig", "ServerConfig", "TelemetryConfig"]
