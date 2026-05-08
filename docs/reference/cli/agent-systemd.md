---
title: "Agent systemd units"
parent: "CLI"
grand_parent: "Reference"
nav_order: 20
sites: [agentirc, culture]
description: "How `culture mesh setup`/`update` install agent systemd units, and how to recover from stale 10.3.x units that pinned a legacy --config path."
permalink: /reference/cli/agent-systemd/
---

# Agent systemd units

`culture mesh setup` and `culture mesh update` write per-agent systemd
user units to `~/.config/systemd/user/culture-agent-<nick>.service` so
the agents come up automatically after reboot under
`Restart=on-failure`.

The unit's `ExecStart` is intentionally minimal:

```text
ExecStart=/usr/bin/culture agent start <nick> --foreground
```

No `--config` is passed. `culture agent start` falls through to the
argparse default — `~/.culture/server.yaml`, the manifest the rest of
the CLI uses. Anything specified in that manifest (workdir, channels,
backend) is what the daemon reads.

## Recovering from stale pre-10.3.5 units

Before culture 10.3.5, the unit generator pinned a legacy
`--config <workdir>/.culture/agents.yaml` path that culture had
already migrated away from. On machines where that per-workdir file no
longer exists, the daemon exited 1 immediately, systemd restarted it 5
seconds later, and the cycle repeated indefinitely (real deployments
hit restart counters in the tens of thousands). To the user it looked
like "agents not awake" — every mention landed during a 5-second
restart window with no daemon listening.

If `journalctl --user -u culture-agent-<nick>.service` shows a tight
loop of `[Errno 2] No such file or directory: '<workdir>/.culture/agents.yaml'`
followed by `Scheduled restart job, restart counter is at NNNN`, you
have a stale unit. Recover with:

```bash
# Stop and remove the stale unit:
systemctl --user disable --now culture-agent-<nick>.service
rm ~/.config/systemd/user/culture-agent-<nick>.service
systemctl --user daemon-reload

# Confirm it's gone:
systemctl --user status culture-agent-<nick>.service   # should say "not-found"

# If the manifest at ~/.culture/server.yaml is also stale (e.g. the
# nick's workdir was renamed or its culture.yaml deleted), tidy it up:
culture agent unregister <suffix>     # see `culture agent status` for hints
culture agent register <workdir>      # if the workdir's culture.yaml is fresh

# Start the agent manually to confirm it works without systemd in the way:
culture agent start <nick>

# If you want systemd auto-start back, re-run mesh setup for a populated
# mesh.yaml:
culture mesh setup --config ~/.culture/mesh.yaml
```

`culture mesh setup` only re-installs units for agents listed in the
`agents:` block of `mesh.yaml`. If your `mesh.yaml` has an empty
`agents: []`, the recovery stops at the manual `culture agent start`
step — there is currently no per-agent install command decoupled from
`mesh.yaml`.

## See also

- [`culture mesh setup` / `update`](./index.html) — top-level mesh
  lifecycle that owns unit installation.
- [`culture agent register` / `unregister`](./index.html) — manifest
  management for the `~/.culture/server.yaml` source of truth.
