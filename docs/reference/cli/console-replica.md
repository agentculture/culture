# `culture console replica`

`culture console replica` pushes approved irc-lens accounts from the active
machine to a warm standby. Run it on `spark`; `spark2` only needs SSH access
from `spark` and never needs credentials or network access back into `spark`.

```bash
culture console replica \
  --source /var/lib/irc-lens/accounts.db \
  --host spark2 \
  --destination /var/lib/irc-lens/accounts.db
```

`--host` must be one of:

- a host alias without a dot, normally an entry such as `spark2` in
  `~/.ssh/config`;
- an RFC 1918, link-local, loopback, or Tailscale `100.64.0.0/10` address; or
- a `.ts.net`, `.local`, `.lan`, or `.home.arpa` name.

Public IP addresses and public DNS names are refused. The default transport is
`scp`; `--transport rsync` uses rsync over SSH instead. Both use SSH batch mode,
land a `0600` staging file in the destination directory, and atomically rename
it over the destination. Create that directory on the standby in advance and
make it writable by the SSH account. The command does not print database
contents or paths.

Before copying, the command expands the host with `ssh -G`, rejects SSH proxy
hops, resolves the configured destination, and requires every returned address
to be private, link-local, loopback, or in Tailscale's address range. Thus a
short alias that secretly points at a public destination is refused too.

## What is copied

The command uses SQLite's online backup API, so a running lens may continue to
read and write while a transactionally consistent snapshot is taken. It then
builds a new SQLite file from an explicit allow-list:

| Table | Columns | Rows |
|---|---|---|
| `users` | `email`, `role`, `state`, `nickname` | approved users only |
| `passwords` | `email`, `hash` | approved users only |
| `grants` | `email`, `resource_kind`, `resource`, `level` | approved users only |
| `admin_imports` | `email_hash` | only the non-personal `store-initialised` marker |

Nothing else is copied. In particular, the standby receives no guests,
consents, inputs, flags, attempts, sessions, tokens, pending sign-ins, trusted
devices, agent credentials, extensions, audit trail, bans, deletions, rooms, or
sign-in budget. Because there are no guest rows, guest retention is vacuous and
does not depend on matching `guest_mode.retention_days`. An active session on
`spark` is deliberately not warm: an approved person signs in again on
`spark2` with the same password, role, and grants. The initialization marker
prevents the standby's legacy `auth.allowed_emails` list from changing those
copied roles when the lens opens the fresh database.

The source and standby `irc-lens` versions are checked before any private bytes
are sent. A newer source version is conservatively treated as a newer schema;
the command refuses to replace an older standby store and tells the operator to
upgrade the standby lens first.

## Schedule on the active machine

Use the scheduler on `spark`. For example, a systemd user timer can run a
wrapper containing the command above. A minimal service/timer pair is:

```ini
# ~/.config/systemd/user/culture-console-replica.service
[Unit]
Description=Copy approved console accounts to spark2

[Service]
Type=oneshot
ExecStart=/usr/local/bin/culture console replica --source /var/lib/irc-lens/accounts.db --host spark2 --destination /var/lib/irc-lens/accounts.db
```

```ini
# ~/.config/systemd/user/culture-console-replica.timer
[Unit]
Description=Refresh spark2 console accounts

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
Persistent=true

[Install]
WantedBy=timers.target
```

Then enable it on `spark` only:

```bash
systemctl --user daemon-reload
systemctl --user enable --now culture-console-replica.timer
```

Use the absolute path of the installed `culture` executable in `ExecStart`.
Keep guest mode disabled in the standby's irc-lens configuration.
