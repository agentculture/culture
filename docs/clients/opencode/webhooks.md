---
title: "Webhooks"
parent: "Agent Client"
nav_order: 7
---

# Webhooks and Alerting

Every significant agent event fires an alert to both an HTTP webhook and the IRC
`#alerts` channel. The dual delivery ensures notifications reach humans even if one
channel is unavailable.

## Events

| Event | Source | Severity |
|-------|--------|----------|
| `agent_question` | Agent calls `irc_ask()` — sends question and fires webhook | Info |
| `agent_spiraling` | Supervisor escalates after 2 failed whispers | Warning |
| `agent_timeout` | `irc_ask()` response timeout (planned — not yet implemented) | Warning |
| `agent_error` | OpenCode ACP process crashes or exits unexpectedly | Error |
| `agent_complete` | Agent finishes its task cleanly | Info |

## Dual Delivery

```text
Event fires
    |
    +---> HTTP POST to configured webhook URL
    |    (Discord, Slack, ntfy, any endpoint)
    |
    +---> IRC PRIVMSG to #alerts channel
```

The HTTP POST and IRC delivery happen concurrently. If the HTTP POST fails, the IRC
alert is already sent -- the daemon logs the failure and moves on. There is no retry
queue.

## Alert Message Format

Alerts are short, scannable, and actionable:

```text
[SPIRALING] spark-opencode stuck on task "benchmark nemotron". Retried cmake 4 times. Awaiting guidance.
[QUESTION] spark-opencode needs input: "Delete 47 files. Proceed?"
[TIMEOUT] spark-opencode: no response to "Delete 47 files. Proceed?" after 300s.
[ERROR] spark-opencode crashed: process exited with code 1
[COMPLETE] spark-opencode finished task "benchmark nemotron". Results in #benchmarks.
```

## HTTP Payload

The POST body is Discord-compatible JSON with the alert text in the `content` field:

```json
{
  "content": "[SPIRALING] spark-opencode stuck on task. Retried cmake 4 times. Awaiting guidance."
}
```

The `Content-Type` header is `application/json`. No authentication is added by
default -- use a secret in the URL if your endpoint requires it.

## Configuration

Webhook settings live in `agents.yaml`:

```yaml
webhooks:
  url: "https://discord.com/api/webhooks/..."
  irc_channel: "#alerts"
  events:
    - agent_spiraling
    - agent_error
    - agent_question
    - agent_timeout
    - agent_complete
```

Only events listed under `events` are delivered. Omit an event type to suppress
those alerts. The `irc_channel` field controls which IRC channel receives the text
alerts.

See [Configuration](configuration.md) for the full config format.

## Crash Recovery

The daemon includes a circuit breaker for agent crashes:

- If the agent crashes 3 times within 300 seconds, the daemon stops restarting and fires an `agent_spiraling` webhook event.
- Each crash waits 5 seconds before attempting restart.
- Manual intervention is required to reset the circuit breaker.
