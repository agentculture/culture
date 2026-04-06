"""Sidebar widget — channels list and entity roster grouped by type."""

from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label, Static

# ---------------------------------------------------------------------------
# Data classes used by the sidebar
# ---------------------------------------------------------------------------


@dataclass
class ChannelItem:
    """A channel entry in the sidebar."""

    name: str
    member_count: int = 0
    unread: int = 0


@dataclass
class EntityItem:
    """An entity (agent / human / bot / admin) entry in the sidebar."""

    nick: str
    entity_type: str = "agent"  # "agent" | "admin" | "human" | "bot"
    online: bool = True
    icon: str = ""


# ---------------------------------------------------------------------------
# Internal row widgets
# ---------------------------------------------------------------------------

# Type icons shown when an entity has no personal icon
_TYPE_ICON: dict[str, str] = {
    "agent": "🤖",
    "admin": "👑",
    "human": "👤",
    "bot": "⚙",
}

# Group order for entity types
_GROUP_ORDER = ["agent", "admin", "human", "bot"]

# Display labels for each group
_GROUP_LABEL: dict[str, str] = {
    "agent": "AGENTS",
    "admin": "ADMIN",
    "human": "HUMANS",
    "bot": "BOTS",
}


class _ChannelRow(Static):
    """A single channel row in the sidebar."""

    DEFAULT_CSS = """
    _ChannelRow {
        width: 1fr;
        height: 1;
        padding: 0 1;
    }
    _ChannelRow:hover {
        background: $accent 30%;
    }
    _ChannelRow.active {
        background: $accent 60%;
        color: $text;
    }
    """

    def __init__(self, channel: ChannelItem, active: bool = False) -> None:
        name = channel.name
        count_str = f" ({channel.member_count})" if channel.member_count else ""
        unread_str = f" [bold yellow]*{channel.unread}[/]" if channel.unread else ""
        markup = f"{name}{count_str}{unread_str}"
        super().__init__(markup, markup=True)
        self._channel_name = channel.name
        if active:
            self.add_class("active")

    def on_click(self) -> None:
        self.post_message(Sidebar.ChannelSelected(self._channel_name))


class _EntityRow(Static):
    """A single entity row in the sidebar."""

    DEFAULT_CSS = """
    _EntityRow {
        width: 1fr;
        height: 1;
        padding: 0 2;
    }
    _EntityRow:hover {
        background: $accent 30%;
    }
    """

    def __init__(self, entity: EntityItem) -> None:
        dot = "[green]●[/]" if entity.online else "[dim]○[/]"
        icon = entity.icon or _TYPE_ICON.get(entity.entity_type, "")
        markup = f"{dot} {icon} {entity.nick}"
        super().__init__(markup, markup=True)
        self._nick = entity.nick

    def on_click(self) -> None:
        self.post_message(Sidebar.EntitySelected(self._nick))


# ---------------------------------------------------------------------------
# Sidebar widget
# ---------------------------------------------------------------------------


class Sidebar(Widget):
    """Left sidebar showing channels and entities grouped by type.

    Reactive properties ``channels``, ``entities``, and ``active_channel``
    trigger a full recompose when changed so the displayed list always
    reflects current state.
    """

    DEFAULT_CSS = """
    Sidebar {
        width: 24;
        border-right: solid $accent;
        overflow-y: auto;
    }
    Sidebar .section-header {
        width: 1fr;
        height: 1;
        padding: 0 1;
        color: $warning;
        text-style: bold;
    }
    """

    channels: reactive[list[ChannelItem]] = reactive(list, recompose=True)
    entities: reactive[list[EntityItem]] = reactive(list, recompose=True)
    active_channel: reactive[str] = reactive("", recompose=True)

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    class ChannelSelected(Message):
        """Posted when the user selects a channel in the sidebar."""

        def __init__(self, channel: str) -> None:
            super().__init__()
            self.channel = channel

    class EntitySelected(Message):
        """Posted when the user selects an entity in the sidebar."""

        def __init__(self, nick: str) -> None:
            super().__init__()
            self.nick = nick

    # ------------------------------------------------------------------
    # Composition
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        # Channels section
        yield Label("CHANNELS", classes="section-header")
        for ch in self.channels:
            yield _ChannelRow(ch, active=(ch.name == self.active_channel))

        # Entity sections grouped by type
        groups: dict[str, list[EntityItem]] = {t: [] for t in _GROUP_ORDER}
        for ent in self.entities:
            bucket = groups.get(ent.entity_type, groups["agent"])
            bucket.append(ent)

        for group_type in _GROUP_ORDER:
            members = groups[group_type]
            if not members:
                continue
            label = _GROUP_LABEL[group_type]
            yield Label(label, classes="section-header")
            for ent in members:
                yield _EntityRow(ent)
