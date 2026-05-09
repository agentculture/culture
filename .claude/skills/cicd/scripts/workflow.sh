#!/usr/bin/env bash
# Steward cicd workflow entry point (renamed from pr-review in 0.7.0).
#
# Subcommands:
#   lint                    run the portability lint on the current diff (staged + unstaged)
#   open-pr [gh pr flags]   gh pr create + sleep 180s + fetch reviewer comments.
#                           Use right after pushing the initial branch.
#                           Override the wait with --wait <secs>.
#   poll <PR>               fetch and display review comments
#   poll-readiness <PR>     loop until required reviewers are ready (default:
#                           qodo only; Copilot's bot stopped posting in 2026)
#                           — or PR closes / cap hits. Forwards extra flags
#                           to scripts/poll-readiness.sh (--max-iters N,
#                           --interval SECS, --require qodo[,copilot],
#                           --repo OWNER/REPO).
#   wait-after-push <PR>    sleep 180s then re-fetch comments. Use after pushing fixes.
#                           Override the wait with --wait <secs>.
#   await <PR>              poll for reviewer readiness (default: up to 30 × 60s,
#                           requires qodo only), then run pr-status + pr-comments.
#                           Exits non-zero on SonarCloud ERROR or unresolved
#                           threads. Tunables: STEWARD_PR_AWAIT_ITERS (default 30),
#                           STEWARD_PR_AWAIT_INTERVAL (default 60),
#                           STEWARD_PR_REVIEWERS (default "qodo").
#                           Legacy fixed-sleep mode: set STEWARD_PR_AWAIT_SECONDS=<n>
#                           (deprecated; emits a warning).
#   reply <PR>              batch reply to review comments (JSONL on stdin), --resolve
#   delta                   dump CLAUDE.md head + culture.yaml for each sibling project
#                           listed in skills.local.yaml (alignment-delta check)
#   help                    print this message

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$SKILL_DIR/../../.." && pwd)"

CFG="$REPO_ROOT/.claude/skills.local.yaml"
[ -f "$CFG" ] || CFG="$REPO_ROOT/.claude/skills.local.yaml.example"

# Read a top-level YAML list from CFG. Schema is intentionally tiny:
#   <key>:
#     - item
#     - item
# Stops at the next top-level key. No PyYAML dependency.
read_list() {
    awk -v key="$1" '
        function trim(s) { sub(/^[[:space:]]+/, "", s); sub(/[[:space:]]+$/, "", s); return s }
        {
            line = $0
            sub(/[[:space:]]+#.*$/, "", line)
        }
        in_list && line ~ /^[[:space:]]*-[[:space:]]*/ {
            item = line
            sub(/^[[:space:]]*-[[:space:]]*/, "", item)
            item = trim(item)
            sub(/^["\047]/, "", item); sub(/["\047]$/, "", item)
            if (item != "") print item
            next
        }
        in_list && line ~ /^[^[:space:]#]/ { exit }
        line ~ ("^" key ":[[:space:]]*($|#)") { in_list = 1 }
    ' "$CFG"
}

cmd="${1:-help}"
shift || true

case "$cmd" in
    lint)
        bash "$SCRIPT_DIR/portability-lint.sh"
        ;;
    open-pr)
        bash "$SCRIPT_DIR/create-pr-and-wait.sh" "$@"
        ;;
    poll)
        PR="${1:?Usage: workflow.sh poll <PR>}"
        bash "$SCRIPT_DIR/pr-comments.sh" "$PR"
        ;;
    poll-readiness)
        if [ $# -lt 1 ]; then
            echo "Usage: workflow.sh poll-readiness <PR> [--max-iters N] [--interval SECS] [--repo OWNER/REPO]" >&2
            exit 2
        fi
        bash "$SCRIPT_DIR/poll-readiness.sh" "$@"
        ;;
    wait-after-push)
        # Forward all remaining args (PR number plus any --wait/--repo
        # flags wait-and-check.sh accepts) so docs that promise
        # `--wait <secs>` actually work.
        if [ $# -lt 1 ]; then
            echo "Usage: workflow.sh wait-after-push <PR> [--wait SECS] [--repo OWNER/REPO]" >&2
            exit 2
        fi
        bash "$SCRIPT_DIR/wait-and-check.sh" "$@"
        ;;
    await)
        PR="${1:?Usage: workflow.sh await <PR>}"
        # Legacy fixed-sleep path — kept for back-compat. If
        # STEWARD_PR_AWAIT_SECONDS is set we honor it but warn; the modern
        # path is readiness-driven via poll-readiness.sh.
        if [ -n "${STEWARD_PR_AWAIT_SECONDS:-}" ]; then
            echo "warning: STEWARD_PR_AWAIT_SECONDS is deprecated; prefer STEWARD_PR_AWAIT_ITERS / _INTERVAL." >&2
            echo "→ waiting ${STEWARD_PR_AWAIT_SECONDS}s (legacy fixed-sleep) …" >&2
            sleep "$STEWARD_PR_AWAIT_SECONDS"
        else
            ITERS="${STEWARD_PR_AWAIT_ITERS:-30}"
            INTERVAL="${STEWARD_PR_AWAIT_INTERVAL:-60}"
            echo "── poll-readiness ────────────────────────────────────────────────────" >&2
            # Don't bail if the looper TIMEOUTs — still want to dump pr-status
            # and pr-comments so the user sees what *did* arrive.
            set +e
            bash "$SCRIPT_DIR/poll-readiness.sh" \
                --max-iters "$ITERS" --interval "$INTERVAL" "$PR"
            POLL_RC=$?
            set -e
            if [ "$POLL_RC" -ne 0 ]; then
                echo "(poll-readiness exited $POLL_RC — falling through to status/comments anyway)" >&2
            fi
        fi
        # pr-status.sh is the source of truth for the SonarCloud / unresolved
        # markers we gate on, so its failure must propagate — otherwise we'd
        # falsely claim "✓ no SonarCloud ERROR" when the check never ran.
        # Use the if-form so `set -e` doesn't abort before we report the rc.
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
        # pr-comments.sh is the user-visible thread dump — its failure is also
        # a hard fail so reviewers don't go untriaged.
        echo "── pr-comments ───────────────────────────────────────────────────────" >&2
        if bash "$SCRIPT_DIR/pr-comments.sh" "$PR"; then
            COMMENTS_RC=0
        else
            COMMENTS_RC=$?
        fi
        if [ "$COMMENTS_RC" -ne 0 ]; then
            echo >&2
            echo "✗ pr-comments.sh failed (exit $COMMENTS_RC) — review threads not fetched" >&2
            exit "$COMMENTS_RC"
        fi
        # Decide pass/fail from the captured pr-status.sh output. Markers:
        #   • "SonarCloud ❌ Quality Gate ERROR" → SonarCloud failed
        #   • "Unresolved: N" with N>0 → unresolved review threads
        SONAR_FAIL=0
        UNRESOLVED=0
        if printf '%s\n' "$STATUS_OUT" | grep -qE 'SonarCloud[[:space:]]+❌[[:space:]]+Quality Gate ERROR'; then
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
    reply)
        PR="${1:?Usage: workflow.sh reply <PR>  (JSONL on stdin)}"
        bash "$SCRIPT_DIR/pr-batch.sh" --resolve "$PR"
        ;;
    delta)
        any=0
        while IFS= read -r sibling; do
            [ -z "$sibling" ] && continue
            any=1
            sibling_abs="$REPO_ROOT/$sibling"
            if [ ! -d "$sibling_abs" ] && [ ! -d "$sibling" ]; then
                echo "=== $sibling ==="
                echo "(not present on disk — skipped)"
                continue
            fi
            target="$sibling_abs"
            [ -d "$target" ] || target="$sibling"
            echo "=== $target ==="
            if [ -f "$target/CLAUDE.md" ]; then
                head -40 "$target/CLAUDE.md"
                echo "..."
            else
                echo "(no CLAUDE.md)"
            fi
            echo "--- culture.yaml ---"
            if [ -f "$target/culture.yaml" ]; then
                cat "$target/culture.yaml"
            else
                echo "(no culture.yaml)"
            fi
            echo
        done < <(read_list sibling_projects)
        [ "$any" -eq 0 ] && echo "(no sibling_projects configured in $CFG)"
        ;;
    help|--help|-h)
        sed -n '2,29p' "${BASH_SOURCE[0]}" | sed 's/^# *//'
        ;;
    *)
        echo "unknown subcommand: $cmd" >&2
        echo "run '$(basename "$0") help' for usage." >&2
        exit 2
        ;;
esac
