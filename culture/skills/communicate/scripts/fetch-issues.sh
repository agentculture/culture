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
    *-*)  # range like 191-197
      IFS='-' read -r start end <<< "$1"
      for ((i=start; i<=end; i++)); do NUMBERS+=("$i"); done
      shift ;;
    *)  NUMBERS+=("$1"); shift ;;
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
