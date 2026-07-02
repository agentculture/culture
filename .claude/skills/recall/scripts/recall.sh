#!/usr/bin/env bash
# recall.sh — search the shared eidetic memory store (the /recall skill).
#
# Thin, portable wrapper around `eidetic recall`. It resolves the CLI, points
# the embedding modes at the local model-gear embed gear (overridable), and
# forwards every flag verbatim — so `recall.sh "<query>" --mode hybrid --json`
# is exactly `eidetic recall "<query>" --mode hybrid --json`.
#
# The store is the files backend. Default location resolves per-operation:
# PUBLIC records inside a git repo → <repo-root>/.eidetic/memory (committed,
# team-shared); PRIVATE records, or any record outside a git repo →
# $HOME/.eidetic/memory (never committed). Recall reads both stores and merges.
# An explicit EIDETIC_DATA_DIR wins and short-circuits to that single dir.

set -euo pipefail

# ── resolve the eidetic CLI (installed tool first, then dev checkout) ────────
EIDETIC=()
resolve_eidetic() {
    if command -v eidetic >/dev/null 2>&1; then
        EIDETIC=(eidetic)            # installed console script — the normal case
        return 0
    fi
    # Dev fallback: inside the eidetic-cli checkout, run via uv.
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
    # In a vendored copy there is no eidetic-cli checkout to fall back to, so the
    # only honest remedy is to install the CLI. One `error:` + one `hint:` line.
    printf 'error: eidetic CLI not found.\n' >&2
    printf 'hint: install it with: uv tool install eidetic-cli (or pipx install eidetic-cli); the console script is eidetic.\n' >&2
    return 1
}

usage() {
    cat <<'EOF'
recall.sh — search the shared eidetic memory store (the /recall skill).

Usage:
  recall.sh "<query>" [--mode exact|approximate|keyword|hybrid] [--top-k N] \
            [--alpha F] [--case-sensitive] [--filter KEY=VALUE]... \
            [--backend files|mongo|neo4j] [--scope NAME] [--visibility public|private] \
            [--json]

Modes (default: hybrid):
  exact        case-insensitive verbatim substring (--case-sensitive to tighten); offline-safe
  approximate  vector cosine / semantic similarity (uses the embed server)
  keyword      BM25 lexical; only records sharing a query term; offline-safe
  hybrid       alpha*approximate + (1-alpha)*keyword (--alpha, default 0.5);
               degrades to keyword-only when the embed server is offline

Every flag is forwarded verbatim to `eidetic recall`. See `eidetic explain recall`.
EOF
}

case "${1:-}" in
    -h | --help)
        usage
        exit 0
        ;;
    "")
        # A missing query is a usage error, not success. The bareword `help` is
        # a legitimate search term, so it is intentionally NOT a usage alias.
        printf 'error: no query given.\n' >&2
        printf 'hint: recall.sh "<query>" [--mode ...] [--json]; run recall.sh --help for usage.\n' >&2
        exit 1
        ;;
esac

resolve_eidetic || exit 2

# ── default to this agent's PERSONAL, PRIVATE scope (culture.yaml `suffix`) ──
# Query this agent's OWN personal scope by default, matching where /remember
# writes, instead of the global `default` scope shared by every project on this
# host. We read the `suffix` from the nearest culture.yaml (walking up from this
# script), so the scope follows the repo identity rather than being hard-coded —
# a downstream cite-don't-import copy adapts to its own suffix, and the colleague
# backend (running in a worktree of this same repo) resolves the same suffix,
# keeping the Claude↔colleague shared-memory story intact.
#
# The personal scope is PRIVATE by default to match /remember: in eidetic's model
# a private record is served only to a recall in the SAME scope (`can_serve`), so
# querying with --scope <suffix> --visibility private is what retrieves those
# isolated records (a public/default recall can't see them). Scope and visibility
# are paired — the private default applies only when we inject the resolved scope,
# and only if the caller didn't pass --visibility (so an explicit
# `--visibility public` still wins). An explicit --scope on the command line takes
# over steering entirely; a wheel install with no culture.yaml falls back to the
# plain CLI default (`default`/`public`).
resolve_scope() {
    local dir suffix=""
    dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
    while [ -n "$dir" ] && [ "$dir" != "/" ]; do
        if [ -f "$dir/culture.yaml" ]; then
            # Capture only the first non-space token after `suffix:` (so an
            # inline `# comment` or trailing space can't bleed into the scope),
            # then strip surrounding quotes only — matching the canonical parser
            # in .claude/skills/cicd/scripts/_resolve-nick.sh.
            # `|| true`: under `set -o pipefail`, `head -n1` closing the pipe
            # early can SIGPIPE `sed`, making the substitution non-zero and
            # aborting the script. An empty parse must yield "" here, not exit.
            suffix=$(sed -n \
                's/^[[:space:]]*-\{0,1\}[[:space:]]*suffix:[[:space:]]*\([^[:space:]]*\).*/\1/p' \
                "$dir/culture.yaml" | head -n1 | tr -d "\"'" || true)
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
        # rollout-cli eidetic-memory recipe POLICY OVERRIDE (not eidetic's
        # upstream private default): default to PUBLIC, so a plain recall queries
        # the in-repo public pool (<repo>/.eidetic/memory) this repo writes to.
        # Pass --visibility private to also surface this agent's private ($HOME)
        # notes. The two-store read model reads both dirs regardless.
        has_flag --visibility "$@" || SCOPE_ARGS+=(--visibility public)
    elif ! has_flag --visibility "$@"; then
        # No suffix AND no explicit --visibility: the query runs against
        # eidetic's own default (scope=default, visibility=public), not this
        # agent's private personal scope — so an empty result isn't silently
        # misread. Warn on stderr (stdout stays clean for --json). Warn ONLY
        # here: an explicit --scope (outer guard) or --visibility (this guard) is
        # a deliberate choice, honored verbatim, so either flag silences this.
        printf 'warning: no culture.yaml suffix resolved; querying the public default scope rather than a private personal scope. Pass --scope or --visibility to target deliberately.\n' >&2
    fi
fi

# Default the embedding endpoint to the local model-gear embed gear. eidetic
# falls back to a deterministic offline embedding if it's unreachable, so this
# is safe even when the gear is down. Override by exporting these yourself.
: "${EIDETIC_EMBED_URL:=http://localhost:8002/v1}"
: "${EIDETIC_EMBED_MODEL:=Qwen/Qwen3-Embedding-0.6B}"
export EIDETIC_EMBED_URL EIDETIC_EMBED_MODEL

exec "${EIDETIC[@]}" recall "${SCOPE_ARGS[@]}" "$@"
