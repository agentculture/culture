# culture's test suite now tests ONLY the front-door's integration with culture_core: it runs against a no-logic fake culture_core that doubles the integration contract, and the engine-logic tests are gone — a culture_core internal bug no longer fails culture's suite

> culture is the public-facing space of CULTURE.DEV — focused on the integration and the front, not the engine. Its test suite reflects that identity: it covers ONLY the front-door's integration with culture_core, runs against a no-logic fake culture_core that doubles the integration contract, and the engine-logic tests (culture_core's domain) are gone — a culture_core internal bug no longer fails culture's suite.

## Audience

- culture maintainers + CI: anyone bumping the culture-core pin or editing the front-door (culture/__init__.py, __main__.py).

## Before → After

- Before: 132 test files / 1423 tests run against the REAL culture_core; ~104 files import culture.* and exercise engine logic (persistence, bots, telemetry, config, overview) through the alias. The suite is really culture_core's logic suite running a second time; a culture_core behavior change can redden culture's CI even when the front-door is fine.
- After: culture's suite is a small integration-contract suite. A fake culture_core (structural skeleton, no behavior) stands in for the engine; the surviving tests assert the front-door's integration invariants (alias module-identity, __spec__/resources preservation, mock.patch targeting, missing-module parity, console entry point, python -m culture, version shim). Engine-logic tests are deleted.

## Why it matters

- Front-door and engine are now separate packages with separate ownership. culture should test what it OWNS (the aliasing seam), not re-run culture_core's logic. Isolation makes culture's CI fast, stable across engine releases, and honest about its true responsibility.

## Requirements

- The fake culture_core is a structural skeleton exposing only what the surviving front-door tests reference: importable culture_core + nested submodules (cli with main+doctor, persistence, protocol, telemetry, clients.claude.config, doctor, pidfile with read_pid, a skills package), each behavior-free. Its size is bounded by the front-door touch-surface, not culture_core's API.
  - honesty: Every surviving test in tests/test_frontdoor_cutover.py (minus the deleted engine-content assertions) passes with the fake seeded as culture_core and the real engine unused.

## Honesty conditions

- The audience is real: the people who edit culture/__init__.py or bump culture-core~=0.5.0 are the ones whose CI this suite gates.
- The before-state numbers are accurate: 132 files / 1423 tests today, ~104 importing culture.*, dominated by engine-logic modules (verified by grep).
- The fake culture_core is a structural skeleton: it exposes every module/attribute the front-door's alias finder + __main__ + entry point actually touch (importable culture_core, a cli.main, and whatever submodules the surviving integration tests import), but each has NO real behavior.
- Ownership is actually split: culture_core has its own upstream test suite that covers the engine logic culture is about to stop testing.
- The fake stays minimal: its size is bounded by the front-door's touch-surface, not by culture_core's API; it needs no updates when culture_core changes behavior (only when the front-door touches a NEW attribute).
- There exists a runnable mode where culture's pytest run resolves culture_core to the fake (not the installed 0.5.x), the suite is green, AND an intentional logic regression in the real engine leaves culture's suite green.
- The announcement is verifiable on both halves: culture's shipped surface is the front-door/integration (culture/ is just __init__.py + __main__.py over culture_core), AND after the change 'pytest' resolves culture_core to the fake, is green, and the engine-logic test files no longer exist in tests/.

## Success signals

- Running culture's suite with the real culture_core swapped for the no-logic fake passes; deleting the engine-logic tests leaves a small green suite; injecting a deliberate logic bug into the (real) engine does NOT redden culture's suite (proves isolation).

## Scope / boundaries

- NOT re-testing culture_core behavior, NOT building a maintained mock of the full engine API, NOT a general mocking framework. The fake exposes only the structural surface the front-door touches.

## Non-goals

- No real-engine smoke lane, no two-mode pytest, no CI import-smoke against the installed engine. culture's pytest never touches the real culture_core.

## Decisions

- culture's CI will NOT guard culture_core pin bumps. The suite runs ONLY against the fake; pin-bump/regression safety is delegated entirely to culture_core's own upstream CI. Accepted tradeoff: a culture-core release could break culture silently until runtime.
- Tests asserting real engine content (culture-core==0.5.x installed; culture_core.skills/communicate/SKILL.md resource exists) are DELETED, not moved or rewritten. The fake carries no bundled data and no version contract.

## Hard questions

- risk: Net test value could DROP: deleting 1400+ tests removes the only place culture's CI exercises the engine end-to-end. If the pin-bump guard isn't replaced, regressions reach users.
- After logic tests are gone and the suite runs against a fake, WHAT catches a real culture_core release that breaks culture at pin-bump time? The current retained suite IS that guard (per CLAUDE.md + memory). Removing it removes the guard unless we replace it.
- How is the fake injected so the alias finder resolves to it? (e.g. conftest pre-seeds sys.modules['culture_core*'] before 'import culture', or a sitecustomize/shadow package.) Is that swap clean and reliable under pytest-xdist?
- risk: A hand-maintained fake drifts from culture_core's real shape; the front-door could change to touch a new attribute the fake lacks, and the fake-based suite wouldn't catch the resulting real-world breakage.
- Some current 'integration' tests assert REAL engine content: culture-core==0.5.x installed, and culture_core.skills/communicate/SKILL.md exists as a resource. These cannot pass against a no-content fake. Do they get deleted, or kept in a separate real-engine lane?

## Open / follow-up

- Accepted risk follow-up: since culture's CI no longer exercises the real engine, document a manual/release-time smoke check (culture --version, import culture.cli against the real engine) in the release process even though it is out of test scope.
