"""Boss mission persistence: shared module for all backends.

A boss agent's mission is the human's brief — the text of every
``@mention`` the boss receives in its channels. The brief lives in
the IRC channel history, but a daemon restart loses the SDK session
that was holding that context. Without persistence, a restarted boss
gets a fresh prompt with no idea what it was doing.

This module is the **single source of truth** for the boss mission
file: the same persist / load / clear / cap logic is used by every
backend's daemon (claude / codex / acp / copilot + the agent-harness
template). Per CLAUDE.md cite-don't-import + all-backends rule.

The original v8.18.7-fix-pattern-b draft put these methods inline on
``culture/clients/claude/daemon.py``, which (a) violated the
all-backends rule by leaving codex/acp/copilot without persistence,
and (b) had no size cap (Qodo-equivalent finding: file grows
unbounded). This module addresses both.

The file lives at ``~/.culture/mission/<nick>.md``. It is appended
to on every ``@mention`` and read in full on daemon start. A size
cap (``MISSION_MAX_BYTES``) bounds the worst case so a long-running
boss does not balloon its system-prompt context.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
from typing import Any

from culture.clients._perm_broker import mission_path_for

logger = logging.getLogger(__name__)

# Cap for the mission file. When a new append would exceed this, the
# OLDEST entries are dropped (truncated from the head). Boss agents
# care most about recent context; pre-cap entries are still in the
# IRC channel history if anyone needs them.
MISSION_MAX_BYTES = 32 * 1024  # 32 KiB — fits in a normal SDK prompt budget

# How much we drop when the cap is hit. We rotate down to this floor
# so we don't truncate every single append.
MISSION_ROTATE_FLOOR_BYTES = 24 * 1024  # 24 KiB after rotation


def is_boss_agent(agent_config: Any) -> bool:
    """True if *agent_config* carries the ``boss`` tag.

    Accepts any object with a ``tags`` attribute (list[str]). A non-list
    or missing ``tags`` is treated as not-a-boss.
    """
    tags = getattr(agent_config, "tags", None)
    if not isinstance(tags, (list, tuple)):
        return False
    return "boss" in tags


def persist_mention(nick: str, sender: str, text: str) -> None:
    """Append a single ``@mention`` to the boss's mission file.

    Idempotent against directory absence (mkdir -p). Resilient to
    transient OS errors (logs + returns instead of raising — the
    mission file is best-effort observability, not a hard invariant).

    Caller MUST gate on ``is_boss_agent`` first; this function does
    not re-check, so a non-boss never reaches here.

    Applies head-truncation when the resulting file would exceed
    ``MISSION_MAX_BYTES`` — drops the oldest entries down to the
    ``MISSION_ROTATE_FLOOR_BYTES`` budget.
    """
    path = mission_path_for(nick)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError:
        logger.warning("Failed to ensure mission dir for %s", nick)
        return

    ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"\n## [{ts}] <{sender}>\n\n{text}\n"

    try:
        existing = ""
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                existing = fh.read()

        candidate = existing + entry
        if len(candidate.encode("utf-8")) > MISSION_MAX_BYTES:
            # Drop oldest text from the head down to the floor.
            # Split on section boundaries (``\n## [``) so we never
            # split a single mention in half.
            sections = candidate.split("\n## [")
            # sections[0] is whatever was before the first ## — keep it
            # (it's usually empty). The rest are each one mention block.
            head = sections[0]
            blocks = sections[1:]
            while (
                blocks
                and len(("\n## [".join([head] + blocks)).encode("utf-8"))
                > MISSION_ROTATE_FLOOR_BYTES
            ):
                blocks.pop(0)
            candidate = "\n## [".join([head] + blocks) if blocks else head
            # Write a rotation marker so a reader knows entries were dropped.
            candidate = (
                "<!-- mission rotated: oldest entries dropped to stay under "
                f"{MISSION_MAX_BYTES // 1024} KiB -->\n" + candidate
            )

        with open(path, "w", encoding="utf-8") as fh:
            fh.write(candidate)
    except OSError:
        logger.warning("Failed to persist mission for %s", nick)


def load_context(nick: str) -> str:
    """Read the persisted mission content for *nick*.

    Returns an empty string when no mission file exists. Returns the
    file content with trailing whitespace trimmed.
    """
    path = mission_path_for(nick)
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def clear(nick: str) -> None:
    """Remove the mission file for *nick* (idempotent on missing).

    Called by ``culture agent stop`` and ``culture agent archive``
    when the boss is being retired — without this, the next session
    spawn inherits stale mission context from a prior mission.
    """
    path = mission_path_for(nick)
    try:
        os.unlink(path)
    except FileNotFoundError:
        return
    except OSError:
        logger.warning("Failed to clear mission file for %s", nick)


def build_system_prompt_extension(nick: str) -> str:
    """Return the mission section to append to a boss's system prompt.

    Returns an empty string when no mission is persisted (fresh boss
    or non-boss caller). Caller should append directly to its base
    system prompt:

        prompt = base + build_system_prompt_extension(self.agent.nick)

    Format is deliberately stable: a markdown ``# Your current mission``
    header followed by the persisted content. Stable so prompt cache
    hits as long as the mission text doesn't change.
    """
    mission = load_context(nick)
    if not mission:
        return ""
    return "\n\n# Your current mission (persisted across restarts)\n\n" + mission
