"""Console command parser."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto


class CommandType(Enum):
    CHAT = auto()
    CHANNELS = auto()
    JOIN = auto()
    PART = auto()
    WHO = auto()
    SEND = auto()
    READ = auto()
    OVERVIEW = auto()
    STATUS = auto()
    AGENTS = auto()
    START = auto()
    STOP = auto()
    RESTART = auto()
    ICON = auto()
    TOPIC = auto()
    KICK = auto()
    INVITE = auto()
    SERVER = auto()
    QUIT = auto()
    UNKNOWN = auto()


@dataclass
class ParsedCommand:
    type: CommandType
    args: list[str] = field(default_factory=list)
    text: str = ""


# Commands where trailing words after args form free text
_TEXT_COMMANDS = {
    "send": (CommandType.SEND, 1),  # /send <target> <text...>
    "topic": (CommandType.TOPIC, 1),  # /topic <channel> <text...>
}

# Simple commands: name -> type
_COMMANDS: dict[str, CommandType] = {
    "channels": CommandType.CHANNELS,
    "join": CommandType.JOIN,
    "part": CommandType.PART,
    "who": CommandType.WHO,
    "read": CommandType.READ,
    "overview": CommandType.OVERVIEW,
    "status": CommandType.STATUS,
    "agents": CommandType.AGENTS,
    "start": CommandType.START,
    "stop": CommandType.STOP,
    "restart": CommandType.RESTART,
    "icon": CommandType.ICON,
    "kick": CommandType.KICK,
    "invite": CommandType.INVITE,
    "server": CommandType.SERVER,
    "quit": CommandType.QUIT,
}


def parse_command(input_text: str) -> ParsedCommand:
    """Parse user input into a command or chat message."""
    stripped = input_text.strip()
    if not stripped:
        return ParsedCommand(type=CommandType.CHAT, text="")

    if not stripped.startswith("/"):
        return ParsedCommand(type=CommandType.CHAT, text=stripped)

    parts = stripped[1:].split()
    if not parts:
        return ParsedCommand(type=CommandType.CHAT, text=stripped)

    cmd_name = parts[0].lower()
    rest = parts[1:]

    # Text commands: split at boundary, rest is free text
    if cmd_name in _TEXT_COMMANDS:
        cmd_type, arg_count = _TEXT_COMMANDS[cmd_name]
        args = rest[:arg_count]
        text = " ".join(rest[arg_count:])
        return ParsedCommand(type=cmd_type, args=args, text=text)

    # Regular commands
    if cmd_name in _COMMANDS:
        return ParsedCommand(type=_COMMANDS[cmd_name], args=rest)

    return ParsedCommand(type=CommandType.UNKNOWN, text=stripped)
