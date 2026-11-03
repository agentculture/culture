"""Health pings use real loopback HTTP and never a real service manager."""

import argparse
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from culture_core import persistence
from culture_core.cli import console
from culture_core.cli.shared import healthcheck

SECRET_NAME = "CULTURE_HEALTHCHECK_URL"
READY = "/readyz"
CONFIG_FLAG = "--config"
SERVICE = "culture-healthcheck.service"
TIMER = "culture-healthcheck.timer"
PUBLIC_BASE = "http://127.0.0.1:9988"


@contextmanager
def listener(status=200):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(status)
            self.end_headers()

        def log_message(self, *_args):
            pass

    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    except PermissionError:
        pytest.skip("sandbox blocks local HTTP listener sockets")
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize(
    ("local_status", "public_status", "missing", "expected"),
    [
        (200, 200, False, "/ping"),
        (503, 200, False, "/ping/fail"),
        (200, 503, False, "/ping/fail"),
        (200, 200, True, None),
    ],
    ids=["both_pass", "local_fails", "public_fails", "url_missing"],
)
def test_ping(local_status, public_status, missing, expected, monkeypatch):
    with listener(local_status) as local, listener(public_status) as public, listener() as ping:
        monkeypatch.delenv(SECRET_NAME, raising=False)
        if not missing:
            monkeypatch.setenv(SECRET_NAME, ping[0] + "/ping")
        result = healthcheck.check_readiness(local[0], public[0])
        assert result == (0 if expected == "/ping" else 1)
        assert ping[1] == ([] if missing else [expected])
        if not missing:
            assert local[1] == [READY]
            assert public[1] == [READY]


@pytest.fixture
def lens_config(tmp_path):
    path = tmp_path / "lens config.yaml"
    path.write_text(f"web:\n  port: 9876\nmedia:\n  public_base_url: {PUBLIC_BASE}\n")
    return path


def test_config_addresses(lens_config):
    assert healthcheck.read_addresses(lens_config) == ("http://127.0.0.1:9876", PUBLIC_BASE)


def test_install_healthcheck(tmp_path, lens_config, monkeypatch, capsys):
    units = tmp_path / "units"
    calls = []
    monkeypatch.setattr(persistence, "get_platform", lambda: "linux")
    monkeypatch.setattr(persistence, "_systemd_user_dir", lambda: units)
    monkeypatch.setattr(persistence, "_run_cmd", lambda cmd: calls.append(cmd) or True)
    monkeypatch.setattr(persistence, "resolve_grant_bin", lambda **kw: "/opt/bin/grant")
    monkeypatch.setattr(console.sys, "executable", "/usr/bin/python3")
    monkeypatch.setattr(console, "DEFAULT_CONFIG", str(tmp_path / "absent-server.yaml"))
    secret_value = "secret-value-that-must-never-be-written"
    monkeypatch.setenv(SECRET_NAME, secret_value)
    args = argparse.Namespace(
        console_args=["install", "--healthcheck", CONFIG_FLAG, str(lens_config)]
    )
    for _ in range(2):
        with pytest.raises(SystemExit) as exc:
            console.dispatch(args)
        assert exc.value.code == 0
    service = (units / SERVICE).read_text()
    timer = (units / TIMER).read_text()
    assert f"--inject {SECRET_NAME}={SECRET_NAME} --" in service
    assert "-m culture_core.cli.shared.healthcheck" in service
    assert str(lens_config) in service
    assert "Type=oneshot" in service
    assert "Restart=on-failure" not in service
    assert "OnUnitActiveSec=5min" in timer
    assert ["systemctl", "--user", "enable", "--now", TIMER] in calls
    assert ["systemctl", "--user", "start", SERVICE] in calls
    assert secret_value not in service + timer + capsys.readouterr().out
    assert sorted(p.name for p in units.iterdir()) == [SERVICE, TIMER]


def test_missing_url_without_network(monkeypatch):
    monkeypatch.delenv(SECRET_NAME, raising=False)
    assert healthcheck.check_readiness(PUBLIC_BASE, PUBLIC_BASE) == 1


def test_default_lens_config(lens_config, monkeypatch):
    import irc_lens.config

    monkeypatch.setattr(irc_lens.config, "default_config_path", lambda: lens_config)
    assert healthcheck.read_addresses() == healthcheck.read_addresses(lens_config)


def test_config_requires_public_address(tmp_path):
    from culture_core.cli._errors import CultureError

    path = tmp_path / "incomplete.yaml"
    path.write_text("web: {}\n")
    with pytest.raises(CultureError):
        healthcheck.read_addresses(path)
