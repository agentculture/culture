"""Culture-bridge Claude Code plugin.

Implements the CC-IS-the-boss surface. The plugin installs four
user-scoped hooks into ``~/.claude/settings.json`` (SessionStart, Stop,
UserPromptSubmit, PreToolUse) and exposes ``mesh ...`` MCP tools so the
CC assistant can drive the bridge over its IPC socket.

See ``docs/superpowers/specs/2026-06-03-mesh-rearchitecture-plan.md``
Phase 4 for the design rationale, and ``README.md`` in this directory
for operator-facing usage.
"""
