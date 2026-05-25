#!/usr/bin/env bash
# think.sh — drive devague's working-backwards idea→spec engine (the /think skill).
#
# The skill is named `think`; the product/CLI it drives is `devague` (the spec→plan
# half lives in the sibling /spec-to-plan skill, which drives `devague plan`).
# devague turns a vague feature idea into a buildable spec by working backwards.
# This wrapper is the agent-facing operator for the deterministic devague CLI:
# it resolves the CLI portably and forwards every move verbatim — including the
# `status` verb the CLI internalised in 0.11.0 (devague#30/#31), which reads the
# convergence gate and names the recommended next move.
#
# Origin: authored and maintained in agentculture/devague. guildmaster pulls
# this skill from devague and re-broadcasts it to the rest of the AgentCulture
# mesh, so it is written to run anywhere — portable bash, no devague-checkout
# assumptions.
#
# Frames persist under .devague/ in the current directory, so run from the repo
# you are speccing.

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
think.sh — drive devague's working-backwards idea→spec engine (the /think skill).

Usage:
  think.sh <move> [args...]    forward a devague move
  think.sh status [--frame S]  where the frame stands + the next move
  think.sh help                this help

Moves (forwarded to the devague CLI; run `devague learn` for the full method):
  new          start a frame from the announcement ("pretend it shipped")
  capture      record + classify a claim (--kind audience|after_state|...)
  interrogate  pressure-test a claim (--honesty / --hard-question / --risk)
  confirm      confirm a claim or honesty condition  (USER-only decision)
  reject       reject a claim or honesty condition
  park         record open vagueness instead of forcing an answer
  converge     check whether the frame can export a spec
  export       write the buildable spec (only after converge passes)
  status       where the frame stands + the recommended next move
  show / list  render a frame / list frames
  learn        teach the method   |   explain <move>  explain one move

Frames persist under .devague/ in the current directory — run from the repo
you are speccing. Results go to stdout, diagnostics to stderr; pass --json to
any move for structured output.

Note: every move — including `status` — is forwarded verbatim to the devague
CLI, so new devague moves work without editing this script. (`status` was
internalised into the CLI in devague 0.11.0; it is no longer wrapper-only.)

Next leg: once a frame exports a spec, hand off to the /spec-to-plan skill
(`devague plan ...`) to turn that spec into a buildable plan.
EOF
}

main() {
    case "${1:-help}" in
        help | -h | --help)
            usage
            return 0
            ;;
        *)
            # Forward every move to the CLI verbatim (including --version, the
            # internalised `status` verb, and any future devague move), so its
            # own parser owns the surface.
            resolve_devague
            exec "${DEVAGUE[@]}" "$@"
            ;;
    esac
}

main "$@"
