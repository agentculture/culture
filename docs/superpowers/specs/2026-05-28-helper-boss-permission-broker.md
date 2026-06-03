---
title: "Helper Boss Permission Broker"
parent: "Design"
nav_order: 24
---

> **OBSOLETE NOTE (2026-06-03):** The **grant-ceiling** sections of
> this spec (any reference to `DEFAULT_BOSS_CEILING`,
> `boss-policy/<nick>.yaml`, `write_default_boss_ceiling`,
> `load_boss_ceiling`, `is_above_ceiling`, or the ceiling re-check
> inside `gate()`) are **obsolete as of Phase 5.2** of the
> [Mesh Rearchitecture plan](2026-06-03-mesh-rearchitecture-plan.md).
> A boss can grant a worker any tool the boss itself has; runtime tool
> authority is governed entirely by the worker's policy file. The new
> bypass-prevention surface is `BareStickyApproveRefusedError` raised
> by `_append_sticky_rule` when an `--always allow` rule for
> `Bash`/`Edit`/`Write`/`mcp__*` lacks an `input_regex` — see Phase 5
> of the rearch plan. The rest of this spec (broker file conventions,
> context-watermark handoff, daemon action log) remains current.

# Helper Boss Permission Broker

**Status:** Draft
**Date:** 2026-05-28
**Branch:** `feat/helper-boss-permission-broker`

> **Scope note (rev 2):** This PR ships three related boss-supervision pillars,
> aligned with the user during authoring:
>
> 1. **Permission broker** — boss-as-human approval for helper tool calls (the
>    bulk of this spec).
> 2. **Context-watermark handoff** — daemon self-monitors context usage; at 90%
>    it asks the agent to write a handoff for its post-compact self, compacts,
>    then reminds it to read the handoff. See
>    [Context-watermark handoff](#context-watermark-handoff).
> 3. **Daemon action log** — structured control-plane JSONL per agent, all
>    backends. See [Daemon action log](#daemon-action-log).
>
> All three share the same `~/.culture/` file conventions, `CULTURE_HOME`
> resolution, atomic-write discipline, and JSONL shapes.

## Problem

The `culture-boss` skill lets a regular Claude Code session ("boss") spawn helper Claude Code sessions on a local culture IRC mesh. Today, helpers run with:

```python
# culture/clients/claude/agent_runner.py:132-143
opts = ClaudeAgentOptions(
    model=self.model,
    cwd=self.directory,
    permission_mode="bypassPermissions",
    setting_sources=["project"],
)
```

Two consequences:

1. **Helpers don't inherit the boss's tools.** `setting_sources=["project"]` reads only the helper's `cwd` `.claude/` directory. The boss's user-level MCP servers (Gmail, Drive, Atlassian, Cloudflare, Context7, Playwright), skills (`~/.claude/skills/`), and plugins are unavailable to helpers.
2. **Helpers are silently autonomous.** `permission_mode="bypassPermissions"` means tool calls never prompt — and there is no other gating dial. A helper with a wider toolbox could autonomously send email, edit Drive files, or run arbitrary Bash, with the boss only seeing the IRC chatter after the fact.

The user wants: *boss is the human-in-the-loop*. Helpers should inherit the boss's full tool surface, but every dangerous tool call should route through the boss for approval — same UX a human gets in standard Claude Code, transposed onto IRC.

## Why `bypassPermissions` stays on

`permission_mode` controls the **terminal UI prompts**. Helpers are headless daemon processes with no tty; the SDK's other modes (`default`, `acceptEdits`, `plan`) all expect a human reading stdin. `bypassPermissions` is the only mode that doesn't block on a prompt that nobody is reading.

The actual safety lever is independent: `ClaudeAgentOptions.can_use_tool` (verified at `claude_agent_sdk/types.py:1075`). It's a programmatic callback fired **before every tool invocation**, returning `PermissionResultAllow` or `PermissionResultDeny`. It is honored even in `bypassPermissions` mode — that mode just turns off the *UI* prompt path, not the programmatic callback.

The signature (`claude_agent_sdk/types.py:159-161`):

```python
CanUseTool = Callable[
    [str, dict[str, Any], ToolPermissionContext],
    Awaitable[PermissionResult]
]
```

This is the SDK hook we use to make the boss the gatekeeper.

### SDK consequence: `can_use_tool` forces streaming-mode prompts

`claude_agent_sdk/_internal/client.py:54-60` enforces:

```python
if options.can_use_tool:
    if isinstance(prompt, str):
        raise ValueError(
            "can_use_tool callback requires streaming mode. "
            "Please provide prompt as an AsyncIterable instead of a string."
        )
```

The current `AgentRunner` at `culture/clients/claude/agent_runner.py:173-176` calls `query(prompt=prompt, …)` with `prompt: str` from `_prompt_queue: asyncio.Queue[str]`. **Adding `can_use_tool` without converting the prompt to an `AsyncIterable[dict]` raises immediately on first turn.**

The implementation must wrap each queued prompt at the `_process_turn` call site:

```python
async def _as_stream(text: str) -> AsyncIterable[dict[str, Any]]:
    yield {"type": "user", "message": {"role": "user", "content": text}}

async for message in query(
    prompt=_as_stream(prompt),  # was: prompt=prompt
    options=self._make_options(),
):
    ...
```

This change is required in Phase 2 alongside the callback wiring. No other call sites change — `_prompt_queue` stays `asyncio.Queue[str]`; only the SDK-call surface adapts.

### SDK consequence: no timeout on the callback

`claude_agent_sdk/_internal/query.py:258-262` calls `await self.can_use_tool(...)` with no timeout wrapper. The CLI subprocess does not impose its own deadline either. "Queue and wait indefinitely" is structurally supported by the SDK.

### SDK consequence: deny without interrupt

`claude_agent_sdk/types.py:154` defines `PermissionResultDeny.interrupt: bool = False`. The broker returns `interrupt=False` so a deny scopes to the single tool call (model receives the denial in its `tool_result` and decides what to do next) rather than aborting the entire turn. Spec mandates `interrupt=False` everywhere unless future work explicitly opts into hard abort.

## Architecture overview

```text
┌──────────────────────┐                ┌─────────────────────┐
│ Boss Claude Code     │                │ Helper daemon       │
│ session (interactive)│                │ (culture-agent-*)   │
│                      │                │                     │
│ - pending-perms.sh   │   reads /      │ - AgentRunner       │
│ - approve.sh         │   writes       │ - can_use_tool=     │
│ - deny.sh            │ ◄────────────► │     broker.gate()   │
│ - watch-perms.sh     │                │                     │
└──────────────────────┘                └─────────────────────┘
              │                                    │
              │                                    │
              ▼                                    ▼
   ┌──────────────────────────────────────────────────┐
   │  ~/.culture/                                     │
   │   ├── perm-queue/<id>.json     (requests)        │
   │   ├── perm-decisions/<id>.json (verdicts)        │
   │   ├── perm-policy/<nick>.yaml  (auto-rules)      │
   │   └── audit/<nick>.jsonl       (after-the-fact)  │
   └──────────────────────────────────────────────────┘
```

Three concerns separated:

| Concern | Mechanism | Files |
|---|---|---|
| **Tool inheritance** | Widen `setting_sources` (Claude); populate `skill_directories` (Copilot) | `culture/clients/claude/agent_runner.py`, `culture/clients/copilot/agent_runner.py` |
| **Synchronous boss approval** | `can_use_tool` callback (Claude) / `PermissionHandler` (Copilot) wired to a file-backed broker | New `culture/clients/_perm_broker.py` |
| **Post-hoc visibility** | Per-helper JSONL audit log of every AssistantMessage | All four `daemon.py` files, hooked in existing `_on_agent_message` |

## File layouts

All paths under `~/.culture/`. Directories are created on demand by `ensure-mesh.sh` and `spawn-helper.sh`.

### `perm-queue/<id>.json` — helper request

```json
{
  "id": "req-2026-05-28T14-32-17-abc12",
  "helper_nick": "local-research",
  "tool_name": "Bash",
  "input": {"command": "gh pr create --title ...", "description": "..."},
  "context": {
    "task_channel": "#task-research",
    "session_id": "uuid-from-sdk"
  },
  "created_at": "2026-05-28T14:32:17.123Z"
}
```

- Filename = `<id>.json`. ID format: `req-<ISO8601 with dashes>-<5 hex chars>`. Sortable by creation order, unique under timer collision.
- Written atomically via `tempfile + os.replace`. Helpers `os.fsync` before the rename so a kernel-level crash doesn't leave a partial file.
- Persists until the matching decision file is consumed.

### `perm-decisions/<id>.json` — boss verdict

```json
{
  "id": "req-2026-05-28T14-32-17-abc12",
  "verdict": "allow",
  "scope": "once",
  "reason": "ok",
  "decided_by": "boss",
  "decided_at": "2026-05-28T14:32:21.456Z"
}
```

- `verdict`: `"allow" | "deny"`.
- `scope`: `"once" | "always"`. If `"always"`, the decision is appended to `perm-policy/<helper_nick>.yaml` before the file is written.
- `reason`: optional free text. Returned to the SDK's `PermissionResultDeny.message` on deny. Surfaces in the model's tool_result.
- Written atomically via `tempfile + os.replace`. The helper watches for this file's appearance.

### `perm-policy/<nick>.yaml` — per-helper sticky rules

Seeded by `spawn-helper.sh` with safe-read defaults; mutated by `approve.sh ... always`.

```yaml
# auto_allow: rules matched before any IRC round-trip — return allow immediately
auto_allow:
  - tool: Read
  - tool: Glob
  - tool: Grep
  - tool: Bash
    input_regex: '^(ls|cat|head|tail|wc|file|stat|pwd|which|rg|grep|find|tree|git (status|log|diff|blame|show)|gh (.* )?(list|view))(\s|$)'

# auto_deny: rules matched before any IRC round-trip — return deny immediately
auto_deny: []

# require_approval: explicitly route to boss (default fallthrough for anything
# not matched above is also "require approval", so this section is informational
# unless you want to surface specific tool classes explicitly)
require_approval:
  - tool: Edit
  - tool: Write
  - tool: 'mcp__.*'
  - tool: Bash  # matches everything not in auto_allow's regex
```

Matching order: `auto_deny` → `auto_allow` → require approval (fallthrough). First match wins. `input_regex` is Python `re.search`-style; absence of `input_regex` means "any input matches".

For MCP tools, `tool` is matched as a regex when it contains regex metacharacters; otherwise an exact string match.

### Path resolution — `CULTURE_HOME`

All four directories (`perm-queue/`, `perm-decisions/`, `perm-policy/`, `audit/`) are rooted under a single `CULTURE_HOME`. Every Python module that touches these paths uses a single helper:

```python
# culture/clients/_perm_broker.py
def _culture_home() -> str:
    return os.environ.get("CULTURE_HOME", os.path.expanduser("~/.culture"))
```

Every bash script uses the parallel:

```bash
CULTURE_HOME="${CULTURE_HOME:-$HOME/.culture}"
```

Tests override via `monkeypatch.setenv("CULTURE_HOME", str(tmp_path))`; the broker, scripts, and `spawn-helper.sh` all see the same root.

**Caveat:** systemd/launchd-spawned agent daemons do not inherit the user's shell `CULTURE_HOME` unless the service file sets it. For v1, this is documented in `docs/agentirc/helper-permissions.md` as a known gotcha — agents started via the standard `cu agent start` flow inherit the shell environment, which is the common path.

### `audit/<nick>.jsonl` — post-hoc activity log

One line per AssistantMessage. Grows forever (rotation is non-goal; see "Non-goals"). Schema:

```json
{
  "ts": "2026-05-28T14:32:17.123Z",
  "nick": "local-research",
  "type": "assistant",
  "model": "claude-opus-4-7",
  "text": "I'll search for PR #123 …",
  "tool_uses": [
    {"name": "Bash", "input_digest": "sha256:abc..."}
  ],
  "tool_results": [
    {"name": "Bash", "content_digest": "sha256:def...", "preview": "first 200 chars"}
  ]
}
```

`input_digest`/`content_digest` lets the audit log stay compact while still letting the boss correlate to the in-flight `perm-queue/<id>.json` (which contains the full input).

### `daemon-log/<nick>.jsonl` — control-plane action log

One line per daemon action (see [Daemon action log](#daemon-action-log) for the vocabulary). Grows forever. Schema:

```json
{"ts": "2026-05-28T14:32:17.123Z", "nick": "local-research", "action": "compact", "detail": {"trigger": "context_watermark", "pct": 0.91}}
```

### `handoff/<nick>.md` — context-handoff document

Plain markdown, overwritten each context-fill cycle. Written by the agent (via its `Write` tool, auto-allowed for this path) when the daemon detects the 90% watermark. Read by the agent after compact (daemon injects a reminder) and by the boss for visibility. See [Context-watermark handoff](#context-watermark-handoff).

## Permission lifecycle

```text
┌─────────┐     ┌─────────────┐    ┌──────────────┐    ┌────────┐
│ SDK     │ ──► │ broker.gate │──► │ policy match │──► │ allow  │
│ tool    │     │ (callback)  │    │              │    │ (fast) │
│ wants to│     └─────────────┘    └──────────────┘    └────────┘
│ run     │                                │
└─────────┘                                │ no match
                                           ▼
                              ┌──────────────────────┐
                              │ write perm-queue/    │
                              │ <id>.json            │
                              │ (atomic rename)      │
                              └──────────────────────┘
                                           │
                                           ▼
                              ┌──────────────────────┐
                              │ await decision file  │
                              │ (poll + asyncio.sleep│
                              │  or watchdog/inotify)│
                              │ NO TIMEOUT           │
                              └──────────────────────┘
                                           │
                                           ▼
                              ┌──────────────────────┐
                              │ read decision; if    │
                              │ scope=always, append │
                              │ to perm-policy yaml  │
                              └──────────────────────┘
                                           │
                              ┌────────────┴────────────┐
                              ▼                         ▼
                  ┌──────────────────┐      ┌──────────────────┐
                  │ PermissionResult │      │ PermissionResult │
                  │ Allow            │      │ Deny(message=    │
                  │                  │      │   reason)        │
                  └──────────────────┘      └──────────────────┘
```

**Steps in detail:**

1. SDK fires `can_use_tool(tool_name, input, context)`.
2. Broker reads `perm-policy/<nick>.yaml` (cached, mtime-checked).
3. If a rule matches: return `PermissionResultAllow` or `PermissionResultDeny` immediately. **No round-trip.**
4. Otherwise: mint `id`, write request to `perm-queue/<id>.json` atomically.
5. `await` decision file. Poll every 250ms with `os.path.exists`, then re-stat on appearance to handle the atomic-rename race. Indefinite — no timeout. (IRC summary post to `#perm-<name>` is **deferred to v1.1**: the broker module sits below the daemon's IRC transport and has no clean plumbing to it today; threading an `on_perm_request` callback from daemon → runner → broker is a follow-up. v1 boss UX is `pending-perms.sh`/`watch-perms.sh`.)
6. Read decision JSON. If `scope == "always"`, append to policy YAML *before* returning, so subsequent matching calls auto-resolve.
7. Delete `perm-queue/<id>.json` and `perm-decisions/<id>.json` after consuming (both files are single-use).
8. Return `PermissionResultAllow(updated_input=None)` or `PermissionResultDeny(message=reason, interrupt=False)`.

## Race conditions and atomicity

| Scenario | Mitigation |
|---|---|
| Boss writes two decisions for one ID | `approve.sh`/`deny.sh` refuse to write if `perm-decisions/<id>.json` exists. Race-window: two concurrent boss invocations. Mitigated by `os.O_CREAT \| os.O_EXCL` open before the rename target. First-writer-wins; second exits non-zero. |
| Helper dies waiting | Request file stays in `perm-queue/`. On next `spawn-helper.sh`, `--reattach` is non-goal; the orphan is shown by `pending-perms.sh` but cannot be resolved (no helper to unblock). `cleanup-stale-perms.sh` (new) garbage-collects requests whose helper is not running. |
| Helper task cancelled by SDK / `close-helper.sh` mid-`await` | The broker's `gate()` wraps its poll loop in `try/finally` — on `asyncio.CancelledError` it deletes its in-flight `perm-queue/<id>.json` and re-raises the cancellation. Past asyncio incidents in this codebase (commit `d0902f9`, "fix: re-raise CancelledError and save create_task results") mandate explicit re-raise discipline; broker code follows the same rule. |
| Boss decides but file write is partial | Atomic `tempfile + os.replace`. Readers only see the post-rename inode. Partial writes never become visible. |
| Helper reads policy mid-update by `approve.sh always` | Policy YAML is also written via atomic `tempfile + os.replace`. Broker checks mtime before each request; stale cache is benign (one extra round-trip until next read). |
| Multiple helpers on the same boss | Each helper has its own policy file (`<nick>.yaml`), audit log, and request IDs. No shared state besides directory layout. |
| `perm-queue/` directory missing | Broker creates it (and `perm-decisions/`, `perm-policy/`, `audit/`) on first request with `os.makedirs(..., exist_ok=True)`. `ensure-mesh.sh` also pre-creates them. |

**Concurrent file-watcher latency:** poll cadence of 250ms is an explicit trade-off — fast enough that human-typed approvals feel instant (boss-to-helper unblock under 500ms typical), slow enough that 4 helpers waiting don't burn measurable CPU. inotify (Linux) and FSEvents (macOS) are available via `watchdog` but add a dependency; deferred to follow-up if poll cadence proves too slow.

## Policy matcher semantics

Implemented in `culture/clients/_perm_broker.py` as a pure-Python matcher (no third-party dep beyond PyYAML, already in `pyproject.toml` for culture.yaml parsing).

```python
def match(tool_name: str, input_dict: dict, policy: dict) -> Literal["allow", "deny", None]:
    for section, verdict in (("auto_deny", "deny"), ("auto_allow", "allow")):
        for rule in policy.get(section, []) or []:
            if _rule_matches(tool_name, input_dict, rule):
                return verdict
    return None  # caller routes to boss
```

`_rule_matches`:

- `rule["tool"]` is a string. If it contains any of `.*+?[]^$|`, treat as `re.fullmatch`; else exact equality.
- `rule["input_regex"]` (optional) is `re.search`-style against a tool-specific projection of `input_dict`:
  - `Bash` → `input_dict["command"]` (string).
  - `Edit` / `Write` → `input_dict["file_path"]` (string).
  - `mcp__*` → `json.dumps(input_dict, sort_keys=True)` (string).
  - Other tools → no input projection; rule with `input_regex` against them is a no-match.
- All rule keys other than `tool` and `input_regex` are reserved (silently ignored to allow forward-compatible additions).

Policy file is parsed once at broker init and re-loaded on `os.path.getmtime` change (checked at the start of every `gate()` call). Stale policy cache → one extra round-trip; not a correctness issue.

## Backend matrix

| Backend | Tool inheritance | Permission broker | Context-watch | Audit log | Daemon-action log |
|---|---|---|---|---|---|
| **Claude** | `setting_sources=["user","project","local"]` (`agent_runner.py`) | **Full** — native `can_use_tool` (SDK `types.py:1075`) | **Full** — `ResultMessage.usage.input_tokens` | Yes | Yes |
| **Copilot** | `skill_directories` (deferred — SDK not verifiable in venv) | **Deferred → audit-only** this PR (Copilot SDK `PermissionHandler` signature not verifiable in the current venv; see Unverified Assumptions) | No — no token counts on responses (issue #299) | Yes | Yes |
| **Codex** | No setting_sources surface in app-server JSON-RPC | **Audit-only** — no per-tool callback in protocol | No — no token counts | Yes | Yes |
| **ACP** | No skill/MCP inheritance hook in ACP surface | **Audit-only** — no per-tool callback | No — no token counts | Yes | Yes |

**All-backends rule honored via documentation**, per CLAUDE.md ("a feature that only exists in one backend is a bug" — addressed by the **audit log** and **daemon-action log** providing parity-of-visibility on every backend, even where parity-of-control or context-watch is technically impossible). The two universal pillars (audit log, daemon-action log) ship on all four backends; the two Claude-only pillars (synchronous broker approval, context-watermark) are gated by SDK capability and documented as such.

`spawn-helper.sh` does **not** restrict backend choice. A boss spawning a non-Claude backend gets a warning printed to stderr ("audit-only mode: this helper will not request boss approval before tool calls and has no context-watch") but is allowed to proceed.

## Boss-side script surface

All under `~/.claude/skills/culture-boss/scripts/`. Existing scripts that grow new behavior are noted.

| Script | Argv | Behavior |
|---|---|---|
| `pending-perms.sh` | (none) | Lists every `perm-queue/*.json`. Columns: `id`, `nick`, `tool`, `input-preview`, `age`. Empty list → exit 0 with no output. Exit 1 if `perm-queue/` is missing (mesh not bootstrapped). |
| `approve.sh` | `<id> [always [<tool-pattern>]]` | Writes `perm-decisions/<id>.json` with `verdict=allow`, `scope` per argv. `always` without a pattern uses `tool: <tool_name>` (exact match); `always <pattern>` uses pattern as `tool` field. Fails if decision file already exists. **Atomic-write discipline:** the script writes to a temp path via `python3 -c "import os, json, tempfile; …"` using `O_CREAT \| O_EXCL` on the temp file, then `os.replace(tmp, dest)` for atomic install. The `O_CREAT \| O_EXCL` on the *destination* path is the first-writer-wins guard. |
| `deny.sh` | `<id> [reason...]` | Writes `perm-decisions/<id>.json` with `verdict=deny`. Reason joined into `reason` field. Fails if decision file already exists. **Atomic-write discipline:** identical to `approve.sh` — temp file + `os.replace` + `O_CREAT \| O_EXCL` first-writer-wins. |
| `watch-perms.sh` | (none) | `tail -F` style live view. Combines `inotifywait` (Linux) / `fswatch` (macOS) when available, else polls every 1s. Prints new requests with timestamp + helper nick + tool. |
| `policy.sh` | `list <nick>` / `add <nick> <yaml-snippet>` / `remove <nick> <rule-index>` | Inspect/edit `perm-policy/<nick>.yaml`. Adds run through a YAML re-emit so formatting stays consistent. |
| `cleanup-stale-perms.sh` | (none) | Walk `perm-queue/`; for each request whose `helper_nick` has no running daemon (check via `cu agent status`), delete the file. Idempotent. |
| `daemon-log.sh` | `<name> [limit]` | Tail + pretty-print `daemon-log/local-<name>.jsonl`. Default limit 30. |
| `context-status.sh` | `[name]` | Read-only context-watch visibility. Reports last-known context `pct` per helper (from the daemon-action log's most recent `compact`/`handoff_written` entries, or a dedicated `context` action emitted each turn). With a name, shows just that helper. |
| `spawn-helper.sh` | `<name> [cwd]` (unchanged argv) | **Now also** seeds `perm-policy/<name>.yaml` with safe-read defaults + the handoff-write auto-allow rule. Creates `audit/`, `perm-queue/`, `perm-decisions/`, `perm-policy/`, `daemon-log/`, `handoff/` if missing. |
| `status.sh` | (unchanged) | **Now also** prints `[N pending perms]` and the most recent daemon-action per helper. |
| `read-replies.sh` | `<name> [limit]` (unchanged) | **Now also** prepends `[N pending perms]` summary line if any exist for that helper. |

Per-script bash files target POSIX `bash`, follow the existing `_common.sh` sourcing pattern, and use `python -c` for atomic file writes (avoiding the need to add a write-fence dependency).

## Helper-side wiring

### Claude backend

`culture/clients/claude/agent_runner.py:_make_options` becomes:

```python
def _make_options(self) -> ClaudeAgentOptions:
    opts = ClaudeAgentOptions(
        model=self.model,
        cwd=self.directory,
        permission_mode="bypassPermissions",
        setting_sources=["user", "project", "local"],   # widen
        can_use_tool=self._can_use_tool,                # conditional — see below
    )
    if self.system_prompt:
        opts.system_prompt = self.system_prompt
    if self._session_id:
        opts.resume = self._session_id
    return opts
```

**Critical: `_can_use_tool` is conditionally set.** Setting it unconditionally would route every Claude agent's tool calls through the broker, including standalone mesh agents (e.g. `spark-culture`, future agents started directly via `cu agent start`) where no boss is watching the queue — those agents would hang indefinitely on the first non-auto-allow tool call.

`AgentRunner.__init__` decides at construction time whether to attach the callback:

```python
def __init__(self, ..., nick: str = "", ...) -> None:
    ...
    policy_path = os.path.expanduser(f"~/.culture/perm-policy/{nick}.yaml")
    if nick and os.path.exists(policy_path):
        self._broker = PermissionBroker(nick=nick)
        self._can_use_tool = self._broker.gate  # bound method
    else:
        self._broker = None
        self._can_use_tool = None  # SDK skips the streaming-mode check too
```

`spawn-helper.sh` seeds the policy file before starting the helper daemon, so helpers always get supervision. Standalone agents (no policy file) get `can_use_tool=None`, preserving pre-broker behavior identically.

Also: the `query()` call at `_process_turn` is modified to use the streaming-prompt wrapper described in [SDK consequence: `can_use_tool` forces streaming-mode prompts](#sdk-consequence-can_use_tool-forces-streaming-mode-prompts) **only when** `_can_use_tool is not None`. When it's `None`, the existing string-prompt path is preserved (no SDK error, no unnecessary refactor of non-supervised agents).

### Copilot backend

`culture/clients/copilot/agent_runner.py:start` builds `session_kwargs` (line 87). Today:

```python
session_kwargs = {
    "on_permission_request": PermissionHandler.approve_all,
    "model": self.model,
}
```

Becomes:

```python
policy_path = os.path.expanduser(f"~/.culture/perm-policy/{self._nick}.yaml")
if self._nick and os.path.exists(policy_path):
    broker = PermissionBroker(nick=self._nick)
    handler = broker.copilot_handler()  # adapter
else:
    broker = None
    handler = PermissionHandler.approve_all  # preserve current behavior

session_kwargs = {
    "on_permission_request": handler,
    "model": self.model,
}
if user_skill_dirs := _discover_user_skill_dirs():
    session_kwargs["skill_directories"] = user_skill_dirs + self.skill_directories
```

Same gate as Claude: standalone Copilot agents without a policy file keep `PermissionHandler.approve_all` and are unaffected. Boss-spawned helpers get the broker.

`_discover_user_skill_dirs()` returns `[~/.claude/skills/]` if the directory exists, plus any subdirectory matching `*/skills/` under `~/.claude/plugins/` (mirrors the SDK's plugin layout).

`broker.copilot_handler()` is an adapter that closes over the same `gate()` function but adapts the argv/return shape to the Copilot SDK's expected `PermissionHandler` callable.

> **Unverified assumption (Copilot SDK):** the actual `PermissionHandler` callable signature is not visible in this venv (lazy import — `from copilot import CopilotClient, PermissionHandler, SubprocessConfig`). The exact adapter shape must be confirmed at implementation time against the `github-copilot-sdk` package version pinned in `pyproject.toml`. Fallback: if the SDK exposes only `approve_all`/`deny_all` static methods (no callable interface), Copilot reverts to **audit-only** mode (same as Codex/ACP) and the matrix entry is downgraded with a note in the docs page.

### Codex + ACP backends

No agent_runner changes. Daemon-level audit log only.

### Audit log (all four backends)

Each backend's `daemon._on_agent_message` grows one new call. Reference implementation lives in a new `culture/clients/_audit.py`:

```python
# culture/clients/_audit.py
class AuditWriter:
    def __init__(self, nick: str, *, root: str | None = None) -> None: ...
    async def write(self, msg: dict) -> None: ...  # JSONL append, fsync at end
```

Per-backend `daemon.__init__` instantiates `self._audit = AuditWriter(self.agent.nick)`. Each `_on_agent_message` adds one line:

```python
async def _on_agent_message(self, msg: dict) -> None:
    await self._audit.write(msg)              # new
    # ... existing supervisor/relay/status code unchanged
```

Hook points already exist verbatim:

- `culture/clients/claude/daemon.py:426`
- `culture/clients/codex/daemon.py:529`
- `culture/clients/copilot/daemon.py:511`
- `culture/clients/acp/daemon.py:536`

All four files get the same one-line addition. Per the "cite, don't import" rule for `packages/`, `_audit.py` is in `culture/clients/` (not `packages/`) because it's runtime-internal to the harnesses, not a backend template.

## Migration and backward compatibility

- **Helpers spawned before this change**: have no `perm-policy/<nick>.yaml`. **By design, the absence of a policy file means `can_use_tool=None` is set on the SDK call — the agent behaves exactly as it does today (`bypassPermissions` semantics, no broker involvement, no hang).** To bring an existing helper under boss supervision, re-run `spawn-helper.sh <name>` (idempotent — seeds the policy file if missing and restarts the daemon).
- **Boss sessions running an older culture-boss skill**: the new scripts coexist with the old ones (no script is removed; only new scripts added and three existing scripts gain extra output lines). `status.sh`'s extra `[N pending perms]` line is additive and downstream `awk`/`grep` consumers in any other scripts would need to skip the new prefix — but no such consumers exist within this repo.
- **Existing `bypassPermissions` agents that are not helpers** (e.g. long-running mesh agents started directly via `cu agent start`, such as `spark-culture`): no `can_use_tool` callback because there's no policy file. The change to `setting_sources=["user","project","local"]` *does* affect them — they now inherit the user's MCP servers and skills. This is intentional: any agent on the mesh can use the boss's tool surface. They run autonomously as before (no broker gate), with the new audit log providing post-hoc visibility. **Documented as an upgrade note in `docs/agentirc/helper-tool-inheritance.md`.**
- **Config schema**: no breaking changes to `culture.yaml`. The broker reads only `perm-policy/<nick>.yaml`, a separate file written by tooling, not by the user.

## Security threat model

Same-machine, same-UID only. The broker's authority surface (`~/.culture/perm-decisions/`) is filesystem-protected by standard POSIX permissions (`0700` on the directory, `0600` on files — set explicitly by `ensure-mesh.sh` and the broker on creation). Anyone who can write to this directory as the user already has shell access to the user's home dir; the broker offers no protection against that case.

Out of scope: multi-user systems, attacker-controlled supply chain on the helper itself (a malicious skill installed under `~/.claude/skills/` is trusted by the user's normal Claude Code session anyway). The broker is a *boss-as-human* mechanism, not a sandbox.

In scope: race conditions between concurrent boss script invocations (covered by `O_CREAT|O_EXCL` first-writer-wins above), partial-write recovery (atomic rename), and orphaned requests from dead helpers (covered by `cleanup-stale-perms.sh`).

## Context-watermark handoff

**Problem.** A long-running helper accumulates context until the model's window is full. The SDK auto-compacts, but a naive compact loses the working state the agent built up — what it was doing, what it learned, what's left. The fix: just before the window fills, have the agent write a durable handoff for its post-compact self, then remind it to read that handoff after the compact.

**Who monitors.** The **daemon** self-monitors (decided during authoring — reliable, can't blow past the limit between async boss polls). The boss gets read-only visibility via `context-status.sh`.

**Signal.** The Claude SDK's `ResultMessage.usage` exposes per-turn `input_tokens`. Under session resume, each turn's `input_tokens` reflects the *full* context sent to the model (history + new prompt), so it is a direct proxy for current context-window occupancy. The daemon computes:

```
pct = last_input_tokens / context_window_tokens
```

`context_window_tokens` is resolved per-model (see table). At `pct >= 0.90` (configurable; default 0.90) the daemon enters the handoff flow **once** per fill cycle (a latch prevents re-firing every turn while still above threshold; it resets after a compact drops usage below a low-water mark of 0.5).

| Model family | Context window |
|---|---|
| `claude-opus-4-*` (1M beta) | 1_000_000 |
| `claude-opus-4-*`, `claude-sonnet-4-*` | 200_000 |
| `claude-haiku-4-*` | 200_000 |
| unknown | 200_000 (conservative default) |

The window map lives in `culture/clients/_context_watch.py`; unknown models default to 200K and log a warning so the map can be extended.

**Flow (daemon-side, after each turn's ResultMessage):**

```text
turn completes → ResultMessage.usage.input_tokens
       │
       ▼
pct >= 0.90 AND not already latched?
       │ yes
       ▼
1. send handoff prompt to the agent:
   "You are approaching your context limit. Write a concise handoff to
    <CULTURE_HOME>/handoff/<nick>.md for your post-compact self: what you're
    doing, key decisions, what's left, important file paths. Then stop."
       │  (wait for the turn to complete — agent writes the file via its
       │   normal Write tool, which the broker auto-allows for the handoff
       │   path; see policy note below)
       ▼
2. trigger compact (existing _ipc_compact path / runner.send_prompt("/compact"))
       │
       ▼
3. set "reminder pending" latch
       │
       ▼
next activation (mention/poll) → prepend to the prompt:
   "[context-handoff] You recently compacted. Read your handoff at
    <CULTURE_HOME>/handoff/<nick>.md before continuing."
       │
       ▼
clear reminder latch
```

**Handoff file.** `~/.culture/handoff/<nick>.md` — plain markdown, overwritten each cycle (the latest handoff is the only relevant one). Not JSONL; it is meant to be read by the agent (and the boss) as prose.

**Policy interaction.** For a *supervised* helper, the handoff `Write` must not block on boss approval (the agent is mid-context-crisis; a stalled approval defeats the purpose). The daemon seeds an `auto_allow` rule for `Write` to the handoff path into the helper's policy at spawn time:

```yaml
auto_allow:
  - tool: Write
    input_regex: '/handoff/<nick>\.md$'
```

(Added by `spawn-helper.sh` policy seed and by `_context_watch` defensively if absent.) All *other* `Write` calls still route to the boss.

**Config.** New optional fields on the agent `culture.yaml`:

```yaml
context_watch:
  enabled: true          # default true
  high_water: 0.90       # fraction of context window that triggers handoff
  low_water: 0.50        # fraction below which the latch resets
```

Absent config → defaults (`enabled: true`, `0.90`, `0.50`). This is additive; no breaking change to existing `culture.yaml` files.

**Module.** `culture/clients/_context_watch.py` holds the pure logic:

```python
@dataclass
class ContextWatchState:
    high_water: float = 0.90
    low_water: float = 0.50
    handoff_latched: bool = False
    reminder_pending: bool = False

def context_window_for(model: str) -> int: ...
def evaluate(state, input_tokens, model) -> WatchAction: ...
#   WatchAction ∈ {NONE, WRITE_HANDOFF, REMINDER_DUE}
```

The daemon owns an instance per agent, feeds it each `ResultMessage`, and acts on the returned `WatchAction`. Pure-function `evaluate` is unit-tested without a live SDK.

**Backend coverage.** Claude exposes per-turn `input_tokens` (`ResultMessage.usage`) — full support. Codex/Copilot do **not** expose token counts on their responses today (Copilot: documented at `copilot/agent_runner.py:210`, issue #299; Codex: same gap). For those backends the watermark cannot be computed, so context-watch is **Claude-only** in this PR; documented in the backend matrix. The daemon-action log still records compaction events on all backends when they occur via the existing `_ipc_compact` path.

## Daemon action log

**Problem.** The agent-message audit log captures what the *agent* says and which tools it calls. It does not capture what the *daemon* does to manage the agent — starts, stops, compactions, crashes, pause/resume, watermark-triggered handoffs. The boss wants a control-plane record.

**Shape.** One JSONL line per daemon action at `~/.culture/daemon-log/<nick>.jsonl`. Universal across all four backends. Schema:

```json
{
  "ts": "2026-05-28T14:32:17.123Z",
  "nick": "local-research",
  "action": "compact",
  "detail": {"trigger": "context_watermark", "pct": 0.91}
}
```

**Action vocabulary** (string `action` + free-form `detail` dict):

| action | when | detail |
|---|---|---|
| `agent_start` | daemon starts the runner | `{model, directory}` |
| `agent_stop` | graceful stop | `{}` |
| `agent_exit` | runner exits (incl. crash) | `{exit_code}` |
| `crash` | crash recorded in sliding window | `{exit_code, count}` |
| `circuit_open` | circuit breaker trips | `{count, window_s}` |
| `pause` / `resume` | pause state changes | `{manual: bool}` |
| `compact` | compact triggered | `{trigger: "ipc"\|"context_watermark", pct?}` |
| `clear` | context cleared | `{}` |
| `handoff_written` | watermark handoff prompt sent | `{pct, path}` |
| `handoff_reminder` | post-compact reminder injected | `{path}` |

**Module.** `culture/clients/_daemon_log.py` with a `DaemonLog` writer mirroring `AuditWriter`'s atomic-append discipline:

```python
class DaemonLog:
    def __init__(self, nick: str) -> None: ...
    async def record(self, action: str, **detail: Any) -> None: ...
```

Each backend's `daemon.__init__` instantiates `self._daemon_log = DaemonLog(nick=agent.nick)`. Existing lifecycle methods gain one `await self._daemon_log.record(...)` line each at the points in the vocabulary table. These are surgical additions; no control-flow changes.

**Relationship to Python logging.** The daemon keeps its existing `logger.info/warning` calls (human-readable, systemd journal). The action log is the *structured, boss-readable* complement — not a replacement. Where both fire for the same event, that's intentional (one for ops, one for the boss).

**Boss-side.** `daemon-log.sh <name> [limit]` tails `~/.culture/daemon-log/<nick>.jsonl` and pretty-prints. `status.sh` additionally prints the most recent action per helper.

## Testing strategy

Per project convention (CLAUDE.md): no mocks for the server; real I/O. Pytest + pytest-asyncio + pytest-xdist (already in use). `/run-tests` for execution.

| Test | Location | What it verifies |
|---|---|---|
| `test_perm_broker_policy.py` | `tests/clients/` | Policy matcher: exact tool match, regex tool match, `input_regex` against Bash command, MCP wildcards, fallthrough to None, malformed YAML graceful degrade. Pure-function tests; no filesystem. |
| `test_perm_broker_fs.py` | `tests/clients/` | End-to-end with a real tmp dir as `~/.culture`. Spin a broker, fire `gate()` in a task, verify request file appears, write decision file, verify task completes with correct verdict. Cover allow/deny/timeout-via-cancel/scope-always-mutates-policy/missing-policy-file. |
| `test_perm_broker_atomic.py` | `tests/clients/` | Concurrent `approve.sh`-equivalent calls for the same ID — verify exactly one wins. Verify partial-write recovery (write to tmpfile, kill before rename, broker doesn't see it). |
| `test_audit_writer.py` | `tests/clients/` | Append-only JSONL semantics across re-opens. Verify each line is valid JSON. Verify timestamps are monotonic ISO8601. |
| `test_spawn_helper_seeds_policy.py` | `tests/integration/` | Pytest. Runs `spawn-helper.sh foo` via subprocess against an isolated `CULTURE_HOME` temp dir, then asserts `<CULTURE_HOME>/perm-policy/local-foo.yaml` exists and matches the default-content fixture. (Repo convention is pytest, not shell — even for shell-driven flows.) |
| `test_claude_agent_runner_inheritance.py` | `tests/clients/` (or `tests/harness/` alongside existing `test_agent_runner_claude.py`) | Construct an `AgentRunner` two ways: (a) without a policy file → assert `_make_options().setting_sources == ["user","project","local"]` and `can_use_tool is None`; (b) with a fixture-written policy file → assert `can_use_tool is not None` and is a callable bound to a `PermissionBroker`. |
| `test_claude_runner_streaming_mode.py` | `tests/harness/` | Verify the conditional prompt-wrapping branch in `_process_turn`. With no policy file: `_can_use_tool is None`, and the `query()` call site receives `prompt` as a `str` (today's behavior). With a policy file: `_can_use_tool` is set, and the call site receives `prompt` as an `AsyncIterable[dict]` whose first yielded item is `{"type": "user", "message": {"role": "user", "content": <text>}}`. Use a Transport stub injected via `ClaudeAgentOptions(...)` to capture what `query()` was called with — no live Claude CLI required. |
| `test_context_watch.py` | `tests/` | Pure-function `evaluate()`: below high-water → NONE; at/above high-water and not latched → WRITE_HANDOFF + latch set; still above while latched → NONE; after drop below low-water → latch reset; reminder_pending → REMINDER_DUE then cleared. `context_window_for()` maps known models and defaults unknown to 200K. |
| `test_daemon_log.py` | `tests/` | `DaemonLog.record()` appends one valid-JSON line per call; action + detail round-trip; concurrent records serialize; timestamps ISO8601-UTC. Real tmp `CULTURE_HOME`. |

xdist-safe via `tmp_path` fixture for the `~/.culture`-root isolation; environment variable `CULTURE_HOME` (new, defaults to `~/.culture`) lets tests override.

**AgentRunner constructor change — callsite enumeration.** Adding the policy-file gate at `AgentRunner.__init__` does not change the constructor *signature* (no new required kwargs — `nick` already exists), so existing callsites continue to compile. However, runtime behavior changes: any test that constructs an `AgentRunner` with a `nick` value that happens to match an existing policy file path will see `can_use_tool` set. Phase 2 must verify the seven existing callsites (`culture/clients/claude/daemon.py:326`; `tests/test_agent_runner.py:64, 90, 120, 143, 175, 204`; `tests/harness/test_agent_runner_claude.py:180`) and confirm:

1. Production callsite — gets supervised iff its policy file exists (correct by design).
2. Test callsites use either a sentinel nick (`"test-..."`) that has no policy file, OR an explicit `CULTURE_HOME` env override pointing at an empty tmp dir.

Existing tests using `nick="spark-claude"` (`test_agent_runner_claude.py:178`) are safe as long as no `~/.culture/perm-policy/spark-claude.yaml` exists on the developer's machine. CI is clean by definition. Local-dev test runs that *happen* to have a real `spark-claude` agent registered will see different behavior — flagged as a known dev-environment caveat in the testing docs.

## Project conventions applied

From `culture/CLAUDE.md`:

- **All-backends rule** — addressed via documentation parity (audit log universal) + matrix table calling out where control parity is impossible.
- **Citation pattern** — `_perm_broker.py`, `_audit.py`, `_context_watch.py`, `_daemon_log.py` live in `culture/clients/` (runtime-internal). They are **not** added to `packages/agent-harness/`. Backend daemons import them directly. (Rationale: these are cross-backend infrastructure; the "cite, don't import" rule applies to per-backend templates that are reflected into each backend, not to genuinely shared runtime code.)
- **Documentation** — four new pages: `docs/agentirc/helper-permissions.md` (broker), `docs/agentirc/helper-tool-inheritance.md` (setting_sources widening + skill_directories), `docs/agentirc/helper-context-handoff.md` (context-watermark), `docs/agentirc/helper-daemon-log.md` (daemon-action log). Linked from `docs/agentirc/index.md`.
- **Idempotent migrations** — N/A; no DDL.
- **Pre-push review** — touches transport-adjacent daemon code in all four backends. Invoke `Agent(subagent_type="feature-dev:code-reviewer", ...)` on staged diff before first push.
- **Doc-test-alignment** — new boss-side scripts, new config file paths (`perm-policy/<nick>.yaml`), new `CULTURE_HOME` env var convention. (No new IRC verbs or channel patterns in v1 — those land in v1.1 with the IRC-summary follow-up.) Invoke `Agent(subagent_type="doc-test-alignment", ...)` before first push.
- **Version bump** — minor (new feature). Per CLAUDE.md, run `/version-bump minor` before opening PR.
- **Format before commit** — run `uv run black <files>` and `uv run isort <files>` on staged Python files before `git commit`.

## Unverified assumptions

| Assumption | Required check | When |
|---|---|---|
| Copilot SDK's `PermissionHandler` exposes a callable interface that can be substituted for `PermissionHandler.approve_all` | Read `github-copilot-sdk` source at the version pinned in `pyproject.toml`; confirm `on_permission_request` accepts a custom callable matching `Callable[[PermissionRequest], Awaitable[PermissionDecision]]` (or similar) | Before wiring Copilot — if it doesn't, Copilot drops to audit-only and the matrix is downgraded |
| `~/.claude/skills/` exists for the user running the boss | At broker init in Copilot wiring, log a warning if absent; do not fail | Implementation time |
| `os.replace` is atomic on the user's filesystem | True on POSIX local filesystems including macOS/Linux; non-atomic on NFS or some FUSE mounts | Documented limitation; flag in `docs/agentirc/helper-permissions.md` |
| `CULTURE_HOME` env var doesn't collide with an existing convention | `grep -rn "CULTURE_HOME" culture/` | Implementation time |

## Open questions

1. **Should `auto_allow` rules be re-evaluated after a session-resume?** A helper that crashes and resumes via SDK `resume` could face a different policy if `approve.sh always` was used during the prior session. Decision: re-read on each `gate()` call (already in design), so resume picks up the latest policy automatically. Documented behavior, not a question — listed here as confirmation.
2. **Watchdog/inotify vs polling**: poll-only for v1. If approval latency feedback is bad, add `watchdog` as a dep and switch in v1.1. Tracked as a follow-up issue, not in scope.

## Non-goals

- **Multi-boss arbitration on a single helper.** One helper, one boss. If two boss sessions share a helper name (against the documented convention), behavior is undefined — first-writer-wins on decisions.
- **Web UI.** Boss UX is shell + IRC only.
- **RBAC / per-channel policy.** All policy is per-helper-nick. No "this user can approve Bash but not Edit."
- **Audit log rotation.** Grows forever. Operator concern, not feature scope.
- **Reversing the gate** (allow by default, ask for deny). Default is "ask boss"; safe-read auto-allow is the only fast-path concession.
- **Boss notifications outside the boss session** (push to phone, etc). Boss is responsible for noticing pending requests in its own conversation flow.
- **IRC `#perm-<name>` channel posts.** Deferred to v1.1. The broker module sits below the daemon's IRC transport with no clean plumbing today. v1 boss UX is the file-backed queue surfaced via `pending-perms.sh` and `watch-perms.sh`.

## Phases

| Phase | Work | Acceptance gate |
|---|---|---|
| **P0** | Spec authored and reviewed. | `/review-plan` reports no critical findings. |
| **P1** | `culture/clients/_perm_broker.py` + `culture/clients/_audit.py`. Pure modules, no backend wiring. Unit tests pass. | `test_perm_broker.py`, `test_audit_writer.py` all green. |
| **P2** | Claude backend wiring. `agent_runner.py` widens settings + adds callback + streaming-prompt branch. `daemon.py` adds audit writer. | `test_claude_runner_inheritance.py` green. Existing claude tests still pass. |
| **P3** | Codex/ACP audit-only wiring. Just the audit writer in each daemon. | Existing codex/acp tests still pass. |
| **P4** | Copilot wiring (subject to PermissionHandler verification). If verification fails, demote Copilot to audit-only and update the matrix. | Manual inspection + existing copilot tests still pass. |
| **P5** | Boss-side scripts: `pending-perms.sh`, `approve.sh`, `deny.sh`, `watch-perms.sh`, `policy.sh`, `cleanup-stale-perms.sh`, `daemon-log.sh`, `context-status.sh`. Modify `spawn-helper.sh`, `ensure-mesh.sh`, `status.sh`, `read-replies.sh`, `_common.sh`. | `test_spawn_helper_seeds_policy.py` green; manual smoke test (spawn helper, see policy seeded). |
| **P6 (context-watch)** | `culture/clients/_context_watch.py` (pure). Wire into Claude `daemon.py`: feed `ResultMessage.usage` per turn, act on `WatchAction` (write-handoff prompt → compact → reminder). Add `context_watch` config to `culture/clients/claude/config.py`. | `test_context_watch.py` green. |
| **P7 (daemon-log)** | `culture/clients/_daemon_log.py` (atomic JSONL). Instantiate `DaemonLog` in all four `daemon.__init__`; add `record(...)` calls at lifecycle points (start/stop/exit/crash/circuit/pause/resume/compact/clear/handoff). | `test_daemon_log.py` green. Existing daemon tests still pass. |
| **P8** | Docs: `docs/agentirc/helper-permissions.md`, `docs/agentirc/helper-tool-inheritance.md`, `docs/agentirc/helper-context-handoff.md`, `docs/agentirc/helper-daemon-log.md`. Update `docs/agentirc/index.md`. Update culture-boss `SKILL.md`. | `doc-test-alignment` subagent reports no missing coverage. |
| **P9** | Format, `/run-tests`, code-reviewer subagent on staged diff. | All green + reviewer findings addressed or explicitly accepted. |
| **P10** | `/version-bump minor` + CHANGELOG entry. Commit, push, open PR. | PR exists, CI green. |

## Out of scope

- Any change to the IRC server (`culture/agentirc/`).
- Any change to `packages/agent-harness/` template files (broker is runtime-internal; not reflected).
- Web UI, dashboards, metrics dashboards. (Audit JSONL feeds whatever the operator wants.)
- Permission rule import/export (e.g. share a policy file between machines). Operator concern.
- Long-term audit log archival, redaction, or compliance features.

## Acceptance summary

The feature ships when:

1. A boss can run `spawn-helper.sh foo`, brief the helper, and see all of the boss's MCP servers + skills available to the helper.
2. When the helper tries to invoke Edit/Write/Bash-with-side-effects/any MCP, the boss receives a pending request via `pending-perms.sh`.
3. `approve.sh <id>` unblocks the helper within ~500ms; `deny.sh <id> <reason>` causes the model to receive a denial in its tool result.
4. `approve.sh <id> always` makes subsequent identical requests auto-approve with no round-trip.
5. `~/.culture/audit/<nick>.jsonl` contains a line per AssistantMessage for every helper in every backend.
6. Helpers in Codex/ACP backends do not block on `can_use_tool` (no broker hook) and have audit logs.
7. A Claude helper approaching 90% context writes `~/.culture/handoff/<nick>.md`, compacts, and is reminded to read it on next activation (verifiable via `daemon-log/<nick>.jsonl` `handoff_written` + `compact` + `handoff_reminder` entries).
8. `~/.culture/daemon-log/<nick>.jsonl` records control-plane actions (start/stop/compact/crash/handoff) for every helper in every backend; `daemon-log.sh <name>` tails it.
9. `/run-tests` green. `doc-test-alignment` reports no gaps. `feature-dev:code-reviewer` finds no high-confidence issues unaddressed.

---

## Evidence Log

### Cited source — SDK supports `can_use_tool`

`claude_agent_sdk/types.py:159-161`:

```python
CanUseTool = Callable[
    [str, dict[str, Any], ToolPermissionContext], Awaitable[PermissionResult]
]
```

`claude_agent_sdk/types.py:1075`:

```python
# Tool permission callback
can_use_tool: CanUseTool | None = None
```

`claude_agent_sdk/types.py:139-157` defines `PermissionResultAllow(behavior="allow", updated_input, updated_permissions)` and `PermissionResultDeny(behavior="deny", message, interrupt)`.

### Cited source — SDK supports `setting_sources` with `user|project|local`

`claude_agent_sdk/types.py:1090`:

```python
# Setting sources to load (user, project, local)
setting_sources: list[SettingSource] | None = None
```

### Cited source — current Claude wiring uses project-only

`culture/clients/claude/agent_runner.py:132-143` (verbatim, current main):

```python
def _make_options(self) -> ClaudeAgentOptions:
    opts = ClaudeAgentOptions(
        model=self.model,
        cwd=self.directory,
        permission_mode="bypassPermissions",
        setting_sources=["project"],
    )
    if self.system_prompt:
        opts.system_prompt = self.system_prompt
    if self._session_id:
        opts.resume = self._session_id
    return opts
```

### Cited source — Copilot's existing PermissionHandler swap point

`culture/clients/copilot/agent_runner.py:85-96`:

```python
session_kwargs: dict[str, Any] = {
    "on_permission_request": PermissionHandler.approve_all,
    "model": self.model,
}
if self.system_prompt:
    session_kwargs["system_message"] = {"content": self.system_prompt}
if self.skill_directories:
    session_kwargs["skill_directories"] = self.skill_directories

self._session = await self._client.create_session(**session_kwargs)
```

### Cited source — `_on_agent_message` hook points across all four backends

- `culture/clients/claude/daemon.py:426-431`:

  ```python
  async def _on_agent_message(self, msg: dict) -> None:
      """Feed agent activity to the supervisor for observation."""
      if self._supervisor:
          await self._supervisor.observe(msg)

      self._capture_agent_status(msg)
  ```

- `culture/clients/codex/daemon.py:529-537`:

  ```python
  async def _on_agent_message(self, msg: dict) -> None:
      """Relay agent text to IRC and feed to supervisor."""
      self._consecutive_turn_failures = 0
      await self._relay_response_to_irc(msg)

      if self._supervisor:
          await self._supervisor.observe(msg)

      self._capture_agent_status(msg)
  ```

- `culture/clients/copilot/daemon.py:511-519`: same shape as codex (verified by grep + 20-line read).
- `culture/clients/acp/daemon.py:536-544`: same shape as codex (verified by grep + 20-line read).

### Cited source — spawn-helper.sh script (insertion point for policy seeding)

`~/.claude/skills/culture-boss/scripts/spawn-helper.sh:1-45` (verbatim, current):

```bash
#!/usr/bin/env bash
# Spawn a helper agent into its own private task channel.
# Usage: spawn-helper <name> [cwd]
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/_common.sh"

NAME="${1:?usage: spawn-helper <name> [cwd]}"
CWD="${2:-}"
if [ -z "$CWD" ]; then
  CWD="${HOME}/.culture/helpers/${NAME}"
  mkdir -p "$CWD"
fi
NICK="local-${NAME}"
TASK_CHAN="#task-${NAME}"
# ... agent registration ...
echo "[boss] spawned $NICK; private channel $TASK_CHAN; cwd $CWD."
```

Policy seed insertion lands after the `mkdir -p "$CWD"` block and before agent registration.

---

*End of spec.*
