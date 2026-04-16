"""Bot filter DSL — safe expressions over event dicts."""

import pytest

from culture.bots.filter_dsl import (
    FilterParseError,
    compile_filter,
    evaluate,
)


def event(**kw):
    d = {"type": "user.join", "channel": "#general", "data": {"nick": "ori"}}
    d.update(kw)
    return d


def test_equality():
    f = compile_filter("type == 'user.join'")
    assert evaluate(f, event()) is True
    assert evaluate(f, event(type="user.part")) is False


def test_and():
    f = compile_filter("type == 'user.join' and channel == '#general'")
    assert evaluate(f, event()) is True
    assert evaluate(f, event(channel="#other")) is False


def test_or():
    f = compile_filter("type == 'user.join' or type == 'user.part'")
    assert evaluate(f, event()) is True
    assert evaluate(f, event(type="user.part")) is True
    assert evaluate(f, event(type="server.link")) is False


def test_not():
    f = compile_filter("not (type == 'user.join')")
    assert evaluate(f, event()) is False
    assert evaluate(f, event(type="user.part")) is True


def test_in_list():
    f = compile_filter("type in ['server.link', 'server.unlink']")
    assert evaluate(f, event(type="server.link")) is True
    assert evaluate(f, event(type="user.join")) is False


def test_dotted_field():
    f = compile_filter("data.nick == 'ori'")
    assert evaluate(f, event()) is True
    assert evaluate(f, event(data={"nick": "bob"})) is False


def test_missing_field_is_false():
    f = compile_filter("data.missing == 'x'")
    assert evaluate(f, event()) is False


def test_in_string_membership():
    f = compile_filter("'research' in data.tags")
    ev = event(data={"tags": ["research", "ai"]})
    assert evaluate(f, ev) is True
    ev2 = event(data={"tags": ["games"]})
    assert evaluate(f, ev2) is False


def test_parens_for_precedence():
    f = compile_filter("(type == 'a' or type == 'b') and channel == '#c'")
    assert evaluate(f, event(type="a", channel="#c")) is True
    assert evaluate(f, event(type="b", channel="#c")) is True
    assert evaluate(f, event(type="a", channel="#other")) is False


def test_parse_error_message():
    with pytest.raises(FilterParseError) as exc:
        compile_filter("type = 'x'")  # single '=' invalid
    assert exc.value.column >= 0
    assert exc.value.expected


def test_parse_error_unclosed_string():
    with pytest.raises(FilterParseError):
        compile_filter("type == 'unclosed")


def test_parse_error_no_function_calls():
    with pytest.raises(FilterParseError):
        compile_filter("exec('x')")
