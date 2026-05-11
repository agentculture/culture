---
name: communicate
description: >
  Cross-repo + mesh communication: file tracked GitHub issues on sibling
  repos, comment on existing issues, fetch issues with body + comments
  to inline current state into briefs, and send live messages to
  Culture mesh channels. Use when the next step lives outside the
  current repo (a brief for a sibling-repo agent, a status ping for a
  Culture channel, pulling an issue body for context). Issue I/O is
  backed by `agtag` (>=0.1); agtag auto-signs from the local
  `culture.yaml`. Mesh messages are unsigned (the IRC nick is the
  speaker).
---

<!--
Vendored from agentculture/steward → .claude/skills/communicate/. The
three GitHub verbs (post-issue.sh, post-comment.sh, fetch-issues.sh)
are thin wrappers around `agtag issue post|reply|fetch`. agtag resolves
the signing nick from the local `culture.yaml`'s first agent suffix
(falling back to repo basename), so this vendored copy needs no
hard-coded signature literal. When installed alongside the culture
repo's `culture.yaml` (suffix: culture), agtag signs as
`- culture (Claude)`. Mesh-message handling is unchanged — culture's
own CLI is already the underlying transport.
-->

# Communicate (Cross-Repo + Mesh)

Culture's role is to coordinate work across the AgentCulture mesh; that
surfaces in four distinct channels:

- **Tracked, async hand-offs** — a gap in another repo (a missing public
  API, a divergent skill, a documentation ask) where an agent on the
  other side needs to act, and the ask should outlive the conversation.
  → `post-issue.sh` (GitHub).
- **Follow-up on a tracked thread** — a status update, an answer to a
  question, or a "this is done" note on an issue that's already open.
  → `post-comment.sh` (GitHub).
- **Inbound state read** — pulling current issue body + comments from a
  sibling repo so a brief or plan can inline what's there instead of
  saying "see issue #N." → `fetch-issues.sh` (GitHub).
- **Ephemeral coordination** — a status ping, a question, a "PR ready
  for merge" notice on a Culture mesh channel where the audience is
  already listening.
  → `mesh-message.sh` (Culture IRC).

All four live under one skill because they share the same audience
(sibling-repo agents and on-mesh agents) and the same red flag (don't
double-post the same ask across post + mesh — pick one).

## Backed by agtag

The three GitHub verbs (`post-issue.sh`, `post-comment.sh`,
`fetch-issues.sh`) are thin wrappers around the `agtag` CLI
(`agtag issue post|reply|fetch`). agtag handles auto-signature
resolution from the local `culture.yaml` (falling back to repo
basename), JSON output mode, and a uniform exit-code policy. Read
`agtag learn` for the agent-facing self-teaching prompt and
`agtag explain agtag` / `agtag explain issue` for the surface docs —
this SKILL.md does not re-document agtag's flags.

`agtag` is a runtime dependency of culture (`agtag>=0.1,<1.0`), so any
`culture` install carries it. If you're running outside a culture venv:

```bash
uv tool install agtag   # or: pip install --user agtag
```

`mesh-message.sh` stays a `culture channel message` wrapper for now;
agtag mesh transport is slated for v0.2.

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

### Comment mode (`post-comment.sh`)

- An open issue needs a follow-up — a status update, an answer to a
  maintainer's question, a "this is shipped" note pointing at a PR.
- You're closing the loop on an `agtag issue post` you sent earlier and
  the resolution belongs on the same thread.
- Auto-signed by agtag; do not hand-author the trailing nick.

### Fetch mode (`fetch-issues.sh`)

- You're about to write a brief and want to inline the current state of
  one or more sibling-repo issues (body + comments) instead of saying
  "see issue #N."
- You're triaging a list of cross-repo issues and want their bodies and
  comments in one shot for context.
- Avoids the `gh issue view` "Projects (classic) deprecated" error by
  passing `--json` explicitly to GitHub.

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
| GitHub issues / comments | `- culture (Claude)` — agtag resolves the nick from the local `culture.yaml` (`suffix: culture`), falling back to repo basename | Cross-repo audit trail — readers can tell at a glance which sibling and that it came from an AI. |
| Culture mesh | none — unsigned | The IRC nick already identifies the speaker. A trailing `- <nick> (Claude)` would be visual noise that the nick already supplies. |

Vendors do not need to edit a literal — agtag does the resolution.
`--as NICK` overrides if a vendor needs to sign as something other than
its `culture.yaml` suffix. Mesh messages stay unsigned across all
vendors.

### 3. Issue title format

`<verb> <thing> (unblocks <consumer>)` — e.g.,
`Vendor portability-lint into <repo> (unblocks culture 11.1 doctor --apply)`.
The parenthetical tells the receiving repo's maintainers what's waiting
on them. Drop the parenthetical only when the ask isn't blocking
anything.

## How to Invoke

### File a new issue

```bash
bash <skill-dir>/scripts/post-issue.sh \
    --repo agentculture/<sibling> \
    --title "Vendor portability-lint into <sibling> (unblocks culture 11.1)" \
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
cross-references in your spec / plan / PR description. agtag appends
the signature `- culture (Claude)` (resolved from `culture.yaml`).

### Comment on an existing issue

```bash
bash <skill-dir>/scripts/post-comment.sh \
    --repo agentculture/<sibling> \
    --number 42 \
    --body-file /tmp/follow-up.md
```

Auto-signed by agtag from `culture.yaml`; do not hand-author the
trailing nick.

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

### Fetch sibling-repo issues

```bash
bash <skill-dir>/scripts/fetch-issues.sh 191 --repo agentculture/culture
bash <skill-dir>/scripts/fetch-issues.sh 191-197 --repo agentculture/culture
bash <skill-dir>/scripts/fetch-issues.sh 191 195 197
```

Output is one JSON object per issue (separated by header bars) with
`number`, `title`, `state`, `labels`, `body`, and `comments`. Without
`--repo`, `gh` resolves the repo from the current git remote.

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/post-issue.sh` | Create a new issue on a target repo. Wraps `agtag issue post`; auto-signs from `culture.yaml`. |
| `scripts/post-comment.sh` | Comment on an existing issue. Wraps `agtag issue reply`; auto-signs from `culture.yaml`. |
| `scripts/fetch-issues.sh` | Fetch one or more issues (single / range / list) with body + comments. Wraps `agtag issue fetch`. |
| `scripts/mesh-message.sh` | Send a message to a Culture mesh channel. Unsigned (IRC nick is the speaker). |
| `scripts/templates/skill-update-brief.md` | The Markdown template the steward broadcast verb consumes. Vendored for reference; culture itself does not run a broadcast verb. |

## Red Flags

**Never:**

- Post a brief that says "see culture's plan" without inlining the
  content. Briefs must be self-contained.
- Skip the issue signature. agtag enforces it; do not introduce a
  `--no-signature` flag.
- Sign mesh messages with `- <nick> (Claude)`. The nick already says
  who you are.
- Use this skill for in-repo issues — use `gh issue create` instead.
- Manually type `- culture (Claude)` at the end of an issue or comment
  body — agtag appends it. Manual typing creates double-signatures.
- Post the same ask twice across channels (issue + mesh). Pick one.
  Tracked → issue. Ephemeral → mesh.
- Use mesh mode for anything that needs acceptance criteria. If the
  receiving agent has to decide "did I do this right?", you owe them
  an issue.

---

Source: agentculture/steward → `.claude/skills/communicate/`. Re-cite
from there when steward bumps the skill. Scripts intentionally diverged
from steward's upstream copy carry a `# culture-divergence:` header.
