# Ops Tooling Design

Issue: [#68](https://github.com/OriNachum/culture/issues/68)

## Context

culture has solid server federation, agent lifecycle management, and mesh
visibility. What it lacks is operational resilience: daemons don't survive
reboots, new machines require manual SSH-and-type setup, there's no update
workflow, and server-to-server links don't auto-reconnect after the initial
startup attempt.

This design adds four capabilities to close those gaps: persistence,
scaffolding, fleet updates, and self-healing.

## Scope

All changes are **local-only** — no cross-machine coordination, no central
orchestrator. Each machine manages itself. Cross-platform: Linux, macOS,
Windows.

No new external dependencies. Pure Python stdlib + existing pyyaml.

---

## 1. Persistence — Per-Process Auto-Start

### Goal

Server and agents come back after reboot without manual intervention.

### Approach

Keep the existing standalone commands (`culture server start`,
`culture start`) as primary. Add platform-specific auto-start entries that
invoke these commands. The OS service manager handles restart-on-crash.

Each process (server, each agent) gets its own auto-start entry:

```
OS Auto-Start Layer
├── culture-server-spark       → culture server start --foreground ...
├── culture-agent-spark-claude → culture start spark-claude --foreground
└── culture-agent-spark-codex  → culture start spark-codex --foreground
```

### Platform Backends

**Linux — systemd user units** in `~/.config/systemd/user/`:

```ini
[Unit]
Description=culture server spark

[Service]
Type=simple
ExecStart=culture server start --foreground --name spark --port 6667
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

After writing: `systemctl --user daemon-reload && systemctl --user enable <unit>`.
Warn if `loginctl enable-linger` is not active (required for user services
without login session).

**macOS — launchd plists** in `~/Library/LaunchAgents/`:

```xml
<dict>
  <key>Label</key><string>com.culture.server-spark</string>
  <key>ProgramArguments</key>
  <array><string>culture</string><string>server</string><string>start</string>
         <string>--foreground</string><string>--name</string><string>spark</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>~/.culture/logs/server-spark.log</string>
  <key>StandardErrorPath</key><string>~/.culture/logs/server-spark.log</string>
</dict>
```

After writing: `launchctl load <plist>`.

**Windows — Scheduled Tasks + restart wrapper:**

Use `schtasks` to create a logon-triggered task. Since Windows Task Scheduler
has limited restart-on-crash support, generate a `.bat` wrapper that loops:

```bat
:loop
culture server start --foreground --name spark --port 6667
timeout /t 5
goto loop
```

The scheduled task runs the `.bat` at logon. Stored in
`%USERPROFILE%\.culture\services\`.

### --foreground Flag

Add `--foreground` to `server start` and `start` commands. When set:

- Skip `os.fork()` / `os.setsid()` — run in the current process
- Still write PID file and set up signal handlers
- Required for service managers that expect foreground processes
- Standalone (fork-and-detach) mode remains the default for interactive use

### New Module

`culture/persistence.py`:

- `install_service(name, command, description)` — detect platform, generate
  and install the appropriate auto-start entry
- `uninstall_service(name)` — remove auto-start entry and stop if running
- `list_services()` — return installed service names and their status

---

## 2. Scaffolding — mesh.yaml + `culture setup`

### Goal

Get a new machine into the mesh without manual fiddling.

### Two-Phase Approach

1. **Config generation (agent-assisted):** Claude asks the user about server
   name, links, agents, and writes `~/.culture/mesh.yaml`. No secrets in
   this step — passwords left as empty strings.

2. **Service installation (user-executed):** User runs `culture setup` which
   reads mesh.yaml, prompts for any missing link passwords, generates
   auto-start entries, and starts everything.

### mesh.yaml Schema

File: `~/.culture/mesh.yaml`

```yaml
server:
  name: spark
  host: "0.0.0.0"
  port: 6667
  links:
    - name: thor
      host: 192.168.1.12
      port: 6667
      password: ""        # empty = prompt during setup
      trust: full

agents:
  - nick: claude          # suffix only; full nick = spark-claude
    type: claude
    workdir: ~/projects/myproject
    channels:
      - "#general"
  - nick: codex
    type: codex
    workdir: ~/projects/other
    channels:
      - "#general"
```

### `culture setup` Flow

```
culture setup [--config ~/.culture/mesh.yaml] [--uninstall]
```

1. Load mesh.yaml (error if missing).
2. For each link with empty password, prompt: `Link password for thor:`.
   Save passwords back to mesh.yaml.
3. Generate per-agent `agents.yaml` in each agent's workdir (bridge to
   existing config format used by daemon).
4. Generate auto-start entries via `persistence.py`.
5. Start server, wait for it to bind, start agents.
6. Print status summary.

`--uninstall`: stop all processes, remove auto-start entries.

Idempotent: re-running updates config and regenerates service files. Checks
if processes are already running before starting duplicates.

If an agent's workdir doesn't exist, create it. If multiple agents share a
workdir, their entries are merged into one `agents.yaml`.

### New Module

`culture/mesh_config.py`:

- `MeshConfig`, `MeshServerConfig`, `MeshLinkConfig`, `MeshAgentConfig`
  dataclasses
- `load_mesh_config(path)` / `save_mesh_config(config, path)` — yaml
  load/save following the existing atomic-write pattern from
  `clients/claude/config.py`

---

## 3. Fleet Updates — `culture update`

### Goal

Roll out code changes without the mesh going dark. Single command.

### `culture update` Flow

```
culture update [--dry-run] [--skip-upgrade] [--config ~/.culture/mesh.yaml]
```

1. Load mesh.yaml to know what's running on this machine.
2. If not `--skip-upgrade`: run `uv tool upgrade culture` (fall back to
   `pip install --upgrade culture` if `uv` not found). Report old →
   new version.
3. **Re-exec with new code:** After upgrade, exec
   `culture update --skip-upgrade` so the restart logic uses the new
   binary. This avoids stale in-memory imports.
4. Stop all agents (graceful SIGTERM, 5s timeout, SIGKILL fallback).
5. Stop server.
6. Regenerate auto-start entries (CLI path may have changed after upgrade).
7. Start server, wait for bind.
8. Start all agents.
9. Print status.

`--dry-run`: print what would happen without executing.

Downtime window: seconds between server stop and server start. Agents on
remote peers see the link drop and will auto-reconnect once the server is
back (via self-healing, below).

---

## 4. Self-Healing — S2S Link Auto-Reconnect

### Goal

Server-to-server links automatically reconnect after drops.

### Current State

- Agent → server reconnect: exists in `irc_transport.py` with exponential
  backoff (1s → 60s). Works well.
- Server → server links: attempted once at startup. If a peer is down, the
  link silently fails and is never retried. If an established link drops,
  it's never retried.

### Design

Add a background link retry manager to `IRCd`.

**State tracking** — new field in `IRCd.__init__`:

```python
self._link_retry_state: dict[str, dict] = {}
# peer_name -> {"delay": float, "task": asyncio.Task | None}
```

**Retry scheduling** — new method `IRCd._maybe_retry_link(peer_name)`:

1. Find matching `LinkConfig` from `self.config.links` by name. If none
   found (peer was the initiator, not in our config), do nothing.
2. If peer already has an active retry task, skip.
3. Create an asyncio task that: sleeps for `delay` (starting at 5s), calls
   `connect_to_peer()`, on success resets delay and removes retry state, on
   failure doubles delay (capped at 120s) and re-schedules.

**Trigger on link drop** — modify `_remove_link(link, squit=False)`:

After existing cleanup, if `not squit`, call `_maybe_retry_link(peer_name)`.

**Distinguish SQUIT from crash** — modify `ServerLink`:

- Add `self._squit_received = False` to `__init__`.
- In SQUIT handler: set `self._squit_received = True` before raising.
- In `handle()` finally block: pass `squit=self._squit_received` to
  `_remove_link`.

**Cancel retry on incoming connection** — in `ServerLink._try_complete_handshake`,
after registering the link, call `self.server.cancel_link_retry(peer_name)`.
This handles the case where both sides retry simultaneously.

**Initial startup** — in `_run_server` (cli.py), when initial
`connect_to_peer()` fails, call `ircd._maybe_retry_link(lc.name)` to start
the retry cycle.

**Shutdown** — in `IRCd.stop()`, cancel all retry tasks.

---

## Files to Modify

| File | Change |
|------|--------|
| `culture/persistence.py` | **NEW** — platform auto-start generation |
| `culture/mesh_config.py` | **NEW** — mesh.yaml dataclass + load/save |
| `culture/server/ircd.py` | Add `_link_retry_state`, `_maybe_retry_link`, `cancel_link_retry`; modify `_remove_link` signature; modify `stop()` |
| `culture/server/server_link.py` | Add `_squit_received` flag; pass `squit` to `_remove_link` in `handle()` finally; call `cancel_link_retry` in handshake |
| `culture/cli.py` | Add `setup` and `update` subcommands; add `--foreground` to `server start` and `start`; guard `os.fork()`/`os.setsid()`/`SIGKILL` for Windows |

## Files to Create

| File | Purpose |
|------|---------|
| `culture/persistence.py` | Platform-specific service install/uninstall/list |
| `culture/mesh_config.py` | MeshConfig dataclasses and YAML I/O |
| `tests/test_link_reconnect.py` | S2S auto-reconnect tests |
| `tests/test_mesh_config.py` | mesh.yaml round-trip tests |
| `tests/test_persistence.py` | Service file generation tests (mock subprocess) |
| `docs/ops-tooling.md` | User-facing documentation |

## Documentation

Write `docs/ops-tooling.md` covering:

- `culture setup` usage and mesh.yaml format
- `culture update` usage and flags
- Platform-specific auto-start details (systemd, launchd, Windows)
- S2S auto-reconnect behavior

Update `docs/cli.md` with new commands. Update the culture admin skill
(`culture/skills/culture/SKILL.md`) with ops tooling reference.

## Edge Cases

1. **Windows has no `os.fork()`**: Guard fork/setsid calls with
   `sys.platform != "win32"`. On Windows, `--foreground` is implied for
   daemon mode (process stays attached). The `.bat` wrapper handles restart.

2. **Windows signal handling**: `SIGKILL` doesn't exist. Use
   `os.kill(pid, signal.SIGTERM)` which maps to `TerminateProcess` on
   Windows. Add platform guard in `_server_stop` and `_stop_agent`.

3. **Retry race condition**: Both sides retry simultaneously, one gets
   rejected (duplicate link name). The `cancel_link_retry` call on
   successful handshake prevents infinite retry loops.

4. **systemd linger**: User services require `loginctl enable-linger`.
   `culture setup` checks and warns (or offers to enable).

5. **Self-upgrade re-exec**: After `uv tool upgrade`, the running process
   has stale imports. `os.execv` replaces it with a fresh process loading
   the new code.

6. **mesh.yaml vs agents.yaml**: mesh.yaml is the source of truth when
   using `culture setup`. It generates agents.yaml. Users not using
   mesh.yaml are unaffected.

## Verification

1. **Self-healing**: Start two linked servers. Kill one. Verify the other
   retries and reconnects when the killed server restarts. Verify SQUIT
   does NOT trigger retry.

2. **Persistence**: Run `culture setup`, reboot the machine, verify
   server and agents come back automatically. Test on each platform.

3. **Scaffolding**: On a fresh machine, write mesh.yaml, run
   `culture setup`, verify everything starts and links to the mesh.

4. **Updates**: Run `culture update`, verify package upgrades, services
   restart with new code, mesh reconnects.

## Implementation Order

1. Self-healing (S2S reconnect) — standalone, no new files beyond test
2. mesh_config.py — new module, no dependencies on others
3. persistence.py — new module, needs platform testing
4. CLI: `--foreground` flag — small change, prerequisite for persistence
5. CLI: `setup` command — ties together mesh_config + persistence
6. CLI: `update` command — ties together everything
7. Documentation and skill updates
