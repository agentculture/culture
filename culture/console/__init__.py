"""Textual TUI for the Culture agent mesh — DEPRECATED.

Replaced by ``irc-lens`` exposed via ``culture console``. This package
is left in place for one minor cycle so out-of-tree importers can
migrate; it will be removed in 10.0.

See ``docs/superpowers/specs/2026-05-05-culture-console-design.md``.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "culture/console/ Textual TUI is deprecated and will be removed in "
    "10.0; replaced by irc-lens via 'culture console'",
    DeprecationWarning,
    stacklevel=2,
)
