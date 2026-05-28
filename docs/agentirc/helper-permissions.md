---
layout: default
title: Helper Permission Broker
parent: AgentIRC
nav_order: 93
---

# Helper Permission Broker

The permission broker lets a regular Claude Code session (the **boss**) act as the
human-in-the-loop authority for tool calls made by helper agents it spawns on a
local Culture mesh. Helpers run headless (no terminal), so the boss approves or
denies their tool use over a file-backed queue instead of a terminal prompt.

Design spec: `docs/superpowers/specs/2026-05-28-helper-boss-permission-broker.md`

## Why a broker

Helper daemons run the Claude Agent SDK in `bypassPermissions` mode — the only
mode that doesn't block on a terminal prompt nobody is reading. That mode turns
off the *UI* prompt path, not the programmatic `can_use_tool` callback. The
broker is that callback: it decides allow/deny per tool call, consulting a
per-helper policy file and, when no rule matches, the boss.

## File layout

All under `CULTURE_HOME` (default `~/.culture`):

| Path | Purpose |
|---|---|
| `perm-policy/<nick>.yaml` | Per-helper auto-allow / auto-deny rules. Its presence is what makes a helper *boss-supervised*. |
| `perm-queue/<id>.json` | A pending request the helper wrote and is blocking on. |
| `perm-decisions/<id>.json` | The boss's verdict; the helper consumes it and unblocks. |

`CULTURE_HOME` is honored by both the broker (Python) and the boss scripts. Set
it consistently if you override it.

## Policy file

`spawn-helper.sh` seeds a default policy with safe-read auto-allows:

```yaml
auto_allow:
  - tool: Read
  - tool: Glob
  - tool: Grep
  - tool: Bash
    input_regex: '^(ls|cat|head|tail|wc|file|stat|pwd|which|rg|grep|find|tree|git (status|log|diff|blame|show)|gh (.* )?(list|view))(\s|$)'
  - tool: Write
    input_regex: '/handoff/<nick>\.md$'   # context-handoff write, see helper-context-handoff
auto_deny: []
require_approval:
  - tool: Edit
  - tool: Write
  - tool: 'mcp__.*'
  - tool: Bash
```

Matching: `auto_deny` is checked before `auto_allow`; first match wins. A tool
pattern containing regex metacharacters is matched as a regex (`re.fullmatch`),
otherwise by exact string. `input_regex` (optional) is `re.search` against a
per-tool projection — `Bash`→command, `Edit`/`Write`→file_path, `mcp__*`→JSON
of the input. Anything not matched falls through to the boss.

The policy is re-read on every gate call (mtime-checked), so `approve.sh … always`
takes effect immediately, including after a session resume.

## Lifecycle

1. The SDK fires `can_use_tool(tool, input, ctx)` before a tool runs.
2. The broker matches the policy. `allow`/`deny` return immediately — no round trip.
3. Otherwise it writes `perm-queue/<id>.json` and blocks on `perm-decisions/<id>.json`,
   polling every 250 ms. **There is no timeout** — the helper waits indefinitely.
4. When the boss writes a decision, the helper reads it, deletes both files, and
   returns allow/deny to the SDK. `scope: always` first appends a sticky rule to
   the policy file.
5. If the helper task is cancelled mid-wait (e.g. `close-helper.sh`), the broker
   deletes its in-flight request file and re-raises the cancellation.

## Boss workflow

```bash
pending-perms.sh                 # list all pending requests across helpers
pending-perms.sh --full <id>     # full request JSON for one id
approve.sh <id>                  # one-shot allow
approve.sh <id> always           # sticky allow for this exact tool name
approve.sh <id> always 'Bash'    # sticky allow for a tool pattern
deny.sh <id> [reason...]         # deny; reason returned to the model
watch-perms.sh                   # live tail of new requests (side terminal)
policy.sh list <name>            # inspect a helper's policy
policy.sh allow|deny <name> <tool> [input_regex]
policy.sh reset <name>           # reseed default policy
cleanup-stale-perms.sh           # GC requests whose helper is gone + orphan decisions
```

`status.sh` and `read-replies.sh` surface a `[N pending perms]` count so the boss
notices requests during its normal flow.

## Atomicity and races

- Decision and request files are written via tempfile + `os.replace` — readers
  never see a partial file.
- `approve.sh`/`deny.sh` use `O_CREAT | O_EXCL` on the destination so two
  concurrent boss invocations can't both decide one request (first writer wins).
- A decision written after the helper already consumed its verdict becomes an
  orphan; `cleanup-stale-perms.sh` garbage-collects orphans and requests whose
  helper daemon is no longer running.

> **Known limitation:** `os.replace` is atomic on local POSIX filesystems
> (macOS/Linux) but not on NFS or some FUSE mounts. Keep `CULTURE_HOME` on a
> local disk.

## Backend support

| Backend | Broker |
|---|---|
| Claude | **Full** — native `can_use_tool`. |
| Copilot | Audit-only in this release (the Copilot SDK `PermissionHandler` signature could not be verified at build time). |
| Codex | Audit-only — no per-tool callback in the app-server protocol. |
| ACP | Audit-only — no per-tool callback in the ACP surface. |

Audit-only backends still record every agent message to
`audit/<nick>.jsonl` and every daemon action to `daemon-log/<nick>.jsonl`, so the
boss retains visibility even where synchronous approval isn't possible.

## Security model

Same-machine, same-UID only. The broker directories are created `0700` with
`0600` files. Anyone able to write to `perm-decisions/` as the user already has
shell access to the user's home directory; the broker is a *boss-as-human*
mechanism, not a sandbox.

## Standalone agents

An agent **without** a `perm-policy/<nick>.yaml` runs exactly as before this
feature: `can_use_tool` is not set, no broker is involved, no approval is
required. Only boss-spawned helpers (which get a seeded policy file) are
supervised. Existing mesh agents are unaffected except that they now inherit the
boss's user-level tool surface — see [Helper Tool Inheritance](helper-tool-inheritance.md).
