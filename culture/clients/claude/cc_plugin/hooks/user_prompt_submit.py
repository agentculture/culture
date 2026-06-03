#!/usr/bin/env python3
"""UserPromptSubmit hook (Phase 4.6 + 4.10) — fallback drain + model latch.

On every human-typed prompt:

    1. Drain the bridge queue (belt-and-braces with the Stop hook —
       if Stop missed a beat, this catches up).
    2. On the first prompt of a session, emit
       ``set_runtime_model(model=$CLAUDE_MODEL)`` so the bridge's
       daemon-log records ``model_resolved`` (v8.18.6 invariant —
       Phase 4.10).

Output shape:
    ``{"hookSpecificOutput": {"additionalContext": "..."}}``
to prepend the queue contents to the assistant's view of the prompt.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Per-CC-session flag file the hook writes after the first prompt so
# we only emit ``set_runtime_model`` once per session. ``CLAUDE_SESSION_ID``
# is part of the hook stdin payload — we use it directly so two
# concurrent CC sessions don't trample each other.
_MODEL_LATCH_DIR_ENV = "CULTURE_HOME"


try:  # pragma: no cover — import guard
    from culture.clients.claude.cc_plugin import _bridge_client
    from culture.clients.claude.cc_plugin._nick_resolver import resolve_project_nick
except ImportError:  # pragma: no cover
    _bridge_client = None  # type: ignore[assignment]

    def resolve_project_nick(cwd: str) -> str:  # type: ignore[misc]
        return os.environ.get("CULTURE_NICK") or os.environ.get("CULTURE_BOSS_NICK", "local-boss")


def _read_stdin_json() -> dict[str, Any]:
    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        return {}
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _drain_queue(nick: str) -> list[dict[str, Any]]:
    if _bridge_client is None:
        return []
    try:
        resp = _bridge_client.request(nick, "inbox_drain", timeout=2.0)
    except Exception:  # noqa: BLE001
        return []
    if not resp.get("ok", True):
        return []
    data = resp.get("data") or {}
    entries = data.get("entries") or []
    if isinstance(entries, list):
        return entries
    return list(resp.get("_whispers") or [])


def _maybe_latch_model(nick: str, session_id: str) -> None:
    """Emit ``set_runtime_model`` to the bridge once per session."""
    if not session_id or _bridge_client is None:
        return
    model = os.environ.get("CLAUDE_MODEL", "").strip()
    if not model:
        return
    culture_home = os.environ.get(_MODEL_LATCH_DIR_ENV, "").strip() or os.path.join(
        os.path.expanduser("~"), ".culture"
    )
    latch_dir = os.path.join(culture_home, "bridge", "session-model-latched")
    try:
        os.makedirs(latch_dir, mode=0o700, exist_ok=True)
    except OSError:
        pass
    safe_sid = session_id.replace("/", "_").replace("..", "_")
    latch_path = os.path.join(latch_dir, f"{safe_sid}.flag")
    if os.path.exists(latch_path):
        return
    try:
        _bridge_client.request(nick, "set_runtime_model", timeout=2.0, model=model)
    except Exception:  # noqa: BLE001
        return
    try:
        with open(latch_path, "w", encoding="utf-8") as fh:
            fh.write(model)
    except OSError:
        pass


def _format_additional_context(entries: list[dict[str, Any]]) -> str:
    lines = ["<system-reminder>", "culture-bridge: pending inbound events:"]
    for entry in entries[:50]:
        kind = entry.get("kind", "inbound")
        sender = entry.get("sender", "?")
        target = entry.get("target", "")
        text = (entry.get("text") or entry.get("message") or "").strip()
        lines.append(f"  [{kind}] {sender} -> {target}: {text}")
    if len(entries) > 50:
        lines.append(f"  ... (+{len(entries) - 50} more — call `mesh inbox`)")
    lines.append("</system-reminder>")
    return "\n".join(lines)


def main() -> int:
    event = _read_stdin_json()
    cwd = event.get("cwd") or os.getcwd()
    nick = os.environ.get("CULTURE_NICK") or resolve_project_nick(cwd)

    _maybe_latch_model(nick, event.get("session_id", ""))

    entries = _drain_queue(nick)
    if not entries:
        return 0

    output = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": _format_additional_context(entries),
        }
    }
    sys.stdout.write(json.dumps(output))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
