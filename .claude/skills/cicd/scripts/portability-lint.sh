#!/usr/bin/env bash
# Portability lint: catch path leaks and per-user config dependencies in
# committed docs/configs before they ship in a PR. Steward's recurring bug
# class.
#
# Usage: portability-lint.sh [--all]
#   default: lint files modified vs HEAD (staged + unstaged)
#   --all:   lint all tracked files
#
# Exits 0 if clean, 1 if any leak is found.

set -euo pipefail

mode="${1:-diff}"
case "$mode" in
    --all) files=$(git ls-files -- ':(exclude)*.lock') ;;
    diff|--diff) files=$(git diff --diff-filter=AMR --name-only HEAD -- ':(exclude)*.lock') ;;
    *) echo "Usage: $(basename "$0") [--all]" >&2; exit 2 ;;
esac

[ -z "$files" ] && { echo "(no files to check)"; exit 0; }

# ----- Check 1: hard-coded /home/<user>/... paths -----
hits1=$(echo "$files" | xargs -r grep -nE '/home/[a-z][a-z0-9_-]+/' 2>/dev/null || true)

# ----- Check 2: per-user dotfile *config* refs in committed docs/configs -----
# Carve-outs (allowed, NOT flagged):
#   - ~/.claude/skills/<x>/scripts/   vendored tool calls
#   - ~/.culture/                     Culture mesh data this skill is supposed to read
md_yaml=$(echo "$files" | grep -E '\.(md|ya?ml|toml|json|jsonc)$' || true)
if [ -n "$md_yaml" ]; then
    hits2=$(echo "$md_yaml" | xargs -r grep -nE '~/\.[A-Za-z]' 2>/dev/null \
        | grep -vE '~/\.claude/skills/[^[:space:]"]+/scripts/' \
        | grep -vE '~/\.culture/' \
        || true)
else
    hits2=""
fi

fail=0
if [ -n "$hits1" ]; then
    echo "❌ Hard-coded /home/<user>/ paths:"
    echo "$hits1" | sed 's/^/    /'
    echo "   Fix: use ../sibling, repo URL, or \$WORKSPACE/sibling instead."
    fail=1
fi
if [ -n "$hits2" ]; then
    [ "$fail" -eq 1 ] && echo
    echo "❌ Per-user ~/.<dotfile> config refs in committed doc/config:"
    echo "$hits2" | sed 's/^/    /'
    echo "   Allowed carve-outs: ~/.claude/skills/.../scripts/ (tool calls), ~/.culture/ (mesh data)."
    echo "   Otherwise: commit a repo-local config or document a portable lookup."
    fail=1
fi

[ "$fail" -eq 0 ] && echo "✓ portability lint clean ($(echo "$files" | wc -l | tr -d ' ') files checked)"
exit $fail
