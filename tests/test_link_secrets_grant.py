"""Link secrets survive reboot through grant-injected server units (#509).

Every test uses a temporary fake grant executable and/or a monkeypatched OS
keyring lookup.  Nothing reads the real grant store, keyring, systemd, or
network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
from pathlib import Path

import pytest

from culture_core import credentials, persistence
from culture_core.cli import _build_parser
from culture_core.cli import mesh as mesh_cli
from culture_core.cli import server as server_cli
from culture_core.cli._errors import CultureError
from culture_core.cli.shared import mesh as shared_mesh
from culture_core.mesh_config import MeshConfig, MeshLinkConfig, MeshServerConfig, save_mesh_config


def _exec_start(unit_text: str) -> list[str]:
    line = next(line for line in unit_text.splitlines() if line.startswith("ExecStart="))
    return shlex.split(line.removeprefix("ExecStart="))


def _fake_grant(
    tmp_path: Path,
    *,
    names: list[str],
    expected_stdin: str | None = None,
) -> tuple[Path, Path]:
    """Create a grant double that lists names and verifies set uses stdin.

    Its log contains argv only.  The expected secret is represented in the
    script by a SHA-256 digest, never written to the log or printed.
    """
    script = tmp_path / "grant"
    log = tmp_path / "grant-argv.json"
    expected_digest = hashlib.sha256((expected_stdin or "").encode()).hexdigest()
    script.write_text(
        "#!/usr/bin/python3\n"
        "import hashlib, json, pathlib, sys\n"
        f"names = {names!r}\n"
        f"log = pathlib.Path({str(log)!r})\n"
        "if sys.argv[1:] == ['list', '--json']:\n"
        "    print(json.dumps({'ok': True, 'names': names}))\n"
        "    raise SystemExit(0)\n"
        "if len(sys.argv) > 3 and sys.argv[1] == 'set' and sys.argv[3] == '-':\n"
        "    value = sys.stdin.read()\n"
        f"    expected = {expected_digest!r}\n"
        "    if hashlib.sha256(value.encode()).hexdigest() != expected:\n"
        "        raise SystemExit(9)\n"
        "    log.write_text(json.dumps(sys.argv[1:]))\n"
        "    print('grant: set ' + sys.argv[2])\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(8)\n"
    )
    script.chmod(0o700)
    return script, log


def _write_mesh(tmp_path: Path, *peers: str) -> Path:
    path = tmp_path / "mesh.yaml"
    links = [MeshLinkConfig(name=peer, host=f"{peer}.example.test", port=6667) for peer in peers]
    save_mesh_config(
        MeshConfig(server=MeshServerConfig(name="spark2", links=links)),
        path,
    )
    return path


def test_lookup_credential_prefers_grant_injected_environment(monkeypatch):
    monkeypatch.setenv("CULTURE_LINK_SECRET_THOR_EU", "from-grant-run")

    def _no_keyring(*_args, **_kwargs):
        raise AssertionError("the keyring must not be touched when grant injected the secret")

    monkeypatch.setattr(credentials, "_run", _no_keyring)

    assert credentials.lookup_credential("thor.eu") == "from-grant-run"


def test_empty_injected_environment_falls_back_to_keyring(monkeypatch):
    monkeypatch.setenv("CULTURE_LINK_SECRET_THOR", "")
    monkeypatch.setattr(credentials, "lookup_keyring_credential", lambda _peer: "legacy")

    assert credentials.lookup_credential("thor") == "legacy"


def test_link_secret_name_normalizes_peer_exactly():
    assert credentials.link_secret_name("Thor.eu-west_1") == "CULTURE_LINK_SECRET_THOR_EU_WEST_1"


def test_resolve_links_collision_skips_injected_value_and_uses_each_keyring_entry(
    monkeypatch, caplog
):
    mesh = MeshConfig(
        server=MeshServerConfig(
            name="spark2",
            links=[
                MeshLinkConfig(name="peer-a", host="one.example.test"),
                MeshLinkConfig(name="peer.a", host="two.example.test"),
            ],
        )
    )
    monkeypatch.setattr("culture_core.mesh_config.load_mesh_config", lambda _path: mesh)
    monkeypatch.setenv("CULTURE_LINK_SECRET_PEER_A", "wrong-shared-injected-value")
    keyring_values = {"peer-a": "first-keyring", "peer.a": "second-keyring"}
    monkeypatch.setattr(
        "culture_core.credentials.lookup_keyring_credential",
        keyring_values.__getitem__,
    )

    with caplog.at_level("WARNING", logger="culture"):
        links = shared_mesh.resolve_links_from_mesh("/tmp/fake-mesh.yaml")

    assert [(link.name, link.password) for link in links] == [
        ("peer-a", "first-keyring"),
        ("peer.a", "second-keyring"),
    ]
    warning = "\n".join(record.message for record in caplog.records)
    assert "peer-a" in warning and "peer.a" in warning
    assert "injected" in warning and "OS keyring" in warning


def test_missing_credential_warning_names_environment_and_keyring(monkeypatch, caplog):
    mesh = MeshConfig(
        server=MeshServerConfig(
            name="spark2",
            links=[MeshLinkConfig(name="thor", host="thor.example.test")],
        )
    )
    monkeypatch.setattr("culture_core.mesh_config.load_mesh_config", lambda _path: mesh)
    monkeypatch.setattr("culture_core.credentials.lookup_credential", lambda _name: None)

    with caplog.at_level("WARNING", logger="culture"):
        assert shared_mesh.resolve_links_from_mesh("/tmp/fake-mesh.yaml") == []

    warning = "\n".join(record.message for record in caplog.records)
    assert "CULTURE_LINK_SECRET_THOR" in warning
    assert "OS keyring" in warning


def test_grant_wrap_accepts_several_injects_and_parks_exit_64():
    wrapped = persistence.grant_wrap(
        ["/usr/bin/python3", "-m", "culture_core"],
        grant_bin="/opt/bin/grant",
        injects=[
            ("CULTURE_LINK_SECRET_THOR", "CULTURE_LINK_SECRET_THOR"),
            ("CULTURE_LINK_SECRET_ZEUS", "CULTURE_LINK_SECRET_ZEUS"),
        ],
    )

    assert wrapped == [
        "/opt/bin/grant",
        "run",
        "--inject",
        "CULTURE_LINK_SECRET_THOR=CULTURE_LINK_SECRET_THOR",
        "--inject",
        "CULTURE_LINK_SECRET_ZEUS=CULTURE_LINK_SECRET_ZEUS",
        "--",
        "/usr/bin/python3",
        "-m",
        "culture_core",
    ]
    assert persistence.is_grant_wrapped(wrapped)
    unit = persistence._build_systemd_unit("culture-server-spark2", wrapped, "server")
    assert "RestartPreventExitStatus=78 64" in unit
    assert persistence.exec_start_has_inject(unit, "CULTURE_LINK_SECRET_ZEUS")


def test_server_install_injects_only_existing_grant_link_secrets(tmp_path, monkeypatch, capsys):
    present = "CULTURE_LINK_SECRET_THOR"
    fake_grant, _log = _fake_grant(tmp_path, names=[present])
    mesh_path = _write_mesh(tmp_path, "thor", "missing")
    unit_dir = tmp_path / "systemd" / "user"
    monkeypatch.setattr(persistence, "resolve_grant_bin", lambda **_kwargs: str(fake_grant))
    monkeypatch.setattr(persistence, "get_platform", lambda: "linux")
    monkeypatch.setattr(persistence, "_systemd_user_dir", lambda: unit_dir)
    monkeypatch.setattr(persistence, "_run_cmd", lambda *_args, **_kwargs: True)

    server_cli._server_install(
        argparse.Namespace(
            config=str(mesh_path),
            standalone=False,
            allow_dev_interpreter=True,
        )
    )

    unit = (unit_dir / "culture-server-spark2.service").read_text()
    argv = _exec_start(unit)
    assert argv[:5] == [
        str(fake_grant),
        "run",
        "--inject",
        f"{present}={present}",
        "--",
    ]
    assert "CULTURE_LINK_SECRET_MISSING" not in unit
    assert "RestartPreventExitStatus=78 64" in unit
    warning = capsys.readouterr().err
    assert "missing" in warning
    assert "grant" in warning


def test_server_install_without_any_grant_link_secrets_stays_silent(tmp_path, monkeypatch, capsys):
    fake_grant, _log = _fake_grant(tmp_path, names=[])
    mesh_path = _write_mesh(tmp_path, "thor", "missing")
    monkeypatch.setattr(persistence, "resolve_grant_bin", lambda **_kwargs: str(fake_grant))
    monkeypatch.setattr(persistence, "install_service", lambda *_args, **_kwargs: tmp_path / "unit")

    server_cli._server_install(
        argparse.Namespace(
            config=str(mesh_path),
            standalone=False,
            allow_dev_interpreter=True,
        )
    )

    assert capsys.readouterr().err == ""


def test_server_install_rejects_link_secret_collision_before_install(tmp_path, monkeypatch):
    mesh_path = _write_mesh(tmp_path, "peer-a", "peer.a")
    monkeypatch.setattr(
        persistence,
        "install_service",
        lambda *_args, **_kwargs: pytest.fail("installed colliding link secrets"),
    )

    with pytest.raises(CultureError, match="collision.*peer-a.*peer\\.a"):
        server_cli._server_install(
            argparse.Namespace(
                config=str(mesh_path),
                standalone=False,
                allow_dev_interpreter=True,
            )
        )


def test_shared_grant_inject_resolution_omits_colliding_names(monkeypatch):
    mesh = MeshConfig(
        server=MeshServerConfig(
            name="spark2",
            links=[
                MeshLinkConfig(name="peer-a", host="one.example.test"),
                MeshLinkConfig(name="peer.a", host="two.example.test"),
            ],
        )
    )
    monkeypatch.setattr(persistence, "resolve_grant_bin", lambda **_kwargs: "/tmp/grant")
    monkeypatch.setattr(
        credentials,
        "grant_secret_names",
        lambda _bin: {"CULTURE_LINK_SECRET_PEER_A"},
    )

    assert shared_mesh.grant_link_injects(mesh) == ("/tmp/grant", [])


def test_secrets_to_grant_uses_names_only_and_stdin(tmp_path, monkeypatch, capsys):
    secret = "migration-secret-not-for-output"
    existing = "CULTURE_LINK_SECRET_ALREADY_THERE"
    fake_grant, log = _fake_grant(
        tmp_path,
        names=[existing],
        expected_stdin=secret,
    )
    mesh_path = _write_mesh(tmp_path, "new-peer", "already.there", "locked")
    looked_up: list[str] = []

    def _fake_keyring(peer: str) -> str | None:
        looked_up.append(peer)
        return {"new-peer": secret, "locked": None}[peer]

    monkeypatch.setattr(persistence, "resolve_grant_bin", lambda **_kwargs: str(fake_grant))
    monkeypatch.setattr(credentials, "lookup_keyring_credential", _fake_keyring)

    args = _build_parser().parse_args(["mesh", "secrets-to-grant", "--config", str(mesh_path)])
    with pytest.raises(CultureError) as exc:
        mesh_cli.dispatch(args)

    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert secret not in output
    assert looked_up == ["new-peer", "locked"]
    assert "new-peer" in output and "copied" in output
    assert "already.there" in output and "already" in output
    assert "locked" in output and "keyring locked or empty" in output
    grant_log = log.read_text()
    assert secret not in grant_log
    assert exc.value.code != 0
    grant_argv = json.loads(grant_log)
    assert grant_argv[:3] == ["set", "CULTURE_LINK_SECRET_NEW_PEER", "-"]


def test_secrets_to_grant_processes_every_peer_then_fails_for_uncopied(
    tmp_path, monkeypatch, capsys
):
    mesh_path = _write_mesh(tmp_path, "locked", "copy-fails", "copied", "already")
    looked_up: list[str] = []
    stored: list[str] = []
    monkeypatch.setattr(persistence, "resolve_grant_bin", lambda: "/tmp/fake-grant")
    monkeypatch.setattr(
        credentials,
        "grant_secret_names",
        lambda _bin: {"CULTURE_LINK_SECRET_ALREADY"},
    )

    def _lookup(peer: str) -> str | None:
        looked_up.append(peer)
        return None if peer == "locked" else f"secret-for-{peer}"

    def _store(_bin: str, name: str, _value: str) -> bool:
        stored.append(name)
        return name != "CULTURE_LINK_SECRET_COPY_FAILS"

    monkeypatch.setattr(credentials, "lookup_keyring_credential", _lookup)
    monkeypatch.setattr(credentials, "store_grant_secret", _store)
    args = _build_parser().parse_args(["mesh", "secrets-to-grant", "--config", str(mesh_path)])

    with pytest.raises(CultureError) as exc:
        mesh_cli.dispatch(args)

    assert exc.value.code != 0
    assert looked_up == ["locked", "copy-fails", "copied"]
    assert stored == ["CULTURE_LINK_SECRET_COPY_FAILS", "CULTURE_LINK_SECRET_COPIED"]
    output = capsys.readouterr().out
    assert "locked: skipped" in output
    assert "copy-fails: failed" in output
    assert "copied: copied" in output
    assert "already: already" in output


def test_secrets_to_grant_rejects_link_secret_collision_before_grant_lookup(tmp_path, monkeypatch):
    mesh_path = _write_mesh(tmp_path, "peer-a", "peer.a")
    monkeypatch.setattr(
        persistence,
        "resolve_grant_bin",
        lambda: pytest.fail("resolved grant before rejecting collision"),
    )
    args = _build_parser().parse_args(["mesh", "secrets-to-grant", "--config", str(mesh_path)])

    with pytest.raises(CultureError, match="collision.*peer-a.*peer\\.a"):
        mesh_cli.dispatch(args)


def test_secrets_to_grant_parser_is_registered():
    args = _build_parser().parse_args(["mesh", "secrets-to-grant", "--config", "/tmp/m.yaml"])
    assert args.mesh_command == "secrets-to-grant"
    assert args.config == "/tmp/m.yaml"
