---
layout: default
title: Helper Tool Inheritance
parent: AgentIRC
nav_order: 94
---

# Helper Tool Inheritance

Helper agents spawned by a boss session inherit the boss's full tool surface —
the user-level skills, MCP servers, and plugins configured in `~/.claude/` — so a
helper can reach for the same tools the boss can.

Design spec: `docs/superpowers/specs/2026-05-28-helper-boss-permission-broker.md`

## What changed

The Claude agent runner previously loaded only project-level settings:

```python
ClaudeAgentOptions(setting_sources=["project"])
```

It now loads all three layers:

```python
ClaudeAgentOptions(setting_sources=["user", "project", "local"])
```

The Claude CLI reads each layer's `settings.json` (and skills/plugins), so a
helper now sees:

- **`user`** — `~/.claude/settings.json` (your MCP servers), `~/.claude/skills/`,
  `~/.claude/plugins/`.
- **`project`** — the helper cwd's `.claude/` (project skills, project MCP).
- **`local`** — machine-local overrides.

## Scope: every Claude agent, not just helpers

This widening applies to **all** Claude agents on the mesh, including
standalone, long-running ones started with `cu agent start` (e.g.
`spark-culture`). Those agents now have access to the user's MCP servers and
skills.

This is intentional — any agent on the mesh should be able to use the boss's
tool surface. The difference between a *supervised helper* and a *standalone
agent* is **not** the tool surface; it is whether a `perm-policy/<nick>.yaml`
exists (see [Helper Permission Broker](helper-permissions.md)):

- **Helper** (policy file present): inherits tools **and** routes dangerous tool
  calls through the boss for approval.
- **Standalone agent** (no policy file): inherits tools and runs autonomously
  (`bypassPermissions`, as before), with the
  [daemon-action log](helper-daemon-log.md) providing after-the-fact visibility.

## Security note

A helper inheriting `~/.claude/` gains every MCP server you have configured —
including Gmail, Drive, Calendar, Atlassian, Cloudflare, etc. For a supervised
helper, the broker gates these (all `mcp__*` tools require boss approval by
default). For a standalone agent, there is no gate — so only run standalone
agents you trust with your full tool surface, or give them a policy file to bring
them under boss supervision.

## Backend support

| Backend | Inheritance |
|---|---|
| Claude | Full — `setting_sources=["user","project","local"]`. |
| Copilot | Deferred — the Copilot SDK `skill_directories` wiring is planned but the SDK was not verifiable at build time. |
| Codex | Not applicable — the app-server protocol exposes no user-skills/MCP surface from the harness. |
| ACP | Not applicable — no skill/MCP inheritance hook in the ACP surface. |
