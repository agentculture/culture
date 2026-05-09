# Coverage baseline

This doc tracks culture's coverage floor and the per-domain growth path through Phase 0a (cultureagent extraction).

## Baseline (locked 2026-05-09)

- **Project-wide:** 56.86% (7556/13289 lines)
- **Integration-only baseline (`tests/test_integration_layer5.py`):** 15.95%
- **Harness-module-only:** 56% of 5304 lines

The baseline was measured by `uv run pytest -n auto --cov=culture --cov-report=xml:coverage.xml`. Full audit: [`docs/superpowers/notes/2026-05-09-cultureagent-coverage-audit.md`](superpowers/notes/2026-05-09-cultureagent-coverage-audit.md) (lands in a follow-up PR).

## Locks

- **pytest CI gate:** [`tool.coverage.report] fail_under = 56`](../pyproject.toml) in `pyproject.toml`. Drops below 56% fail the PR locally / in CI before SonarCloud reports back.
- **SonarCloud Quality Gate:** `sonar.qualitygate.wait=true` in [`sonar-project.properties`](../sonar-project.properties) blocks CI on the gate decision. Gate threshold managed in SonarCloud's project settings (out-of-tree).
- **CI scanner:** `.github/workflows/tests.yml` runs `SonarSource/sonarqube-scan-action` after pytest, uploading `coverage.xml` to the SonarCloud project (`OriNachum_culture`). Fork PRs without `SONAR_TOKEN` skip the scan cleanly.

## Per-domain growth path (Phase 0a)

| Domain | Today | After Phase 0a (projected) | Gating done by |
|---|---|---|---|
| `culture/clients/shared/` | ~58% | ~95% | Tasks 2, 3, 4, 5, 6 |
| `culture/clients/claude/` | ~85% | ~90% | Tasks 6, 7, 8 |
| `culture/clients/codex/` | ~25% | ~75% | Tasks 7, 8 (parameterized) |
| `culture/clients/copilot/` | ~25% | ~75% | Tasks 7, 8 (parameterized) |
| `culture/clients/acp/` | ~25% | ~70% | Tasks 7, 8 (parameterized) |
| **Project-wide** | **56.86%** | **~73%** | Sum of above |

After each Phase 0a task PR lands, the `fail_under` value in `pyproject.toml` ratchets up by the measured delta. Task 9 (closeout) adds per-domain enforcement — e.g., a separate CI step running `uv run pytest --cov=culture/clients --cov-fail-under=80` — when domain floors are at their post-Phase-0a values.

## Why "Coverage on New Code" alone isn't enough here

SonarCloud's default gate is "Coverage on New Code ≥ 80%" — protects new lines from being added without tests. That stays on as a regression preventer, but it doesn't lock today's overall floor: a delete that removes tested code can raise overall percentage while leaving real coverage gaps. The pytest `fail_under = 56` plus manual ratchets per Task PR is the tighter floor.
