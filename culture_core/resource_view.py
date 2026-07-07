"""Shared resource-view seam: fetch and serialize the resident presence aggregation.

This module is the single source of truth for the mesh resource view —
per-resident presence state, token spend, and warn-only budget status. Two
front doors consume it and MUST stay byte-compatible:

* the ``culture residents`` CLI verb (``culture_core/cli/residents.py``);
* the upcoming resource-view HTTP endpoint for irc-lens (plan task t7),
  which reuses :func:`serialize_residents` for its payload.

Transport status (plan risks r3/r4)
-----------------------------------

The agentirc IRCd does **not** implement the PRESENCE query surface yet —
the server-side aggregation is the subject of the t3 hand-off brief
(agentirc#53). Until that lands and culture's agentirc floor is bumped,
every real server answers the query probe with ``421 Unknown command``,
which this module surfaces as :class:`PresenceUnsupportedError` so the
front doors can degrade gracefully (``supported: false``) instead of
failing.

The transport is deliberately isolated in ONE seam function,
:func:`_query_presence_wire`, speaking the *anticipated* wire shape
(``PRESENCE LIST`` -> ``PRESENCELIST :<json>`` lines -> ``PRESENCEEND``).
When agentirc answers the brief, only that function changes; the
:class:`Resident` model, the budget join, and the canonical serializer
stay put.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from culture_core.config import AgentConfig, ServerConfig, load_config_or_default
from culture_core.observer import RECV_TIMEOUT, IRCObserver
from culture_core.protocol.message import Message

# Default server config path. Deliberately duplicated from
# culture_core.cli.shared.constants: importing anything under
# culture_core.cli here would run the whole CLI package __init__, which
# imports the residents CLI module, which imports this module — circular.
_DEFAULT_CONFIG = "~/.culture/server.yaml"

# Shared error strings for the "culture server unreachable" failure mode.
# The residents CLI and the /residents.json endpoint must speak with one
# voice — same message, same remediation — so both import these instead of
# carrying their own copies.
UNREACHABLE_MESSAGE = "cannot connect to IRC server. Is the server running?"
UNREACHABLE_REMEDIATION = "start it with: culture server start"


def to_json(payload: dict) -> str:
    """THE single canonical ``json.dumps`` site for the resource view.

    Both front doors — ``culture residents --json`` and the
    ``/residents.json`` endpoint — emit exactly this function over
    :func:`serialize_residents` output, so byte-compatibility is enforced
    by construction, not by convention.
    """
    return json.dumps(payload)


class PresenceUnsupportedError(Exception):
    """The server is reachable but has no PRESENCE query surface.

    Raised while the aggregation side of the PRESENCE extension is pending
    in agentirc (agentirc#53). Front doors catch this and degrade to a
    ``supported: false`` resource view with exit/status success — a mesh
    without presence support is a known state, not an error.
    """


@dataclass
class Resident:
    """One row of the resource view.

    The first nine fields mirror the per-resident record the server
    aggregation returns (protocol/extensions/presence.md). The last three
    are culture-side derived fields, joined from the local manifest's
    ``AgentConfig.token_budget`` / ``token_budget_warn_pct`` when the nick
    matches a registered agent — ``None`` when no budget is configured or
    (for the spend-derived pair) when the resident sent no token counters.
    """

    nick: str
    server: str | None = None
    state: str | None = None
    since: str | None = None
    task: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    presumed_hung: bool = False
    last_refresh: str | None = None
    # Culture-side derived fields (never on the wire; see apply_budgets).
    token_budget: int | None = None
    budget_used_pct: float | None = None
    budget_warning: bool | None = None


# Canonical per-resident key order of the serialized payload. This IS the
# JSON schema order documented in docs/resident-presence.md — the t7
# endpoint and the CLI --json output both emit exactly this.
_RESIDENT_FIELDS: tuple[str, ...] = (
    "nick",
    "server",
    "state",
    "since",
    "task",
    "tokens_in",
    "tokens_out",
    "presumed_hung",
    "last_refresh",
    "token_budget",
    "budget_used_pct",
    "budget_warning",
)


def serialize_residents(
    residents: Sequence[Resident],
    supported: bool,
    *,
    now: datetime | None = None,
) -> dict:
    """Build THE canonical resource-view JSON payload.

    Pure (no I/O) and deterministic: residents are sorted by nick and every
    record carries all :data:`_RESIDENT_FIELDS` keys in fixed order, with
    ``None`` for anything unknown. The ``culture residents --json`` output
    and the t7 HTTP endpoint both emit exactly ``json.dumps`` of this dict —
    one serializer, one schema.

    ``now`` exists for deterministic tests; production callers omit it and
    get the current UTC time. A naive ``now`` is interpreted as UTC — never
    as host-local time (which is what a bare ``astimezone`` would do).
    """
    stamp = now if now is not None else datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    generated_at = (
        stamp.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )
    return {
        "supported": bool(supported),
        "generated_at": generated_at,
        "residents": [
            {name: getattr(resident, name) for name in _RESIDENT_FIELDS}
            for resident in sorted(residents, key=lambda r: r.nick)
        ],
    }


def _as_int(value: object) -> int | None:
    """Return the value as an int, or None. Bools are NOT counts."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _as_str(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _resident_from_wire(record: dict) -> Resident:
    """Build a Resident from one aggregation record, defensively typed."""
    return Resident(
        nick=str(record.get("nick") or ""),
        server=_as_str(record.get("server")),
        state=_as_str(record.get("state")),
        since=_as_str(record.get("since")),
        task=_as_str(record.get("task")),
        tokens_in=_as_int(record.get("tokens_in")),
        tokens_out=_as_int(record.get("tokens_out")),
        presumed_hung=bool(record.get("presumed_hung")),
        last_refresh=_as_str(record.get("last_refresh")),
    )


def apply_budgets(residents: Iterable[Resident], agents: Iterable[AgentConfig]) -> None:
    """Join local-manifest budget config onto fetched residents, by nick.

    For each resident whose nick matches a registered agent with a
    ``token_budget`` configured:

    * ``token_budget`` is copied over;
    * ``budget_used_pct`` = (tokens_in + tokens_out) / budget * 100, rounded
      to one decimal — but only when the resident actually reported token
      counters. A state-only resident keeps ``None`` (spend unknowable,
      never a false alarm);
    * ``budget_warning`` is True once used-pct reaches
      ``token_budget_warn_pct`` (inclusive), False below it, ``None`` when
      spend is unknowable.

    Warn-only by design: nothing anywhere acts on these flags in v1
    (docs/resident-presence.md).

    Budgets that are not a real int >= 1 are skipped entirely (all derived
    fields stay ``None``): the legacy agents.yaml loader constructs
    ``AgentConfig`` directly and bypasses the culture.yaml budget
    sanitizing, so ``token_budget: 0`` (or a bool, or a negative) can reach
    this join — it must never reach the division below.
    """
    by_nick = {agent.nick: agent for agent in agents if agent.nick}
    for resident in residents:
        agent = by_nick.get(resident.nick)
        if agent is None:
            continue
        budget = agent.token_budget
        if not isinstance(budget, int) or isinstance(budget, bool) or budget < 1:
            continue  # unset, or an unsanitized value — see docstring
        resident.token_budget = budget
        if resident.tokens_in is None and resident.tokens_out is None:
            continue  # state-only backend: budget known, spend unknowable
        spend = (resident.tokens_in or 0) + (resident.tokens_out or 0)
        resident.budget_used_pct = round(spend * 100.0 / budget, 1)
        resident.budget_warning = resident.budget_used_pct >= agent.token_budget_warn_pct


async def _query_presence_wire(observer: IRCObserver) -> list[dict]:
    """THE transport seam — query the server for the presence aggregation.

    Anticipated wire contract (proposed to agentirc in the t3 brief,
    agentirc#53 — subject to change when the brief is answered; ONLY this
    function should need to change):

    * client sends ``PRESENCE LIST`` after registering;
    * server replies with one ``PRESENCELIST :<json resident object>`` line
      per resident, terminated by ``PRESENCEEND``;
    * a server without the surface replies ``421 <nick> PRESENCE :Unknown
      command`` (today's agentirc), raised here as
      :class:`PresenceUnsupportedError`. A server that stays silent on the
      probe or drops the connection **before any record arrives** is
      treated the same way — reachable, but no presence support;
    * a timeout or connection close **mid-stream** — after ``PRESENCELIST``
      records were already received — raises ``ConnectionError`` instead:
      that server clearly speaks the surface but stalled, and classifying
      it unsupported would report a healthy ``supported: false`` while
      silently discarding the residents already received.

    Connection-level failures (refused, DNS, registration timeout) raise
    ``OSError`` / ``ConnectionError`` as with every other observer query.
    """
    reader, writer, _nick = await observer._connect_and_register()
    records: list[dict] = []
    try:
        writer.write(b"PRESENCE LIST\r\n")
        await writer.drain()
        buffer = ""
        while True:
            try:
                data = await asyncio.wait_for(reader.read(4096), timeout=RECV_TIMEOUT)
            except asyncio.TimeoutError:
                if records:
                    raise ConnectionError(
                        f"presence stream stalled after {len(records)} record(s)"
                    ) from None
                raise PresenceUnsupportedError("server did not answer the PRESENCE query") from None
            if not data:
                if records:
                    raise ConnectionError(f"presence stream stalled after {len(records)} record(s)")
                raise PresenceUnsupportedError(
                    "server closed the connection during the PRESENCE query"
                )
            buffer += data.decode(errors="replace")
            done, buffer = await _drain_presence_buffer(buffer, records, writer)
            if done:
                return records
    finally:
        await observer._disconnect(writer)


async def _drain_presence_buffer(
    buffer: str, records: list[dict], writer: asyncio.StreamWriter
) -> tuple[bool, str]:
    """Consume complete lines from *buffer*. Returns (done, remainder)."""
    while "\r\n" in buffer:
        line, buffer = buffer.split("\r\n", 1)
        if not line.strip():
            continue
        msg = Message.parse(line)
        if msg.command == "PING":
            token = msg.params[0] if msg.params else ""
            writer.write(f"PONG :{token}\r\n".encode())
            await writer.drain()
            continue
        if msg.command == "421" and "PRESENCE" in msg.params:
            raise PresenceUnsupportedError(
                "server replied '421 Unknown command' to the PRESENCE query"
            )
        if msg.command == "PRESENCEEND":
            return True, buffer
        if msg.command == "PRESENCELIST" and msg.params:
            try:
                parsed = json.loads(msg.params[-1])
            except ValueError:
                continue  # malformed record: skip, never crash the view
            if isinstance(parsed, dict) and parsed.get("nick"):
                records.append(parsed)
    return False, buffer


async def fetch_residents_async(
    config: ServerConfig, *, parent_nick: str | None = None
) -> list[Resident]:
    """Query *config*'s server for the resource view (async form, for t7).

    Returns the residents with culture-side budget fields already joined
    from ``config.agents``. Raises :class:`PresenceUnsupportedError` when
    the server is reachable but has no PRESENCE surface (pending
    agentirc#53), and ``OSError`` / ``ConnectionError`` when it is not
    reachable at all.
    """
    observer = IRCObserver(
        host=config.server.host,
        port=config.server.port,
        server_name=config.server.name,
        parent_nick=parent_nick,
    )
    raw = await _query_presence_wire(observer)
    residents = [_resident_from_wire(record) for record in raw]
    apply_budgets(residents, config.agents)
    return residents


def fetch_residents_for(config: ServerConfig, *, parent_nick: str | None = None) -> list[Resident]:
    """Sync wrapper over :func:`fetch_residents_async` for a loaded config.

    Runs the query on a fresh event loop — the CLI's calling convention.
    Every exception escaping this function is a *connection* problem (or
    :class:`PresenceUnsupportedError`), never a config-file one, which is
    what lets the residents CLI keep its config and connection failure
    domains separate. Callers already inside an event loop (the t7
    endpoint) use :func:`fetch_residents_async` directly.
    """
    return asyncio.run(fetch_residents_async(config, parent_nick=parent_nick))


def fetch_residents(config_path: str | Path | None = None) -> list[Resident]:
    """Compat wrapper: load the server config, then :func:`fetch_residents_for`.

    Loads the server config (default ``~/.culture/server.yaml``) and runs
    the query. Note the mixed failure surface: config-file I/O errors
    (``OSError``) and connection errors both escape from here — callers
    that need to tell them apart (the residents CLI) load the config
    themselves and call :func:`fetch_residents_for`.
    """
    path = Path(os.path.expanduser(str(config_path or _DEFAULT_CONFIG)))
    config = load_config_or_default(path)
    parent = os.environ.get("CULTURE_NICK", "").strip() or None
    return fetch_residents_for(config, parent_nick=parent)
