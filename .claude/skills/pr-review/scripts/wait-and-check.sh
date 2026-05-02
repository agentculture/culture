#!/usr/bin/env bash
set -euo pipefail

# Wait another N seconds (default 180 = 3 min) and re-check PR feedback.
#
# Use after `create-pr-and-wait.sh` if the first 3-minute window came back
# empty or thin and you suspect a slow reviewer. Different from polling: this
# script is "give it one more deliberate window," not "spam every minute."
#
# Usage:
#   wait-and-check.sh <PR_NUMBER> [--wait SECS] [--repo OWNER/REPO]
#
# Flags:
#   --wait SECS     How long to sleep before fetching comments. Default 180.
#   --repo R        Override the repo (default: auto-detect from git remote).
#
# Exit codes:
#   0   Wait completed and feedback fetched.
#   2   Bad usage (missing PR number).
#   *   Whatever the comment-fetch step returns.

WAIT_SECS=180
REPO=""
PR_NUM=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --wait) WAIT_SECS="$2"; shift 2 ;;
        --repo) REPO="$2"; shift 2 ;;
        *) PR_NUM="$1"; shift ;;
    esac
done

if [[ -z "$PR_NUM" ]]; then
    echo "Usage: wait-and-check.sh <PR_NUMBER> [--wait SECS] [--repo OWNER/REPO]" >&2
    exit 2
fi

echo "Waiting ${WAIT_SECS}s before re-checking PR #${PR_NUM}..."
sleep "$WAIT_SECS"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_COMMENTS="$SCRIPT_DIR/pr-comments.sh"
GLOBAL_COMMENTS="$HOME/.claude/skills/pr-review/scripts/pr-comments.sh"

REPO_ARG=()
if [[ -n "$REPO" ]]; then
    REPO_ARG=(--repo "$REPO")
fi

if [[ -x "$PROJECT_COMMENTS" ]]; then
    bash "$PROJECT_COMMENTS" "${REPO_ARG[@]}" "$PR_NUM"
elif [[ -x "$GLOBAL_COMMENTS" ]]; then
    bash "$GLOBAL_COMMENTS" "${REPO_ARG[@]}" "$PR_NUM"
else
    echo "Note: pr-comments.sh not found at $PROJECT_COMMENTS or $GLOBAL_COMMENTS — using inline gh api fallback." >&2
    if [[ -z "$REPO" ]]; then
        REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
    fi
    echo "════════════════ INLINE REVIEW COMMENTS ════════════════"
    gh api "repos/$REPO/pulls/$PR_NUM/comments" --paginate \
        --jq '.[] | "── ID: \(.id) ── \(.user.login) on \(.path):\(.original_line // .line // "?") ──\n\(.body)\n"'
    echo ""
    echo "════════════════ ISSUE COMMENTS ════════════════"
    gh api "repos/$REPO/issues/$PR_NUM/comments" --paginate \
        --jq '.[] | "── ID: \(.id) ── \(.user.login) ──\n\(.body)\n"'
    echo ""
    echo "════════════════ TOP-LEVEL REVIEWS ════════════════"
    gh api "repos/$REPO/pulls/$PR_NUM/reviews" --paginate \
        --jq '.[] | select((.body // "") != "") | "── ID: \(.id) ── \(.user.login) ── State: \(.state) ──\n\(.body)\n"'
fi
