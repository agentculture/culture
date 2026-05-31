"""Alert routers — IRC, email, webhook (v8.19.19).

Each ``send_*`` method takes a ready ``Alert`` and ships it over the
chosen transport. Failures are logged but never propagated — we never
let one broken transport take the watcher down. IRC is always-on (the
mesh is the canonical fan-out point); email + webhook are opt-in via
``~/.culture/watcher.yaml`` (so a default install never exfiltrates
data to an unconfigured external endpoint).
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Optional

from culture.watcher.patterns import Alert, PatternEvent

logger = logging.getLogger(__name__)


@dataclass
class IRCConfig:
    enabled: bool = True
    target_nick: str = ""  # nudge the orchestrator (boss) directly
    fallback_channel: str = "#alerts"


@dataclass
class EmailConfig:
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    password_env: str = ""  # env-var name holding the password — never the password itself
    from_addr: str = ""
    to_addrs: tuple[str, ...] = ()
    use_starttls: bool = True


@dataclass
class WebhookConfig:
    enabled: bool = False
    url: str = ""
    secret_env: str = ""  # env-var name (HMAC-style); not sent if blank


@dataclass
class AlertSinks:
    irc: IRCConfig
    email: EmailConfig
    webhook: WebhookConfig


class AlertRouter:
    """Routes ``PatternEvent`` records to the configured sinks.

    Construct with a ``send_irc`` callable (closure binding the IRC
    observer used by the watcher service) and the parsed sink config.
    The IRC callable signature is ``async def send_irc(target: str,
    text: str) -> None``. Email + webhook are synchronous (used inside
    ``run_in_executor`` by the service).
    """

    def __init__(self, sinks: AlertSinks):
        self.sinks = sinks

    @classmethod
    def from_config_dict(cls, data: dict[str, Any]) -> "AlertRouter":
        alerts = data.get("alerts") or {}
        irc_d = alerts.get("irc") or {}
        email_d = alerts.get("email") or {}
        webhook_d = alerts.get("webhook") or {}
        return cls(
            AlertSinks(
                irc=IRCConfig(
                    enabled=bool(irc_d.get("enabled", True)),
                    target_nick=str(irc_d.get("target_nick", "") or ""),
                    fallback_channel=str(irc_d.get("fallback_channel", "#alerts") or "#alerts"),
                ),
                email=EmailConfig(
                    enabled=bool(email_d.get("enabled", False)),
                    smtp_host=str(email_d.get("smtp_host", "") or ""),
                    smtp_port=int(email_d.get("smtp_port", 587) or 587),
                    smtp_user=str(email_d.get("smtp_user", "") or ""),
                    password_env=str(email_d.get("password_env", "") or ""),
                    from_addr=str(email_d.get("from_addr", "") or ""),
                    to_addrs=tuple(email_d.get("to_addrs") or ()),
                    use_starttls=bool(email_d.get("use_starttls", True)),
                ),
                webhook=WebhookConfig(
                    enabled=bool(webhook_d.get("enabled", False)),
                    url=str(webhook_d.get("url", "") or ""),
                    secret_env=str(webhook_d.get("secret_env", "") or ""),
                ),
            )
        )

    # --- Formatting --------------------------------------------------------

    def format_irc_line(self, ev: PatternEvent) -> str:
        prefix = {"high": "[ALERT]", "medium": "[warn]", "low": "[info]"}.get(ev.severity, "[?]")
        return f"{prefix} {ev.summary}"

    def format_email_body(self, ev: PatternEvent) -> tuple[str, str]:
        subject = f"culture watcher: {ev.pattern} — {ev.target}"
        body = (
            f"Pattern: {ev.pattern}\n"
            f"Severity: {ev.severity}\n"
            f"Target: {ev.target}\n"
            f"Summary: {ev.summary}\n\n"
            f"Detail:\n{ev.detail or '(none)'}\n"
        )
        return subject, body

    def format_webhook_payload(self, ev: PatternEvent) -> dict[str, Any]:
        return {
            "pattern": ev.pattern,
            "severity": ev.severity,
            "target": ev.target,
            "summary": ev.summary,
            "detail": ev.detail,
            "ts": ev.ts,
        }

    # --- Sinks -------------------------------------------------------------

    def irc_recipients(self) -> list[str]:
        if not self.sinks.irc.enabled:
            return []
        out: list[str] = []
        if self.sinks.irc.target_nick:
            out.append(self.sinks.irc.target_nick)
        if self.sinks.irc.fallback_channel:
            out.append(self.sinks.irc.fallback_channel)
        return out

    def send_email(self, ev: PatternEvent) -> bool:
        cfg = self.sinks.email
        if not cfg.enabled:
            return False
        if not (cfg.smtp_host and cfg.from_addr and cfg.to_addrs):
            logger.warning("email alert skipped: SMTP config incomplete")
            return False
        password: Optional[str] = None
        if cfg.password_env:
            password = os.environ.get(cfg.password_env)
            if not password:
                logger.warning("email alert skipped: env var %s is empty", cfg.password_env)
                return False
        subject, body = self.format_email_body(ev)
        msg = EmailMessage()
        msg["From"] = cfg.from_addr
        msg["To"] = ", ".join(cfg.to_addrs)
        msg["Subject"] = subject
        msg.set_content(body)
        try:
            ctx = ssl.create_default_context()
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=10) as srv:
                if cfg.use_starttls:
                    srv.starttls(context=ctx)
                if cfg.smtp_user and password:
                    srv.login(cfg.smtp_user, password)
                srv.send_message(msg)
            return True
        except (OSError, smtplib.SMTPException) as exc:
            logger.warning("email alert failed: %s", exc)
            return False

    def send_webhook(self, ev: PatternEvent) -> bool:
        cfg = self.sinks.webhook
        if not cfg.enabled or not cfg.url:
            return False
        payload = self.format_webhook_payload(ev)
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": "culture-watcher/1"}
        if cfg.secret_env:
            secret = os.environ.get(cfg.secret_env)
            if secret:
                # Naive shared-secret header; consumers can verify.
                headers["X-Culture-Watcher-Secret"] = secret
        req = urllib.request.Request(cfg.url, data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if 200 <= resp.status < 300:
                    return True
                logger.warning("webhook returned %s", resp.status)
                return False
        except (urllib.error.URLError, OSError) as exc:
            logger.warning("webhook alert failed: %s", exc)
            return False
