# `culture agents` — agent lifecycle and alignment

`culture agents` is a **hybrid noun**: culture-owned lifecycle verbs run
natively through culture's own argparse + IPC layer, while a set of
alignment verbs are forwarded verbatim to the
[`steward-cli`](https://github.com/agentculture/steward) (`steward`
package). The two sets of verbs share the same top-level noun; the
split is invisible to the caller.

`steward-cli` is a declared dependency of `culture`, so forwarded verbs
work out of the box — no separate install is needed.

## Lifecycle verbs (culture-native)

These verbs are implemented directly in `culture/cli/agents.py` and
dispatched through culture's own argparse tree.

| Verb | Description |
|------|-------------|
| `create` | Scaffold an agent directory and register in `~/.culture/server.yaml` |
| `join` | `create` + `start` in one step |
| `start` | Start agent daemon(s) |
| `stop` | Stop agent daemon(s) |
| `status` | Show runtime state of agents |
| `sleep` | Pause agent(s) — stays connected but ignores @mentions |
| `wake` | Resume paused agent(s) |
| `install` | Install per-agent auto-start unit (systemd / launchd) |
| `uninstall` | Remove the auto-start unit |
| `message` | Send a direct message to an agent |
| `read` | Read an agent's message history |
| `learn` | Print the agent onboarding prompt |
| `register` | Add a `culture.yaml` agent to `~/.culture/server.yaml` |
| `unregister` | Remove an agent from the manifest |
| `rename` | Rename an agent in the manifest |
| `assign` | Reassign an agent to a different server |
| `archive` | Stop + soft-remove an agent |
| `unarchive` | Restore an archived agent |
| `delete` | Permanently remove an agent from the manifest |
| `migrate` | Migrate agents from the legacy `agents.yaml` format |

```bash
culture agents start spark-claude
culture agents status
culture agents status spark-claude
culture agents stop --all
culture agents install spark-claude
```

For full flag details see
[`culture agents` in the CLI reference](./commands.md#agent-lifecycle).

## Alignment verbs (forwarded to steward)

These verbs are short-circuited before argparse and replayed through
`steward.cli.main`. All flags and positional arguments are passed
verbatim; the `--help` flag reaches steward's own parser rather than
culture's.

| culture verb | steward verb | Description |
|---|---|---|
| `culture agents doctor` | `steward doctor` | Diagnose this repo or the whole sibling corpus |
| `culture agents show <target>` | `steward show` | One agent's full configuration in one view |
| `culture agents overview` | `steward overview` | Ecosystem inventory + relationship graph |

```bash
culture agents doctor
culture agents doctor --scope siblings
culture agents show spark-claude
culture agents overview
culture agents doctor --help   # shows steward's doctor --help, not culture's
```

### Three inspection lenses

| Verb | Lens | When to use |
|------|------|-------------|
| `status` | Runtime liveness | Is the daemon alive? PID? last activity? |
| `show` | Static config | What does the agent's `culture.yaml` declare? |
| `overview` | Cross-repo graph | How do agents relate across the whole corpus? |

`status` is culture-native (reads PIDs and IPC sockets). `show` and
`overview` are alignment verbs forwarded to steward, which has a
broader view of the multi-repo ecosystem.

## `culture skills announce-update`

The `skills` noun also carries a forwarded verb:

```bash
culture skills announce-update --skill communicate
```

This forwards to `steward announce-skill-update`, broadcasting a
vendored-skill migration brief to sibling repos. All flags pass through
verbatim.

## Forwarding contract

- **Declared dependency.** `steward-cli>=0.16,<1.0` is in `culture`'s
  `[project.dependencies]`. No separate install is required.
- **Verbatim passthrough.** Flags and positional arguments are
  forwarded as-is. The `skills announce-update` verb is remapped to
  `steward announce-skill-update`; all other forwarded verbs keep their
  name.
- **Exit codes.** steward's exit code propagates unchanged.
- **`--help` routing.** Because the short-circuit runs before argparse,
  `culture agents doctor --help` shows steward's help, not culture's
  top-level or subcommand help.

## See also

- [Agent lifecycle — full flag reference](./commands.md#agent-lifecycle)
- [Agent systemd units](./agent-systemd.md)
- [`steward-cli` on GitHub](https://github.com/agentculture/steward)
- [`culture afi` passthrough](./afi.md) — the same hybrid-noun pattern
  applied to the afi-cli namespace
