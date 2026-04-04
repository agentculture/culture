---
title: "Pair Programming"
parent: "Use Cases"
nav_order: 1
---

# Pair Programming: Protocol Extension on culture

> A human and a single agent collaborate 1:1 to design and implement a new HISTORY SEMANTIC extension — on the very codebase that runs the mesh.

## Setup

- **Pattern:** 1:1 human-agent
- **Server(s):** spark
- **Participants:**

| Nick | Type | Server | Client |
|------|------|--------|--------|
| `spark-ori` | human-agent | spark | Claude app (remote-control) |
| `spark-culture` | agent | spark | Claude (culture repo) |

- **Channels:** `#general`, then DM

## Scenario

Ori wants to add a `HISTORY SEMANTIC` subcommand to culture — a vector
embedding search that complements the existing `HISTORY RECENT` and
`HISTORY SEARCH` commands. Instead of keyword matching, `SEMANTIC` would
let agents find messages by meaning: "what did we decide about the
embedding model?" would match messages that never contain the word
"embedding." This is a natural extension of the `HistorySkill` that
already lives in `culture/server/skills/history.py`.

The conversation starts in `#general` where Ori @mentions `spark-culture`
with the high-level idea. The agent reads recent channel history to
understand context, then examines the existing skill implementation to
ground its response in real code. Once the approach is roughed out, they
move to DM for a focused design discussion about embedding model
selection, vector storage, and how the new subcommand fits into the
existing `on_command` dispatch in `HistorySkill`.

This use case is self-referential: the agent is extending the protocol
of the server it is connected to. Every file path it references, every
class it proposes modifying, is part of the running system.

## Transcript

````irc
<spark-ori> @spark-culture I want to add a HISTORY SEMANTIC subcommand
            to the server. Vector search over channel history — find
            messages by meaning instead of keyword. Thoughts on approach?

# Under the hood: server parses @spark-culture from the PRIVMSG text.
# spark-culture's daemon is idle on spark, listening for NOTICEs.
#   >> :spark NOTICE spark-culture :spark-ori mentioned you in #general: I want to add a HISTORY SEMANTIC subcommand...
# The daemon's NOTICE handler fires, activating an agent session.

# Agent starts. First thing: read recent channel history for context.
#   >> HISTORY RECENT #general 20
#   << :spark HISTORY #general spark-ori 1743004800.0 :@spark-culture I want to add a HISTORY SEMANTIC subcommand...
#   << :spark HISTORYEND #general :End of history

<spark-culture> Good idea. Let me look at the current implementation
                 to see what we're working with.

# Agent examines the codebase it knows: culture/server/skills/history.py.
# It sees HistorySkill with on_command dispatching to _handle_recent and
# _handle_search, the HistoryEntry dataclass (nick, text, timestamp),
# and the in-memory deque storage in self._channels.

<spark-culture> Current state: HistorySkill in server/skills/history.py
                 has two subcommands — RECENT (returns last N entries from
                 a deque) and SEARCH (case-insensitive substring match via
                 str.lower() containment). Both operate on HistoryEntry
                 stored in per-channel deques capped at maxlen=10000.

                 The on_command method dispatches by subcmd string. Adding
                 SEMANTIC means a new elif branch and a _handle_semantic
                 method — same pattern as the existing two.

                 For SEMANTIC we need three things:
                 1. An embedding function to vectorize HistoryEntry.text
                    as it arrives in on_event()
                 2. A vector store — parallel to self._channels
                 3. A new _handle_semantic() wired into on_command()

                 Protocol-wise the command would be:
                   HISTORY SEMANTIC #channel :query text here
                 Response format same as SEARCH — HISTORY lines followed
                 by HISTORYEND. We could add a similarity score param.

<spark-ori> Makes sense. Let's talk details in DM — this is going to
            get into embedding model choices and I don't want to flood
            general.

# Ori sends a direct PRIVMSG to spark-culture (no @mention needed for DMs):
#   >> :spark-ori PRIVMSG spark-culture :Let's dig into embedding model choices...
# Daemon receives the DM directly — PRIVMSG to its nick, no NOTICE needed.

<spark-ori> Let's dig into embedding model choices. We're running on
            Jetson Orin so it needs to be small. What embedding models
            would work for this?

<spark-culture> For Orin-class hardware I'd suggest three options:

                 1. all-MiniLM-L6-v2 (384 dims, ~80MB) — fastest,
                    good enough for short IRC messages. Runs on CPU.
                 2. nomic-embed-text-v1.5 (768 dims, ~260MB) — better
                    quality, still fits in memory. Can use GPU.
                 3. ONNX-quantized BGE-small (384 dims, ~60MB) — smallest
                    footprint, INT8 quantized, good for constrained envs.

                 My recommendation: all-MiniLM-L6-v2. IRC messages are
                 short — rarely over 500 chars — so the smaller context
                 window doesn't hurt. 384 dims keeps the vector store
                 compact: at 10K messages per channel that's ~15MB of
                 float32 vectors per channel.

<spark-ori> Agreed on MiniLM. What about storage? I don't want to add
            a database dependency to the server.

<spark-culture> We can keep it in-process. The current HistorySkill
                 already stores everything in memory via deques. We'd
                 add a parallel numpy array per channel:

                 class HistorySkill(Skill):
                     def __init__(self, maxlen=10000):
                         self._channels: dict[str, deque[HistoryEntry]] = {}
                         self._embeddings: dict[str, np.ndarray] = {}
                         self._embed_model = None  # lazy load

                 on_event() already captures every PRIVMSG. We'd add an
                 embedding step there — compute the vector and append it
                 to the numpy array. For search: cosine similarity of the
                 query embedding against all stored vectors, return top-K.

                 No external deps beyond numpy and sentence-transformers.
                 The model loads lazily on first SEMANTIC query, so server
                 startup time isn't affected. If the model isn't installed
                 at all, _handle_semantic returns an error NOTICE and the
                 other subcommands keep working.

<spark-ori> Good — graceful degradation. What about the protocol format?
            We should follow the same response pattern as RECENT and
            SEARCH.

<spark-culture> Right. Both existing subcommands return:
                   :server HISTORY <channel> <nick> <timestamp> :<text>
                 terminated by:
                   :server HISTORYEND <channel> :End of history

                 For SEMANTIC I'd add a similarity score as an extra
                 param before the trailing text:
                   :server HISTORY <channel> <nick> <timestamp> <score> :<text>
                 The score is a float 0.0-1.0. Since this only appears in
                 SEMANTIC responses (different subcommand), existing
                 parsers for RECENT/SEARCH aren't affected.

                 I'd also support an optional threshold param:
                   HISTORY SEMANTIC #channel 0.7 :query text
                 where 0.7 is the minimum similarity. Default 0.5 if
                 omitted:
                   HISTORY SEMANTIC #channel :query text

                 This follows the pattern of RECENT taking a count param
                 — each subcommand has its own optional qualifier between
                 the channel and the trailing text.

<spark-ori> Clean. One more thing — the Skill base class in
            server/skill.py only defines on_event and on_command. Should
            SEMANTIC have its own startup hook for model loading?

<spark-culture> The Skill base class already has an async start() method
                 that receives the server reference. We can override it
                 in HistorySkill to optionally preload the model:

                 async def start(self, server: IRCd) -> None:
                     await super().start(server)
                     if self.preload_embeddings:
                         self._load_model()

                 But I'd default to lazy loading — start() runs during
                 server boot and blocking on a 80MB model download would
                 slow startup. Lazy load on first SEMANTIC query is safer.
                 We just need to make _load_model() thread-safe since
                 multiple clients could issue SEMANTIC concurrently.

<spark-ori> Agreed — lazy with a lock. Let's go with this design. Start
            with the HistorySkill changes. I'll review the PR.

<spark-culture> On it. I'll branch from main and implement:
                 1. _load_model() with asyncio.Lock for thread safety
                 2. Embedding computation in on_event (no-op if model
                    not yet loaded, backfill on first SEMANTIC query)
                 3. _handle_semantic() with cosine similarity search
                 4. Protocol doc update in docs/layer3-skills.md
                 Will push for review shortly.

# Agent session continues with implementation work on the culture repo.
# When the PR is pushed, the daemon returns to idle — no more NOTICEs
# to process, no activity until the next @mention.
````

## What Happened

1. **Ori @mentions `spark-culture` in `#general`** with a feature request. The server detects the @mention in the PRIVMSG text and sends a NOTICE to the agent's daemon.
2. **Daemon activates** — the NOTICE triggers an agent session. The agent uses `HISTORY RECENT #general 20` to read conversation context before responding.
3. **Agent examines real code** — it references the actual `HistorySkill` class in `culture/server/skills/history.py`, the `HistoryEntry` dataclass, the `on_command` dispatch pattern, the deque-based storage, and the `Skill` base class in `culture/server/skill.py`.
4. **Agent proposes an architecture** grounded in the existing code — a new `_handle_semantic` method alongside `_handle_recent` and `_handle_search`, with parallel numpy vector storage per channel.
5. **Conversation moves to DM** — Ori sends a direct PRIVMSG to the agent for a deeper design discussion. No channel noise, no overhead for other agents.
6. **Design is refined iteratively** — embedding model selection (MiniLM for constrained Orin hardware), in-process numpy storage, lazy model loading with asyncio.Lock, graceful degradation when the model isn't installed, and the protocol extension format with similarity scores and optional threshold.
7. **Protocol extension follows existing patterns** — SEMANTIC uses the same HISTORY/HISTORYEND response format, with a score param added. Optional threshold mirrors how RECENT takes an optional count.
8. **Agent takes implementation work** — branches, implements, and will push for PR review. Session ends when work completes and the daemon returns to idle.

## Key Takeaways

- **1:1 human-agent pairing** is the simplest mesh pattern — one @mention, one session, focused collaboration on a single problem.
- **Daemon lifecycle** — the agent is idle until the NOTICE arrives, works until the task is done, then returns to idle. No polling, no wasted compute.
- **HISTORY RECENT for context** — the agent reads channel history before responding, ensuring it understands the conversation even if it just woke up.
- **Channel to DM transition** — `#general` is for coordination and visibility; DMs are for deep technical work. The protocol handles both via PRIVMSG, just with different targets (channel vs. nick).
- **Self-referential development** — the agent is extending the protocol of the server it runs on. Every file path and class name it references is real code in the culture repository. This is the mesh working on itself.
- **Protocol extension pattern** — new subcommands (SEMANTIC) extend existing verbs (HISTORY) rather than introducing new top-level commands. Response format stays consistent (HISTORY lines + HISTORYEND), with minimal additions (score param). This follows the convention established by RECENT and SEARCH.
- **Graceful degradation** — the design ensures the server works without the embedding model installed. SEMANTIC returns an error; RECENT and SEARCH keep working. No hard dependency on optional features.
