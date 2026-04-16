"""Project-wide constants. Keep strings here, never in source code."""

from __future__ import annotations

import re

# System pseudo-user and channel
SYSTEM_USER_PREFIX = "system-"
SYSTEM_CHANNEL = "#system"
SYSTEM_USER_REALNAME = "Culture system messages"

# IRCv3 message-tag keys we emit/consume
EVENT_TAG_TYPE = "event"
EVENT_TAG_DATA = "event-data"

# Peer link capability (server-to-server)
PEER_CAPABILITY_EVENTS = "events/1"

# Reserved-nick pattern: any nick starting with `system-` is server-owned.
RESERVED_NICK_RE = re.compile(r"^system-[a-zA-Z0-9][a-zA-Z0-9\-]*$")

# Event-type name regex (dotted lowercase, ≥2 segments)
EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)+$")
