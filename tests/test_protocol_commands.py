"""Tests for `culture.protocol.commands` — IRC verb constants.

The module is a flat registry of uppercase string constants (RFC 2812 +
culture extensions + S2S federation verbs). The contract is: each public
name maps to a string equal to its own name, so the registry stays in
sync with the IRC wire format. One discovery-style test covers all
statements; adding a new verb does not require a test edit.
"""

from __future__ import annotations

from culture.protocol import commands


def _public_constants() -> dict[str, object]:
    return {
        name: value
        for name, value in vars(commands).items()
        if name.isupper() and not name.startswith("_")
    }


def test_module_exports_at_least_the_known_rfc_verbs():
    """Smoke-check: a known RFC 2812 baseline must always be present."""
    rfc_baseline = {
        "NICK",
        "USER",
        "QUIT",
        "JOIN",
        "PART",
        "PRIVMSG",
        "NOTICE",
        "PING",
        "PONG",
        "TOPIC",
        "NAMES",
        "MODE",
        "WHO",
        "WHOIS",
    }
    exported = set(_public_constants())
    missing = rfc_baseline - exported
    assert not missing, f"missing RFC 2812 verbs: {sorted(missing)}"


def test_every_command_constant_is_a_nonempty_string():
    for name, value in _public_constants().items():
        assert isinstance(value, str), f"{name} is not a str: {type(value)!r}"
        assert value, f"{name} is empty"


def test_every_command_constant_matches_its_name():
    """The wire verb (value) must equal the python name (attribute)."""
    for name, value in _public_constants().items():
        assert value == name, f"{name} = {value!r}, expected {name!r}"
