"""Rooms management skill — ROOMCREATE, ROOMMETA, TAGS, ROOMINVITE, ROOMKICK, ROOMARCHIVE."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Callable

from culture.aio import maybe_await
from culture.protocol import replies
from culture.protocol.message import Message
from culture.server.rooms_util import generate_room_id, parse_room_meta
from culture.server.skill import Event, EventType, Skill

if TYPE_CHECKING:
    from culture.server.client import Client


class RoomsSkill(Skill):
    name = "rooms"
    commands = {"ROOMCREATE", "ROOMMETA", "TAGS", "ROOMINVITE", "ROOMKICK", "ROOMARCHIVE"}

    async def on_command(self, client: Client, msg: Message) -> None:
        handler = {
            "ROOMCREATE": self._handle_roomcreate,
            "ROOMMETA": self._handle_roommeta,
            "TAGS": self._handle_tags,
            "ROOMINVITE": self._handle_roominvite,
            "ROOMKICK": self._handle_roomkick,
            "ROOMARCHIVE": self._handle_roomarchive,
        }.get(msg.command)
        if handler:
            await maybe_await(handler(client, msg))

    async def _handle_roomcreate(self, client: Client, msg: Message) -> None:
        if len(msg.params) < 2:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "ROOMCREATE", replies.MSG_NEEDMOREPARAMS
            )
            return

        channel_name = msg.params[0]
        if not channel_name.startswith("#"):
            await client.send(
                Message(
                    prefix=self.server.config.name,
                    command="NOTICE",
                    params=[client.nick, "Channel name must start with #"],
                )
            )
            return

        if channel_name in self.server.channels:
            await client.send_numeric(
                replies.ERR_NOSUCHCHANNEL, channel_name, "Channel already exists"
            )
            return

        meta_text = msg.params[1]
        meta = parse_room_meta(meta_text)

        channel = self.server.get_or_create_channel(channel_name)
        channel.room_id = generate_room_id()
        channel.creator = client.nick
        channel.owner = client.nick
        channel.purpose = meta.get("purpose")
        channel.instructions = meta.get("instructions")
        if "persistent" in meta:
            channel.persistent = meta["persistent"].lower() == "true"
        else:
            channel.persistent = True  # managed rooms default to persistent
        channel.created_at = time.time()
        channel.extra_meta = {}

        # Parse tags
        tags_str = meta.get("tags", "")
        channel.tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []

        # Parse agent_limit
        limit_str = meta.get("agent_limit")
        if limit_str:
            try:
                channel.agent_limit = int(limit_str)
            except ValueError:
                pass

        # Store extra metadata (anything not a known key)
        known_keys = {"purpose", "instructions", "persistent", "tags", "agent_limit"}
        for key, value in meta.items():
            if key not in known_keys:
                channel.extra_meta[key] = value

        # Invite existing agents whose tags match the new room's tags
        if channel.tags:
            await self._on_room_tags_changed(channel, set(), set(channel.tags))

        # Auto-join creator as operator
        channel.add(client)
        client.channels.add(channel)

        # Send JOIN to the creator
        join_msg = Message(prefix=client.prefix, command="JOIN", params=[channel_name])
        await client.send(join_msg)

        # Send NAMES list
        nicks = " ".join(f"{channel.get_prefix(m)}{m.nick}" for m in channel.members)
        await client.send_numeric(replies.RPL_NAMREPLY, "=", channel_name, nicks)
        await client.send_numeric(replies.RPL_ENDOFNAMES, channel_name, "End of /NAMES list")

        # Send ROOMCREATED confirmation with room ID
        await client.send(
            Message(
                prefix=self.server.config.name,
                command="ROOMCREATED",
                params=[
                    channel_name,
                    channel.room_id,
                    f"Room created: {channel.purpose or channel_name}",
                ],
            )
        )
        self._persist_room(channel)
        await self.server.emit_event(
            Event(
                type=EventType.ROOMMETA,
                channel=channel_name,
                nick=client.nick,
                data={"room_id": channel.room_id, "meta": self._serialize_meta(channel)},
            )
        )

    async def _handle_roommeta(self, client: Client, msg: Message) -> None:
        if not msg.params:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "ROOMMETA", replies.MSG_NEEDMOREPARAMS
            )
            return

        channel_name = msg.params[0]
        channel = self.server.channels.get(channel_name)

        if not channel:
            await client.send_numeric(
                replies.ERR_NOSUCHCHANNEL, channel_name, replies.MSG_NOSUCHCHANNEL
            )
            return

        if not channel.is_managed:
            await client.send(
                Message(
                    prefix=self.server.config.name,
                    command="NOTICE",
                    params=[client.nick, f"{channel_name} is not a managed room"],
                )
            )
            return

        # Build full metadata dict for easy access
        def _channel_meta(ch) -> dict:
            return {
                "room_id": ch.room_id or "",
                "creator": ch.creator or "",
                "owner": ch.owner or "",
                "purpose": ch.purpose or "",
                "instructions": ch.instructions or "",
                "tags": ",".join(ch.tags),
                "persistent": str(ch.persistent).lower(),
                "agent_limit": str(ch.agent_limit) if ch.agent_limit is not None else "",
                "archived": str(ch.archived).lower(),
                "created_at": str(ch.created_at) if ch.created_at is not None else "",
            }

        READ_ONLY_KEYS = {"room_id", "creator", "archived", "created_at"}

        if len(msg.params) == 1:
            await self._query_all_roommeta(client, channel, channel_name, _channel_meta)
        elif len(msg.params) == 2:
            await self._query_single_roommeta(
                client, channel, channel_name, msg.params[1], _channel_meta
            )
        else:
            await self._update_roommeta(
                client, channel, channel_name, msg.params[1], msg.params[2], READ_ONLY_KEYS
            )

    async def _query_all_roommeta(
        self, client: Client, channel, channel_name: str, _channel_meta: Callable
    ) -> None:
        """Send all metadata key-value pairs for a channel."""
        meta = _channel_meta(channel)
        for key, value in meta.items():
            await client.send(
                Message(
                    prefix=self.server.config.name,
                    command="ROOMMETA",
                    params=[channel_name, key, value],
                )
            )
        await client.send(
            Message(
                prefix=self.server.config.name,
                command="ROOMETAEND",
                params=[channel_name, "End of ROOMMETA"],
            )
        )

    async def _query_single_roommeta(
        self, client: Client, channel, channel_name: str, key: str, _channel_meta: Callable
    ) -> None:
        """Send a single metadata value for a channel."""
        meta = _channel_meta(channel)
        value = meta.get(key, channel.extra_meta.get(key, ""))
        await client.send(
            Message(
                prefix=self.server.config.name,
                command="ROOMMETA",
                params=[channel_name, key, value],
            )
        )
        await client.send(
            Message(
                prefix=self.server.config.name,
                command="ROOMETAEND",
                params=[channel_name, "End of ROOMMETA"],
            )
        )

    async def _update_roommeta(
        self, client: Client, channel, channel_name: str, key: str, value: str, read_only_keys: set
    ) -> None:
        """Validate permissions and apply a metadata update."""
        is_owner = channel.owner == client.nick
        is_operator = channel.is_operator(client)
        if not is_owner and not is_operator:
            await client.send_numeric(
                replies.ERR_CHANOPRIVSNEEDED,
                channel_name,
                "You do not have permission to update room metadata",
            )
            return

        if key in read_only_keys:
            await client.send(
                Message(
                    prefix=self.server.config.name,
                    command="NOTICE",
                    params=[client.nick, f"{key} is read-only"],
                )
            )
            return

        # Apply the update
        if key == "purpose":
            channel.purpose = value
        elif key == "instructions":
            channel.instructions = value
        elif key == "tags":
            old_tags = set(channel.tags)
            channel.tags = [t.strip() for t in value.split(",") if t.strip()]
            new_tags = set(channel.tags)
            await self._on_room_tags_changed(channel, old_tags, new_tags)
        elif key == "owner":
            channel.owner = value
        elif key == "persistent":
            channel.persistent = value.lower() == "true"
        elif key == "agent_limit":
            try:
                channel.agent_limit = int(value)
            except ValueError:
                await client.send(
                    Message(
                        prefix=self.server.config.name,
                        command="NOTICE",
                        params=[client.nick, f"Invalid value for agent_limit: {value}"],
                    )
                )
                return
        else:
            channel.extra_meta[key] = value

        await client.send(
            Message(
                prefix=self.server.config.name,
                command="ROOMETASET",
                params=[channel_name, key, value, "Updated"],
            )
        )
        self._persist_room(channel)
        await self.server.emit_event(
            Event(
                type=EventType.ROOMMETA,
                channel=channel.name,
                nick=client.nick,
                data={"room_id": channel.room_id, "meta": self._serialize_meta(channel)},
            )
        )

    async def _handle_tags_added(self, channel, added_tags: set) -> None:
        """Invite local agents whose tags match newly added room tags."""
        from culture.server.remote_client import RemoteClient

        for client in list(self.server.clients.values()):
            if isinstance(client, RemoteClient):
                continue
            client_tags = set(client.tags)
            if client_tags & added_tags and client not in channel.members:
                await self._send_system_invite(client, channel)

    async def _handle_tags_removed(self, channel, removed_tags: set) -> None:
        """Notify local members whose tags match removed room tags."""
        from culture.server.remote_client import RemoteClient

        for member in list(channel.members):
            if isinstance(member, RemoteClient):
                continue
            member_tags = set(member.tags)
            if member_tags & removed_tags:
                await member.send(
                    Message(
                        prefix=self.server.config.name,
                        command="ROOMTAGNOTICE",
                        params=[
                            member.nick,
                            channel.name,
                            f"Tags removed from room: {','.join(removed_tags & member_tags)}",
                        ],
                    )
                )

    async def _on_room_tags_changed(self, channel, old_tags: set, new_tags: set) -> None:
        """Fire tag-based notifications when a room's tags change.

        - Tags ADDED: find agents with matching tags not in room → ROOMINVITE
        - Tags REMOVED: find in-room local agents with those tags → ROOMTAGNOTICE
        """
        added = new_tags - old_tags
        removed = old_tags - new_tags

        if added:
            await self._handle_tags_added(channel, added)

        if removed:
            await self._handle_tags_removed(channel, removed)

    async def _handle_tags(self, client: Client, msg: Message) -> None:
        if not msg.params:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "TAGS", replies.MSG_NEEDMOREPARAMS
            )
            return

        nick = msg.params[0]

        if len(msg.params) == 1:
            # Query tags for nick
            target = self.server.clients.get(nick)
            if not target:
                await client.send_numeric(replies.ERR_NOSUCHNICK, nick, replies.MSG_NOSUCHNICK)
                return

            tags_str = ",".join(target.tags)
            await client.send(
                Message(
                    prefix=self.server.config.name,
                    command="TAGS",
                    params=[nick, tags_str],
                )
            )
            await client.send(
                Message(
                    prefix=self.server.config.name,
                    command="TAGSEND",
                    params=[nick, "End of TAGS"],
                )
            )

        else:
            # Set tags — self only (unless server operator)
            tags_value = msg.params[1]

            if nick != client.nick:
                await client.send(
                    Message(
                        prefix=self.server.config.name,
                        command="NOTICE",
                        params=[
                            client.nick,
                            "You do not have permission to set tags for other users",
                        ],
                    )
                )
                return

            target = self.server.clients.get(nick)
            if not target:
                await client.send_numeric(replies.ERR_NOSUCHNICK, nick, replies.MSG_NOSUCHNICK)
                return

            old_tags = set(target.tags)
            target.tags = [t.strip() for t in tags_value.split(",") if t.strip()]
            new_tags = set(target.tags)
            await self._on_agent_tags_changed(target, old_tags, new_tags)

            await client.send(
                Message(
                    prefix=self.server.config.name,
                    command="TAGSSET",
                    params=[nick, tags_value, "Tags updated"],
                )
            )
            await self.server.emit_event(
                Event(
                    type=EventType.TAGS,
                    channel=None,
                    nick=target.nick,
                    data={"tags": target.tags},
                )
            )

    async def _on_agent_tags_changed(self, client: Client, old_tags: set, new_tags: set) -> None:
        """Fire tag-based notifications when an agent's tags change.

        - Tags ADDED: find managed rooms with matching tags where agent is not a member → ROOMINVITE
        - Tags REMOVED: find managed rooms agent is in that have those tags → ROOMTAGNOTICE
        """
        added = new_tags - old_tags
        removed = old_tags - new_tags

        for channel in list(self.server.channels.values()):
            if not channel.is_managed:
                continue
            channel_tags = set(channel.tags)

            if added and (channel_tags & added) and client not in channel.members:
                await self._send_system_invite(client, channel)

            if removed and (channel_tags & removed) and client in channel.members:
                await client.send(
                    Message(
                        prefix=self.server.config.name,
                        command="ROOMTAGNOTICE",
                        params=[
                            client.nick,
                            channel.name,
                            "You no longer have matching tags for this"
                            f" room: {','.join(channel_tags & removed)}",
                        ],
                    )
                )

    async def _send_system_invite(self, client: Client, channel) -> None:
        """Send a system-generated ROOMINVITE to a client for a room."""
        parts = [channel.name]
        if channel.purpose:
            parts.append(f"purpose={channel.purpose}")
        if channel.tags:
            parts.append(f"tags={','.join(channel.tags)}")
        if channel.instructions:
            parts.append(f"instructions={channel.instructions}")
        await client.send(
            Message(
                prefix=self.server.config.name,
                command="ROOMINVITE",
                params=[channel.name, client.nick, ";".join(parts[1:]) or channel.name],
            )
        )

    async def _handle_roominvite(self, client: Client, msg: Message) -> None:
        from culture.server.remote_client import RemoteClient

        if len(msg.params) < 2:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "ROOMINVITE", replies.MSG_NEEDMOREPARAMS
            )
            return

        channel_name = msg.params[0]
        target_nick = msg.params[1]

        channel = self.server.channels.get(channel_name)
        if not channel:
            await client.send_numeric(
                replies.ERR_NOSUCHCHANNEL, channel_name, replies.MSG_NOSUCHCHANNEL
            )
            return

        target = self.server.clients.get(target_nick)
        if not target:
            await client.send_numeric(replies.ERR_NOSUCHNICK, target_nick, replies.MSG_NOSUCHNICK)
            return

        # Don't send to RemoteClients
        if isinstance(target, RemoteClient):
            return

        # Build context payload
        parts = []
        if channel.purpose:
            parts.append(f"purpose={channel.purpose}")
        if channel.tags:
            parts.append(f"tags={','.join(channel.tags)}")
        if channel.instructions:
            parts.append(f"instructions={channel.instructions}")
        context = ";".join(parts) if parts else channel_name

        await target.send(
            Message(
                prefix=client.prefix,
                command="ROOMINVITE",
                params=[
                    channel_name,
                    target_nick,
                    (
                        f"requestor={client.nick};{context}"
                        if context != channel_name
                        else f"requestor={client.nick}"
                    ),
                ],
            )
        )

        # Confirmation notice to inviter
        await client.send(
            Message(
                prefix=self.server.config.name,
                command="NOTICE",
                params=[client.nick, f"Invited {target_nick} to {channel_name}"],
            )
        )

    async def _handle_roomkick(self, client: Client, msg: Message) -> None:
        if len(msg.params) < 2:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "ROOMKICK", replies.MSG_NEEDMOREPARAMS
            )
            return

        channel_name = msg.params[0]
        target_nick = msg.params[1]

        channel = self.server.channels.get(channel_name)
        if not channel:
            await client.send_numeric(
                replies.ERR_NOSUCHCHANNEL, channel_name, replies.MSG_NOSUCHCHANNEL
            )
            return

        if not channel.is_managed:
            await client.send(
                Message(
                    prefix=self.server.config.name,
                    command="NOTICE",
                    params=[client.nick, f"{channel_name} is not a managed room"],
                )
            )
            return

        # Owner-only permission check
        if channel.owner != client.nick:
            await client.send(
                Message(
                    prefix=self.server.config.name,
                    command="NOTICE",
                    params=[
                        client.nick,
                        f"You do not have permission to kick members from {channel_name}",
                    ],
                )
            )
            return

        target = self.server.clients.get(target_nick)
        if not target or target not in channel.members:
            await client.send_numeric(
                replies.ERR_USERNOTINCHANNEL,
                target_nick,
                channel_name,
                "They aren't on that channel",
            )
            return

        # Force-part: notify all current members then remove target
        part_msg = Message(
            prefix=target.prefix,
            command="PART",
            params=[channel_name, f"Kicked by {client.nick}"],
        )
        for member in list(channel.members):
            await member.send(part_msg)

        channel.remove(target)
        target.channels.discard(channel)

        if not channel.members and not channel.persistent:
            del self.server.channels[channel_name]

    def _next_archive_name(self, base_name: str) -> str:
        """Return the next available archive name for a channel.

        First try ``{base_name}-archived``.  If taken, try
        ``{base_name}-archived#2``, ``#3``, etc.
        """
        candidate = f"{base_name}-archived"
        if candidate not in self.server.channels:
            return candidate
        n = 2
        while True:
            candidate = f"{base_name}-archived#{n}"
            if candidate not in self.server.channels:
                return candidate
            n += 1

    async def _handle_roomarchive(self, client: Client, msg: Message) -> None:
        if not msg.params:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "ROOMARCHIVE", replies.MSG_NEEDMOREPARAMS
            )
            return

        channel_name = msg.params[0]
        channel = self.server.channels.get(channel_name)

        if not channel:
            await client.send_numeric(
                replies.ERR_NOSUCHCHANNEL, channel_name, replies.MSG_NOSUCHCHANNEL
            )
            return

        if not channel.is_managed:
            await client.send(
                Message(
                    prefix=self.server.config.name,
                    command="NOTICE",
                    params=[client.nick, f"{channel_name} is not a managed room"],
                )
            )
            return

        # Owner or channel operator only
        is_owner = channel.owner == client.nick
        is_operator = channel.is_operator(client)
        if not is_owner and not is_operator:
            await client.send(
                Message(
                    prefix=self.server.config.name,
                    command="NOTICE",
                    params=[client.nick, f"You do not have permission to archive {channel_name}"],
                )
            )
            return

        # Determine archive name
        archive_name = self._next_archive_name(channel_name)

        # Notify all members before parting them
        notice_msg = Message(
            prefix=self.server.config.name,
            command="NOTICE",
            params=[
                channel_name,
                f"{channel_name} is being archived as {archive_name}",
            ],
        )
        for member in list(channel.members):
            await member.send(notice_msg)

        # Part all members
        part_msg = Message(
            prefix=self.server.config.name,
            command="PART",
            params=[channel_name, f"Room archived as {archive_name}"],
        )
        for member in list(channel.members):
            await member.send(part_msg)
            member.channels.discard(channel)

        channel.members.clear()
        channel.operators.clear()
        channel.voiced.clear()

        # Rename: remove old entry, update channel object, add under new name
        del self.server.channels[channel_name]
        channel.name = archive_name
        channel.archived = True
        self.server.channels[archive_name] = channel

        # Confirm to requester
        await client.send(
            Message(
                prefix=self.server.config.name,
                command="ROOMARCHIVED",
                params=[channel_name, archive_name, f"Room archived as {archive_name}"],
            )
        )
        self._persist_room(channel)
        await self.server.emit_event(
            Event(
                type=EventType.ROOMARCHIVE,
                channel=channel_name,
                nick=client.nick,
                data={"room_id": channel.room_id, "archive_name": archive_name},
            )
        )

    def _serialize_meta(self, channel) -> str:
        import json

        return json.dumps(
            {
                "room_id": channel.room_id,
                "name": channel.name,
                "creator": channel.creator,
                "owner": channel.owner,
                "purpose": channel.purpose,
                "instructions": channel.instructions,
                "tags": channel.tags,
                "persistent": channel.persistent,
                "agent_limit": channel.agent_limit,
                "extra_meta": channel.extra_meta,
                "created_at": channel.created_at,
            }
        )

    def _persist_room(self, channel) -> None:
        """Save room to disk if data_dir is configured."""
        if not self.server.config.data_dir:
            return
        from culture.server.room_store import RoomStore

        store = RoomStore(self.server.config.data_dir)
        store.save(channel)

    async def on_event(self, event: Event) -> None:
        """React to PART/QUIT events to notify owners of empty persistent rooms."""
        if event.type == EventType.PART:
            await self._check_empty_room_on_part(event)
        elif event.type == EventType.QUIT:
            await self._check_empty_rooms_on_quit(event)

    async def _check_empty_room_on_part(self, event: Event) -> None:
        """If a persistent managed room will be empty after this PART, notify the owner."""
        channel_name = event.channel
        if not channel_name:
            return
        channel = self.server.channels.get(channel_name)
        if not channel or not channel.is_managed or not channel.persistent:
            return

        # The parting member is still in the channel when the event fires.
        # If they are the only member, the room becomes empty.
        if len(channel.members) != 1:
            return

        await self._notify_owner_room_empty(channel)

    async def _check_empty_rooms_on_quit(self, event: Event) -> None:
        """For each persistent managed room the quitting user was in, check if empty."""
        channel_names: list[str] = event.data.get("channels", [])
        quitting_nick = event.nick
        for channel_name in channel_names:
            channel = self.server.channels.get(channel_name)
            if not channel or not channel.is_managed or not channel.persistent:
                continue
            # All members except the quitting client
            remaining = [m for m in channel.members if m.nick != quitting_nick]
            if remaining:
                continue
            await self._notify_owner_room_empty(channel)

    async def _notify_owner_room_empty(self, channel) -> None:
        """Send a NOTICE to the channel owner suggesting archival."""
        if not channel.owner:
            return
        owner_client = self.server.clients.get(channel.owner)
        if not owner_client:
            return
        await owner_client.send(
            Message(
                prefix=self.server.config.name,
                command="NOTICE",
                params=[
                    channel.owner,
                    f"{channel.name} is now empty."
                    f" Consider archiving it with ROOMARCHIVE {channel.name}",
                ],
            )
        )
