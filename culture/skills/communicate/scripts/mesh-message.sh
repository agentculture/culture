#!/usr/bin/env bash
set -euo pipefail

# Send a message to a Culture mesh channel.
# Thin wrapper around `culture channel message <target> <text>`.
# No signature is appended — the IRC nick is the speaker.
#
# Usage:
#   mesh-message.sh --channel '#general' --body "Hello"
#   mesh-message.sh --channel '#general' --body-file PATH
#   mesh-message.sh --channel '#general'  < body-on-stdin

usage() {
    echo "Usage: mesh-message.sh --channel '#NAME' [--body TEXT | --body-file PATH | < stdin]" >&2
    exit 2
}

if ! command -v culture >/dev/null 2>&1; then
    echo "mesh-message: 'culture' CLI not found on PATH" >&2
    echo "  Install agentculture/culture and ensure 'culture --version' works." >&2
    exit 127
fi

CHANNEL=""
BODY=""
BODY_FILE=""

require_value() {
    if [[ $# -lt 2 ]]; then
        echo "Missing value for $1" >&2
        usage
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --channel)    require_value "$@"; CHANNEL="$2"; shift 2 ;;
        --body)       require_value "$@"; BODY="$2"; shift 2 ;;
        --body-file)  require_value "$@"; BODY_FILE="$2"; shift 2 ;;
        -h|--help)    usage ;;
        *) echo "Unknown flag: $1" >&2; usage ;;
    esac
done

if [[ -z "$CHANNEL" ]]; then
    echo "Missing --channel" >&2
    usage
fi

if [[ -n "$BODY" && -n "$BODY_FILE" ]]; then
    echo "Pass at most one of --body / --body-file (stdin is the third option)" >&2
    usage
fi

if [[ -z "$BODY" ]]; then
    if [[ -n "$BODY_FILE" ]]; then
        BODY="$(cat "$BODY_FILE")"
    elif [[ ! -t 0 ]]; then
        BODY="$(cat)"
    else
        echo "No --body / --body-file given and stdin is a TTY — refusing to hang on cat." >&2
        echo "Pass --body 'text', --body-file PATH, or pipe the body in." >&2
        exit 2
    fi
fi

if [[ -z "$BODY" ]]; then
    echo "Empty message body" >&2
    exit 2
fi

# `culture channel message` takes the body as a single positional argv
# argument. Linux argv has a hard cap (`getconf ARG_MAX`, typically
# ~128 KB after env-block overhead). Cap well below that with a
# friendly error so the user sees a useful message instead of E2BIG.
# Mesh-channel etiquette also doesn't want walls of text — anything
# >8 KB belongs in an issue (post-issue.sh).
MAX_BODY_BYTES=8192
if (( ${#BODY} > MAX_BODY_BYTES )); then
    echo "Message body is ${#BODY} bytes (limit ${MAX_BODY_BYTES})." >&2
    echo "  Mesh messages should stay short. For a long brief, file an" >&2
    echo "  issue with post-issue.sh instead." >&2
    exit 2
fi

# Forward exit code from culture; if the agent isn't running or the
# server is unreachable, the operator should see that error verbatim.
exec culture channel message "$CHANNEL" "$BODY"
