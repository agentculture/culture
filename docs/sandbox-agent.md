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
| `knowledge_budget_chars` | `48000` | prompt budget for the bundle (sets latency: ~7 s/answer at ~10k tokens on cortex-spark2) |
| `owner_channel`, `flag_log` | none, `~/.culture/sandbox/flags.jsonl` | where NSFW flags go |
| `peer_agents` | `{}` | other agents sharing the rooms, `{nick: one line about it}` (or a list of nicks); enables the loop guard below and goes into the system prompt so the agent can tell guests how to reach them |
| `max_agent_hops` | `1` | peer messages answered per room after each human message |

Adding a model (for example an AWS-hosted one) is a config entry with its own
`url` and `key_env`; nothing else changes.

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
- **Prompt.** The system prompt lists the peers, tells the agent to explain
  to guests how to reach them (`@nick`), and to write a peer's nick without
  `@` when only mentioning it.

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
