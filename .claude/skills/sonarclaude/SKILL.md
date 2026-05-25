---
name: sonarclaude
description: >
  Query SonarCloud API for code quality data. Use when: checking quality gate status,
  fetching code issues or security hotspots, reviewing metrics (coverage, bugs, code smells),
  or the user says "sonar", "quality gate", "code quality", "sonarclaude".
---

# SonarClaude

Query SonarCloud projects for quality gate status, issues, metrics, and security hotspots.

## Prerequisites

The script requires the following tools on `PATH`:

- `bash`
- `curl` — talks to the SonarCloud REST API
- `jq` — parses responses and URL-encodes accept comments

## Environment

Requires `SONAR_TOKEN` environment variable. Set the project key per-repo via
the `SONAR_PROJECT` environment variable (or pass `--project KEY` on each
invocation).

## Usage

```bash
# Quality gate status (pass/fail)
bash .claude/skills/sonarclaude/scripts/sonar.sh status

# List issues (bugs, vulnerabilities, code smells)
bash .claude/skills/sonarclaude/scripts/sonar.sh issues

# Filter issues by severity and type
bash .claude/skills/sonarclaude/scripts/sonar.sh issues --severity CRITICAL --type BUG

# Key metrics (coverage, bugs, code smells, duplication, LOC)
bash .claude/skills/sonarclaude/scripts/sonar.sh metrics

# Security hotspots
bash .claude/skills/sonarclaude/scripts/sonar.sh hotspots

# Accept (won't-fix) an OPEN issue with a rationale comment
bash .claude/skills/sonarclaude/scripts/sonar.sh accept \
  --issue AZ3Ep83i4ywi8V_l99p0 \
  --comment "Pushback: rule premise doesn't fit our test fixture pattern."

# Different project
bash .claude/skills/sonarclaude/scripts/sonar.sh status --project OtherOrg_OtherProject

# Raw JSON output
bash .claude/skills/sonarclaude/scripts/sonar.sh issues --raw

# Limit results
bash .claude/skills/sonarclaude/scripts/sonar.sh issues --limit 10
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--project` | `$SONAR_PROJECT` | SonarCloud project key |
| `--severity` | all | Filter: `BLOCKER`, `CRITICAL`, `MAJOR`, `MINOR`, `INFO` |
| `--type` | all | Filter: `BUG`, `VULNERABILITY`, `CODE_SMELL` |
| `--limit` | `25` | Max results returned |
| `--raw` | off | Output raw JSON instead of formatted summary |
| `--issue` | — | Issue key (required for `accept`) |
| `--comment` | — | Rationale comment (required for `accept`) |

## When to use `accept`

Sonar rules occasionally flag patterns where the rule's premise does not fit the
code (e.g. test fixtures with intentional bad-password literals, contradictory
rule pairs like S7494 vs S7500). After deciding pushback in PR review, run
`sonar.sh accept` with a rationale comment instead of leaving the issue OPEN —
this moves it to ACCEPTED/WONTFIX in SonarCloud history (visible to future
maintainers) while clearing the quality-gate signal. Always include a `--comment`
explaining why; silent dismissals are not acceptable curation.

Do not call SonarCloud's HTTP API directly from other skills or one-off
scripts — `sonar.sh accept` is the supported entry point so the rationale
flow stays consistent.
