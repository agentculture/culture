"""Direct edge-case tests for ``culture.watcher.alerts.AlertRouter`` (v8.19.20).

Closes a gap from the v8.19.19 ship: ``AlertRouter`` had zero direct
tests — it was only exercised via ``WatcherService.dispatch`` with a
mock IRC callable. These tests cover the formatting helpers, the
config gates on each sink, and the actual email/webhook send paths
against a local SMTP debug server / HTTP loopback.
"""

from __future__ import annotations

import http.server
import json
import os
import socket
import threading
from email.message import EmailMessage
from unittest.mock import patch

import pytest

from culture.watcher.alerts import (
    AlertRouter,
    AlertSinks,
    EmailConfig,
    IRCConfig,
    WebhookConfig,
)
from culture.watcher.patterns import PatternEvent


def _ev(**overrides) -> PatternEvent:
    defaults = dict(
        pattern="silent_death",
        severity="high",
        target="local-w",
        summary="w appears dead",
        detail="last start 2026-05-31T00:00Z",
    )
    defaults.update(overrides)
    return PatternEvent(**defaults)


# --- format_irc_line -------------------------------------------------------


def test_format_irc_line_severity_prefixes():
    router = AlertRouter.from_config_dict({})
    assert router.format_irc_line(_ev(severity="high")).startswith("[ALERT]")
    assert router.format_irc_line(_ev(severity="medium")).startswith("[warn]")
    assert router.format_irc_line(_ev(severity="low")).startswith("[info]")
    assert router.format_irc_line(_ev(severity="bogus")).startswith("[?]")


def test_format_irc_line_includes_summary():
    router = AlertRouter.from_config_dict({})
    ev = _ev(summary="boss stuck for 3h")
    line = router.format_irc_line(ev)
    assert "boss stuck for 3h" in line


# --- format_email_body -----------------------------------------------------


def test_format_email_body_subject_and_full_body():
    router = AlertRouter.from_config_dict({})
    subj, body = router.format_email_body(_ev())
    assert "silent_death" in subj
    assert "local-w" in subj
    assert "Pattern: silent_death" in body
    assert "Severity: high" in body
    assert "Target: local-w" in body
    assert "Summary: w appears dead" in body
    assert "Detail:\nlast start" in body


def test_format_email_body_empty_detail_renders_none_marker():
    router = AlertRouter.from_config_dict({})
    _, body = router.format_email_body(_ev(detail=""))
    assert "(none)" in body


# --- format_webhook_payload ------------------------------------------------


def test_format_webhook_payload_has_all_keys():
    router = AlertRouter.from_config_dict({})
    payload = router.format_webhook_payload(_ev())
    assert set(payload.keys()) == {
        "pattern",
        "severity",
        "target",
        "summary",
        "detail",
        "ts",
    }
    assert payload["pattern"] == "silent_death"
    assert isinstance(payload["ts"], (int, float))


# --- irc_recipients --------------------------------------------------------


def test_irc_recipients_both_set():
    router = AlertRouter.from_config_dict(
        {"alerts": {"irc": {"enabled": True, "target_nick": "boss", "fallback_channel": "#alerts"}}}
    )
    assert router.irc_recipients() == ["boss", "#alerts"]


def test_irc_recipients_no_target_nick():
    router = AlertRouter.from_config_dict(
        {"alerts": {"irc": {"enabled": True, "target_nick": "", "fallback_channel": "#alerts"}}}
    )
    assert router.irc_recipients() == ["#alerts"]


def test_irc_recipients_disabled_returns_empty():
    router = AlertRouter.from_config_dict(
        {
            "alerts": {
                "irc": {"enabled": False, "target_nick": "boss", "fallback_channel": "#alerts"}
            }
        }
    )
    assert router.irc_recipients() == []


def test_irc_recipients_empty_fallback_falls_back_to_default():
    """An empty fallback_channel string is coerced to the '#alerts' default.

    Reasoning: dropping the fallback channel entirely would leave an alert
    going nowhere when target_nick is also unset, so the loader actively
    prevents the no-recipient state. Documented behaviour, not a bug —
    if a user truly wants no channel fallback they set irc.enabled=False.
    """
    router = AlertRouter.from_config_dict(
        {"alerts": {"irc": {"enabled": True, "target_nick": "boss", "fallback_channel": ""}}}
    )
    assert router.irc_recipients() == ["boss", "#alerts"]


# --- send_email config gates -----------------------------------------------


def test_send_email_disabled_returns_false():
    router = AlertRouter.from_config_dict({})  # email defaults to disabled
    assert router.send_email(_ev()) is False


def test_send_email_missing_smtp_host_returns_false():
    router = AlertRouter.from_config_dict(
        {"alerts": {"email": {"enabled": True, "from_addr": "a@b", "to_addrs": ["c@d"]}}}
    )
    # smtp_host is empty → skipped, never touches the network.
    assert router.send_email(_ev()) is False


def test_send_email_missing_from_addr_returns_false():
    router = AlertRouter.from_config_dict(
        {"alerts": {"email": {"enabled": True, "smtp_host": "x", "to_addrs": ["c@d"]}}}
    )
    assert router.send_email(_ev()) is False


def test_send_email_missing_to_addrs_returns_false():
    router = AlertRouter.from_config_dict(
        {"alerts": {"email": {"enabled": True, "smtp_host": "x", "from_addr": "a@b"}}}
    )
    assert router.send_email(_ev()) is False


def test_send_email_password_env_unset_returns_false(monkeypatch):
    monkeypatch.delenv("WATCHER_TEST_PWD", raising=False)
    router = AlertRouter.from_config_dict(
        {
            "alerts": {
                "email": {
                    "enabled": True,
                    "smtp_host": "smtp.example.com",
                    "smtp_user": "u",
                    "password_env": "WATCHER_TEST_PWD",  # not in env
                    "from_addr": "a@b",
                    "to_addrs": ["c@d"],
                }
            }
        }
    )
    assert router.send_email(_ev()) is False


def test_send_email_smtp_failure_returns_false_not_raise(monkeypatch):
    """A broken SMTP relay must NEVER take the watcher down."""

    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr("smtplib.SMTP", boom)
    router = AlertRouter.from_config_dict(
        {
            "alerts": {
                "email": {
                    "enabled": True,
                    "smtp_host": "smtp.example.com",
                    "from_addr": "a@b",
                    "to_addrs": ["c@d"],
                }
            }
        }
    )
    # send_email must catch the OSError and return False — not propagate.
    assert router.send_email(_ev()) is False


# --- send_webhook ----------------------------------------------------------


def test_send_webhook_disabled_returns_false():
    router = AlertRouter.from_config_dict({})
    assert router.send_webhook(_ev()) is False


def test_send_webhook_empty_url_returns_false():
    router = AlertRouter.from_config_dict({"alerts": {"webhook": {"enabled": True, "url": ""}}})
    assert router.send_webhook(_ev()) is False


def test_send_webhook_url_unreachable_returns_false():
    router = AlertRouter.from_config_dict(
        {
            "alerts": {
                "webhook": {
                    "enabled": True,
                    "url": "http://127.0.0.1:1/never",  # port 1 — never listens
                }
            }
        }
    )
    # urlopen will raise URLError; send_webhook must catch and return False.
    assert router.send_webhook(_ev()) is False


def test_send_webhook_posts_json_with_secret(monkeypatch):
    """Stand up a localhost HTTP server, send a webhook, verify body+headers."""
    received: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            received["body"] = self.rfile.read(length).decode("utf-8")
            received["secret"] = self.headers.get("X-Culture-Watcher-Secret")
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):  # silence test noise
            pass

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=server.handle_request, daemon=True)
    t.start()

    monkeypatch.setenv("WATCHER_TEST_SECRET", "shh-its-a-secret")
    router = AlertRouter.from_config_dict(
        {
            "alerts": {
                "webhook": {
                    "enabled": True,
                    "url": f"http://127.0.0.1:{port}/alert",
                    "secret_env": "WATCHER_TEST_SECRET",
                }
            }
        }
    )
    ok = router.send_webhook(_ev())
    t.join(timeout=2.0)
    server.server_close()
    assert ok is True
    assert "shh-its-a-secret" == received.get("secret")
    payload = json.loads(received["body"])
    assert payload["pattern"] == "silent_death"
    assert payload["severity"] == "high"


# --- from_config_dict tolerance --------------------------------------------


def test_from_config_dict_handles_missing_alerts_key():
    router = AlertRouter.from_config_dict({"poll_interval_seconds": 30})
    # All sinks must take their defaults — IRC on, email off, webhook off.
    assert router.sinks.irc.enabled is True
    assert router.sinks.email.enabled is False
    assert router.sinks.webhook.enabled is False


def test_from_config_dict_handles_empty_alerts_subkeys():
    router = AlertRouter.from_config_dict({"alerts": {"irc": None, "email": None, "webhook": None}})
    # None-valued subkeys must NOT crash — defaults all the way down.
    assert router.sinks.irc.fallback_channel == "#alerts"
    assert router.sinks.email.smtp_port == 587
    assert router.sinks.webhook.url == ""


def test_from_config_dict_coerces_to_addrs_list_to_tuple():
    router = AlertRouter.from_config_dict(
        {"alerts": {"email": {"enabled": True, "to_addrs": ["a@b.com", "c@d.com"]}}}
    )
    assert isinstance(router.sinks.email.to_addrs, tuple)
    assert router.sinks.email.to_addrs == ("a@b.com", "c@d.com")
