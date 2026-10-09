"""Guest-sandbox IRCd provisioning (guest-mode-sandbox t1, obligation o3).

The sandbox is an isolated, unlinked ``sbx`` culture IRCd bound to 127.0.0.1
only.  Unit tests cover the ``culture server install --standalone`` unit
command; the integration test starts a real ``sbx`` server subprocess next to
a real in-process ``spark`` IRCd on random ports and proves they never federate.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import socket
import subprocess
import sys
import time

import pytest

from culture_core.cli import server as srv_mod
from culture_core.cli._errors import CultureError
from culture_core.cli.shared.mesh import build_standalone_server_start_cmd
from tests.conftest import IRCTestClient

_CULTURE = [sys.executable, "-m", "culture_core"]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _install_args(**kw) -> argparse.Namespace:
    base = dict(
        standalone=True,
        name="sbx",
        host="127.0.0.1",
        port=6700,
        webhook_port=7700,
        data_dir=None,
        config="/nonexistent/mesh.yaml",
        allow_dev_interpreter=True,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def _flag(cmd: list[str], flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


# --- unit: the unit command (AC1) -------------------------------------------


def test_standalone_cmd_loopback_ports_datadir_and_no_federation():
    cmd = build_standalone_server_start_cmd(
        _CULTURE, name="sbx", port=6700, webhook_port=7700, data_dir="/d/sbx"
    )
    assert _flag(cmd, "--host") == "127.0.0.1"
    assert _flag(cmd, "--port") == "6700"
    assert _flag(cmd, "--webhook-port") == "7700"
    assert _flag(cmd, "--data-dir") == "/d/sbx"
    assert "--foreground" in cmd
    assert "--link" not in cmd
    assert "--mesh-config" not in cmd


def test_cli_install_standalone_writes_unit_without_mesh_yaml(monkeypatch, capsys):
    captured = {}

    def fake_install(name, command, description, after=None, **kw):
        captured.update(name=name, command=command, kw=kw)
        return "/tmp/fake.service"

    monkeypatch.setattr("culture_core.persistence.install_service", fake_install)
    monkeypatch.setattr(
        srv_mod, "_load_mesh_for_provisioning", lambda *_: pytest.fail("must not read mesh.yaml")
    )
    srv_mod._server_install(_install_args())
    cmd = captured["command"]
    assert captured["name"] == "culture-server-sbx"
    assert _flag(cmd, "--host") == "127.0.0.1"
    assert _flag(cmd, "--data-dir") == os.path.expanduser("~/.culture/data-sbx")
    assert "--link" not in cmd and "--mesh-config" not in cmd
    assert "Installed culture-server-sbx" in capsys.readouterr().out


def test_cli_install_standalone_requires_ports():
    with pytest.raises(CultureError, match="--port"):
        srv_mod._server_install(_install_args(port=None))


def test_cli_parser_accepts_standalone_flags():
    parser = argparse.ArgumentParser()
    srv_mod.register(parser.add_subparsers(dest="command"))
    ns = parser.parse_args(
        ["server", "install", "--standalone", "--name", "sbx", "--port", "6700"]
        + ["--webhook-port", "7700", "--data-dir", "/d"]
    )
    assert (ns.standalone, ns.name, ns.port, ns.webhook_port) == (True, "sbx", 6700, 7700)
    assert ns.host == "127.0.0.1"


def test_cli_uninstall_by_name_skips_mesh_yaml(monkeypatch, capsys):
    removed = []
    monkeypatch.setattr(
        "culture_core.persistence.uninstall_service", lambda s: removed.append(s) or True
    )
    monkeypatch.setattr(
        srv_mod, "_load_mesh_for_provisioning", lambda *_: pytest.fail("must not read mesh.yaml")
    )
    srv_mod._server_uninstall(argparse.Namespace(name="sbx", config="x"))
    assert removed == ["culture-server-sbx"]


# --- integration: real sbx next to a real spark (AC1 + AC2) ------------------


def _listening_binds(pid: int) -> set[tuple[str, int]]:
    """(addr, port) of every TCP LISTEN socket owned by *pid*, via ``ss``."""
    out = subprocess.run(["ss", "-ltnpH"], capture_output=True, text=True, check=True).stdout
    binds = set()
    for line in out.splitlines():
        if f"pid={pid}," not in line:
            continue
        local = line.split()[3]
        addr, _, port = local.rpartition(":")
        binds.add((addr.strip("[]"), int(port)))
    return binds


async def _register(port: int, nick: str) -> IRCTestClient:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    c = IRCTestClient(reader, writer)
    await c.send(f"NICK {nick}")
    await c.send(f"USER {nick} 0 * :{nick}")
    await c.recv_until(" 001 ")
    return c


@pytest.fixture
def sbx_process(tmp_path):
    """A real ``sbx`` server subprocess with an isolated HOME and data-dir."""
    port, webhook = _free_port(), _free_port()
    data_dir = tmp_path / "data-sbx"
    cmd = build_standalone_server_start_cmd(
        _CULTURE, name="sbx", port=port, webhook_port=webhook, data_dir=str(data_dir)
    )
    env = {**os.environ, "HOME": str(tmp_path)}
    log = open(tmp_path / "sbx.log", "w")
    proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT)
    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"sbx exited early:\n{(tmp_path / 'sbx.log').read_text()}")
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("sbx never listened")
    yield proc, port, webhook, data_dir, tmp_path / "sbx.log"
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()


def test_sbx_binds_loopback_only(sbx_process):
    proc, port, webhook, data_dir, _ = sbx_process
    binds = _listening_binds(proc.pid)
    assert (("127.0.0.1", port)) in binds
    assert binds, "no listeners found"
    assert {addr for addr, _ in binds} == {"127.0.0.1"}, binds
    assert {p for _, p in binds} <= {port, webhook}


@pytest.mark.asyncio
async def test_sbx_and_spark_do_not_federate(server, sbx_process):
    """Real spark IRCd + real sbx: no S2S handshake, no shared channel."""
    proc, sbx_port, _, data_dir, log = sbx_process
    assert server.config.links == []

    guest = await _register(sbx_port, "sbx-guest")
    mesh = await _register(server.config.port, "spark-ori")
    try:
        await guest.send("JOIN #general")
        await mesh.send("JOIN #general")
        await guest.recv_all(timeout=0.5)
        await mesh.recv_all(timeout=0.5)

        await guest.send("PRIVMSG #general :hello from the sandbox")
        await mesh.send("PRIVMSG #general :hello from the mesh")
        assert not any("sandbox" in ln for ln in await mesh.recv_all(timeout=1.0))
        assert not any("from the mesh" in ln for ln in await guest.recv_all(timeout=1.0))

        # Neither side knows the other's nicks.
        await mesh.send("NAMES #general")
        assert "sbx-guest" not in await mesh.recv_until("366")
        await guest.send("NAMES #general")
        assert "spark-ori" not in await guest.recv_until("366")
    finally:
        await guest.close()
        await mesh.close()

    # Even an attempted S2S handshake from spark does not link: sbx has no
    # link configured, so no peer ever appears on either side.
    try:
        await server.connect_to_peer("127.0.0.1", sbx_port, "any-password")
    except Exception:
        pass
    await asyncio.sleep(1.0)
    assert server.links == {} or "sbx" not in server.links

    # sbx never dialed out: it holds no connection to the spark port.
    est = subprocess.run(["ss", "-tnpH", "state", "established"], capture_output=True, text=True)
    spark_dials = [
        ln
        for ln in est.stdout.splitlines()
        if f"pid={proc.pid}," in ln and re.search(rf":{server.config.port}\s", ln.split("users")[0])
    ]
    assert spark_dials == []
    assert "linking" not in log.read_text().lower()
