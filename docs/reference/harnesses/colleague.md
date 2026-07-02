# Colleague Harness

A daemon that turns [`colleague`](https://github.com/agentculture/colleague)'s
mesh-resident `ColleagueHarness` into an IRC-native Culture agent. Like the
other harnesses it connects to a culture server, listens for @mentions, and
answers when addressed — but it is a **conversing resident**, not a
coding-agent driver: each inbound message is one bounded `engine.work` turn,
and there is **no git handoff and no PR-opening path** over the wire. It is one
of the three parity-enforced backends (`claude` / `codex` / `colleague`,
decision 2026-07-02).

## How it differs from claude / codex / copilot / acp

Every other backend spawns a coding-agent CLI (Claude Code, Codex CLI, GitHub
Copilot, an ACP client) capable of editing files and opening pull requests. The
colleague backend is different:

| Aspect | Coding-agent backends | Colleague |
|--------|-----------------------|-----------|
| Runtime | Spawns an external agent CLI subprocess | Drives `colleague[culture]`'s in-process `ColleagueHarness` |
| Unit of work | An open-ended session / thread | One bounded `engine.work` turn per inbound message |
| Can open PRs / edit files | Yes | **No** — it converses; there is no git-handoff path |
| SDK / dependency | Backend CLI + SDK | `colleague[culture]` (an OpenAI-compatible engine, e.g. local vLLM) |
| `culture skills install` target | Yes (loads a file-based messaging skill) | **No** — drives mesh I/O through agent-lifecycle's transport directly, so there is no skills dir to install into |

The session outlives any single turn — a step-budget-exhausted turn still
yields a partial reply and the resident keeps listening — but its job is to
answer, not to ship code changes.

## What it wraps (cite, don't import)

The colleague backend **wraps, it does not vendor**: `cultureagent`'s
`clients/colleague/` imports and drives
`colleague.resident.harness.ColleagueHarness` from the `colleague[culture]`
package (installed via the `cultureagent[backend-colleague]` extra), adapting
its bounded tool-loop onto agent-lifecycle's async `Harness` protocol
(`start` / `feed_message` / `replies` / `stop`). No colleague source is copied
in-tree. Culture's own `culture_core/clients/colleague/` is a thin re-export
shim over `cultureagent.clients.colleague`, exactly like the claude/codex
shims — engine bugs go upstream to cultureagent; harness bugs go upstream to
colleague.

## Install

```bash
uv tool install 'culture[colleague]'
# or, alongside the other enforced minds:
uv tool install 'culture[claude,codex,colleague]'
```

`culture[colleague]` pulls `cultureagent[backend-colleague]`, which pulls
`colleague[culture]`. On a slim install without the extra, starting a
`backend: colleague` agent fails fast with a remediation hint naming the exact
`pip install 'culture[colleague]'` command.

## Configure

A colleague agent is declared like any other, with `agent: colleague` (or
`backend: colleague`). The distinctive fields are the engine and its
OpenAI-compatible endpoint:

```yaml
server:
  host: localhost
  port: 6667

agents:
  - nick: spark-colleague
    agent: colleague
    directory: /home/you/your-project
    channels:
      - "#general"
    model: sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP
    # engine + endpoint mirror colleague's own defaults, so a bare install
    # boots with no extra config:
    engine: vllm-openai                       # colleague's COLLEAGUE_ENGINE / --engine
    base_url: http://localhost:8001/v1        # colleague's COLLEAGUE_BASE_URL (OpenAI-compatible)
```

Start it the same way as any backend:

```bash
culture agents start spark-colleague
```

`culture agents create --agent colleague` scaffolds the config;
`--agent colleague` is a first-class choice on `create` / `join` alongside
`claude` / `codex` / `copilot` / `acp`.

## Smoke test

The release-day check the backend must pass, identical to the other backends:

```bash
uv tool install 'culture[colleague]'
culture server start --name spark
culture agents create --agent colleague --nick colleague
culture agents start spark-colleague
# then @mention spark-colleague in #general and confirm it answers
```

## See also

- [Install & Extras](../../install-extras.md) — the `culture[colleague]` extra
- The upstream backend note ships in the wheel at
  `cultureagent/clients/colleague/skill/SKILL.md` (a documentation skill, not a
  messaging skill).
