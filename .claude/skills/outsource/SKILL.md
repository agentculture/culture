---
name: outsource
type: command
description: >
  Hand a scoped repo task to convertible — a *different* engine/model than you
  (e.g. a local vLLM Qwen) — and fold its answer back. The point isn't a stronger
  model; it's a different mind, and diversity helps: `outsource review` gets an
  independent second opinion on a diff, `outsource explore` gets a fresh read of
  an area, `outsource write` delegates a small implementation. Use when the user
  says "outsource this", "get a second opinion", "have convertible review/explore/
  write", "ask the other model", or when you want a diverse perspective rather
  than just doing it yourself. Read-only verbs (explore/review) run isolated in a
  throwaway git worktree and cannot touch the working tree.
---

# outsource — use convertible as a different mind

`outsource` drives the **`convertible`** CLI so a Claude agent can hand a scoped
task to a *different* engine (default: a local vLLM `Qwen3.6-27B` on
`:8001`). Convertible's model is **not** assumed to be stronger than you — its
value is **diversity**. A second, independent mind catches things the author's
mind glides past, which is why **review** is the headline verb.

This skill is the operator: a portable wrapper that resolves the CLI and turns
each verb into a `convertible drive`, then prints the drive's result summary.

## How to run

The entry point is `scripts/outsource.sh`. Invoke it from the repo you want
convertible to work on:

```bash
bash .claude/skills/outsource/scripts/outsource.sh <verb> "<text>" [options]
```

It resolves the CLI portably — an installed `convertible` on `PATH` (the normal
case), falling back to `uv run convertible` when inside the convertible checkout,
else an install hint.

### Verbs

| Verb | What it does | Side effects |
|------|--------------|--------------|
| `explore "<question or area>"` | Read-only investigation of the repo; the model reads and reports findings. | **None** — runs in a throwaway worktree at HEAD. |
| `review "<what to focus on>" [--base main]` | A diverse second opinion on the **committed** diff (`<base>...HEAD`). | **None** — throwaway worktree; reviews committed changes only. |
| `write "<task>" [--pr]` | Implement a change. Commits to a drive branch by default; `--pr` pushes + opens a PR. | In-place: a `convertible/<id>` drive branch (or a PR). |

### Options

| Option | Meaning |
|--------|---------|
| `--repo PATH` | Target repo (default: `.`). |
| `--base BRANCH` | Base for the `review` diff (default: `main`). |
| `--engine NAME` | Engine wheel (default: `$CONVERTIBLE_ENGINE` or `vllm-openai`). |
| `--model NAME` | Model (default: `$CONVERTIBLE_MODEL` or `mmangkad/Qwen3.6-27B-NVFP4`). |
| `--base-url URL` | OpenAI base URL (default: `$CONVERTIBLE_BASE_URL` or `http://localhost:8001/v1`). |
| `--max-steps N` | Loop step budget (default: 20). |
| `--allow-dirty` | (`write`) allow running on a dirty tree. |
| `--pr` | (`write`) push + open a PR instead of a local drive branch. |

The result printed to stdout is the drive's `TaskResult.summary` (plus
`changed_files` / drive branch for `write`), parsed from `convertible drive
--json`. Per-step progress streams to stderr while it runs.

## When to reach for which verb

- **review** — the standing use. You wrote (or an agent wrote) a change and you
  want a candid, independent pass over the *committed* diff before you trust it.
  Treat the output as a second opinion to weigh, not a verdict.
- **explore** — you want a fresh, unbiased read of an unfamiliar area ("how does
  X work here?") without anchoring on your own assumptions.
- **write** — a small, well-scoped implementation you're happy to delegate. The
  result lands on a drive branch you can inspect, merge, or discard.

## Hard rules (do not violate)

- **explore and review are read-only.** They run in a throwaway `git worktree`
  at HEAD, so a stray write can't reach your working tree or branch; the prompts
  also tell the model not to modify anything. Don't route a change-making task
  through them — use `write`.
- **`write` refuses a dirty tree** unless you pass `--allow-dirty`. This guards
  the dirty-tree hazard: `convertible drive --no-pr` commits *uncommitted* edits
  onto the drive branch and leaves you there. Commit or stash first.
- **Outsourced output is a second opinion, not authority.** The engine may be a
  smaller/different model; weigh its findings, verify its claims, and own the
  decision yourself.

## Honest limits

- Read-only is enforced by **worktree isolation + prompt constraint**, not a
  sandbox — the loop always exposes `write_file`/`run_command`, so the model can
  still run arbitrary *read-only* commands.
- `review` covers **committed** changes only (`<base>...HEAD`). To review
  uncommitted work, commit it first.
- The default engine is whatever single model is running locally; a multi-model
  fleet (different model per verb) is separate infrastructure.

## Provenance

This is a **first-party convertible** skill — `agentculture/convertible` is its
origin. guildmaster **re-broadcasts** it to the mesh (the same inbound pattern as
the devague-origin workflow skills), tracking it in `docs/skill-sources.md`. The
`cite, don't import` policy holds: downstream repos copy it, they don't symlink
or depend on it.
