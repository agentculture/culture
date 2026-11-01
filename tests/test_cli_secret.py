"""Tests for `culture secret` -> grant passthrough (culture#506, t5).

Every subprocess runs with HOME pointed at a temp dir so the real grant
store ($HOME/.local/share/grant) is never read.
"""

import os
import subprocess
import sys

import pytest

from culture_core.cli import introspect


@pytest.fixture
def env(tmp_path):
    e = dict(os.environ)
    e["HOME"] = str(tmp_path)
    for k in ("XDG_DATA_HOME", "GRANT_HOME"):
        e.pop(k, None)
    return e


def _run(prog, args, env):
    cmd = (
        [sys.executable, "-m", "culture_core", "secret", *args]
        if prog == "culture"
        else [
            sys.executable,
            "-c",
            "import sys; from grant.cli import main; sys.exit(main(sys.argv[1:]))",
            *args,
        ]
    )
    return subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)


def test_dependency_pin_declared():
    import pathlib

    text = (pathlib.Path(__file__).parents[1] / "pyproject.toml").read_text()
    assert '"grant>=0.11,<0.12"' in text


def test_secret_list_matches_grant_list(env):
    for prog in ("grant", "culture"):
        assert _run(prog, ["set", "ALPHA", "v1"], env).returncode == 0
    assert _run("grant", ["set", "BETA", "v2"], env).returncode == 0
    expected = _run("grant", ["list"], env)
    got = _run("culture", ["list"], env)
    assert expected.returncode == 0
    assert got.returncode == 0
    assert "ALPHA" in expected.stdout
    assert "BETA" in expected.stdout
    assert got.stdout == expected.stdout


def test_secret_get_hidden_refused_like_grant(env):
    assert _run("grant", ["set", "HID", "topsecret", "--hidden"], env).returncode == 0
    expected = _run("grant", ["get", "HID"], env)
    got = _run("culture", ["get", "HID"], env)
    assert expected.returncode != 0
    assert got.returncode == expected.returncode
    assert got.stdout == expected.stdout
    assert got.stderr == expected.stderr
    assert "topsecret" not in got.stdout + got.stderr


def test_secret_get_visible_value(env):
    _run("culture", ["set", "VIS", "hello"], env)
    got = _run("culture", ["get", "VIS"], env)
    assert got.returncode == 0
    assert got.stdout.strip() == "hello"


def test_explain_secret_not_coming_soon(env):
    r = subprocess.run(
        [sys.executable, "-m", "culture_core", "explain", "secret"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip()
    assert "coming soon" not in r.stdout.lower()


def test_root_explain_lists_secret_without_marker(env):
    r = subprocess.run(
        [sys.executable, "-m", "culture_core", "explain"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert r.returncode == 0, r.stderr
    assert "`culture secret`" in r.stdout
    assert "`culture secret`  (coming soon)" not in r.stdout


def test_secret_is_a_passthrough_noun():
    assert introspect._PASSTHROUGHS["secret"] == "grant"


def test_no_shushu_left_in_readme_or_docs():
    import pathlib
    import re

    root = pathlib.Path(__file__).parents[1]
    allowed = {
        # historical records and the doctor example, per the t5 acceptance criterion
        "docs/doctor.md",
        "CHANGELOG.md",
    }
    offenders = []
    files = [root / "README.md", *sorted((root / "docs").rglob("*.md"))]
    for f in files:
        rel = f.relative_to(root).as_posix()
        if rel in allowed:
            continue
        # frozen design records (specs/plans) name shushu historically
        if rel.startswith(("docs/superpowers/", "docs/specs/", "docs/plans/")):
            continue
        if re.search("shushu", f.read_text(), re.IGNORECASE):
            offenders.append(rel)
    assert not offenders, offenders
