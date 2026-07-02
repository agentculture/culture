"""culture_core.agentirc is a stable engine re-export package.

- ``config.py`` re-exports ``ServerConfig`` / ``LinkConfig`` /
  ``TelemetryConfig`` from the ``agentirc.config`` dependency.
- This ``__init__`` re-exports the same three symbols for
  ``culture_core`` call sites.
- The package also carries ``CLAUDE.md`` + ``docs/`` (the AgentIRC
  docs CI copies to ``docs/agentirc/``).
"""

from culture_core.agentirc.config import LinkConfig, ServerConfig, TelemetryConfig

__all__ = ["LinkConfig", "ServerConfig", "TelemetryConfig"]
