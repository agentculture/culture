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
#
# Stale `.coverage` / `.coverage.*` shards from a previous run are removed
# before pytest so `coverage combine` can never silently merge old data into
# the new report.

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

# Wipe stale coverage data so a failed `coverage combine` can't mask a real
# problem by merging old shards into the new report.
if [[ -n "$NEED_COMBINE" ]]; then
    rm -f .coverage .coverage.*
fi

echo "Running: ${CMD[*]}"
"${CMD[@]}"
PYTEST_RC=$?

FINAL_RC="$PYTEST_RC"

if [[ -n "$NEED_COMBINE" ]]; then
    # Combine xdist worker shards into a single `.coverage`. When only one
    # shard exists, the parallel-mode file is still named `.coverage.<host>.*`
    # so we always have shards to merge — but be defensive: only combine when
    # shard files actually exist, and propagate real combine failures.
    shopt -s nullglob
    SHARDS=(.coverage.*)
    shopt -u nullglob
    if (( ${#SHARDS[@]} > 0 )); then
        uv run coverage combine -q
        COMBINE_RC=$?
        if (( COMBINE_RC != 0 )); then
            echo "coverage combine failed (rc=$COMBINE_RC) — refusing to report on partial data" >&2
            (( FINAL_RC == 0 )) && FINAL_RC="$COMBINE_RC"
        fi
    elif [[ ! -f .coverage ]]; then
        echo "no coverage data produced — skipping report" >&2
        (( FINAL_RC == 0 )) && FINAL_RC=1
    fi

    if [[ -f .coverage ]]; then
        uv run coverage report
        REPORT_RC=$?
        # Surface a coverage-floor failure (exit 2 from `coverage report`)
        # over a passing pytest run.
        if (( FINAL_RC == 0 && REPORT_RC != 0 )); then
            FINAL_RC="$REPORT_RC"
        fi
    fi

    if [[ -n "$NEED_XML" ]] && [[ -f .coverage ]]; then
        uv run coverage xml -o coverage.xml
        XML_RC=$?
        if (( XML_RC != 0 )); then
            echo "coverage xml failed (rc=$XML_RC) — coverage.xml may be missing or stale" >&2
            (( FINAL_RC == 0 )) && FINAL_RC="$XML_RC"
        fi
    fi
fi

exit "$FINAL_RC"
