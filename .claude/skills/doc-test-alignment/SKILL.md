---
name: doc-test-alignment
description: >
  Verify that committed docs (README.md, CLAUDE.md, SKILL.md descriptions) still
  describe what the code and tests actually do. Use at the end of a plan, before
  PR creation, or when the user says "check doc-test alignment", "verify docs",
  or "do the docs still match the code". STUB — `scripts/check.sh` exits with a
  not-yet-implemented error today; the contract for what it will do lives in
  this file.
---

# doc-test-alignment (stub)

This skill is a stub. The real workflow is intentionally not yet implemented —
the file exists so that `steward verify` can find it and so contributors who
land here know it is on the roadmap, not forgotten.

## How to run

`scripts/check.sh` is the entry point. Today it prints a not-yet-implemented
notice and exits non-zero. When the workflow lands, the script will gate
PR-readiness on the alignment contract below; until then, treat any green
exit code from this script as a bug.

## What it will check

The skill is the contract for four narrow alignments. README.md command
examples must still execute against the current checkout and produce output
that matches the surrounding prose. The "build/test/publish" command lines in
CLAUDE.md must do the same. For each `.claude/skills/<name>/`, the SKILL.md
`description` frontmatter must agree with what the scripts under
`scripts/` actually do — surfacing disagreements (e.g. SKILL.md claims the
skill bumps versions but `scripts/` has no bump script). And for each test,
the test name should still describe the assertions the test makes — flagging
drift where the name advertises a feature the assertions no longer touch.

## Why it ships as a stub

Each of those four checks is independently non-trivial. Shipping a partial
implementation would either silently pass when it shouldn't, or false-positive
on intentional doc-vs-code differences. The right path is to land the checks
one at a time, with their own tests, behind a
`steward verify --check doc-test-alignment` flag. The parent verbs (`verify`,
`doctor`) are named in the "Roadmap" section of `CLAUDE.md`; the broader
sibling-pattern contract lives in `docs/sibling-pattern.md`.

## What this stub guarantees today

- The skill directory exists, so `steward verify`'s skills-convention check
  finds the standard layout (SKILL.md + `scripts/` with an entry-point).
- `scripts/check.sh` is the entry-point script, satisfying the AgentCulture
  skills convention requirement that every skill ships an executable script.
- This `SKILL.md` is the contract for what the skill will do — when the
  implementation lands, it must satisfy this description or the description
  must move first.
