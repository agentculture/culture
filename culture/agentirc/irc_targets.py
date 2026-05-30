"""Validation + sanitization for IRC targets (channels, nicks).

IRC protocol lines are CRLF-delimited; any user-controlled string that
makes it into a raw line MUST be checked for CR/LF or it's an injection
vector. Per Qodo PR #30 #2 (security finding on the v8.19.0 --channels
flag + HISTORY backfill paths).

This module is shared between:

* the CLI (``culture boss spawn --channels`` parsing)
* the per-backend daemon IPC (``_ipc_irc_join``, ``_ipc_irc_part``,
  ``_ipc_irc_send``)
* the transport's outbound ``send_raw`` (last line of defense)

The check is the same everywhere: forbidden codepoints are CR (``\\r``),
LF (``\\n``), NUL (``\\0``), space, comma (channel-list separator),
and the bell (``\\x07``). Channels additionally must start with ``#``
(or ``&``/``+``/``!`` per RFC 2812 §1.3) and be ≤ 50 characters.
"""

from __future__ import annotations

# RFC 2812 §2.3.1: channel names start with one of these prefixes.
_CHANNEL_PREFIXES = ("#", "&", "+", "!")

# Channel name max length per RFC 2812 §1.3 (50 octets including prefix).
CHANNEL_NAME_MAX = 50

# Codepoints that MUST NOT appear in any IRC target: CR, LF, NUL, space,
# comma (target-list separator per RFC 2812 §3), bell.
_FORBIDDEN_TARGET_CHARS = frozenset("\r\n\0 ,\x07")


class InvalidIRCTarget(ValueError):
    """A channel or nick contains characters forbidden by the IRC protocol.

    Distinct from a plain ``ValueError`` so callers can disambiguate the
    failure (e.g., HTTP 400 vs HTTP 500 in dashboard handlers).
    """


def validate_channel_name(name: str) -> str:
    """Return *name* unchanged if it is a valid IRC channel name, else raise.

    Raises ``InvalidIRCTarget`` for: empty, missing/incorrect prefix,
    overlong, or containing any of CR/LF/NUL/space/comma/bell.

    Does NOT modify the input — sanitization (stripping forbidden chars)
    would mask attempts at injection, which we want to *fail loudly* so
    the orchestrator + audit log can see them.
    """
    if not isinstance(name, str):
        raise InvalidIRCTarget(f"channel name must be str, got {type(name).__name__}")
    if not name:
        raise InvalidIRCTarget("channel name is empty")
    if not name.startswith(_CHANNEL_PREFIXES):
        raise InvalidIRCTarget(f"channel name {name!r} must start with one of {_CHANNEL_PREFIXES}")
    if len(name) > CHANNEL_NAME_MAX:
        raise InvalidIRCTarget(f"channel name {name!r} exceeds {CHANNEL_NAME_MAX} octets")
    bad = _FORBIDDEN_TARGET_CHARS & set(name)
    if bad:
        raise InvalidIRCTarget(
            f"channel name contains forbidden characters {sorted(bad)!r}: {name!r}"
        )
    return name


def parse_channels_arg(raw: str | None) -> list[str]:
    """Parse a ``--channels`` CLI argument into a validated list.

    Accepts a comma-separated string. Each entry is stripped of
    surrounding whitespace, prefixed with ``#`` if it lacks a channel
    prefix, then validated. Invalid entries raise ``InvalidIRCTarget``
    (with the offending entry quoted) — DO NOT silently drop or
    sanitize, since the security goal is to surface injection
    attempts, not hide them.

    Returns an empty list for ``None`` or ``""``.
    """
    if not raw:
        return []
    out: list[str] = []
    for raw_entry in raw.split(","):
        entry = raw_entry.strip()
        if not entry:
            continue
        # Auto-prefix with '#' for ergonomics, but only if the entry
        # doesn't already carry a valid prefix.
        if not entry.startswith(_CHANNEL_PREFIXES):
            entry = "#" + entry
        validate_channel_name(entry)
        out.append(entry)
    return out


def assert_safe_irc_line(line: str) -> str:
    """Last-line-of-defense check before writing a raw IRC line to the wire.

    Even if every caller pre-validates its inputs, a defense-in-depth
    check here catches future regressions where a new send path forgets
    to validate. Refuses any string containing CR/LF/NUL.

    Returns *line* unchanged on success; raises ``InvalidIRCTarget`` on
    failure (caller should log + drop the message).
    """
    if not isinstance(line, str):
        raise InvalidIRCTarget(f"IRC line must be str, got {type(line).__name__}")
    if "\r" in line or "\n" in line or "\0" in line:
        raise InvalidIRCTarget(
            "IRC line contains forbidden control characters (CR/LF/NUL); "
            "this is the last-line CRLF-injection guard — fix the caller."
        )
    return line
