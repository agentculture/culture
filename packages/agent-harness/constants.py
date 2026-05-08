"""Agent harness template — timeout constants.

This file is part of the citation reference at
``packages/agent-harness/``. When a new backend is created by copying
this directory into ``culture/clients/<backend>/``, this file becomes
that backend's source of timeout constants. Each cited backend owns
its copy and customizes the values.

Self-contained on purpose — no imports from ``culture._constants``
because the harness template predates and seeds new backends, not the
other way round. (See project CLAUDE.md: "Code in ``packages/`` is
reference implementation — copied, not imported.")
"""

from __future__ import annotations

__all__ = ["DEFAULT_TURN_TIMEOUT_SECONDS", "STOP_GRACE_SECONDS"]


# Outer per-turn safety-net timeout for the SDK call. ``0`` (or any
# non-positive number) disables the wrap. New backends should mirror
# this default unless their SDK has different latency expectations.
DEFAULT_TURN_TIMEOUT_SECONDS: float = 600.0

# Time the runner's ``stop()`` waits for the run-loop to finish after
# enqueueing the sentinel.
STOP_GRACE_SECONDS: float = 5.0
