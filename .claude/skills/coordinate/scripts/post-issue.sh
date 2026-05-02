#!/usr/bin/env bash
set -euo pipefail

# Post a cross-repo issue with auto-signature `- culture (Claude)`.
#
# Usage:
#   post-issue.sh --repo OWNER/REPO --title "Title" --body-file PATH
#   post-issue.sh --repo OWNER/REPO --title "Title"  < body-on-stdin

usage() {
    echo "Usage: post-issue.sh --repo OWNER/REPO --title TITLE [--body-file PATH | < stdin]" >&2
    exit 2
}

# `gh issue create --body "$LONG_STRING"` can hit the OS argv length limit
# (typically ~128 KB) for the long self-contained briefs this skill is meant
# to post. Stage the body to a tempfile and pass `--body-file` instead.
TMP_BODY=$(mktemp -t coordinate-post-issue-body.XXXXXX)
trap 'rm -f "$TMP_BODY"' EXIT

REPO=""
TITLE=""
BODY_FILE=""

# Require a value to follow each flag (otherwise `--repo` with no argument
# would crash on `$2` under `set -u` instead of printing usage).
require_value() {
    if [[ $# -lt 2 ]]; then
        echo "Missing value for $1" >&2
        usage
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo)       require_value "$@"; REPO="$2"; shift 2 ;;
        --title)      require_value "$@"; TITLE="$2"; shift 2 ;;
        --body-file)  require_value "$@"; BODY_FILE="$2"; shift 2 ;;
        -h|--help)    usage ;;
        *) echo "Unknown flag: $1" >&2; usage ;;
    esac
done

if [[ -z "$REPO" || -z "$TITLE" ]]; then
    usage
fi

if [[ -n "$BODY_FILE" ]]; then
    cat "$BODY_FILE" > "$TMP_BODY"
else
    cat > "$TMP_BODY"
fi

# Append the signature.
printf '\n\n- culture (Claude)\n' >> "$TMP_BODY"

gh issue create --repo "$REPO" --title "$TITLE" --body-file "$TMP_BODY"
