# sbx-ask: the guest-mode sandbox agent

`sbx-ask` is a dedicated, **tool-less** question-answering agent for the
guest-mode sandbox server (`sbx`). It generalizes the `spark-ask` prototype.
Guests are untrusted, so the agent can only reply with text.

It is not an agent backend: it lives in `culture_core/sandbox/agent.py` and is
a plain IRC client plus an OpenAI-compatible chat-completions client.

## Run it

```bash
culture sandbox agent --bundle-from . --config sandbox.yaml   # build the docs bundle, exit
culture sandbox agent --config sandbox.yaml                   # run
```

(`python -m culture_core.sandbox.agent` is equivalent.) The nick must start with
the server name, e.g. `sbx-ask` on a server named `sbx`.

## Config (YAML, all keys optional)

| Key | Default | Meaning |
|-----|---------|---------|
| `host`, `port` | `127.0.0.1`, `6667` | sandbox IRC server |
| `nick`, `channels` | `sbx-ask`, `[#general]` | identity and channels (also answers DMs) |
| `gateway_url` | `http://localhost:8001/v1/chat/completions` | default gateway |
| `models` | `[cortex-spark2]` | tried in order; each entry is a name or `{name, url, key_env}` |
| `key_env`, `key_file` | `SBX_GATEWAY_KEY`, none | the agent's own gateway key: env var, else `KEY=value` line in `key_file` |
| `max_tokens` | `700` | clamped to at most 700 |
| `knowledge_dir` | `~/.culture/sandbox/knowledge` | the only place the agent reads |
| `max_active` | `5` | guests served at once |
| `answer_unaddressed` | `true` | answer every room line (guests don't know to @mention); `false` = mention-only in channels, DMs always answered |
| `guest_room_prefix` | `#g-` | follow each guest into its private room `<prefix><nick>` (EVENTSUB `user.join`, needs the `agentirc.io/bot` capability) and rejoin existing ones on connect; must match irc-lens `guest_mode.sandbox.room_prefix`; `null` turns it off |
| `room_sweep_s` | `300` | how often sbx-ask LISTs rooms and leaves a guest room nobody else is in (AgentIRC sends no `QUIT` for a closed tab); empty rooms are not rejoined on connect |
| `knowledge_budget_chars` | `48000` | ceiling on the docs bundle in the prompt; the bundle is also shrunk to fit `max_prompt_chars` (see [Work budget](#work-budget)) |
| `max_prompt_chars` | `40000` | one hard cap on the whole request: system prompt, docs, search hits, room lines, DM history and the question |
| `history_turns`, `history_chars` | `6`, `4000` | the per-guest DM history: question/answer pairs kept, and characters of them sent per request (newest whole pairs) |
| `search_hits`, `search_chars`, `search_keywords`, `search_timeout` | `8`, `1500`, `3`, `2.0` | per-request chat search: hits and characters kept, words searched, seconds waited; `0` hits turns it off |
| `owner_channel`, `flag_log` | none, `~/.culture/sandbox/flags.jsonl` | where NSFW flags go |
| `peer_agents` | `{}` | other agents sharing the rooms, `{nick: one line about it}` (or a list of nicks); enables the loop guard below and goes into the system prompt so the agent can tell guests how to reach them |
| `max_agent_hops` | `1` | peer messages answered per room after each human message |
| `room_context_lines`, `room_context_chars` | `30`, `6000` | what the agent knows of a room: the last lines from everyone in it (guests, other agents, its own replies), newest kept within the character budget; seeded from `HISTORY RECENT` on join; `0` lines turns it off |

Adding a model (for example an AWS-hosted one) is a config entry with its own
`url` and `key_env`; nothing else changes.

## Room context

Each agent keeps a transcript per room: the last `room_context_lines` lines
from **everyone** in it, meaning guests, other agents and its own replies. It's
held in memory only and dropped when the room is swept. When the agent joins a
room (a guest's new room, or rejoining after a restart), it sends
`HISTORY RECENT <room> <n>` to AgentIRC (in-memory history, so it works on
the `--no-persist` sandbox) and puts the replayed lines before anything heard
live meanwhile. The server's history is the source of truth: on every
(re)join the transcript is rebuilt from the replay plus only the lines heard
live after it (overlap matched by position, so order is kept and a line
said twice stays twice). The agent asks for 4× `room_context_lines` entries
(at most 200) because `system-*` join/lifecycle lines are skipped; its own
replies are stored one entry per IRC line, as the server stores them. A
question that arrives during a catch-up waits up to 3 s for it; a catch-up
with no `HISTORYEND` (an IRCd without the HISTORY skill) is abandoned after
10 s with a warning. NSFW and command lines (and the agent's declines of
them) never become context. Only rooms the agent is in keep a transcript.

For a question in a room, the model gets that transcript (newest lines kept
within `room_context_chars`) as one "recent messages in this room" context
message, then the question attributed to its sender. The system prompt lets
it use the conversation (what the guest said, what other agents said); the
culture facts still come only from the reference material. DMs keep the
per-guest Q&A history (`history_turns`). Before this, an agent saw only its
own Q&A with each guest, so a second agent asked "what would you add to
that?" didn't know what "that" was.

## Work budget

Each answer is kept to a small, fixed amount of work:

- **Response cap and no reasoning.** `max_tokens` is clamped to 700, the
  system prompt asks for a short answer, `enable_thinking` is false, and any
  `<think>` block is stripped (see Safety properties).
- **One cap on the request.** Everything the model is sent fits in
  `max_prompt_chars` (40,000). The docs bundle is loaded to fit beside the
  larger context a request can carry: a room question carries room lines and
  search hits, a DM carries DM history, never both. So the docs get
  `max_prompt_chars` minus the rest of the system prompt, minus the larger
  of `room_context_chars` + `search_chars` and `history_chars`, minus 1,500
  for the headers and the question, at most `knowledge_budget_chars`: about
  30,000 with the defaults. If a request is still over, context gives
  way in this order: search hits first, then the oldest room lines, then the
  oldest DM turns. The question is never cut; only if it and the bare system
  prompt are over the cap is the end of the docs cut.
- **DM history in characters.** A DM sends at most `history_chars` of the
  guest's newest whole question/answer pairs (and at most `history_turns`).
  A newest pair longer than the whole budget is cut, the answer's end
  first, rather than sending no history.
- **Chat search for this request only.** The agents have no tools, so the
  agent searches deterministically. When a room question has words (at least
  4 letters, not common words or agent nicks) that aren't in the recent
  window, and the room has older lines outside the window (its transcript
  has dropped lines, or a catch-up filled it), the agent sends
  `HISTORY SEARCH <room> <word>` for up to `search_keywords` of them, longest
  first. It keeps lines not already in the window (no `system-*`, NSFW or
  command lines), the newest `search_hits` within `search_chars`, and passes
  them before the recent lines as "earlier messages in this room that may be
  relevant". They are part of that one request: never added to the
  transcript or the history, so the next request starts clean. The agent
  waits at most `search_timeout` seconds and answers with whatever arrived.
  The search runs when the question's turn with the model comes (under the
  single-flight lock), so answers keep the order questions arrived in. It
  doesn't search until the IRCd on this connection has answered a HISTORY
  request. HISTORY replies carry no request id, so they are matched to
  requests in the order sent, per room. A request is never dropped from
  that queue, so a timed-out search's late reply is still taken as its own.
  Instead, no search is sent while a room has 32 unanswered requests, and a
  reconnect releases every waiter.

## Several agents in one room

Two or more sandbox agents (for example `sbx-ask` on `cortex-spark2` and
`sbx-bolt` on `worker-spark`) can share the guest rooms. List each one in the
others' `peer_agents`; typically one answers every human line
(`answer_unaddressed: true`) and the others are mention-only. The loop guard
(`_should_answer`):

- **Humans.** An explicit `@nick` decides who answers: `@sbx-bolt …` is
  bolt's alone, even if it also names sbx-ask. A bare nick counts only when
  nobody is `@`-tagged. With no tag at all, only the `answer_unaddressed`
  agent answers. DMs are always answered.
- **Agents.** A peer's message is answered only when it deliberately tags this
  agent as `@nick` in its body. The leading `<nick>:` prefix every reply starts
  with, or a bare nick (an agent telling a guest about another agent), does
  not count.
- **Hop cap.** At most `max_agent_hops` (default 1) peer messages are
  answered per room after each human message; a human speaking resets it.
  Agents can hand a guest's question to each other, but never keep talking
  on their own.
- **System bots.** Lines from system bots (`system-<server>-<bot>`, such as
  the welcome bot's "Welcome … on CULTURE.DEV" greeting) are never answered,
  even when they `@`-tag the agent — a guest join must not draw an agent
  reply.
- **Prompt.** The system prompt lists the peers, tells the agent to explain
  to guests how to reach them (`@nick`), and to write a peer's nick without
  `@` when only mentioning it.

- **Agents tag peers only when asked.** In an answer, `@peer` stays a tag
  only if the line being answered mentions that peer (the guest asked to
  involve it); otherwise it is written as `@`, a zero-width space, then the
  nick. That reads the same, and a guest who copies it still reaches the
  peer (as a bare mention), but the peer does not take it as a deliberate
  tag. Prompting alone wasn't enough: in a demo the model wrote "start your
  message with @sbx-bolt" and sbx-bolt answered.
- **NSFW still reaches the owner.** A guest line left to a peer is still
  checked by the `answer_unaddressed` agent and flagged to its `flag_log` /
  `owner_channel`; the tagged peer declines it.

irc-lens mirrors the same rule for its "`<agent>` is thinking" indicator.

**Known limitation.** The agent doesn't track who is in a room, so a guest
line that tags only a peer is left to that peer even when the peer is down or
not in the room; the guest then gets no answer. Run peers under the same
supervision as `sbx-ask`, and remove a peer from `peer_agents` when it is
retired.

## Safety properties

- **No tools, ever.** Every request comes from `build_request`, which never
  emits `tools`/`tool_choice`/`functions`, always sets
  `chat_template_kwargs.enable_thinking=false`, always includes a short-answer
  instruction, and clamps `max_tokens` to 700.
- **Bounded concurrency.** Model calls are single-flight. At most `max_active`
  distinct guests are admitted; others get a one-line queue-position message
  and are served as slots free up.
- **Commands are declined** by a deterministic regex pre-filter (no model call)
  and by the system prompt.
- **Curated, read-only knowledge.** `--bundle-from REPO` copies `README.md`,
  `CLAUDE.md` and `docs/**/*.md` minus internal working papers
  (`docs/superpowers`, `docs/specs`, `docs/plans`) into `knowledge_dir` (files 0444, dirs 0555).
  The agent reads only `*.md` there (symlinks ignored) and nothing else.
- **NSFW.** First-pass handling: a keyword pre-filter, plus a system-prompt
  instruction to reply with `[[NSFW]]`. Either path declines, appends a JSON
  line (`ts`, `reason`, `nick`, `excerpt`) to `flag_log`, and posts
  `FLAG nsfw from <nick>: <excerpt>` to `owner_channel` if set. Keyword lists
  are crude by design; expect false negatives and tune as needed.
- The gateway key is never logged or echoed.

## Tests

`tests/test_sandbox_agent.py` runs the IRC side against a real server on a
random port and the model side against a local recording HTTP server.
