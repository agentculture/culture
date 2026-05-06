#!/usr/bin/env bash
set -euo pipefail

# Create a PR, wait for automated reviewers (qodo, copilot, sonarcloud) to
# post comments, then dump the feedback. Wraps the manual `gh pr create →
# sleep 180 → pr-comments.sh` sequence into one invocation.
#
# culture-divergence: this script adds an opt-in `--push` flag (issue #318)
# that runs `git push -u origin HEAD` before `gh pr create` so callers don't
# have to remember the separate push step. Steward's upstream copy does not
# carry this flag; if you re-cite from steward, re-apply the --push patch.
#
# Usage:
#   create-pr-and-wait.sh [--push] --title "Title" --body-file PATH [--wait SECS] [extra gh pr create flags...]
#   create-pr-and-wait.sh [--push] --title "Title" [--wait SECS] [extra gh pr create flags...] < body-on-stdin
#
# Flags:
#   --title TITLE     PR title (required)
#   --body-file PATH  Read the body from a file. If omitted, reads stdin.
#   --wait SECS       How long to sleep after `gh pr create` before fetching
#                     comments. Default: 180 (3 min). qodo/copilot/sonarcloud
#                     usually post within this window. Use `wait-and-check.sh`
#                     to extend by another 3 min if reviewers haven't finished.
#   --push            Run `git push -u origin <current-branch>` before
#                     `gh pr create`. Off by default (back-compat). Recommended
#                     so the whole push+create+wait flow is one invocation.
#   Any other flags pass through to `gh pr create` (e.g. --base, --reviewer).
#
# Behavior:
#   1. (Optional, --push) `git push -u origin <current-branch>` — refuses
#      to run on a detached HEAD.
#   2. Stage the body to a tempfile and `gh pr create --body-file …` so
#      large self-contained briefs don't hit the OS argv length limit.
#   3. `sleep $WAIT_SECS` to give reviewers time to post.
#   4. Run the sibling `pr-comments.sh <PR_NUMBER>` (vendored next to this
#      script) to dump inline + issue + review comments.
#
# Exit codes:
#   0  PR created and feedback fetched (no judgment about whether feedback
#      is clean — caller decides what to do with it).
#   2  Bad usage (missing --title, or no body source on a TTY, or --push on
#      a detached HEAD).
#   3  Could not parse PR number from `gh pr create` output.
#   *  Whatever `git push`, `gh pr create`, or the comment-fetch step returns.

usage() {
    # Exit code: 0 when invoked via --help (informational), 2 when the
    # caller passed bad/missing args (real usage error).
    local rc="${1:-2}"
    echo "Usage: create-pr-and-wait.sh [--push] --title TITLE [--body-file PATH | < stdin] [--wait SECS] [gh pr create flags...]" >&2
    exit "$rc"
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
PUSH=false
PASSTHROUGH=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --title)      require_value "$@"; TITLE="$2"; shift 2 ;;
        --body-file)  require_value "$@"; BODY_FILE="$2"; shift 2 ;;
        --wait)       require_value "$@"; WAIT_SECS="$2"; shift 2 ;;
        --push)       PUSH=true; shift ;;
        -h|--help)    usage 0 ;;
        *) PASSTHROUGH+=("$1"); shift ;;
    esac
done

if [[ -z "$TITLE" ]]; then
    usage
fi

# Step 0 (optional): push the current branch. Refuse on detached HEAD —
# `git push -u origin HEAD` on a detached HEAD does not set up a tracking
# branch and produces a confusing remote name.
if [[ "$PUSH" == true ]]; then
    BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null || true)
    if [[ -z "$BRANCH" ]]; then
        echo "--push requires a non-detached HEAD." >&2
        exit 2
    fi
    echo "Pushing $BRANCH to origin..."
    git push -u origin "$BRANCH"
fi

# Stage the body to a tempfile and pass `--body-file` to gh. Large
# self-contained briefs can otherwise hit the OS argv length limit
# (~128 KB) when passed via `--body "$BODY"`.
TMP_BODY=$(mktemp -t cicd-create-pr-body.XXXXXX)
trap 'rm -f "$TMP_BODY"' EXIT

if [[ -n "$BODY_FILE" ]]; then
    cat "$BODY_FILE" > "$TMP_BODY"
elif [[ ! -t 0 ]]; then
    cat > "$TMP_BODY"
else
    echo "No --body-file given and stdin is a TTY — refusing to hang on cat." >&2
    echo "Pass --body-file PATH or pipe the body in." >&2
    exit 2
fi

# Step 1: create the PR
PR_URL=$(gh pr create --title "$TITLE" --body-file "$TMP_BODY" "${PASSTHROUGH[@]}")
echo "Created: $PR_URL"

PR_NUM=$(echo "$PR_URL" | grep -oE '[0-9]+$' || true)
if [[ -z "$PR_NUM" ]]; then
    echo "Could not parse PR number from gh pr create output: $PR_URL" >&2
    exit 3
fi

# Step 2: wait for reviewers
echo "Waiting ${WAIT_SECS}s for automated reviewers (qodo, copilot, sonarcloud)..."
sleep "$WAIT_SECS"

# Step 3: dump feedback. The cicd skill always vendors pr-comments.sh
# next to this script — no per-user $HOME fallback (would violate the
# skills-portability rule).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$SCRIPT_DIR/pr-comments.sh" "$PR_NUM"
