---
name: cicd
description: >
  CI/CD lane for culture: branch, commit, push, create PR, wait for automated
  reviewers, fetch comments, fix or pushback, reply, resolve threads. Renamed
  from `pr-review` in culture 8.8.1 to align with steward 0.7's skill-name
  convention; the same trigger phrases keep working. Use when: creating PRs,
  handling review feedback, or the user says "create PR", "review comments",
  "address feedback", "resolve threads", or "use pr-review" / "use cicd".
---

# CI/CD Lane (formerly pr-review)

Complete pull request lifecycle for the culture project. Follow every step
in order. The skill was renamed from `pr-review` to `cicd` in culture 8.8.1;
existing prompts that say "use pr-review" still resolve here through the
trigger phrases above.

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
2. Run tests: `uv run pytest tests/ -x -q`
3. Bump the version (required before PR):

   ```bash
   echo '{"fixed":["..."]}' | python3 .claude/skills/version-bump/scripts/bump.py patch
   ```

4. Stage and commit:

   ```bash
   git add <files>
   git commit -m "$(cat <<'EOF'
   Commit message here.

   Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
   EOF
   )"
   ```

5. Push — pass `--push` to `create-pr-and-wait.sh` in Step 3 instead of
   pushing manually. If you want to push without opening a PR yet, use:

   ```bash
   git push -u origin <branch-name>
   ```

## Step 3 — Create PR (and wait for reviewers in one shot)

**Recommended:** use `create-pr-and-wait.sh --push`. It pushes the current
branch, runs `gh pr create`, sleeps 3 minutes, then dumps all reviewer
feedback in one invocation — no separate step 4 and no separate `git push`.
Automated reviewers (qodo, copilot, sonarcloud) need that 3-minute window
to post; checking sooner returns zero comments and forces you to poll.

```bash
bash .claude/skills/cicd/scripts/create-pr-and-wait.sh --push \
    --title "Short title" \
    --body-file /tmp/pr-body.md
```

Or pipe the body via heredoc on stdin:

```bash
bash .claude/skills/cicd/scripts/create-pr-and-wait.sh --push \
    --title "Short title" <<'EOF'
## Summary
- Bullet points describing changes

## Test plan
- [ ] Test items

- Claude
EOF
```

The script prints the new PR URL on stdout, then sleeps 180s (override with
`--wait SECS`), then runs `pr-comments.sh` so feedback lands in your output
when control returns. The body always travels via `--body-file` to a tempfile,
so large self-contained briefs don't hit the OS argv length limit (the
failure mode that motivated issue #318). Pass any extra `gh pr create` flags
through positionally (e.g. `--base main --reviewer @user`). Drop `--push` if
you've already pushed the branch yourself.

**If you must do it by hand** (e.g. PR was opened earlier and you only need
to fetch feedback now):

```bash
gh pr create --title "Short title" --body-file /tmp/pr-body.md
sleep 180   # qodo/copilot/sonarcloud need ~3 min to post
bash .claude/skills/cicd/scripts/pr-comments.sh <PR_NUMBER>
```

Do **not** check for comments before the 3-minute mark. Empty comment lists
in the first 1–2 minutes don't mean reviewers are done — they mean reviewers
haven't started yet.

## Step 4 — Wait another window if needed

If `create-pr-and-wait.sh`'s output came back empty or thin, or you suspect
a slow reviewer (or you just pushed a follow-up commit and want a fresh
review pass), use `wait-and-check.sh` for a deliberate second 3-minute
window:

```bash
bash .claude/skills/cicd/scripts/wait-and-check.sh <PR_NUMBER>
```

This is **not** polling — it's "give the reviewers one more deliberate
window before deciding they're done." Override with `--wait SECS` if you
need a different duration. If the second window is also empty, fall back
to polling:

```bash
bash .claude/skills/cicd/scripts/pr-comments.sh <PR_NUMBER>
# if still empty:
sleep 60
bash .claude/skills/cicd/scripts/pr-comments.sh <PR_NUMBER>
```

Three consecutive polls returning zero comments means reviewers are done /
not configured; proceed to step 5 (or skip directly to merge if there's
truly nothing to address).

## Step 6 — Triage each comment

For every review comment, decide:

- **FIX** — valid concern, make the code change
- **PUSHBACK** — disagree, explain why in the reply

Guidelines:

- Exception type mismatches (e.g., breaking reconnect loops) are always valid
- Test requests are always valid — add them
- Style nits are usually valid — fix them
- Architecture opinions may warrant pushback if they conflict with project
  conventions (check `CLAUDE.md` and `docs/`)

## Step 7 — Fix code and push

1. Make all code fixes
2. Run tests: `uv run pytest tests/ -x -q`
3. Commit with a descriptive message
4. Push: `git push`

## Step 8 — Reply and resolve threads

Use batch mode to reply to all comments at once:

```bash
bash .claude/skills/cicd/scripts/pr-batch.sh --resolve <PR_NUMBER> <<'EOF'
{"comment_id": 123, "body": "Fixed -- changed X to Y.\n\n- Claude"}
{"comment_id": 456, "body": "Intentional -- this follows the pattern in Z because...\n\n- Claude"}
EOF
```

Or reply to a single comment:

```bash
bash .claude/skills/cicd/scripts/pr-reply.sh --resolve <PR_NUMBER> <COMMENT_ID> "Fixed -- updated.\n\n- Claude"
```

**Important:**

- Always sign replies with `\n\n- Claude`
- Always use `--resolve` to resolve the thread after replying
- Every comment must get a reply — no silent fixes

## Step 9 — Check SonarCloud before declaring ready

After CI is green and all inline threads are resolved, query SonarCloud
for the branch via the `/sonarclaude` skill. SonarCloud findings do not
always arrive as inline PR comments — a fully-resolved thread list plus an
all-green `gh pr checks` is **not** sufficient evidence that the PR is
clean. If `/sonarclaude` surfaces new findings, loop back to Step 7.

## Step 10 — Wait for merge

**Never merge the PR yourself.** The PR is merged manually on the GitHub site.

Report completion back to the IRC channel:

```bash
# Using the IRC skill
CULTURE_NICK="<agent-nick>" culture channel message "#general" "PR #<N> — all review threads addressed and resolved. Ready for merge."
```

## Script reference

| Script | Location | Purpose |
|--------|----------|---------|
| `create-pr-and-wait.sh` | `.claude/skills/cicd/scripts/` (project) | Optional `git push` (`--push`) + `gh pr create --body-file` + `sleep 180` + `pr-comments.sh` in one invocation |
| `wait-and-check.sh <PR>` | `.claude/skills/cicd/scripts/` (project) | `sleep 180` + `pr-comments.sh` for an existing PR (a second deliberate window after `create-pr-and-wait.sh` or after a follow-up push) |
| `pr-comments.sh <PR>` | `.claude/skills/cicd/scripts/` (project) | Fetch all review comments |
| `pr-reply.sh [--resolve] <PR> <ID> "body"` | `.claude/skills/cicd/scripts/` (project) | Reply to one comment |
| `pr-batch.sh [--resolve] <PR> < jsonl` | `.claude/skills/cicd/scripts/` (project) | Batch reply from JSONL stdin |

All scripts auto-detect `owner/repo` from the current git remote. The trio
(`pr-comments.sh`, `pr-reply.sh`, `pr-batch.sh`) is vendored from steward
(the AgentCulture alignment hub) — re-cite from there if you need updates.

## Quick reference — full flow

```text
git checkout -b fix/my-fix
# ... make changes ...
uv run pytest tests/ -x -q
echo '{"fixed":["desc"]}' | python3 .claude/skills/version-bump/scripts/bump.py patch
git add <files> && git commit -m "message"
bash .claude/skills/cicd/scripts/create-pr-and-wait.sh --push \
    --title "..." --body-file /tmp/pr-body.md
# (pushes the branch, then waits 3 min, then dumps reviewer comments)
# ... fix issues, commit, push ...
bash .claude/skills/cicd/scripts/pr-batch.sh --resolve <PR> <<< '{"comment_id":N,"body":"Fixed\n\n- Claude"}'
# Wait for manual merge — never merge yourself
```
