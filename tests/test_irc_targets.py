"""Tests for IRC target validation + sanitization (Qodo PR #30 #2).

The validation module is the single source of truth for what's a safe
IRC channel/target. These tests assert it rejects every known CRLF /
NUL / control-char injection variant and accepts legitimate names.
"""

from __future__ import annotations

import pytest

from culture.agentirc.irc_targets import (
    CHANNEL_NAME_MAX,
    InvalidIRCTarget,
    assert_safe_irc_line,
    parse_channels_arg,
    validate_channel_name,
)


class TestValidateChannelName:
    def test_accepts_valid_hash_channel(self):
        assert validate_channel_name("#team") == "#team"

    def test_accepts_other_rfc_prefixes(self):
        assert validate_channel_name("&local") == "&local"
        assert validate_channel_name("+modeless") == "+modeless"
        assert validate_channel_name("!safe") == "!safe"

    def test_accepts_max_length(self):
        name = "#" + "a" * (CHANNEL_NAME_MAX - 1)
        assert validate_channel_name(name) == name

    def test_rejects_empty(self):
        with pytest.raises(InvalidIRCTarget):
            validate_channel_name("")

    def test_rejects_no_prefix(self):
        with pytest.raises(InvalidIRCTarget):
            validate_channel_name("team")

    def test_rejects_overlong(self):
        with pytest.raises(InvalidIRCTarget):
            validate_channel_name("#" + "a" * CHANNEL_NAME_MAX)

    def test_rejects_carriage_return(self):
        with pytest.raises(InvalidIRCTarget):
            validate_channel_name("#team\rPRIVMSG #other :pwn")

    def test_rejects_line_feed(self):
        with pytest.raises(InvalidIRCTarget):
            validate_channel_name("#team\nPRIVMSG #other :pwn")

    def test_rejects_crlf(self):
        with pytest.raises(InvalidIRCTarget):
            validate_channel_name("#team\r\nPRIVMSG #other :pwn")

    def test_rejects_nul(self):
        with pytest.raises(InvalidIRCTarget):
            validate_channel_name("#team\0extra")

    def test_rejects_space(self):
        with pytest.raises(InvalidIRCTarget):
            validate_channel_name("#team room")

    def test_rejects_comma(self):
        with pytest.raises(InvalidIRCTarget):
            validate_channel_name("#team,#other")

    def test_rejects_bell(self):
        with pytest.raises(InvalidIRCTarget):
            validate_channel_name("#team\x07")

    def test_rejects_non_string(self):
        with pytest.raises(InvalidIRCTarget):
            validate_channel_name(123)  # type: ignore[arg-type]


class TestParseChannelsArg:
    def test_empty_string_returns_empty_list(self):
        assert parse_channels_arg("") == []

    def test_none_returns_empty_list(self):
        assert parse_channels_arg(None) == []

    def test_single_channel_with_prefix(self):
        assert parse_channels_arg("#team") == ["#team"]

    def test_single_channel_auto_prefixed(self):
        assert parse_channels_arg("team") == ["#team"]

    def test_multiple_channels(self):
        assert parse_channels_arg("#team,#joint-fixes,boss") == ["#team", "#joint-fixes", "#boss"]

    def test_strips_whitespace(self):
        assert parse_channels_arg(" #team ,  #joint  ") == ["#team", "#joint"]

    def test_empty_entries_skipped(self):
        assert parse_channels_arg("#a,,#b,") == ["#a", "#b"]

    def test_rejects_injection_attempt(self):
        with pytest.raises(InvalidIRCTarget):
            parse_channels_arg("#team,#team\r\nQUIT :pwn")

    def test_rejects_nul_in_entry(self):
        with pytest.raises(InvalidIRCTarget):
            parse_channels_arg("#good,#bad\0")


class TestAssertSafeIrcLine:
    def test_accepts_normal_command(self):
        line = "JOIN #team"
        assert assert_safe_irc_line(line) == line

    def test_accepts_with_irc_prefix_and_args(self):
        line = ":nick!user@host PRIVMSG #team :hello"
        assert assert_safe_irc_line(line) == line

    def test_rejects_cr(self):
        with pytest.raises(InvalidIRCTarget):
            assert_safe_irc_line("JOIN #team\rPRIVMSG x :y")

    def test_rejects_lf(self):
        with pytest.raises(InvalidIRCTarget):
            assert_safe_irc_line("JOIN #team\nPRIVMSG x :y")

    def test_rejects_crlf(self):
        with pytest.raises(InvalidIRCTarget):
            assert_safe_irc_line("JOIN #team\r\nPRIVMSG x :y")

    def test_rejects_nul(self):
        with pytest.raises(InvalidIRCTarget):
            assert_safe_irc_line("JOIN #team\0PRIVMSG x :y")

    def test_rejects_non_string(self):
        with pytest.raises(InvalidIRCTarget):
            assert_safe_irc_line(None)  # type: ignore[arg-type]
