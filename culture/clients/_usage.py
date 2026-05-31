"""Per-agent cumulative token-usage tally (v8.19.21).

A side-car log that captures per-turn input/output token counts from
whichever backend exposes them (claude today; codex/copilot do not).
The dashboard sums the file to surface a ``tokens_used`` badge on
each agent chip — the orchestrator can see at a glance how many tokens
each worker has burned.

This is intentionally separate from ``_audit.py``: the audit log is
the authoritative per-turn SDK message capture (already capped at
16 KiB per field for context safety), while usage records are
small (~80 bytes per turn) and would clutter the audit pipeline.

Format: JSONL at ``~/.culture/usage/<nick>.jsonl``. One line per turn:

    {"ts": "2026-05-31T12:34:56Z", "in": 1234, "out": 567, "model": "..."}

Missing keys (a backend that doesn't expose tokens) are skipped; the
dashboard treats any record with neither ``in`` nor ``out`` as zero.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

from culture.clients._perm_broker import culture_home

logger = logging.getLogger(__name__)


def _usage_dir() -> str:
    return os.path.join(culture_home(), "usage")


def usage_path_for(nick: str) -> str:
    return os.path.join(_usage_dir(), f"{nick}.jsonl")


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def record_turn_usage_sync(
    nick: str,
    *,
    tokens_input: int | None,
    tokens_output: int | None = None,
    model: str = "",
) -> None:
    """Append one usage line for ``nick``. Synchronous; safe inside ``to_thread``.

    If both token counts are ``None`` the call is a no-op (nothing to
    record). Errors are logged but never propagate — usage is advisory,
    and a broken disk must NOT stall the agent loop.
    """
    if tokens_input is None and tokens_output is None:
        return
    rec: dict[str, object] = {"ts": _now_iso()}
    if isinstance(tokens_input, int) and tokens_input >= 0:
        rec["in"] = tokens_input
    if isinstance(tokens_output, int) and tokens_output >= 0:
        rec["out"] = tokens_output
    if model:
        rec["model"] = model
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    path = usage_path_for(nick)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except OSError as exc:
        logger.debug("usage record failed for %s: %s", nick, exc)


async def record_turn_usage(
    nick: str,
    *,
    tokens_input: int | None,
    tokens_output: int | None = None,
    model: str = "",
) -> None:
    """Async wrapper around :func:`record_turn_usage_sync` for daemon use."""
    await asyncio.to_thread(
        record_turn_usage_sync,
        nick,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        model=model,
    )


def sum_tokens(nick: str) -> dict[str, int]:
    """Sum cumulative tokens for ``nick``. Returns ``{"in": N, "out": M, "total": N+M, "turns": K}``.

    Missing file → all zero. Malformed lines are skipped silently —
    a partial write or human-edited file must not crash the dashboard.
    """
    path = usage_path_for(nick)
    total_in = total_out = turns = 0
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                try:
                    rec = json.loads(raw)
                except (ValueError, json.JSONDecodeError):
                    continue
                if not isinstance(rec, dict):
                    continue
                vin = rec.get("in")
                vout = rec.get("out")
                touched = False
                if isinstance(vin, (int, float)):
                    total_in += int(vin)
                    touched = True
                if isinstance(vout, (int, float)):
                    total_out += int(vout)
                    touched = True
                if touched:
                    turns += 1
    except OSError:
        pass
    return {
        "in": total_in,
        "out": total_out,
        "total": total_in + total_out,
        "turns": turns,
    }


def clear_usage(nick: str) -> bool:
    """Remove the usage file for ``nick``. Returns True iff a file was deleted."""
    try:
        os.remove(usage_path_for(nick))
        return True
    except OSError:
        return False
