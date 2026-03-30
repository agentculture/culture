"""Rooms management skill — ROOMCREATE, ROOMMETA, TAGS, ROOMINVITE, ROOMKICK, ROOMARCHIVE."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from agentirc.protocol.message import Message
from agentirc.protocol import replies
from agentirc.server.rooms_util import generate_room_id, parse_room_meta
from agentirc.server.skill import Event, EventType, Skill

if TYPE_CHECKING:
    from agentirc.server.client import Client


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
            await handler(client, msg)

    async def _handle_roomcreate(self, client: Client, msg: Message) -> None:
        if len(msg.params) < 2:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "ROOMCREATE", "Not enough parameters"
            )
            return

        channel_name = msg.params[0]
        if not channel_name.startswith("#"):
            await client.send(Message(
                prefix=self.server.config.name,
                command="NOTICE",
                params=[client.nick, "Channel name must start with #"],
            ))
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
        channel.persistent = meta.get("persistent", "").lower() == "true"
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
        await client.send(Message(
            prefix=self.server.config.name,
            command="ROOMCREATED",
            params=[channel_name, channel.room_id, f"Room created: {channel.purpose or channel_name}"],
        ))

    async def _handle_roommeta(self, client: Client, msg: Message) -> None:
        if not msg.params:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "ROOMMETA", "Not enough parameters"
            )
            return

        channel_name = msg.params[0]
        channel = self.server.channels.get(channel_name)

        if not channel:
            await client.send_numeric(
                replies.ERR_NOSUCHCHANNEL, channel_name, "No such channel"
            )
            return

        if not channel.is_managed:
            await client.send(Message(
                prefix=self.server.config.name,
                command="NOTICE",
                params=[client.nick, f"{channel_name} is not a managed room"],
            ))
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
            # Query all metadata
            meta = _channel_meta(channel)
            for key, value in meta.items():
                await client.send(Message(
                    prefix=self.server.config.name,
                    command="ROOMMETA",
                    params=[channel_name, key, value],
                ))
            await client.send(Message(
                prefix=self.server.config.name,
                command="ROOMETAEND",
                params=[channel_name, "End of ROOMMETA"],
            ))

        elif len(msg.params) == 2:
            # Query single key
            key = msg.params[1]
            meta = _channel_meta(channel)
            value = meta.get(key, channel.extra_meta.get(key, ""))
            await client.send(Message(
                prefix=self.server.config.name,
                command="ROOMMETA",
                params=[channel_name, key, value],
            ))
            await client.send(Message(
                prefix=self.server.config.name,
                command="ROOMETAEND",
                params=[channel_name, "End of ROOMMETA"],
            ))

        else:
            # Update a field — owner or operator only
            key = msg.params[1]
            value = msg.params[2]

            is_owner = channel.owner == client.nick
            is_operator = channel.is_operator(client)
            if not is_owner and not is_operator:
                await client.send_numeric(
                    replies.ERR_CHANOPRIVSNEEDED,
                    channel_name,
                    "You do not have permission to update room metadata",
                )
                return

            if key in READ_ONLY_KEYS:
                await client.send(Message(
                    prefix=self.server.config.name,
                    command="NOTICE",
                    params=[client.nick, f"{key} is read-only"],
                ))
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
                    await client.send(Message(
                        prefix=self.server.config.name,
                        command="NOTICE",
                        params=[client.nick, f"Invalid value for agent_limit: {value}"],
                    ))
                    return
            else:
                channel.extra_meta[key] = value

            await client.send(Message(
                prefix=self.server.config.name,
                command="ROOMETASET",
                params=[channel_name, key, value, "Updated"],
            ))

    async def _on_room_tags_changed(self, channel, old_tags: set, new_tags: set) -> None:
        """Fire tag-based notifications when a room's tags change.

        - Tags ADDED: find agents with matching tags not in room → ROOMINVITE
        - Tags REMOVED: find in-room local agents with those tags → ROOMTAGNOTICE
        """
        from agentirc.server.remote_client import RemoteClient

        added = new_tags - old_tags
        removed = old_tags - new_tags

        if added:
            for client in list(self.server.clients.values()):
                if isinstance(client, RemoteClient):
                    continue
                client_tags = set(client.tags)
                if client_tags & added and client not in channel.members:
                    await self._send_system_invite(client, channel)

        if removed:
            for member in list(channel.members):
                if isinstance(member, RemoteClient):
                    continue
                member_tags = set(member.tags)
                if member_tags & removed:
                    await member.send(Message(
                        prefix=self.server.config.name,
                        command="ROOMTAGNOTICE",
                        params=[
                            member.nick,
                            channel.name,
                            f"Tags removed from room: {','.join(removed & member_tags)}",
                        ],
                    ))

    async def _handle_tags(self, client: Client, msg: Message) -> None:
        if not msg.params:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "TAGS", "Not enough parameters"
            )
            return

        nick = msg.params[0]

        if len(msg.params) == 1:
            # Query tags for nick
            target = self.server.clients.get(nick)
            if not target:
                await client.send_numeric(
                    replies.ERR_NOSUCHNICK, nick, "No such nick"
                )
                return

            tags_str = ",".join(target.tags)
            await client.send(Message(
                prefix=self.server.config.name,
                command="TAGS",
                params=[nick, tags_str],
            ))
            await client.send(Message(
                prefix=self.server.config.name,
                command="TAGSEND",
                params=[nick, "End of TAGS"],
            ))

        else:
            # Set tags — self only (unless server operator)
            tags_value = msg.params[1]

            if nick != client.nick:
                await client.send(Message(
                    prefix=self.server.config.name,
                    command="NOTICE",
                    params=[client.nick, "You do not have permission to set tags for other users"],
                ))
                return

            target = self.server.clients.get(nick)
            if not target:
                await client.send_numeric(
                    replies.ERR_NOSUCHNICK, nick, "No such nick"
                )
                return

            old_tags = set(target.tags)
            target.tags = [t.strip() for t in tags_value.split(",") if t.strip()]
            new_tags = set(target.tags)
            await self._on_agent_tags_changed(target, old_tags, new_tags)

            await client.send(Message(
                prefix=self.server.config.name,
                command="TAGSSET",
                params=[nick, tags_value, "Tags updated"],
            ))

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
                await client.send(Message(
                    prefix=self.server.config.name,
                    command="ROOMTAGNOTICE",
                    params=[
                        client.nick,
                        channel.name,
                        f"You no longer have matching tags for this room: {','.join(channel_tags & removed)}",
                    ],
                ))

    async def _send_system_invite(self, client: Client, channel) -> None:
        """Send a system-generated ROOMINVITE to a client for a room."""
        parts = [channel.name]
        if channel.purpose:
            parts.append(f"purpose={channel.purpose}")
        if channel.tags:
            parts.append(f"tags={','.join(channel.tags)}")
        if channel.instructions:
            parts.append(f"instructions={channel.instructions}")
        await client.send(Message(
            prefix=self.server.config.name,
            command="ROOMINVITE",
            params=[client.nick, channel.name, ";".join(parts[1:]) or channel.name],
        ))

    async def _handle_roominvite(self, client: Client, msg: Message) -> None:
        pass  # Task 7

    async def _handle_roomkick(self, client: Client, msg: Message) -> None:
        pass  # Task 8

    async def _handle_roomarchive(self, client: Client, msg: Message) -> None:
        pass  # Task 9
