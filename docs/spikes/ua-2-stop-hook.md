# UA-2 spike — Stop hook `decision: "block"` semantics

**Status:** design-complete; offline hook-script behavior verified; live in-CC verification deferred to the human or to the Phase 4.5 implementation agent.

**Owner:** culture fork-rearch agent (Opus 4.7).

**Plan reference:** `docs/superpowers/specs/2026-06-03-mesh-rearchitecture-plan.md`, Phase 0 Task 0.4 (UA-2 spike). Determines Phase 4.5 implementation strategy.

**Companion plan rows:** UA-2 (line 282), Phase 4.5 Stop hook end-of-turn drain (line 751), Phase 4.7 PreToolUse perm-interrupt + recursion-avoidance note that explicitly back-references this idempotency pattern (line 753), risk note that UA-2 is the only unverified-but-load-bearing assumption in the plan (line 831).

## 1. What we are verifying

The fork-rearchitecture plan rests on one CC behavior we have not personally exercised end-to-end:

> When CC's assistant turn is about to end, a user-scope Stop hook can return `{"decision": "block", "reason": "<text>"}` and CC will (a) honor the block, (b) re-enter another assistant turn, and (c) carry `<text>` as context for that next turn.

Plus one safety property:

> The hook's stdin payload includes `stop_hook_active: bool`. When the hook is firing because of its own prior block, `stop_hook_active` is `true`; the hook MUST return no decision in that case, otherwise CC loops forever.

If either property is wrong, Phase 4.5's "end-of-turn IRC-queue drain" needs a different mechanism (polling fallback, or fold inbound into the AD-1 PreToolUse interrupt pillar).

## 2. The throwaway plugin

Living at `/private/tmp/culture-stop-spike/` (gitignored — this directory is the spike vehicle, not the deliverable; this report is the deliverable).

### 2.1 `plugin.json`

```json
{
  "name": "culture-stop-spike",
  "description": "UA-2 spike: verifies Stop hook decision:block + stop_hook_active idempotency. Throwaway.",
  "version": "0.0.1",
  "author": { "name": "culture fork-rearch" },
  "hooks": {
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_DIR}/hooks/stop.py\"",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

The `${CLAUDE_PLUGIN_DIR}` substitution is the documented CC-provided variable for plugin-relative paths, so the same manifest works whether the plugin is loaded from a marketplace, a `directory:` source, or symlinked into `~/.claude/plugins/`.

### 2.2 `hooks/stop.py`

```python
#!/usr/bin/env python3
"""UA-2 spike Stop hook."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

LOG_PATH = Path("/tmp/culture-stop-spike.log")
FAKE_REASON = "[SPIKE] Mesh teammate fork-rearch-qa just DM'd you: ping"


def _log(line: str) -> None:
    try:
        with LOG_PATH.open("a") as fh:
            fh.write(f"{datetime.now().isoformat()} {line}\n")
    except OSError:
        pass


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        _log(f"bad-json err={exc!r} raw={raw!r}")
        return 0

    stop_hook_active = bool(payload.get("stop_hook_active", False))
    session_id = payload.get("session_id", "<no-session>")
    _log(
        f"fired session_id={session_id} stop_hook_active={stop_hook_active} "
        f"keys={sorted(payload.keys())}"
    )

    if stop_hook_active:
        # Idempotency guard: CC re-entered because of OUR prior block.
        # Stop normally now — else infinite loop.
        _log("idempotent-passthrough: stop_hook_active=true, exiting 0")
        return 0

    decision = {"decision": "block", "reason": FAKE_REASON}
    sys.stdout.write(json.dumps(decision))
    sys.stdout.flush()
    _log(f"blocked: emitted decision={decision}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Key design properties:

- **Reads stdin once.** CC sends the hook payload as a single JSON object on stdin.
- **Idempotency guard at the top.** `stop_hook_active=true` short-circuits to `exit 0` with empty stdout, which is the documented "no decision, proceed normally" signal. This is the same pattern Phase 4.7's PreToolUse hook uses for `mesh `-prefixed tool calls — see review iter-3 C-1 from agent 7 and the cross-reference at plan line 753.
- **Block path emits a JSON object to stdout.** `{"decision": "block", "reason": "<text>"}`. CC's contract is that on `decision: "block"`, the assistant turn does not terminate; instead CC enters a new turn whose context starts with the `reason` field as a system-reminder-class message.
- **Best-effort logging to `/tmp/culture-stop-spike.log`.** Lets the human observe each firing without staring at CC's debug pane. Write failures are swallowed because a logging failure must never break CC.
- **Malformed input does not break CC.** Bad JSON → log it, exit 0 silently.

### 2.3 Offline behavioral check (already executed by this agent)

The hook script was exercised against three synthetic stdin payloads. Results:

| Stdin | Stdout | Exit | Log line |
|---|---|---|---|
| `{"session_id":"test-1","stop_hook_active":false,...}` | `{"decision": "block", "reason": "[SPIKE] Mesh teammate fork-rearch-qa just DM'd you: ping"}` | 0 | `fired ... stop_hook_active=False` → `blocked: emitted decision=...` |
| `{"session_id":"test-2","stop_hook_active":true,...}` | _(empty)_ | 0 | `fired ... stop_hook_active=True` → `idempotent-passthrough` |
| `not-json` | _(empty)_ | 0 | `bad-json err=JSONDecodeError(...)` |

All three branches behave as specified. The remaining unknown — and the whole point of the spike — is whether CC itself honors the emitted decision object. That requires a live CC session and is documented in section 3.

## 3. Live test procedure

This part needs a human at a real CC terminal. The agent author of this spike does not have an in-process CC instance it can interrogate.

### 3.1 Install the plugin

Two options; the second is preferred because it leaves no junk in `~/.claude/`.

**A. Symlink into the user plugins dir:**

```bash
ln -s /private/tmp/culture-stop-spike ~/.claude/plugins/culture-stop-spike
```

**B. Register `/private/tmp/culture-stop-spike` as a `directory:` source in a throwaway marketplace.** Skip if option A worked.

Restart CC after installing so the manifest is picked up.

### 3.2 Sanity check that the hook is wired

```bash
tail -F /tmp/culture-stop-spike.log
```

Leave that tailing in another terminal. Open CC and type any prompt that does not require tools, e.g. `say hi`. As soon as CC's assistant turn would naturally end, the log should show a `fired ... stop_hook_active=False` → `blocked: emitted decision=...` pair.

If nothing appears in the log, the hook is not wired — check the plugin install, `${CLAUDE_PLUGIN_DIR}` resolution, and that the hook script is executable.

### 3.3 Verify block + re-entry + reason-as-context (the actual UA-2 check)

In the same CC session, type:

```
say hi briefly
```

Expected sequence:

1. CC produces a short assistant message ("hi" or similar).
2. CC's turn would normally end here. Instead, the Stop hook fires (first log line).
3. CC enters a new assistant turn.
4. Inside that next turn, CC should make some reference to having seen the fake DM — e.g. it tries to respond to "fork-rearch-qa", asks who that is, acknowledges the ping, or otherwise demonstrates that the `reason` string entered its context. If CC is silent and just ends again, the second log line should show `stop_hook_active=True` → `idempotent-passthrough`, which still proves the block was honored but means CC did not surface the reason to the user — note that as a partial-pass.

### 3.4 Verify idempotency (the infinite-loop guard)

Re-run the same prompt. Confirm in the log that the SECOND firing of the hook in the same Stop-cycle carries `stop_hook_active=True` and that the script exits with empty stdout. Confirm CC actually stops after that second firing instead of looping forever.

If the second firing somehow comes through with `stop_hook_active=False` again, the idempotency property is broken on this CC build and Phase 4.5 needs an external once-per-turn latch (e.g., a marker file under `~/.culture/`).

### 3.5 Capture results in the "Findings" section of this report

After running the live procedure, edit Section 4 below in place. Replace "PENDING" with "PASS" / "PASS-PARTIAL" / "FAIL" and paste the relevant log excerpt + a 1-sentence note on what CC actually said in the re-entered turn.

## 4. Findings

### 4.1 Offline hook-script behavior — PASS

All three stdin paths (block, idempotent passthrough, malformed-input) behaved as designed. Logged in `/tmp/culture-stop-spike.log` during this spike session; see section 2.3 for the matrix.

### 4.2 Does CC honor `decision: "block"` and re-enter? — PENDING (live test)

Requires the section 3.3 procedure. Until then, the Phase 4.5 design depends on the documented CC contract for `Stop` hooks; that contract is what the plan cites at line 741.

### 4.3 Does `stop_hook_active` idempotency work? — PENDING (live test)

Requires the section 3.4 procedure.

### 4.4 Gotchas surfaced during design

- **`${CLAUDE_PLUGIN_DIR}` quoting.** If the plugin path contains spaces, the command-array form must keep the variable inside double quotes — which the manifest already does.
- **`python3` on PATH.** Phase 0.3 of the same plan calls out the PATH-check as an acceptance gate. If `python3` is not on PATH in CC's hook subprocess environment, the hook silently never runs. The live-test procedure's section 3.2 tail-the-log step is the canary for this.
- **Best-effort logging only.** The hook MUST NOT block CC on a log-write failure (e.g., `/tmp` full). The script catches `OSError` around the log write for exactly this reason.
- **Empty-`reason` failure mode.** If for any reason `FAKE_REASON` were empty, CC's contract is ambiguous — some hook implementations would treat that as "no reason given, proceed normally". The production hook in Phase 4.5 must guarantee a non-empty reason when emitting `decision: "block"` (the IRC-event drain is naturally non-empty, but a defensive check is warranted).
- **No JSON schema validation.** The spike hook accepts any payload shape and just reads `stop_hook_active`. The Phase 4.5 production hook should at minimum validate `hook_event_name == "Stop"` and skip on any other event, so a future CC change that reuses the same script for another hook does not surprise it.
- **Recursion avoidance pattern is now established for Phase 4.7.** The `stop_hook_active` short-circuit at the top of the script is the literal pattern Phase 4.7's PreToolUse hook copies, with `name.startswith('mesh ')` substituting for `stop_hook_active`. The mental model is identical: "if I am about to act on my own prior side effect, return no decision".

### 4.5 Fallback design if the live test fails

If 4.2 (block + re-entry) fails on the shipping CC version:

- **Plan B1: poll instead of drain.** Phase 4.5 changes from "Stop hook drains queue at end of turn" to "Phase 5.4's watchdog polls the IRC queue every N seconds and injects via PreToolUse on the next tool call". Loses end-of-turn freshness; gains independence from any Stop-hook contract.
- **Plan B2: fold inbound into AD-1.** Treat every inbound DM / mention as a perm-request-shaped event that fires the PreToolUse interrupt (which the plan already verifies separately via Phase 4.7). Single hook surface, one less place to break.

If 4.2 passes but 4.3 (idempotency) fails:

- **Plan C: external once-per-turn latch.** Touch a marker file (e.g., `~/.culture/cc-stop-latched-<session_id>`) on first fire; check-and-bail on subsequent fires within the same session. Sweep the marker on `SessionStart`. Uglier than the built-in `stop_hook_active` flag but functionally equivalent.

## 5. Cleanup

After the live test concludes and findings are recorded:

```bash
rm -rf /private/tmp/culture-stop-spike
rm ~/.claude/plugins/culture-stop-spike  # if symlinked
rm /tmp/culture-stop-spike.log
```

The artifact that survives is THIS report.
