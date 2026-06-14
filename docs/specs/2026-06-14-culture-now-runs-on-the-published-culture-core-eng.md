# culture now runs on the published culture-core engine: it pins culture-core 0.4.0, the in-tree culture/ tree is gone (deleted or reduced to thin re-export shims), and the operator's culture CLI, telemetry, and config discovery work exactly as before

> culture now runs on the published culture-core engine: it pins culture-core 0.4.0, the in-tree culture/ tree is gone (deleted or reduced to thin re-export shims), and the operator's culture CLI, telemetry, and config discovery work exactly as before

## Audience

- culture operators (the people running the mesh / installing culture) and the culture front-door maintainers who own the slimmed repo; downstream is unchanged for them

## Before → After

- Before: culture 13.6.0 carries the full 85-module engine in-tree (culture/), has no culture-core dependency, declares loose dep bounds (afi-cli<1.0, agex-cli<1.0, unbounded OTel) that only stay green because uv.lock is stale, and its console entry point is culture = culture.cli:main
- After: culture pins culture-core==0.4.0 (uv.lock refreshed), overlapping bounds aligned down to culture-core's caps, the in-tree culture/ tree deleted or reduced to thin re-export shims onto culture_core.*, the console entry resolved, the test suite re-pointed, and a version bump applied — all in one PR

## Why it matters

- this is the unblocking step for front-door slimming: with the engine moved to its own PyPI package, culture becomes the thin public face (branding/docs/deploy) and the reusable core lives once, matching the agentirc and cultureagent extractions; it also fixes the latent dependency-drift bomb that a fresh lock refresh would otherwise detonate

## Requirements

- culture pins culture-core (==0.4.0 or ~=0.4.0) and refreshes uv.lock in the same PR
  - honesty: after the pin, a fresh 'uv lock' resolves culture-core to exactly 0.4.0 and the lock refresh stays green (no broken transitive afi-cli/agex-cli/OTel pulled)
- culture aligns its overlapping dependency declarations down to culture-core's validated caps: agex-cli<0.14, afi-cli<0.4, OTel stack <1.42 and the instrumentation/semconv line <0.63b0
  - honesty: culture's declared bounds become a subset of culture-core's caps, so a future culture-core unpin cannot silently re-loosen them, and the validated versions remain the only resolvable ones
- the cutover ships as a single PR following pin -> forward/shim -> delete, with no functionality gap at any commit
  - honesty: every commit on the branch leaves culture importable and the culture CLI runnable — no commit deletes the in-tree engine before the pin+shim that replaces it is in place

## Honesty conditions

- after the cutover a clean install of culture pulls culture-core 0.4.0 as a dependency and the repo holds no in-tree engine implementation (only shims and/or front-door code)
- operators take no action and notice no difference; front-door maintainers can change branding/docs/deploy without touching engine code
- culture 13.6.0 on main verifiably has the 85-module culture/ tree, no culture-core dep, loose bounds, and culture=culture.cli:main (checkable in pyproject.toml + git)
- the sub-changes (pin+lock, bound-align, shim/delete, entry-point, test migration, bump) can land together with no intermediate broken commit
- front-door slimming genuinely depends on this cutover and the drift risk is real — a fresh lock without the pin resolves to broken afi-cli/agex-cli/OTel/copilot versions
- the PR touches no wire/identity strings and does not modify agentirc/cultureagent deps or culture-core's caps
- diffing culture CLI help/output/config-discovery before vs after the cutover shows no operator-visible change
- running culture's retained test suite after the uv.lock refresh exits 0
- culture --help and a representative command produce output identical to 13.6.0 and culture.yaml is discovered from the same paths
- a dependency resolve cannot select afi-cli>=0.4, agex-cli>=0.14, OTel>=1.42, or copilot-sdk>0.2.0 given culture's aligned bounds
- with shims in place, 'import culture.<anything>' resolves to the culture_core.* implementation and the operator-visible behavior is byte-for-byte identical to 13.6.0
- after repointing, the installed culture command invokes culture_core.cli:main and behaves identically to 13.6.0
- the existing 96 test files pass unmodified because the shims make each culture.<name> import resolve to its culture_core counterpart, and the full suite green-lights every culture-core pin bump

## Success signals

- culture's full test suite passes against the pinned culture-core engine after a fresh uv.lock refresh (green because of the pin, not despite it)
- the culture operator command works unchanged end-to-end (dual entry point), and culture.yaml discovery + culture.* telemetry metric names are observably identical to 13.6.0
- a fresh resolve of culture's declared bounds can no longer pull a broken newer afi-cli / agex-cli / OTel / copilot-sdk — the aligned caps make the validated versions the only resolvable ones

## Scope / boundaries

- not re-litigating the extraction itself (culture-core#2/#3 are done and 0.4.0 is published); not absorbing agentirc or cultureagent (they stay separate embedded deps); not changing any wire/identity strings (telemetry culture.* names, culture.yaml filename); not loosening culture-core's dependency caps
- not a behavior change for operators — the culture CLI surface, output, and config discovery must be byte-for-byte the same; this is a packaging/sourcing move only

## Decisions

- the in-tree culture/ tree is reduced to thin re-export shims onto culture_core.* (so import culture.x keeps working) rather than deleted outright
- culture keeps its [project.scripts] culture entry but repoints it to culture_core.cli:main (explicit in culture's own pyproject, redundant-but-harmless with culture-core's dual entry point)
- culture retains the full engine test suite and runs it against the pinned culture-core engine through the re-export shims (untouched imports), plus a small front-door packaging test (entry point resolves, wheel ships the shims); no coverage is dropped here

## Hard questions

- risk: shims add a second import surface to maintain; if culture_core renames a module, the matching shim silently breaks until someone notices
- if culture stops running the engine suite, what catches a bad culture-core release at pin-bump time before it reaches operators?
