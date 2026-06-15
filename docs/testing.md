# Testing

## Front-door test suite

culture is the thin front-door over the culture_core engine. Its test suite covers ONLY the front-door integration seam: the alias finder in `culture/__init__.py`, the console entry point, module identity, and `mock.patch` targeting. It does NOT test culture_core logic.

## The fake culture_core

`tests/_fake_engine.py` builds a behavior-free structural skeleton via `build_fake_culture_core()`. It exposes exactly the modules the front-door touches:

- `culture_core` (top-level package)
- `culture_core.cli` with `main` and `doctor`
- `culture_core.clients.claude.config`
- `culture_core.persistence`
- `culture_core.protocol`
- `culture_core.telemetry`
- `culture_core.doctor`
- `culture_core.pidfile` with `read_pid`
- `culture_core.skills`

Each module has an empty `__path__` so unseeded submodules raise `ModuleNotFoundError`. The fake imports NO real engine code.

## Injection

`tests/conftest.py` seeds the fake into `sys.modules` before any test imports `culture.*`. Because pytest-xdist spawns fresh workers, the injection runs afresh in each worker, so `culture.<x>` always resolves to the fake. The real engine is never imported during the suite (`culture_core.__file__` is `None`).

## Deliberate decision and tradeoff

Because culture's CI runs ONLY against the fake, it no longer guards culture_core pin bumps. That regression-safety is delegated entirely to culture_core's own upstream CI.

**Accepted tradeoff:** a culture_core release could break culture silently until runtime.

## Follow-up: manual release smoke check

Since CI no longer exercises the real engine, releases should include a manual smoke check against the installed real culture_core:

```bash
culture --version
python -c "import culture.cli"
```

## References

- Spec: `docs/specs/2026-06-15-culture-s-test-suite-now-tests-only-the-front-door.md`
- Plan: `docs/plans/2026-06-15-culture-s-test-suite-now-tests-only-the-front-door.md`
