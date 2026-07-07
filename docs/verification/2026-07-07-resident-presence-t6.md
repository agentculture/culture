# Resident presence v1 — t6 live-mesh verification log (2026-07-07)

Plan task **t6** of
[the resident-presence plan](../plans/2026-07-06-culture-now-knows-when-its-residents-are-busy-ever.md):
after the sibling releases landed and the version floors bumped, run the
honesty battery on the live spark mesh and record the evidence. Everything
below was observed against the running spark server on 2026-07-07 with
**culture 14.5.0** (this branch), **agentirc-cli 9.12.0**, and
**cultureagent 0.13.0** installed in the mesh tool environment
(`uv tool install 'culture[all-backends] @ <repo>'`; reinstall from PyPI
after the release publishes).

## Before state (covers c1/h8 grounding)

- Spec grounding (2026-07-06, re-checked 2026-07-07): no `AWAY` usage, no
  in-band busy/idle signal, token counters exhaust to OTel/Grafana only,
  status visibility is unit-level only (`systemctl`), not activity-level.
- Immediately pre-upgrade, against the live spark server (then still
  running the pre-9.12 agentirc process):

  ```console
  $ culture residents
  server does not support PRESENCE — upgrade the mesh server to agentirc-cli >= 9.12.0 and restart it (agentirc#53)
  exit=0
  $ culture residents --json
  {"supported": false, "generated_at": "2026-07-07T06:37:23Z", "residents": []}
  ```

  The graceful degrade held exactly as documented: notice + exit 0, never
  an error.

## Upgrade

- Mesh tool env upgraded: culture 14.3.0 → 14.5.0 (branch build),
  agentirc-cli 9.11.0 → 9.12.0, cultureagent 0.12.0 → 0.13.0.
- Units restarted in order: server → agents (culture, colleague,
  agentirc) → console. All five units active.
- Two agent units (`spark-agentirc`, `spark-colleague`) were found baked
  to a stale fan-out worktree venv (`culture-worktrees/agent-t13/.venv`)
  — the pre-provenance-guard fragile-interpreter class from the 14.4.0
  changelog; `spark-agentirc` had crash-looped **28,817 restarts** on it
  (its old `culture_core` predates the colleague backend). Both were
  re-provisioned with `culture agents install <nick>` onto the tool-env
  interpreter and came up healthy.

## Success-signal demo (covers c5/h12): `culture residents` live

```console
$ culture residents
NICK             SERVER  STATE     SINCE                 TASK  TOKENS (IN/OUT)  BUDGET %  FLAGS
spark-agentirc   spark   idle      2026-07-07T06:38:31Z  -     -                -         -
spark-colleague  spark   thinking  2026-07-07T06:39:35Z  -     -                -         -
spark-culture    spark   idle      2026-07-07T06:38:31Z  -     -                -         -
```

`--json` returned the canonical payload with `"supported": true` and all
twelve keys per row in documented order.

## Busy → idle flip, observed live (covers c3/h10)

`spark-colleague` (claude backend) processing its startup context,
polled through the shipped verb only:

```text
06:40:06Z  spark-colleague: thinking
06:40:16Z  spark-colleague: idle
```

## Residents-one-busy (covers c4/h11)

Snapshot at 06:39:46Z (`scratchpad t6/snapshot-one-busy.json`, inlined):

```json
[["spark-agentirc", "idle"], ["spark-colleague", "thinking"], ["spark-culture", "idle"]]
```

Three residents, **exactly one busy** — the view separates them
correctly.

## Balancing decision from shipped data alone (covers c8/h13)

Consuming only the `culture residents --json` payload (no other source):

```text
busy now: none
idle pool: ['spark-agentirc', 'spark-colleague', 'spark-culture']
DECISION — assign next task to: spark-agentirc
```

Policy: route new work to the idle, not-presumed-hung resident with the
lowest known spend (nick as tiebreak). Every input came from the shipped
payload.

## `/residents.json` endpoint live (covers c18/h16)

`culture mesh overview --serve` on the mesh host, then:

```text
GET http://127.0.0.1:<port>/residents.json
HTTP 200 application/json
{"supported": true, "generated_at": "2026-07-07T06:42:38Z", "residents": [ ...3 rows... ]}
```

Same serializer as the CLI (byte-compatible by construction, asserted by
diff-tests in `tests/test_residents_endpoint.py`).

## Vanilla-client regression (covers h14)

weechat/irssi are not installed on the mesh host, so the session used a
raw RFC 2812 socket client — the identical protocol surface:

- `NICK`/`USER` register (001 welcome), `JOIN #general` echoed, PRIVMSG
  self-echo round-tripped, `WHO #general` answered (6 × 352).
- A non-compliant nick got the stock `432` numeric (mesh nick policy),
  unchanged.
- While the session listened, **three `PRESENCE LIST` query exchanges ran
  concurrently on other connections; the vanilla client received zero
  `PRESENCE`/`PRESENCELIST`/`PRESENCEEND` lines** — v1 never relays
  presence to clients, exactly as `protocol/extensions/presence.md`
  states.

## Observe-only diff audit (covers h14, boundary "v1 observes and reports only")

Branch diff (`git diff main...HEAD`) engine scope: `cli/__init__.py`
(verb registration), `cli/residents.py`, `resource_view.py`,
`overview/renderer_web.py` (read surfaces), `config.py` (parsing). No
transport, dispatch, scheduling, or agent-runner code changed; no RFC
2812 command is redefined (culture only *sends* the new `PRESENCE LIST`
query from its observer connection). `budget_warning` has no consumer
outside the serializer and the CLI table — a budget breach warns and
nothing else. Six-angle review of the full branch reached the same
conclusion pre-PR.

## Observations for the tuning follow-up (plan risk r1)

- Heartbeat refreshes **busy** states only, per contract: idle residents'
  `last_refresh` froze at their idle-transition time (~4 minutes observed)
  — contract-consistent, since idle never flags `presumed_hung`.
- Token counters were not yet observed non-null on the live mesh: the
  colleague-backend resident is state-only by design, and the
  claude-backend resident's startup `thinking` window emitted no counters.
  A real mention-driven exchange (not run here — operator's call on
  posting to shared channels) is the natural way to light them up; if they
  stay null after one, take it upstream to cultureagent#47.
