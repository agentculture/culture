#!/usr/bin/env bash
#
# outsource — hand a scoped repo task to convertible (a different engine/mind).
#
# Convertible's engine is not necessarily stronger than the calling agent; it is
# a *different* mind, and diversity helps — which is why `review` is the headline
# verb. Three verbs drive `convertible drive` and print the result:
#
#   outsource explore "<question or area>"   read-only investigation -> findings
#   outsource review  "<what to focus on>"   diverse second-opinion on the diff
#   outsource write   "<task>" [--pr]        implement a change
#
# explore/review run in a throwaway `git worktree` at HEAD, so they can never
# touch your working tree or branch (any stray write is discarded). write runs
# in-place and lands a drive branch (or a PR with --pr).
#
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROMPTS_DIR="$SKILL_DIR/prompts"

# ── resolve the convertible CLI (installed, then local-dev fallback) ─────────
CONVERTIBLE=()
resolve_convertible() {
    if command -v convertible >/dev/null 2>&1; then
        CONVERTIBLE=(convertible)        # installed tool — the normal case
        return 0
    fi
    # Local-dev fallback: inside the convertible checkout, run via uv.
    local dir="$PWD"
    while [[ -n "$dir" ]] && [[ "$dir" != "/" ]]; do
        if [[ -f "$dir/pyproject.toml" ]] \
            && grep -q '^name = "convertible-cli"' "$dir/pyproject.toml" 2>/dev/null; then
            if command -v uv >/dev/null 2>&1; then
                CONVERTIBLE=(uv run convertible)
                return 0
            fi
            break
        fi
        dir=$(dirname "$dir")
    done
    cat >&2 <<'EOF'
error: convertible CLI not found.
hint: install it with `uv tool install convertible-cli` (or `pipx install convertible-cli`),
      or run from inside the convertible checkout with `uv` available.
      https://github.com/agentculture/convertible
EOF
    return 1
}

usage() {
    cat <<'EOF'
outsource — hand a scoped repo task to convertible (a different engine/mind).

Usage:
  outsource explore "<question or area>"     Read-only investigation -> findings (no side effects)
  outsource review  "<what to focus on>"     Diverse second-opinion on the committed diff (no side effects)
  outsource write   "<task>" [--pr]          Implement a change (drive branch, or PR with --pr)

Options:
  --repo PATH        Target repo (default: .)
  --base BRANCH      Base for `review` diff (default: main)
  --engine NAME      Engine wheel (default: $CONVERTIBLE_ENGINE or vllm-openai)
  --model NAME       Model (default: $CONVERTIBLE_MODEL or mmangkad/Qwen3.6-27B-NVFP4)
  --base-url URL     OpenAI base URL (default: $CONVERTIBLE_BASE_URL or http://localhost:8001/v1)
  --max-steps N      Loop step budget (default: 20)
  --timeout N        Per-request timeout, seconds (default: $CONVERTIBLE_TIMEOUT or 300)
  --allow-dirty      (write) allow running on a dirty tree
  --pr               (write) push + open a PR instead of a local drive branch

explore/review run in a throwaway git worktree at HEAD — they cannot touch your
working tree or branch. review compares <base>...HEAD (committed changes only).
EOF
}

# ── parse the verb ──────────────────────────────────────────────────────────
VERB="${1:-}"
case "$VERB" in
    explore | review | write) shift ;;
    -h | --help) usage; exit 0 ;;
    "") usage >&2; exit 2 ;;
    *)
        echo "error: unknown verb '$VERB' (expected explore|review|write)" >&2
        echo "hint: run 'outsource --help'" >&2
        exit 2
        ;;
esac

# Required external tools — fail fast with a clear message, not an opaque
# mid-run error, if the environment is missing one.
require_tools() {
    local missing=() t
    for t in python3 git grep mktemp; do
        command -v "$t" >/dev/null 2>&1 || missing+=("$t")
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        echo "error: missing required tool(s): ${missing[*]}" >&2
        echo "hint: outsource needs python3, git, grep, and mktemp on PATH." >&2
        exit 2
    fi
}

# Guard a value-taking flag: a trailing flag with no value would otherwise
# dereference an unset $2 and abort under `set -u`.
need_value() {  # $1 = remaining arg count ($#), $2 = flag name
    [[ "$1" -ge 2 ]] || {
        echo "error: $2 requires a value" >&2
        echo "hint: run 'outsource --help'" >&2
        exit 2
    }
}

require_tools

# ── defaults + flag parsing ─────────────────────────────────────────────────
REPO="."
BASE="main"
ENGINE="${CONVERTIBLE_ENGINE:-vllm-openai}"
MODEL="${CONVERTIBLE_MODEL:-mmangkad/Qwen3.6-27B-NVFP4}"
BASE_URL="${CONVERTIBLE_BASE_URL:-http://localhost:8001/v1}"
MAX_STEPS=20
TIMEOUT="${CONVERTIBLE_TIMEOUT:-300}"
ALLOW_DIRTY=0
OPEN_PR=0
ARG=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo) need_value "$#" "$1"; REPO="$2"; shift 2 ;;
        --base) need_value "$#" "$1"; BASE="$2"; shift 2 ;;
        --engine) need_value "$#" "$1"; ENGINE="$2"; shift 2 ;;
        --model) need_value "$#" "$1"; MODEL="$2"; shift 2 ;;
        --base-url) need_value "$#" "$1"; BASE_URL="$2"; shift 2 ;;
        --max-steps) need_value "$#" "$1"; MAX_STEPS="$2"; shift 2 ;;
        --timeout) need_value "$#" "$1"; TIMEOUT="$2"; shift 2 ;;
        --allow-dirty) ALLOW_DIRTY=1; shift ;;
        --pr) OPEN_PR=1; shift ;;
        -h | --help) usage; exit 0 ;;
        --) shift; while [[ $# -gt 0 ]]; do ARG="${ARG:+$ARG }$1"; shift; done ;;
        -*) echo "error: unknown option '$1'" >&2; echo "hint: run 'outsource --help'" >&2; exit 2 ;;
        *) ARG="${ARG:+$ARG }$1"; shift ;;
    esac
done

[[ -n "$ARG" ]] || { echo "error: $VERB needs a description argument" >&2; usage >&2; exit 2; }
[[ -d "$REPO" ]] || { echo "error: --repo is not a directory: $REPO" >&2; exit 2; }
REPO="$(cd "$REPO" && pwd)"

resolve_convertible || exit 2

# Per-request timeout is config (no drive flag); EngineConfig reads it from env.
# A local model can be slow on a growing context, so default generously.
export CONVERTIBLE_TIMEOUT="$TIMEOUT"
COMMON_FLAGS=(--engine "$ENGINE" --model "$MODEL" --base-url "$BASE_URL" --max-steps "$MAX_STEPS" --json)

# ── render an instruction from a prompt template ────────────────────────────
render_prompt() {
    local file="$PROMPTS_DIR/$1.md"
    [[ -f "$file" ]] || { echo "error: missing prompt template: $file" >&2; exit 2; }
    ARG="$ARG" BASE="$BASE" python3 - "$file" <<'PY'
import os, sys
tpl = open(sys.argv[1], encoding="utf-8").read()
sys.stdout.write(tpl.replace("$ARGUMENTS", os.environ["ARG"]).replace("$BASE", os.environ["BASE"]))
PY
}

# ── print the TaskResult that convertible emitted as JSON on stdout ─────────
# Reads JSON on stdin; prints a human/agent-readable digest; exits non-zero if
# the drive failed.
print_result() {
    # NOTE: must be `python3 -c`, not `python3 - <<HEREDOC`: a heredoc becomes
    # python's stdin (the script source), which would shadow the piped JSON and
    # leave sys.stdin.read() empty. The script body uses no single quotes.
    python3 -c '
import sys, json
raw = sys.stdin.read().strip()
if not raw:
    sys.stderr.write("error: convertible produced no result on stdout (see diagnostics above)\n")
    sys.exit(2)
try:
    d = json.loads(raw)
except Exception:
    sys.stderr.write("error: could not parse convertible --json output:\n")
    sys.stderr.write(raw[:2000] + "\n")
    sys.exit(2)
print("status:", d.get("status"))
print()
print((d.get("summary") or "").rstrip())
cf = d.get("changed_files") or []
if cf:
    print("\nchanged files:", ", ".join(cf))
if d.get("branch"):
    print("drive branch:", d["branch"])
if d.get("artifacts_path"):
    print("artifact:", d["artifacts_path"])
sys.exit(0 if d.get("status") == "ok" else 1)
'
}

# ── read-only verbs: isolate the drive in a throwaway worktree at HEAD ──────
# Worktree state is module-global, not a function local: the EXIT trap fires
# *after* run_readonly returns, so under `set -u` a local would be unbound.
_WT=""
_DRIVE_BRANCH=""

_cleanup_worktree() {
    [[ -n "$_WT" ]] || return 0
    git -C "$REPO" worktree remove --force "$_WT" >/dev/null 2>&1 || true
    rm -rf "$_WT" >/dev/null 2>&1 || true
    # Only ever delete the ephemeral drive branch convertible names
    # (convertible/<task_id>) — never an unrelated local branch, even if the
    # JSON `branch` value were unexpected.
    if [[ "$_DRIVE_BRANCH" == convertible/* ]]; then
        git -C "$REPO" branch -D "$_DRIVE_BRANCH" >/dev/null 2>&1 || true
    fi
}

run_readonly() {
    local instruction="$1"
    git -C "$REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
        || { echo "error: --repo is not a git repository: $REPO" >&2; exit 2; }

    _WT="$(mktemp -d)"
    trap _cleanup_worktree EXIT
    git -C "$REPO" worktree add -q --detach "$_WT" HEAD

    local out
    out="$("${CONVERTIBLE[@]}" drive "$instruction" --repo "$_WT" --no-pr "${COMMON_FLAGS[@]}")" || true
    _DRIVE_BRANCH="$(printf '%s' "$out" | python3 -c 'import sys, json
try:
    print(json.load(sys.stdin).get("branch") or "")
except Exception:
    print("")' 2>/dev/null || true)"
    printf '%s' "$out" | print_result
}

# ── write verb: in-place drive (drive branch, or PR with --pr) ──────────────
run_write() {
    local instruction="$1"
    if [[ "$ALLOW_DIRTY" -eq 0 ]] \
        && [[ -n "$(git -C "$REPO" status --porcelain 2>/dev/null)" ]]; then
        echo "error: working tree is dirty — commit/stash first, or pass --allow-dirty" >&2
        echo "hint: 'convertible drive --no-pr' commits uncommitted edits onto the drive branch" >&2
        exit 2
    fi
    local out
    if [[ "$OPEN_PR" -eq 1 ]]; then
        out="$("${CONVERTIBLE[@]}" drive "$instruction" --repo "$REPO" "${COMMON_FLAGS[@]}")"
    else
        out="$("${CONVERTIBLE[@]}" drive "$instruction" --repo "$REPO" --no-pr "${COMMON_FLAGS[@]}")"
    fi
    printf '%s' "$out" | print_result
}

case "$VERB" in
    explore) run_readonly "$(render_prompt explore)" ;;
    review) run_readonly "$(render_prompt review)" ;;
    write) run_write "$(render_prompt write)" ;;
esac
