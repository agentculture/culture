# Shared Harness Modules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move 7 backend-agnostic harness modules into `culture/clients/shared/` and import them across all four backends, deleting ~3880 lines of duplicated code while keeping the cited tier (`daemon.py`, `config.py`, `constants.py`) intact.

**Architecture:** Two-tier harness — shared tier in `culture/clients/shared/` (imported), cited tier in `packages/agent-harness/` (copied). Big-bang PR with 4 commits: reconcile divergences → lift `WebhookConfig` → move 7 modules + sweep imports → docs.

**Tech Stack:** Python 3.12, `uv`, `pytest` (+ `pytest-xdist` for `/run-tests`), `Agent` subagent for code review, `gh` CLI for PR.

**Spec:** `docs/superpowers/specs/2026-05-09-shared-harness-modules-design.md`
**Tracks:** [#357](https://github.com/agentculture/culture/issues/357)

---

## Pre-flight

Before starting, confirm:

- Working tree on `main`, no uncommitted changes other than the brainstorming spec/plan (commit `32fde1f`).
- `pre-commit` hooks installed and passing on a no-op run.
- Tests pass on `main`: `/run-tests` is green.

If any of these fail, resolve before continuing — don't paper over.

---

### Task 1: Branch and version bump

**Files:**
- Modify: `pyproject.toml` (version bump)
- Modify: `CHANGELOG.md` (new release section)
- Modify: `uv.lock` (if `/version-bump` updates it)

- [ ] **Step 1: Create branch from main**

```bash
git checkout main
git pull --ff-only
git checkout -b feat/shared-harness-modules
```

- [ ] **Step 2: Run version bump**

Run: `/version-bump minor` (skill).
The shared tier is a structural feature, not a bug fix. No public CLI / IRC verb changes, so it's `minor`, not `major`. `/version-bump` updates `pyproject.toml`, `CHANGELOG.md`, and (if needed) `uv.lock` and creates its own commit.

Expected: version moves from `10.3.9` → `10.4.0` (or whatever the current minor floor is). A commit titled like `chore: bump version to 10.4.0` lands on the branch.

- [ ] **Step 3: Verify the bump**

```bash
git log -1 --stat
grep -E "^version" pyproject.toml
head -20 CHANGELOG.md
```

Expected: latest commit touches `pyproject.toml` + `CHANGELOG.md`; version field matches the new minor; CHANGELOG has a new top-most section dated today.

- [ ] **Step 4: Update CHANGELOG entry**

`/version-bump` produces a placeholder entry. Replace its bullet list with:

```markdown
### Added

- `culture/clients/shared/` — shared-by-import tier for backend-agnostic harness modules (`attention`, `message_buffer`, `ipc`, `telemetry`, `irc_transport`, `socket_server`, `webhook`, `webhook_types`). Eliminates ~3880 lines of cite-don't-import duplication. Tracks [#357](https://github.com/agentculture/culture/issues/357). See `docs/architecture/shared-vs-cited.md` for the tier rule and fork-back procedure.

### Changed

- `WebhookConfig` lifted from each backend's `config.py` into `culture/clients/shared/webhook_types.py`; per-backend re-export keeps `from culture.clients.<backend>.config import WebhookConfig` working.
- `tests/harness/test_all_backends_parity.py` no longer watches the 7 moved modules; new `test_no_per_backend_copy_of_shared_modules` guards against fork-back.
```

Amend the version-bump commit (this is the one allowed amend — we're appending the human-meaningful changelog text to the same release commit):

```bash
git add CHANGELOG.md
git commit --amend --no-edit
```

---

### Task 2: Reconcile pre-existing divergences

**Files:**
- Modify: `culture/clients/acp/socket_server.py` (add 4 missing comment/docstring lines)
- Modify: `culture/clients/claude/webhook.py:62` (wrap urlopen)
- Modify: `culture/clients/codex/webhook.py:62` (wrap urlopen)
- Modify: `culture/clients/acp/webhook.py:62` (wrap urlopen)

These reconciliations land **before** any moves so each module is byte-identical across all four backends going into Task 4.

- [ ] **Step 1: Diff acp/socket_server.py vs claude/socket_server.py to confirm the 4-line gap**

```bash
diff culture/clients/claude/socket_server.py culture/clients/acp/socket_server.py
```

Expected: differences are the per-backend `from culture.clients.<backend>.ipc import …` line (will be obsoleted in Task 4) plus exactly 4 lines of comments/docstring missing in acp:

- inside the docstring at the top of the whisper-send method: two lines explaining the race condition (`This avoids race conditions in tests where send_whisper is called` / `shortly after open_unix_connection.`)
- two `# If there are already connected clients, send immediately.` / `# No clients yet — queue for delivery when one connects.` comments

If the diff shows anything else, **stop and investigate** — the spec's reconciliation plan assumes only those 4 lines.

- [ ] **Step 2: Patch acp/socket_server.py with the 4 missing lines**

Open `culture/clients/acp/socket_server.py` and copy the missing comments/docstring lines verbatim from `culture/clients/claude/socket_server.py` (keep the existing acp-specific `from culture.clients.acp.ipc import …` line — it'll be rewritten by Task 4's sweep). Do **not** change the import line yet.

After the edit:

```bash
diff culture/clients/claude/socket_server.py culture/clients/acp/socket_server.py
```

Expected: only the `from culture.clients.<backend>.ipc import …` line differs.

- [ ] **Step 3: Confirm webhook.py divergence**

```bash
for b in claude codex copilot acp; do echo "== $b =="; sed -n '58,68p' culture/clients/$b/webhook.py; done
```

Expected: `claude`, `codex`, `acp` all do `urllib.request.urlopen(req, timeout=10)` bare; `copilot` does `with urllib.request.urlopen(req, timeout=10) as resp: resp.read()`. If anything else differs, stop and investigate.

- [ ] **Step 4: Patch the three bare-urlopen backends**

For each of `claude`, `codex`, `acp`, edit `culture/clients/<backend>/webhook.py` so the urlopen call matches copilot's — i.e. replace

```python
            urllib.request.urlopen(req, timeout=10)
```

with

```python
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
```

Indentation must match the surrounding `try:` block.

- [ ] **Step 5: Confirm all four webhook.py files now agree (modulo imports)**

```bash
for b in codex copilot acp; do
  diff culture/clients/claude/webhook.py culture/clients/$b/webhook.py
done
```

Expected: only the `from culture.clients.<backend>.config import WebhookConfig` line differs across backends. If anything else differs, stop and investigate.

- [ ] **Step 6: Run tests**

```bash
/run-tests
```

Expected: all green. (`/run-tests` runs pytest with `-n auto`. The webhook urlopen change is behavior-equivalent for callers; the socket_server doc lines have no runtime effect.)

- [ ] **Step 7: Commit**

```bash
git add culture/clients/acp/socket_server.py \
        culture/clients/claude/webhook.py \
        culture/clients/codex/webhook.py \
        culture/clients/acp/webhook.py
git commit -m "$(cat <<'EOF'
chore(harness): reconcile pre-share divergences in socket_server and webhook

- acp/socket_server.py: add 4 missing comment/docstring lines from claude.
- claude/codex/acp webhook.py: adopt copilot's `with urllib.request.urlopen(req, timeout=10) as resp: resp.read()` form so the response is closed on every backend.

Prep for moving these modules into culture/clients/shared/ (#357).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Lift `WebhookConfig` into `clients/shared/webhook_types.py`

**Files:**
- Create: `culture/clients/shared/webhook_types.py`
- Modify: `culture/clients/claude/config.py:55-69` (replace WebhookConfig body with re-export)
- Modify: `culture/clients/codex/config.py:~55-69` (same)
- Modify: `culture/clients/copilot/config.py:~55-69` (same)
- Modify: `culture/clients/acp/config.py:~55-69` (same)
- Test: `tests/harness/test_webhook_config_shared.py` (new, smoke test)

- [ ] **Step 1: Write the smoke test**

Create `tests/harness/test_webhook_config_shared.py`:

```python
"""WebhookConfig is imported from culture.clients.shared.webhook_types
and remains accessible from each backend's config module via re-export."""

from __future__ import annotations

import pytest

from culture.clients.shared.webhook_types import WebhookConfig as SharedWebhookConfig


def test_default_webhook_config_values():
    cfg = SharedWebhookConfig()
    assert cfg.url is None
    assert cfg.irc_channel == "#alerts"
    assert cfg.events == [
        "agent_spiraling",
        "agent_error",
        "agent_question",
        "agent_timeout",
        "agent_complete",
    ]


@pytest.mark.parametrize("backend", ["claude", "codex", "copilot", "acp"])
def test_backend_config_reexports_webhook_config(backend: str):
    """Each backend's config.py re-exports WebhookConfig from the shared
    module, so existing `from culture.clients.<backend>.config import
    WebhookConfig` imports keep working."""
    mod = __import__(
        f"culture.clients.{backend}.config", fromlist=["WebhookConfig"]
    )
    assert mod.WebhookConfig is SharedWebhookConfig
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/harness/test_webhook_config_shared.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'culture.clients.shared.webhook_types'` (and the re-export check would also fail).

- [ ] **Step 3: Create the shared module**

Create `culture/clients/shared/webhook_types.py`:

```python
"""Shared types for webhook alerting.

Lives in culture/clients/shared/ rather than per-backend config.py because
the dataclass is byte-identical across all four backends and has no
backend-specific behavior. Each backend's config.py re-exports it for
in-tree callers via:

    from culture.clients.shared.webhook_types import WebhookConfig  # noqa: F401

See docs/architecture/shared-vs-cited.md for the shared-vs-cited rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class WebhookConfig:
    """Webhook alerting settings."""

    url: str | None = None
    irc_channel: str = "#alerts"
    events: list[str] = field(
        default_factory=lambda: [
            "agent_spiraling",
            "agent_error",
            "agent_question",
            "agent_timeout",
            "agent_complete",
        ]
    )
```

- [ ] **Step 4: Replace the WebhookConfig class in each backend's config.py with a re-export**

For each of `claude`, `codex`, `copilot`, `acp`, open `culture/clients/<backend>/config.py`, locate the `class WebhookConfig:` block (decorated with `@dataclass`), and replace the entire decorator + class definition with:

```python
from culture.clients.shared.webhook_types import WebhookConfig  # noqa: F401
```

Place the re-export at the same line range that the class previously occupied so the rest of the file (including the `webhooks: WebhookConfig = field(default_factory=WebhookConfig)` field on the surrounding config dataclass) keeps working unchanged. The `@dataclass` decorator import (`from dataclasses import dataclass, field`) at the top of the file may now be partially unused — keep `field` if other dataclasses in the file use it; remove `dataclass` only if no other dataclass remains in the file. For most backends `dataclass` will still be used elsewhere.

- [ ] **Step 5: Run the smoke test — should pass**

```bash
uv run pytest tests/harness/test_webhook_config_shared.py -v
```

Expected: 5 PASS (1 default-values + 4 re-export checks).

- [ ] **Step 6: Run the full suite**

```bash
/run-tests
```

Expected: all green. If a test fails, it's almost certainly a backend-internal `from culture.clients.<backend>.config import WebhookConfig` callsite that the re-export should be servicing — investigate whether the re-export is correctly placed in the right file at the right scope.

- [ ] **Step 7: Commit**

```bash
git add culture/clients/shared/webhook_types.py \
        culture/clients/{claude,codex,copilot,acp}/config.py \
        tests/harness/test_webhook_config_shared.py
git commit -m "$(cat <<'EOF'
feat(harness): lift WebhookConfig into culture/clients/shared/webhook_types

The dataclass was byte-identical across all four backends. Lifting it into
the shared tier kills 4 copies and prepares webhook.py to import from
shared next. Each backend's config.py keeps a one-line re-export so existing
`from culture.clients.<backend>.config import WebhookConfig` callers
keep working unchanged.

Refs #357.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Move 7 modules into `culture/clients/shared/` and sweep imports

This is the big commit. It happens in module-by-module steps so each step is independently runnable; the commit lands once all 7 modules are moved and tests are green.

**Files (created):**
- `culture/clients/shared/attention.py`
- `culture/clients/shared/message_buffer.py`
- `culture/clients/shared/ipc.py`
- `culture/clients/shared/telemetry.py`
- `culture/clients/shared/irc_transport.py`
- `culture/clients/shared/socket_server.py`
- `culture/clients/shared/webhook.py`

**Files (deleted, 4 × 7 = 28):**
- `culture/clients/{claude,codex,copilot,acp}/{attention,message_buffer,ipc,telemetry,irc_transport,socket_server,webhook}.py`

**Files (deleted, 7):**
- `packages/agent-harness/{attention,message_buffer,ipc,telemetry,irc_transport,socket_server,webhook}.py`

**Files (modified):**
- ~95 import sites across `culture/` and `tests/`
- `tests/harness/test_all_backends_parity.py` (drop 7 entries)

**Files (test):**
- `tests/harness/test_no_per_backend_copy_of_shared_modules.py` (new)

#### Per-module sub-procedure

For each of the 7 modules, follow this procedure. Module list, in dependency-friendly order (pure leaves first):

1. `attention.py`
2. `message_buffer.py`
3. `ipc.py`
4. `telemetry.py`
5. `irc_transport.py`
6. `socket_server.py` (depends on shared `ipc`)
7. `webhook.py` (depends on shared `webhook_types`)

For module `M`:

- [ ] **Step 4.M.a: Copy reference to shared/**

```bash
cp packages/agent-harness/M.py culture/clients/shared/M.py
```

(replacing `M` with the module name without `.py`).

- [ ] **Step 4.M.b: Strip the cite-don't-import docstring header from the shared copy**

The reference in `packages/agent-harness/M.py` carries language like "This is the {backend} citation of the reference module in `packages/agent-harness/M.py`" in cited copies, but the reference itself does not. The shared copy in `culture/clients/shared/M.py` should drop any line that says "This is the … citation …" or "Cited byte-identically into each backend per the all-backends rule" — replace with a single docstring line "Shared harness module — imported by every backend. See docs/architecture/shared-vs-cited.md." Keep all other docstring content (purpose, public API, etc.).

For modules whose reference docstring already says only "Pure module …" or similar (`attention.py`, `message_buffer.py`), leave the docstring as-is.

- [ ] **Step 4.M.c: Rewrite intra-module imports inside the shared copy**

Inside `culture/clients/shared/M.py`, any `from culture.clients.<backend>.X import …` becomes `from culture.clients.shared.X import …` if `X` is also being moved (i.e. `X ∈ {attention, message_buffer, ipc, telemetry, irc_transport, socket_server, webhook, webhook_types}`). Do not rewrite imports that point at cited modules (`config`, `constants`, `daemon`).

For `socket_server.py`: rewrite the `from culture.clients.<backend>.ipc import …` line to `from culture.clients.shared.ipc import …`.
For `webhook.py`: rewrite `from culture.clients.<backend>.config import WebhookConfig` to `from culture.clients.shared.webhook_types import WebhookConfig`.
For the other 5 modules: no internal rewrites needed (they don't import sibling shared modules).

- [ ] **Step 4.M.d: Sweep callsites**

Update every `from culture.clients.<backend>.M import …` import across `culture/` and `tests/` to `from culture.clients.shared.M import …`. Sed is a starting point; **manually audit the resulting diff** before continuing — sed will not catch unusual phrasings (multi-line imports, `import culture.clients.<backend>.M as alias`, etc.).

```bash
find culture tests -name '*.py' -exec sed -i \
  "s/from culture\.clients\.\(claude\|codex\|copilot\|acp\)\.M import/from culture.clients.shared.M import/g" {} +
git diff --stat
```

Then `git diff` and read the changes. If any non-import line changed, revert and rework the sed.

- [ ] **Step 4.M.e: Delete the 4 per-backend copies**

```bash
git rm culture/clients/{claude,codex,copilot,acp}/M.py
```

- [ ] **Step 4.M.f: Run targeted tests**

```bash
uv run pytest -xvs tests/harness/test_M_module.py 2>/dev/null \
  || uv run pytest -xvs tests/test_M.py 2>/dev/null \
  || /run-tests
```

(The targeted test name varies — `test_attention.py`, `test_telemetry_module.py`, `test_socket_server.py`, `test_thread_buffer.py`. Fall back to the full suite if the module has no obvious targeted test.) Expected: green.

If a test fails, the most likely cause is a missed import sweep — `git grep "from culture.clients.\(claude\|codex\|copilot\|acp\).M"` should return zero results.

#### After all 7 modules are moved

- [ ] **Step 4.8: Delete the 7 reference copies from packages/agent-harness/**

```bash
git rm packages/agent-harness/{attention,message_buffer,ipc,telemetry,irc_transport,socket_server,webhook}.py
```

- [ ] **Step 4.9: Update `tests/harness/test_all_backends_parity.py`**

Read `tests/harness/test_all_backends_parity.py`. The parity matrix is a list/dict of cited filenames. Remove these entries:

- `attention.py`
- `message_buffer.py`
- `ipc.py`
- `telemetry.py`
- `irc_transport.py`
- `socket_server.py`
- `webhook.py`

Keep entries for `daemon.py`, `config.py`, `constants.py`. The `_normalize_telemetry` helper at the top of the file becomes dead code if it was only used for `telemetry.py` parity — delete it if so.

- [ ] **Step 4.10: Add the fork-back guard test**

Create `tests/harness/test_no_per_backend_copy_of_shared_modules.py`:

```python
"""Guard against re-citing a shared module into a single backend without
following the documented fork-back procedure.

If a backend genuinely needs to diverge on one of these modules, the
fork-back procedure (see docs/architecture/shared-vs-cited.md) is to:

    1. cp culture/clients/shared/X.py culture/clients/<backend>/X.py
    2. update that backend's imports to point at the local file
    3. leave the other three backends pointing at shared/
    4. re-add X.py to the parity matrix for the diverging backends
    5. update docs/architecture/shared-vs-cited.md and CLAUDE.md

This test catches the case where someone skips steps 4 and 5 and silently
re-cites a shared module.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
_CLIENTS = _REPO_ROOT / "culture" / "clients"

BACKENDS = ["claude", "codex", "copilot", "acp"]
SHARED_MODULES = {
    "attention.py",
    "message_buffer.py",
    "ipc.py",
    "telemetry.py",
    "irc_transport.py",
    "socket_server.py",
    "webhook.py",
}


def test_no_per_backend_copy_of_shared_modules():
    leaked: dict[str, set[str]] = {}
    for backend in BACKENDS:
        backend_dir = _CLIENTS / backend
        local_files = {p.name for p in backend_dir.iterdir() if p.is_file()}
        intersection = SHARED_MODULES & local_files
        if intersection:
            leaked[backend] = intersection

    assert not leaked, (
        f"Shared modules leaked back into per-backend directories: {leaked}. "
        f"If a backend genuinely needs to diverge, follow the fork-back "
        f"procedure in docs/architecture/shared-vs-cited.md."
    )
```

- [ ] **Step 4.11: Run the full suite**

```bash
/run-tests
```

Expected: all green. If parity tests fail, double-check that you removed the right entries in step 4.9. If `test_no_per_backend_copy_of_shared_modules` fails, you missed deleting one of the 28 per-backend copies — `git status` will show which.

- [ ] **Step 4.12: Sanity-check the import graph**

```bash
git grep "from culture.clients.\(claude\|codex\|copilot\|acp\)\.\(attention\|message_buffer\|ipc\|telemetry\|irc_transport\|socket_server\|webhook\)"
```

Expected: zero results. (`webhook_types` is not in the regex because it lives only in `shared/`.)

- [ ] **Step 4.13: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(harness): move 7 backend-agnostic modules into culture/clients/shared/

Modules moved (all previously cited byte-identically into 4 backends):
- attention.py (247 LOC × 4)
- message_buffer.py (71 × 4)
- ipc.py (41 × 4)
- telemetry.py (332 × 4)
- irc_transport.py (399 × 4)
- socket_server.py (~125 × 4)
- webhook.py (~64 × 4)

Each is now imported from culture.clients.shared. Per-backend copies
deleted from culture/clients/{claude,codex,copilot,acp}/ and from
packages/agent-harness/. ~3880 LOC removed total.

Parity test (tests/harness/test_all_backends_parity.py) updated to drop
entries for the moved files; new tests/harness/test_no_per_backend_copy_
of_shared_modules.py guards against re-citing a shared module without
following the fork-back procedure (docs/architecture/shared-vs-cited.md).

Cited tier preserved unchanged: daemon.py, config.py, constants.py
(packages/agent-harness/) plus per-backend agent_runner.py and
supervisor.py. The all-backends rule continues to apply to that tier.

Refs #357.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Documentation

**Files:**
- Modify: `CLAUDE.md` (Citation Pattern section)
- Rewrite: `packages/agent-harness/README.md`
- Create: `docs/architecture/shared-vs-cited.md`

- [ ] **Step 1: Append "Shared vs cited" paragraph to CLAUDE.md**

Open `CLAUDE.md`, find the `## Citation Pattern` section, and append after the existing **All-backends rule** paragraph:

```markdown
**Shared vs cited.** Modules with no backend-specific behavior live in
`culture/clients/shared/` and are imported by every backend (currently:
`attention`, `message_buffer`, `ipc`, `telemetry`, `irc_transport`,
`socket_server`, `webhook`, plus `WebhookConfig` in `webhook_types`).
Modules where backends genuinely diverge are cited from
`packages/agent-harness/` (currently: `daemon.py`, `config.py`,
`constants.py`) plus the per-backend `agent_runner.py` and `supervisor.py`.
The all-backends rule applies to the cited tier — when you change a cited
file, propagate to all four. The shared tier doesn't need the rule because
import enforces it. See `docs/architecture/shared-vs-cited.md` for the rule
and the fork-back procedure.
```

- [ ] **Step 2: Rewrite packages/agent-harness/README.md**

Open `packages/agent-harness/README.md`. Read the current content. Update the "files in this directory" / "step 1: copy from packages/" sections so they reflect that:

- Only `daemon.py`, `config.py`, `constants.py`, `culture.yaml` are cited (copied verbatim into each new backend).
- The 7 previously-listed shared modules (`attention.py`, etc.) are no longer here — they're imported from `culture.clients.shared`.
- Bootstrap instructions for a new (5th) backend now read: copy the cited files; write your own `agent_runner.py` and `supervisor.py`; import the shared modules from `culture.clients.shared`.

If the existing README has a flat "files in this directory" list, replace with two subsections: "Cited (copy these)" and "Imported from culture.clients.shared (do not copy)" referencing the architecture doc.

- [ ] **Step 3: Create docs/architecture/shared-vs-cited.md**

```bash
mkdir -p docs/architecture
```

Create `docs/architecture/shared-vs-cited.md` with this content:

````markdown
# Shared vs cited modules

The `culture` agent harness uses a two-tier code-distribution model.

## The rule

A harness module belongs in `culture/clients/shared/` if it has **no
backend-specific behavior** — nothing in it would ever differ between
`claude`, `codex`, `copilot`, or `acp`. Pure logic and pure-glue I/O
qualify; orchestration that reads SDK-specific shapes does not.

Cited modules live in `packages/agent-harness/` and are copied byte-for-byte
into each backend at `culture/clients/<backend>/`. The
[all-backends rule](../../CLAUDE.md#citation-pattern) — "a feature in only
one backend is a bug" — applies to the cited tier.

Shared modules live in `culture/clients/shared/` and are imported directly
by every backend. The all-backends rule doesn't need to apply because
Python's import system enforces it.

## Current file list

### Shared (imported)

| File | Why shared |
|---|---|
| `attention.py` | Pure state machine; no I/O, no SDK shapes |
| `message_buffer.py` | Pure value type |
| `ipc.py` | Frame encoder/decoder for whisper protocol |
| `telemetry.py` | OTel glue; identical config across backends |
| `irc_transport.py` | RFC 2812 client wrapper; no SDK shapes |
| `socket_server.py` | Unix-socket whisper plumbing |
| `webhook.py` | `urllib.request` POST; identical schema |
| `webhook_types.py` | `WebhookConfig` dataclass |

### Cited (copied)

| File | Why cited |
|---|---|
| `daemon.py` | Each backend's main loop wraps SDK-specific shapes (claude-agent-sdk, codex-agent-sdk, etc.) |
| `config.py` | Per-backend defaults and SDK-specific options |
| `constants.py` | Per-backend literals (channel names, timeouts) |
| `agent_runner.py` | "Yours to write" — the SDK call site itself |
| `supervisor.py` | "Yours to write" — backend-specific liveness logic |

The cited tier's parity is locked down by
`tests/harness/test_all_backends_parity.py`; the shared tier's "no
per-backend copy leaked" property is locked down by
`tests/harness/test_no_per_backend_copy_of_shared_modules.py`.

## Fork-back procedure

If a shared module needs to start diverging for one backend (for example,
an SDK upgrade forces telemetry to emit different attributes per backend):

1. `cp culture/clients/shared/X.py culture/clients/<backend>/X.py` for each
   backend that needs the local copy.
2. In *that backend's* code (its `daemon.py` and any tests that targeted
   the shared path), change `from culture.clients.shared.X import …` to
   `from culture.clients.<backend>.X import …`.
3. Leave any backends that still agree pointing at `shared/`.
4. Re-add `X.py` to the parity matrix in
   `tests/harness/test_all_backends_parity.py` for the now-cited backends
   so the cite-paste invariant is enforced again for them.
5. Move `X` from the "Shared" table above to the "Cited" table in this
   doc, and update the `Citation Pattern` section in `CLAUDE.md` to match.

The two-tier model bends without breaking. The shared tier is **not** an
all-or-nothing commitment — it just describes where the line currently is.
````

- [ ] **Step 4: Run markdownlint on the new and changed docs**

```bash
markdownlint-cli2 CLAUDE.md packages/agent-harness/README.md docs/architecture/shared-vs-cited.md
```

Expected: zero issues. Repo config is `~/.markdownlint-cli2.yaml` plus repo `.markdownlint-cli2.yaml`. Fix any reported issues with `--fix` then re-run.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md packages/agent-harness/README.md docs/architecture/shared-vs-cited.md
git commit -m "$(cat <<'EOF'
docs: shared-vs-cited tier rule and fork-back procedure

- CLAUDE.md: append "Shared vs cited" paragraph to the Citation Pattern section.
- packages/agent-harness/README.md: rewrite the file list and bootstrap instructions to reflect that only daemon/config/constants are cited; the rest are imported from culture.clients.shared.
- docs/architecture/shared-vs-cited.md: new canonical doc covering the tier rule, the current file list, and the fork-back procedure.

Refs #357.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Pre-push checks, push, PR

- [ ] **Step 1: Run the full test suite once more**

```bash
/run-tests
```

Expected: green. If anything fails, fix on this branch — do not push red.

- [ ] **Step 2: Pre-push code review**

Per repo `CLAUDE.md`, this PR touches `packages/`-adjacent code and shared transport, so a pre-push review is required. Run:

```
Agent(subagent_type="superpowers:code-reviewer", prompt="Review the staged commits on branch feat/shared-harness-modules against spec docs/superpowers/specs/2026-05-09-shared-harness-modules-design.md and plan docs/superpowers/plans/2026-05-09-shared-harness-modules.md. Focus areas: (1) the 7 module moves to culture/clients/shared/ — verify no behavior change, no missed import sweep, no broken intra-module imports; (2) WebhookConfig re-export pattern — verify all four backends keep `from culture.clients.<backend>.config import WebhookConfig` working; (3) the socket_server / webhook reconciliations — verify they're behavior-preserving; (4) parity test changes — verify the 7 entries are dropped cleanly and no still-cited file is accidentally dropped; (5) docs accuracy. Report blockers, nits, and any divergence from the spec.")
```

Address any blockers; treat nits as discretionary. Re-run `/run-tests` after any code change.

- [ ] **Step 3: Manual mesh smoke test**

Spin up the four-backend mesh on this branch:

```bash
culture server start --name spark
culture start spark-claude spark-codex spark-copilot spark-acp
```

In another terminal, exercise:

- `culture channel join #spark-test` — confirm all four agents joined.
- Mention each agent in turn (`@spark-claude hello`, etc.) — confirm response.
- DM each agent — confirm response.
- Trigger a fake webhook event (e.g. via the test channel) and check the agent posts to `#alerts` and the configured webhook URL receives a request (if one is configured).
- `culture server logs spark` — scan for telemetry traces; confirm no `ImportError` or `ModuleNotFoundError`.

Stop the mesh:

```bash
culture stop spark-claude spark-codex spark-copilot spark-acp
culture server stop --name spark
```

Note results in the PR description (Test plan section).

- [ ] **Step 4: Push the branch**

```bash
git push -u origin feat/shared-harness-modules
```

- [ ] **Step 5: Open the PR**

Use `/cicd` skill or `gh pr create` directly:

```bash
gh pr create --title "feat(harness): shared-by-import tier in culture/clients/shared/ (#357)" --body "$(cat <<'EOF'
## Summary

- New `culture/clients/shared/` tier holds 7 backend-agnostic harness modules (`attention`, `message_buffer`, `ipc`, `telemetry`, `irc_transport`, `socket_server`, `webhook`) plus `webhook_types` (`WebhookConfig`). Imported by every backend; ~3880 LOC of duplicated code deleted.
- Cited tier preserved: `daemon.py`, `config.py`, `constants.py` still copied via `packages/agent-harness/`. All-backends rule continues to apply there.
- `tests/harness/test_all_backends_parity.py` no longer watches the moved modules. New `test_no_per_backend_copy_of_shared_modules` guards against fork-back without the documented procedure.
- Pre-share reconciliations: `acp/socket_server.py` adopted claude's fuller doc lines; all four `webhook.py` adopted copilot's `with urllib.request.urlopen … as resp: resp.read()` form.
- Docs: new `docs/architecture/shared-vs-cited.md`, updated `CLAUDE.md` Citation Pattern section, rewritten `packages/agent-harness/README.md`.

Tracks #357. Spec: `docs/superpowers/specs/2026-05-09-shared-harness-modules-design.md`.

## Test plan

- [x] `/run-tests` green on the branch.
- [x] Manual four-backend mesh smoke (claude/codex/copilot/acp): join, mention, DM, telemetry trace, webhook fire — all working.
- [x] Pre-push `superpowers:code-reviewer` pass.
- [ ] Qodo / Copilot review.
- [ ] SonarCloud check via `/sonarclaude` before declaring ready.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

- Claude
EOF
)"
```

- [ ] **Step 6: `/cicd` review loop**

Trigger `/cicd` (or wait for Qodo/Copilot to comment), pull comments, address feedback, push fixes, reply to comments, resolve threads. Repeat until clean.

- [ ] **Step 7: SonarCloud check**

Before declaring ready: `/sonarclaude` to inspect SonarCloud findings on this branch (per repo CLAUDE.md: SonarCloud findings don't always arrive as inline PR comments). Address any new findings; resolve any false positives with comments.

- [ ] **Step 8: Mark ready, request merge**

Once Qodo + Copilot + SonarCloud + the human reviewer are all green, leave a "ready for merge" comment on the PR signed `- Claude` and stop.

---

## Self-review notes (for the plan author)

- **Spec coverage:** every section in
  `docs/superpowers/specs/2026-05-09-shared-harness-modules-design.md`
  maps to a task above (goal/rule → Task 4 + docs; layout → Task 4;
  reconciliations → Task 2; tests/parity → Task 4 steps 9-10; migration
  steps → Tasks 1-6; docs → Task 5; rollback → encoded in the fork-back
  procedure doc; risks → mitigations live in the test plan).
- **Type/name consistency:** module names spelled identically in tasks
  and in the parity-test guard list. `WebhookConfig` referenced as
  `culture.clients.shared.webhook_types.WebhookConfig` everywhere.
- **No placeholders:** every "do X" step includes the actual code or
  command. No "implement appropriate Y."
- **One change per commit:** version bump (Task 1), reconciliations
  (Task 2), `WebhookConfig` lift (Task 3), bulk move (Task 4), docs
  (Task 5). Task 6 produces no commits — it's the push/review loop.
