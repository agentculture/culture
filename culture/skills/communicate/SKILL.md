---
name: communicate
description: >
  Cross-repo + mesh communication: file tracked GitHub issues on sibling
  repos and send live messages to Culture mesh channels. Use when the
  next step lives outside the current repo (a brief for a sibling-repo
  agent, a status ping for a Culture channel). Issue posts auto-sign
  with `- culture (Claude)`; mesh messages are unsigned (the IRC nick
  is the speaker).
---

<!--
Vendored from agentculture/steward → .claude/skills/communicate/SKILL.md.
Adapted for culture: signature literal is `- culture (Claude)` (the culture
mesh, acting as platform, is the speaker for cross-repo posts coming from
this skill). Mesh-message handling is unchanged — culture's own CLI is
already the underlying transport.
-->

# Communicate (Cross-Repo + Mesh)

Culture's role is to coordinate work across the AgentCulture mesh; that
surfaces in two distinct channels:

- **Tracked, async hand-offs** — a gap in another repo (a missing public
  API, a divergent skill, a documentation ask) where an agent on the
  other side needs to act, and the ask should outlive the conversation.
  → `post-issue.sh` (GitHub).
- **Ephemeral coordination** — a status ping, a question, a "PR ready
  for merge" notice on a Culture mesh channel where the audience is
  already listening.
  → `mesh-message.sh` (Culture IRC).

Both channels live under one skill because they share the same audience
(sibling-repo agents) and the same red flag (don't double-post the same
ask across both — pick one).

## When to Use

### Issue mode (`post-issue.sh`)

- A gap surfaces in **another repo's surface** (missing public API,
  wire-format compat fix, divergent skill, documentation ask).
- You're handing off a self-contained brief to a sibling-repo agent.
- You're asking a question that benefits from a tracked artifact rather
  than ephemeral chat.

### Mesh mode (`mesh-message.sh`)

- You want to ping a Culture channel with a status update ("PR #N ready
  for merge", "starting nightly corpus scan").
- You're asking a question where you expect a fast reply from whoever
  is listening on the channel right now.
- You're announcing a decision that doesn't need a tracked artifact.

## When NOT to Use

- **In-repo issues for the repo you're currently in** — open them with
  `gh issue create` directly.
- **PR review comments** — those have their own per-repo workflow that
  already auto-signs replies.
- **Routine commits** — those don't get cross-repo signatures.
- **Long-form asks on the mesh** — anything that needs acceptance
  criteria belongs in an issue, not a channel message.

## Conventions

### 1. Briefs are self-contained

The receiving agent must not need culture-side context to act. Inline
the relevant content; do not say "see culture's plan."

A brief that says "see culture#NN" is a bug. The receiving agent will
look at it, get lost in culture-specific context that's irrelevant to
them, and either ask for clarification (slow round-trip) or guess wrong
(worse). Inline the ask, the rationale, and concrete acceptance
criteria. Quote source-of-truth files (path + line numbers + small
excerpts) when their shape matters to the ask.

### 2. Per-channel signature rules

| Channel | Signature | Why |
|---------|-----------|-----|
| GitHub issues | `- culture (Claude)` (auto-appended by `post-issue.sh`) | Cross-repo audit trail — readers can tell at a glance which sibling and that it came from an AI. |
| Culture mesh | none — unsigned | The IRC nick already identifies the speaker. A trailing `- culture (Claude)` would be visual noise that the nick already supplies. |

This vendored copy hard-codes its own issue-signature literal —
`communicate` deliberately stays self-contained per the
skills-portability rule. Mesh messages stay unsigned across all vendors.

### 3. Issue title format

`<verb> <thing> (unblocks <consumer>)` — e.g.,
`Vendor portability-lint into <repo> (unblocks culture 10.x doctor --apply)`.
The parenthetical tells the receiving repo's maintainers what's waiting
on them. Drop the parenthetical only when the ask isn't blocking
anything.

## How to Invoke

### File a new issue

```bash
bash <skill-dir>/scripts/post-issue.sh \
    --repo agentculture/<sibling> \
    --title "Vendor portability-lint into <sibling> (unblocks culture 10.x)" \
    --body-file /tmp/brief.md
```

Or pass the body on stdin:

```bash
bash <skill-dir>/scripts/post-issue.sh \
    --repo agentculture/<sibling> \
    --title "..." <<'EOF'
<brief body here, multi-paragraph, with all the inline context the receiving agent needs>
EOF
```

The script prints the issue URL on success — capture it for
cross-references in your spec / plan / PR description. The signature
`- culture (Claude)` is auto-appended at the end of the body.

### Send a mesh channel message

```bash
bash <skill-dir>/scripts/mesh-message.sh \
    --channel "#general" \
    --body "PR #42 — all review threads addressed. Ready for merge."
```

Body can also come from `--body-file PATH` or stdin. The script wraps
`culture channel message <target> <text>` and forwards exit codes
unchanged, so failures (no Culture server, agent not connected) surface
verbatim. No signature is appended — the IRC nick is the speaker.

The script requires `culture` on PATH and an active mesh agent for the
current shell (set `CULTURE_NICK`). If either is missing the script
fails with the underlying CLI error verbatim — fix the registration,
don't paper over it.

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/post-issue.sh` | Create a new issue on a target repo. Auto-signs `- culture (Claude)`. |
| `scripts/mesh-message.sh` | Send a message to a Culture mesh channel. Unsigned (IRC nick is the speaker). |

More scripts can land here as the communication footprint grows —
`post-comment.sh` for issue follow-ups, `mesh-ask.sh` for
question-shaped pings via `culture channel ask`, etc. Add them when
there's a second concrete need; do not pre-build for hypotheticals.

## Red Flags

**Never:**

- Post a brief that says "see culture's plan" without inlining the
  content. Briefs must be self-contained.
- Skip the issue signature. The script enforces it; do not introduce a
  `--no-signature` flag.
- Sign mesh messages with `- culture (Claude)`. The nick already says
  who you are.
- Use this skill for in-repo issues — use `gh issue create` instead.
- Manually type `- culture (Claude)` at the end of an issue body — the
  script appends it. Manual typing creates double-signatures when the
  script is later refactored.
- Post the same ask twice across channels (issue + mesh). Pick one.
  Tracked → issue. Ephemeral → mesh.
- Use mesh mode for anything that needs acceptance criteria. If the
  receiving agent has to decide "did I do this right?", you owe them
  an issue.

---

Source: agentculture/steward → `.claude/skills/communicate/SKILL.md`. Vendored 2026-05-05.
