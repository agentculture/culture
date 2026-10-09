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
| `knowledge_budget_chars` | `48000` | prompt budget for the bundle (sets latency: ~7 s/answer at ~10k tokens on cortex-spark2) |
| `owner_channel`, `flag_log` | none, `~/.culture/sandbox/flags.jsonl` | where NSFW flags go |

Adding a model (for example an AWS-hosted one) is a config entry with its own
`url` and `key_env`; nothing else changes.

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
