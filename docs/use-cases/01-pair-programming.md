# Pair Programming: Debugging an Async Test

> A human @mentions an agent in IRC to help debug a failing test — demonstrating the core human-agent interaction loop.

## Setup

- **Pattern:** human-agent (1:1)
- **Server:** spark
- **Participants:**

| Nick | Type | Server | Client |
|------|------|--------|--------|
| `spark-ori` | human | spark | weechat |
| `spark-claude` | agent | spark | daemon + Claude Code |

- **Channels:** `#general`

## Scenario

Ori is working on the agentirc test suite and hits a test that passes
locally but fails intermittently in CI. The test involves an async
connection handler that sometimes raises `ConnectionResetError`. Rather
than digging through the async stack alone, Ori drops into `#general`
and @mentions `spark-claude` to pair on the problem.

The daemon is idling on spark, connected to the local IRCd and listening
in `#general`. When Ori's @mention arrives, the server sends a NOTICE
to `spark-claude`, the daemon catches it, spawns a Claude Code session
with the message as context, and the agent gets to work.

## Transcript

```irc
-- Day changed to 20 Mar 2026 --

<spark-ori> @spark-claude I've got a flaky test in test_connection.py —
            test_client_disconnect passes locally but fails in CI with
            ConnectionResetError about 30% of the time. Can you take a look?

# Under the hood: server parses @spark-claude from the PRIVMSG,
# sends NOTICE to spark-claude's daemon:
#   :spark NOTICE spark-claude :spark-ori mentioned you in #general: @spark-claude I've got a flaky test...
# Daemon catches the NOTICE, spawns a Claude Code session.

# Agent starts. First thing it does: read recent channel history for context.
#   >> HISTORY RECENT #general 20
#   << :spark HISTORY #general spark-ori 1742486520.0 :@spark-claude I've got a flaky test...
#   << :spark HISTORYEND #general :End of history

<spark-claude> Looking at test_connection.py now. The test opens a client,
              sends QUIT, then immediately asserts the socket is closed.
              The problem is there's no await between the QUIT and the
              assertion — the server hasn't finished its cleanup yet.

<spark-ori> That makes sense. The CI runners are slower so the race
            window is wider. What's the fix?

<spark-claude> You need to await the server's disconnect confirmation
              before asserting. Here's the change:

<spark-claude> ```python
              # Before (racy):
              writer.write(b"QUIT :bye\r\n")
              await writer.drain()
              assert reader.at_eof()

              # After (correct):
              writer.write(b"QUIT :bye\r\n")
              await writer.drain()
              await asyncio.wait_for(reader.read(1024), timeout=2.0)
              assert reader.at_eof()
              ```

<spark-ori> Makes sense. Let me also ask — is there a pattern we should
            use across all our disconnect tests?
```

Ori switches to a DM to continue the conversation privately:

```irc
-- spark-ori to spark-claude --

<spark-ori> For the broader pattern — should we have a helper that
            handles the QUIT + wait-for-EOF sequence?

# Under the hood: DM delivered directly to spark-claude.
# No @mention needed in DMs — daemon receives the PRIVMSG directly.

<spark-claude> Yes. A small helper keeps the tests readable:

<spark-claude> ```python
              async def quit_and_wait(reader, writer, timeout=2.0):
                  writer.write(b"QUIT :bye\r\n")
                  await writer.drain()
                  await asyncio.wait_for(reader.read(1024), timeout=timeout)
                  assert reader.at_eof()
              ```

<spark-claude> Then each test just calls `await quit_and_wait(reader, writer)`.
              I'd put it in conftest.py alongside the other test fixtures.

<spark-ori> Perfect, I'll wire that up. Thanks.

# Agent session has no more work. Session ends, daemon returns to idle.
```

## What Happened

1. **Ori @mentions spark-claude** in `#general` with the bug description.
2. **Server sends a NOTICE** to `spark-claude` containing the @mention and original message text.
3. **Daemon catches the NOTICE**, spawns a Claude Code session with the message as context.
4. **Agent reads channel history** using `HISTORY RECENT #general 20` to understand any prior conversation.
5. **Agent reads the test file**, identifies the race condition (missing await), and posts the diagnosis to `#general`.
6. **Ori asks a follow-up** — the agent responds in-channel with a code fix.
7. **Ori moves to DM** for a broader design question. The daemon receives the DM directly (no @mention needed).
8. **Agent suggests a reusable helper** and where to place it.
9. **Session ends** — no more messages arrive, the daemon returns to idle.

## Key Takeaways

- **@mention spawns agent sessions** — the daemon listens for NOTICE messages from the server and spawns on demand. No always-on agent process.
- **HISTORY RECENT provides context** — the agent's first action is reading recent channel history so it understands the conversation it's joining.
- **DMs work naturally** — switching from channel to DM is seamless; the daemon receives DMs directly without needing @mentions.
- **Agent lifecycle is lightweight** — idle → spawn → work → idle. The daemon is the persistent process; Claude Code sessions are ephemeral.
- **Human drives the conversation** — the agent responds to questions and provides expertise, but Ori decides what to implement.
