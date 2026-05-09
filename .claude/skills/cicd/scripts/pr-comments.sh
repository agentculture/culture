#!/usr/bin/env bash
set -euo pipefail

# Fetch and display all PR feedback in one pass:
#   1. Inline review comments (with thread resolve status)
#   2. Issue comments (qodo summaries, sonarcloud, etc.)
#   3. Top-level reviews with a non-empty body (copilot overview, etc.)
#   4. SonarCloud new issues (silently skipped if project isn't on SonarCloud).
#      Project key is derived as `<owner>_<repo>`; override with
#      SONAR_PROJECT_KEY=<key> for non-standard naming.
#
# culture-divergence: Section 4 hardening (PR #359 review, 10.5.1)
#   - distinguish curl errors from missing-issues field (don't relabel a
#     network/rate-limit failure as "project not registered")
#   - URL-encode the project key before splicing into the API URL
#   - raise ps to 500 and warn when the response says more issues exist
#   When re-citing this script from steward, re-apply these three patches
#   in Section 4 unless steward has merged the same hardening upstream.
#
# Usage: pr-comments.sh [--repo OWNER/REPO] PR_NUMBER

REPO=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo) REPO="$2"; shift 2 ;;
        *) break ;;
    esac
done

PR_NUMBER="${1:?Usage: pr-comments.sh [--repo OWNER/REPO] PR_NUMBER}"

if [[ -z "$REPO" ]]; then
    REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
fi

# ── Section 1: inline review comments ─────────────────────────────────────
THREADS_JSON=$(gh api graphql -f query="
{
  repository(owner: \"${REPO%%/*}\", name: \"${REPO##*/}\") {
    pullRequest(number: $PR_NUMBER) {
      reviewThreads(first: 100) {
        nodes {
          id
          isResolved
          comments(first: 100) {
            nodes { databaseId }
          }
        }
      }
    }
  }
}" --jq '.data.repository.pullRequest.reviewThreads.nodes')

# Build a map from every comment ID in every thread → its thread metadata,
# so replies in a thread also show resolved status (not just the first comment).
THREAD_MAP=$(echo "$THREADS_JSON" | jq -r '
  [.[] as $t | $t.comments.nodes[] | {
    comment_id: .databaseId,
    thread_id: $t.id,
    resolved: $t.isResolved
  }]
')

INLINE=$(gh api "repos/$REPO/pulls/$PR_NUMBER/comments" --paginate)
INLINE_COUNT=$(echo "$INLINE" | jq 'length')

echo "════════════════ INLINE REVIEW COMMENTS ($INLINE_COUNT) ════════════════"
echo "$INLINE" | jq -r --argjson threads "$THREAD_MAP" '
  .[] | . as $c |
  ($threads | map(select(.comment_id == $c.id)) | first // {resolved: "unknown", thread_id: "?"}) as $t |
  "──────────────────────────────────────────────────",
  "ID: \($c.id)  |  Thread: \(if $t.resolved == true then "RESOLVED" elif $t.resolved == false then "UNRESOLVED" else "?" end)  |  Reply-to: \($c.in_reply_to_id // "none")",
  "File: \($c.path):\($c.original_line // $c.line // "?")",
  "Thread ID: \($t.thread_id)",
  "Author: \($c.user.login)",
  "",
  ($c.body | split("\n") | if length > 10 then .[:10] + ["... (truncated)"] else . end | join("\n")),
  ""
'

# ── Section 2: issue comments (general PR comments) ───────────────────────
ISSUE=$(gh api "repos/$REPO/issues/$PR_NUMBER/comments" --paginate)
ISSUE_COUNT=$(echo "$ISSUE" | jq 'length')

echo ""
echo "════════════════ ISSUE COMMENTS ($ISSUE_COUNT) ════════════════"
echo "$ISSUE" | jq -r '
  .[] |
  "──────────────────────────────────────────────────",
  "ID: \(.id)  |  Author: \(.user.login)  |  Created: \(.created_at)",
  "",
  (.body | split("\n") | if length > 10 then .[:10] + ["... (truncated)"] else . end | join("\n")),
  ""
'

# ── Section 3: top-level reviews with a body ──────────────────────────────
REVIEWS=$(gh api "repos/$REPO/pulls/$PR_NUMBER/reviews" --paginate)
REVIEWS_WITH_BODY=$(echo "$REVIEWS" | jq '[.[] | select((.body // "") != "")]')
REVIEW_COUNT=$(echo "$REVIEWS_WITH_BODY" | jq 'length')

echo ""
echo "════════════════ TOP-LEVEL REVIEWS ($REVIEW_COUNT) ════════════════"
echo "$REVIEWS_WITH_BODY" | jq -r '
  .[] |
  "──────────────────────────────────────────────────",
  "Review ID: \(.id)  |  Author: \(.user.login)  |  State: \(.state)  |  Submitted: \(.submitted_at)",
  "",
  (.body | split("\n") | if length > 10 then .[:10] + ["... (truncated)"] else . end | join("\n")),
  ""
'

# ── Section 4: SonarCloud new issues ──────────────────────────────────────
# Public API; no auth needed for public projects. Project key defaults to
# the GitHub `<owner>_<repo>` convention; override with SONAR_PROJECT_KEY.
SONAR_KEY="${SONAR_PROJECT_KEY:-${REPO%%/*}_${REPO##*/}}"
SONAR_KEY_URI=$(jq -nr --arg v "$SONAR_KEY" '$v|@uri')
SONAR_PS=500

# Capture curl exit code separately so we can distinguish a transport
# failure from a successful "no issues" / "project not found" response.
SONAR_RAW=$(curl -fsS "https://sonarcloud.io/api/issues/search?componentKeys=${SONAR_KEY_URI}&pullRequest=${PR_NUMBER}&ps=${SONAR_PS}" 2>/dev/null) && SONAR_CURL_OK=1 || SONAR_CURL_OK=0

echo ""
if [[ "$SONAR_CURL_OK" -ne 1 ]]; then
    echo "════════════════ SONARCLOUD NEW ISSUES ════════════════"
    echo "(curl failed contacting sonarcloud.io — section skipped; check network/rate-limit/API status)"
elif echo "$SONAR_RAW" | jq -e 'has("issues")' >/dev/null 2>&1; then
    SONAR_COUNT=$(echo "$SONAR_RAW" | jq '.issues | length')
    SONAR_TOTAL=$(echo "$SONAR_RAW" | jq '.paging.total // .total // (.issues | length)')
    echo "════════════════ SONARCLOUD NEW ISSUES ($SONAR_COUNT) ════════════════"
    if [[ "$SONAR_COUNT" -gt 0 ]]; then
        echo "$SONAR_RAW" | jq -r '
          .issues[] |
          "──────────────────────────────────────────────────",
          "[\(.severity)] [\(.rule)] \(.component | sub("^[^:]+:"; "")):\(.line // "?")",
          (.message | if length > 200 then .[:200] + "…" else . end),
          ""
        '
    fi
    if [[ "$SONAR_TOTAL" -gt "$SONAR_COUNT" ]]; then
        echo "(warning: SonarCloud reports ${SONAR_TOTAL} issues but only ${SONAR_COUNT} fetched. Re-run with a higher ps or narrow by status.)"
    fi
else
    echo "════════════════ SONARCLOUD NEW ISSUES ════════════════"
    echo "(project key '${SONAR_KEY}' not registered on sonarcloud.io — section skipped)"
fi
