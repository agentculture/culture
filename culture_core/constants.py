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

# Event-type name regex (dotted lowercase, ≥2 segments)
EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)+$")
