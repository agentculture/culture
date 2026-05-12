#!/usr/bin/env bash
# Run pytest with optional parallelism and coverage.
# Usage: bash test.sh [OPTIONS] [PYTEST_ARGS...]
#
# Options:
#   --parallel, -p    Run with -n auto (pytest-xdist)
#   --coverage, -c    Enable coverage reporting
#   --ci              Mimic full CI invocation (-n auto + coverage + xml)
#   --quick, -q       Quick mode: no coverage, quiet output
#
# Extra args are passed through to pytest.
#
# When coverage is enabled, this script runs `coverage combine` after pytest
# so xdist worker `.coverage.*` files are merged before the final report.
# pyproject.toml sets `parallel = true` so the worker files are produced; we
# suppress pytest-cov's auto-report (`--cov-report=`) and render manually via
# `coverage report` / `coverage xml` after combine.

set -uo pipefail

PARALLEL=""
COVERAGE=""
CI_MODE=""
QUIET=""
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --parallel|-p) PARALLEL=1; shift ;;
        --coverage|-c) COVERAGE=1; shift ;;
        --ci)          CI_MODE=1; shift ;;
        --quick|-q)    QUIET=1; shift ;;
        *)             EXTRA_ARGS+=("$1"); shift ;;
    esac
done

CMD=(uv run pytest)
NEED_COMBINE=""
NEED_XML=""

if [[ -n "$CI_MODE" ]]; then
    CMD+=(-n auto --cov=culture --cov-report= -v)
    NEED_COMBINE=1
    NEED_XML=1
elif [[ -n "$QUIET" ]]; then
    CMD+=(-q)
    [[ -n "$PARALLEL" ]] && CMD+=(-n auto)
else
    [[ -n "$PARALLEL" ]] && CMD+=(-n auto)
    if [[ -n "$COVERAGE" ]]; then
        CMD+=(--cov=culture --cov-report=)
        NEED_COMBINE=1
    fi
    CMD+=(-v)
fi

CMD+=("${EXTRA_ARGS[@]}")

echo "Running: ${CMD[*]}"
"${CMD[@]}"
PYTEST_RC=$?

if [[ -n "$NEED_COMBINE" ]]; then
    # Combine xdist worker shards into a single .coverage, then render.
    # `coverage combine` is a no-op (or warns) when only one shard exists.
    uv run coverage combine -q 2>/dev/null || true
    uv run coverage report
    REPORT_RC=$?
    if [[ -n "$NEED_XML" ]]; then
        uv run coverage xml -o coverage.xml
    fi
    # Surface a coverage-floor failure (exit 2 from `coverage report`) if
    # pytest itself passed.
    if [[ "$PYTEST_RC" -eq 0 && "$REPORT_RC" -ne 0 ]]; then
        PYTEST_RC=$REPORT_RC
    fi
fi

exit "$PYTEST_RC"
