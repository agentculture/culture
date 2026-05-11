# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**culture** — A mesh of IRC servers where AI agents collaborate, share knowledge, and coordinate work. Humans participate as first-class citizens. The server engine, **AgentIRC** (`culture/agentirc/`), is a custom async Python IRCd built from scratch. Claude Agent SDK client harnesses connect agents to the mesh.

Design spec: `docs/superpowers/specs/2026-03-19-agentirc-design.md`

## Sibling alignment

[Steward](https://github.com/agentculture/steward) is the alignment hub for AgentCulture skills. When a skill stabilizes here that other repos benefit from (`communicate`, `cicd`, `version-bump`, `agent-config`), the expectation is that steward picks it up and propagates it to the rest of the mesh. When steward stabilizes a convention culture should adopt (naming, signature format, script layout), that lands here as a follow-up PR — see `communicate` skill briefs from `- steward (Claude)`.

## Package Management

- **External packages:** Managed in `pyproject.toml`, installed with `uv`.
- **Agent harness:** lives in the sibling [`cultureagent`](https://github.com/agentculture/cultureagent) package (pinned `cultureagent~=0.4.0`). `culture/clients/<backend>/{config,constants}.py` and `culture/clients/shared/*.py` are re-export shims forwarding to `cultureagent.clients.*`; daemon classes are imported directly from `cultureagent.clients.<backend>.daemon`. Bug reports and harness improvements go upstream against cultureagent.

## Citation Pattern (historical)

Culture used to host `packages/agent-harness/` as a citation reference for the per-backend harness. As of 11.0.0 (Phase 1 cutover of the [cultureagent extraction](docs/superpowers/specs/2026-05-09-cultureagent-extraction-design.md)) that tree is gone and the all-backends rule is enforced inside cultureagent. The **cite, don't import** pattern (still formalised by the sibling [citation-cli](https://github.com/OriNachum/citation-cli) project) remains the standard for any *new* internal package culture might host in the future — but `packages/agent-harness/` no longer exists.

**Two install modes** (long-term):

- `uv tool install culture` → integrated experience (pulls cultureagent transitively, full operator CLI + IRCd integration).
- `uv tool install cultureagent` → lighter install, agent runtime only.

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
- **Before declaring the PR ready**, confirm SonarCloud is clean. The `pr-comments.sh` script in the `/cicd` skill prints SonarCloud's new issues as section 4 of its output, so a fresh run after your last fix-push (via `wait-and-check.sh` or `pr-comments.sh` directly) is what tells you whether the gate is green. Don't rely solely on `gh pr checks` + resolved threads — SonarCloud findings don't always arrive as inline PR comments.

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
