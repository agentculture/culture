#!/usr/bin/env bash
set -euo pipefail

# Batch reply to PR review comments from JSONL on stdin.
# Each line: {"comment_id": 123, "body": "reply text"}
# Usage: pr-batch.sh [--repo OWNER/REPO] [--resolve] PR_NUMBER < input.jsonl

REPO=""
RESOLVE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo) REPO="$2"; shift 2 ;;
        --resolve) RESOLVE=true; shift ;;
        *) break ;;
    esac
done

PR_NUMBER="${1:?Usage: pr-batch.sh [--repo OWNER/REPO] [--resolve] PR_NUMBER < input.jsonl}"

if [[ -z "$REPO" ]]; then
    REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESOLVE_FLAG=""
if [[ "$RESOLVE" == true ]]; then
    RESOLVE_FLAG="--resolve"
fi

SUCCESS=0
FAIL=0

while IFS= read -r line; do
    # Skip empty lines
    [[ -z "$line" ]] && continue

    COMMENT_ID=$(echo "$line" | jq -r '.comment_id')
    BODY=$(echo "$line" | jq -r '.body')

    if [[ "$COMMENT_ID" == "null" || "$BODY" == "null" ]]; then
        echo "SKIP: invalid line: $line"
        ((FAIL++)) || true
        continue
    fi

    echo "--- Comment $COMMENT_ID ---"
    if bash "$SCRIPT_DIR/pr-reply.sh" --repo "$REPO" $RESOLVE_FLAG "$PR_NUMBER" "$COMMENT_ID" "$BODY"; then
        ((SUCCESS++)) || true
    else
        echo "FAILED: comment $COMMENT_ID"
        ((FAIL++)) || true
    fi
done

echo ""
echo "Done: $SUCCESS succeeded, $FAIL failed"
