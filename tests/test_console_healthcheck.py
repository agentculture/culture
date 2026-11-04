"""Health pings use real loopback HTTP and never a real service manager."""

import argparse
import json
import socket
import subprocess
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
import yaml

from culture_core import persistence
from culture_core.cli import console
from culture_core.cli._errors import CultureError
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
    bodies = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(status)
            self.end_headers()

        def do_POST(self):
            requests.append(self.path)
            bodies.append(
                (self.rfile.read(int(self.headers["Content-Length"])).decode(), self.headers)
            )
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
        yield f"http://127.0.0.1:{server.server_port}", requests, bodies
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
        # Exit status says whether the report got through: /fail is a success.
        assert result == (1 if missing else 0)
        assert ping[1] == ([] if missing else [expected])
        if not missing:
            assert local[1] == [READY]
            assert public[1] == [READY]


def _ready_ping(checks, monkeypatch):
    """Run a full check with both /readyz passing; return the report sent."""
    with listener() as local, listener() as public, listener() as ping:
        monkeypatch.setenv(SECRET_NAME, ping[0] + "/ping")
        assert healthcheck.check_readiness(local[0], public[0], checks) == 0
        return ping[1], [body for body, _headers in ping[2]]


def _agent_config(tmp_path, url, models, name="agent.yaml"):
    keys = tmp_path / "keys.env"
    keys.write_text("GW_KEY=gateway-key-value\n")
    path = tmp_path / name
    path.write_text(
        yaml.safe_dump(
            {
                "gateway_url": url + "/chat",
                "key_env": "GW_KEY",
                "key_file": str(keys),
                "models": models,
            }
        )
    )
    return path


def _stub_units(monkeypatch, states):
    def run(cmd, **_kw):
        return subprocess.CompletedProcess(cmd, 0, stdout=states[cmd[-1]] + "\n")

    monkeypatch.setattr(healthcheck.subprocess, "run", run)


def test_no_checks_file_keeps_readyz_only(tmp_path, monkeypatch):
    assert _ready_ping(tmp_path / "absent.yaml", monkeypatch) == (["/ping"], [])


def test_stopped_unit_fails_and_names_it(tmp_path, monkeypatch):
    checks = tmp_path / "checks.yaml"
    checks.write_text("units: [culture-server-sbx, culture-agent-x, culture-agent-y]\n")
    _stub_units(
        monkeypatch,
        {
            "culture-server-sbx": "inactive",
            "culture-agent-x": "active",
            "culture-agent-y": "activating",
        },
    )
    assert _ready_ping(checks, monkeypatch) == (
        ["/ping/fail"],
        ["unit culture-server-sbx: inactive\nunit culture-agent-y: activating"],
    )


def test_refused_model_fails_though_gateway_is_up(tmp_path, monkeypatch):
    with listener(404) as gateway:
        checks = tmp_path / "checks.yaml"
        agent = _agent_config(tmp_path, gateway[0], ["gone"])
        checks.write_text(f"agents: ['{agent}']\n")
        assert _ready_ping(checks, monkeypatch) == (
            ["/ping/fail"],
            ["demo agent agent.yaml: no model answers"],
        )


def test_model_probe_is_authorised_small_and_shared(tmp_path, monkeypatch):
    with listener() as gateway:
        checks = tmp_path / "checks.yaml"
        one = _agent_config(tmp_path, gateway[0], ["lane"], "one.yaml")
        two = _agent_config(tmp_path, gateway[0], ["lane"], "two.yaml")
        checks.write_text(f"agents: ['{one}', '{two}']\n")
        assert _ready_ping(checks, monkeypatch) == (["/ping"], [])
        assert gateway[1] == ["/chat"]
        body, headers = gateway[2][0]
        assert json.loads(body)["max_tokens"] == 1
        assert json.loads(body)["model"] == "lane"
        assert headers["Authorization"] == "Bearer gateway-key-value"


def test_fallback_model_counts_as_answering(tmp_path, monkeypatch):
    tried = []

    def answers(_url, model, _key):
        tried.append(model)
        return model == "second"

    monkeypatch.setattr(healthcheck, "_model_answers", answers)
    checks = tmp_path / "checks.yaml"
    agent = _agent_config(tmp_path, "http://127.0.0.1:1", ["first", "second"])
    checks.write_text(f"agents: ['{agent}']\n")
    assert _ready_ping(checks, monkeypatch) == (["/ping"], [])
    assert tried == ["first", "second"]


@pytest.mark.parametrize(
    "text",
    ["units: [", "units: 5\n", "units: [--help]\n", "agents: [5]\n", "- a\n"],
    ids=["bad_yaml", "not_a_list", "option_as_unit", "not_a_path", "not_a_mapping"],
)
def test_bad_checks_file_fails_rather_than_passes(text, tmp_path, monkeypatch):
    checks = tmp_path / "checks.yaml"
    checks.write_text(text)
    pings, bodies = _ready_ping(checks, monkeypatch)
    assert pings == ["/ping/fail"]
    assert bodies[0].startswith("healthcheck checks file:")


def test_failure_body_never_carries_the_key(tmp_path, monkeypatch):
    with listener(500) as gateway:
        checks = tmp_path / "checks.yaml"
        agent = _agent_config(tmp_path, gateway[0], ["lane"])
        checks.write_text(f"agents: ['{agent}']\n")
        _pings, bodies = _ready_ping(checks, monkeypatch)
    assert "gateway-key-value" not in bodies[0]
    assert gateway[0] not in bodies[0]


def test_readyz_failure_is_named(monkeypatch):
    with listener(503) as local, listener() as public, listener() as ping:
        monkeypatch.setenv(SECRET_NAME, ping[0] + "/ping")
        assert healthcheck.check_readiness(local[0], public[0]) == 0
        assert ping[2][0][0] == "local /readyz"


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
    monkeypatch.setattr(
        healthcheck.subprocess,
        "run",
        lambda cmd, **_kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0),
    )
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
    assert ["systemctl", "--user", "start", "--no-block", SERVICE] in calls
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


def test_install_reports_failed_systemctl(tmp_path, lens_config, monkeypatch):
    """A failing `enable --now` must not read as a working monitor."""
    monkeypatch.setattr(persistence, "get_platform", lambda: "linux")
    monkeypatch.setattr(persistence, "_systemd_user_dir", lambda: tmp_path / "units")
    monkeypatch.setattr(persistence, "resolve_grant_bin", lambda **kw: "/opt/bin/grant")
    monkeypatch.setattr(console.sys, "executable", "/usr/bin/python3")
    monkeypatch.setattr(
        healthcheck.subprocess,
        "run",
        lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 1),
    )
    with pytest.raises(CultureError, match="could not activate"):
        healthcheck.install_healthcheck(str(lens_config), allow_dev_interpreter=True)


def test_probe_sends_its_own_user_agent(monkeypatch):
    """Cloudflare answers Python's default user agent with 403."""
    seen = []

    class Opener:
        def open(self, request, timeout):
            seen.append(request.get_header("User-agent"))
            raise OSError("not reached")

    monkeypatch.setattr(healthcheck.urllib.request, "build_opener", lambda *_h: Opener())
    assert healthcheck._request_ok("https://example.org/readyz") is False
    assert seen == ["culture-healthcheck"]


def test_malformed_response_counts_as_not_ok():
    """http.client's errors aren't OSError, and must not kill the report."""
    server = socket.create_server(("127.0.0.1", 0))
    port = server.getsockname()[1]

    def answer():
        conn, _ = server.accept()
        conn.recv(1024)
        conn.sendall(b"not http at all\r\n\r\n")
        conn.close()

    thread = Thread(target=answer, daemon=True)
    thread.start()
    try:
        assert healthcheck._request_ok(f"http://127.0.0.1:{port}/readyz", use_proxy=False) is False
    finally:
        thread.join(5)
        server.close()


def test_report_retries_before_giving_up(monkeypatch):
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            raise OSError("down")

    monkeypatch.setattr(healthcheck.urllib.request, "build_opener", lambda *_h: Opener())
    assert healthcheck._request_ok("https://example.org/ping", attempts=3) is False
    assert len(calls) == 3


def test_non_string_bind_gets_a_clean_result(tmp_path):
    path = tmp_path / "lens.yaml"
    path.write_text(f"web:\n  port: 9876\n  bind: 123\nmedia:\n  public_base_url: {PUBLIC_BASE}\n")
    local, _public = healthcheck.read_addresses(path)
    assert local == "http://123:9876"


def test_uninstall_removes_units_and_is_safe_to_repeat(tmp_path, monkeypatch, capsys):
    units = tmp_path / "units"
    units.mkdir()
    (units / SERVICE).write_text("x")
    (units / TIMER).write_text("x")
    calls = []
    monkeypatch.setattr(persistence, "get_platform", lambda: "linux")
    monkeypatch.setattr(persistence, "_systemd_user_dir", lambda: units)
    monkeypatch.setattr(
        healthcheck.subprocess,
        "run",
        lambda cmd, **_kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0),
    )
    healthcheck.uninstall_healthcheck()
    healthcheck.uninstall_healthcheck()
    assert not (units / SERVICE).exists()
    assert not (units / TIMER).exists()
    assert ["systemctl", "--user", "disable", "--now", TIMER] in calls


def test_console_uninstall_flag_leaves_the_console_unit_alone(monkeypatch, capsys):
    removed = []
    monkeypatch.setattr(healthcheck, "uninstall_healthcheck", lambda: removed.append(True))
    monkeypatch.setattr(
        "culture_core.persistence.uninstall_service",
        lambda *_a, **_kw: pytest.fail("touched the console unit"),
    )
    args = argparse.Namespace(console_args=["uninstall", "--healthcheck"])
    with pytest.raises(SystemExit) as exc:
        console.dispatch(args)
    assert exc.value.code == 0
    assert removed == [True]
