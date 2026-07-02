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
  without it, the unit defers to irc-lens's own default
  (`~/.config/irc-lens/config.yaml`), which `console serve`
  auto-initializes on first run. The `<name>` in the unit name is the
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
exit contract) instead of crash-looping.

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
