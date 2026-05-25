---
name: run-tests
description: Run pytest with parallel execution and coverage. Use when running tests, verifying changes, or the user says "run tests", "test", or "pytest".
---

# Run Tests

Run the project's pytest suite with optional parallelism (pytest-xdist) and coverage.

## Usage

```bash
# Default: parallel + verbose (recommended)
bash .claude/skills/run-tests/scripts/test.sh -p

# Quick check: parallel + quiet
bash .claude/skills/run-tests/scripts/test.sh -p -q

# Full CI mode: parallel + coverage + xml report
bash .claude/skills/run-tests/scripts/test.sh --ci

# Specific test file
bash .claude/skills/run-tests/scripts/test.sh -p tests/test_socket_server.py

# Without parallelism (for debugging test ordering issues)
bash .claude/skills/run-tests/scripts/test.sh tests/test_rooms.py

# With coverage
bash .claude/skills/run-tests/scripts/test.sh -p -c
```

## Options

| Flag | Short | Description |
|------|-------|-------------|
| `--parallel` | `-p` | Run with `-n auto` (pytest-xdist, uses all CPU cores) |
| `--coverage` | `-c` | Enable coverage reporting to terminal |
| `--ci` | | Full CI mode: parallel + coverage + XML report + verbose |
| `--quick` | `-q` | Quiet output (no verbose, no coverage) |

Extra arguments are passed through to pytest (e.g., `-x` for stop-on-first-failure, `-k "pattern"` for filtering).

## When to Use Which Mode

- **After code changes:** `bash .claude/skills/run-tests/scripts/test.sh -p` — fast parallel run, verbose output
- **Quick sanity check:** `bash .claude/skills/run-tests/scripts/test.sh -p -q` — minimal output
- **Before PR / release:** `bash .claude/skills/run-tests/scripts/test.sh --ci` — matches CI exactly
- **Debugging flaky test:** `bash .claude/skills/run-tests/scripts/test.sh tests/test_flaky.py` — sequential, single file

## Provenance

Canonical supplier is **agentculture/guildmaster** (post steward→guildmaster
cutover). Culture's `scripts/test.sh` is intentionally **ahead** of guildmaster's
copy: it adds xdist shard-combine, stale-shard wipe, coverage-floor (exit 2)
surfacing, and combine/report/xml failure propagation that guildmaster's
simpler `exec pytest --cov` lacks. Do not overwrite from upstream on resync;
the improvements are offered back to guildmaster.
