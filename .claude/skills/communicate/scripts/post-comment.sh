#!/usr/bin/env bash
set -euo pipefail

# Comment on an existing cross-repo issue. Thin wrapper around
# `agtag issue reply` that mirrors `post-issue.sh`'s ergonomics.
#
# Signature: agtag resolves the signing nick from the local
# `culture.yaml` (falling back to repo basename), so vendors do not
# need to edit a literal here.
#
# Usage:
#   post-comment.sh --repo OWNER/REPO --number N --body-file PATH
#   post-comment.sh --repo OWNER/REPO --number N  < body-on-stdin

usage() {
    echo "Usage: post-comment.sh --repo OWNER/REPO --number N [--body-file PATH | < stdin]" >&2
    exit 2
}

REPO=""
NUMBER=""
BODY_FILE=""

require_value() {
    if [[ $# -lt 2 ]]; then
        echo "Missing value for $1" >&2
        usage
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo)       require_value "$@"; REPO="$2"; shift 2 ;;
        --number)     require_value "$@"; NUMBER="$2"; shift 2 ;;
        --body-file)  require_value "$@"; BODY_FILE="$2"; shift 2 ;;
        -h|--help)    usage ;;
        *) echo "Unknown flag: $1" >&2; usage ;;
    esac
done

if [[ -z "$REPO" || -z "$NUMBER" ]]; then
    usage
fi

if ! command -v agtag >/dev/null 2>&1; then
    echo "agtag not found on PATH. Install agtag (>=0.1) to use this skill." >&2
    exit 2
fi

if [[ -z "$BODY_FILE" ]]; then
    if [[ -t 0 ]]; then
        echo "No --body-file given and stdin is a TTY — refusing to hang on cat." >&2
        echo "Pass --body-file PATH or pipe the body in." >&2
        exit 2
    fi
    TMP_BODY=$(mktemp -t communicate-post-comment-body.XXXXXX)
    trap 'rm -f "$TMP_BODY"' EXIT
    cat > "$TMP_BODY"
    BODY_FILE="$TMP_BODY"
fi

# Don't `exec` here: see post-issue.sh for the rationale (stdin-spooled
# tempfile + EXIT trap; exec would skip the trap and leak the body).
agtag issue reply --repo "$REPO" --number "$NUMBER" --body-file "$BODY_FILE"
exit $?
