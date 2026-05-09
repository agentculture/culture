# Shared harness modules — selective `import` over `cite`

**Status:** Design
**Date:** 2026-05-09
**Tracks issue:** [#357](https://github.com/agentculture/culture/issues/357)
**Author:** Ori Nachum + Claude

## Goal

Carve a "shared by import" tier into the agent harness so that modules with no
backend-specific behavior live in one place and are imported by every backend.
Keep the cited-citation tier for modules where backends genuinely diverge.

This relaxes the **cite, don't import** rule selectively, where the rule is
costing duplication without buying us divergence.

## Non-goals

- We do **not** retire `packages/agent-harness/` or the citation pattern.
  Backends still cite `daemon.py`, `config.py`, `constants.py` from there.
- We do **not** change the public CLI or IRC verbs.
- We do **not** introduce a new shared package distributable on PyPI. Shared
  modules live inside `culture/clients/shared/`, not in `packages/`.
- We do **not** consume `citation-cli` as a tool. The cite-don't-import pattern
  applies conceptually only.

## Background

Today every agent backend (`claude`, `codex`, `copilot`, `acp`) carries a
near-identical copy of the harness, cited from `packages/agent-harness/`. The
[`test_all_backends_parity.py`][parity] suite locks down byte-equivalence
between the reference and each cited copy.

This works, but most of the cited files have **never** diverged in practice.
SonarCloud flagged 78% duplication on PR #356 for exactly this reason. From
issue #357:

> A defensible middle ground: import truly pure, leaf modules… cite the impure
> orchestration layers… where divergence is expected.

This spec settles where that line goes and what we do about the modules on
each side.

## Tier rule

A harness module belongs in `culture/clients/shared/` if it has **no
backend-specific behavior** — nothing in it would ever differ between
`claude`, `codex`, `copilot`, or `acp`. Pure logic and pure-glue I/O qualify;
orchestration that reads SDK-specific shapes does not.

Cited tier (unchanged): `daemon.py`, `agent_runner.py`, `supervisor.py`,
`config.py`, `constants.py`. These are where backend personality lives.

Shared tier (this spec):

| Module | LOC × 4 today | Why shared |
|---|---|---|
| `attention.py` | 247 | Pure state machine, no I/O |
| `message_buffer.py` | 71 | Pure value type |
| `ipc.py` | 41 | Frame encoder/decoder, no backend logic |
| `telemetry.py` | 332 | OTel glue, identical config across backends |
| `irc_transport.py` | 399 | RFC 2812 client wrapper, no SDK shapes |
| `socket_server.py` | 126 | Unix-socket glue for whispers |
| `webhook.py` | 64 | `urllib.request` POST, identical schema |
| `WebhookConfig` (in `config.py`) | ~14 | Byte-identical dataclass |

**Total dedup: ~1294 lines × 3 = ~3880 lines deleted.**

The all-backends rule keeps full force on the cited tier. The shared tier
doesn't need it because Python's import system enforces the property for free.

## Layout after migration

```text
culture/clients/
  shared/
    __init__.py              (existing, unchanged)
    rooms.py                 (existing, unchanged)
    attention.py             (new — moved from packages/agent-harness/)
    message_buffer.py        (new)
    ipc.py                   (new)
    telemetry.py             (new)
    irc_transport.py         (new)
    socket_server.py         (new)
    webhook.py               (new)
    webhook_types.py         (new — holds WebhookConfig)
  claude/
    daemon.py                (still cited)
    agent_runner.py          (per-backend, unchanged)
    supervisor.py            (per-backend, unchanged)
    config.py                (still cited; WebhookConfig becomes a re-export)
    constants.py             (still cited)
    # attention.py, message_buffer.py, ipc.py, telemetry.py,
    # irc_transport.py, socket_server.py, webhook.py — DELETED
  codex/  copilot/  acp/     same shape as claude/

packages/agent-harness/
  daemon.py                  (kept — citation reference)
  config.py                  (kept — citation reference)
  constants.py               (kept — citation reference)
  culture.yaml               (kept)
  README.md                  (rewritten — see "Docs")
  skill/                     (kept)
  # attention.py, message_buffer.py, ipc.py, telemetry.py,
  # irc_transport.py, socket_server.py, webhook.py — DELETED
```

Imports flip from `from culture.clients.<backend>.attention import ...` to
`from culture.clients.shared.attention import ...` everywhere.

## Reconciling pre-existing divergences

Three files have minor per-backend variation that has to be resolved into one
canonical version *before* moving to `shared/`. These reconciliations land in
the same PR, in the first commit, ahead of the moves.

**`socket_server.py` (acp differs).** acp's copy is missing four explanatory
comment/docstring lines vs claude/codex/copilot. Adopt the fuller version.
No behavioral change for any backend.

**`webhook.py` (copilot differs).** copilot wraps `urllib.request.urlopen` in
`with ... as resp: resp.read()` while claude/codex/acp call it bare and leak
the response. Adopt copilot's version — closes the response on all backends,
strict improvement.

**`WebhookConfig` lift.** The dataclass is byte-identical across all four
backends today. Lift it into `culture/clients/shared/webhook_types.py`. Each
backend's `config.py` keeps a one-line re-export so existing
`from culture.clients.<backend>.config import WebhookConfig` keeps working
without sweeping changes:

```python
from culture.clients.shared.webhook_types import WebhookConfig  # noqa: F401
```

The shared `webhook.py` imports `WebhookConfig` directly from
`webhook_types`.

## Tests and parity

**Existing tests that import the moved modules** — update import paths only.
The tests don't change behavior; they just stop being parameterized over four
backends for the moved modules (one test per shared module is enough; Python
enforces the rest).

Affected files (audit during implementation, not exhaustive):

- `tests/harness/test_attention.py`
- `tests/harness/test_telemetry_module.py`
- `tests/test_socket_server.py`
- `tests/test_thread_buffer.py`
- any `tests/harness/test_agent_runner_<backend>.py` that reaches into a
  moved module

**`tests/harness/test_all_backends_parity.py`** — drop the seven moved files
from the parity matrix. Keep watching the still-cited files (`daemon.py`,
`config.py`, `constants.py`).

**One new guard test** to catch accidental fork-back:

```python
def test_no_per_backend_copy_of_shared_modules():
    SHARED = {"attention.py", "message_buffer.py", "ipc.py", "telemetry.py",
              "irc_transport.py", "socket_server.py", "webhook.py"}
    for backend in BACKENDS:
        leaked = SHARED & {p.name for p in (CLIENTS / backend).iterdir()}
        assert not leaked, (
            f"{backend}: shared modules leaked back as files: {leaked}. "
            f"If a backend genuinely needs to diverge, follow the fork-back "
            f"procedure in docs/architecture/shared-vs-cited.md."
        )
```

This is the only new check. It guards against someone "fixing" a problem by
re-citing a shared file into one backend without going through the documented
fork-back procedure.

**Manual mesh smoke check before merge.** Spin up the four-backend mesh
locally:

```bash
culture server start --name spark
culture start spark-claude spark-codex spark-copilot spark-acp
```

Confirm join + mention + buffer + telemetry + webhook all flow. Note results
in the PR description. One-shot manual check, not automated.

## Migration steps (big-bang PR)

The diff order, top-to-bottom in the PR:

1. **Branch + version bump.** `feat/shared-harness-modules`, `/version-bump
   minor` (new shared tier is a structural feature, no breaking API change for
   external users — internal imports change but no public CLI/IRC verb does).

2. **Reconcile divergences in place** (commit 1):
   - update `acp/socket_server.py` to claude's fuller version
   - update `claude/webhook.py`, `codex/webhook.py`, `acp/webhook.py` to
     copilot's `with ... as resp` form
   - run `/run-tests` — must be green

3. **Lift `WebhookConfig`** (commit 2):
   - create `clients/shared/webhook_types.py` with the dataclass
   - replace each backend's `config.py` `WebhookConfig` block with the
     one-line re-export
   - run `/run-tests` — must be green

4. **Move shared modules** (commit 3, the big one):
   - copy each of the seven files from `packages/agent-harness/` into
     `culture/clients/shared/`
   - delete each backend's per-backend copy (4 × 7 = 28 files)
   - delete the copy in `packages/agent-harness/`
   - sweep imports across the codebase. Sed is a starting point, not the
     spec — audit the diff manually:

     ```sh
     find culture tests -name '*.py' -exec sed -i \
       's/from culture\.clients\.\(claude\|codex\|copilot\|acp\)\.\(attention\|message_buffer\|ipc\|telemetry\|irc_transport\|socket_server\|webhook\)/from culture.clients.shared.\2/g' {} +
     ```

   - update `tests/harness/test_all_backends_parity.py` to drop the seven
     entries from its matrix
   - add `test_no_per_backend_copy_of_shared_modules`

5. **Docs** (commit 4):
   - `CLAUDE.md` — add Citation Pattern "shared vs cited" paragraph (see Docs
     section below)
   - `packages/agent-harness/README.md` — rewrite file list and step-1
     copy-from instructions
   - new doc: `docs/architecture/shared-vs-cited.md`
   - `CHANGELOG.md` — already updated by `/version-bump`; add a bullet under
     the new release section

6. **Manual mesh smoke**: see Tests section. Note results in the PR.

7. **Push + `/cicd`**: pre-push, run
   `Agent(subagent_type="superpowers:code-reviewer", ...)` on the staged diff
   per repo CLAUDE.md (touches `packages/` and shared transport). Push, wait
   for Qodo + Copilot, address feedback. Check SonarCloud via `/sonarclaude`
   before declaring ready.

## Docs

**`CLAUDE.md` Citation Pattern section** — add a paragraph clarifying the two
tiers:

> **Shared vs cited.** Modules with no backend-specific behavior live in
> `culture/clients/shared/` and are imported by every backend (currently:
> `attention`, `message_buffer`, `ipc`, `telemetry`, `irc_transport`,
> `socket_server`, `webhook`, plus `WebhookConfig` in `webhook_types`).
> Modules where backends genuinely diverge are cited from
> `packages/agent-harness/` (currently: `daemon.py`, `config.py`,
> `constants.py`) plus the per-backend `agent_runner.py` and `supervisor.py`.
> The all-backends rule applies to the cited tier — when you change a cited
> file, propagate to all four. The shared tier doesn't need the rule because
> import enforces it.

**`packages/agent-harness/README.md`** — rewrite the file list and the "step
1: copy from packages/" instructions so they reflect that only `daemon.py` /
`config.py` / `constants.py` are cited; the rest are imported from
`culture.clients.shared`.

**New doc — `docs/architecture/shared-vs-cited.md`** — the canonical
explanation. Covers:

1. The rule (verbatim from "Tier rule" above).
2. The file list with a one-line "why this is shared / why this is cited"
   for each.
3. The **fork-back procedure** when a shared module needs to start
   diverging:
   - copy `culture/clients/shared/X.py` into `culture/clients/<backend>/X.py`
   - update *that one backend's* imports to point at the local file
   - leave the other three backends pointing at `shared/`
   - re-add `X.py` to the parity matrix for the now-cited backends if more
     than one diverges
   - update this doc and `CLAUDE.md` to move `X` from the shared list to the
     cited list

**Issue #357**: post a closing comment summarizing the resolution and link to
the merged PR + `docs/architecture/shared-vs-cited.md`.

## Rollback story

If the shared tier turns out to be a mistake — for example, an SDK upgrade
forces telemetry to look different per backend — the unwind is mechanical and
cheap:

1. `cp culture/clients/shared/X.py culture/clients/<backend>/X.py` for each
   backend that needs the local copy.
2. Update those backends' imports to point at the local file.
3. Leave any backends that still agree pointing at `shared/`.
4. Restore the file's entry in `tests/harness/test_all_backends_parity.py`
   for the diverging backends.

The two-tier model bends without breaking. The shared tier is **not** an
all-or-nothing commitment.

## Risks

- **Shared bug = four-way blast radius.** A bug in `clients/shared/telemetry.py`
  hits all four daemons simultaneously, where today it only hits whichever
  backend got the bad cite-paste. Mitigation: same tests we run today, plus
  the shared modules are actually mature (they've been byte-identical across
  four cited copies for months).
- **Drift pressure builds invisibly.** A backend developer might want to tweak
  `telemetry.py` slightly for their backend and silently bypass the shared
  module via duck-typing or a wrapper. Mitigation: the fork-back procedure is
  documented and explicitly cheap, so there's no incentive to bypass it.
- **External backends starting from `packages/agent-harness/`.** Anyone
  bootstrapping a fifth backend now needs to know that some modules are
  imports, not copies. Mitigation: `packages/agent-harness/README.md`
  rewrite makes this explicit.

## Open questions

None at design time. The questions resolved during brainstorming:

- **Sharing rule** → "Pure + I/O-bound, no backend logic" (broad rule).
- **`packages/` fate** → delete shared files from `packages/`.
- **Migration shape** → one big-bang PR.

[parity]: ../../../tests/harness/test_all_backends_parity.py
