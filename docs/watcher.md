# Culture Watcher

Deterministic mesh-state watchdog (introduced v8.19.19).

The watcher is the **closed-loop guard** for the mesh — a background
process that polls authoritative state files (`~/.culture/daemon-log/*.jsonl`,
`~/.culture/audit/*.jsonl`, `~/.culture/perm-queue/`) and fires
deterministic alerts when known failure patterns trigger. The
orchestrator does not have to babysit the dashboard to notice problems.

The watcher runs out-of-process, never holds an LLM token quota, and
survives any other component's restart. Detection is pure file I/O
plus a `os.kill(pid, 0)` liveness probe.

## Patterns

The MVP ships with five detectors. Each emits a deterministic `key`
(`pattern_name:target`) so the cooldown cache can suppress repeated
reports of the same problem within a configurable window.

| Pattern | Severity | Trigger |
|---|---|---|
| `silent_death` | high | `agent_start` recorded but no `agent_exit`, AND pidfile-backed PID is gone. |
| `crash_burst` | high | ≥3 `crash` records inside 5 minutes for the same nick. |
| `token_spike` | medium | Assistant input-token sum exceeds 50 000 in any 10-minute window. |
| `perm_escalation_above_ceiling` | high | A pending permission request names a tool on the boss's ceiling denylist. |
| `mission_stuck` | medium | Boss running ≥2 hours with neither `engaged` daemon-log nor assistant audit. |

Thresholds are tunable per pattern in `~/.culture/watcher.yaml`.

## Alert sinks

| Sink | Default | What it sends |
|---|---|---|
| IRC | on | `PRIVMSG` to `target_nick` (the boss/orchestrator) and to `#alerts`. Goes over a `PersistentObserver` so a 30-second poll doesn't open + tear down a TCP+IRC handshake every pass. |
| Email | off (opt-in) | One SMTP message per pattern firing. Password sourced from `password_env` (never the literal). STARTTLS by default. |
| Webhook | off (opt-in) | JSON POST to `url`. If `secret_env` is set, the env-var's value is sent in `X-Culture-Watcher-Secret`. |

Failures in any sink are logged but never propagate — a broken SMTP
relay never takes the watcher down.

## CLI

```sh
culture watcher start              # foreground poll loop
culture watcher once               # one pass + exit (good for cron)
culture watcher status             # show recent firings + ages
culture watcher test --target NICK # synthesize a fake silent_death and fan out
```

`status` is purely local: it prints what `~/.culture/watcher-state.json`
recorded. Use it after a `test` to confirm a sink fired.

## Config — `~/.culture/watcher.yaml`

```yaml
poll_interval_seconds: 30
cooldown_seconds: 600     # default per-firing dedupe window

patterns:
  - silent_death
  - crash_burst
  - token_spike
  - perm_escalation_above_ceiling
  - mission_stuck

# Tools that must escalate to the human (not just the boss).
# A pending request whose tool_name matches a list entry fires
# `perm_escalation_above_ceiling`.
boss_ceiling:
  local-boss:
    - mcp__atlassian__create_issue
    - mcp__slack__send_message

alerts:
  irc:
    enabled: true
    target_nick: local-boss
    fallback_channel: "#alerts"
  email:
    enabled: false
    smtp_host: smtp.gmail.com
    smtp_port: 587
    smtp_user: ops@example.com
    password_env: WATCHER_SMTP_PASSWORD
    from_addr: ops@example.com
    to_addrs:
      - me@example.com
    use_starttls: true
  webhook:
    enabled: false
    url: https://example.com/culture-alert
    secret_env: WATCHER_WEBHOOK_SECRET
```

A missing file is treated as defaults (all five patterns on,
IRC-only). The watcher never panics on bad config; bad fields are
warned and ignored.

## Persistence

* `~/.culture/watcher.yaml` — config.
* `~/.culture/watcher-state.json` — last firings + cooldowns.
  Crash-safe by atomic rename; corrupt → treated as empty.

## What it does NOT do

* The watcher does **not** restart agents. Restarting is the boss's
  job; the watcher only surfaces the signal.
* The watcher does **not** take rate-limit risks on its sinks. SMTP
  / webhook errors are logged once and the firing is still recorded
  in `watcher-state.json` so the cooldown won't try again until the
  window expires.
* The watcher is **not** a full incident manager. It's a deterministic
  signal generator — pair it with the dashboard's Pending Approvals
  pane for the action surface.
