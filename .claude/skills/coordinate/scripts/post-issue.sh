#!/usr/bin/env bash
set -euo pipefail

# Post a cross-repo issue with auto-signature `- culture (Claude)`.
#
# Usage:
#   post-issue.sh --repo OWNER/REPO --title "Title" --body-file PATH
#   post-issue.sh --repo OWNER/REPO --title "Title"  < body-on-stdin

REPO=""
TITLE=""
BODY_FILE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo) REPO="$2"; shift 2 ;;
        --title) TITLE="$2"; shift 2 ;;
        --body-file) BODY_FILE="$2"; shift 2 ;;
        *) echo "Unknown flag: $1" >&2; exit 2 ;;
    esac
done

if [[ -z "$REPO" || -z "$TITLE" ]]; then
    echo "Usage: post-issue.sh --repo OWNER/REPO --title TITLE [--body-file PATH | < stdin]" >&2
    exit 2
fi

if [[ -n "$BODY_FILE" ]]; then
    BODY=$(cat "$BODY_FILE")
else
    BODY=$(cat)
fi

SIGNED_BODY="${BODY}

- culture (Claude)"

gh issue create --repo "$REPO" --title "$TITLE" --body "$SIGNED_BODY"
