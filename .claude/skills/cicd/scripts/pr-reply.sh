#!/usr/bin/env bash
set -euo pipefail

# Reply to a PR review comment, optionally resolve its thread.
# Usage: pr-reply.sh [--repo OWNER/REPO] [--resolve] PR_NUMBER COMMENT_ID "body"
#
# culture-divergence: steward's upstream copy resolves the agent's nick
# from culture.yaml via _resolve-nick.sh and signs as "- <nick> (Claude)".
# Culture signs replies as plain "- Claude" per cicd/SKILL.md Step 8 and
# the user's global CLAUDE.md convention. If you re-cite from steward,
# re-apply this divergence (drop _resolve-nick.sh; hard-code SIG="- Claude").

REPO=""
RESOLVE=false
PRINT_BODY=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo) REPO="$2"; shift 2 ;;
        --resolve) RESOLVE=true; shift ;;
        --print-body) PRINT_BODY=true; shift ;;
        *) break ;;
    esac
done

PR_NUMBER="${1:?Usage: pr-reply.sh [--repo OWNER/REPO] [--resolve] [--print-body] PR_NUMBER COMMENT_ID \"body\"}"
COMMENT_ID="${2:?Missing COMMENT_ID}"
BODY="${3:?Missing reply body}"

if [[ "$PRINT_BODY" != true && -z "$REPO" ]]; then
    REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
fi

# Sign with culture's standard signature (hard-coded; see header comment).
SIG="- Claude"
if ! printf '%s' "$BODY" | grep -qFx -- "$SIG"; then
    BODY="${BODY}

${SIG}"
fi

if [[ "$PRINT_BODY" == true ]]; then
    printf '%s\n' "$BODY"
    exit 0
fi

# Post reply
REPLY_URL=$(gh api "repos/$REPO/pulls/$PR_NUMBER/comments/$COMMENT_ID/replies" \
    -f body="$BODY" \
    --jq '.html_url')
echo "Replied: $REPLY_URL"

# Resolve thread if requested
if [[ "$RESOLVE" == true ]]; then
    # Find the thread ID for this comment
    THREAD_ID=$(gh api graphql -f query="
    {
      repository(owner: \"${REPO%%/*}\", name: \"${REPO##*/}\") {
        pullRequest(number: $PR_NUMBER) {
          reviewThreads(first: 100) {
            nodes {
              id
              comments(first: 100) {
                nodes { databaseId }
              }
            }
          }
        }
      }
    }" --jq ".data.repository.pullRequest.reviewThreads.nodes[] | select(any(.comments.nodes[]; .databaseId == $COMMENT_ID)) | .id")

    if [[ -n "$THREAD_ID" ]]; then
        RESOLVED=$(gh api graphql -f query="
          mutation { resolveReviewThread(input: {threadId: \"$THREAD_ID\"}) { thread { isResolved } } }
        " --jq '.data.resolveReviewThread.thread.isResolved')
        echo "Resolved: $RESOLVED (thread $THREAD_ID)"
    else
        echo "Warning: could not find thread for comment $COMMENT_ID"
    fi
fi
