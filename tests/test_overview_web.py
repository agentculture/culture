"""Tests for overview web renderer."""
import time

from agentirc.overview.model import Agent, Message, MeshState, Room
from agentirc.overview.renderer_web import render_html


def _make_fixture() -> MeshState:
    now = time.time()
    agent = Agent(
        nick="spark-claude", status="active", activity="working on: tests",
        channels=["#general"], server="spark",
        backend="claude", model="claude-opus-4-6",
    )
    msg = Message(nick="spark-claude", text="hello", timestamp=now - 60, channel="#general")
    room = Room(
        name="#general", topic="Testing",
        members=[agent], operators=["spark-claude"],
        federation_servers=[], messages=[msg],
    )
    return MeshState(server_name="spark", rooms=[room], agents=[agent], federation_links=[])


def test_render_html_produces_valid_html():
    mesh = _make_fixture()
    html = render_html(mesh, message_limit=4)
    assert "<!DOCTYPE html>" in html
    assert "<html" in html
    assert "</html>" in html


def test_render_html_contains_content():
    mesh = _make_fixture()
    html = render_html(mesh, message_limit=4)
    assert "spark mesh" in html
    assert "#general" in html
    assert "spark-claude" in html
    assert "hello" in html


def test_render_html_has_cream_styles():
    mesh = _make_fixture()
    html = render_html(mesh, message_limit=4)
    assert "#faf7f2" in html or "faf7f2" in html


def test_render_html_has_auto_refresh():
    mesh = _make_fixture()
    html = render_html(mesh, message_limit=4, refresh_interval=5)
    assert 'http-equiv="refresh"' in html or "refresh" in html.lower()
