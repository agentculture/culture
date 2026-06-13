# culture doctor: a top-level command that diagnoses drift between the ~/.culture/server.yaml agent manifest and the on-disk culture.yaml repos, exiting non-zero when it finds problems

> culture doctor: a top-level command that diagnoses drift between the ~/.culture/server.yaml agent manifest and the on-disk culture.yaml repos, exiting non-zero when it finds problems

## Audience

- culture operators running a mesh host (e.g. spark) who manage the agent manifest, plus CI/ops automation that needs a machine-checkable gate

## Before → After

- Before: drift is only surfaced as a side-effect WARNING inside resolve_agents() when agents are resolved; there is no first-class command to audit the manifest, and nothing detects unregistered repos or suffix collisions at all
- After: an operator runs 'culture doctor' and gets a categorized report of manifest<->filesystem drift (broken registrations, unregistered repos, suffix collisions) with a non-zero exit code when problems exist

## Why it matters

- stale manifest entries and unregistered repos silently break agent start/observe; operators discover drift by accident. A doctor makes the existing resolve_agents() validation a first-class, scriptable health check

## Requirements

- for class-2 (unregistered repos), doctor scans the local filesystem one level above the culture repo (the workspace parent, e.g. /home/spark/git) for */culture.yaml and reports any whose directory is not in the manifest — warning-only, exit 0; default root = the culture repo's parent, overridable with --root
  - honesty: class-2 findings are warning-only: they print with a 'culture agents register <path>' hint and never change the exit code; only class-1 (broken registration) and class-3 (suffix collision) make doctor exit non-zero
  - honesty: the class-2 scan root is derived from the culture repo's OWN location at runtime — its parent directory — discovered via the manifest's self-entry for this repo (falling back to the in-tree cwd repo root), so moving the workspace to /git2 moves the scan root automatically; never hardcoded; --root overrides
- doctor offers an opt-in fix that registers class-2 repos (on-disk culture.yaml not in the manifest) by ADDING them to ~/.culture/server.yaml via the existing add_to_manifest()/register path; default run stays read-only/diagnose; the fix writes only server.yaml, never the discovered repo's culture.yaml
  - honesty: the fix path reuses add_to_manifest()/the existing register code (not a parallel writer), is idempotent (re-running registers nothing new), and prints exactly which suffix->path entries it added; default invocation makes zero writes

## Honesty conditions

- a top-level 'culture doctor' verb is registered in the CLI group list and dispatches without colliding with 'culture agents doctor' (the steward forward)
- the command is runnable by an operator with no args (sensible defaults) and emits machine-parseable output (--json) for CI/ops gating
- the report cleanly separates the three drift classes and each problem line names the offending nick/repo and the suggested fix command (e.g. 'culture agents unregister <suffix>' / 'culture agents register <path>')
- doctor reuses the existing resolve_agents()/load_culture_yaml() validation in culture/config.py rather than reimplementing the missing-dir / missing-yaml / suffix-not-in-yaml checks
- the warning logic in resolve_agents() remains the runtime path; doctor surfaces the same findings as an explicit, exit-coded report without changing resolve-time behavior
- exit code is 0 only when zero problems are found in ALL three classes; any class-1 or class-3 problem forces non-zero; class-2 (unregistered repos) severity is a confirmed decision (warning-only vs error)
- a test proves doctor never modifies any discovered repo's culture.yaml (those files byte-identical before/after, even in --fix mode); only ~/.culture/server.yaml may change, and only when the fix is explicitly requested

## Success signals

- on a healthy host doctor exits 0 with an all-clear; on the current spark host it reports 3 missing-dir registrations, ~44 unregistered culture.yaml repos, and the culture-sonar-cli/daria suffix collision, and exits non-zero

## Scope / boundaries

- culture doctor checks manifest<->filesystem consistency and can REGISTER discovered repos into our own server.yaml manifest (opt-in, e.g. --fix/--register); it never edits the other repos' culture.yaml or any file we don't own, and it never does agent alignment/config-quality doctoring (that stays 'culture agents doctor' forwarded to steward)

## Non-goals

- not an auto-UNregister tool (removing stale class-1 entries stays a human decision / suggested command); not a watcher/daemon; does not reach across linked peers or other hosts' manifests; never writes to a repo's own culture.yaml

## Decisions

- lives as a top-level 'culture doctor' verb (own CLI group), NOT under 'culture agents', to avoid the steward-forwarded doctor verb
