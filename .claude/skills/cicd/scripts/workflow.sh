#!/usr/bin/env bash
set -euo pipefail

# culture-divergence: env var prefix renamed STEWARD_* → CULTURE_*; header
# prose rebranded "steward" → "culture". Vendored from steward 0.12.0 — see
# https://github.com/agentculture/steward/blob/main/.claude/skills/cicd/scripts/workflow.sh
#
# Culture cicd workflow — thin layer over `agex pr` plus two culture
# extensions (`status`, `await`) for SonarCloud gating and triage flow.
#
# Subcommands:
#   lint                   `agex pr lint --exit-on-violation`. Same rules
#                          culture used to vendor in portability-lint.sh
#                          (which still ships for one-off diff lints).
#   open  [gh-pr flags]    `agex pr open --delayed-read "$@"`. Creates the
#                          PR, then polls 180s for an initial briefing.
#                          Body via --body-file PATH or stdin; --title is
#                          required.
#   read  [PR] [--wait N]  `agex pr read "$@"`. One-shot briefing today;
#                          pass --wait N to poll for reviewer readiness.
#                          Covers what create-pr-and-wait / pr-comments /
#                          wait-and-check / poll-readiness used to do.
#   reply <PR>             `agex pr reply <PR>` (JSONL on stdin). agex
#                          auto-signs from culture.yaml; same JSONL
#                          shape as the old pr-batch.sh.
#   delta                  `agex pr delta`. Sibling alignment dump.
#
#   status <PR>            Culture extension: pr-status.sh — SonarCloud
#                          gate, OPEN issues, hotspots, unresolved
#                          inline-thread tally, deploy-preview URL.
#                          Source of truth for the `await` gate.
#   await  <PR>            Culture extension: `read --wait` for the
#                          briefing, then `status` for the gate. Exits
#                          non-zero on SonarCloud ERROR or unresolved
#                          threads. Tunables:
#                            CULTURE_PR_AWAIT_WAIT (default 1800)
#                              — seconds passed to `read --wait`.
#                            CULTURE_PR_AWAIT_SECONDS (legacy)
#                              — fixed pre-sleep, deprecated.
#
#   help                   print this message

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# agex's `--agent` flag accepts only claude-code|codex|copilot|acp. The
# workspace culture.yaml convention is `backend: claude`, so we always
# pass --agent explicitly to insulate culture from that naming gap.
# Override via CULTURE_AGEX_AGENT if you're running under codex/copilot/acp.
AGEX_AGENT="${CULTURE_AGEX_AGENT:-claude-code}"

require_agex() {
    if ! command -v agex >/dev/null 2>&1; then
        echo "✗ agex not on PATH. Install agex-cli (>=0.1)." >&2
        echo "  uv tool install agex-cli  # or pip install agex-cli" >&2
        exit 2
    fi
}

cmd="${1:-help}"
shift || true

case "$cmd" in
    lint)
        require_agex
        exec agex pr lint --agent "$AGEX_AGENT" --exit-on-violation "$@"
        ;;
    open)
        require_agex
        exec agex pr open --agent "$AGEX_AGENT" --delayed-read "$@"
        ;;
    read)
        require_agex
        exec agex pr read --agent "$AGEX_AGENT" "$@"
        ;;
    reply)
        require_agex
        PR="${1:?Usage: workflow.sh reply <PR>  (JSONL on stdin)}"
        exec agex pr reply --agent "$AGEX_AGENT" "$PR"
        ;;
    delta)
        require_agex
        exec agex pr delta --agent "$AGEX_AGENT" "$@"
        ;;
    status)
        PR="${1:?Usage: workflow.sh status <PR>}"
        exec bash "$SCRIPT_DIR/pr-status.sh" "$PR"
        ;;
    await)
        require_agex
        PR="${1:?Usage: workflow.sh await <PR>}"

        # Legacy fixed-sleep escape hatch.
        if [ -n "${CULTURE_PR_AWAIT_SECONDS:-}" ]; then
            echo "warning: CULTURE_PR_AWAIT_SECONDS is deprecated; prefer CULTURE_PR_AWAIT_WAIT." >&2
            echo "→ sleeping ${CULTURE_PR_AWAIT_SECONDS}s (legacy fixed-sleep) before agex pr read …" >&2
            sleep "$CULTURE_PR_AWAIT_SECONDS"
            WAIT_ARGS=()
        else
            WAIT="${CULTURE_PR_AWAIT_WAIT:-1800}"
            WAIT_ARGS=(--wait "$WAIT")
        fi

        # 1. agex pr read --wait — readiness loop + briefing.
        # Capture rc from the command itself (not from the negated test —
        # `if ! cmd; then rc=$?` would store the if-test status, always 0
        # in the failure branch, masking the real exit code).
        echo "── agex pr read ──────────────────────────────────────────────────────" >&2
        if agex pr read --agent "$AGEX_AGENT" "$PR" "${WAIT_ARGS[@]}"; then
            READ_RC=0
        else
            READ_RC=$?
        fi
        if [ "$READ_RC" -ne 0 ]; then
            echo "✗ agex pr read failed (exit $READ_RC)" >&2
            exit "$READ_RC"
        fi

        # 2. pr-status.sh — authoritative gate (Sonar QG, unresolved threads).
        echo >&2
        echo "── pr-status ─────────────────────────────────────────────────────────" >&2
        if STATUS_OUT=$(bash "$SCRIPT_DIR/pr-status.sh" "$PR" 2>&1); then
            STATUS_RC=0
        else
            STATUS_RC=$?
        fi
        printf '%s\n' "$STATUS_OUT"
        if [ "$STATUS_RC" -ne 0 ]; then
            echo >&2
            echo "✗ pr-status.sh failed (exit $STATUS_RC) — cannot determine PR state" >&2
            exit "$STATUS_RC"
        fi

        # 3. Gate. Markers in pr-status.sh output:
        #     "Quality Gate ERROR"          → Sonar fail
        #     "Unresolved: N" with N>0      → unresolved threads
        SONAR_FAIL=0
        UNRESOLVED=0
        if printf '%s\n' "$STATUS_OUT" | grep -qE 'Quality Gate ERROR'; then
            SONAR_FAIL=1
        fi
        if PENDING=$(printf '%s\n' "$STATUS_OUT" | grep -oE 'Unresolved:[[:space:]]+[0-9]+' | grep -oE '[0-9]+$' | head -1); then
            [ -n "${PENDING:-}" ] && [ "$PENDING" -gt 0 ] && UNRESOLVED=1
        fi
        if [ "$SONAR_FAIL" -eq 1 ] || [ "$UNRESOLVED" -eq 1 ]; then
            echo >&2
            [ "$SONAR_FAIL" -eq 1 ] && echo "✗ SonarCloud quality gate ERROR" >&2
            [ "$UNRESOLVED" -eq 1 ] && echo "✗ ${PENDING} unresolved review thread(s)" >&2
            exit 1
        fi
        echo >&2
        echo "✓ no SonarCloud ERROR, no unresolved threads" >&2
        ;;
    help|--help|-h)
        sed -n '8,41p' "${BASH_SOURCE[0]}" | sed 's/^# *//'
        ;;
    *)
        echo "unknown subcommand: $cmd" >&2
        echo "run '$(basename "$0") help' for usage." >&2
        exit 2
        ;;
esac
