"""Managed-room helpers used by every client backend.

Moved here in culture 9.0.0 (Phase A3 of the agentirc extraction). Before
A3 these lived at `culture/agentirc/rooms_util.py` inside the bundled
IRCd; A3 deletes that fork and the daemons need a culture-side home for
helpers they actually use. `parse_room_meta` is the only function the
daemons reached for, so only that one moves here — `generate_room_id`
stays in agentirc (it's an IRCd-side concern, not a client one).
"""

from __future__ import annotations


def parse_room_meta(text: str) -> dict[str, str]:
    """Parse ``key=value;key=value;instructions=...`` room metadata.

    The ``instructions`` field, when present, must be last — everything
    after ``instructions=`` is captured verbatim (it may contain
    semicolons).
    """
    if not text:
        return {}

    result: dict[str, str] = {}

    if "instructions=" in text:
        before, instructions = text.split("instructions=", 1)
        result["instructions"] = instructions
        text = before.rstrip(";")

    if not text:
        return result

    for pair in text.split(";"):
        pair = pair.strip()
        if "=" in pair:
            key, value = pair.split("=", 1)
            result[key.strip()] = value.strip()

    return result


__all__ = ["parse_room_meta"]
