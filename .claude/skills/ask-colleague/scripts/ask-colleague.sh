#!/usr/bin/env bash
#
# ask-colleague — hand a scoped repo task to colleague (a different engine/mind).
#
# Colleague's engine is not necessarily stronger than the calling agent; it is
# a *different* mind, and diversity helps — which is why `review` is the headline
# verb. Three verbs drive `colleague drive` and print the result:
#
#   ask-colleague explore "<question or area>"   read-only investigation -> findings
#   ask-colleague review  "<what to focus on>"   diverse second-opinion on the diff
#   ask-colleague write   "<task>" [--apply]     implement a change (preview by default)
#   ask-colleague feedback <id|last> --rating N  grade a past drive (ROI loop); no rating -> show
#   ask-colleague feedback list                  list every recorded drive by request + grade
#   ask-colleague clean                          reap stale colleague/* branches + artifacts (#162)
#
# explore/review run in a throwaway `git worktree` at HEAD, so they can never
# touch your working tree or branch (any stray write is discarded). write also
# previews in a throwaway worktree by default (reporting what it WOULD change);
# pass --apply to land a drive branch in place, or --pr to push + open a PR.
#
# explore/review are read-only probes: they preserve their artifact but do NOT
# move the `last` pointer (issue #132), so `feedback last` stays aimed at the
# most recent consequential write. Grade a probe by its printed task-id (every
# drive prints `task:` and a `grade:` hint), or find it with `feedback list`.
#
# A crashed/interrupted `write --apply` can leave a dangling colleague/<id>
# branch (and 0-byte .colleague/ artifacts) that breaks `git fetch`. The
# EXIT-trap cleanup below only reaps the *current* read-only run's worktree, not
# a *prior* crashed run — `ask-colleague clean` (which shells out to `colleague
# clean`) reaps those, scoped strictly to colleague/* so an unrelated branch is
# never touched.
#
# Exit-code policy (matches colleague's CLI contract, #161): 0 success · 1
# user-input error (bad/missing verb, flag, arg, or path; dirty-tree state guard
# — same class as the runtime's EXIT_USER_ERROR guard) · 2 environment/setup
# error (missing required tool, colleague CLI not found, missing prompt template).
#
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROMPTS_DIR="$SKILL_DIR/prompts"

# ── resolve the colleague CLI (installed, then local-dev fallback) ─────────
COLLEAGUE=()
resolve_colleague() {
    if command -v colleague >/dev/null 2>&1; then
        COLLEAGUE=(colleague)        # installed tool — the normal case
        return 0
    fi
    # Local-dev fallback: inside the colleague checkout, run via uv.
    local dir="$PWD"
    while [[ -n "$dir" ]] && [[ "$dir" != "/" ]]; do
        if [[ -f "$dir/pyproject.toml" ]] \
            && grep -q '^name = "colleague"' "$dir/pyproject.toml" 2>/dev/null; then
            if command -v uv >/dev/null 2>&1; then
                COLLEAGUE=(uv run colleague)
                return 0
            fi
            break
        fi
        dir=$(dirname "$dir")
    done
    cat >&2 <<'EOF'
error: colleague CLI not found.
hint: install it with `uv tool install colleague` (or `pipx install colleague`),
      or run from inside the colleague checkout with `uv` available.
      https://github.com/agentculture/colleague
EOF
    return 1
}

usage() {
    cat <<'EOF'
ask-colleague — hand a scoped repo task to colleague (a different engine/mind).

Usage:
  ask-colleague explore "<question or area>"     Read-only investigation -> findings (no side effects)
  ask-colleague review  "<what to focus on>"     Diverse second-opinion on the committed diff (no side effects)
  ask-colleague write   "<task>" [--apply|--pr]  Implement a change (preview by default; --apply lands it)
  ask-colleague feedback <id|last> [--rating N]  Grade a past drive (ROI loop); with --rating records, without shows
  ask-colleague feedback list                    List every recorded drive by request + grade (find one by its request)
  ask-colleague clean [--dry-run]                Reap stale/corrupt colleague/* branches + orphaned .colleague/ artifacts (#162)

Options:
  --repo PATH        Target repo (default: .)
  --dry-run          (clean) report what would be reaped without changing anything
  --base BRANCH      Base for `review` diff (default: main)
  --engine NAME      Backend plugin (default: $COLLEAGUE_ENGINE or vllm-openai)
  --model NAME       Model (default: $COLLEAGUE_MODEL or sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP)
  --base-url URL     OpenAI base URL (default: $COLLEAGUE_BASE_URL or http://localhost:8001/v1)
  --max-steps N      Loop step budget (default: 20)
  --timeout N        Per-request timeout, seconds (default: $COLLEAGUE_TIMEOUT or 300)
  --apply            (write) apply the change in place (drive branch) instead of previewing
  --allow-dirty      (write) allow running on a dirty tree (only with --apply/--pr)
  --pr               (write) push + open a PR instead of a local drive branch (implies --apply)
  --rating N         (feedback) record a 1-5 quality rating for the drive
  --notes "..."      (feedback) free-text notes to store with the rating
  --by NAME          (feedback) who is grading (default: colleague's resolved identity)

explore/review run in a throwaway git worktree at HEAD — they cannot touch your
working tree or branch. review compares <base>...HEAD (committed changes only).
write previews in a throwaway worktree too unless --apply (or --pr) is given.
feedback grades a finished drive: stats (in the artifact) say what it cost,
feedback says how good it was — together, the ROI of outsourcing. explore/review
do not move `last` (they are read-only) — grade them by their printed task-id, or
run `ask-colleague feedback list` to find a drive by its request.
clean recovers a repo a crashed run wedged: it reaps stale/corrupt colleague/*
branches + orphaned .colleague/ artifacts (scoped to colleague/* only). For the
full flag set (--merged / --older-than) call `colleague clean` directly.
EOF
}

# ── parse the verb ──────────────────────────────────────────────────────────
VERB="${1:-}"
case "$VERB" in
    explore | review | write | feedback | clean) shift ;;
    -h | --help) usage; exit 0 ;;
    "") usage >&2; exit 1 ;;  # missing arg -> user-input error (#161)
    *)
        echo "error: unknown verb '$VERB' (expected explore|review|write|feedback|clean)" >&2
        echo "hint: run 'ask-colleague --help'" >&2
        exit 1  # bad verb -> user-input error (#161)
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
        echo "hint: ask-colleague needs python3, git, grep, and mktemp on PATH." >&2
        exit 2
    fi
}

# Guard a value-taking flag: a trailing flag with no value would otherwise
# dereference an unset $2 and abort under `set -u`.
need_value() {  # $1 = remaining arg count ($#), $2 = flag name
    [[ "$1" -ge 2 ]] || {
        echo "error: $2 requires a value" >&2
        echo "hint: run 'ask-colleague --help'" >&2
        exit 1  # missing flag value -> user-input error (#161)
    }
}

require_tools

# ── defaults + flag parsing ─────────────────────────────────────────────────
REPO="."
BASE="main"
# COLLEAGUE_* wins; the legacy CONVERTIBLE_* names are honored as a deprecated fallback.
ENGINE="${COLLEAGUE_ENGINE:-${CONVERTIBLE_ENGINE:-vllm-openai}}"
MODEL="${COLLEAGUE_MODEL:-${CONVERTIBLE_MODEL:-sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP}}"
BASE_URL="${COLLEAGUE_BASE_URL:-${CONVERTIBLE_BASE_URL:-http://localhost:8001/v1}}"
MAX_STEPS=20
TIMEOUT="${COLLEAGUE_TIMEOUT:-${CONVERTIBLE_TIMEOUT:-300}}"
ALLOW_DIRTY=0
APPLY=0
OPEN_PR=0
DRY_RUN=0
RATING=""
NOTES=""
BY=""
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
        --apply) APPLY=1; shift ;;
        --allow-dirty) ALLOW_DIRTY=1; shift ;;
        --pr) OPEN_PR=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --rating) need_value "$#" "$1"; RATING="$2"; shift 2 ;;
        --notes) need_value "$#" "$1"; NOTES="$2"; shift 2 ;;
        --by) need_value "$#" "$1"; BY="$2"; shift 2 ;;
        -h | --help) usage; exit 0 ;;
        --) shift; while [[ $# -gt 0 ]]; do ARG="${ARG:+$ARG }$1"; shift; done ;;
        # unknown option -> user-input error (#161)
        -*) echo "error: unknown option '$1'" >&2; echo "hint: run 'ask-colleague --help'" >&2; exit 1 ;;
        *) ARG="${ARG:+$ARG }$1"; shift ;;
    esac
done

# clean takes no description argument; every other verb requires one. (All of the
# guards below are user-input errors -> exit 1, per the policy comment at the top.)
if [[ "$VERB" == "clean" ]]; then
    [[ -z "$ARG" ]] || { echo "error: clean takes no description argument" >&2; exit 1; }
else
    [[ -n "$ARG" ]] || { echo "error: $VERB needs a description argument" >&2; usage >&2; exit 1; }
fi
[[ -d "$REPO" ]] || { echo "error: --repo is not a directory: $REPO" >&2; exit 1; }
REPO="$(cd "$REPO" && pwd)"

# One git-repo guard for every verb: --repo is a runtime target like `git -C`, but
# it must at least be a real git work tree. Fail fast with a clear message instead
# of an opaque mid-drive error (read-only verbs add a worktree, write commits a
# drive branch, clean reaps colleague/* refs).
git -C "$REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || { echo "error: --repo is not a git repository: $REPO" >&2; exit 1; }

# review interpolates --base into the LLM instruction ("git diff $BASE...HEAD"),
# so reject a value that is not a real commit/ref before it is rendered into the
# prompt — fail fast rather than hand the model a bogus (or injected) ref.
if [[ "$VERB" == "review" ]]; then
    git -C "$REPO" rev-parse --verify --quiet "${BASE}^{commit}" >/dev/null 2>&1 \
        || { echo "error: --base is not a valid commit/ref in $REPO: $BASE" >&2; exit 1; }
fi

resolve_colleague || exit 2

# Per-request timeout is config (no drive flag); EngineConfig reads it from env.
# A local model can be slow on a growing context, so default generously.
export COLLEAGUE_TIMEOUT="$TIMEOUT"
COMMON_FLAGS=(--engine "$ENGINE" --model "$MODEL" --base-url "$BASE_URL" --max-steps "$MAX_STEPS" --json)

# ── render an instruction from a prompt template ────────────────────────────
render_prompt() {
    local file="$PROMPTS_DIR/$1.md"
    [[ -f "$file" ]] || { echo "error: missing prompt template: $file" >&2; exit 2; }
    # Single-pass substitution: doing `.replace("$ARGUMENTS", ARG).replace("$BASE",
    # BASE)` in two passes lets a literal "$BASE" *inside* the user's argument get
    # clobbered by the second pass. One re.sub never re-scans already-substituted
    # text, so injected tokens survive verbatim.
    ARG="$ARG" BASE="$BASE" python3 - "$file" <<'PY'
import os, re, sys
tpl = open(sys.argv[1], encoding="utf-8").read()
repl = {"$ARGUMENTS": os.environ["ARG"], "$BASE": os.environ["BASE"]}
sys.stdout.write(re.compile(r"\$ARGUMENTS|\$BASE").sub(lambda m: repl[m.group(0)], tpl))
PY
}

# ── print the TaskResult that colleague emitted as JSON on stdout ─────────
# Reads JSON on stdin; prints a human/agent-readable digest — to stdout on
# success, to stderr on failure so a caller can script on a clean stdout — and
# exits non-zero if the drive failed.
print_result() {
    # NOTE: must be `python3 -c`, not `python3 - <<HEREDOC`: a heredoc becomes
    # python's stdin (the script source), which would shadow the piped JSON and
    # leave sys.stdin.read() empty. The script body uses no single quotes.
    #
    # $1 (optional): the real artifact directory. When the drive ran in a
    # throwaway worktree (read-only verbs), the JSON's artifacts_path points into
    # that soon-deleted worktree; pass the real repo's .colleague/ so the
    # printed path names the preserved copy instead. Empty -> print as-is.
    # $2 (optional): "1" when the drive is gradable (its artifact survives in the
    # real repo) -> print a copy-paste `grade:` hint with the explicit task-id, so
    # the caller never has to rely on `last` (issue #132). A preview leaves it
    # empty (its artifact was discarded with the worktree, so it is not gradable).
    ASK_COLLEAGUE_REAL_ARTIFACT_DIR="${1:-}" ASK_COLLEAGUE_GRADABLE="${2:-}" python3 -c '
import sys, json, os
raw = sys.stdin.read().strip()
if not raw:
    sys.stderr.write("error: colleague produced no result on stdout (see diagnostics above)\n")
    sys.exit(2)
try:
    d = json.loads(raw)
except Exception:
    sys.stderr.write("error: could not parse colleague --json output:\n")
    sys.stderr.write(raw[:2000] + "\n")
    sys.exit(2)
ok = d.get("status") == "ok"
out = sys.stdout if ok else sys.stderr
tid = d.get("task_id") or ""
print("status:", d.get("status"), file=out)
if tid:
    print("task:", tid, file=out)
# A drive that stopped without calling finish (colleague#142) or exhausted its
# step budget did NOT deliver an authoritative result — its summary is the model
# trailing off mid-task. Warn so the caller treats it as a partial, not a verdict.
# The warning is a DIAGNOSTIC -> always stderr (never stdout), so a successful
# drive keeps a clean, machine-readable stdout (no single quotes in this body).
if d.get("stopped_without_finish"):
    print("warning: drive ended without calling finish — treat the summary as a", file=sys.stderr)
    print("         partial (the model stopped mid-task), not an authoritative result.", file=sys.stderr)
elif d.get("not_finished"):
    print("warning: drive ran out of steps without finishing — summary is partial.", file=sys.stderr)
print(file=out)
print((d.get("summary") or "").rstrip(), file=out)
cf = d.get("changed_files") or []
if cf:
    print("\nchanged files:", ", ".join(cf), file=out)
if d.get("branch"):
    print("drive branch:", d["branch"], file=out)
ap = d.get("artifacts_path")
real_dir = os.environ.get("ASK_COLLEAGUE_REAL_ARTIFACT_DIR") or ""
if ap and real_dir:
    ap = os.path.join(real_dir, os.path.basename(ap))
if ap:
    print("artifact:", ap, file=out)
# A drive is gradable whenever its artifact survives — including a FAILED drive
# (colleague writes an artifact on failure too, h5): a failure rated 1/5 is exactly
# the ROI signal, so the hint must not be gated on `ok` (#139 qodo). It prints to
# `out` (stderr on failure), matching the rest of the failure digest.
if tid and os.environ.get("ASK_COLLEAGUE_GRADABLE") == "1":
    print("grade:", "ask-colleague feedback", tid, "--rating N", file=out)
sys.exit(0 if ok else 1)
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
    # Only ever delete the ephemeral drive branch colleague names
    # (colleague/<task_id>) — never an unrelated local branch, even if the
    # JSON `branch` value were unexpected.
    if [[ "$_DRIVE_BRANCH" == colleague/* ]]; then
        git -C "$REPO" branch -D "$_DRIVE_BRANCH" >/dev/null 2>&1 || true
    fi
    # Defensive: clear the handles so a re-entry is a clean no-op. The EXIT trap
    # fires once today, but this keeps cleanup idempotent against future refactors
    # (dogfood-review suggestion, #61).
    _WT=""
    _DRIVE_BRANCH=""
}

# Extract the drive branch (colleague/<id>) from a TaskResult JSON on stdin.
_extract_branch() {
    python3 -c 'import sys, json
try:
    print(json.load(sys.stdin).get("branch") or "")
except Exception:
    print("")' 2>/dev/null || true
}

# Extract the task id from a TaskResult JSON on stdin.
_extract_task_id() {
    python3 -c 'import sys, json
try:
    print(json.load(sys.stdin).get("task_id") or "")
except Exception:
    print("")' 2>/dev/null || true
}

# Extract the artifact filename (basename of artifacts_path) from a TaskResult
# JSON on stdin. The runtime names artifacts <task_id>.<slug>.json (slugged) or
# <task_id>.json (bare), so the skill must copy the file the drive actually
# reported rather than reconstruct a name. os.path.basename strips any directory
# component, so a hostile artifacts_path can never point the copy outside .colleague/.
_extract_artifact_name() {
    python3 -c 'import sys, json, os
try:
    print(os.path.basename(json.load(sys.stdin).get("artifacts_path") or ""))
except Exception:
    print("")' 2>/dev/null || true
}

# A copied filename must be a single safe path segment before it is joined into a
# copy destination (mirrors colleague/feedback.py's _validate_task_id: allow
# [A-Za-z0-9][A-Za-z0-9._-]*, reject "."/".." and any path separator). The name
# comes from colleague's own TaskResult basename, but validating it keeps the
# write strictly inside $REPO/.colleague/ even for a malformed/hostile result.
_valid_segment() {
    [[ "$1" != "." && "$1" != ".." && "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]
}

# Read-only verbs drive in a throwaway worktree that _cleanup_worktree deletes, so
# the artifact written under <worktree>/.colleague/ would vanish with it. Copy it
# back to the real repo's .colleague/ so the drive can still be graded afterwards
# by its task-id (`ask-colleague feedback <id>` / `ask-colleague feedback list`).
#
# Deliberately does NOT write a last_drive pointer (issue #132): explore/review
# are read-only probes and must not move `last`, or a later probe would steal a
# grade meant for a consequential write. `last` stays aimed at the most recent
# write; a probe is graded by its printed task-id.
#
# Writes only the gitignored .colleague/ bookkeeping dir — never the tracked tree.
# $1 is the artifact basename (<task_id>.<slug>.json or <task_id>.json) from the
# drive's reported artifacts_path. Returns non-zero when the name is unsafe or the
# copy fails, so run_readonly never reports a preserved path that isn't there.
_preserve_artifact() {
    local art_name="$1"
    [[ -n "$art_name" && -n "$_WT" ]] || return 1
    if ! _valid_segment "$art_name"; then
        printf 'ask-colleague: refusing to preserve unsafe artifact name %q\n' "$art_name" >&2
        return 1
    fi
    local src="$_WT/.colleague"
    local dst="$REPO/.colleague"
    [[ -f "$src/$art_name" ]] || return 1
    mkdir -p "$dst" || return 1
    # The JSON artifact is the record of the drive — surface a copy failure rather
    # than swallow it, so the caller can fall back to honest path reporting.
    if ! cp -f "$src/$art_name" "$dst/$art_name"; then
        printf 'ask-colleague: could not preserve artifact %s\n' "$art_name" >&2
        return 1
    fi
    # The trace shares the artifact stem (.json -> .trace.jsonl); a best-effort
    # copy is fine — it is optional context, not the record of the drive.
    local trace_name="${art_name%.json}.trace.jsonl"
    if [[ -f "$src/$trace_name" ]]; then
        cp -f "$src/$trace_name" "$dst/$trace_name" 2>/dev/null || true
    fi
}

# Spin up a throwaway detached worktree at HEAD (the isolation both read-only
# verbs and the write preview share). `mktemp -d` is given an explicit template:
# GNU mktemp tolerates a bare `-d`, but BSD/macOS mktemp requires one.
_add_worktree() {
    _WT="$(mktemp -d "${TMPDIR:-/tmp}/ask-colleague.XXXXXX")"
    trap _cleanup_worktree EXIT
    git -C "$REPO" worktree add -q --detach "$_WT" HEAD
}

run_readonly() {
    local instruction="$1"
    _add_worktree
    local out
    out="$("${COLLEAGUE[@]}" drive "$instruction" --repo "$_WT" --no-pr "${COMMON_FLAGS[@]}")" || true
    _DRIVE_BRANCH="$(printf '%s' "$out" | _extract_branch)"
    # Preserve the artifact to the real repo BEFORE the EXIT trap removes the
    # worktree, so the drive can be graded by its task-id (`ask-colleague feedback
    # <id>`). Only point print_result at the real repo — and mark the drive
    # gradable — when the copy actually landed; otherwise the printed `artifact:`
    # would name a file preservation never wrote, and the grade hint would point
    # at an artifact that isn't there.
    local art_name real_dir="" gradable=""
    art_name="$(printf '%s' "$out" | _extract_artifact_name)"
    if _preserve_artifact "$art_name"; then
        real_dir="$REPO/.colleague"
        gradable="1"
    fi
    printf '%s' "$out" | print_result "$real_dir" "$gradable"
}

# ── write preview (default): drive in a throwaway worktree, show the would-be ──
# change, then discard. Nothing reaches the real working tree or branch — pass
# --apply (or --pr) to land it for real.
run_preview() {
    local instruction="$1"
    _add_worktree
    local out
    out="$("${COLLEAGUE[@]}" drive "$instruction" --repo "$_WT" --no-pr "${COMMON_FLAGS[@]}")" || true
    _DRIVE_BRANCH="$(printf '%s' "$out" | _extract_branch)"

    # Capture the would-be patch before _cleanup_worktree deletes the drive branch.
    local patch=""
    if [[ "$_DRIVE_BRANCH" == colleague/* ]]; then
        patch="$(git -C "$REPO" diff "HEAD..$_DRIVE_BRANCH" 2>/dev/null || true)"
    fi

    local rc=0
    printf '%s' "$out" | print_result || rc=$?
    if [[ "$rc" -eq 0 ]]; then
        if [[ -n "$patch" ]]; then
            printf '\n--- preview diff (NOT applied — pass --apply to land it) ---\n'
            printf '%s\n' "$patch"
        else
            printf '\n(preview: no file changes reported; NOT applied)\n'
        fi
    fi
    return "$rc"
}

# ── write verb: preview by default; --apply lands a drive branch; --pr opens a ─
# PR (implies apply). The dirty-tree guard only matters when applying in place —
# a preview runs in an isolated worktree and never touches the working tree.
run_write() {
    local instruction="$1"
    if [[ "$APPLY" -eq 0 && "$OPEN_PR" -eq 0 ]]; then
        run_preview "$instruction"
        return
    fi
    if [[ "$ALLOW_DIRTY" -eq 0 ]] \
        && [[ -n "$(git -C "$REPO" status --porcelain 2>/dev/null)" ]]; then
        echo "error: working tree is dirty — commit/stash first, or pass --allow-dirty" >&2
        echo "hint: 'colleague drive --no-pr' commits uncommitted edits onto the drive branch" >&2
        # User-fixable state guard -> exit 1, matching the runtime's own
        # dirty-tree guard (colleague/handoff.py _guard_clean_tree =
        # EXIT_USER_ERROR), not exit 2 (#161).
        exit 1
    fi
    # The runtime now enforces its own dirty-tree guard (colleague#149); pass
    # --allow-dirty through so an operator opt-in here isn't re-refused by the
    # CLI. Append to the always-non-empty COMMON_FLAGS (not the global one at the
    # top — read-only/preview verbs run in a clean worktree and must not get it),
    # which sidesteps the `set -u` empty-array expansion hazard.
    [[ "$ALLOW_DIRTY" -eq 1 ]] && COMMON_FLAGS+=(--allow-dirty)
    # `|| true`: a failed drive (`colleague drive` returns 1 when status != ok,
    # printing the result JSON to stdout) must still flow into print_result so the
    # digest is emitted (to stderr) and the wrapper exits non-zero — not aborted by
    # `set -e` at the assignment, which would swallow the digest. Matches the
    # read-only / preview paths, which already guard this way.
    local out
    if [[ "$OPEN_PR" -eq 1 ]]; then
        out="$("${COLLEAGUE[@]}" drive "$instruction" --repo "$REPO" "${COMMON_FLAGS[@]}")" || true
    else
        out="$("${COLLEAGUE[@]}" drive "$instruction" --repo "$REPO" --no-pr "${COMMON_FLAGS[@]}")" || true
    fi
    # A landed write persists its artifact in the real repo and moves `last`, so it
    # is gradable — print the `grade:` hint (with the explicit task-id).
    printf '%s' "$out" | print_result "" "1"
}

# ── feedback verb: grade a finished drive (the ROI loop) ────────────────────
# A thin pass-through to `colleague feedback`: `list` lists every recorded drive
# by request + grade; with --rating it records a 1-5 grade + notes; without, it
# shows the drive's existing feedback. The ref is the drive's task-id, `last` for
# the most recent consequential drive in --repo, or the literal `list`. No
# worktree, no engine — colleague owns the store and its own stdout/stderr/exit.
run_feedback() {
    local ref="$1"
    # Build one command array (never empty) so we don't expand an empty array
    # under `set -u` — the optional --by is appended only when set.
    local cmd=("${COLLEAGUE[@]}" feedback)
    if [[ "$ref" == "list" ]]; then
        cmd+=(list)
    elif [[ -n "$RATING" ]]; then
        cmd+=(record "$ref" --rating "$RATING" --notes "$NOTES")
        [[ -n "$BY" ]] && cmd+=(--by "$BY")
    else
        cmd+=(show "$ref")
    fi
    cmd+=(--repo "$REPO")
    "${cmd[@]}"
}

# ── clean verb: recover a repo a crashed run wedged (#162) ──────────────────
# A thin pass-through to `colleague clean`: the runtime reaps stale/corrupt
# colleague/* branches + orphaned .colleague/ artifacts, scoped strictly to
# colleague/* (so an unrelated branch is never touched) and conservative with
# .git/objects. No worktree, no engine — colleague owns the reap + its own
# stdout/stderr/exit. The full flag set (--merged / --older-than) lives on
# `colleague clean`; the skill forwards the common --dry-run.
run_clean() {
    local cmd=("${COLLEAGUE[@]}" clean --repo "$REPO")
    [[ "$DRY_RUN" -eq 1 ]] && cmd+=(--dry-run)
    "${cmd[@]}"
}

case "$VERB" in
    explore) run_readonly "$(render_prompt explore)" ;;
    review) run_readonly "$(render_prompt review)" ;;
    write) run_write "$(render_prompt write)" ;;
    feedback) run_feedback "$ARG" ;;
    clean) run_clean ;;
esac
