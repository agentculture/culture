# Build Plan — culture now runs on the published culture-core engine: it pins culture-core 0.5.0, the in-tree culture/ tree is gone (replaced by sys.modules module-identity aliases onto culture_core.*), and the operator's culture CLI, telemetry, and config discovery work exactly as before

slug: `culture-now-runs-on-the-published-culture-core-eng` · status: `exported` · from frame: `culture-now-runs-on-the-published-culture-core-eng`

> culture now runs on the published culture-core engine: it pins culture-core 0.5.0, the in-tree culture/ tree is gone (replaced by sys.modules module-identity aliases onto culture_core.*), and the operator's culture CLI, telemetry, and config discovery work exactly as before

## Tasks

### t1 — pyproject + lock: add and pin culture-core ~=0.5.0 (the release forwarding doctor), repoint console entry [project.scripts] culture = culture_core.cli:main, and refresh uv.lock. The bound caps (agex-cli<0.14, afi-cli<0.4, OTel <1.42 / instrumentation+semconv <0.63b0, copilot==0.2.0) already landed in PR #455 — verify they remain, do not re-edit them

- covers: c20, h20, c10, h15, c12, h2, c5, h10
- acceptance:
  - pyproject.toml declares culture-core ~=0.5.0 (or ==0.5.0) in dependencies and [project.scripts] culture = 'culture_core.cli:main'
  - the #455 caps remain present and unchanged (agex-cli<0.14, afi-cli<0.4, OTel <1.42, instrumentation/semconv <0.63b0, github-copilot-sdk==0.2.0)
  - uv lock succeeds and resolves culture-core to exactly 0.5.0; a fresh resolve cannot select afi-cli>=0.4, agex-cli>=0.14, OTel>=1.42, or copilot-sdk>0.2.0

### t2 — replace the in-tree culture/ tree with a single sys.modules module-identity alias bootstrap onto `culture_core.*`: each culture.<mod> is registered as the SAME module object as culture_core.<mod> (not `from culture_core.x import *` re-exports), then delete the in-tree engine implementation. Sequence so the pin (t1) lands before/with the alias swap — no broken intermediate commit

- depends on: t1
- covers: c23, h23, c22, h22, c13, h3
- acceptance:
  - a single alias bootstrap makes sys.modules['culture.<x>'] is sys.modules['culture_core.<x>'] for every engine module; the in-tree implementation files are deleted (only the bootstrap + front-door code remain)
  - import culture.cli / .doctor / .clients / .protocol / .telemetry each resolves to the identical culture_core.* object (import culture.doctor is culture_core.doctor)
  - every commit on the branch leaves 'import culture' working and the culture CLI runnable — no commit deletes a module before the alias+pin that replaces it is in place

### t3 — version bump 13.6.1 -> 13.7.0 (minor: new dependency-sourcing of the engine) + Keep-a-Changelog entry describing the 0.5.0 pin + sys.modules alias cutover, via /version-bump

- depends on: t1
- covers: c13
- acceptance:
  - pyproject.toml version bumped from 13.6.1 to 13.7.0; CHANGELOG.md has a new top section describing the culture-core 0.5.0 pin + alias cutover; version-check CI passes

### t4 — front-door packaging/smoke test: assert the 'culture' entry point resolves to culture_core.cli:main, culture-core 0.5.0 is the locked/declared engine dep, import culture works, and culture.doctor aliases culture_core.doctor (guards the doctor functionality through the alias)

- depends on: t1, t2
- covers: c9, h14
- acceptance:
  - a new test asserts the importlib.metadata entry point 'culture' targets culture_core.cli:main and that 'import culture' succeeds
  - the test asserts culture-core 0.5.0 is the locked/declared engine dependency and that culture.doctor is culture_core.doctor

### t5 — behavior-parity verification: full retained suite green through the aliases after a fresh uv.lock (including the 7 doctor tests via the alias), and culture --help / culture.yaml discovery / culture.* telemetry names observably identical to pre-cutover

- depends on: t2, t4
- covers: c8, h13, c2, h7, c3, h8, c6, h11, c7, h12
- acceptance:
  - /run-tests passes: all retained engine tests (incl. the 7 doctor tests) green against the pinned culture-core 0.5.0 through the aliases
  - culture --help output, culture.yaml discovery paths, and culture.* telemetry metric names match pre-cutover (no operator-visible change; no wire/identity-string change)
