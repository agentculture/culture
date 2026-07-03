# tests/test_provisioning_interpreter_guard.py
"""Provisioning provenance guard (always-on-mesh plan, task t3).

2026-07-03 outage: ``culture server install`` was run from the agent-t13
*dev worktree* venv, so the generated unit's ExecStart baked that worktree's
interpreter via ``sys.executable``. When the worktree/venv was later removed
the ExecStart pointed at a dead path and the server crash-looped 11,235 times.

The guard classifies the interpreter a provisioning verb is about to bake and
refuses (or, with ``--allow-dev-interpreter``, warns and proceeds) when it
lives inside a project/worktree virtualenv rather than an installed tool.

Design: the heuristic is a *pure* function (``classify_interpreter``) so it is
exhaustively unit-testable; the override flag is the escape hatch for any
false positive.
"""

import argparse
import sys
from unittest.mock import patch

import pytest

from culture_core.cli._errors import EXIT_USER_ERROR, CultureError
from culture_core.persistence import (
    InterpreterClass,
    classify_interpreter,
    install_service,
)

# Representative interpreter paths for the classification matrix.
UV_TOOL = "/home/x/.local/share/uv/tools/culture/bin/python3"
PIPX = "/home/x/.local/share/pipx/venvs/culture/bin/python"
SYSTEM_USR = "/usr/bin/python3"
SYSTEM_LOCAL = "/usr/local/bin/python3"
SYSTEM_BREW = "/opt/homebrew/bin/python3.12"
SYSTEM_PYENV = "/home/x/.pyenv/versions/3.11.4/bin/python"
SYSTEM_BARE = "culture"
REPO_VENV = "/home/spark/git/culture/.venv/bin/python"
WORKTREE_VENV = "/home/spark/git/culture-worktrees/agent-t13/.venv/bin/python"
VENV_NODOT = "/home/x/project/venv/bin/python"
# A project/worktree venv nested under a tree that *also* satisfies the uv or
# pipx adjacency heuristic (Qodo #5): the parent checkout happens to live
# under a directory named "uv/tools" (or "pipx/venvs"), but the interpreter
# itself is still a fragile `.venv` inside that checkout, not a tool-managed
# venv. Fragility must win.
UV_TOOLS_NESTED_VENV = "/home/me/repos/uv/tools/culture/.venv/bin/python"
PIPX_VENVS_NESTED_VENV = "/home/me/repos/pipx/venvs/culture/.venv/bin/python"


# --------------------------------------------------------------------------
# Pure classifier matrix
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,expected",
    [
        (UV_TOOL, InterpreterClass.UV_TOOL),
        (PIPX, InterpreterClass.PIPX),
        (SYSTEM_USR, InterpreterClass.SYSTEM),
        (SYSTEM_LOCAL, InterpreterClass.SYSTEM),
        (SYSTEM_BREW, InterpreterClass.SYSTEM),
        (SYSTEM_PYENV, InterpreterClass.SYSTEM),
        (SYSTEM_BARE, InterpreterClass.SYSTEM),
        (REPO_VENV, InterpreterClass.DEV_VENV),
        (WORKTREE_VENV, InterpreterClass.DEV_VENV),
        (VENV_NODOT, InterpreterClass.DEV_VENV),
        (UV_TOOLS_NESTED_VENV, InterpreterClass.DEV_VENV),
        (PIPX_VENVS_NESTED_VENV, InterpreterClass.DEV_VENV),
    ],
)
def test_classify_matrix(path, expected):
    assert classify_interpreter(path) is expected


def test_classify_no_regression_adjacent_without_venv_component_stays_durable():
    """Qodo #5 no-regression: the real uv-tool / pipx layouts have NO
    .venv/venv path component (the venv dir is named after the tool, e.g.
    'culture', not '.venv'), so moving the DEV_VENV check ahead of the
    adjacency heuristics must not affect them."""
    assert classify_interpreter(UV_TOOL) is InterpreterClass.UV_TOOL
    assert classify_interpreter(PIPX) is InterpreterClass.PIPX


def test_classify_is_pure_no_env_read():
    """No env argument -> path-only classification (deterministic)."""
    assert classify_interpreter(WORKTREE_VENV, env=None) is InterpreterClass.DEV_VENV
    assert classify_interpreter(UV_TOOL, env={}) is InterpreterClass.UV_TOOL


def test_classify_uv_tool_dir_env_hint():
    """A non-default UV_TOOL_DIR (path without a literal 'uv/tools') is still
    recognized as durable when the env hint points at it."""
    interp = "/opt/uvtools/culture/bin/python"
    assert classify_interpreter(interp) is InterpreterClass.SYSTEM
    assert (
        classify_interpreter(interp, env={"UV_TOOL_DIR": "/opt/uvtools"})
        is InterpreterClass.UV_TOOL
    )


def test_classify_pipx_home_env_hint():
    # A 'venvs' dir with no 'pipx' marker before it is not recognized as pipx
    # by path alone -> SYSTEM. With the PIPX_HOME hint pointing at its parent it
    # is correctly recognized as a durable pipx venv.
    interp = "/custom/px/venvs/culture/bin/python"
    assert classify_interpreter(interp) is InterpreterClass.SYSTEM
    assert classify_interpreter(interp, env={"PIPX_HOME": "/custom/px"}) is InterpreterClass.PIPX


def test_classify_windows_style_paths():
    win_venv = r"C:\Users\x\repo\.venv\Scripts\python.exe"
    assert classify_interpreter(win_venv) is InterpreterClass.DEV_VENV
    win_uv = r"C:\Users\x\AppData\Roaming\uv\tools\culture\Scripts\python.exe"
    assert classify_interpreter(win_uv) is InterpreterClass.UV_TOOL


def test_classify_empty_is_system():
    assert classify_interpreter("") is InterpreterClass.SYSTEM


# --------------------------------------------------------------------------
# install_service guard: golden no-op, refuse, warn-and-proceed, legacy skip
# --------------------------------------------------------------------------


def _linux_stub(tmp_path):
    """Common patches so install_service writes into a tmp systemd dir."""
    unit_dir = tmp_path / "systemd" / "user"
    return unit_dir, [
        patch("culture_core.persistence.get_platform", return_value="linux"),
        patch("culture_core.persistence._systemd_user_dir", return_value=unit_dir),
        patch("culture_core.persistence._run_cmd"),
    ]


def test_guard_noop_byte_identical_for_installed_tool(tmp_path):
    """Golden: with a durable (uv-tool) interpreter, the guard is a pure no-op.
    Enforcing it (allow_dev_interpreter=False) yields a byte-identical unit to
    the legacy, unguarded call (allow_dev_interpreter=None)."""
    unit_dir = tmp_path / "systemd" / "user"
    cmd = [UV_TOOL, "-m", "culture_core", "server", "start"]
    with (
        patch("culture_core.persistence.get_platform", return_value="linux"),
        patch("culture_core.persistence._systemd_user_dir", return_value=unit_dir),
        patch("culture_core.persistence._run_cmd"),
    ):
        p_legacy = install_service("culture-server-spark", cmd, "d", allow_dev_interpreter=None)
        legacy = p_legacy.read_text()
        p_guarded = install_service("culture-server-spark", cmd, "d", allow_dev_interpreter=False)
        guarded = p_guarded.read_text()
    assert guarded == legacy
    assert UV_TOOL in guarded


def test_guard_refuses_dev_venv_without_override(tmp_path):
    unit_dir, stubs = _linux_stub(tmp_path)
    cmd = [WORKTREE_VENV, "-m", "culture_core", "server", "start"]
    with stubs[0], stubs[1], stubs[2], pytest.raises(CultureError) as exc:
        install_service("culture-server-spark", cmd, "d", allow_dev_interpreter=False)
    assert exc.value.code == EXIT_USER_ERROR
    assert WORKTREE_VENV in exc.value.message
    assert "--allow-dev-interpreter" in exc.value.remediation
    # Refusal happens before any file is written.
    assert not (unit_dir / "culture-server-spark.service").exists()


def test_guard_warns_and_proceeds_with_override(tmp_path, capsys):
    unit_dir, stubs = _linux_stub(tmp_path)
    cmd = [WORKTREE_VENV, "-m", "culture_core", "server", "start"]
    with stubs[0], stubs[1], stubs[2]:
        path = install_service("culture-server-spark", cmd, "d", allow_dev_interpreter=True)
    assert path.exists()
    err = capsys.readouterr().err
    assert WORKTREE_VENV in err  # the exact baked path is named
    assert "crash-loop" in err.lower()


def test_guard_skipped_when_none_legacy_callers(tmp_path):
    """allow_dev_interpreter=None (default) => guard never fires. Preserves the
    behavior of existing callers (e.g. bulk `mesh setup`) unchanged."""
    unit_dir, stubs = _linux_stub(tmp_path)
    cmd = [WORKTREE_VENV, "-m", "culture_core", "server", "start"]
    with stubs[0], stubs[1], stubs[2]:
        path = install_service("culture-server-spark", cmd, "d")  # default None
    assert path.exists()


# --------------------------------------------------------------------------
# Verb-level enforcement: server / agents / console
# --------------------------------------------------------------------------


def _write_mesh(tmp_path, name="spark"):
    from culture_core.mesh_config import (
        MeshConfig,
        MeshServerConfig,
        save_mesh_config,
    )

    mesh_yaml = tmp_path / "mesh.yaml"
    save_mesh_config(
        MeshConfig(server=MeshServerConfig(name=name, host="0.0.0.0", port=6667)), mesh_yaml
    )
    return mesh_yaml


def _write_manifest(tmp_path, name="spark", suffix="claude"):
    from culture_core.config import (
        ServerConfig,
        ServerConnConfig,
        save_server_config,
    )

    server_yaml = tmp_path / "server.yaml"
    workdir = tmp_path / "proj"
    workdir.mkdir(exist_ok=True)
    save_server_config(
        str(server_yaml),
        ServerConfig(server=ServerConnConfig(name=name), manifest={suffix: str(workdir)}),
    )
    return server_yaml


def test_server_install_refuses_dev_interpreter(tmp_path):
    from culture_core.cli.server import _server_install

    mesh_yaml = _write_mesh(tmp_path)
    unit_dir = tmp_path / "systemd" / "user"
    args = argparse.Namespace(config=str(mesh_yaml), allow_dev_interpreter=False)
    with (
        patch.object(sys, "executable", WORKTREE_VENV),
        patch("culture_core.persistence.get_platform", return_value="linux"),
        patch("culture_core.persistence._systemd_user_dir", return_value=unit_dir),
        patch("culture_core.persistence._run_cmd"),
        pytest.raises(CultureError) as exc,
    ):
        _server_install(args)
    assert WORKTREE_VENV in exc.value.message
    assert not list(unit_dir.glob("*.service"))


def test_server_install_proceeds_with_override(tmp_path):
    from culture_core.cli.server import _server_install

    mesh_yaml = _write_mesh(tmp_path)
    unit_dir = tmp_path / "systemd" / "user"
    args = argparse.Namespace(config=str(mesh_yaml), allow_dev_interpreter=True)
    with (
        patch.object(sys, "executable", WORKTREE_VENV),
        patch("culture_core.persistence.get_platform", return_value="linux"),
        patch("culture_core.persistence._systemd_user_dir", return_value=unit_dir),
        patch("culture_core.persistence._run_cmd"),
    ):
        _server_install(args)
    assert [u.name for u in unit_dir.glob("*.service")] == ["culture-server-spark.service"]


def test_server_install_durable_interpreter_needs_no_flag(tmp_path):
    from culture_core.cli.server import _server_install

    mesh_yaml = _write_mesh(tmp_path)
    unit_dir = tmp_path / "systemd" / "user"
    args = argparse.Namespace(config=str(mesh_yaml), allow_dev_interpreter=False)
    with (
        patch.object(sys, "executable", UV_TOOL),
        patch("culture_core.persistence.get_platform", return_value="linux"),
        patch("culture_core.persistence._systemd_user_dir", return_value=unit_dir),
        patch("culture_core.persistence._run_cmd"),
    ):
        _server_install(args)
    assert (unit_dir / "culture-server-spark.service").exists()


def test_agents_install_refuses_then_overrides(tmp_path):
    from culture_core.cli.agents import _cmd_install

    server_yaml = _write_manifest(tmp_path)
    unit_dir = tmp_path / "systemd" / "user"

    refuse_args = argparse.Namespace(
        config=str(server_yaml), nick="claude", allow_dev_interpreter=False
    )
    with (
        patch.object(sys, "executable", REPO_VENV),
        patch("culture_core.persistence.get_platform", return_value="linux"),
        patch("culture_core.persistence._systemd_user_dir", return_value=unit_dir),
        patch("culture_core.persistence._run_cmd"),
        pytest.raises(CultureError) as exc,
    ):
        _cmd_install(refuse_args)
    assert REPO_VENV in exc.value.message
    assert not list(unit_dir.glob("*.service"))

    ok_args = argparse.Namespace(config=str(server_yaml), nick="claude", allow_dev_interpreter=True)
    with (
        patch.object(sys, "executable", REPO_VENV),
        patch("culture_core.persistence.get_platform", return_value="linux"),
        patch("culture_core.persistence._systemd_user_dir", return_value=unit_dir),
        patch("culture_core.persistence._run_cmd"),
    ):
        _cmd_install(ok_args)
    assert (unit_dir / "culture-agent-spark-claude.service").exists()


def test_console_install_refuses_then_overrides(tmp_path):
    from culture_core.cli import console

    server_yaml = _write_manifest(tmp_path)  # provides server.yaml with name spark
    unit_dir = tmp_path / "systemd" / "user"

    with (
        patch.object(console, "DEFAULT_CONFIG", str(server_yaml)),
        patch.object(sys, "executable", REPO_VENV),
        patch("culture_core.persistence.get_platform", return_value="linux"),
        patch("culture_core.persistence._systemd_user_dir", return_value=unit_dir),
        patch("culture_core.persistence._run_cmd"),
        pytest.raises(CultureError) as exc,
    ):
        console.dispatch(argparse.Namespace(console_args=["install"]))
    assert REPO_VENV in exc.value.message
    assert not list(unit_dir.glob("*.service"))

    with (
        patch.object(console, "DEFAULT_CONFIG", str(server_yaml)),
        patch.object(sys, "executable", REPO_VENV),
        patch("culture_core.persistence.get_platform", return_value="linux"),
        patch("culture_core.persistence._systemd_user_dir", return_value=unit_dir),
        patch("culture_core.persistence._run_cmd"),
        pytest.raises(SystemExit) as sysexit,
    ):
        console.dispatch(argparse.Namespace(console_args=["install", "--allow-dev-interpreter"]))
    assert sysexit.value.code == 0
    assert (unit_dir / "culture-console-spark.service").exists()


# --------------------------------------------------------------------------
# argparse surface carries the override flag
# --------------------------------------------------------------------------


def test_server_install_parser_has_allow_dev_flag():
    from culture_core.cli import _build_parser

    p = _build_parser()
    assert p.parse_args(["server", "install", "--allow-dev-interpreter"]).allow_dev_interpreter
    assert p.parse_args(["server", "install"]).allow_dev_interpreter is False


def test_agents_install_parser_has_allow_dev_flag():
    from culture_core.cli import _build_parser

    p = _build_parser()
    assert p.parse_args(
        ["agents", "install", "claude", "--allow-dev-interpreter"]
    ).allow_dev_interpreter
    assert p.parse_args(["agents", "install", "claude"]).allow_dev_interpreter is False
