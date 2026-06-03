"""IRCv3 ``draft/chathistory`` skill — per-nick DM spool drain.

Phase 3 of the 2026-06-03 mesh-rearchitecture. The bridge process,
on reconnect, issues ``CHATHISTORY <own-nick>`` and replays each
spooled DM into CC via IPC.

Syntax (this server's subset of the draft):

    CHATHISTORY <target-nick> [limit]
    CHATHISTORY DELETE <msg_id>

- ``CHATHISTORY <target>`` returns the recipient's spooled DMs as a
  batch of ``PRIVMSG`` lines prefixed with IRCv3 tags ``msgid=...``
  and ``server-time=...``. The recipient is identified by *target*;
  the requesting client's nick MUST equal *target* (IDOR guard —
  Phase 3, review iter-4 B-3). Cross-nick reads return
  ``ERR_NOPRIVILEGES (481)``.
- ``CHATHISTORY DELETE <msg_id>`` is the two-phase-drain ack
  (Phase 3.5): the bridge, after CC acks the inbound_dm, instructs
  the IRCd to mark that msg_id delivered.
- Channel CHATHISTORY (``CHATHISTORY <#channel>``) is OUT OF SCOPE
  for this skill — channel history is owned by ``HistorySkill``'s
  ``HISTORY RECENT/SEARCH`` verbs. A request for a channel target
  falls through with ``ERR_NOSUCHCHANNEL``-style messaging.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from culture.agentirc.skill import Skill
from culture.protocol import replies
from culture.protocol.message import Message

if TYPE_CHECKING:
    from culture.agentirc.client import Client

logger = logging.getLogger(__name__)

# Hard cap on the per-request batch size. Matches the
# ``CHATHISTORY=100`` ISUPPORT advertised in ``client.py:_send_welcome``.
CHATHISTORY_LIMIT_MAX = 100


def _server_time_iso(ts: float) -> str:
    """Format *ts* (epoch seconds) as ISO8601 UTC with millisecond
    precision — the IRCv3 ``server-time`` tag spec."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    # %f is microseconds; trim to ms then append the ``Z`` per IRCv3.
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


class ChatHistorySkill(Skill):
    """Implements the per-nick spool drain side of ``draft/chathistory``."""

    name = "chathistory"
    commands = {"CHATHISTORY"}

    async def on_command(self, client: Client, msg: Message) -> None:
        if not msg.params:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "CHATHISTORY", replies.MSG_NEEDMOREPARAMS
            )
            return

        head = msg.params[0]
        # Sub-verb dispatch: ``DELETE`` is the mark-delivered ack path.
        if head.upper() == "DELETE":
            await self._handle_delete(client, msg)
            return
        await self._handle_drain(client, msg)

    async def _handle_drain(self, client: Client, msg: Message) -> None:
        target = msg.params[0]
        try:
            limit = int(msg.params[1]) if len(msg.params) > 1 else CHATHISTORY_LIMIT_MAX
        except ValueError:
            limit = CHATHISTORY_LIMIT_MAX
        limit = max(1, min(limit, CHATHISTORY_LIMIT_MAX))

        # Channel chathistory not implemented here — defer to
        # HistorySkill's verbs. Replying with ERR_NOSUCHCHANNEL keeps
        # the wire predictable for clients that probe.
        if target.startswith("#"):
            await client.send_numeric(
                replies.ERR_NOSUCHCHANNEL,
                target,
                "CHATHISTORY for channels uses HISTORY RECENT/SEARCH",
            )
            return

        # IDOR guard (CRITICAL, Phase 3 review iter-4 B-3): a peer may
        # ONLY drain its own spool. Any cross-nick request — including
        # a peer probing for the existence of another nick — returns
        # ERR_NOPRIVILEGES so the spool cannot be enumerated.
        if client.nick != target:
            await client.send_numeric(
                replies.ERR_NOPRIVILEGES,
                "CHATHISTORY",
                replies.MSG_NOPRIVILEGES,
            )
            return

        spool = getattr(self.server, "dm_spool", None)
        if spool is None:
            # Spool open failed at boot; respond with an empty batch
            # rather than an error so the bridge proceeds normally.
            await self._send_end_of_batch(client, target)
            return

        try:
            # Qodo PR #50 #6: drain off the event loop. A large spool
            # against a slow disk would otherwise stall every connected
            # client for the duration of the SELECT.
            entries = await spool.aquery_for_nick(target, limit=limit)
        except Exception:  # noqa: BLE001
            logger.warning("CHATHISTORY drain failed for %s", target, exc_info=True)
            await self._send_end_of_batch(client, target)
            return

        # Open a batch so the client can detect the boundaries cleanly
        # (IRCv3 ``batch`` cap). A simple ``BATCH`` envelope keeps the
        # wire shape close to the draft without dragging in the full
        # tag spec — sufficient for the bridge consumer.
        batch_id = f"chathist-{target}"
        await client.send_raw(
            f":{self.server.config.name} BATCH +{batch_id} draft/chathistory {target}"
        )
        for entry in entries:
            await self._emit_history_line(client, entry, batch_id)
        await client.send_raw(f":{self.server.config.name} BATCH -{batch_id}")
        await self._send_end_of_batch(client, target)

    async def _emit_history_line(self, client: Client, entry: dict, batch_id: str) -> None:
        """Emit a single spooled DM as a PRIVMSG with msgid + server-time tags.

        Builds a structured ``Message`` so ``Client.send`` handles tag
        serialization + traceparent injection consistently (rather than
        emitting a raw ``@tags ...`` line where the trace-injection
        wrapper would mis-prefix the line).
        """
        msg_id = entry["msg_id"]
        sender = entry["sender"]
        recipient = entry["recipient"]
        payload = entry["payload"]
        server_time = _server_time_iso(entry["ts_server"])

        tags: dict[str, str] = {
            "msgid": msg_id,
            "server-time": server_time,
            "batch": batch_id,
        }
        # Forward any persisted IRCv3 tags from the original spool row
        # (e.g. trace context). Keys collide → spool entry wins for
        # provenance; we already populate the canonical set above so a
        # collision is benign.
        persisted = entry.get("tags") or ""
        for tok in persisted.split():
            if "=" in tok:
                k, v = tok.split("=", 1)
                tags.setdefault(k, v)
            else:
                tags.setdefault(tok, "")

        msg = Message(
            tags=tags,
            prefix=sender,
            command="PRIVMSG",
            params=[recipient, payload],
        )
        await client.send(msg)

    async def _send_end_of_batch(self, client: Client, target: str) -> None:
        """Send a sentinel so the bridge knows the replay is complete."""
        await client.send_raw(f":{self.server.config.name} CHATHISTORY END {target}")

    async def _handle_delete(self, client: Client, msg: Message) -> None:
        """``CHATHISTORY DELETE <msg_id>`` — mark a spooled DM delivered.

        The bridge issues this after CC acks the inbound_dm push (the
        two-phase drain). The msg_id is the same value the spool
        emitted in the ``msgid=`` tag. To prevent a malicious peer from
        marking another boss's spool entries delivered, the DB row's
        recipient must equal the requesting nick.
        """
        if len(msg.params) < 2:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "CHATHISTORY", replies.MSG_NEEDMOREPARAMS
            )
            return
        msg_id = msg.params[1]
        spool = getattr(self.server, "dm_spool", None)
        if spool is None:
            # Nothing to mark; respond OK silently — replay safety is on
            # the bridge side anyway (idempotent re-acks).
            await self._send_delete_ack(client, msg_id, ok=True)
            return

        # Targeted O(1) lookup so we can enforce recipient-equals-caller
        # without leaking msg_id existence. The previous page-scan was
        # capped at CHATHISTORY_LIMIT_MAX (100); valid msg_ids beyond
        # position 100 in ts_server order returned a spurious
        # ERR_NOPRIVILEGES and the spool leaked indefinitely once it
        # grew past the cap. ``get_by_msg_id`` is IDOR-safe by
        # construction: the WHERE clause pins recipient to the
        # requesting nick.
        try:
            # Qodo PR #50 #6: same off-loop policy as the drain path.
            owned = await spool.aget_by_msg_id(client.nick or "", msg_id)
        except Exception:  # noqa: BLE001
            await self._send_delete_ack(client, msg_id, ok=False)
            return
        if not owned:
            # Either unknown msg_id OR the msg_id belongs to someone
            # else's spool. Same response in both cases (IDOR-safe).
            await client.send_numeric(
                replies.ERR_NOPRIVILEGES,
                "CHATHISTORY",
                replies.MSG_NOPRIVILEGES,
            )
            return

        try:
            await spool.amark_delivered(msg_id)
        except Exception:  # noqa: BLE001
            await self._send_delete_ack(client, msg_id, ok=False)
            return
        await self._send_delete_ack(client, msg_id, ok=True)

    async def _send_delete_ack(self, client: Client, msg_id: str, ok: bool) -> None:
        verdict = "OK" if ok else "FAIL"
        await client.send_raw(f":{self.server.config.name} CHATHISTORY DELETE {msg_id} {verdict}")
