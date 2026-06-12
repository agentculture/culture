# Skill sources (provenance ledger)

The skills under `.claude/skills/` are **vendored, not depended on**. Culture
copies each one into its own tree and owns the copy — the *cite, don't import*
policy (formalised by [citation-cli](https://github.com/OriNachum/citation-cli)).
This file records where each skill comes from so a resync knows what to pull and
from where, and so a culture-side edit isn't silently overwritten on the next
propagation.

## How to read this table

- **Origin** — the repo that *authors* the skill (the source of truth for its
  behaviour).
- **Cite from** — the local sibling checkout a resync copies from. For most
  skills this is the hub, **guildmaster**, even when the *origin* is elsewhere
  (guildmaster re-broadcasts those). For `ask-colleague` the citation point is
  `colleague` itself today.
- **Divergence** — `culture-ahead` means culture's copy is intentionally *ahead*
  of the cited copy; such files carry a `# culture-divergence:` header and are
  offered back upstream rather than overwritten on resync. `verbatim` means the
  copy should match the cited source byte-for-byte (modulo the local provenance
  line).

## Ledger

| Skill | Origin | Cite from | Divergence |
|-------|--------|-----------|------------|
| `agent-config` | guildmaster (forked from steward) | `../guildmaster/.claude/skills/agent-config/` | culture-ahead (`scripts/show.sh`) |
| `ask-colleague` | **colleague** (first-party) | `../colleague/.claude/skills/ask-colleague/` | culture-ahead (prompts)¹ |
| `assign-to-workforce` | devague (re-broadcast via guildmaster) | `../guildmaster/.claude/skills/assign-to-workforce/` | verbatim |
| `cicd` | guildmaster | `../guildmaster/.claude/skills/cicd/` | culture-ahead (`status` / `await`, `scripts/workflow.sh`) |
| `communicate` | guildmaster | `../guildmaster/.claude/skills/communicate/` | culture-ahead (`scripts/fetch-issues.sh`) |
| `doc-test-alignment` | guildmaster | `../guildmaster/.claude/skills/doc-test-alignment/` | verbatim (stub) |
| `pypi-maintainer` | guildmaster | `../guildmaster/.claude/skills/pypi-maintainer/` | verbatim |
| `run-tests` | guildmaster | `../guildmaster/.claude/skills/run-tests/` | culture-ahead (`scripts/test.sh`) |
| `sonarclaude` | guildmaster | `../guildmaster/.claude/skills/sonarclaude/` | culture-ahead (`scripts/sonar.sh`) |
| `spec-to-plan` | devague (re-broadcast via guildmaster) | `../guildmaster/.claude/skills/spec-to-plan/` | verbatim |
| `think` | devague (re-broadcast via guildmaster) | `../guildmaster/.claude/skills/think/` | verbatim |
| `version-bump` | guildmaster | `../guildmaster/.claude/skills/version-bump/` | verbatim |

## Notes

- **guildmaster is the hub.** Since the steward → guildmaster cutover
  (2026-05-24), guildmaster is the skills supplier: it owns the canonical set and
  the broadcast verbs (`guild teach` / `guild onboard`). Most skills land in
  culture *from guildmaster*, including ones authored elsewhere — the **devague**
  workflow trio (`think`, `spec-to-plan`, `assign-to-workforce`) originates in
  [devague](https://github.com/agentculture/devague) and is re-broadcast through
  guildmaster.
- **`ask-colleague` is the inverse case.** It is **first-party to
  [colleague](https://github.com/agentculture/colleague)** — guildmaster could one
  day pull it *from* colleague and re-broadcast, but today the citation point is
  colleague itself (n/a upstream). Vendored fresh per
  [issue #446](https://github.com/agentculture/culture/issues/446).
- **culture-ahead copies are offered back upstream**, not overwritten on resync.
  Look for the `# culture-divergence:` header in the file to see why a copy
  diverges.

¹ `ask-colleague` is not byte-verbatim against colleague — one remaining
divergence to reapply after a re-vendor until upstream adopts it:

- **Prompts** (`prompts/{explore,review,write}.md`) carry **markdownlint-only**
  blank-line-around-list (MD032) fixes for culture's stricter config —
  whitespace only, no change to prompt content.

The earlier **`scripts/ask-colleague.sh`** divergence — a culture-ahead fix to
`resolve_colleague()` so the `uv run` local-dev fallback also searches the
`--repo` target, not just `$PWD` (PR #447), tracked upstream as
[colleague#181](https://github.com/agentculture/colleague/issues/181) — has been
**adopted upstream and re-vendored verbatim** from colleague#183
([PR #448](https://github.com/agentculture/culture/pull/448)). The
`# culture-divergence:` header is gone and the wrapper now matches upstream; the
behaviour (repo-aware `uv` fallback) is unchanged.
