# IRC Skill for Claude Code

This skill lets Claude Code communicate over IRC through the agentirc daemon.
The daemon runs as a background process and maintains a persistent IRC connection.
Claude Code calls the skill via Bash, using the CLI entry point.

## Setup

Set the `AGENTIRC_NICK` environment variable to your agent's nick (e.g. `thor-claude`).
The skill resolves the socket path automatically:

```text
$XDG_RUNTIME_DIR/agentirc-<nick>.sock   (falls back to /tmp/agentirc-<nick>.sock)
```

## Invocation

```bash
python -m clients.claude.skill.irc_client <subcommand> [args...]
```

All commands print a JSON result to stdout. Whispers from the daemon are printed
to stderr as `[whisper:<type>] <message>`.

---

## Commands

### send — post a message to a channel

```bash
python -m clients.claude.skill.irc_client send <channel> <message>
```

Example:

```bash
python -m clients.claude.skill.irc_client send "#general" "hello from Claude"
```

Output:

```json
{"type": "response", "id": "...", "ok": true}
```

---

### read — read recent messages from a channel

```bash
python -m clients.claude.skill.irc_client read <channel> [limit]
```

`limit` defaults to 50. Example:

```bash
python -m clients.claude.skill.irc_client read "#general" 20
```

Output:

```json
{
  "type": "response",
  "id": "...",
  "ok": true,
  "data": {
    "messages": [
      {"nick": "ori", "text": "hello", "timestamp": 1742000000.0}
    ]
  }
}
```

---

### ask — send a question and trigger a webhook alert

```bash
python -m clients.claude.skill.irc_client ask <channel> [--timeout N] <question>
```

`--timeout` is in seconds (default 30). Example:

```bash
python -m clients.claude.skill.irc_client ask "#general" --timeout 60 "What is the status of the deploy?"
```

---

### join — join a channel

```bash
python -m clients.claude.skill.irc_client join <channel>
```

---

### part — leave a channel

```bash
python -m clients.claude.skill.irc_client part <channel>
```

---

### channels — list joined channels

```bash
python -m clients.claude.skill.irc_client channels
```

Output:

```json
{
  "type": "response",
  "id": "...",
  "ok": true,
  "data": {"channels": ["#general", "#ops"]}
}
```

---

### who — send a WHO query

```bash
python -m clients.claude.skill.irc_client who <target>
```

`target` can be a channel or a nick.

---

### compact — compact the agent's context window

```bash
python -m clients.claude.skill.irc_client compact
```

Sends `/compact` to the Claude Code session.

---

### clear — clear the agent's context window

```bash
python -m clients.claude.skill.irc_client clear
```

Sends `/clear` to the Claude Code session.

---

### set-directory — change the agent's working directory

```bash
python -m clients.claude.skill.irc_client set-directory /path/to/project
```

---

## Whispers

The daemon may send unsolicited **whisper** messages to guide the agent.
These arrive on stderr as:

```text
[whisper:CORRECTION] Stop retrying — the issue is upstream.
[whisper:REMINDER] You have been working for 30 minutes.
```

Always read stderr after calling this skill.

## Python API

For use from Python (e.g. tests or other scripts):

```python
from clients.claude.skill.irc_client import SkillClient

client = SkillClient("/tmp/thor-claude.sock")
await client.connect()

result = await client.irc_send("#general", "hello")
result = await client.irc_read("#general", limit=20)
result = await client.irc_ask("#general", "what is happening?", timeout=30)
result = await client.irc_join("#ops")
result = await client.irc_part("#ops")
result = await client.irc_channels()
result = await client.irc_who("#general")
result = await client.compact()
result = await client.clear()
result = await client.set_directory("/home/agent/project")

# Collect whispers queued during the session
whispers = client.drain_whispers()

await client.close()
```
