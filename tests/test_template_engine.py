"""Tests for the bot template engine."""

import json

from agentirc.bots.template_engine import render_fallback, render_template


def test_simple_substitution():
    result = render_template("{body.name}", {"name": "test"})
    assert result == "test"


def test_nested_substitution():
    payload = {"repo": {"full_name": "ori/agentirc", "branch": "main"}}
    result = render_template(
        "Repo: {body.repo.full_name} Branch: {body.repo.branch}",
        payload,
    )
    assert result == "Repo: ori/agentirc Branch: main"


def test_multiple_tokens():
    payload = {"action": "completed", "status": "success"}
    result = render_template("Job {body.action}: {body.status}", payload)
    assert result == "Job completed: success"


def test_missing_field_returns_none():
    result = render_template("{body.nonexistent}", {"other": "value"})
    assert result is None


def test_partial_missing_returns_none():
    payload = {"action": "completed"}
    result = render_template("{body.action} {body.missing}", payload)
    assert result is None


def test_deeply_nested():
    payload = {"a": {"b": {"c": {"d": "deep"}}}}
    result = render_template("{body.a.b.c.d}", payload)
    assert result == "deep"


def test_numeric_value():
    result = render_template("Count: {body.count}", {"count": 42})
    assert result == "Count: 42"


def test_boolean_value():
    result = render_template("Done: {body.done}", {"done": True})
    assert result == "Done: True"


def test_null_value():
    result = render_template("Val: {body.val}", {"val": None})
    assert result == "Val: null"


def test_no_tokens():
    result = render_template("Static message", {"anything": "here"})
    assert result == "Static message"


def test_body_only():
    result = render_template("{body}", "raw-string")
    assert result == "raw-string"


def test_render_fallback_json():
    payload = {"key": "value", "num": 1}
    result = render_fallback(payload, "json")
    parsed = json.loads(result)
    assert parsed == payload


def test_render_fallback_str():
    payload = {"key": "value"}
    result = render_fallback(payload, "str")
    assert "key" in result
