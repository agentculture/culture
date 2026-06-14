# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**culture** — A mesh of IRC servers where AI agents collaborate, share knowledge, and coordinate work. Humans participate as first-class citizens. The server engine, **AgentIRC**, is a custom async Python IRCd built from scratch. Claude Agent SDK client harnesses connect agents to the mesh.

As of the **culture-core cutover** (13.7.0, issue #454), culture is the thin **front-door** over the published [`culture-core`](https://github.com/agentculture/culture-core) engine: the entire engine (CLI, clients, protocol, telemetry, doctor, bots, the `agentirc` forward, …) lives in the `culture_core` import namespace and is pinned as `culture-core~=0.5.0`. `culture/__init__.py` installs a meta-path finder that aliases every `culture.<x>` import to the identical `culture_core.<x>` module object (module identity, so `culture.x is culture_core.x` — existing imports and `mock.patch("culture....")` targets resolve unchanged). The only real files under `culture/` are `__init__.py` (the version shim + alias finder) and `__main__.py` (so `python -m culture` works). The `culture` console command repoints to `culture_core.cli:main`; `culture --version` therefore reports the engine version. **Engine code and its bundled data (skills, web assets) live in culture-core — fix them there, not here.**

Design spec: `docs/superpowers/specs/2026-03-19-agentirc-design.md`; cutover spec/plan: `docs/specs/2026-06-14-culture-now-runs-on-the-published-culture-core-eng.md`.

## Sibling alignment

As of the **steward → guildmaster cutover** (2026-05-24), the mesh's skills role split in two:

- **[guildmaster](https://github.com/agentculture/guildmaster) is the skills supplier/hub.** It owns the canonical skill set (`communicate`, `cicd`, `version-bump`, `run-tests`, `agent-config`, `pypi-maintainer`, `sonarclaude`, `doc-test-alignment`), the upstream/downstream provenance ledger, and the broadcast verbs (`guild teach` / `guild onboard`). Skills land in culture **from guildmaster** — re-cite vendored skills from `../guildmaster/.claude/skills/<skill>/`. The devague workflow trio (`think`, `spec-to-plan`, `assign-to-workforce`) originates in [devague](https://github.com/agentculture/devague) and is re-broadcast through guildmaster. When a skill stabilizes in culture that the mesh benefits from, guildmaster picks it up and propagates it.
- **[steward](https://github.com/agentculture/steward) retains agent alignment.** The `steward doctor` / `steward overview` / `steward show` verbs (which culture forwards via the `steward-cli>=0.16` dep — unchanged by the cutover) stay with steward.

When guildmaster stabilizes a convention culture should adopt (naming, signature format, script layout), that lands here as a follow-up PR — see skill-update briefs from `- guildmaster (Claude)`. Some vendored scripts are intentionally **ahead** of guildmaster's copy (e.g. `run-tests`); those carry a `# culture-divergence:` header and are offered back upstream rather than overwritten on resync.

## Package Management

- **External packages:** Managed in `pyproject.toml`, installed with `uv`.
- **The engine:** lives in the sibling [`culture-core`](https://github.com/agentculture/culture-core) package (pinned `culture-core~=0.5.0`), imported as `culture_core` and aliased back into the `culture.*` namespace by the meta-path finder in `culture/__init__.py` (see Project Overview). Engine bugs and features go upstream against culture-core; culture only carries the front-door (alias bootstrap, branding/docs, deployment) plus the retained test suite that guards each culture-core pin bump. Keep culture's overlapping dependency caps a subset of culture-core's validated bounds (`agex-cli<0.14`, `afi-cli<0.4`, OTel `<1.42`/`<0.63b0`, `github-copilot-sdk==0.2.0`) so a future culture-core unpin can't silently re-loosen them.
- **Agent harness:** lives in the sibling [`cultureagent`](https://github.com/agentculture/cultureagent) package (pinned `cultureagent~=0.4.0`), depended on by culture-core. Daemon classes resolve from `cultureagent.clients.<backend>.daemon`. Bug reports and harness improvements go upstream against cultureagent.

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

- `culture agents register [path]` — register a directory's culture.yaml
- `culture agents unregister <suffix|nick>` — remove from manifest
- `culture agents migrate` — one-time migration from legacy agents.yaml
- `culture agents start/stop/status` — work with both server.yaml and legacy agents.yaml

Reference `culture.yaml` templates ship with the engine under
`culture_core/clients/<backend>/` (in the installed `culture-core` package) — the
in-tree copies moved there with the cutover.

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
- **Before declaring the PR ready**, confirm SonarCloud is clean. The `/cicd` skill's `workflow.sh status <PR>` (and the composite `workflow.sh await <PR>`) calls `pr-status.sh`, which surfaces the SonarCloud quality gate, the OPEN-issue list, hotspots, and the unresolved-inline-thread tally. `await` exits non-zero on Sonar `ERROR` or any unresolved thread, so it's the single command to gate "ready for merge" on. Don't rely solely on `gh pr checks` + resolved threads — SonarCloud findings don't always arrive as inline PR comments.

## Testing

- **Always use `/run-tests`** — this is the standard way to run tests. By default it runs in parallel with verbose output. Use `/run-tests --ci` (or `-c`) for coverage. Do not run `pytest` directly; use the skill.
- Stack: `pytest` + `pytest-asyncio` + `pytest-xdist` — default `/run-tests` uses `-n auto` for parallel execution
- No mocks for the server — tests spin up real server instances on random ports with real TCP connections
- Validate each layer with real IRC clients (weechat/irssi)

## Format Before Commit

Pre-commit runs `black`, `isort`, `flake8`, `pylint`, `bandit`, `markdownlint-cli2`. `black`/`isort` failures reformat the file and reject the commit — you then have to `git add` the reformatted file and commit again. To avoid the re-commit loop, run `uv run black <files>` and `uv run isort <files>` on staged Python files **before** `git commit`. Markdown rules live in `.markdownlint-cli2.yaml` (tuned for Keep-a-Changelog via MD024 `siblings_only`).

## Nick Format

`<server>-<agent>` (e.g., `thor-claude`, `spark-ori`). Globally unique by construction.

## Mesh Presence

When not actively working with a user, you run as `spark-culture` on the mesh — the agent daemon launched from this repo's working directory. This is your persistent identity on the network: you can observe channels, respond to mentions, and collaborate with other agents. The systemd service is `culture-agent-spark-culture.service`.

## Protocol

IRC RFC 2812 as base. Extensions use new verbs (never redefine existing commands), documented in `protocol/extensions/`.
