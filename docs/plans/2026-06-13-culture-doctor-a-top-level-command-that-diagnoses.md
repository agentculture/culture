# Build Plan — culture doctor: a top-level command that diagnoses drift between the ~/.culture/server.yaml agent manifest and the on-disk culture.yaml repos, exiting non-zero when it finds problems

slug: `culture-doctor-a-top-level-command-that-diagnoses` · status: `exported` · from frame: `culture-doctor-a-top-level-command-that-diagnoses`

> culture doctor: a top-level command that diagnoses drift between the ~/.culture/server.yaml agent manifest and the on-disk culture.yaml repos, exiting non-zero when it finds problems

## Tasks

### t1 — Define the doctor result model: Finding + DoctorReport dataclasses in culture/doctor/model.py

- covers: h3, h11
- acceptance:
  - Finding carries drift_class (1|2|3), severity ('error'|'warning'), subject (nick/suffix), path, message, fix_hint; DoctorReport groups class1/class2/class3 finding lists
  - DoctorReport.exit_code is nonzero iff any class-1 or class-3 finding exists; class-2-only and empty reports both yield exit_code 0 (unit-tested for all three cases)

### t2 — Scan-root resolution + on-disk repo discovery in culture/doctor/discovery.py

- depends on: t1
- covers: c12, h14
- acceptance:
  - resolve_scan_root(config, cwd=None, override=None) returns override when given; else the parent of the culture repo derived from the manifest self-entry; else the parent of the cwd git-root — no path literal hardcoded
  - Relocating a fixture culture repo from <tmp>/git to <tmp>/git2 changes the resolved scan root accordingly (tmpdir test)
  - discover_ondisk_repos(root) yields exactly one entry per <root>/*/culture.yaml with its declared suffix(es); a fixture of 3 culture.yaml repos + 1 plain dir yields 3 entries

### t3 — Three drift-class checks (class1 broken-registration, class2 unregistered, class3 suffix-collision) in culture/doctor/checks.py

- depends on: t1
- covers: c4, h6, h7
- acceptance:
  - check_registrations(config) reuses load_culture_yaml(); a fixture manifest with one missing-dir + one missing-yaml + one registered-suffix-not-in-yaml yields exactly 3 class-1 findings; a fully valid manifest yields none
  - A parity test asserts class-1 findings match exactly what resolve_agents() warns on for the same manifest (proves reuse, not reimplementation; resolve-time warning path is left unchanged)
  - check_unregistered(config, discovered) flags discovered repos whose dir is not a manifest value (severity warning); check_suffix_collisions(config, discovered) flags a discovered suffix already bound to a different path in the manifest, and duplicate suffixes across discovered repos (severity error) — and does NOT flag the ordinary unregistered repos that simply declare their own unique suffix

### t4 — Opt-in register fix in culture/doctor/fix.py (adds class-2 repos to server.yaml via add_to_manifest)

- depends on: t1
- covers: c15, h13, h12, c13
- acceptance:
  - register_unregistered(config_path, class2_findings) adds each unregistered repo via the existing add_to_manifest() (not a parallel YAML writer) and returns the list of suffix->path entries added
  - Idempotent: a second run over the same findings adds nothing; empty findings performs zero writes
  - Test asserts every discovered repo's culture.yaml is byte-identical before/after, and only ~/.culture/server.yaml changed

### t5 — run_doctor() orchestrator in culture/doctor/__init__.py composing discovery + 3 checks + optional fix

- depends on: t2, t3, t4
- covers: c1, c3, c7
- acceptance:
  - run_doctor(config, root_override=None, fix=False) -> DoctorReport composes discovery + the three checks, applying the fix only when fix=True
  - On a fixture mirroring spark (3 missing-dir manifest entries, several unregistered repos incl. one duplicate-suffix collision) the report has 3 class-1, >=1 class-2, >=1 class-3 findings and exit_code != 0; on an all-valid fixture the report is empty with exit_code 0
  - With fix=False, run_doctor writes no files (manifest byte-identical)

### t6 — CLI group 'culture doctor' in culture/cli/doctor.py (flags, rendering, exit codes, --json)

- depends on: t5
- covers: h4, h5, h2, c2, h3, h11
- acceptance:
  - Exports NAME='doctor', register(subparsers) and dispatch(args) per the cli module pattern; flags: --json, --root PATH, --fix/--register
  - No-args run prints a human report grouped by the 3 drift classes; each problem line names the repo/nick and the exact suggested command ('culture agents unregister <suffix>' for class-1, 'culture agents register <path>' for class-2); --json emits the same findings as a structured payload
  - Process exits nonzero iff a class-1 or class-3 finding exists (class-2-only exits 0); --fix registers class-2 repos and prints what it added; a test asserts the manifest is unmodified after a non---fix run

### t7 — Register the doctor group in culture/cli/__init__.py GROUPS and prove no steward-forward collision

- depends on: t6
- covers: c1, h4
- acceptance:
  - 'doctor' module added to the GROUPS registry; 'culture doctor --help' and 'culture doctor' dispatch to the new handler
  - Test asserts top-level 'culture doctor' does NOT route through _maybe_forward_to_steward, while 'culture agents doctor' still short-circuits to the steward forward (the existing alignment verb is untouched)

### t8 — Document the feature in docs/doctor.md

- depends on: t6
- covers: c5, c13
- acceptance:
  - docs/doctor.md covers purpose, the 3 drift classes, all flags (--json, --root, --fix), exit-code semantics, and the boundary vs the steward-forwarded 'culture agents doctor'
  - markdownlint-cli2 passes on docs/doctor.md

### t9 — Bump version (minor) and update CHANGELOG.md + uv.lock for the new feature

- depends on: t7, t8
- acceptance:
  - pyproject.toml version bumped minor (new feature) via /version-bump; CHANGELOG.md gets a Keep-a-Changelog entry for 'culture doctor'; uv.lock staged alongside
  - version-check CI job passes (version strictly greater than main)

## Risks

- [unknown_nonblocking] Scan-root self-identification: when culture is installed as a uv tool (not editable / not run from the checkout), discovering 'this repo' from the manifest self-entry vs cwd git-root may need iteration; --root is the always-correct escape hatch (task t2)
- [unknown_nonblocking] Class-3 collision scope: must distinguish a genuine duplicate-suffix collision from the ~44 legitimate unregistered repos that each declare their own unique suffix; over-broad matching would flag healthy repos (task t3)
