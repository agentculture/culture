# culture doctor

`culture doctor` is a top-level command that diagnoses **drift** between the
agent manifest (`~/.culture/server.yaml`, the `agents:` map of `suffix ->
directory`) and the on-disk `culture.yaml` repos in your workspace. It exits
non-zero when it finds real breakage, so it doubles as a CI/ops health gate.

```bash
culture doctor                 # human-readable report
culture doctor --json          # machine-readable, for CI
culture doctor --fix           # also register unregistered repos (opt-in)
```

## What it checks

`culture doctor` reports three independent **drift classes**.

### Class 1 — Broken registrations

A manifest entry that cannot be resolved: its directory is missing, its
`culture.yaml` is missing, or the registered suffix is not declared in that
`culture.yaml`. **Severity: error** (fails the exit code).

Suggested fix: `culture agents unregister <suffix>`.

This reuses the same validation as `resolve_agents()` in `culture/config.py`
(which already logs these as warnings at agent-resolution time) — `doctor` just
surfaces them as a first-class, exit-coded report without changing that
runtime path.

### Class 2 — Unregistered repos

A directory under the workspace root that has a `culture.yaml` but is **not** in
the manifest. **Severity: warning** — this is informational and does **not**
change the exit code, because having an on-disk repo that simply isn't running
on this host is legitimate.

Suggested fix: `culture agents register <path>` (or `culture doctor --fix`,
below).

### Class 3 — Suffix collisions

A discovered repo that declares a suffix already bound to a **different** path
in the manifest, or the same suffix declared by two different repos.
**Severity: error** (fails the exit code). This catches copy-paste mistakes
such as a second repo whose `culture.yaml` claims an in-use agent suffix.

## Flags

| Flag | Default | Meaning |
|------|---------|---------|
| `--config PATH` | `~/.culture/server.yaml` | The manifest to check. |
| `--root PATH` | the culture repo's own parent | Workspace root to scan for on-disk `culture.yaml` repos. |
| `--json` | off | Emit findings as a JSON object instead of text. |
| `--fix`, `--register` | off | Opt-in: register the class-2 (unregistered) repos into the manifest. |

The default scan root is the **parent directory of the culture repo itself**,
resolved at runtime — it is discovered from the manifest's self-entry for this
repo, falling back to the git root of the current directory. It is never a
hardcoded path, so relocating the workspace (e.g. `~/git` -> `~/git2`) moves the
scan root with it. Use `--root` to point elsewhere.

## Exit code

`culture doctor` exits `0` only when there are **zero** class-1 and **zero**
class-3 findings. Class-2 (unregistered) findings are warning-only and never
change the exit code. So a non-zero exit means genuine breakage — a broken
registration or a suffix collision — which makes the command a reliable gate:

```bash
culture doctor || echo "manifest drift detected"
```

The `--json` payload also carries an `ok` field, which is **stricter** than the
exit code: `ok` is `true` only when there are *no findings at all* (including
class-2 warnings), whereas `exit_code` is `0` as long as there are no class-1 or
class-3 errors. Gate CI on `exit_code` (or the process exit status); gate on
`ok` only if you also want unregistered-repo warnings to fail the build. The
exit code is `0` or `1`.

## The `--fix` action

`culture doctor --fix` (alias `--register`) registers each class-2 repo into the
manifest by reusing the existing `culture agents register` path
(`add_to_manifest()`). It is **opt-in** — a plain `culture doctor` makes no
writes at all.

The fix writes **only** `~/.culture/server.yaml`. It never edits a discovered
repo's `culture.yaml` or any file culture does not own, and it is idempotent
(re-running registers nothing new). With no class-2 findings it is a no-op. It
does not touch class-1 (broken registrations, surfaced with an `unregister`
hint) or class-3 (suffix collisions, reported for manual resolution — there is
no safe auto-fix, since resolving a collision means renaming a suffix in a repo
culture does not own). Those remain a human decision.

## Example

```text
Broken registrations (class 1):
  ✗ spark-shushu: culture.yaml missing for spark-shushu at /home/spark/git/shushu
      fix: culture agents unregister shushu
Unregistered repos (class 2, warning):
  • guildmaster: on-disk culture.yaml not registered: /home/spark/git/guildmaster
      fix: culture agents register /home/spark/git/guildmaster
Suffix collisions (class 3):
  ⚠ daria: suffix 'daria' at /home/spark/git/culture-sonar-cli collides with registered /home/spark/git/daria
```

`culture doctor --json` emits the same findings as a structured object
(`{class1, class2, class3, ok, exit_code}`) for CI consumption.

## Relationship to `culture agents doctor`

`culture doctor` is **distinct** from `culture agents doctor`. The latter is
forwarded to the [`steward`](https://github.com/agentculture/steward) CLI and
performs **agent alignment / config-quality** doctoring — a different concern.

| Command | Scope |
|---------|-------|
| `culture doctor` | manifest ↔ filesystem consistency (this page) |
| `culture agents doctor` | agent alignment / config quality (steward) |

The two never collide: `culture doctor` is a native top-level verb, while
`culture agents doctor` is short-circuited to steward before argparse runs.
