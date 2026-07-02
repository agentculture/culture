"""IRCv3 message-tags parsing and formatting round-trips."""

from culture_core.protocol.message import Message


def test_parse_single_tag():
    m = Message.parse("@event=user.join :nick!u@h PRIVMSG #c :hi\r\n")
    assert m.tags == {"event": "user.join"}
    assert m.prefix == "nick!u@h"
    assert m.command == "PRIVMSG"
    assert m.params == ["#c", "hi"]


def test_parse_multiple_tags():
    m = Message.parse("@a=1;b=2;c=3 PING :x\r\n")
    assert m.tags == {"a": "1", "b": "2", "c": "3"}
    assert m.command == "PING"


def test_parse_tag_without_value():
    m = Message.parse("@flag :x!u@h PRIVMSG #c :msg\r\n")
    assert m.tags == {"flag": ""}


def test_parse_tag_value_escapes():
    # IRCv3 escapes: \: → ; ; \s → space ; \\ → \ ; \r → CR ; \n → LF
    m = Message.parse(r"@k=a\:b\sc\\d\r\ne PING :x" + "\r\n")
    assert m.tags == {"k": "a;b c\\d\r\ne"}


def test_parse_no_tags():
    m = Message.parse(":nick PRIVMSG #c :body\r\n")
    assert m.tags == {}


def test_format_with_tags():
    m = Message(
        tags={"event": "user.join", "event-data": "eyJuIjoxfQ=="},
        prefix="system-spark!system@spark",
        command="PRIVMSG",
        params=["#system", "ori joined"],
    )
    line = m.format()
    assert line.startswith("@")
    assert "event=user.join" in line
    assert "event-data=eyJuIjoxfQ==" in line
    assert " :system-spark!system@spark PRIVMSG #system :ori joined\r\n" in line


def test_format_without_tags_includes_prefix():
    m = Message(tags={}, prefix="x", command="PING", params=["y"])
    assert m.format() == ":x PING y\r\n"


def test_format_escapes_tag_value():
    m = Message(
        tags={"k": "a;b c\\d\r\ne"},
        prefix=None,
        command="PING",
        params=["x"],
    )
    line = m.format()
    assert r"k=a\:b\sc\\d\r\ne" in line


def test_round_trip():
    original = "@event=user.join;event-data=e30= :n!u@h PRIVMSG #c :hello world\r\n"
    m = Message.parse(original)
    assert m.format() == original


def test_parse_unknown_escape_drops_backslash():
    """Per IRCv3 spec, unknown escapes (e.g. \\x) yield just the second char."""
    m = Message.parse(r"@k=foo\xbar PING :z" + "\r\n")
    assert m.tags == {"k": "fooxbar"}
