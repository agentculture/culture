#!/usr/bin/env bash
set -euo pipefail

# Create a PR, wait for automated reviewers (qodo, copilot, sonarcloud) to
# post comments, then dump the feedback. Wraps the manual `gh pr create →
# sleep 300 → pr-comments.sh` sequence into one invocation.
#
# Usage:
#   create-pr-and-wait.sh --title "Title" --body-file PATH [--wait SECS] [extra gh pr create flags...]
#   create-pr-and-wait.sh --title "Title" [--wait SECS] [extra gh pr create flags...] < body-on-stdin
#
# Flags:
#   --title TITLE     PR title (required)
#   --body-file PATH  Read the body from a file. If omitted, reads stdin.
#   --wait SECS       How long to sleep after `gh pr create` before fetching
#                     comments. Default: 180 (3 min). qodo/copilot/sonarcloud
#                     usually post within this window. Use `wait-and-check.sh`
#                     to extend by another 3 min if reviewers haven't finished.
#   Any other flags pass through to `gh pr create` (e.g. --base, --reviewer).
#
# Behavior:
#   1. `gh pr create` with the given title/body (and any passthrough flags).
#   2. `sleep $WAIT_SECS` to give reviewers time to post.
#   3. Run `pr-comments.sh <PR_NUMBER>` to dump inline + issue + review
#      comments. Looks for the script in this directory first, then in the
#      user's global ~/.claude/skills/pr-review/scripts/, then falls back to
#      an inline `gh api` dump.
#
# Exit codes:
#   0  PR created and feedback fetched (no judgment about whether feedback
#      is clean — caller decides what to do with it).
#   2  Bad usage (missing --title).
#   3  Could not parse PR number from `gh pr create` output.
#   *  Whatever `gh pr create` or the comment-fetch step returns.

usage() {
    echo "Usage: create-pr-and-wait.sh --title TITLE [--body-file PATH | < stdin] [--wait SECS] [gh pr create flags...]" >&2
    exit 2
}

require_value() {
    if [[ $# -lt 2 ]]; then
        echo "Missing value for $1" >&2
        usage
    fi
}

WAIT_SECS=180
TITLE=""
BODY_FILE=""
PASSTHROUGH=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --title)      require_value "$@"; TITLE="$2"; shift 2 ;;
        --body-file)  require_value "$@"; BODY_FILE="$2"; shift 2 ;;
        --wait)       require_value "$@"; WAIT_SECS="$2"; shift 2 ;;
        -h|--help)    usage ;;
        *) PASSTHROUGH+=("$1"); shift ;;
    esac
done

if [[ -z "$TITLE" ]]; then
    usage
fi

if [[ -n "$BODY_FILE" ]]; then
    BODY=$(cat "$BODY_FILE")
else
    BODY=$(cat)
fi

# Step 1: create the PR
PR_URL=$(gh pr create --title "$TITLE" --body "$BODY" "${PASSTHROUGH[@]}")
echo "Created: $PR_URL"

PR_NUM=$(echo "$PR_URL" | grep -oE '[0-9]+$' || true)
if [[ -z "$PR_NUM" ]]; then
    echo "Could not parse PR number from gh pr create output: $PR_URL" >&2
    exit 3
fi

# Step 2: wait for reviewers
echo "Waiting ${WAIT_SECS}s for automated reviewers (qodo, copilot, sonarcloud)..."
sleep "$WAIT_SECS"

# Step 3: dump feedback
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_COMMENTS="$SCRIPT_DIR/pr-comments.sh"
GLOBAL_COMMENTS="$HOME/.claude/skills/pr-review/scripts/pr-comments.sh"

if [[ -x "$PROJECT_COMMENTS" ]]; then
    bash "$PROJECT_COMMENTS" "$PR_NUM"
elif [[ -x "$GLOBAL_COMMENTS" ]]; then
    bash "$GLOBAL_COMMENTS" "$PR_NUM"
else
    # Fallback: inline gh api dump.
    echo "Note: pr-comments.sh not found at $PROJECT_COMMENTS or $GLOBAL_COMMENTS — using inline gh api fallback." >&2
    REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
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
