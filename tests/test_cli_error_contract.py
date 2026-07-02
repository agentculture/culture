"""#19 contract: every CLI failure carries a remediation hint.

Three layers:

1. AST guard — every ``CultureError(...)`` construction under
   ``culture_core/cli/`` passes a remediation argument, and a literal
   remediation is non-empty. Reintroducing a hint-less error fails here.
2. The central ``main()`` handler — a raised CultureError reaches the
   operator as ``error:`` + ``hint:`` on stderr with the error's exit code.
3. First-run — a missing *default* config file produces a one-time stderr
   note with the setup commands instead of silently defaulting.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import culture_core.cli as cli_pkg
from culture_core.config import ServerConfig, ServerConnConfig, save_server_config

CLI_DIR = pathlib.Path(cli_pkg.__file__).parent


# ---------------------------------------------------------------------------
# 1. AST guard: no CultureError without a concrete remediation
# ---------------------------------------------------------------------------


def _culture_error_calls():
    for py_path in sorted(CLI_DIR.rglob("*.py")):
        tree = ast.parse(py_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name == "CultureError":
                yield py_path, node


def test_every_culture_error_in_cli_has_nonempty_remediation():
    violations = []
    for py_path, node in _culture_error_calls():
        remediation = node.args[2] if len(node.args) >= 3 else None
        for kw in node.keywords:
            if kw.arg == "remediation":
                remediation = kw.value
        where = f"{py_path.relative_to(CLI_DIR.parent.parent)}:{node.lineno}"
        if remediation is None:
            violations.append(f"{where}: CultureError raised without a remediation")
        elif (
            isinstance(remediation, ast.Constant)
            and isinstance(remediation.value, str)
            and not remediation.value.strip()
        ):
            violations.append(f"{where}: CultureError raised with an empty remediation")
    assert not violations, "CLI errors must carry a remediation hint (#19):\n" + "\n".join(
        f"  {v}" for v in violations
    )


def test_ast_guard_sees_the_raise_sites():
    """The guard must actually be scanning something — introspect.py and
    agents.py both raise CultureError today."""
    files = {py_path.name for py_path, _ in _culture_error_calls()}
    assert "introspect.py" in files
    assert "agents.py" in files


# ---------------------------------------------------------------------------
# 2. Central handler: error: + hint: + exit code, end-to-end through main()
# ---------------------------------------------------------------------------


def _write_config(tmp_path):
    workdir = tmp_path / "proj"
    workdir.mkdir(exist_ok=True)
    (workdir / "culture.yaml").write_text("agents:\n  - suffix: claude\n    backend: claude\n")
    server_yaml = tmp_path / "server.yaml"
    config = ServerConfig(
        server=ServerConnConfig(name="spark"),
        manifest={"claude": str(workdir.resolve())},
    )
    save_server_config(str(server_yaml), config)
    return server_yaml


def test_agents_start_unknown_nick_lists_candidates_and_hints(tmp_path, monkeypatch, capsys):
    """Acceptance (#19): `culture agents start no-such-nick` exits non-zero
    and lists the configured nicks."""
    server_yaml = _write_config(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["culture", "agents", "start", "no-such-nick", "--config", str(server_yaml)],
    )

    with pytest.raises(SystemExit) as excinfo:
        cli_pkg.main()

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "error: Agent 'no-such-nick' not found in config" in err
    assert "spark-claude" in err, "the error must list configured nicks"
    assert "hint:" in err, "every CLI failure carries a remediation hint"


def test_archived_agent_error_carries_hint():
    from culture_core.cli._errors import CultureError
    from culture_core.cli.agents import _resolve_by_nick
    from culture_core.config import AgentConfig

    config = ServerConfig(
        server=ServerConnConfig(name="spark"),
        agents=[AgentConfig(suffix="claude", nick="spark-claude", archived=True)],
    )
    with pytest.raises(CultureError) as excinfo:
        _resolve_by_nick(config, "spark-claude")
    assert "unarchive first: culture agents unarchive spark-claude" in excinfo.value.remediation


# ---------------------------------------------------------------------------
# 3. First-run: missing default config says so once, with setup commands
# ---------------------------------------------------------------------------


def test_first_run_missing_default_config_notes_setup_commands(tmp_path, monkeypatch, capsys):
    """Acceptance (#19): the fresh-operator path (no config file) never
    fails or no-ops silently — `culture agents status` still works AND a
    stderr note names the setup commands."""
    import culture_core.cli.agents as agent_mod

    monkeypatch.setenv("HOME", str(tmp_path))
    # DEFAULT_CONFIG is baked at import from the real HOME; repoint it so
    # the handler reads the same (missing) location the notice checks.
    monkeypatch.setattr(agent_mod, "DEFAULT_CONFIG", str(tmp_path / ".culture" / "server.yaml"))
    monkeypatch.setattr("sys.argv", ["culture", "agents", "status"])

    cli_pkg.main()

    captured = capsys.readouterr()
    assert "no server config at" in captured.err
    assert "culture server start" in captured.err
    assert "culture agents create" in captured.err
    # The command itself still runs (no crash, no silent no-op).
    assert "No agents" in captured.out


def test_first_run_note_silent_with_explicit_config(tmp_path, monkeypatch, capsys):
    """An explicit --config that doesn't exist is the command's own error
    surface — the first-run note is only for the default location."""
    monkeypatch.setenv("HOME", str(tmp_path))
    server_yaml = _write_config(tmp_path)
    monkeypatch.setattr("sys.argv", ["culture", "agents", "status", "--config", str(server_yaml)])

    cli_pkg.main()

    assert "no server config" not in capsys.readouterr().err


def test_first_run_note_silent_for_introspection(tmp_path, monkeypatch, capsys):
    """Introspection verbs work configless — no nag."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["culture", "overview"])

    cli_pkg.main()

    assert "no server config" not in capsys.readouterr().err


def test_first_run_note_silent_when_config_exists(tmp_path, monkeypatch, capsys):
    import culture_core.cli.agents as agent_mod

    monkeypatch.setenv("HOME", str(tmp_path))
    culture_dir = tmp_path / ".culture"
    culture_dir.mkdir()
    config = ServerConfig(server=ServerConnConfig(name="spark"))
    save_server_config(str(culture_dir / "server.yaml"), config)
    monkeypatch.setattr(agent_mod, "DEFAULT_CONFIG", str(culture_dir / "server.yaml"))
    monkeypatch.setattr("sys.argv", ["culture", "agents", "status"])

    cli_pkg.main()

    assert "no server config" not in capsys.readouterr().err


def test_first_run_note_silent_with_equals_form_config(tmp_path, monkeypatch, capsys):
    """argparse accepts --config=PATH too — that is also an explicit config."""
    monkeypatch.setenv("HOME", str(tmp_path))
    server_yaml = _write_config(tmp_path)
    monkeypatch.setattr("sys.argv", ["culture", "agents", "status", f"--config={server_yaml}"])

    cli_pkg.main()

    assert "no server config" not in capsys.readouterr().err


def test_empty_dynamic_remediation_gets_runtime_backstop(monkeypatch, capsys):
    """A dynamically-computed remediation that ends up empty still reaches
    the operator with a usable hint (runtime backstop behind the AST guard)."""
    from culture_core.cli import agents as agent_mod
    from culture_core.cli._errors import CultureError

    def _boom(_args):
        raise CultureError(1, "boom", " ")

    monkeypatch.setattr(agent_mod, "dispatch", _boom)
    monkeypatch.setattr("sys.argv", ["culture", "agents", "status"])

    with pytest.raises(SystemExit) as excinfo:
        cli_pkg.main()

    assert excinfo.value.code == 1
    err_out = capsys.readouterr().err
    assert "error: boom" in err_out
    assert "hint: run 'culture --help'" in err_out
