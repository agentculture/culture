# Agent systemd units

Two paths install per-agent systemd user units to
`$HOME/.config/systemd/user/culture-agent-<nick>.service` so the agents
come up automatically after reboot under `Restart=on-failure`:

- `culture mesh setup` / `culture mesh update` — bulk install/refresh
  for every agent in `~/.culture/mesh.yaml`.
- `culture agents install <nick>` / `culture agents uninstall <nick>` —
  one-off install or removal for a single agent registered in
  `~/.culture/server.yaml`. Decoupled from `mesh.yaml`; useful for
  hosts that manage agents directly and for recovery.

The unit's `ExecStart` is intentionally minimal:

```text
ExecStart=/usr/bin/culture agents start <nick> --foreground
```

No `--config` is passed. `culture agents start` falls through to the
argparse default — `~/.culture/server.yaml`, the manifest the rest of
the CLI uses. Anything specified in that manifest (workdir, channels,
backend) is what the daemon reads.

The `<python>` (`/usr/bin/culture` above) is the interpreter that ran
the install — `sys.executable`. `culture agents install` refuses to bake
a fragile dev-worktree/repo virtualenv interpreter into the unit (it
would crash-loop when the checkout is removed); pass
`--allow-dev-interpreter` to override. See
[the interpreter provenance guard](../../durable-mesh.md#interpreter-provenance-guard).

Agent units are ordered behind the server unit:

```text
After=culture-server-<name>.service
Wants=culture-server-<name>.service
```

The server name resolves from the same manifest as the agent's nick,
so a reboot brings the mesh up server-first. `Wants=` (not
`Requires=`) means a server restart does not tear agents down — they
reconnect on their own. The sibling `culture server install` /
`culture console install` verbs provision the units agents order
behind; see [Durable mesh](../../durable-mesh.md).

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
# Uninstall the stale unit (disables, stops, removes file, runs daemon-reload):
culture agents uninstall <nick>

# Re-install with the current unit body (no --config pin):
culture agents install <nick>

# Confirm it's healthy:
systemctl --user status culture-agent-<nick>.service
```

If the manifest at `~/.culture/server.yaml` itself is stale (e.g. the
nick's workdir was renamed or its `culture.yaml` deleted), tidy it
before re-installing:

```bash
culture agents unregister <suffix>     # see `culture agents status` for hints
culture agents register <workdir>      # if the workdir's culture.yaml is fresh
culture agents install <suffix>
```

`culture agents install` / `uninstall` operate on a single agent listed
in `~/.culture/server.yaml` — no `mesh.yaml` required. For bulk install
across every agent in `mesh.yaml`, use `culture mesh setup` instead.

## Migrating from `culture agent` (13.0.0)

The singular `culture agent` noun was removed in 13.0.0; all verbs moved to
`culture agents`. Units installed by older versions still contain
`ExecStart=… culture agent start …`, which is now invalid. For each managed
agent, re-run:

```bash
culture agents install <nick>
systemctl --user daemon-reload
```

This rewrites the unit's `ExecStart` to `culture agents start …`. The service
**name** (`culture-agent-<nick>.service`) is unchanged.

## See also

- [`culture mesh setup` / `update`](./index.html) — top-level mesh
  lifecycle that owns unit installation.
- [`culture agents register` / `unregister`](./index.html) — manifest
  management for the `~/.culture/server.yaml` source of truth.
