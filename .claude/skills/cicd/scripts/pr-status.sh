#!/usr/bin/env bash
# pr-status.sh — one-shot status overview for a Steward PR.
#
# Combines five things review feedback usually scatters across:
#   1. PR state (open / merged / closed) + branch + author
#   2. CI checks (build / lint / unit / sonarcloud / cf-pages / etc.)
#   3. Review-bot pipeline status (Copilot, qodo, SonarCloud, Cloudflare)
#   4. SonarCloud quality gate + open-issue count
#   5. Inline-thread resolved-vs-unresolved tally
#
# Usage: scripts/pr-status.sh [--repo OWNER/REPO] [--sonar-key KEY] PR_NUMBER
#
# Defaults:
#   --repo           auto-detected via `gh repo view`
#   --sonar-key      derived from repo as `<owner>_<name>` (SonarCloud convention)
#
# Requires: gh, jq, curl, python3.

set -euo pipefail

REPO=""
SONAR_KEY=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo) REPO="$2"; shift 2 ;;
        --sonar-key) SONAR_KEY="$2"; shift 2 ;;
        *) break ;;
    esac
done

PR_NUMBER="${1:?Usage: pr-status.sh [--repo OWNER/REPO] [--sonar-key KEY] PR_NUMBER}"

if [[ -z "$REPO" ]]; then
    REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
fi
# Sonar key precedence: explicit --sonar-key flag > SONAR_PROJECT_KEY env >
# `<owner>_<repo>` derivation. Mirrors pr-comments.sh so SKILL.md's claim
# that the env var works for both scripts is true.
if [[ -z "$SONAR_KEY" ]]; then
    SONAR_KEY="${SONAR_PROJECT_KEY:-${REPO%%/*}_${REPO##*/}}"
fi

# ── 1. PR header ──────────────────────────────────────────────────────────
PR_JSON=$(gh pr view "$PR_NUMBER" --json \
    number,title,state,isDraft,mergedAt,mergedBy,baseRefName,headRefName,author,url)

echo "════════════════════════════════════════════════════════════════════"
echo "$PR_JSON" | jq -r '
    "PR #\(.number) — \(.title)",
    "  \(.url)",
    "  Author:  \(.author.login)",
    "  Branch:  \(.headRefName)  →  \(.baseRefName)",
    "  State:   \(if .state == "MERGED" then "MERGED at \(.mergedAt) by \(.mergedBy.login)" elif .state == "OPEN" and .isDraft then "OPEN (draft)" else .state end)"
'
echo "════════════════════════════════════════════════════════════════════"

# ── 2. CI checks ──────────────────────────────────────────────────────────
echo
echo "── CI checks ─────────────────────────────────────────────────────────"
# `gh pr checks` exits non-zero when checks are still pending/failing.
# We don't care about its exit code here; capture and pretty-print.
CHECKS=$(gh pr checks "$PR_NUMBER" 2>/dev/null || true)
if [[ -z "$CHECKS" ]]; then
    echo "  (no checks reported)"
else
    echo "$CHECKS" | awk -F'\t' '
        {
            name  = $1
            state = $2
            dur   = $3
            sym   = "?"
            if (state == "pass")            sym = "✅"
            else if (state == "fail")       sym = "❌"
            else if (state == "skipping")   sym = "⏭"
            else if (state == "pending"  || state == "queued"   || state == "in_progress") sym = "…"
            printf "  %s %-22s %-10s %s\n", sym, name, state, dur
        }
    '
fi

# ── 3. Review bots & comment pipeline ────────────────────────────────────
echo
echo "── Review pipeline ───────────────────────────────────────────────────"

# Inline-thread tally via GraphQL (resolved vs unresolved).
THREADS_JSON=$(gh api graphql -f query="
{
  repository(owner: \"${REPO%%/*}\", name: \"${REPO##*/}\") {
    pullRequest(number: $PR_NUMBER) {
      reviewThreads(first: 100) {
        nodes { id isResolved comments(first: 1) { nodes { author { login } } } }
      }
    }
  }
}" --jq '.data.repository.pullRequest.reviewThreads.nodes')

INLINE_TOTAL=$(echo "$THREADS_JSON" | jq 'length')
INLINE_RESOLVED=$(echo "$THREADS_JSON" | jq '[.[] | select(.isResolved)] | length')
INLINE_PENDING=$((INLINE_TOTAL - INLINE_RESOLVED))

# Per-bot inline counts.
COPILOT_INLINE=$(echo "$THREADS_JSON" | jq '[.[] | select((.comments.nodes[0].author.login // "") | startswith("Copilot"))] | length')
QODO_INLINE=$(echo "$THREADS_JSON" | jq '[.[] | select((.comments.nodes[0].author.login // "") | startswith("qodo"))] | length')

# Issue-level comments (qodo summary, sonarcloud quality-gate body, cf-pages preview, etc.).
# Skip --paginate to avoid array concatenation; per_page=100 covers typical PRs.
ISSUE=$(gh api "repos/$REPO/issues/$PR_NUMBER/comments?per_page=100")
QODO_ISSUE=$(echo "$ISSUE" | jq '[.[] | select((.user.login // "") | startswith("qodo"))] | length')
SONARQUBE_ISSUE=$(echo "$ISSUE" | jq '[.[] | select((.user.login // "") | startswith("sonarqubecloud"))] | length')
CFPAGES_ISSUE=$(echo "$ISSUE" | jq '[.[] | select((.user.login // "") | test("cloudflare"))] | length')
COPILOT_TOPLEVEL=$(gh api "repos/$REPO/pulls/$PR_NUMBER/reviews?per_page=100" \
    | jq '[.[] | select((.user.login // "") | startswith("copilot")) | select((.body // "") != "")] | length')

# Cloudflare deploy URL hidden in issue-comment bodies (look for pages.dev).
CF_URL=$(echo "$ISSUE" | jq -r '[.[].body // "" | scan("https?://[a-z0-9.-]+\\.pages\\.dev[^\\s)\"<]*")] | first // ""')

printf "  %-12s %s\n"  "Copilot"     "$([[ "$COPILOT_TOPLEVEL" -gt 0 || "$COPILOT_INLINE" -gt 0 ]] && echo "✅ overview×$COPILOT_TOPLEVEL, inline×$COPILOT_INLINE" || echo "— no posts yet")"
printf "  %-12s %s\n"  "qodo"        "$([[ "$QODO_ISSUE" -gt 0 || "$QODO_INLINE" -gt 0 ]] && echo "✅ summary×$QODO_ISSUE, inline×$QODO_INLINE" || echo "— no posts yet")"
printf "  %-12s %s\n"  "Cloudflare"  "$([[ -n "$CF_URL" ]] && echo "✅ $CF_URL" || ([[ "$CFPAGES_ISSUE" -gt 0 ]] && echo "✅ ($CFPAGES_ISSUE comments)" || echo "— no deploy preview"))"

# ── 4. SonarCloud quality gate + open issues ─────────────────────────────
SONAR_QG=$(curl -s "https://sonarcloud.io/api/qualitygates/project_status?projectKey=${SONAR_KEY}&pullRequest=${PR_NUMBER}")
SONAR_QG_STATUS=$(echo "$SONAR_QG" | jq -r '.projectStatus.status // "UNKNOWN"')
SONAR_OPEN=$(curl -s "https://sonarcloud.io/api/issues/search?componentKeys=${SONAR_KEY}&pullRequest=${PR_NUMBER}&statuses=OPEN,CONFIRMED&ps=1" \
    | jq -r '.total // 0')
SONAR_HOTSPOTS=$(curl -s "https://sonarcloud.io/api/hotspots/search?projectKey=${SONAR_KEY}&pullRequest=${PR_NUMBER}&status=TO_REVIEW&ps=1" \
    | jq -r '.paging.total // 0')

case "$SONAR_QG_STATUS" in
    OK)    SONAR_SYM="✅" ;;
    ERROR) SONAR_SYM="❌" ;;
    WARN)  SONAR_SYM="⚠ " ;;
    *)     SONAR_SYM="?" ;;
esac
printf "  %-12s %s Quality Gate %s, %d OPEN issue(s), %d hotspot(s)\n" \
    "SonarCloud" "$SONAR_SYM" "$SONAR_QG_STATUS" "$SONAR_OPEN" "$SONAR_HOTSPOTS"

# When SonarCloud has OPEN issues, list them — saves a follow-up curl.
if [[ "$SONAR_OPEN" != "0" ]]; then
    echo
    echo "  SonarCloud OPEN issues:"
    curl -s "https://sonarcloud.io/api/issues/search?componentKeys=${SONAR_KEY}&pullRequest=${PR_NUMBER}&statuses=OPEN,CONFIRMED&ps=20" \
        | jq -r '.issues[] | "    • [\(.rule)] \(.component | sub("^[^:]+:"; ""))(:\(.line // "?")) (\(.severity)) — \(.message)"'
fi

# ── 5. Tally + summary ────────────────────────────────────────────────────
echo
echo "── Inline threads ────────────────────────────────────────────────────"
printf "  Total: %d   Resolved: %d   Unresolved: %d\n" \
    "$INLINE_TOTAL" "$INLINE_RESOLVED" "$INLINE_PENDING"

if [[ "$INLINE_PENDING" -gt 0 ]]; then
    echo
    echo "  Unresolved threads:"
    echo "$THREADS_JSON" | jq -r '
        .[] | select(.isResolved == false) |
        "    • \(.comments.nodes[0].author.login): thread \(.id)"
    '
fi

echo
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "(For full comment bodies: bash \"$SCRIPT_DIR/pr-comments.sh\" $PR_NUMBER)"
