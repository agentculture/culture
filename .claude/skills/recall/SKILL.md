---
name: recall
type: command
description: >
  Search the shared eidetic memory store and get back ranked, provenanced
  records. Drives `eidetic recall` with four search modes — exact (verbatim
  substring), approximate (vector/semantic), keyword (BM25 lexical), and hybrid
  (a weighted blend of vector+keyword, the default) — each hit carrying its
  text, full metadata, a relevance `score`, and a freshness `signal`. Recall
  passively reinforces matched records (bumps last_recall + recall_count).
  Shadowed and archived records are excluded by default; use
  --include-shadowed / --include-archived to retrieve them. The store lives at
  ~/.eidetic/memory (a home-dir path outside any git worktree); the wrapper
  defaults queries to this agent's PERSONAL, PRIVATE scope (`--scope culture
  --visibility private`, suffix read from culture.yaml) — matching where
  /remember writes — so a no-flag recall returns this agent's own private records
  plus the shared public pool, and Claude and the colleague backend recall each
  other's memories because both resolve the same suffix via this skill. Use
  when the user says "recall", "what do we know about X", "search memory",
  "have we seen X before", "look it up in memory", "eidetic recall", or before
  answering from scratch when prior context may already be stored. Pairs with
  the sibling /remember skill.
---

# recall — search the shared eidetic memory

`recall` drives **`eidetic recall`**: given a query, it returns the top-k stored
records ranked by relevance, each with its `text`, full `metadata` (provenance),
a numeric `score`, and a freshness `signal`. It is the read half of the memory
surface; the write half is the sibling **/remember** skill.

The point of a *shared* store is that memory is a **team faculty**, not a
per-agent silo: a record Claude wrote is recallable by the colleague backend
(and vice versa), because both resolve the same `~/.eidetic/memory` path.

## How to run

```bash
bash .claude/skills/recall/scripts/recall.sh "<query>" [flags...]
```

The wrapper resolves the CLI portably (installed `eidetic` on `PATH`, else
`uv run eidetic` from the checkout) and forwards every flag verbatim, so it is
exactly `eidetic recall …`. Run it from anywhere; the store is the same.

## Search modes (`--mode`, default `hybrid`)

| Mode | What it matches | Needs embed server? |
|------|-----------------|---------------------|
| `exact` | case-insensitive verbatim substring (`--case-sensitive` to tighten) | no — offline-safe |
| `approximate` | vector cosine / semantic similarity | yes (falls back offline) |
| `keyword` | BM25 lexical; only records sharing a query term | no — offline-safe |
| `hybrid` | `alpha*approximate + (1-alpha)*keyword` (`--alpha`, default 0.5) | uses it when up |

`hybrid` is the default because the two signals cover each other's blind spots:
vector catches paraphrases, keyword catches exact ids/quotes. When the embed
server is unreachable, `hybrid` collapses to keyword-only (it never fuses
meaningless offline-fallback cosine).

## Output fields

Each hit in `--json` output includes:

| Field | Notes |
|-------|-------|
| `id` | stable record identity |
| `text` | the stored chunk |
| `type` | record type |
| `metadata` | full provenance, round-tripped verbatim from ingest |
| `score` | relevance score from the chosen search mode (freshness-blended) |
| `signal` | freshness strength in [0, 1]; computed at recall time from age, recall frequency, and staleness |
| `created` | ISO-8601 ingest date (may be DATE_UNKNOWN for legacy records) |
| `last_recall` | ISO-8601 timestamp of the most recent recall hit (null if never recalled) |
| `recall_count` | number of times this record has been recalled (passive reinforcement counter) |
| `lifecycle` | `active`, `shadowed`, or `archived` |
| `links` | list of related-memory ids |

## Freshness signal

Every `recall` hit carries a `signal` field (float in `[0, 1]`). The signal
blends **multiplicatively** into the lexical/vector score so recently-created
and frequently-recalled records surface ahead of stale ones. The formula:

```
access_bonus = min(0.5, recall_count * 0.05)
age_factor   = 1 / (1 + days_since_creation * 0.01)
staleness    = days_since_last_recall * 0.01
signal       = clamp((0.5 - staleness + access_bonus) * age_factor, 0, 1)
blended_score = score * (1 + 0.25 * (signal - 0.5))
```

Records with no temporal data (legacy, undated) are an exact no-op — the blend
is skipped for them so pre-existing fixture scores are unchanged.

Each `recall` call is also **passive reinforcement**: it bumps `last_recall` and
`recall_count` on every matched record, so frequently-recalled memories organically
gain signal strength over time.

## Lifecycle flags

By default, `recall` returns only `active` records. Use these flags to retrieve
non-active records:

- `--include-shadowed` — include records whose `lifecycle == "shadowed"` (records
  superseded within their scope by a newer record). Shadowed records are preserved
  and still searchable; they are just hidden from the default result set.
- `--include-archived` — include records whose `lifecycle == "archived"` (records
  older than ~1 year or below the signal threshold). Archived records are fully
  preserved; the flag makes them retrievable again.

Both flags can be combined. Neither affects ranking — shadowed/archived records
compete on score/signal just like active ones when included.

## Common flags (forwarded to `eidetic recall`)

- `--mode exact|approximate|keyword|hybrid` — default `hybrid`.
- `--top-k N` — max results (default 5).
- `--alpha F` — hybrid blend weight in `[0,1]` (default 0.5).
- `--case-sensitive` — for `--mode exact`.
- `--filter KEY=VALUE` — metadata facet filter (repeatable): e.g. `--filter source=docs`.
- `--scope NAME` / `--visibility public|private` — scope isolation (no private
  leak). **The wrapper defaults this to the agent's PERSONAL, PRIVATE scope**
  (`--scope culture --visibility private`, suffix read from `culture.yaml`),
  matching where `/remember` writes — so a no-flag recall returns this agent's
  own private records **plus** the shared public pool, while those private records
  stay invisible to a `default`/other-scope recall. Pass `--scope`/`--visibility`
  to query elsewhere; a wheel install with no `culture.yaml` falls back to the
  CLI default `default`/`public`.
- `--backend files|mongo|neo4j` — default `files` (the shared home-dir store).
- `--include-shadowed` — include shadowed records in results (excluded by default).
- `--include-archived` — include archived records in results (excluded by default).
- `--json` — structured list to stdout (use this when an agent parses the result).

## Examples

```bash
# Default hybrid recall, JSON for an agent to parse:
bash .claude/skills/recall/scripts/recall.sh "jetson nano power draw" --json

# Find the exact message that mentions a phrase:
bash .claude/skills/recall/scripts/recall.sh "Orin Nano" --mode exact

# Keyword search, offline-safe, narrowed to a source:
bash .claude/skills/recall/scripts/recall.sh "thermal throttle" --mode keyword \
    --filter source=discord --top-k 10

# Retrieve a record that was recently shadowed (its superseding record is now active):
bash .claude/skills/recall/scripts/recall.sh "old topic" --include-shadowed --json

# Retrieve all records including archived (to audit stale memories):
bash .claude/skills/recall/scripts/recall.sh "power" --include-archived --include-shadowed --json
```

## Notes

- **Provenance is mandatory** on every hit — recall is for *cited* answers.
- The embed endpoint defaults to the local model-gear embed gear
  (`http://localhost:8002/v1`, model `Qwen/Qwen3-Embedding-0.6B`); override with
  `EIDETIC_EMBED_URL` / `EIDETIC_EMBED_MODEL`. `exact`/`keyword` ignore it.
- **Use the wrapper, not a bare `eidetic`.** The console script may not be on
  `PATH` (in a dev checkout it isn't) — the wrapper resolves it for you (`PATH`
  first, else `uv run eidetic`). For the docs, run `eidetic explain recall` if
  installed, otherwise `uv run --project <eidetic-cli checkout> eidetic explain
  recall`. (`explain` is an **`eidetic`** verb — a sibling tool like `devex`
  won't know it.)
- **Reading scores:** `exact`, `keyword`, and `hybrid` drop non-matching records
  (hybrid drops any record with a `0.0` blended score), so their hits are real
  matches. `approximate` keeps every candidate ranked by raw cosine, so it can
  return low/near-zero scores when the store is small — lower `--top-k` to trim.
  A `--min-score` threshold is a tracked follow-up.
- **Sharing scope = one OS user.** The default store is `~/.eidetic/memory`, so
  every agent/process running as the *same* OS user shares it (that is the point —
  Claude + colleague). It is not isolated between OS users by anything but file
  permissions; keep genuinely private data in a `--visibility private` scope and
  treat the host as the trust boundary.

## Provenance

First-party to **eidetic-cli** — eidetic owns its memory surface. Cite, don't
import: downstream repos copy this skill, they don't symlink it. See
[`docs/skill-sources.md`](../../../docs/skill-sources.md).
