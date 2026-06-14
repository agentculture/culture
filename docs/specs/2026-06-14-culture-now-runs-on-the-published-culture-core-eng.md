# culture now runs on the published culture-core engine: it pins culture-core 0.5.0, the in-tree culture/ tree is gone (replaced by sys.modules module-identity aliases onto culture_core.*), and the operator's culture CLI, telemetry, and config discovery work exactly as before

> culture now runs on the published culture-core engine: it pins culture-core 0.5.0, the in-tree culture/ tree is gone (replaced by sys.modules module-identity aliases onto culture_core.*), and the operator's culture CLI, telemetry, and config discovery work exactly as before

## Audience

- culture operators (the people running the mesh / installing culture) and the culture front-door maintainers who own the slimmed repo; downstream is unchanged for them

## Before → After

- Before: culture 13.6.0 carries the full 85-module engine in-tree (culture/), has no culture-core dependency, declares loose dep bounds (afi-cli<1.0, agex-cli<1.0, unbounded OTel) that only stay green because uv.lock is stale, and its console entry point is culture = culture.cli:main
- After: culture pins culture-core==0.5.0 (uv.lock refreshed); overlapping bounds were already aligned to culture-core's caps in the merged precursor PR #455; the in-tree culture/ tree is replaced by sys.modules aliases onto culture_core.*; the console entry is repointed to culture_core.cli:main; a version bump is applied — all in one follow-up PR

## Why it matters

- this is the unblocking step for front-door slimming: with the engine moved to its own PyPI package, culture becomes the thin public face (branding/docs/deploy) and the reusable core lives once, matching the agentirc and cultureagent extractions; it also fixes the latent dependency-drift bomb that a fresh lock refresh would otherwise detonate

## Requirements

- culture aligns its overlapping dependency declarations down to culture-core's validated caps: agex-cli<0.14, afi-cli<0.4, OTel stack <1.42 and the instrumentation/semconv line <0.63b0
  - honesty: culture's declared bounds become a subset of culture-core's caps, so a future culture-core unpin cannot silently re-loosen them, and the validated versions remain the only resolvable ones
- the cutover ships as a single PR following pin -> forward/shim -> delete, with no functionality gap at any commit
  - honesty: every commit on the branch leaves culture importable and the culture CLI runnable — no commit deletes the in-tree engine before the pin+shim that replaces it is in place
- culture pins culture-core ~=0.5.0 (the release that forwards the doctor feature, culture#453 / culture-core#4) and refreshes uv.lock in the same PR; 0.4.0 is no longer the target because it lacks the doctor package, cli doctor group, and 7 doctor tests
  - honesty: a fresh uv lock resolves culture-core to 0.5.0 (not 0.4.0), and culture_core.doctor + the cli doctor group + the 7 doctor tests are reachable through the aliases, so the cutover loses no functionality

## Honesty conditions

- operators take no action and notice no difference; front-door maintainers can change branding/docs/deploy without touching engine code
- culture 13.6.0 on main verifiably has the 85-module culture/ tree, no culture-core dep, loose bounds, and culture=culture.cli:main (checkable in pyproject.toml + git)
- front-door slimming genuinely depends on this cutover and the drift risk is real — a fresh lock without the pin resolves to broken afi-cli/agex-cli/OTel/copilot versions
- the PR touches no wire/identity strings and does not modify agentirc/cultureagent deps or culture-core's caps
- diffing culture CLI help/output/config-discovery before vs after the cutover shows no operator-visible change
- running culture's retained test suite after the uv.lock refresh exits 0
- culture --help and a representative command produce output identical to 13.6.0 and culture.yaml is discovered from the same paths
- a dependency resolve cannot select afi-cli>=0.4, agex-cli>=0.14, OTel>=1.42, or copilot-sdk>0.2.0 given culture's aligned bounds
- after repointing, the installed culture command invokes culture_core.cli:main and behaves identically to 13.6.0
- the existing 96 test files pass unmodified because the shims make each culture.<name> import resolve to its culture_core counterpart, and the full suite green-lights every culture-core pin bump
- every mock.patch('culture.<x>') target resolves to the identical module object culture_core uses (sys.modules['culture.x'] is sys.modules['culture_core.x']), so the full retained suite passes unmodified and import culture.doctor resolves to culture_core.doctor
- a clean checkout after the follow-up PR holds no in-tree engine implementation (only the sys.modules alias bootstrap + front-door code), pulls culture-core 0.5.0, and the culture CLI / telemetry names / culture.yaml discovery are byte-for-byte identical to pre-cutover
- after the cutover a clean install of culture pulls culture-core 0.5.0 as a dependency and the repo holds no in-tree engine implementation (only the sys.modules alias bootstrap and front-door code)

## Success signals

- culture's full test suite passes against the pinned culture-core engine after a fresh uv.lock refresh (green because of the pin, not despite it)
- the culture operator command works unchanged end-to-end (dual entry point), and culture.yaml discovery + culture.* telemetry metric names are observably identical to 13.6.0
- a fresh resolve of culture's declared bounds can no longer pull a broken newer afi-cli / agex-cli / OTel / copilot-sdk — the aligned caps make the validated versions the only resolvable ones

## Scope / boundaries

- not re-litigating the extraction itself (culture-core#2/#3 are done and 0.4.0 is published); not absorbing agentirc or cultureagent (they stay separate embedded deps); not changing any wire/identity strings (telemetry culture.* names, culture.yaml filename); not loosening culture-core's dependency caps
- not a behavior change for operators — the culture CLI surface, output, and config discovery must be byte-for-byte the same; this is a packaging/sourcing move only

## Decisions

- culture keeps its [project.scripts] culture entry but repoints it to culture_core.cli:main (explicit in culture's own pyproject, redundant-but-harmless with culture-core's dual entry point)
- culture retains the full engine test suite and runs it against the pinned culture-core engine through the re-export shims (untouched imports), plus a small front-door packaging test (entry point resolves, wheel ships the shims); no coverage is dropped here
- the in-tree culture/ tree is replaced via sys.modules module-identity aliasing — each culture.<mod> IS the same module object as culture_core.<mod>, not naive 'from culture_core.x import *' re-exports — so the 152 mock.patch('culture....') targets patch the live engine module and the suite passes unmodified

## Hard questions

- risk: shims add a second import surface to maintain; if culture_core renames a module, the matching shim silently breaks until someone notices
- if culture stops running the engine suite, what catches a bad culture-core release at pin-bump time before it reaches operators?
- where does the alias bootstrap live, and what fails loudly if culture_core renames or drops a module the alias map still lists?
