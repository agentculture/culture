#!/usr/bin/env bash
set -euo pipefail

# Post a cross-repo issue. Thin wrapper around `agtag issue post` that
# preserves this skill's stable script path so existing callers
# (e.g. steward-cli's `announce-skill-update`) keep working unchanged.
#
# Signature: agtag resolves the signing nick from the local
# `culture.yaml` (falling back to repo basename), so vendors do not
# need to edit a literal here.
#
# Usage:
#   post-issue.sh --repo OWNER/REPO --title "Title" --body-file PATH
#   post-issue.sh --repo OWNER/REPO --title "Title"  < body-on-stdin

usage() {
    echo "Usage: post-issue.sh --repo OWNER/REPO --title TITLE [--body-file PATH | < stdin]" >&2
    exit 2
}

REPO=""
TITLE=""
BODY_FILE=""

# Require a value to follow each flag (otherwise `--repo` with no argument
# would crash on `$2` under `set -u` instead of printing usage).
require_value() {
    local flag="$1"
    if [[ $# -lt 2 ]]; then
        echo "Missing value for $flag" >&2
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

if ! command -v agtag >/dev/null 2>&1; then
    echo "agtag not found on PATH. Install agtag (>=0.1) to use this skill." >&2
    exit 2
fi

# agtag has no stdin mode — spool body to a tempfile so both call shapes
# (--body-file and stdin) reach agtag as --body-file.
if [[ -z "$BODY_FILE" ]]; then
    if [[ -t 0 ]]; then
        echo "No --body-file given and stdin is a TTY — refusing to hang on cat." >&2
        echo "Pass --body-file PATH or pipe the body in." >&2
        exit 2
    fi
    TMP_BODY=$(mktemp -t communicate-post-issue-body.XXXXXX)
    trap 'rm -f "$TMP_BODY"' EXIT
    cat > "$TMP_BODY"
    BODY_FILE="$TMP_BODY"
fi

# Don't `exec` here: when stdin spooled the body to a tempfile we set an
# EXIT trap to delete it, and exec would replace the shell before the trap
# could run, leaking the spooled body on disk.
agtag issue post --repo "$REPO" --title "$TITLE" --body-file "$BODY_FILE"
exit $?
