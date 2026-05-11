---
name: cicd
description: >
  CI/CD lane for culture: branch, commit, push, create PR, wait for automated
  reviewers, fetch comments, fix or pushback, reply, resolve threads.
  Delegates `lint` / `open` / `read` / `reply` / `delta` to `agex pr`; keeps
  two culture-side extensions on top — `status` (SonarCloud gate, OPEN
  issues, hotspots, unresolved-thread tally) and `await` (`read --wait` +
  `status`, exits non-zero on Sonar ERROR or unresolved threads). Renamed
  from `pr-review` in culture 8.8.1; rebased on `agex pr` (from steward
  0.12.0) in culture 11.1.0. Use when: creating PRs, handling review
  feedback, or the user says "create PR", "review comments", "address
  feedback", "resolve threads", or "use pr-review" / "use cicd".
---

# CI/CD Lane (formerly pr-review)

Complete pull request lifecycle for the culture project. Follow every step
in order. The skill was renamed from `pr-review` to `cicd` in culture 8.8.1
and rebased on `agex pr` in culture 11.1.0; existing prompts that say "use
pr-review" still resolve here through the trigger phrases above.

`agex pr` (in `agentculture/agex-cli`) is the upstream for the five core
PR-lifecycle verbs — `lint`, `open`, `read`, `reply`, `delta`. Culture used
to vendor parallel scripts for each (`create-pr-and-wait.sh`, `pr-batch.sh`,
`pr-comments.sh`, `wait-and-check.sh`, `poll-readiness.sh`); in 11.1.0 those
were dropped in favor of delegating to `agex`. What's left in this skill is
the culture-specific gating layer:

- `status` — SonarCloud quality gate, OPEN issues, hotspots, deploy preview
  URL, unresolved-inline-thread tally.
- `await` — composes `agex pr read --wait` with `status` and gates on
  Sonar `ERROR` / unresolved threads. The single command to run after
  pushing a fix when you want "wake me when this PR is triage-able."

Both extensions are filed upstream
([agex-cli#41](https://github.com/agentculture/agex-cli/issues/41)); when
they land they migrate out of this skill.

## Prerequisites

Hard requirements: `agex` (already a culture runtime dep — `agex-cli>=0.13`
in `pyproject.toml`), `gh` (GitHub CLI), `jq`, `bash`, `python3` (stdlib
only), `curl` (used by `pr-status.sh`).

If `agex` is not on PATH (e.g. running outside a culture venv):

```bash
uv tool install agex-cli   # or: pip install --user agex-cli
```

## Step 1 — Branch

If you are on `main`, create a feature branch first:

```bash
git checkout -b <branch-name>
```

Branch naming conventions:

| Type | Pattern | Example |
|------|---------|---------|
| Bug fix | `fix/<short-desc>` | `fix/server-not-running-crash` |
| Feature | `feat/<short-desc>` | `feat/webhook-alerts` |
| Docs | `docs/<short-desc>` | `docs/protocol-extensions` |
| Skill | `skill/<short-desc>` | `skill/cicd-and-communicate-resync` |

## Step 1b — Check for existing PRs on the branch

Before adding new work to an existing branch, check if there's already an
open PR:

```bash
gh pr view --json number,title,state --jq '{number,title,state}'
```

If the command fails with "no pull requests found", there is no open PR —
proceed normally. Only act on the result if it returns valid JSON with
`state: "OPEN"`.

If an open PR exists and your new changes are **unrelated** to that PR's
scope, **stop and ask the user**:

> "There's an open PR (#N: 'title') on this branch. The new changes
> are unrelated to that PR. Would you like to merge the existing PR
> first before starting the new work?"

Wait for the user's answer before proceeding. If the user says yes,
let them merge (never merge yourself). If they say continue, add the
changes to the existing PR.

If the new changes ARE related to the existing PR, proceed normally —
commit and push to the same branch.

## Step 2 — Make changes, commit, push

1. Edit code
2. Run tests via the `run-tests` skill (parallel by default)
3. Bump the version (required before PR — `version-check` CI gate
   enforces this):

   ```bash
   echo '{"fixed":["..."]}' | python3 .claude/skills/version-bump/scripts/bump.py patch
   ```

4. Stage and commit:

   ```bash
   git add <files>
   git commit -m "$(cat <<'EOF'
   Commit message here.

   Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
   EOF
   )"
   ```

5. Push the branch (no `--push` flag on `workflow.sh open` anymore — see
   the [Migration note](#migration-from-create-pr-and-waitsh) at the end):

   ```bash
   git push -u origin <branch-name>
   ```

## Step 3 — Create PR (and wait for an initial briefing in one shot)

Use `workflow.sh open` — it forwards to `agex pr open --delayed-read`,
which creates the PR then polls 180s for an initial briefing (CI checks,
SonarCloud gate + new issues, all comments, next-step footer). Automated
reviewers (qodo, copilot, sonarcloud) need that window to post; checking
sooner returns zero comments.

```bash
bash .claude/skills/cicd/scripts/workflow.sh open \
    --title "Short title" \
    --body-file /tmp/pr-body.md
```

Or pipe the body via heredoc on stdin:

```bash
bash .claude/skills/cicd/scripts/workflow.sh open --title "Short title" <<'EOF'
## Summary
- Bullet points describing changes

## Test plan
- [ ] Test items
EOF
```

`agex pr open` writes the body via `--body-file` to a tempfile under the
hood, so large self-contained briefs don't hit the OS argv length limit.
The auto-signature `- <nick> (Claude)` is appended by agex from the
repo-root `culture.yaml` (first agent's `suffix`) — don't sign manually
in the body.

**If you must do it by hand** (PR was opened earlier and you only need
to fetch feedback now):

```bash
bash .claude/skills/cicd/scripts/workflow.sh read <PR_NUMBER>
```

Do **not** check for comments before the 3-minute mark on a brand-new PR.
Empty comment lists in the first 1–2 minutes don't mean reviewers are done
— they mean reviewers haven't started yet.

## Step 4 — Wait another window if needed

If the initial briefing came back empty or thin, or you suspect a slow
reviewer (or you just pushed a follow-up commit and want a fresh review
pass), use `workflow.sh read --wait` for a deliberate poll:

```bash
bash .claude/skills/cicd/scripts/workflow.sh read <PR_NUMBER> --wait 180
```

This polls `agex pr read` up to 180 seconds, exiting when required
reviewers have posted (or the cap hits). It is **not** open-ended polling
— it's "give the reviewers one more deliberate window before deciding
they're done." For the SonarCloud-gated variant that exits non-zero on
unresolved threads, use `workflow.sh await <PR>` instead.

If two windows are also empty, fall back to one-shot reads spaced a
minute apart:

```bash
bash .claude/skills/cicd/scripts/workflow.sh read <PR_NUMBER>
# if still empty:
sleep 60
bash .claude/skills/cicd/scripts/workflow.sh read <PR_NUMBER>
```

Three consecutive reads returning zero comments means reviewers are done /
not configured; proceed to triage (or skip directly to merge if there's
truly nothing to address).

### Long waits — background polling

`agex pr read --wait N` polls in-session for up to N seconds. The
Anthropic prompt cache has a 5-minute TTL; sleeping past it burns context
every cache miss. Two ways to drive the wait:

- **Synchronous** — `workflow.sh await <PR>` after `workflow.sh open`.
  Fine when readiness is expected within ~5 minutes.
- **Asynchronous** — for longer waits, run `agex pr read --wait NNN`
  inside a background subagent (Agent tool, `run_in_background: true`)
  so the main session only pays the cache cost when readiness fires.
  The subagent's only job is to invoke `agex pr read --wait` and echo
  its headline back. The parent triages with `workflow.sh await` when
  the notification arrives.

## Step 6 — Triage each comment

For every review comment, decide **FIX** or **PUSHBACK** with reasoning.

Default to **FIX** for: portability complaints (always valid for culture
— recurring bug class), test or doc requests, style nits aligned with
project conventions, exception-type mismatches (e.g. breaking reconnect
loops), Sonar findings.

Default to **PUSHBACK** for: architecture opinions that conflict with the
project `CLAUDE.md` (notably the all-backends rule and the citation
pattern), greenfield false-positives.

### Alignment-delta rule

If the PR touches `CLAUDE.md`, `culture.yaml`, or anything under
`.claude/skills/`, run `workflow.sh delta` **before** declaring FIX or
PUSHBACK on each comment. Note any sibling that needs a follow-up PR and
mention it in your reply.

## Step 7 — Fix code and push

1. Make all code fixes
2. Run tests via the `run-tests` skill
3. Commit with a descriptive message
4. Push: `git push`

## Step 8 — Reply and resolve threads

`workflow.sh reply` forwards to `agex pr reply <PR>` which reads JSONL
from stdin, posts the replies, and resolves the threads in one call.
agex auto-appends `- <nick> (Claude)` (resolved from `culture.yaml`'s
first agent `suffix`, falling back to repo basename) when the reply
body isn't already signed — don't sign manually.

Batch reply to all comments at once:

```bash
bash .claude/skills/cicd/scripts/workflow.sh reply <PR_NUMBER> <<'EOF'
{"comment_id": 123, "body": "Fixed -- changed X to Y."}
{"comment_id": 456, "body": "Intentional -- this follows the pattern in Z because..."}
EOF
```

For a one-off reply that doesn't merit JSONL, the vendored
`pr-reply.sh` (with its `_resolve-nick.sh` dependency) is still shipped
and runs outside `workflow.sh`:

```bash
bash .claude/skills/cicd/scripts/pr-reply.sh --resolve <PR_NUMBER> <COMMENT_ID> "Fixed -- updated."
```

**Important:**

- Don't add `- Claude` or any other signature to the reply body — agex
  / `pr-reply.sh` appends `- <nick> (Claude)` automatically.
- `workflow.sh reply` resolves threads by default; `pr-reply.sh` needs
  `--resolve`.
- Every comment must get a reply — no silent fixes.

## Step 9 — Check SonarCloud before declaring ready

After CI is green and all inline threads are resolved, run the culture
gate:

```bash
bash .claude/skills/cicd/scripts/workflow.sh status <PR_NUMBER>
```

or the composite wait-then-gate:

```bash
bash .claude/skills/cicd/scripts/workflow.sh await <PR_NUMBER>
```

`status` (powered by `pr-status.sh`) surfaces the SonarCloud quality
gate, the OPEN-issue list with `[SEVERITY] [rule] path:line`, hotspots,
the deploy-preview URL, and the unresolved-inline-thread tally.
`await` polls `agex pr read --wait` first, then runs `status`, and
exits non-zero if Sonar reports `ERROR` or any thread is still open.

SonarCloud findings do **not** always arrive as inline PR comments — a
fully-resolved thread list plus an all-green `gh pr checks` is **not**
sufficient evidence that the PR is clean. If new findings show up,
loop back to Step 7. For non-standard project keys, set
`SONAR_PROJECT_KEY=<key>` before running the script (the default is
`<owner>_<repo>`).

## Step 10 — Wait for merge

**Never merge the PR yourself.** The PR is merged manually on the
GitHub site.

Report completion back to the IRC channel:

```bash
# Using the IRC skill
CULTURE_NICK="<agent-nick>" culture channel message "#general" "PR #<N> — all review threads addressed and resolved. Ready for merge."
```

## Script reference

| Script | Location | Purpose |
|--------|----------|---------|
| `workflow.sh <subcmd>` | `.claude/skills/cicd/scripts/` | Single entry point. Subcommands: `lint`, `open`, `read`, `reply`, `delta`, `status`, `await`, `help`. `lint` / `open` / `read` / `reply` / `delta` forward to `agex pr <verb>`; `status` / `await` are culture extensions. |
| `pr-status.sh <PR>` | `.claude/skills/cicd/scripts/` | One-shot status overview: PR state, CI checks, SonarCloud quality gate + issue count, inline-thread resolved tally. Backs `workflow.sh status` and the post-wait gate in `workflow.sh await`. |
| `pr-reply.sh [--resolve] <PR> <ID> "body"` | `.claude/skills/cicd/scripts/` | One-off single-comment reply. Auto-signs as `- <nick> (Claude)` via `_resolve-nick.sh`. Use when JSONL is overkill. |
| `_resolve-nick.sh` | `.claude/skills/cicd/scripts/` | Helper used by `pr-reply.sh`. Resolves the agent's nick from `<repo-root>/culture.yaml`'s first agent `suffix`, falling back to the repo basename. |
| `portability-lint.sh [--all]` | `.claude/skills/cicd/scripts/` | Catch absolute `/home/<user>/` paths and per-user dotfile references in committed docs/configs. Default mode lints the current diff (staged + unstaged); `--all` lints every tracked file. Run via `workflow.sh lint` (which forwards to `agex pr lint --exit-on-violation`). |

All scripts auto-detect `owner/repo` from the current git remote. The
full script set is vendored from steward (the AgentCulture alignment
hub) — re-cite from there if you need updates. Scripts that have
intentionally diverged from steward's upstream copy carry a
`# culture-divergence:` header documenting what was changed and why;
preserve those when re-citing.

## Conventions

`agex pr` emits a **"Next step:"** footer at the end of every command
that names the right next verb (the same chain `agex learn cicd`
documents) — follow that rather than memorizing an order. `workflow.sh
help` mirrors the verb table when you need the culture-flavored
extensions (`status`, `await`) on top.

## Migration from `create-pr-and-wait.sh`

`create-pr-and-wait.sh --push` is gone in culture 11.1.0. The
auto-push behavior (originally added under issue #318) was a
culture-divergence on top of the upstream steward script; `agex pr open`
has no `--push` equivalent yet. The replacement flow is two steps:

```bash
# OLD (culture <= 11.0.x):
bash .claude/skills/cicd/scripts/create-pr-and-wait.sh --push \
    --title "..." --body-file /tmp/pr-body.md

# NEW (culture >= 11.1.0):
git push -u origin HEAD
bash .claude/skills/cicd/scripts/workflow.sh open \
    --title "..." --body-file /tmp/pr-body.md
```

Same body-file ergonomics (large briefs still travel via tempfile under
the hood), same `--delayed-read` wait, same auto-signature on PR
body — only the push moves out into its own line. Bringing `--push`
back is filed upstream
([agex-cli#41](https://github.com/agentculture/agex-cli/issues/41)).

## Quick reference — full flow

```text
git checkout -b fix/my-fix
# ... make changes ...
# run-tests skill: bash .claude/skills/run-tests/...   (or pytest direct)
echo '{"fixed":["desc"]}' | python3 .claude/skills/version-bump/scripts/bump.py patch
git add <files> && git commit -m "message"
git push -u origin HEAD
bash .claude/skills/cicd/scripts/workflow.sh open \
    --title "..." --body-file /tmp/pr-body.md
# ... triage, fix issues, commit, push ...
bash .claude/skills/cicd/scripts/workflow.sh reply <PR> <<< '{"comment_id":N,"body":"Fixed"}'
bash .claude/skills/cicd/scripts/workflow.sh await <PR>     # SonarCloud-gated readiness
# Wait for manual merge — never merge yourself
```
