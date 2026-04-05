---
title: "Harness Conformance"
parent: Architecture
nav_order: 8
---

# Harness Conformance Checks

## Overview

Qodo PR review is configured to enforce conformance across all 4 agent
backends (claude, codex, copilot, acp). When a PR touches files under
`culture/clients/` or `packages/agent-harness/`, Qodo applies 7 checks
that prevent backends from drifting apart.

The authoritative spec is `docs/agent-harness-spec.md`.

## Configuration

All conformance rules live in `.pr_agent.toml` under the `[pr_reviewer]`
section's `extra_instructions` field.

## Checks

### 1. Cross-backend propagation

When a file in one backend changes, Qodo flags if the corresponding files
in the other 3 backends were not updated. Exception: `agent_runner.py` and
`supervisor.py` contain backend-specific logic — only structural/interface
changes need propagation.

### 2. Generic file identity

These files must be identical across all backends and the reference in
`packages/agent-harness/`, except for import paths:

- `irc_transport.py`
- `message_buffer.py`
- `socket_server.py`
- `ipc.py`
- `webhook.py`
- `skill/irc_client.py`

### 3. Interface contract

Changes to `agent_runner.py` or `supervisor.py` are checked against the
spec's `AgentRunnerBase` and `SupervisorBase` interfaces.

**AgentRunnerBase** requires: `start(initial_prompt)`, `stop()`,
`send_prompt(text)`, `is_running()`, `session_id`, `on_message`, `on_exit`.

**SupervisorBase** requires: `start()`, `stop()`, `observe(turn)`,
`on_whisper`, `on_escalation`.

### 4. Daemon contract

Changes to `daemon.py` are checked for all IPC dispatch handlers:
`irc_send`, `irc_read`, `irc_ask`, `irc_join`, `irc_part`, `irc_who`,
`irc_channels`, `compact`, `clear`, `shutdown`.

Structural features (crash recovery, `_mention_targets`, sleep scheduler)
must be consistent across backends.

### 5. Config schema

Changes to `config.py` must preserve the same field names and types across
all backends. Only default values (model names, agent identifiers) may
differ.

### 6. SKILL.md consistency

All backends' `SKILL.md` must document the same required commands: `send`,
`read`, `ask`, `join`, `part`, `channels`, `who`.

### 7. Spec-doc alignment

Changes to `docs/agent-harness-spec.md` are cross-referenced against
client docs in `docs/clients/` and the code in `culture/clients/`.
Changes to client docs or code are checked against the spec.

## Adding New Checks

Edit `.pr_agent.toml` and add a new subsection under the
`## Harness Conformance (CRITICAL)` heading in `extra_instructions`.
