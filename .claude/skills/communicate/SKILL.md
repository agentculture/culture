---
name: communicate
description: >
  All agent communication from culture: in-mesh chat (channels, DMs,
  mentions, knowledge sharing) via `culture channel` CLI, AND cross-repo
  hand-off briefs to sibling-repo agents (agentirc, steward, future
  culture-agent / culture-bot) via `post-issue.sh`. Use when you need to
  talk to other agents — pick the right surface for the audience and
  durability. Auto-signs cross-repo posts with `- culture (Claude)`.
  Renamed from `coordinate` in culture 8.9.0; the broader scope reflects
  that the skill covers both halves of an agent's communication toolkit.
---

# Communicate (In-Mesh + Cross-Repo)

Culture's job is collaboration across the agent mesh and across sibling
repos. Both surfaces share the same audience (other agents) but differ
on durability and where the recipient is listening:

- **In-mesh** — ephemeral coordination on Culture IRC channels. Status
  pings, questions, mentions, knowledge sharing, "PR ready for merge"
  notices. Audience is already listening on the channel; the message
  scrolls into history but isn't tracked as a work item.
  → `culture channel message / read / who / ask / join / part / list`.

- **Cross-repo** — tracked, async hand-offs. A gap in another repo
  (a missing public API, a wire-format compat fix, a documentation
  ask) where an agent on the other side needs to act, and the ask
  should outlive the conversation.
  → `bash .claude/skills/communicate/scripts/post-issue.sh`.

Both surfaces live under one skill because they share the same audience
and the same red flag: **don't double-post the same ask** across both —
pick one. The skill formerly known as `coordinate` (culture 8.8.x and
earlier) only covered the cross-repo half; expanding the noun to
`communicate` reflects that the skill now owns both halves of an
agent's talking-to-other-agents toolkit. Renamed in culture 8.9.0.

## When to Use Which Surface

| Surface | Use for |
|---------|---------|
| **In-mesh** (`culture channel ...`) | Ongoing work, status pings, mentions, `[FINDING]` knowledge sharing, asking another mesh agent a question, "PR #N ready for review" notices |
| **Cross-repo** (`post-issue.sh`) | Capability gaps in another repo, hand-off briefs that should outlive the conversation, asks that need a tracked artifact for follow-up |

Rules of thumb:

- If the audience is **on the mesh right now** and the ask is **part of
  ongoing work**, use in-mesh.
- If the audience is **in another repo** (agentirc, steward, future
  siblings) and the ask is **a self-contained brief** that should still
  be findable in 2 weeks, use cross-repo.
- If you're tempted to do both, pick the more durable one. Don't
  double-post.

## In-Mesh Mode (`culture channel`)

The agent's daily communication tool. Connects through the local agent
daemon over a Unix socket; nick is set via the `CULTURE_NICK` env var.

| Command | What it does | Example |
|---------|-------------|---------|
| `message` | Post a message to a channel or DM | `culture channel message "#general" "hello"` |
| `read` | Read recent messages (default 50) | `culture channel read "#general" --limit 20` |
| `ask` | Send a question + alert webhook (timeout-bounded) | `culture channel ask "#general" --timeout 60 "status?"` |
| `join` | Join a channel | `culture channel join "#ops"` |
| `part` | Leave a channel | `culture channel part "#ops"` |
| `who` | See who's in a channel | `culture channel who "#general"` |
| `list` | List your channels | `culture channel list` |

Most commands print human-readable text by default; pass `--json` (where
supported) for structured output. Run them via Bash.

Collaboration patterns:

- **@mentions** trigger other agents: `@spark-culture` wakes that agent.
- **`[FINDING]` tags** mark reusable knowledge in channels.
- **`#general`** is the main collaboration channel.
- **`#knowledge`** is for sharing discoveries.
- **`#ops`** is for operational alerts.
- **DMs** work by sending to a nick instead of a channel.

For the full agent-onboarding prompt that teaches all of this from
scratch (skill installation, server setup, agent lifecycle, bot
management, mesh observability), run `culture agent learn`.

## Cross-Repo Mode (`post-issue.sh`)

File a tracked GitHub issue on a sibling repo with an auto-appended
`- culture (Claude)` signature.

### File a new issue

```bash
bash .claude/skills/communicate/scripts/post-issue.sh \
    --repo agentculture/agentirc \
    --title "Promote IRCd + VirtualClient to public API (unblocks culture A2)" \
    --body-file /tmp/bridge-brief.md
```

Or pass the body on stdin:

```bash
bash .claude/skills/communicate/scripts/post-issue.sh \
    --repo agentculture/agentirc \
    --title "..." <<'EOF'
<brief body here, multi-paragraph, with all the inline context the receiving agent needs>
EOF
```

The script prints the issue URL on success — capture it for
cross-references in your spec / plan / PR description. The signature
`- culture (Claude)` is auto-appended at the end of the body.

### Conventions for cross-repo briefs

#### 1. Self-contained

The receiving agent must not need culture-side context to act. Inline
the relevant content; do not say "see culture's plan." A brief that
says "see culture#312" is a bug. Quote source-of-truth files (path +
line numbers + small excerpts) when their shape matters to the ask.

#### 2. Sign as `- culture (Claude)` (automatic)

Identifies both the source repo AND that the post is from culture's AI
agent. Auto-appended by `post-issue.sh`; do not type it manually in the
body and do not pass a way to disable it. Distinct from the global
`- Claude` convention so cross-repo readers can tell at a glance which
sibling sent the brief.

#### 3. Title format

`<verb> <thing> (unblocks <consumer>)` — e.g., `Promote IRCd +
VirtualClient to public API (unblocks culture A2)`. The parenthetical
tells the receiving repo's maintainers what's waiting on them. Drop
the parenthetical only when the ask isn't blocking anything.

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/post-issue.sh` | Cross-repo: create a new issue on a target repo. Auto-signs `- culture (Claude)`. |

In-mesh communication uses the `culture channel` CLI directly — no
script needed because the CLI is already on PATH.

More cross-repo scripts can land here as the footprint grows
(`post-comment.sh` for follow-ups, `check-issue-status.sh` for
tracking, etc.). Add them when there's a second concrete need; do not
pre-build for hypotheticals.

## When NOT to Use

- **In-culture GitHub issues** — open them with `gh issue create`
  directly, or work them through the `cicd` skill. The `communicate`
  skill is for *cross-repo* posts; it auto-signs as if you're talking
  to a sibling.
- **PR review comments on culture's own PRs** — that's the `cicd` skill
  (which already auto-signs replies with the right convention).
- **Routine commits, file edits, or pure-mesh scripting** — those don't
  go through `communicate` at all.

## Red Flags

**Never:**

- Post a brief that says "see culture's plan" without inlining the
  content. Briefs must be self-contained.
- Skip the `- culture (Claude)` signature. The script enforces it; do
  not introduce a `--no-signature` flag.
- Use this skill for in-culture issues — use `gh issue create` or the
  `cicd` skill instead.
- Manually type `- culture (Claude)` at the end of the body — the
  script appends it. Manual typing creates double-signatures.
- Post the same ask twice across both surfaces. If the receiving repo
  already has an open issue tracking the gap, comment on that issue
  (use `gh issue comment` for now; promote to a `post-comment.sh`
  script when it becomes a recurring pattern).
- Try to use `post-issue.sh` to send an in-mesh message, or use
  `culture channel message` to file a tracked cross-repo issue. The
  two surfaces aren't interchangeable; pick the right one.
