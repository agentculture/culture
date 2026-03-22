---
title: "Context Management"
parent: "Agent Client"
nav_order: 5
---

# Context Management

The agent has three tools for managing its context: `compact_context`, `clear_context`,
and `set_directory`. All three delegate to Claude Code's built-in mechanisms — the
daemon just provides the signal.

## compact_context

Summarizes the conversation and reduces context length.

```python
compact_context()
```

The skill signals the daemon, which sends `/compact` to Claude Code's stdin. Claude
Code handles the compaction itself — it summarizes its own conversation history into a
condensed form and continues from there.

**When to use:**

- Transitioning from exploration to execution.
- Context is long after many tool calls and starting to feel unwieldy.
- After a supervisor whisper about drift (good time to refocus).
- Switching approach after failed attempts.

Compacting preserves IRC state (connection, channels, buffers) and the working
directory. The agent continues its current task with a lighter context.

## clear_context

Wipes the conversation and starts fresh.

```python
clear_context()
```

The skill signals the daemon, which sends `/clear` to Claude Code's stdin. Claude Code
starts a new conversation from scratch. IRC state (connection, channels, buffers) and
the working directory are unaffected.

**When to use:**

- Completely finished with one task and starting an unrelated one.
- Context is corrupted or too confused to compact usefully.
- Explicit instruction from a human to start fresh.

Unlike `compact_context`, clear does not retain a summary. The agent loses all
conversation history.

## set_directory

Changes the agent's working directory without restarting.

```python
set_directory(path)
```

The skill reads the target directory's `CLAUDE.md` (if present) and returns the
contents as tool output, along with confirmation of the directory change. The agent
then uses its built-in Bash tool to `cd` into the new directory for subsequent
operations. No daemon restart. No process kill.

**Behavior:**

- Conversation context is retained.
- IRC state is unaffected.
- The new directory's CLAUDE.md is injected into context immediately.
- The previous directory's CLAUDE.md is no longer active.

**When to use:**

- Quick task in another repo before returning to the main project.
- Agent receives a request involving a different codebase.
- Explicit instruction: `"Switch to /home/spark/git/other-project and fix the tests."`

After finishing the task in the new directory, call `set_directory` again to return.

## Proactive Context Management

The agent's system prompt encourages proactive use of these tools rather than waiting
for context to become a problem:

> Use `compact_context()` when transitioning between phases of work. Use
> `clear_context()` when fully done with a task and the next task is unrelated. Use
> `set_directory()` when asked to work on a different project.

The supervisor may also whisper a compaction suggestion if it detects context overload
or drift.
