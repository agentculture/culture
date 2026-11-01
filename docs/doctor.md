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

`culture doctor` reports five independent **drift classes**: classes 1-3 are
manifest/repo drift, class 4 is service health, and class 5 covers
[agent accounts](agent-accounts.md). They are documented below in that order
(classes 4 and 5 after the flags table).

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
| `--data-dir` | the server's | Data dir holding `accounts.json` for the class-5 checks. |
| `--fix`, `--register` | off | Opt-in: register the class-2 (unregistered) repos into the manifest. |

The default scan root is the **parent directory of the culture repo itself**,
resolved at runtime — it is discovered from the manifest's self-entry for this
repo, falling back to the git root of the current directory. It is never a
hardcoded path, so relocating the workspace (e.g. `~/git` -> `~/git2`) moves the
scan root with it. Use `--root` to point elsewhere.

### Class 4 — Service health

Installed `culture-agent-*` / `culture-server-*` systemd user units that are
parked (failed) or restart-looping. A failed unit is an **error** (fails the
exit code); a unit restarted more than 5 times
(`SERVICE_RESTART_LOOP_THRESHOLD`) is a **warning** that does not. The fix
hint is `systemctl --user reset-failed <unit> && systemctl --user restart
<unit>`. `--json` carries these as `class4`. The checks live in
`culture_core/doctor/checks.py` (`check_services`).

### Class 5 — Agent accounts

Checks for [agent accounts](agent-accounts.md) (accounts file in the server's
own data dir, see [Which data dir](agent-accounts.md#which-data-dir), or
`--data-dir`; skipped for a `--no-persist` server; the trusted CIDRs and bind
host come from `server.yaml`):

| Finding | Severity | Fix hint |
|---|---|---|
| `<nick> has an account but its unit lacks the grant inject wrapper` | error | `culture agents account activate <nick>` |
| `<nick>'s unit injects an account secret but its account is <state>/removed` | error | `culture agents account activate <nick>` (or `culture agents install <nick>` to go back to legacy) |
| `installed cultureagent <v> is below 0.16.0, the first release with SASL support` (only when accounts exist) | error | `uv tool install --upgrade 'cultureagent>=0.16.0'` |
| `installed agentirc <v> is below 9.14.0 ...` (only when accounts exist) | error | `uv tool install --upgrade 'agentirc-cli>=9.14.0'` |
| `server binds 0.0.0.0 and has accounts but accounts.trusted_cidrs is empty` | error | add the mesh range under `accounts: trusted_cidrs:`, or bind a specific interface |
| `registered agent <nick> has no account` | info | `culture agents account activate <nick>` (accounts are opt-in) |

External residents are checked for an account but never for a unit (they have
none). Class-5 errors fail the exit code and `ok`; info findings never do (a
fresh install with no accounts reports `ok: true`, exit 0, and lists them as
notes). `--json` carries them as `class5`.

## Exit code

`culture doctor` exits `0` only when there are **zero** class-1 and **zero**
class-3 findings (and no class-4 or class-5 errors). Class-2 (unregistered)
findings are warning-only and never change the exit code. So a non-zero exit means genuine breakage — a broken
registration or a suffix collision — which makes the command a reliable gate:

```bash
culture doctor || echo "manifest drift detected"
```

The `--json` payload also carries an `ok` field, which is **stricter** than the
exit code: `ok` is `true` only when there are *no findings at all* (including
class-2 warnings and class-4 warnings; class-5 info/warnings are the
exception and never clear it), whereas `exit_code` is `0` as long as there are
no class-1 or class-3 errors (and no class-4/class-5 errors). Gate CI on `exit_code` (or the process exit status); gate on
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
