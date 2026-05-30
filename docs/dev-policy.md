# The culture dev policy

The default development process for any agent (human or AI) writing
code in culture. Codifies the elevated quality bar set during the
v8.19 fleet ship: **0 bugs, 100% best industry standards patterns
and security on every PR**.

Companion to `docs/task-model.md` (which codifies tasks/agents/
channels) and the existing `CLAUDE.md` (which codifies project
conventions).

## The quality bar (non-negotiable)

Every PR — whether written by a human, by a Claude Code session,
or by a culture mesh agent — meets these on first push:

| Dimension | Standard |
|---|---|
| **Correctness** | All inputs validated at boundaries. No string-concat in shell/SQL/IRC commands. Async tasks referenced (no fire-and-forget GC). Cancellation correct. Timeouts on all I/O. Existing serialized state still loads. |
| **Security** | Fail-closed (deny by default). Defense in depth (broker + ACL + manifest ownership; not one layer). No secrets in logs/audit/daemon-log. `hmac.compare_digest` for token compare. IPC sender-authenticated. Path traversal blocked. Bounded buffers. |
| **Tests** | Real behavior tests, not surface-API mocks. Error paths covered. Real-server integration tests (culture's convention: spin up server on random port, real TCP). Property tests for parsers and state machines. New tests COUNT on the PR — naming the count is part of the PR description. |
| **Observability** | Structured JSON logs (no `print`). OTEL traces across boss → broker → worker. Metrics for SLOs (perm-request latency, worker stall rate, etc.). Audit captures full tool input + content (16 KiB cap) + thinking blocks. |
| **Reviewability** | Small focused PRs (target ≤ 500 lines diff; split larger). Conventional commit messages (`feat:` / `fix:` / `docs:` / `chore:` / `refactor:` / `test:`). PR title under 70 chars. PR body has Summary / Why / What changed / Test plan / Risks. |
| **Composability** | No version-bump races. Stack PRs that depend on each other; don't all create siblings off main. Backward-compat verified — existing serialized state, IRC clients, perm policies all keep working. |
| **Docs** | Every public API (Python module, CLI command, IRC verb, perm field) documented in its appropriate place. CHANGELOG entry. `protocol/extensions/<verb>.md` for new IRC verbs. `docs/<feature>.md` for new features. Pre-push run of `doc-test-alignment` subagent before first push on a branch that adds public API. |

## The default flow

```
agent picks up task
   ↓
agent creates feature branch off task/feature branch
(NOT off main directly when ≥1 PR for this task exists)
   ↓
agent implements + writes tests + writes docs + runs gates locally
   ↓
agent commits with conventional message
   ↓
agent opens PR against the task/feature branch
   ↓
orchestrator reviews → critiques in PR comments
   ↓
agent addresses comments → pushes fixes
   ↓
Qodo (or `superpowers:code-reviewer` subagent / culture review
agent) reviews → critiques
   ↓
agent addresses Qodo comments → pushes fixes
   ↓
orchestrator merges to feature branch
   ↓
when feature branch is complete → integration PR to main
   ↓
human reviews → merges to main
```

### Branch naming

- Feature branches: `feat/<task-name>` or `feat/v<version>-<task-name>`
- Bug-fix branches: `fix/<short-description>`
- Doc branches: `docs/<short-description>`
- Refactor branches: `refactor/<short-description>`

Agent feature branches that stack on a task/feature branch:
`feat/<task>-<sub-component>` (e.g. the dashboard fix worker on a
release-candidate branch).

### Conventional commit messages

```
<type>(<scope>): <subject>

<body — why this matters, not what the diff shows>

<optional footers: Co-authored-by, Closes #X, BREAKING CHANGE: ...>
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`,
`build`, `ci`, `chore`, `revert`.

### PR description template

```markdown
## Summary
1-3 bullets, what the PR does.

## Why
The problem this solves. Cite issue / dogfood finding / RFC if there is one.

## What changed
File-by-file or component-by-component.

## Test plan
- [x] Unit tests added: <count>
- [x] Integration tests added: <count>
- [x] `bash .claude/skills/run-tests/scripts/test.sh -p` passes
- [ ] Manual: <steps to reproduce the change>

## Risks
What could break. What backward-compat scenarios were verified.

## Companion to / Depends on
Linked PRs and merge-order implications.
```

## Pre-push checks (run locally, every push)

```bash
# Format (auto-fix-then-recommit if needed)
uv run black <changed .py files>
uv run isort <changed .py files>

# Lint
uv run flake8 culture/
uv run pylint culture/         # tolerated; nice-to-have for new code

# Type check  (TARGET — not yet enforced project-wide)
uv run mypy --strict culture/

# Security scan
uv run bandit -r culture/ -ll

# Tests
bash .claude/skills/run-tests/scripts/test.sh -p

# Doc / surface check (before first push on a branch with new public API)
Agent(subagent_type="doc-test-alignment", ...)
```

If any of these fail, do NOT push. Fix the issue and re-run.

## Quality gates that the orchestrator enforces in review

When the orchestrator (or designated reviewer agent) reviews a PR,
it checks at minimum:

1. **Code-walk the diff**: open every changed file, read ≥30 lines of
   context around each change. Reading the diff alone is insufficient.
2. **Verify the test plan**: tests added must actually test the
   BEHAVIOR, not the surface API.
3. **Spot-check claims**: every PR description says "X works
   because Y" — pick 1-2 and verify against the code.
4. **Check composability**: this PR's changes vs the other open
   PRs. No file overlap that creates merge conflicts? No version
   collision? No semantic conflict with a sibling PR's design?
5. **Run the gates**: don't trust CI alone if CI doesn't run on
   personal-fork PRs (current reality for edo-ceder/culture). Run
   tests + lint + bandit locally as part of review.

## Adversarial review pattern

For load-bearing or security-sensitive PRs, the orchestrator runs an
**adversarial verify** step: spawn a separate review agent whose
job is to refute the verdict. Their role brief:

> Your job is to find why the verdict is wrong. Pick the strongest
> claim and try to break it. Pick the weakest area scored "pass" and
> find what was missed. If nothing breaks, the verdict stands.

Adversarial verifiers MUST be independent (different cwd, different
nick, different brief — not a continuation of the original reviewer).

## What changes the policy

The orchestrator can change the policy **per task** when the work
shape demands it:

- Emergency hotfix → may skip Qodo review (orchestrator review only)
- Pure documentation PR → may go direct to main
- Trivial dependency bump → may skip full test suite if change is
  obviously scoped
- Experimental/spike branch → may skip the full quality gates if
  the branch is explicitly marked `spike/` (and will be discarded
  or rewritten before any merge to main)

These exceptions MUST be called out in the PR description.

## Worker reliability mitigations (lessons from v8.18.7 fleet)

Workers can die silently between `DONE-FINAL` and `git push` —
specifically when an idle worker is reactivated by a DM and the SDK
CLI's `Stream closed` bug fires. Mitigations:

1. **Workers write findings/output to disk under their cwd**
   BEFORE posting DONE-FINAL. Use a known filename
   (`AUDIT-PR-<num>.md`, `OUTPUT-<task>.md`) so the orchestrator
   can recover the work even if the daemon dies.
2. **Workers commit + push AS PART OF the same turn that produces
   DONE-FINAL** — do NOT split commit and push into a separate
   turn that fires when the orchestrator DMs them.
3. **Orchestrator avoids DM'ing idle workers** that have posted
   DONE-FINAL — instead, close them cleanly via
   `culture boss close <name>` once their work is verified merged.
4. **Watchdog**: the `stalled_in_failed_retry` (v8.18.5) and a new
   `silent_death_after_done` watchdog catch these — surface to the
   orchestrator before the work is lost.

## Version coordination (lessons from v8.18.7 fleet)

When ≥2 PRs ship in parallel, the orchestrator declares the version
stack BEFORE the first PR commits:

```
v8.18.7 = fix-acl       (PR #415)
v8.18.8 = fix-symlink   (PR #26)
v8.19.0 = fix-cli       (PR #416)
v8.19.1 = fix-lifecycle (PR #27)
v8.19.2 = ui-dash       (PR #28)
v8.19.3 = (reserved)
```

Each PR's version bump matches its declared slot. PRs that miss
the assigned version (because they auto-bumped without checking)
get re-bumped in a follow-up commit.

**Future improvement**: a `culture release plan` command that
records the slot assignments and refuses spawn when a slot is
taken. Or a `Changesets`-style file-per-PR mechanism so version
bumping happens at integration time, not in feature branches.

## Co-authoring

When the orchestrator ships a commit on behalf of an agent (e.g.
because the agent's daemon died before push), attribute the agent
in the commit message footer:

```
Co-authored-by: local-fix-symlink-w (mesh worker; orchestrator
shipped the commit since the daemon stopped before it could push)
```

Always include the brief explanation of WHY the orchestrator
shipped on their behalf.

## See also

- `docs/task-model.md` — agent + task + channel + role definitions
- `CLAUDE.md` — project-specific conventions (pre-commit, format,
  package management, mesh presence)
- `docs/v8.18.6-prd-authoring-dogfood.md` — origin of the three-level
  vision framing
- Earlier dogfoods in `docs/` for the discovery cycle behind these
  policies
