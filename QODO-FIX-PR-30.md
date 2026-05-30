# Qodo Fix Report — PR #30

## Findings Summary

| # | Finding | Status | Fix |
|---|---------|--------|-----|
| 1 | Direct `_on_history()` test call (rule violation) | CLOSED | Converted to real-server integration tests |
| 2 | CRLF IRC command injection | RESOLVED (prior commit 431e102) | Already fixed |
| 3 | Harness transport claude-coupled imports | CLOSED | Added TEMPLATE comments + backend="harness" default |
| 4 | Repeated HISTORY backfill duplicates | CLOSED | Early-return guard in join_channel() for all 5 backends |

## Details

### Finding 1 — Tests converted to real TCP
- `test_history_handler_skips_system_nicks` → `test_history_backfill_filters_system_nicks` (real server)
- `test_history_handler_skips_own_messages` → `test_history_backfill_filters_own_messages` (real server)
- Added `test_duplicate_join_does_not_duplicate_history` (real server)

### Finding 3 — Harness template decoupled
- `packages/agent-harness/irc_transport.py`: Added `# TEMPLATE: replace "claude"` comments on import lines
- Changed `backend` default from `"claude"` to `"harness"`

### Finding 4 — Duplicate join guard
- `join_channel()` now returns early if `channel in self.channels`
- Applied to all 5 backends (claude, acp, codex, copilot, harness)
- Prevents duplicate HISTORY RECENT queries and buffer duplication

## Test Results
94 tests pass (17 transport + 47 boss CLI + 30 IRC targets)
