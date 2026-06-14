# Build Plan — culture now runs on the published culture-core engine: it pins culture-core 0.4.0, the in-tree culture/ tree is gone (deleted or reduced to thin re-export shims), and the operator's culture CLI, telemetry, and config discovery work exactly as before

slug: `culture-now-runs-on-the-published-culture-core-eng` · status: `exported` · from frame: `culture-now-runs-on-the-published-culture-core-eng`

> culture now runs on the published culture-core engine: it pins culture-core 0.4.0, the in-tree culture/ tree is gone (deleted or reduced to thin re-export shims), and the operator's culture CLI, telemetry, and config discovery work exactly as before

## Tasks

### t1 — pyproject + lock: pin culture-core==0.4.0, align overlapping dep bounds down to culture-core's caps (agex-cli<0.14, afi-cli<0.4, OTel stack <1.42, instrumentation/semconv <0.63b0), repoint console entry culture=culture_core.cli:main, refresh uv.lock

- covers: c11, c12, c10, c5, h1, h2, h15, h10
- acceptance:
  - pyproject.toml pins culture-core (==0.4.0 or ~=0.4.0) and [project.scripts] culture = culture_core.cli:main
  - agex-cli<0.14, afi-cli<0.4, OTel api/sdk/exporter <1.42 and instrumentation/semconv <0.63b0 are declared (subset of culture-core's caps)
  - uv lock succeeds, resolves culture-core to 0.4.0, and a resolve cannot select afi-cli>=0.4 / agex-cli>=0.14 / OTel>=1.42 / copilot-sdk>0.2.0

### t2 — shim the in-tree culture/ tree: replace all 85 modules with thin re-export shims onto culture_core.* so import culture.x keeps working; sequence so no commit deletes a module before its shim/pin is in place

- depends on: t1
- covers: c1, c4, c13, h6, h9, h3
- acceptance:
  - every former culture/<mod> is a shim re-exporting culture_core.<mod>; import culture.cli/.doctor/.clients/.protocol/.telemetry resolves to culture_core.*
  - no commit on the branch leaves culture un-importable or the CLI un-runnable (pin+shim land together, no broken intermediate)

### t3 — version bump 13.6.0 -> next + CHANGELOG entry for the culture-core cutover (via /version-bump)

- depends on: t1
- covers: c13
- acceptance:
  - version bumped from 13.6.0 in pyproject.toml; CHANGELOG.md has a new top section describing the pin->shim cutover; version-check CI passes

### t4 — front-door packaging/smoke test: assert the culture entry point resolves to culture_core.cli:main, culture-core==0.4.0 is a locked dep, and import culture works

- depends on: t1, t2
- covers: c9, h6, h7
- acceptance:
  - new test asserts importlib.metadata entry point 'culture' targets culture_core.cli:main and that import culture succeeds
  - test asserts culture-core 0.4.0 is the locked/declared engine dependency

### t5 — behavior-parity verification: full retained engine suite green through the shims after a fresh lock, and culture CLI help / culture.yaml discovery / culture.* telemetry names observably identical to 13.6.0

- depends on: t2, t4
- covers: c8, c2, c3, c6, c7, h7, h8, h11, h12, h13, h14
- acceptance:
  - /run-tests passes: all retained engine tests green against the pinned culture-core
  - culture --help output, culture.yaml discovery paths, and culture.* telemetry metric names match 13.6.0 (no operator-visible change; no wire-string change)
