# `culture console replica`

`culture console replica` synchronizes approved irc-lens accounts from the
active machine into a warm standby. Run the push command on `spark`. The
standby needs no credentials or network access back into `spark`.

```bash
/usr/local/bin/culture console replica \
  --source /var/lib/irc-lens/accounts.db \
  --host spark2 \
  --destination /var/lib/irc-lens/accounts.db \
  --remote-culture ~/.local/bin/culture
```

The command takes a transactionally consistent snapshot through SQLite's
online backup API, builds an allow-listed staging database, and hashes its
rows. It pushes only when that hash differs from the last successful push,
when the last success is older than `--max-age` (24 hours by default), or when
`--force` is present. An unchanged run transfers nothing and exits zero.

The successful hash and timestamp live in
`~/.culture/state/console-replica.json` by default. `--state-file` selects a
different file. The file is published atomically with mode `0600`; a failed
push or mismatched standby hash leaves it unchanged, so the next timer run
retries.

`--max-age` accepts seconds or an `s`, `m`, `h`, or `d` suffix, such as `24h`.

## Private transport

`--host` must be one of:

- a host alias without a dot, normally an entry such as `spark2` in
  `~/.ssh/config`;
- an RFC 1918, link-local, loopback, or Tailscale `100.64.0.0/10` address; or
- a `.ts.net`, `.local`, `.lan`, or `.home.arpa` name.

Public addresses and public DNS names are refused. Before transferring data,
the command expands the host with `ssh -G`, rejects proxy hops, resolves the
configured destination, and requires every returned address to be private,
link-local, loopback, or in Tailscale's range.

The default transport is `scp`; `--transport rsync` uses rsync over SSH. Both
copy a mode-`0600` staging file into the same directory as the destination.
The primary then invokes this command over SSH:

```bash
~/.local/bin/culture console replica apply STAGED \
  --destination /var/lib/irc-lens/accounts.db --expected-hash ROW_HASH
```

`--remote-culture` makes the executable explicit because a non-interactive SSH
session often has a restricted `PATH`. It defaults to
`~/.local/bin/culture`. The replica command never invokes a bare `irc-lens`
command remotely.

Create the destination directory in advance and make it writable by the SSH
account. On any copy, apply, or hash-verification failure, the primary exits
nonzero and also makes a best-effort `rm -f` of the remote stage. The standby
apply command deletes its stage on every exit, including validation or merge
failure.

## What is synchronized

The staging database contains this explicit allow-list:

| Table | Columns | Rows |
|---|---|---|
| `users` | `email`, `role`, `state`, `nickname` | approved users only |
| `passwords` | `email`, `hash` | approved users only |
| `grants` | `email`, `resource_kind`, `resource`, `level` | approved users only |

Approved users' Argon2 password hashes therefore reach the standby. Plaintext
passwords do not. The command prints only row counts; it does not print an email,
password hash, row digest, host, or path. The primary passes its row digest to
`apply`, which checks the merged rows against it before committing, so a mismatch
leaves the standby unchanged.

On the standby, `apply` checks its installed irc-lens version and the source
column metadata in the stage. It refuses a newer source without changing the
destination. If the destination has no schema yet, apply constructs it by
opening the real irc-lens `GuestStore` and `AccessStore` before merging.

Apply uses one `BEGIN IMMEDIATE` transaction with a busy timeout. It replaces
all rows in `users`, `passwords`, and `grants`, and records the irc-lens
initialization marker. Accounts removed on the primary are consequently
removed from the standby. Every other table and row remains in place,
including guests, consents, inputs, flags, attempts, sessions, tokens, pending
sign-ins, trusted devices, agent credentials, extensions, the audit trail,
bans, deletions, rooms, sign-in budget, nickname history, and existing admin
import records.

Existing `GuestStore` and `AccessStore` objects continue to work because the
live database file is not replaced. Existing app-session rows also remain.
irc-lens rechecks each session's account approval on requests and in
`app_session.sweep_once`; therefore a session belonging to an account removed
by the merge stops authenticating, and its linked live IRC session is closed
by the normal sweep.

## Schedule on the active machine

Run the service every five minutes on `spark`. A systemd user service and timer
can be:

```ini
# ~/.config/systemd/user/culture-console-replica.service
[Unit]
Description=Synchronize approved console accounts to spark2

[Service]
Type=oneshot
ExecStart=/usr/local/bin/culture console replica --source /var/lib/irc-lens/accounts.db --host spark2 --destination /var/lib/irc-lens/accounts.db --remote-culture ~/.local/bin/culture --max-age 24h
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

Enable the timer on the active machine only:

```bash
systemctl --user daemon-reload
systemctl --user enable --now culture-console-replica.timer
```

Use the absolute path of the local `culture` executable in `ExecStart`. Keep
guest mode disabled in the standby's irc-lens configuration.
