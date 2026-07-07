"""Tests for GET /residents.json on the overview web server (plan task t7).

The endpoint is the second front door of the resource view (the first is
``culture residents --json``, task t5). The plan's acceptance criterion is
byte-compatibility: the endpoint body must be exactly ``json.dumps`` of
``culture_core.resource_view.serialize_residents(...)`` — one serializer,
one schema, verified here by diffing the endpoint bytes against a direct
serializer call over the same fixtures.

Response contract (feeds the irc-lens t8 brief — no bare 500s, ever):

* presence supported          -> 200, canonical payload
* no PRESENCE surface         -> 200, ``supported: false`` payload
* culture server unreachable  -> 503, ``{code, message, remediation}``
* anything unexpected         -> 500, ``{code, message, remediation}``
  (defensive: still structured JSON, never a traceback page)
"""

from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer

import pytest

from culture_core.overview.renderer_web import _make_overview_handler
from culture_core.resource_view import (
    PresenceUnsupportedError,
    Resident,
    serialize_residents,
    to_json,
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
    (via ``_make_overview_handler`` on a ``ThreadingHTTPServer`` — the same
    server class production uses since the F8 fix), overrides the
    ``_fetch_residents`` seam when a ``fetch`` callable is given, pins the
    deterministic-time seam to ``FIXED_NOW``, and returns the listening
    port.
    """
    running: list[tuple[ThreadingHTTPServer, threading.Thread]] = []

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
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
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
    """THE t7 acceptance criterion: endpoint body == ``to_json`` (the one
    canonical dumps site in culture_core.resource_view) of the one canonical
    serializer over the same fixtures — the CLI --json emits the literal
    same ``to_json(serialize_residents(...))`` call, so the two surfaces can
    never drift (test_residents_cli.py asserts the CLI side)."""
    port = overview_server(fetch=_fixture_residents)
    _status, _headers, body = _get(port)
    expected = to_json(serialize_residents(_fixture_residents(), True, now=FIXED_NOW))
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
# Unexpected exception: structured 500, never a bare traceback page
# ---------------------------------------------------------------------------


def test_unexpected_exception_returns_structured_500(overview_server):
    """Anything unexpected escaping the fetch seam (the F3 class of bug,
    defensively) must yield the structured {code, message, remediation}
    JSON 500 — the irc-lens console must never see a traceback page."""

    def _raise_unexpected():
        raise ZeroDivisionError("division by zero")

    port = overview_server(fetch=_raise_unexpected)
    status, headers, body = _get(port)
    assert status == 500
    assert headers["Content-Type"] == "application/json"
    err = json.loads(body)
    assert set(err) == {"code", "message", "remediation"}
    assert err["code"] == 500
    assert err["remediation"]
    assert b"Traceback" not in body


# ---------------------------------------------------------------------------
# Threading: a slow /residents.json fetch must not stall other requests
# ---------------------------------------------------------------------------


def test_slow_fetch_does_not_stall_other_requests(overview_server):
    """The overview server is a ThreadingHTTPServer (F8): each residents
    request runs a fresh IRC connect+register that can take seconds, and on
    the old single-threaded HTTPServer one slow fetch blocked every other
    page load. Prove a second request completes while the first is stuck."""
    started = threading.Event()
    release = threading.Event()
    calls: list[int] = []

    def _fetch():
        calls.append(1)
        if len(calls) == 1:
            started.set()
            release.wait(timeout=10)
        return _fixture_residents()

    port = overview_server(fetch=_fetch)
    slow_result: dict = {}
    slow_thread = threading.Thread(
        target=lambda: slow_result.update(status=_get(port)[0]), daemon=True
    )
    slow_thread.start()
    try:
        assert started.wait(timeout=5), "first request never reached the fetch seam"
        # While the first request is blocked inside its fetch, a second
        # request must still be served (would hang until timeout on a
        # single-threaded server).
        status, _headers, _body = _get(port)
        assert status == 200
        assert "status" not in slow_result, "slow request finished too early"
    finally:
        release.set()
        slow_thread.join(timeout=10)
    assert slow_result.get("status") == 200


# ---------------------------------------------------------------------------
# Routing: only the exact path is the JSON endpoint
# ---------------------------------------------------------------------------


def test_query_string_is_ignored_for_routing(overview_server):
    port = overview_server(fetch=_fixture_residents)
    status, headers, _body = _get(port, "/residents.json?refresh=1")
    assert status == 200
    assert headers["Content-Type"] == "application/json"
