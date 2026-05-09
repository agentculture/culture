#!/usr/bin/env bash
set -euo pipefail

# Resolve the agent's nick for GitHub message signing.
# Order: first agent's `suffix` in <repo-root>/culture.yaml,
# then basename of the git repo root.
# Prints the nick to stdout. Always exits 0 — pr-reply.sh needs *some*
# nick to sign with — but if a culture.yaml exists and we couldn't
# extract a suffix from it, emits a stderr warning so a misconfigured
# manifest doesn't silently mask itself behind the basename fallback.

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$repo_root" ]]; then
    repo_root="$PWD"
fi

manifest="$repo_root/culture.yaml"

if [[ -f "$manifest" ]]; then
    if ! command -v python3 >/dev/null 2>&1; then
        echo "_resolve-nick: python3 not found; cannot parse $manifest, falling back to repo basename" >&2
    else
        nick="$(python3 - "$manifest" <<'PY' 2>/dev/null || true
import re, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    for raw in f:
        line = raw.rstrip("\n")
        m = re.match(r"^[\s-]*\s*suffix:\s*(\S+)", line)
        if m:
            print(m.group(1).strip("'\""))
            break
PY
)"
        if [[ -n "$nick" ]]; then
            printf '%s\n' "$nick"
            exit 0
        fi
        echo "_resolve-nick: $manifest exists but no suffix could be parsed; falling back to repo basename" >&2
    fi
fi

basename "$repo_root"
