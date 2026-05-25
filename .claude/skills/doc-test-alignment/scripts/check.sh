#!/usr/bin/env bash
# doc-test-alignment skill — entry point.
#
# STUB: the real workflow is not implemented yet. This script exists so the
# AgentCulture skills convention is satisfied (every skill ships an executable
# entry-point script); when the real implementation lands here, it must
# satisfy the contract documented in ../SKILL.md.
#
# Exits 2 (EXIT_USER_ERROR-ish for "you asked for something that isn't
# wired up yet") so callers can tell the difference between "checks passed"
# (would be 0) and "stub".

set -euo pipefail

cat >&2 <<'EOF'
doc-test-alignment: not yet implemented.

This skill is a stub; the contract for what `check.sh` will assert lives in
.claude/skills/doc-test-alignment/SKILL.md. Until the implementation lands,
treat any green exit code from this script as a bug.

Roadmap: see CLAUDE.md ("Roadmap (CLI surface)") and docs/sibling-pattern.md.
EOF
exit 2
