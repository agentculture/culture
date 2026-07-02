# Testing

## One suite, the engine's

Since the 14.0.0 merge-back (issue #462) the `culture_core` engine lives
in-tree again, and its full test suite (~1400 tests) runs here — this is the
only suite. It came over verbatim from culture-core 0.17.0:

- Real servers on OS-assigned random ports, real TCP connections — no server
  mocks.
- pytest-asyncio in strict mode (explicit `@pytest.mark.asyncio` markers).
- pytest-xdist parallel execution (`-n auto`); coverage floor 90, enforced.
- `tests/conftest.py` stubs `claude_agent_sdk` at collection time (tests never
  call the real SDK) and provides the `IRCTestClient` / OTel in-memory
  fixtures.

Run it with `/run-tests` (parallel, verbose) or `/run-tests --ci` (adds
coverage + xml). Do not run `pytest` directly.

## The front-door seam tests

`tests/test_frontdoor_cutover.py` pins the import seam introduced by the #454
cutover and retained by the merge-back: the meta-path finder in
`culture/__init__.py` aliases every `culture.<x>` import to the identical
`culture_core.<x>` module object (module identity), the `culture` console
command targets `culture_core.cli:main`, and the distribution ships both
packages with no dependency on the retired `culture-core` dist.

These tests run against the **real in-tree engine**. The split-era
behavior-free fake `culture_core` harness (`tests/_fake_engine.py` and its
conftest seeding) is gone: its purpose was to decouple culture's CI from an
externally pinned engine distribution, and no such distribution exists
anymore.

## Guard suites worth knowing

- `tests/test_engine_identity.py` — forbids stale migration markers, stale
  `culture/<engine-dir>/` paths, and stale dotted `culture.<module>` refs in
  engine code (wire/identity strings like `culture.agentirc`, `culture.irc.*`,
  and the `culture.yaml` filename are allowlisted — never rename those). The
  front-door suite is exempt from the dotted-ref scan: exercising the
  `culture.*` alias namespace is its job.
- `tests/test_packaging_entrypoints.py` — pins the packaging contract: the
  `culture` and `culture-core` console scripts (both →
  `culture_core.cli:main`) from one distribution, telemetry wire strings, and
  the `CULTURE_YAML` constant.
- `tests/test_backend_parity.py` + the `backend-parity` CI job — the
  all-backends rule (`python -m culture_core.devtools.backend_parity`).

## History

The 13.7.x front-door-only arrangement (fake engine, pin-decoupled CI) is
documented in `docs/specs/2026-06-15-culture-s-test-suite-now-tests-only-the-front-door.md`
and was reversed by the merge-back. See
`docs/specs/2026-06-14-culture-now-runs-on-the-published-culture-core-eng.md`
for the cutover that created the split, and issue #462 for the decision to end
it.
