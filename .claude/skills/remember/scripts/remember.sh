#!/usr/bin/env bash
# remember.sh — ingest records into the shared eidetic memory store (the /remember skill).
#
# Thin, portable wrapper around `eidetic remember`. It resolves the CLI, points
# the embedding endpoint at the local model-gear embed gear (overridable), and
# forwards every argument verbatim. Accepts ONE record as a JSON object argument,
# or a BATCH as NDJSON on stdin (one JSON object per line) for bulk ingest.
#
#   remember.sh '{"id":"d1","text":"...","type":"docs","metadata":{...}}' --json
#   cat records.ndjson | remember.sh --json
#
# Upsert is idempotent by id (and dedups by content hash): re-remembering the
# same record updates it in place, never duplicates.
#
# The store is the files backend at ~/.eidetic/memory by default — a home-dir
# path OUTSIDE any git worktree, so a record Claude remembers is recallable by
# the colleague backend (which runs in throwaway worktrees), and vice versa.
# Set EIDETIC_DATA_DIR to opt out of sharing; use --backend mongo|neo4j (with
# EIDETIC_MONGO_URI / NEO4J_URI) for a server-backed shared store.

set -euo pipefail

# ── resolve the eidetic CLI (installed tool first, then dev checkout) ────────
EIDETIC=()
resolve_eidetic() {
    if command -v eidetic >/dev/null 2>&1; then
        EIDETIC=(eidetic)
        return 0
    fi
    local dir
    dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
    while [ -n "$dir" ] && [ "$dir" != "/" ]; do
        if [ -f "$dir/pyproject.toml" ] \
            && grep -q '^name = "eidetic-cli"' "$dir/pyproject.toml" 2>/dev/null; then
            if command -v uv >/dev/null 2>&1; then
                EIDETIC=(uv run --project "$dir" eidetic)
                return 0
            fi
            break
        fi
        dir=$(dirname "$dir")
    done
    cat >&2 <<'EOF'
error: eidetic CLI not found.
hint: install it with `uv tool install eidetic-cli` (or `pipx install eidetic-cli`),
      or run from inside the eidetic-cli checkout with `uv` available.
      The console script is `eidetic` (dist name: eidetic-cli).
EOF
    return 1
}

usage() {
    cat <<'EOF'
remember.sh — ingest records into the shared eidetic memory store (the /remember skill).

Usage:
  remember.sh '<json-object>' [--json] [--backend files|mongo|neo4j] \
              [--scope NAME] [--visibility public|private]
  cat records.ndjson | remember.sh [--json] ...

A record needs `id`, `text`, and `type`; `hash` and `metadata` are recommended
(hash is derived from text when omitted). Upsert is idempotent by id.
Public data only. Every flag is forwarded verbatim to `eidetic remember`.
See `eidetic explain remember`.
EOF
}

case "${1:-}" in
    -h | --help | help)
        usage
        exit 0
        ;;
esac

resolve_eidetic || exit 2

# ── default to this agent's PERSONAL, PRIVATE scope (culture.yaml `suffix`) ──
# A record this agent remembers should land in its OWN personal scope, not the
# global `default` scope shared by every project on this host. We read the
# `suffix` from the nearest culture.yaml (walking up from this script), so the
# scope follows the repo identity rather than being hard-coded — a downstream
# cite-don't-import copy adapts to its own suffix, and the colleague backend
# (running in a worktree of this same repo) resolves the same suffix, keeping
# the Claude↔colleague shared-memory story intact.
#
# The personal scope is PRIVATE by default: in eidetic's model only a private
# record is isolated to its scope (`can_serve`), so private is what actually
# keeps these records from leaking to a default/other-scope recall. Scope and
# visibility are paired — the private default applies only when we inject the
# resolved scope, and only if the caller didn't pass --visibility (so an
# explicit `--visibility public` still wins). An explicit --scope on the command
# line takes over steering entirely; a wheel install with no culture.yaml falls
# back to the plain CLI default (`default`/`public`).
resolve_scope() {
    local dir suffix=""
    dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
    while [ -n "$dir" ] && [ "$dir" != "/" ]; do
        if [ -f "$dir/culture.yaml" ]; then
            # Capture only the first non-space token after `suffix:` (so an
            # inline `# comment` or trailing space can't bleed into the scope),
            # then strip surrounding quotes only — matching the canonical parser
            # in .claude/skills/cicd/scripts/_resolve-nick.sh.
            suffix=$(sed -n \
                's/^[[:space:]]*-\{0,1\}[[:space:]]*suffix:[[:space:]]*\([^[:space:]]*\).*/\1/p' \
                "$dir/culture.yaml" | head -n1 | tr -d "\"'")
            break
        fi
        dir=$(dirname "$dir")
    done
    printf '%s' "$suffix"
}

has_flag() {
    local needle=$1
    shift
    local a
    for a in "$@"; do
        case "$a" in
            "$needle" | "$needle"=*) return 0 ;;
        esac
    done
    return 1
}

SCOPE_ARGS=()
if ! has_flag --scope "$@"; then
    EIDETIC_SCOPE=$(resolve_scope)
    if [ -n "$EIDETIC_SCOPE" ]; then
        SCOPE_ARGS+=(--scope "$EIDETIC_SCOPE")
        has_flag --visibility "$@" || SCOPE_ARGS+=(--visibility private)
    fi
fi

: "${EIDETIC_EMBED_URL:=http://localhost:8002/v1}"
: "${EIDETIC_EMBED_MODEL:=Qwen/Qwen3-Embedding-0.6B}"
export EIDETIC_EMBED_URL EIDETIC_EMBED_MODEL

exec "${EIDETIC[@]}" remember "${SCOPE_ARGS[@]}" "$@"
