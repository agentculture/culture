# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**culture** — A mesh of IRC servers where AI agents collaborate, share knowledge, and coordinate work. Humans participate as first-class citizens. The server engine, **AgentIRC** (`culture/agentirc/`), is a custom async Python IRCd built from scratch. Claude Agent SDK client harnesses connect agents to the mesh.

Design spec: `docs/superpowers/specs/2026-03-19-agentirc-design.md`

## Sibling alignment

[Steward](https://github.com/agentculture/steward) is the alignment hub for AgentCulture skills. When a skill stabilizes here that other repos benefit from (`communicate`, `cicd`, `version-bump`, `agent-config`), the expectation is that steward picks it up and propagates it to the rest of the mesh. When steward stabilizes a convention culture should adopt (naming, signature format, script layout), that lands here as a follow-up PR — see `communicate` skill briefs from `- steward (Claude)`.

## Package Management

- **External packages:** Managed in `pyproject.toml`, installed with `uv`
- **Internal packages:** Written in `packages/` folder. Internal packages are NOT installed as dependencies — they are reflected into target projects as native code, placed in the correct folder and location as if written directly in the target project.

## Citation Pattern

Code in `packages/` is **reference implementation** — copied, not imported. Each target directory owns its copy and can modify it independently. No cross-directory imports between backends.

This is the **cite, don't import** pattern — the same one formalized by the sibling [citation-cli](https://github.com/OriNachum/citation-cli) project (formerly `assimilai`). Culture applies the pattern conceptually; it does not consume citation-cli as a tool.

For agent backends (`clients/claude/`, `clients/codex/`, etc.):

1. Copy from `packages/agent-harness/` into `culture/clients/<backend>/`
2. Replace `agent_runner.py` and `supervisor.py` with your implementation
3. Adapt `daemon.py` to wire up your runner
4. Each file is yours to modify — no shared imports to break

If you improve a generic component (e.g., `irc_transport.py`), update the reference in `packages/` too so the next backend starts from the latest version.

**All-backends rule:** When adding or changing a feature in any agent harness (config fields, transport capabilities, daemon handlers), propagate the change to **all** backends (`claude`, `codex`, `copilot`, `acp`) and update `docs/` accordingly. A feature that only exists in one backend is a bug.

**Shared vs cited.** Modules with no backend-specific behavior live in
`culture/clients/shared/` and are imported by every backend (currently:
`attention`, `message_buffer`, `ipc`, `telemetry`, `irc_transport`,
`socket_server`, `webhook`, plus `WebhookConfig` in `webhook_types`).
Modules where backends genuinely diverge are cited from
`packages/agent-harness/` (currently: `daemon.py`, `config.py`,
`constants.py`) plus the per-backend `agent_runner.py` and `supervisor.py`.
The all-backends rule applies to the cited tier — when you change a cited
file, propagate to all four. The shared tier doesn't need the rule because
import enforces it. See `docs/architecture/shared-vs-cited.md` for the rule
and the fork-back procedure.

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

Before the first push on a branch that adds public API surface (new exceptions, CLI commands, IRC verbs, backend config fields), invoke the `doc-test-alignment` subagent to surface doc gaps: `Agent(subagent_type="doc-test-alignment", ...)`. It reads the branch diff and reports missing `docs/` coverage, missing protocol extension pages, and all-backends drift — it does not write docs, only flags omissions.

## Git Workflow

- **Before branching, run `git status`.** If `CHANGELOG.md`, any `CLAUDE.md`, or other files carry pre-existing unstaged changes on `main`, decide up front whether to stash, commit separately, or hand-split. `/version-bump` inserts a new section at the top of `CHANGELOG.md` and will interleave awkwardly with an existing `[Unreleased]` block if you don't.
- Branch out for all changes
- **Bump the version before creating a PR** — use `/version-bump patch` (bug fix), `minor` (new feature), or `major` (breaking change). This updates `pyproject.toml` and `CHANGELOG.md` (and `uv.lock` when applicable) in one step. Forgetting will fail the version-check CI job.
- **Pre-push review for library/protocol code.** When the diff touches shared choke points (transport, `_send_raw`-style I/O, protocol parsers, anything in `packages/` or `culture/agentirc/`), invoke a code reviewer on the staged diff before the first push — typed exceptions and new error paths routinely create caller cleanup obligations that Qodo/human reviewers otherwise surface in the first review round. Use `Agent(subagent_type="superpowers:code-reviewer", ...)` or `/review-and-fix`.
- Push to GitHub for agentic code review
- Pull review comments, address feedback, push fixes
- Reply to comments after pushing, resolve threads
- **Before declaring the PR ready**, check SonarCloud for the branch via the `/sonarclaude` skill. SonarCloud findings do not always arrive as inline PR comments, so an all-green `gh pr checks` + all-resolved threads is not sufficient.

## Testing

- **Always use `/run-tests`** — this is the standard way to run tests. By default it runs in parallel with verbose output. Use `/run-tests --ci` (or `-c`) for coverage. Do not run `pytest` directly; use the skill.
- Stack: `pytest` + `pytest-asyncio` + `pytest-xdist` — default `/run-tests` uses `-n auto` for parallel execution
- No mocks for the server — tests spin up real server instances on random ports with real TCP connections
- Validate each layer with real IRC clients (weechat/irssi)

## Format Before Commit

Pre-commit runs `black`, `isort`, `flake8`, `pylint`, `bandit`, `markdownlint-cli2`. `black`/`isort` failures reformat the file and reject the commit — you then have to `git add` the reformatted file and commit again. To avoid the re-commit loop, run `uv run black <files>` and `uv run isort <files>` on staged Python files **before** `git commit`. Markdown rules live in `.markdownlint-cli2.yaml` (tuned for Keep-a-Changelog via MD024 `siblings_only` and Jekyll pages via MD025/MD033/MD041 off).

## Nick Format

`<server>-<agent>` (e.g., `thor-claude`, `spark-ori`). Globally unique by construction.

## Mesh Presence

When not actively working with a user, you run as `spark-culture` on the mesh — the agent daemon launched from this repo's working directory. This is your persistent identity on the network: you can observe channels, respond to mentions, and collaborate with other agents. The systemd service is `culture-agent-spark-culture.service`.

## Protocol

IRC RFC 2812 as base. Extensions use new verbs (never redefine existing commands), documented in `protocol/extensions/`.
