"""Tests for GET /residents.json on the overview web server (plan task t7).

The endpoint is the second front door of the resource view (the first is
``culture residents --json``, task t5). The plan's acceptance criterion is
byte-compatibility: the endpoint body must be exactly ``json.dumps`` of
``culture_core.resource_view.serialize_residents(...)`` — one serializer,
one schema, verified here by diffing the endpoint bytes against a direct
serializer call over the same fixtures.

Response contract (feeds the irc-lens t8 brief — no 500s, ever):

* presence supported          -> 200, canonical payload
* no PRESENCE surface         -> 200, ``supported: false`` payload
* culture server unreachable  -> 503, ``{code, message, remediation}``
"""

from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import HTTPServer

import pytest

from culture_core.overview.renderer_web import _make_overview_handler
from culture_core.resource_view import (
    PresenceUnsupportedError,
    Resident,
    serialize_residents,
)

FIXED_NOW = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fixture_residents() -> list[Resident]:
    return [
        Resident(
            nick="spark-claude",
            server="spark",
            state="thinking",
            since="2026-07-07T11:00:00Z",
            task="review PR #471",
            tokens_in=900,
            tokens_out=100,
            presumed_hung=False,
            last_refresh="2026-07-07T11:59:30Z",
            token_budget=1000,
            budget_used_pct=100.0,
            budget_warning=True,
        ),
        Resident(nick="thor-codex", server="thor", state="idle"),
    ]


def _free_port() -> int:
    """Grab a port nothing is listening on (bind, read, close)."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _get(port: int, path: str = "/residents.json") -> tuple[int, dict, bytes]:
    """GET the path; return (status, headers, body) even for error statuses."""
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, dict(exc.headers), exc.read()


@pytest.fixture
def overview_server():
    """Factory: start the overview HTTP server, optionally injecting the
    residents fetch seam.

    ``_start(fetch=...)`` binds the handler exactly like ``serve_web`` does
    (via ``_make_overview_handler``), overrides the ``_fetch_residents``
    seam when a ``fetch`` callable is given, pins the deterministic-time
    seam to ``FIXED_NOW``, and returns the listening port.
    """
    running: list[tuple[HTTPServer, threading.Thread]] = []

    def _start(fetch=None, irc_port: int = 6667):
        handler_cls = _make_overview_handler(
            "127.0.0.1",
            irc_port,
            "testserv",
            None,
            None,
            4,
            5,
        )
        if fetch is not None:
            handler_cls._fetch_residents = lambda self: fetch()
        handler_cls.residents_now = FIXED_NOW
        httpd = HTTPServer(("127.0.0.1", 0), handler_cls)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        running.append((httpd, thread))
        return httpd.server_address[1]

    yield _start

    for httpd, thread in running:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Supported: 200 + canonical payload
# ---------------------------------------------------------------------------


def test_supported_returns_200_application_json(overview_server):
    port = overview_server(fetch=_fixture_residents)
    status, headers, body = _get(port)
    assert status == 200
    assert headers["Content-Type"] == "application/json"
    payload = json.loads(body)
    assert payload["supported"] is True
    assert [r["nick"] for r in payload["residents"]] == ["spark-claude", "thor-codex"]


def test_byte_compatible_with_canonical_serializer(overview_server):
    """THE t7 acceptance criterion: endpoint body == json.dumps of the one
    canonical serializer over the same fixtures — the CLI --json emits the
    same call, so the two surfaces can never drift."""
    port = overview_server(fetch=_fixture_residents)
    _status, _headers, body = _get(port)
    expected = json.dumps(serialize_residents(_fixture_residents(), True, now=FIXED_NOW))
    assert body == expected.encode()


# ---------------------------------------------------------------------------
# No PRESENCE surface: 200 + supported:false (known state, not an error)
# ---------------------------------------------------------------------------


def test_unsupported_presence_degrades_to_supported_false(overview_server):
    def _raise_unsupported():
        raise PresenceUnsupportedError("server replied 421 to the PRESENCE query")

    port = overview_server(fetch=_raise_unsupported)
    status, headers, body = _get(port)
    assert status == 200
    assert headers["Content-Type"] == "application/json"
    # Byte-for-byte the serializer's degraded payload as well.
    expected = json.dumps(serialize_residents([], False, now=FIXED_NOW))
    assert body == expected.encode()
    payload = json.loads(body)
    assert payload["supported"] is False
    assert payload["residents"] == []


# ---------------------------------------------------------------------------
# Culture server unreachable: 503 + structured error, never a bare 500
# ---------------------------------------------------------------------------


def test_unreachable_server_returns_503_structured_error(overview_server):
    def _raise_refused():
        raise ConnectionRefusedError("connection refused")

    port = overview_server(fetch=_raise_refused)
    status, headers, body = _get(port)
    assert status == 503
    assert headers["Content-Type"] == "application/json"
    err = json.loads(body)
    assert set(err) == {"code", "message", "remediation"}
    assert err["code"] == 503
    assert "cannot connect" in err["message"]
    assert err["remediation"]
    assert b"Traceback" not in body


def test_unreachable_server_oserror_also_503(overview_server):
    """Plain OSError (DNS failure, timeout subclass, ...) degrades the same
    way — the downstream irc-lens console must never see a 500."""

    def _raise_oserror():
        raise OSError("no route to host")

    port = overview_server(fetch=_raise_oserror)
    status, _headers, body = _get(port)
    assert status == 503
    assert set(json.loads(body)) == {"code", "message", "remediation"}


def test_real_fetch_seam_unreachable_end_to_end(overview_server):
    """No seam override: the production ``_fetch_residents`` queries the
    bound (dead) IRC port for real and the refused connection surfaces as
    the structured 503 — not an unhandled traceback in the handler."""
    port = overview_server(irc_port=_free_port())
    status, headers, body = _get(port)
    assert status == 503
    assert headers["Content-Type"] == "application/json"
    err = json.loads(body)
    assert set(err) == {"code", "message", "remediation"}


# ---------------------------------------------------------------------------
# Routing: only the exact path is the JSON endpoint
# ---------------------------------------------------------------------------


def test_query_string_is_ignored_for_routing(overview_server):
    port = overview_server(fetch=_fixture_residents)
    status, headers, _body = _get(port, "/residents.json?refresh=1")
    assert status == 200
    assert headers["Content-Type"] == "application/json"
