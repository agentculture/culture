---
name: pr-review
description: >
  Full PR workflow for culture: branch, commit, push, create PR, wait for
  automated reviewers, fetch comments, fix or pushback, reply, resolve threads.
  Use when: creating PRs, handling review feedback, or the user says
  "create PR", "review comments", "address feedback", or "resolve threads".
---

# PR Review Workflow

Complete pull request lifecycle for the culture project. Follow every step
in order.

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
   echo '{"fixed":["..."]}' | python3 ~/.claude/skills/version-bump/scripts/bump.py patch
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

5. Push:

   ```bash
   git push -u origin <branch-name>
   ```

## Step 3 — Create PR

```bash
gh pr create --title "Short title" --body "$(cat <<'EOF'
## Summary
- Bullet points describing changes

## Test plan
- [ ] Test items

- Claude
EOF
)"
```

## Step 4 — Wait for reviewers

Automated reviewers (Qodo, Copilot) need time to post comments.

**Wait 5 minutes** after creating the PR before checking for comments:

```bash
sleep 300
```

## Step 5 — Poll for comments

After the initial wait, poll every 60 seconds until comments appear:

```bash
bash ~/.claude/skills/pr-review/scripts/pr-comments.sh <PR_NUMBER>
```

If no comments yet, wait and retry:

```bash
sleep 60
bash ~/.claude/skills/pr-review/scripts/pr-comments.sh <PR_NUMBER>
```

Continue until at least one unresolved comment exists, or 3 consecutive polls
return zero comments (reviewers are done / not configured).

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
bash ~/.claude/skills/pr-review/scripts/pr-batch.sh --resolve <PR_NUMBER> <<'EOF'
{"comment_id": 123, "body": "Fixed -- changed X to Y.\n\n- Claude"}
{"comment_id": 456, "body": "Intentional -- this follows the pattern in Z because...\n\n- Claude"}
EOF
```

Or reply to a single comment:

```bash
bash ~/.claude/skills/pr-review/scripts/pr-reply.sh --resolve <PR_NUMBER> <COMMENT_ID> "Fixed -- updated.\n\n- Claude"
```

**Important:**

- Always sign replies with `\n\n- Claude`
- Always use `--resolve` to resolve the thread after replying
- Every comment must get a reply — no silent fixes

## Step 9 — Wait for merge

**Never merge the PR yourself.** The PR is merged manually on the GitHub site.

Report completion back to the IRC channel:

```bash
# Using the IRC skill
CULTURE_NICK=<your-nick> python3 -m culture.clients.claude.skill.irc_client \
  send "#general" "PR #<N> — all review threads addressed and resolved. Ready for merge."
```

## Script reference

| Script | Purpose |
|--------|---------|
| `pr-comments.sh <PR>` | Fetch all review comments |
| `pr-reply.sh [--resolve] <PR> <ID> "body"` | Reply to one comment |
| `pr-batch.sh [--resolve] <PR> < jsonl` | Batch reply from JSONL stdin |

All scripts auto-detect `owner/repo` from the current git remote.

## Quick reference — full flow

```text
git checkout -b fix/my-fix
# ... make changes ...
uv run pytest tests/ -x -q
echo '{"fixed":["desc"]}' | python3 ~/.claude/skills/version-bump/scripts/bump.py patch
git add <files> && git commit -m "message"
git push -u origin fix/my-fix
gh pr create --title "..." --body "..."
sleep 300
bash ~/.claude/skills/pr-review/scripts/pr-comments.sh <PR>
# ... fix issues, commit, push ...
bash ~/.claude/skills/pr-review/scripts/pr-batch.sh --resolve <PR> <<< '{"comment_id":N,"body":"Fixed\n\n- Claude"}'
# Wait for manual merge — never merge yourself
```
