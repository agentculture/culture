#!/usr/bin/env bash
set -euo pipefail

# poll-readiness.sh — wait until both automated PR reviewers (qodo + Copilot)
# have posted their full reviews, OR the PR is merged/closed, OR an iteration
# cap is hit.
#
# Designed to be invoked two ways:
#   (a) Synchronously by `workflow.sh await` (stdin/stdout local to the
#       caller — main session burns context during the wait).
#   (b) Asynchronously by a background subagent (Agent tool with
#       run_in_background:true). The subagent owns the wait so the main
#       session pays the cache cost only once, at completion.
#
# Usage:
#   poll-readiness.sh [--repo OWNER/REPO] [--max-iters N] [--interval SECS]
#                     [--require LIST] PR_NUMBER
#
# Defaults: --max-iters 30, --interval 60, --require qodo  (≈30-minute hard cap)
#
# --require accepts a comma-separated subset of {qodo,copilot}; the loop
# exits 0 only when every listed reviewer is "ready" (or PR state flips
# to MERGED/CLOSED). Override the default via STEWARD_PR_REVIEWERS=...
# Copilot is *not* required by default — its automated PR-review bot
# stopped posting top-level reviews on agentculture repos in 2026, so
# requiring it would cause every wait to TIMEOUT. Re-add `--require
# qodo,copilot` if Copilot starts posting again.
#
# Exit codes:
#   0  All required reviewers ready, OR PR state is MERGED / CLOSED.
#   1  TIMEOUT after --max-iters with at least one required reviewer pending.
#   2  Bad usage.
#
# Output:
#   stdout — final headline (≤10 lines), suitable for capture.
#   stderr — per-iteration diagnostics ("still waiting (qodo: …, copilot: …)").
#
# Detection heuristics (mirror cfafi's `poll` skill):
#   qodo ready    = ISSUE COMMENTS section contains "Code Review by Qodo"
#                   AND does NOT contain "Looking for bugs?" (qodo's
#                   "still analysing" placeholder).
#   Copilot ready = TOP-LEVEL REVIEWS header reports a count > 0.
#
# Dependencies: gh, jq, bash, curl  (same as the rest of the skill).

usage() {
    cat >&2 <<'EOF'
Usage: poll-readiness.sh [--repo OWNER/REPO] [--max-iters N] [--interval SECS]
                         [--require LIST] PR_NUMBER

Defaults: --max-iters 30, --interval 60, --require qodo
  (set STEWARD_PR_REVIEWERS to override the default --require list)

--require LIST  comma-separated subset of {qodo,copilot}. Exit 0 only
                when every listed reviewer is ready, or PR closes.
                Copilot is not required by default — its bot stopped
                posting top-level reviews on agentculture repos in 2026.

Exit 0 when all required reviewers ready (or PR closed),
exit 1 on TIMEOUT, exit 2 on bad usage.
EOF
    exit 2
}

require_value() {
    if [[ $# -lt 2 ]]; then
        echo "Missing value for $1" >&2
        usage
    fi
}

REPO=""
MAX_ITERS=30
INTERVAL=60
PR_NUMBER=""
REQUIRE="${STEWARD_PR_REVIEWERS:-qodo}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo)       require_value "$@"; REPO="$2"; shift 2 ;;
        --max-iters)  require_value "$@"; MAX_ITERS="$2"; shift 2 ;;
        --interval)   require_value "$@"; INTERVAL="$2"; shift 2 ;;
        --require)    require_value "$@"; REQUIRE="$2"; shift 2 ;;
        -h|--help)    usage ;;
        --) shift; break ;;
        -*) echo "Unknown flag: $1" >&2; usage ;;
        *)  PR_NUMBER="$1"; shift ;;
    esac
done

[[ -z "$PR_NUMBER" ]] && usage
[[ "$PR_NUMBER" =~ ^[0-9]+$ ]] || { echo "PR_NUMBER must be a positive integer" >&2; exit 2; }
[[ "$MAX_ITERS" =~ ^[0-9]+$ ]] || { echo "--max-iters must be a positive integer" >&2; exit 2; }
[[ "$INTERVAL"  =~ ^[0-9]+$ ]] || { echo "--interval must be a positive integer"  >&2; exit 2; }

# Parse --require into REQUIRE_QODO / REQUIRE_COPILOT booleans. Reject
# unknown reviewers so a typo (e.g. "qoda") doesn't silently make the
# loop exit on iteration 1.
REQUIRE_QODO=0
REQUIRE_COPILOT=0
IFS=',' read -ra _REQ <<<"$REQUIRE"
for r in "${_REQ[@]}"; do
    r="${r// /}"
    case "$r" in
        ""|qodo)   REQUIRE_QODO=1 ;;
        copilot)   REQUIRE_COPILOT=1 ;;
        *) echo "--require: unknown reviewer '$r' (valid: qodo, copilot)" >&2; exit 2 ;;
    esac
done
if [[ $REQUIRE_QODO -eq 0 && $REQUIRE_COPILOT -eq 0 ]]; then
    echo "--require: at least one reviewer must be required" >&2
    exit 2
fi

if [[ -z "$REPO" ]]; then
    REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PR_URL="https://github.com/${REPO}/pull/${PR_NUMBER}"

emit_headline() {
    # $1 final state, $2 qodo status, $3 copilot status, $4 iter count, $5 next-step hint
    cat <<EOF
PR URL:        ${PR_URL}
Final state:   $1
qodo:          $2
Copilot:       $3
Iterations:    $4 / ${MAX_ITERS}
Next step:     $5
EOF
}

iter=0
qodo_status="not-posted"
copilot_status="not-posted"

while (( iter < MAX_ITERS )); do
    iter=$((iter + 1))

    # 1. Short-circuit on closed/merged.
    pr_state=$(gh pr view "$PR_NUMBER" --repo "$REPO" --json state -q .state 2>/dev/null || echo "UNKNOWN")
    if [[ "$pr_state" == "MERGED" || "$pr_state" == "CLOSED" ]]; then
        emit_headline "$pr_state" "$qodo_status" "$copilot_status" "$iter" \
            "PR was ${pr_state,,} before reviewers finished — no triage needed."
        exit 0
    fi

    # 2. Fetch raw, untruncated bodies directly from the GitHub API.
    #    Reasoning: pr-comments.sh truncates comment bodies for human display,
    #    so its output isn't a reliable input for readiness logic — a long
    #    qodo comment can have its "Code Review by Qodo" marker fall past the
    #    truncation, and a stale "Looking for bugs?" placeholder elsewhere in
    #    the dump can flip a real review back to "placeholder-only". Using
    #    `gh api` here also avoids the SonarCloud round-trip pr-comments.sh
    #    now does on every iteration (qodo PR #17 review, perf bug).
    issue_json=$(gh api "repos/$REPO/issues/$PR_NUMBER/comments" --paginate 2>/dev/null) || {
        echo "iter ${iter}: gh api issues/comments failed; will retry" >&2
        sleep "$INTERVAL"
        continue
    }
    reviews_json=$(gh api "repos/$REPO/pulls/$PR_NUMBER/reviews" --paginate 2>/dev/null) || {
        echo "iter ${iter}: gh api pulls/reviews failed; will retry" >&2
        sleep "$INTERVAL"
        continue
    }

    # 3. qodo readiness — match the structural <h3>Code Review by Qodo</h3>
    #    header, which appears only in the done-state body. Cfafi's bare-text
    #    heuristic ("contains 'Code Review by Qodo' AND NOT 'Looking for
    #    bugs?'") false-positives when qodo's done review *quotes* either
    #    string while reporting bugs about polling code (PR #17 hit this).
    #    The placeholder comment uses a different layout (no <h3> wrapper),
    #    so the H3 marker alone is enough.
    qodo_real=$(echo "$issue_json" | jq '[
        .[] | select(.user.login == "qodo-code-review[bot]")
            | select(.body | contains("<h3>Code Review by Qodo</h3>"))
    ] | length')
    qodo_any=$(echo "$issue_json" | jq '[
        .[] | select(.user.login == "qodo-code-review[bot]")
    ] | length')
    if (( qodo_real > 0 )); then
        qodo_status="ready"
    elif (( qodo_any > 0 )); then
        qodo_status="placeholder-only"
    else
        qodo_status="not-posted"
    fi

    # 4. Copilot readiness — at least one top-level review with a non-empty
    #    body. Excludes "review whose only content is inline comments".
    copilot_count=$(echo "$reviews_json" | jq '[
        .[] | select((.user.login // "") | startswith("copilot"))
            | select((.body // "") != "")
    ] | length')
    if (( copilot_count > 0 )); then
        copilot_status="ready"
    else
        copilot_status="not-posted"
    fi

    # 5. Done?
    qodo_ok=1
    copilot_ok=1
    [[ $REQUIRE_QODO    -eq 1 && "$qodo_status"    != "ready" ]] && qodo_ok=0
    [[ $REQUIRE_COPILOT -eq 1 && "$copilot_status" != "ready" ]] && copilot_ok=0
    if [[ $qodo_ok -eq 1 && $copilot_ok -eq 1 ]]; then
        emit_headline "OPEN" "$qodo_status" "$copilot_status" "$iter" \
            "Required reviewers ready (require=${REQUIRE}); run pr-status.sh and triage."
        exit 0
    fi

    echo "iter ${iter}/${MAX_ITERS}: qodo=${qodo_status}, copilot=${copilot_status} (require=${REQUIRE}); sleeping ${INTERVAL}s" >&2
    if (( iter < MAX_ITERS )); then
        sleep "$INTERVAL"
    fi
done

# Fell off the loop — TIMEOUT.
emit_headline "TIMEOUT" "$qodo_status" "$copilot_status" "$MAX_ITERS" \
    "Hit ${MAX_ITERS}-iteration cap; re-run poll-readiness.sh or check PR manually."
exit 1
