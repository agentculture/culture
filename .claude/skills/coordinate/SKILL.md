---
name: coordinate
description: >
  Cross-repo coordination from culture: file issues, post comments, and
  hand off briefs to sibling-repo agents (agentirc, future culture-agent
  / culture-bot). Use when the next step lives in another repo and an
  agent there needs to act on it. Auto-signs every post with
  `- culture (Claude)`. Not for in-culture issues — use `pr-review` or
  `gh issue create` directly for those.
---

# Coordinate (Cross-Repo)

The home for cross-repo coordination originating from culture. The pattern recurs (#308 → agentirc#15 → agentirc 9.5.0; agentirc#15 closing → next bridge issue → agentirc 9.6); without dedicated infrastructure each instance is an ad-hoc `gh issue create` with hand-edited body and manually-typed signature. This skill enforces the conventions and version-controls the scripts.

## When to Use

- A gap surfaces in **another repo's public surface** (a missing public API, a wire-format compat fix, a documentation ask) and you need an agent on the other side to act.
- You're handing off a Track-B-style brief to a sibling-repo agent (agentirc today; culture-agent / culture-bot in future splits).
- You're asking the sibling repo a question that benefits from a tracked issue rather than ephemeral chat.

## When NOT to Use

- **In-culture issues** — open them with `gh issue create` directly, or work them through the `pr-review` skill.
- **PR review comments** — that's the `pr-review` skill (which already auto-signs replies).
- **Routine commits** — those don't get a cross-repo signature.

## Conventions

### 1. Hand-off briefs are self-contained

Same discipline as the Track B brief in `docs/superpowers/specs/2026-04-30-agentirc-extraction-design.md` — the receiving agent must not need culture-side context to act. Inline the relevant content; do not say "see culture's plan."

A brief that says "see culture#312" is a bug. The receiving agent will look at culture#312, get lost in culture-specific context that's irrelevant to them, and either ask for clarification (slow round-trip) or guess wrong (worse). Inline the ask, the rationale, and concrete acceptance criteria. Quote source-of-truth files (path + line numbers + small excerpts) when their shape matters to the ask.

### 2. Sign as `- culture (Claude)`

Identifies both the source repo AND that the post is from culture's AI agent. `post-issue.sh` auto-appends this signature; do not type it manually in the body and do not pass a way to disable it.

This is intentionally distinct from the global `- Claude` convention (which doesn't identify the originating repo). Cross-repo readers can tell at a glance whether a brief came from culture, agentirc, or a future sibling.

### 3. Title format

`<verb> <thing> (unblocks <consumer>)` — e.g., `Promote IRCd + VirtualClient to public API (unblocks culture A2)`. The parenthetical tells the receiving repo's maintainers what's waiting on them. Drop the parenthetical only when the ask isn't blocking anything.

## How to Invoke

### File a new issue

```bash
bash .claude/skills/coordinate/scripts/post-issue.sh \
    --repo agentculture/agentirc \
    --title "Promote IRCd + VirtualClient to public API (unblocks culture A2)" \
    --body-file /tmp/bridge-brief.md
```

Or pass the body on stdin:

```bash
bash .claude/skills/coordinate/scripts/post-issue.sh \
    --repo agentculture/agentirc \
    --title "..." <<'EOF'
<brief body here, multi-paragraph, with all the inline context the receiving agent needs>
EOF
```

The script prints the issue URL on success — capture it for cross-references in your spec / plan / PR description. The signature `- culture (Claude)` is auto-appended at the end of the body.

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/post-issue.sh` | Create a new issue on a target repo. Auto-signs `- culture (Claude)`. |

More scripts can land here as the cross-repo coordination footprint grows — `post-comment.sh` for follow-ups, `check-issue-status.sh` for tracking, etc. Add them when there's a second concrete need; do not pre-build for hypotheticals.

## Red Flags

**Never:**

- Post a brief that says "see culture's plan" without inlining the content. Briefs must be self-contained.
- Skip the signature. The script enforces it; do not introduce a `--no-signature` flag.
- Use this skill for in-culture issues — use `gh issue create` or the `pr-review` skill instead.
- Manually type `- culture (Claude)` at the end of the body — the script appends it. Manual typing creates double-signatures when the script is later refactored.
- Post the same ask twice. If the receiving repo already has an open issue tracking the gap, comment on that issue (use `gh issue comment` for now; promote to a `post-comment.sh` script when it becomes a recurring pattern).
