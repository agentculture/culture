#!/usr/bin/env bash
# Fetch GitHub issues with full body and comments. Thin wrapper around
# `agtag issue fetch` that keeps this skill's range/list expansion
# (agtag is single-issue per call).
#
# Usage: fetch-issues.sh [RANGE|NUMBER...] [--repo OWNER/REPO]
#   fetch-issues.sh 191-197                   # range
#   fetch-issues.sh 191                       # single
#   fetch-issues.sh 191 192 195               # list
#   fetch-issues.sh --repo foo/bar 5          # explicit repo (otherwise gh resolves it from the git remote)

set -euo pipefail

REPO=""
NUMBERS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      if [[ $# -lt 2 || -z "$2" ]]; then
        echo "Error: --repo requires a value (OWNER/REPO)" >&2
        echo "Usage: fetch-issues.sh [RANGE|NUMBER...] [--repo OWNER/REPO]" >&2
        exit 1
      fi
      REPO="$2"
      shift 2 ;;
    # culture-divergence: gate the range branch on a strict
    # `^[0-9]+-[0-9]+$` regex with start<=end and non-empty parts.
    # Upstream (steward 0.11.1) treats any arg containing `-` as a range
    # and feeds `start`/`end` directly into bash arithmetic — inputs
    # like `191-` or `abc-def` crash the whole script under
    # `set -euo pipefail`. Pinned by PR #380 review (Qodo).
    *-*)
      if [[ "$1" =~ ^([0-9]+)-([0-9]+)$ ]]; then
        start="${BASH_REMATCH[1]}"
        end="${BASH_REMATCH[2]}"
        if (( start > end )); then
          echo "Error: range start ($start) > end ($end) in '$1'" >&2
          exit 1
        fi
        for ((i=start; i<=end; i++)); do NUMBERS+=("$i"); done
      else
        echo "Error: malformed range '$1' (expected <start>-<end> with non-empty integers)" >&2
        exit 1
      fi
      shift ;;
    [0-9]*)  NUMBERS+=("$1"); shift ;;
    *)
      echo "Error: unrecognized argument '$1' (expected NUMBER, NUMBER-NUMBER, or --repo OWNER/REPO)" >&2
      exit 1 ;;
  esac
done

if [[ ${#NUMBERS[@]} -eq 0 ]]; then
  echo "Usage: fetch-issues.sh [RANGE|NUMBER...] [--repo OWNER/REPO]" >&2
  exit 1
fi

if ! command -v agtag >/dev/null 2>&1; then
  echo "agtag not found on PATH. Install agtag (>=0.1) to use this skill." >&2
  exit 2
fi

# agtag fetch resolves the repo from the local git remote when --repo
# is omitted, matching the previous gh-based behavior.
REPO_ARGS=()
if [[ -n "$REPO" ]]; then
  REPO_ARGS=(--repo "$REPO")
fi

for num in "${NUMBERS[@]}"; do
  echo "========================================"
  echo "ISSUE #${num}"
  echo "========================================"
  agtag issue fetch "${REPO_ARGS[@]}" --number "$num" --json \
    || echo "ERROR: Could not fetch issue #${num}"
  echo
done
