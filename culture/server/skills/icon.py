"""ICON skill — lets clients set a display icon/emoji."""

from __future__ import annotations

from typing import TYPE_CHECKING

from culture.protocol.message import Message
from culture.server.skill import Skill

if TYPE_CHECKING:
    from culture.server.client import Client


class IconSkill(Skill):
    name = "icon"
    commands = {"ICON"}

    async def on_command(self, client: Client, msg: Message) -> None:
        if msg.command != "ICON":
            return

        if not msg.params:
            # Query current icon
            icon = client.icon or "(none)"
            await client.send(
                Message(
                    prefix=self.server.config.name,
                    command="ICON",
                    params=[client.nick, icon],
                )
            )
            return

        icon = msg.params[0].strip()
        if len(icon) > 4:
            await client.send(
                Message(
                    prefix=self.server.config.name,
                    command="NOTICE",
                    params=[client.nick, "ICON too long (max 4 characters)"],
                )
            )
            return

        client.icon = icon
        await client.send(
            Message(
                prefix=self.server.config.name,
                command="ICON",
                params=[client.nick, icon],
            )
        )
