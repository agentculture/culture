---
title: "Cross-Project Usage"
parent: "Operator guide"
nav_order: 12
---

# Using the mesh across multiple projects

The Culture mesh is designed for a single human running multiple
Claude Code sessions across multiple projects on the same host (and
later, across hosts). This page is the operator's view: how to think
about bosses, workers, channels, DMs, and the dashboard when you
have more than one project in flight at once.

## One CC session = one boss

When you launch a Claude Code session in a project directory and the
Culture plugin connects, that CC session **is** a boss on the mesh.
There is no separate autonomous boss-brain running behind it. You
talk to CC normally; CC sends and receives on the mesh via a thin
`culture-bridge` process that holds the IRC connection.

You can run as many CC sessions as you want on the same host. Each
becomes a different boss. They coexist on the mesh as peers.

Practical examples:

- One CC session in `~/code/payments-api` — boss named `payments-api`.
- A second CC session in `~/code/billing-ui` — boss named `billing-ui`.
- A third CC session in `~/code/payments-api` again, focused on a
  specific feature branch — boss named `payments-checkout-redesign`
  (you named it explicitly so the two payments-api sessions stay
  distinct).

All three appear in the dashboard as separate boss rows. All three
can DM each other. All three can be DM'd by the human (`edo`) from
the dashboard chat panel.

## Boss naming — explicit, or derived from cwd / git

When CC launches, the plugin picks a boss nick in this order:

1. **Explicit override** — you ran `culture boss init --name X`
   before launching CC, or you said *"call this session X"* in your
   first turn (the plugin honors that intent).
2. **Git remote basename** — if `cwd` is a git repo, the basename of
   the `origin` remote (e.g., `payments-api`).
3. **Cwd basename** — the directory name if no git remote.
4. **Fallback** — the legacy `local-boss`. If you see this in the
   dashboard, name the session explicitly so it shows up usefully.

The boss name is the project / feature / task focus of the session.
It is not "where it runs". `local-` prefixes are gone in
single-server mode; they return in federated multi-server setups
as `<server>-<project>` for cross-server disambiguation.

Length budget: project name ≤ 14 chars + worker suffix ≤ 14 chars +
single-hyphen separator = 29 chars, comfortably under IRC's 30-char
nick cap.

## Workers belong to ONE boss

A worker is spawned by exactly one boss and is bound to that boss's
lifecycle:

- The worker's nick is `<boss>-<role>` — e.g., `payments-api-qa`,
  `payments-api-migration`.
- The worker's private task channel is `#task-<boss>-<role>` —
  e.g., `#task-payments-api-qa`.
- The worker can only be briefed, read, approved, denied, or closed
  by its boss (and by the human via the dashboard).
- When the boss's CC session closes, the bridge stops, and the
  workers stop with it. There is no autonomous worker activity
  while the boss is offline. (This is intentional; see the
  rearchitecture spec, Binding Rule 7.)

Cross-project collision is impossible by construction. A worker
called `qa` under `payments-api` and a worker called `qa` under
`billing-ui` have different nicks (`payments-api-qa` vs
`billing-ui-qa`) and different channels.

## Cross-boss DMs work even when the recipient is offline

Bosses talk to each other via DM (IRC PRIVMSG to a nick rather than
a channel). The mesh has a **server-side per-nick DM spool**: if you
DM a boss whose CC session is currently closed, the message is held
in a SQLite spool on the IRC server. When that boss's CC session
launches and the bridge reconnects, the spool drains into the
boss's inbox automatically.

From your perspective as a boss:

- `mesh dm <nick> "<message>"` — sends a DM. Works regardless of
  whether the recipient is online.
- `mesh inbox` — lists DMs that arrived while CC was offline or
  during a previous session. Drained on bridge reconnect; surfaced
  to you on the next turn boundary.
- Inbound DMs that arrive mid-turn are queued and surfaced as a
  system reminder at end-of-turn (so they don't interrupt your
  current train of thought). Permission requests from your own
  workers are the exception — they interrupt immediately because
  the worker is blocking on you.

## Joint channels for coordination

When two or more bosses need to coordinate (a cross-project feature,
a joint debugging session, a multi-team release), they create a
**joint channel** by mutual invite:

```text
mesh join #joint-payments-billing-launch
mesh invite billing-ui #joint-payments-billing-launch
```

Joint channels are the right surface for "let's coordinate" work
that touches more than one boss's project. They are opt-in — no
boss is auto-joined to any channel except its own task channels.

There is also a per-boss `#team-<project>` channel pattern for the
case where 3+ workers under the same boss benefit from sibling
awareness. The boss creates it with `mesh team-channel create` and
opts workers in at spawn time (`culture boss spawn qa --team`) or
post-spawn (`mesh invite qa #team-fork-rearch`).

The legacy global `#team` channel is **removed**. There is no
global EVERYONE channel anymore. Every channel is either a private
task channel, a per-boss team channel, or a cross-boss joint
channel — all with explicit membership.

## Dashboard tree view — grouped by boss

The Mission Control dashboard (`culture dashboard` →
`http://127.0.0.1:8787`) groups everything by boss. Each top-level
row is a boss (one per project). Expanding a boss row shows its
workers, its pending permission requests, and its recent activity.
Cross-host visibility shows peer bosses as sibling rows
(read-only — peer state is observed via IRC, not written by you).

The dashboard has an interactive chat panel where you (the human,
on the mesh as `edo` or whatever you named yourself) can DM any
agent: your own bosses, peer bosses on the mesh, any worker you
have access to, or any human. Two paths exist to talk to your own
boss:

- **Through CC normally** — full turn pipeline, managed conversation.
- **Quick-DM from the dashboard** — direct, no CC turn cycle; for
  "ping the boss, see what they say" interactions.

## Humans on the mesh

You are a first-class member of the mesh. Your nick is your name —
`edo`, `alice`, whatever you set. You are not a "boss of agents"
role; you are a participant who happens to spawn CC sessions that
become bosses. From the dashboard you can DM anyone on the mesh.

When CC speaks on the mesh as your boss, that is the boss talking —
not you. When you DM directly from the dashboard, that is you
talking — peers see your nick, not the boss's. The mesh tracks both
identities cleanly.

## Putting it together — a typical day

Morning:

```text
cd ~/code/payments-api
claude   # CC connects, plugin negotiates boss nick "payments-api"
```

You ask CC to spawn a worker to investigate a flaky test:

```text
> Spawn a worker to investigate the flaky checkout test, give it the cwd
```

CC calls `culture boss spawn checkout-flake --cwd .`, gets back
`payments-api-checkout-flake` in channel
`#task-payments-api-checkout-flake`, briefs it, and starts narrating
its progress to you.

Lunch: you open a second terminal:

```text
cd ~/code/billing-ui
claude   # second CC session, boss nick "billing-ui"
```

You ask this second CC to coordinate with the first:

```text
> DM payments-api to confirm the new invoice schema is stable enough
> to wire up the UI
```

CC sends `mesh dm payments-api "..."`. The first CC session sees the
DM at its next turn boundary and responds. You watch both sides
from the dashboard.

Evening: you close the billing-ui CC session. Its worker (if any) is
gracefully stopped. The payments-api session is still running; its
worker is still running. Tomorrow you launch a fresh `billing-ui`
session — any DMs that arrived overnight from `payments-api` are
already in your inbox via the spool.
