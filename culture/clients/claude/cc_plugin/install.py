"""First-run hook installer for the culture-bridge CC plugin.

Why user-settings-scoped (NOT plugin-scoped)? Claude Code bug #16538
(closed-as-not-planned) — plugin-scoped SessionStart hooks do NOT
reliably inject ``hookSpecificOutput.additionalContext`` so the spool
drain at session start would silently disappear. The Phase 0.4 spike
confirmed that user-settings-scoped hooks + a Stop-hook-block pattern
work for the end-of-turn queue drain.

The installer is **idempotent**: it overwrites only the
``culture-bridge`` block inside ``~/.claude/settings.json`` and leaves
every other hook, MCP server, or unrelated setting alone. Re-running
install on the same machine produces the same on-disk state.

Operators who want to uninstall the plugin remove the
``culture-bridge`` block manually — see ``README.md``.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


# Marker used to identify the plugin's own hooks inside
# ``~/.claude/settings.json``. Every hook command and every nested
# matcher gets ``"_culture_bridge": True`` so we can re-find them on
# upgrade / uninstall without touching unrelated entries.
_MARKER_KEY = "_culture_bridge"

# The four hooks the plugin owns.
HOOK_EVENTS = ("SessionStart", "Stop", "UserPromptSubmit", "PreToolUse")


def settings_path() -> str:
    """Return the user-scope Claude Code settings file path.

    Honours ``$CLAUDE_CONFIG_DIR`` when present (the supported override
    on Claude Code 2.1+), then falls back to ``$HOME/.claude``.
    """
    override = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if override:
        return os.path.join(override, "settings.json")
    home = os.path.expanduser("~")
    return os.path.join(home, ".claude", "settings.json")


def plugin_dir() -> str:
    """The absolute path to the directory containing ``install.py``.

    Hook commands embed this path so Claude Code can invoke the script
    directly without depending on the cwd at hook-fire time.
    """
    return os.path.dirname(os.path.abspath(__file__))


def hook_script_path(event: str) -> str:
    """Path to the hook script implementing ``event`` (case-sensitive
    SessionStart / Stop / UserPromptSubmit / PreToolUse)."""
    script_name = {
        "SessionStart": "session_start.py",
        "Stop": "stop.py",
        "UserPromptSubmit": "user_prompt_submit.py",
        "PreToolUse": "pre_tool_use.py",
        "SessionEnd": "session_end.py",
    }[event]
    return os.path.join(plugin_dir(), "hooks", script_name)


def _hook_command(event: str) -> str:
    """The shell command Claude Code will invoke for this event.

    ``python3`` runs the hook directly. We deliberately do NOT shell out
    to ``uv run`` here — hook latency is human-perceptible and forking
    ``uv`` per hook would compound it. The hook scripts are designed to
    import their dependencies tolerantly.
    """
    script = hook_script_path(event)
    return f"python3 {script}"


def _build_culture_bridge_block() -> dict[str, Any]:
    """Construct the ``hooks`` sub-block this plugin owns.

    Schema follows Claude Code's hooks settings shape:

        {
            "<EventName>": [
                {
                    "matcher": "*",                  # PreToolUse only
                    "hooks": [
                        {"type": "command", "command": "..."}
                    ],
                    "_culture_bridge": true
                }
            ]
        }

    We attach the ``_culture_bridge: true`` marker on each entry so we
    can re-find them on upgrade without touching adjacent operator
    customisations.
    """
    block: dict[str, list[dict[str, Any]]] = {}
    for event in HOOK_EVENTS:
        entry: dict[str, Any] = {
            "hooks": [
                {
                    "type": "command",
                    "command": _hook_command(event),
                }
            ],
            _MARKER_KEY: True,
        }
        # PreToolUse needs a matcher so Claude Code knows which tool
        # calls to gate. ``"*"`` matches every tool — the script itself
        # short-circuits for ``mesh ...`` calls to avoid recursion
        # (Phase 4.7).
        if event == "PreToolUse":
            entry["matcher"] = "*"
        block[event] = [entry]
    return block


def _read_settings(path: str) -> dict[str, Any]:
    """Read existing ``settings.json`` if any; return ``{}`` on missing
    or malformed content. We deliberately tolerate malformed JSON by
    starting fresh — the alternative is hard-failing CC startup on a
    settings file the user could fix in ten seconds."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        logger.warning(
            "Failed to parse existing %s; rewriting from scratch. "
            "Original file backed up to .bak.",
            path,
        )
        try:
            os.replace(path, path + ".bak")
        except OSError:
            pass
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _strip_culture_bridge_entries(hooks: dict[str, Any]) -> dict[str, Any]:
    """Return ``hooks`` with every ``_culture_bridge: true`` entry
    removed. Preserves all unrelated entries verbatim."""
    cleaned: dict[str, Any] = {}
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            cleaned[event] = entries
            continue
        kept = [
            entry for entry in entries if not (isinstance(entry, dict) and entry.get(_MARKER_KEY))
        ]
        if kept:
            cleaned[event] = kept
    return cleaned


def install(settings_path_override: str | None = None) -> str:
    """Write the culture-bridge hook block into the user settings file.

    Idempotent: removes any prior culture-bridge entries first, then
    re-inserts a fresh block reflecting the current script paths. Other
    settings (MCP servers, theme, model defaults, custom hooks the
    operator wrote) are preserved byte-for-byte.

    Returns the absolute path that was written.
    """
    path = settings_path_override or settings_path()
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)

    data = _read_settings(path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    hooks = _strip_culture_bridge_entries(hooks)

    new_block = _build_culture_bridge_block()
    for event, entries in new_block.items():
        existing = hooks.get(event)
        if not isinstance(existing, list):
            existing = []
        hooks[event] = existing + entries

    data["hooks"] = hooks
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def uninstall(settings_path_override: str | None = None) -> str:
    """Inverse of ``install``: strip the culture-bridge block and leave
    everything else alone. Returns the path that was rewritten."""
    path = settings_path_override or settings_path()
    if not os.path.exists(path):
        return path
    data = _read_settings(path)
    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        hooks = _strip_culture_bridge_entries(hooks)
        if hooks:
            data["hooks"] = hooks
        else:
            data.pop("hooks", None)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)
    return path


if __name__ == "__main__":  # pragma: no cover — invoked via ``python -m``
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "uninstall":
        out = uninstall()
        print(f"Removed culture-bridge hooks from {out}")
    else:
        out = install()
        print(f"Installed culture-bridge hooks into {out}")
