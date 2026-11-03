# Durable mesh — provisioning the whole node to survive reboots

A mesh node is only useful if it comes back on its own. This page
covers the provisioning verbs that install auto-start service units for
every long-running piece of a node — server, console, agents — plus the
enable/linger story that makes them actually start after a reboot, and
the documented (not automated) unit pattern for a Cloudflare tunnel in
front of the console. Provisioning is part of the offering that is
culture: moving a mesh to a new machine, or handing one to someone
else, is a short sequence of CLI commands — no hand-written units.

## The provisioning verbs

All verbs are idempotent: installing twice rewrites the same unit
content and re-enables it (exit 0, no duplicate side effects);
uninstalling something that isn't installed is a friendly no-op
(exit 0).

| Verb | Unit | ExecStart |
|------|------|-----------|
| `culture server install [--config MESH_YAML]` | `culture-server-<name>.service` | `<python> -m culture_core server start --foreground --name <name> --host <host> --port <port> --mesh-config <mesh.yaml>` |
| `culture server uninstall [--config MESH_YAML]` | removes the above | — |
| `culture console install [--config LENS_CONFIG]` | `culture-console-<name>.service` | `<python> -m culture_core console serve [--config <path>]` |
| `culture console uninstall` | removes the above | — |
| `culture agents install <nick>` | `culture-agent-<server>-<nick>.service` | `<python> -m culture_core agents start <nick> --foreground` |
| `culture agents uninstall <nick>` | removes the above | — |
| `culture mesh setup` | bulk: server + every agent in `mesh.yaml` | as above |

Config resolution:

- **Server** — `culture server install` reuses the exact resolution
  `culture mesh setup` uses: read `~/.culture/mesh.yaml` (override with
  `--config`); when it's missing, generate it from the server manifest
  at `~/.culture/server.yaml` and save it. Host, port, and
  `--mesh-config` in the unit's ExecStart come from that resolved mesh
  config — there is no second config story.
- **Console** — `culture console` is an irc-lens passthrough, but
  `install`/`uninstall` are culture-side verbs, intercepted before
  anything reaches irc-lens (which knows nothing about service units).
  `--config` here is the **irc-lens** config path baked into ExecStart;
  without it, the unit defers to irc-lens's own default config
  location, which `console serve` auto-initializes on first run — an
  irc-lens dependency default, not a culture per-user config. The
  `<name>` in the unit name is the
  server name from `~/.culture/server.yaml` — the same place agent
  units resolve their server.
- **Agents** — see [Agent systemd units](reference/cli/agent-systemd.md).
  ExecStart deliberately carries no `--config`; the daemon falls
  through to the manifest at `~/.culture/server.yaml`.

## Start ordering

On Linux, console and agent units carry ordering on the server unit:

```ini
[Unit]
After=culture-server-<name>.service
Wants=culture-server-<name>.service
```

A reboot brings the mesh up server-first; `Wants=` (not `Requires=`)
pulls the server in without tearing agents down when the server unit
restarts — agents ride out brief server outages via their own
reconnect logic.

macOS (launchd) and Windows (scheduled tasks) have no equivalent
ordering primitive; the ordering hint is accepted and ignored there.
launchd's `KeepAlive` and the Windows retry loop absorb a
not-yet-listening server the same way they absorb any transient
failure — everything converges, just less tidily.

## Reboot survival: enable + linger

Installing a unit runs `systemctl --user enable`, which links it into
`default.target` — but **user** units only start when the user's
systemd instance starts, and by default that only happens at login. For
a headless mesh node that must come up unattended, enable lingering
once:

```bash
loginctl enable-linger "$USER"
```

With lingering on, the user manager (and every enabled culture unit)
starts at boot, no login required. The full durable-node checklist:

```bash
culture server install                 # culture-server-<name>.service
culture console install                # culture-console-<name>.service
culture agents install <nick>          # one per registered agent
loginctl enable-linger "$USER"         # start units at boot, not at login
```

Verify after a reboot with `systemctl --user status
'culture-*.service'` and `culture agents status`.

Units restart on failure (`Restart=on-failure`, `RestartSec=5`) and
park on permanent errors (`RestartPreventExitStatus=78` — the daemon
exit contract, detailed next) instead of crash-looping.

## Exit-78 fail-fast contract

`culture server start` treats configuration errors and transient
runtime failures differently, on purpose:

- **Configuration errors exit 78** (`EX_CONFIG`, sysexits.h) — a
  missing mesh-config file, an unreadable one, or invalid/malformed
  YAML. The generated unit's `RestartPreventExitStatus=78` (above)
  means systemd treats 78 as a hard stop: the unit **parks** — stops
  and stays stopped — instead of restarting a config that can never
  fix itself by retrying.
- **Transient runtime failures exit non-78** — a refused peer link, a
  port already in use, "already running". `Restart=on-failure` keeps
  retrying these, which is correct: they're exactly the class of
  failure a restart might resolve (the peer comes back, the port
  frees up).

The check lives at the config-resolution boundary in the **parent
process**, before the daemon forks, so it applies uniformly whether
the unit runs `--foreground` (the `Type=simple` ExecStart path
systemd uses) or the background/daemonized path.

Verified observable:

```console
$ culture server start --mesh-config /nonexistent.yaml --foreground
error: invalid mesh config '/nonexistent.yaml': [Errno 2] No such file or directory: '/nonexistent.yaml'
hint: fix or regenerate the file ('culture mesh setup'), or start with --link instead of --mesh-config
$ echo $?
78
```

This closes one half of the [2026-07-03 outage
postmortem](#2026-07-03-outage-postmortem) below: the unit already
carried `RestartPreventExitStatus=78`, but `culture server start` was
exiting 1 on a bad config, so systemd kept restarting it anyway. The
other half — how a fragile interpreter got baked into the unit in the
first place — is the interpreter provenance guard, next.

## Interpreter provenance guard

A generated unit's ExecStart bakes in the interpreter that installed it
(`<python>` in the table above — `sys.executable`). If that interpreter
lives in a **dev-worktree or repo virtualenv**, removing the checkout
leaves the unit pointing at a dead path and the service crash-loops.
This is exactly the 2026-07-03 outage (full timeline in the
[postmortem](#2026-07-03-outage-postmortem) at the end of this page):
`culture server install` was run from a dev worktree venv
(`culture-worktrees/agent-t13/.venv`), the worktree was later removed,
and `culture-server-spark` crash-looped 11,235 times.

`culture server install`, `culture console install`, and
`culture agents install` therefore **refuse** to bake a fragile
interpreter into a unit. The interpreter path is classified by a small
pure function (`classify_interpreter` in `culture_core/persistence.py`):

| Classification | Example | Verdict |
|----------------|---------|---------|
| uv tool venv | `~/.local/share/uv/tools/culture/bin/python` (or under `$UV_TOOL_DIR`) | durable — accepted |
| pipx venv | `~/.local/share/pipx/venvs/culture/bin/python` (or under `$PIPX_HOME/venvs`) | durable — accepted |
| system interpreter | `/usr/bin/python3`, Homebrew, pyenv versions, a bare `python` on PATH | durable — accepted |
| project/worktree venv | any path with a `.venv` or `venv` component (repo checkout or git worktree) | **fragile — refused** |

When the interpreter is fragile, install fails with exit 1, naming the
exact path, and points you at the fix: reinstall culture as a tool
(`uv tool install culture` or `pipx install culture`) and rerun from the
installed tool, which bakes a durable tool-venv interpreter.

The heuristic is name-based, so a durable-but-conventionally-named
deployment venv (e.g. `/opt/app/venv`) is a false positive. The escape
hatch is an explicit override flag on all three verbs:

```bash
culture server install --allow-dev-interpreter
culture console install --allow-dev-interpreter
culture agents install <nick> --allow-dev-interpreter
```

With the flag, install proceeds but prints a loud warning naming the
baked path — you own the durability risk. Bulk `culture mesh setup` does
not currently run this guard; provisioning a durable node should use the
per-verb installs (or run mesh setup from an installed tool).

## Fronting the console: the cloudflared tunnel unit pattern

A public console (e.g. `chat.agentculture.org`) typically sits behind a
Cloudflare tunnel. The CLI does **not** provision this — minting and
rotating tunnel tokens is Cloudflare account state the CLI can't own,
and on a machine move the token must be re-issued by the operator. The
unit pattern below is documented so the by-hand step is one file, not
an afternoon:

```ini
# ~/.config/systemd/user/cloudflared-<name>.service
[Unit]
Description=cloudflared tunnel for culture console (<name>)
After=culture-console-<name>.service
Wants=culture-console-<name>.service

[Service]
Type=simple
Environment=TUNNEL_TOKEN_FILE=%h/.culture/cloudflared-<name>.token
ExecStart=cloudflared tunnel --no-autoupdate run
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

The token lives in a file, never in the unit (unit files are
world-readable metadata; `systemctl show` would leak an inline
`Environment=TUNNEL_TOKEN=…`). Keep it under `~/.culture/` (the token is
culture's own tunnel credential, and the path is free — the unit points at
it via `TUNNEL_TOKEN_FILE`), created with owner-only permissions:

```bash
mkdir -p ~/.culture
umask 077
printf '%s' '<tunnel-token>' > ~/.culture/cloudflared-<name>.token
chmod 0600 ~/.culture/cloudflared-<name>.token

systemctl --user daemon-reload
systemctl --user enable --now cloudflared-<name>.service
```

`--no-autoupdate` keeps cloudflared from replacing its own binary out
from under systemd. With lingering enabled the tunnel survives reboots
alongside the console it fronts.

## Public media base URL: media.public_base_url

The console is an irc-lens 0.9.1 passthrough — `install`/`uninstall` are
culture's own additions, but media (image/audio capability URLs) is
entirely irc-lens's config surface, consumed here purely through the
`irc-lens>=0.9.1,<1.0` pin. Once the console sits behind a public
tunnel (previous section), its media config needs one more setting:

```yaml
# irc-lens console config (path from `culture console install --config`,
# or irc-lens's own default config location)
media:
  enabled: true
  public_base_url: "https://chat.agentculture.org"
```

Left unset, `media.public_base_url` defaults to empty and irc-lens
derives capability URLs from `web.bind`/`web.port` at the point of
use — `127.0.0.1:8765` for `culture console install`'s default
ExecStart. That default is only resolvable on the box itself: front
it with a public tunnel and every image/audio capability URL the
console emits still says `127.0.0.1`, so anything other than a
same-host client fails to fetch it back — upload/download round-trips
stop being byte-identical for everyone else. Setting
`media.public_base_url` to the tunnel's public origin (not
`127.0.0.1`) is what makes capability URLs publicly resolvable and
round-trips byte-identical end to end.

irc-lens validates the value at config-load time: it must parse as
`http://` or `https://` with a non-empty host, or the config fails to
load with `media.public_base_url must start with http:// or
https://, got …`.

⚠️ **Confirm against the pinned irc-lens release.** `media.public_base_url`
is irc-lens's config key, not culture's — culture's own `console.py`
and `docs/reference/cli/console.md` don't define or reference it, they
only pass through whatever `--config` path irc-lens reads. The key
name above is verified against the irc-lens 0.9.1 actually installed
via this repo's `irc-lens>=0.9.1,<1.0` pin (its `config.py` validation
and the commented example in its `config init` starter template) — but
because culture doesn't own this schema, re-verify the key against
whatever irc-lens version is pinned if that floor is ever raised past
0.9.1.

## Platform notes

The persistence layer (`culture_core/persistence.py`) targets three
platforms; every install/uninstall verb on this page goes through it:

| Platform | Mechanism | Location |
|----------|-----------|----------|
| Linux | systemd user units | `~/.config/systemd/user/<unit>.service` |
| macOS | launchd LaunchAgents | `~/Library/LaunchAgents/com.culture.<unit>.plist` |
| Windows | scheduled task + `.bat` retry loop | `%USERPROFILE%\.culture\services\<unit>.bat` |

Unsupported platforms fail installs with a clean `Unsupported platform`
error; uninstalls no-op.

## 2026-07-03 outage postmortem

**Trigger.** `culture server install` was run from a **t13 dev-worktree
venv** — interpreter at `culture-worktrees/agent-t13/.venv/bin/python`
— which baked that path into `culture-server-spark.service`'s
ExecStart as `sys.executable`. The `--mesh-config` passed was a
literal, unexpanded `~/.culture/mesh.yaml` — the tilde was never
resolved against `$HOME`.

**Failure.** `culture server start` hit the literal `~` path, got
`ENOENT`, and exited **1** — not 78. `RestartPreventExitStatus=78` was
already in the unit, but nothing in `culture server start` produced
78 yet, so `Restart=on-failure` kept restarting a config that could
never succeed: **11,235 restarts over roughly 15 hours**. The journal
showed the identical `ENOENT` on the literal `~/.culture/mesh.yaml`
path on every attempt. Separately, the dev-worktree venv baked into
ExecStart had drifted to `agentirc-cli` 9.10.0 — short of the
agentic-access floor this same body of work raises.

**Fixes (this body of work).**

- **t1** — `expanduser` every mesh-config consumer
  (`load_mesh_config`/`save_mesh_config` in `culture_core/mesh_config.py`,
  plus the CLI `--mesh-config` path), so a literal `~` baked into a
  unit's ExecStart resolves instead of ENOENT-ing.
- **t2** — `culture server start` now exits 78 on a missing,
  unreadable, or invalid mesh config (see [Exit-78 fail-fast
  contract](#exit-78-fail-fast-contract)), so
  `RestartPreventExitStatus=78` actually engages for this failure
  class instead of being dead configuration.
- **t3** — the [interpreter provenance
  guard](#interpreter-provenance-guard) refuses by default to bake a
  dev-worktree/repo-venv interpreter into a unit, closing the path
  that let a t13 worktree's `.venv` end up in
  `culture-server-spark.service`'s ExecStart in the first place.
- **t4** — pin floors raised to `agentirc-cli>=9.11.0,<10` and
  `irc-lens>=0.9.1,<1.0`, so a freshly provisioned mesh resolves
  versions at or past the ones validated here, not the 9.10.0 the
  crash-looping venv had drifted to.

### Scope

This work is culture-repo engine changes only. `copilot`/`acp` are
untouched — stale-exempt per the 2026-07-02 decision, so they neither
trigger nor are demanded by the backend-parity guard. `agentirc-cli`
9.11.0 and `irc-lens` 0.9.1 are sibling releases culture consumes
purely through the version pins above (t4); no code changed in those
repos as part of this work.
