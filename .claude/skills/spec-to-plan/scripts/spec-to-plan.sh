#!/usr/bin/env bash
# spec-to-plan.sh — drive devague's spec→plan engine (the /spec-to-plan skill).
#
# The skill is named `spec-to-plan`; the product/CLI it drives is `devague` (the
# idea→spec half lives in the sibling /think skill). This wrapper forwards every
# move to `devague plan <move>` verbatim — including the `status` verb the CLI
# internalised in 0.11.0 (devague#30/#31), which reads the plan convergence gate
# and names the recommended next move. It is the forward leg: seed a plan from a
# *converged* frame, then work it into a buildable plan.
#
# Origin: authored and maintained in agentculture/devague. guildmaster pulls
# this skill from devague and re-broadcasts it to the rest of the AgentCulture
# mesh, so it is written to run anywhere — portable bash, no devague-checkout
# assumptions.
#
# Plans persist under .devague/ in the current directory (alongside frames), so
# run from the repo you are speccing.

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
spec-to-plan.sh — drive devague's spec→plan engine (the /spec-to-plan skill).

Usage:
  spec-to-plan.sh <move> [args...]   forward a `devague plan` move
  spec-to-plan.sh status [--plan S]  where the plan stands + the next move
  spec-to-plan.sh help               this help

Moves (forwarded to `devague plan`; run `devague plan learn` for the method):
  new          start a plan from a CONVERGED frame (--frame <slug>)
  task         add a task (--accept / --dep / --covers, --origin user|llm)
  accept       add an acceptance criterion to a task
  depend       record that a task depends on another (--on)
  cover        mark a task as covering a coverage target (c*/h*)
  confirm      confirm a task                          (USER-only decision)
  reject       reject a task
  risk         record a first-class plan risk
  converge     check whether the plan can export
  export       write the buildable plan (only after converge passes)
  waves        emit deterministic dependency waves (scheduling metadata, not orchestration)
  status       where the plan stands + the recommended next move
  show / list  render a plan / list plans
  learn        teach the method   |   explain <move>  explain one move

Plans persist under .devague/ in the current directory — run from the repo you
are speccing. Results go to stdout, diagnostics to stderr; pass --json to any
move for structured output.

Note: every move — including `status` — is forwarded verbatim as `devague plan
<move>`, so new plan moves work without editing this script. (`status` was
internalised into the CLI in devague 0.11.0; it is no longer wrapper-only.)

Prior leg: a plan is seeded from a converged frame produced by the /think skill.
EOF
}

main() {
    case "${1:-help}" in
        help | -h | --help)
            usage
            return 0
            ;;
        *)
            # Forward every move to `devague plan <move>` verbatim — including the
            # internalised `status` verb (`devague plan status`) — so the CLI's own
            # parser owns the plan surface.
            resolve_devague
            exec "${DEVAGUE[@]}" plan "$@"
            ;;
    esac
}

main "$@"
