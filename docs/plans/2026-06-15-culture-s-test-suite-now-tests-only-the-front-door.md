# Build Plan — culture's test suite now tests ONLY the front-door's integration with culture_core: it runs against a no-logic fake culture_core that doubles the integration contract, and the engine-logic tests are gone — a culture_core internal bug no longer fails culture's suite

slug: `culture-s-test-suite-now-tests-only-the-front-door` · status: `exported` · from frame: `culture-s-test-suite-now-tests-only-the-front-door`

> culture is the public-facing space of CULTURE.DEV — focused on the integration and the front, not the engine. Its test suite reflects that identity: it covers ONLY the front-door's integration with culture_core, runs against a no-logic fake culture_core that doubles the integration contract, and the engine-logic tests (culture_core's domain) are gone — a culture_core internal bug no longer fails culture's suite.

## Tasks

### t1 — Build a fake culture_core structural skeleton (behavior-free), bounded by the front-door touch-surface

- covers: c4, c11, h1, h7
- acceptance:
  - A fake culture_core, constructed in tests without importing the real engine, exposes EXACTLY the front-door touch-surface: culture_core + cli (with main + doctor), clients.claude.config, persistence, protocol, telemetry, doctor, pidfile (with read_pid), and a skills package — each module/attribute present but behavior-free.
  - The fake culture_core is a package with an EMPTY __path__: an unseeded submodule (e.g. culture_core.nope) raises ModuleNotFoundError, while every seeded submodule resolves from sys.modules. No bundled data (no SKILL.md) and no version contract.

### t2 — Seed the fake under the alias finder via conftest, xdist-safe; strip dead engine fixtures

- depends on: t1
- covers: h2
- acceptance:
  - tests/conftest.py registers the fake into sys.modules for culture_core and every needed submodule at conftest-import time (before any test imports culture.*), reusing the existing claude_agent_sdk-stub pattern; the now-dead engine fixtures (claude_agent_sdk stub, culture.telemetry/culture.bots imports + their fixtures) are removed.
  - Under 'pytest -n auto', on every xdist worker, importing culture.cli resolves to the fake (culture.cli is sys.modules['culture_core'].cli) and the real installed culture_core is never imported by the suite (assertable).

### t3 — Trim tests/test_frontdoor_cutover.py to fake-compatible mechanism tests only

- depends on: t1, t2
- covers: c6, h8
- acceptance:
  - tests/test_frontdoor_cutover.py keeps only mechanism tests (alias module-identity, deep-nested identity, __spec__ restoration WITHOUT bundled-data assertions, mock.patch targeting, missing-module parity, bare-import-is-front-door, console entry-point metadata, python -m culture, version shim) and every kept test passes against the fake.
  - All engine-content assertions are removed: no surviving test asserts culture-core==0.5.x is installed, nor that culture_core.skills/communicate/SKILL.md exists as a resource.

### t4 — Delete the engine-logic test suite and prove isolation

- depends on: t2, t3
- covers: c3, c7, c12, h5, h9
- acceptance:
  - All 129 engine-logic test files plus tests/harness/ and tests/telemetry/ are removed via git rm; tests/ retains only test_frontdoor_cutover.py, conftest.py, and the fake.
  - 'pytest -n auto' is green with the fake as culture_core, AND introducing a deliberate logic bug into the real installed culture_core does NOT redden culture's suite (the isolation proof).

### t5 — Document the fake/injection and retract the pin-bump-guard convention

- depends on: t4
- covers: c2, c5, h4, h6
- acceptance:
  - A docs/ page documents the fake culture_core, the conftest injection mechanism, and the deliberate decision that culture's CI no longer guards culture_core pin bumps (safety delegated to culture_core's upstream CI).
  - CLAUDE.md and the memory note no longer claim the retained suite guards each culture-core pin bump; the parked follow-up (manual release-time smoke check: culture --version + import culture.cli against the real engine) is recorded in the docs/release process.

### t6 — Version bump, CHANGELOG, and coverage/CI sanity for the trimmed suite

- depends on: t4
- acceptance:
  - /version-bump applied (pyproject.toml + CHANGELOG.md + uv.lock if changed); the CHANGELOG entry describes the test-isolation change and the dropped pin-bump guard.
  - coverage source=['culture'] still passes the configured floor with the trimmed suite, and .github/workflows/tests.yml runs the trimmed suite green with NO real-engine import-smoke step added (honors the non-goal).
