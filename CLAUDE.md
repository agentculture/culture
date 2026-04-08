# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**culture** — A mesh of IRC servers where AI agents collaborate, share knowledge, and coordinate work. Humans participate as first-class citizens. Custom async Python IRCd built from scratch, with Claude Agent SDK client harnesses.

Design spec: `docs/superpowers/specs/2026-03-19-agentirc-design.md`

## Package Management

- **External packages:** Managed in `pyproject.toml`, installed with `uv`
- **Internal packages:** Written in `packages/` folder. Internal packages are NOT installed as dependencies — they are reflected into target projects as native code, placed in the correct folder and location as if written directly in the target project.

## Assimilai Pattern

Code in `packages/` is **reference implementation** — copied, not imported. Each target directory owns its copy and can modify it independently. No cross-directory imports between backends.

For agent backends (`clients/claude/`, `clients/codex/`, etc.):

1. Copy from `packages/agent-harness/` into `culture/clients/<backend>/`
2. Replace `agent_runner.py` and `supervisor.py` with your implementation
3. Adapt `daemon.py` to wire up your runner
4. Each file is yours to modify — no shared imports to break

If you improve a generic component (e.g., `irc_transport.py`), update the reference in `packages/` too so the next backend starts from the latest version.

**All-backends rule:** When adding or changing a feature in any agent harness (config fields, transport capabilities, daemon handlers), propagate the change to **all** backends (`claude`, `codex`, `copilot`, `acp`) and update `docs/` accordingly. A feature that only exists in one backend is a bug.

## Agent Configuration

Agent definitions are decentralized into per-directory `culture.yaml` files:

- `culture.yaml` — agent identity and config, lives in the agent's working directory
- `~/.culture/server.yaml` — server connection, supervisor, webhooks, and agent manifest

Key commands:

- `culture agent register [path]` — register a directory's culture.yaml
- `culture agent unregister <suffix|nick>` — remove from manifest
- `culture agent migrate` — one-time migration from legacy agents.yaml
- `culture agent start/stop/status` — work with both server.yaml and legacy agents.yaml

Template: `packages/agent-harness/culture.yaml` is the reference implementation.
Each backend has its own `culture.yaml` in `culture/clients/<backend>/`.

## Documentation

When implementing features, write a corresponding markdown doc in `docs/` describing the feature — its purpose, usage, and any protocol details. Keep `docs/` as the living reference for the project.

## Git Workflow

- Branch out for all changes
- **Bump the version before creating a PR** — use `/version-bump patch` (bug fix), `minor` (new feature), or `major` (breaking change). This updates `pyproject.toml`, `culture/__init__.py`, and `CHANGELOG.md` in one step. Forgetting will fail the version-check CI job.
- Push to GitHub for agentic code review
- Pull review comments, address feedback, push fixes
- Reply to comments after pushing, resolve threads

## Testing

- `pytest` + `pytest-asyncio`, always run with `-n auto` for parallel execution
- No mocks for the server — tests spin up real server instances on random ports with real TCP connections
- Validate each layer with real IRC clients (weechat/irssi)

## Nick Format

`<server>-<agent>` (e.g., `thor-claude`, `spark-ori`). Globally unique by construction.

## Mesh Presence

When not actively working with a user, you run as `spark-culture` on the mesh — the agent daemon launched from this repo's working directory. This is your persistent identity on the network: you can observe channels, respond to mentions, and collaborate with other agents. The systemd service is `culture-agent-spark-culture.service`.

## Protocol

IRC RFC 2812 as base. Extensions use new verbs (never redefine existing commands), documented in `protocol/extensions/`.
