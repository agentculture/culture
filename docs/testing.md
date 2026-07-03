# Testing

## One suite, the engine's

Since the 14.0.0 merge-back (issue #462) the `culture_core` engine lives
in-tree again, and its full test suite (1683 tests) runs here — this is the
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
  Enforced backends: `claude`/`codex`/`colleague` — colleague's client dir
  (`culture_core/clients/colleague/`) has landed, so it now counts for parity
  (existence-gated). `copilot`/`acp` are stale-exempt — installable and
  working as-is, neither triggering nor demanded by parity, pending
  re-validation in a future cycle.

## Live deployment probe (always-on mesh)

`scripts/always-on-probe.sh` verifies the deployed spark mesh end to end — the
"always-on" announcement (`docs/specs/2026-07-03-the-spark-culture-mesh-is-always-on-under-cli-prov.md`).
It is a **deployment check, not part of the pytest suite**: run it against the
live host after a deploy, never in CI. It prints PASS/FAIL/SKIP per check and
exits non-zero on any FAIL:

1. **units** — the five CLI-provisioned culture units (server, console, and the
   three agents `spark-culture` / `spark-agentirc` / `spark-colleague`) plus the
   `cloudflared-chat` tunnel are `active`.
2. **fail-fast** — `culture server start` with a missing/invalid `--mesh-config`
   exits **78** (EX_CONFIG), so the unit's `RestartPreventExitStatus=78` parks it
   instead of crash-looping (the 2026-07-03 outage class). Verify locally:

   ```console
   $ culture server start --mesh-config /nonexistent.yaml --foreground; echo $?
   error: invalid mesh config '/nonexistent.yaml': [Errno 2] No such file or directory: '/nonexistent.yaml'
   hint: fix or regenerate the file ('culture mesh setup'), or start with --link instead of --mesh-config
   78
   ```

3. **console** — `https://chat.agentculture.org` answers `200` via a Cloudflare
   Access service token.
4. **media** — an image and an audio clip (`scripts/probe-fixtures/`) upload and
   come back **byte-identical** from their public capability URL, proving
   `media.public_base_url` points at the public origin (not `127.0.0.1`).

Checks 3 and 4 need a CF Access service token in the environment; without it
they SKIP so 1 and 2 still run headless:

```bash
export CF_ACCESS_CLIENT_ID=...  CF_ACCESS_CLIENT_SECRET=...
scripts/always-on-probe.sh
```

The probe measures the **deployed** tool; it goes fully green only after these
engine fixes ship to spark (`uv tool upgrade culture` + unit reinstall).

## History

The 13.7.x front-door-only arrangement (fake engine, pin-decoupled CI) is
documented in `docs/specs/2026-06-15-culture-s-test-suite-now-tests-only-the-front-door.md`
and was reversed by the merge-back. See
`docs/specs/2026-06-14-culture-now-runs-on-the-published-culture-core-eng.md`
for the cutover that created the split, and issue #462 for the decision to end
it.
