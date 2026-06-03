# culture-bridge — Claude Code plugin

`culture-bridge` is the Claude Code (CC) plugin that makes your CC
session a first-class boss on the culture mesh. After install, the CC
session you are typing into IS the boss — there is no separate "boss
brain" running behind it. A small `culture-bridge` daemon holds the
IRC connection on your behalf and pushes inbound events into CC.

See
`docs/superpowers/specs/2026-06-03-mesh-rearchitecture-plan.md`
for the design rationale (Phase 4 — "CC plugin").

## What gets installed where

Activating the plugin runs `install.py`, which writes a single
`culture-bridge` block into **`~/.claude/settings.json`** (the
user-scope settings file, NOT the plugin-scope file). The block
registers four hooks:

| Hook event       | Script                                | Purpose                                                                 |
| ---------------- | ------------------------------------- | ----------------------------------------------------------------------- |
| `SessionStart`   | `hooks/session_start.py`              | resolve project nick, start bridge, drain offline spool, surface roster |
| `Stop`           | `hooks/stop.py`                       | end-of-turn queue drain (DMs / mentions / room invites)                 |
| `UserPromptSubmit` | `hooks/user_prompt_submit.py`       | fallback drain + first-prompt `set_runtime_model` latch                 |
| `PreToolUse`     | `hooks/pre_tool_use.py`               | perm-request interrupt before any tool call                             |

### Why user-settings-scoped and not plugin-scoped?

Claude Code bug **#16538** (closed as "not planned") makes plugin-scoped
`SessionStart` hooks unreliable at injecting
`hookSpecificOutput.additionalContext` — your spool drain would
silently disappear. The Phase 0.4 spike confirmed that user-settings
hooks DO work, including the Stop-hook `decision: "block"` pattern
the Stop hook depends on for the end-of-turn queue drain.

The plugin's installer is **idempotent** — re-running it overwrites
only the `culture-bridge` block in `settings.json` and leaves every
other hook, MCP server, theme, or model default alone.

## Project nick resolution

Every CC session picks a project-named boss nick on launch. Priority:

1. `$CULTURE_BOSS_NICK` env var (operator override).
2. `nick:` field in `<cwd>/culture.yaml`.
3. `git config --get remote.origin.url` basename, stripped of `.git`.
4. `os.path.basename(cwd)`.
5. Legacy fallback `local-boss` (logged as a warning).

The result is sanitized to `[A-Za-z0-9_-]`, lowercased, and clipped to
14 characters so the worker namespace (`<boss>-<worker-suffix>`) fits
comfortably under the 30-char IRC nick cap.

## Workers follow the project naming convention

When you `mesh spawn qa` (or shell out to `culture boss spawn qa
--boss <your-boss-nick>`), the worker is registered as
`<your-boss-nick>-qa` and joins `#task-<your-boss-nick>-qa`. The
worker's brief is auto-prefixed with *"You are
&lt;full-nick&gt;, working under &lt;boss-nick&gt; on …"* so the
worker immediately sees its identity and chain-of-command.

If you accidentally type the full nick into the worker name (e.g.
`mesh spawn fork-rearch-qa --boss fork-rearch`), the spawn helper
strips the redundant prefix so you end up with `fork-rearch-qa`, not
`fork-rearch-fork-rearch-qa`.

## The `mesh ...` tools

The plugin exposes the following tools the assistant calls:

| Verb                            | Purpose                                                      |
| ------------------------------- | ------------------------------------------------------------ |
| `mesh send <channel> <text>`    | PRIVMSG to a channel.                                        |
| `mesh dm <nick> <text>`         | Direct message to a user nick (spooled if recipient offline).|
| `mesh inbox`                    | Drain pending inbound events.                                |
| `mesh who [#channel]`           | List occupants of a channel (or the mesh).                   |
| `mesh status`                   | Report bridge status.                                        |
| `mesh agents`                   | List the workers this boss owns.                             |
| `mesh pending`                  | List perm-queue entries.                                     |
| `mesh approve <id> [--input-regex P] [--scope always\|once]` | Approve a perm request.    |
| `mesh deny <id> [reason]`       | Deny a perm request.                                         |
| `mesh invite <worker> <#chan>`  | Invite a worker into an extra channel.                       |
| `mesh team-channel-create [topic]` | Create `#team-<own-project>` for sibling awareness.       |
| `mesh grant <worker> <tool> [--input-regex P] [--scope always\|once]` | Grant a worker a tool. |

### PreToolUse recursion-avoidance pattern

The `PreToolUse` hook fires before every tool call to gate on pending
worker permission requests (AD-1). But the hook itself runs again when
CC calls `mesh approve` to action the very request it just surfaced.
Without a guard, that triggers infinite recursion: approve →
PreToolUse fires → sees the same request still queued → blocks the
approve → CC re-approves → loop.

`pre_tool_use.py` short-circuits for any tool whose name starts with
`mesh ` (plus an explicit allowlist of every documented `mesh ...`
verb so a future tool with the same prefix doesn't accidentally
bypass the gate). For non-mesh tools, the hook polls the bridge's
perm queue and blocks with `{"decision": "block", "reason":
<details>}` when a request is pending.

### `mesh grant` — known limitation

The rule "boss can only grant what boss has" is enforced via a
best-effort read of `$CLAUDE_PERMITTED_TOOLS`. Claude Code does not
currently surface the session-permitted-tools list to plugin tools
through any documented API, so when the env var is unset we forward
the grant to the bridge and rely on the worker's own policy file to
gate at the next `PreToolUse` (which IS the authoritative second
layer). This is captured in the Phase 4 plan as a known gap with a
Phase 5 follow-up path.

## Uninstall

Manually remove the `culture-bridge` block from
`~/.claude/settings.json`, or run:

```bash
python3 culture/clients/claude/cc_plugin/install.py uninstall
```

Both paths preserve every other entry in `settings.json` byte-for-byte.

## Where things live

```
culture/clients/claude/cc_plugin/
├── plugin.json              # CC plugin manifest
├── install.py               # first-run hook installer (idempotent)
├── tools.py                 # mesh ... MCP tool registrations
├── _nick_resolver.py        # project-nick priority resolution
├── _bridge_client.py        # synchronous AF_UNIX IPC client
└── hooks/
    ├── session_start.py     # SessionStart — drain spool + start bridge
    ├── stop.py              # Stop — end-of-turn queue drain
    ├── user_prompt_submit.py# UserPromptSubmit — fallback drain + model latch
    ├── pre_tool_use.py      # PreToolUse — perm-request interrupt
    └── session_end.py       # SessionEnd — stop owned workers
```
