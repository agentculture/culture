# Workspace map

Which product repo owns what, and which repos are being folded into which.
The plan comes from
[culture-agents#2](https://github.com/agentculture/culture-agents/issues/2) and its
confirmed spec. Anything not built yet is marked `(planned)`. Nothing here is
approval to delete or archive a repository.

## Product repos

| Repo | Owns | Must not own |
| --- | --- | --- |
| **culture** (this repo) | Workspace, operator CLI, composition of the other products | The agent runtime or the chat protocol (`planned`: today it still embeds both) |
| **culture-agents** | Agent registry; direct dispatch and delegation; sessions and runs; attach, steer, pause, resume, cancel, answer; supervision; harness adapter boundary; authoritative run state | Rooms, threads, presence; workflow definitions; the realtime sensory loop |
| **culture-chat** | Rooms, participants, presence, threads, history, replay, bot events, federation | Owning or supervising agent runs |
| **culture-rules** | Rules, triggers, typed workflow steps, workflow state, scheduling | Becoming the permanent controller of agent sessions |
| **embodiment** | Sensing, realtime voice, turn-taking, barge-in, intentional silence, attention policy. Stays an independent app | A separate generic dispatcher or session manager |

Decision separation: embodiment decides whether an event deserves attention,
culture-agents decides how requested work is dispatched, run and recovered,
culture-rules decides when a workflow proceeds, culture-chat only carries
messages.

Non-negotiable: agents run, receive work and are supervised without joining
chat. Chat is an optional transport. Agents dispatch agents through the
culture-agents control API; there is no separate `agent-dispatch` repo.

## Consolidation

| Existing repo | Destination | Gate | Condition |
| --- | --- | --- | --- |
| agent-lifecycle | culture-agents | D2, archive at D6 | Supervision contracts reused (start/stop, restart, health, hooks) |
| cultureagent | culture-agents | D1 to D2, archive at D6 | Harness wrappers replaced by AI SDK adapters only where proven equivalent; bridge kept for the rest |
| agentirc | culture-chat | D3, archive at D6 | Messaging requirements reimplemented or deliberately dropped |
| irc-lens | culture-chat | D3, archive at D6 | Console, web UI and inspection surfaces consolidated |
| codexd | culture-agents (as needed) | D0 inventory, D6 | May be moved as needed; not in the issue's original move table |
| culture, culture-chat, culture-rules, embodiment | keep | D3 to D6 | Rewire to the new services; embodiment calls culture-agents through one adapter |

The four superseded repos (agent-lifecycle, cultureagent, agentirc, irc-lens)
stay unarchived until the replacements pass compatibility, recovery and
data-migration tests. Existing installs keep a versioned migration path and
rollback.

## Delivery gates

| Gate | Outcome | Status |
| --- | --- | --- |
| D0 | Inventory, compatibility matrix, run/session/event schemas, auth model, TS-vs-Python seam | in progress (spec only) |
| D1 | Single-host spike: dispatch, inspect, steer, cancel without chat | `(planned)` |
| D2 | Fleet and recovery: isolated worktrees, idempotent submits, restart reconciliation | `(planned)` |
| D3 | Communication split into culture-chat | `(planned)` |
| D4 | culture-rules dispatches through culture-agents | `(planned)` |
| D5 | embodiment integration | `(planned)` |
| D6 | Migration, deprecation, archive of superseded repos | `(planned)` |

## Decisions recorded in the spec

- The culture-agents core is TypeScript, published as `@culture.dev/agents`
  `(planned)`. The Python `agents` CLI controls it.
- Harnesses run through the Vercel AI SDK adapters inside an NVIDIA OpenShell
  sandbox `(planned)`.
- culture-nodes is out of scope for now.
- embodiment stays an independent app; it is not folded into culture-agents.

## Supporting services

These support the control plane. None replaces the run and session store in
culture-agents.

| Service | Role |
| --- | --- |
| culture-library | Content and retrieval |
| eidetic-cli | Memory |
| workledger-cli | Append-only work provenance |
| evidence-cli, coherence-cli | Measurements |
| grant | Secret injection |
| lobes-cli | Model serving |
| heat-governor | Thermal admission |

## Status

| Repo | State today |
| --- | --- |
| culture | Shipping. Still embeds the IRC server engine (`culture_core/`) and agent daemons, and pins `cultureagent` and `agentirc-cli` |
| culture-agents | Scaffold only: agent-first CLI baseline (`whoami`, `learn`, `explain`, `overview`, `doctor`). No control-plane verbs yet |
| culture-chat | Repo exists. The replacement chat service is `(planned)` |
| culture-rules, embodiment | Shipping; integration with culture-agents `(planned)` |
| cultureagent, agent-lifecycle, agentirc, irc-lens | Shipping and still the live implementations |

Source of truth for plan details: the issue linked at the top. This page is a
map, not a design; if they disagree, the issue and the spec win.
