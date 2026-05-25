#!/usr/bin/env bash
# assign-to-workforce.sh — fan out devague plan waves to parallel agents.
#
# The skill is named `assign-to-workforce`; it reads `devague plan waves`
# (scheduling metadata produced by the /spec-to-plan skill) and renders the
# implementation split plan: task map + per-task agent/model proposal + a
# go/no-go prompt for the human. The actual fan-out (worktree creation,
# spawning, TDD-gated merges) is performed by the operator/main agent once
# the human approves the split plan.
#
# The devague CLI is non-orchestrating (#20): `devague plan waves` describes
# the dependency graph; it does not spawn agents, manage worktrees, or pick
# a backend. This wrapper is the operator-facing helper.
#
# Origin: authored and maintained in agentculture/devague. guildmaster pulls
# this skill from devague and re-broadcasts it to the rest of the AgentCulture
# mesh, so it is written to run anywhere — portable bash, no devague-checkout
# assumptions.
#
# Plans persist under .devague/ in the current directory, so run from the repo
# you are implementing.

set -euo pipefail

# ── resolve the devague CLI (mesh-first, then local-dev fallback) ───────────
DEVAGUE=()
resolve_devague() {
    if command -v devague >/dev/null 2>&1; then
        DEVAGUE=(devague)            # installed tool — the normal mesh case
        return 0
    fi
    # Local-dev fallback: inside the devague checkout, run via uv.
    local dir="$PWD"
    while [ -n "$dir" ] && [ "$dir" != "/" ]; do
        if [ -f "$dir/pyproject.toml" ] \
            && grep -q '^name = "devague"' "$dir/pyproject.toml" 2>/dev/null; then
            if command -v uv >/dev/null 2>&1; then
                DEVAGUE=(uv run devague)
                return 0
            fi
            break
        fi
        dir=$(dirname "$dir")
    done
    cat >&2 <<'EOF'
error: devague CLI not found.
hint: install it with `uv tool install devague` (or `pipx install devague`),
      or run from inside the devague checkout with `uv` available.
      https://github.com/agentculture/devague
EOF
    return 1
}

usage() {
    cat <<'EOF'
assign-to-workforce.sh — fan out devague plan waves to parallel agents.

Usage:
  assign-to-workforce.sh split-plan [--plan <slug>]   print the implementation split plan
  assign-to-workforce.sh waves      [--plan <slug>] [--json]   list dependency waves
  assign-to-workforce.sh help                         this help

Commands:
  split-plan   Read `devague plan waves --json` and render the human-facing
               implementation split plan: task map + per-task agent/model
               proposal + go/no-go. Present this to the human before any
               fan-out; do not proceed without approval.
  waves        Forward `devague plan waves` (and any extra flags) verbatim.
               On a converged plan exits 0 and lists the dependency waves.

Plans persist under .devague/ in the current directory — run from the repo
you are implementing. Results go to stdout, diagnostics to stderr.

Human gates (three only):
  1. The exported spec (already closed by the /think leg).
  2. This implementation split plan (go/no-go to assign to workforce).
  3. The final PR (opened by the main agent via `cicd` / `agex pr open`).

The devague CLI is non-orchestrating (#20): `devague plan waves` describes
the graph; the operator performs the fan-out. One worktree per task; TDD
gates every merge (tests pass before AND after merge); no human per task.
EOF
}

# ── split-plan: render the implementation split plan for human review ────────
cmd_split_plan() {
    local extra_args=()
    # Forward any --plan flag so waves targets the right plan.
    while [ $# -gt 0 ]; do
        extra_args+=("$1")
        shift
    done

    local waves_json tmp_err waves_rc old_exit_trap
    # Clean up the temp file on any exit path — including a signal after its
    # creation — WITHOUT permanently changing the script's process-global EXIT
    # handling. Capture any prior EXIT trap BEFORE mktemp (that capture forks a
    # subshell, so doing it first keeps it out of the untracked-file window),
    # then install our cleanup trap on the line immediately after mktemp, and
    # restore the prior trap once the file is safely gone (#30; PR #31 review;
    # devague#32).
    old_exit_trap="$(trap -p EXIT)"
    tmp_err="$(mktemp)"
    trap 'rm -f "$tmp_err"' EXIT
    set +e
    waves_json="$("${DEVAGUE[@]}" plan waves --json "${extra_args[@]}" 2>"$tmp_err")"
    waves_rc=$?
    set -e
    local waves_err
    waves_err="$(cat "$tmp_err")"
    rm -f "$tmp_err"
    trap - EXIT
    eval "${old_exit_trap}"  # empty string is a no-op; re-installs a prior trap if any

    if [ "$waves_rc" -ne 0 ]; then
        printf '%s\n' "$waves_err" >&2
        return "$waves_rc"
    fi

    DEVAGUE_WAVES_JSON="$waves_json" python3 - <<'PY'
import json
import os
import sys

raw = os.environ.get("DEVAGUE_WAVES_JSON", "").strip()
if not raw:
    print("error: no waves output from devague plan waves", file=sys.stderr)
    print("hint: ensure a converged plan exists (devague plan converge)", file=sys.stderr)
    sys.exit(1)

try:
    data = json.loads(raw)
except json.JSONDecodeError as exc:
    print(f"error: could not parse waves JSON: {exc}", file=sys.stderr)
    sys.exit(1)

plan_slug = data.get("plan", "(unknown)")
waves = data.get("waves") or []

print(f"Implementation split plan — plan: {plan_slug}")
print()
print("Dependency waves (from `devague plan waves`):")
for i, wave in enumerate(waves, 1):
    tasks = ", ".join(wave)
    print(f"  Wave {i}: [{tasks}]")

print()
print("Task assignments (proposed — edit before approving):")
print()

headers = ("Task", "Wave", "Summary", "Agent type", "Model", "Scope note")
rows = []
for i, wave in enumerate(waves, 1):
    for task_id in wave:
        rows.append((
            task_id,
            str(i),
            "(see plan export for summary + acceptance criteria)",
            "subagent",
            "cheaper/faster",
            "TDD-scoped task; isolated worktree; tests gate merge",
        ))

col_widths = [max(len(h), max((len(r[j]) for r in rows), default=0))
              for j, h in enumerate(headers)]

def row_str(cells):
    return "| " + " | ".join(c.ljust(w) for c, w in zip(cells, col_widths)) + " |"

sep = "| " + " | ".join("-" * w for w in col_widths) + " |"
print(row_str(headers))
print(sep)
for row in rows:
    print(row_str(row))

print()
print("Go/no-go: review the table above, edit agent type / model / scope as needed,")
print("then confirm: \"Approved — assign to workforce\" or \"Edit first\".")
print()
print("Once approved, fan out wave by wave:")
print("  1. Create one git worktree per task in the wave.")
print("  2. Spawn a task agent per worktree (brief = task summary + acceptance criteria).")
print("  3. Await all tasks in the wave; then TDD-gate each merge (tests before + after).")
print("  4. Advance to the next wave.")
print("  5. Open the final PR (human gate 3) after all waves merge and tests pass.")
PY
}

main() {
    case "${1:-help}" in
        help | -h | --help)
            usage
            return 0
            ;;
        split-plan)
            shift
            resolve_devague
            cmd_split_plan "$@"
            ;;
        waves)
            shift
            resolve_devague
            exec "${DEVAGUE[@]}" plan waves "$@"
            ;;
        *)
            printf 'error: unknown subcommand: %s\n' "$1" >&2
            printf 'hint: run `assign-to-workforce.sh help` for usage\n' >&2
            return 1
            ;;
    esac
}

main "$@"
