"""Tests for the all-backends parity CI guard (culture_core.devtools.backend_parity)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from culture_core.devtools.backend_parity import (
    AGENTS_CLI_PATH,
    BACKENDS,
    ESCAPE_HATCH_MARKER,
    check_parity,
    escape_hatch_justifications,
    evaluate_parity,
    factory_backends_changed,
    main,
    touched_backends,
)

# ---------------------------------------------------------------------------
# touched_backends
# ---------------------------------------------------------------------------


def test_backends_tuple_names_all_four():
    assert BACKENDS == ("claude", "codex", "copilot", "acp")


def test_touched_backends_maps_backend_paths():
    paths = [
        "culture_core/clients/claude/config.py",
        "culture_core/clients/acp/daemon_glue.py",
    ]
    assert touched_backends(paths) == {"claude", "acp"}


def test_touched_backends_shared_is_not_a_backend_touch():
    assert touched_backends(["culture_core/clients/shared/attention.py"]) == set()


def test_touched_backends_ignores_unrelated_paths():
    paths = [
        "culture_core/protocol/commands.py",
        "tests/test_backend_parity.py",
        "README.md",
        # Prefix must match a directory, not a file that merely starts with it.
        "culture_core/clients/claude_notes.md",
    ]
    assert touched_backends(paths) == set()


def test_touched_backends_normalizes_backslashes():
    assert touched_backends(["culture_core\\clients\\codex\\config.py"]) == {"codex"}


# ---------------------------------------------------------------------------
# factory_backends_changed (ast-based factory diffing)
# ---------------------------------------------------------------------------

FACTORY_TEMPLATE = '''\
"""Synthetic agents.py."""


def _make_backend_config(config, cls):
    return cls()


def _create_codex_daemon(config, agent):
    return "codex-{codex}"


def _create_acp_daemon(config, agent):
    return "acp-{acp}"


def _create_copilot_daemon(config, agent):
    return "copilot-{copilot}"


def _create_claude_daemon(config, agent):
    return "claude-{claude}"
'''


def _agents_source(claude="v1", codex="v1", copilot="v1", acp="v1"):
    return FACTORY_TEMPLATE.format(claude=claude, codex=codex, copilot=copilot, acp=acp)


def test_factory_backends_changed_detects_single_factory_edit():
    base = _agents_source()
    head = _agents_source(claude="v2")
    assert factory_backends_changed(base, head) == {"claude"}


def test_factory_backends_changed_no_factory_edit():
    base = _agents_source()
    head = base.replace("Synthetic agents.py.", "Synthetic agents.py, reworded docstring.")
    assert factory_backends_changed(base, head) == set()


def test_factory_backends_changed_missing_base_counts_all_present_factories():
    head = _agents_source()
    assert factory_backends_changed(None, head) == set(BACKENDS)


def test_factory_backends_changed_removed_factory_counts():
    base = _agents_source()
    head = base.replace('def _create_acp_daemon(config, agent):\n    return "acp-v1"\n', "")
    assert factory_backends_changed(base, head) == {"acp"}


def test_factory_backends_changed_unparsable_source_is_all_or_nothing():
    # An unparsable side yields no factories, so every factory present on the
    # other side registers as changed — fail-closed rather than fail-open.
    assert factory_backends_changed("def broken(:", _agents_source()) == set(BACKENDS)


# ---------------------------------------------------------------------------
# escape_hatch_justifications
# ---------------------------------------------------------------------------


def test_escape_hatch_collects_added_line_justifications():
    diff = (
        "+++ b/culture_core/clients/claude/config.py\n"
        "+    foo = 1  # backend-specific: claude SDK exposes no session hook\n"
        " context = 2  # backend-specific: not an added line\n"
        "-    old = 3  # backend-specific: removed, does not count\n"
    )
    assert escape_hatch_justifications(diff) == ["claude SDK exposes no session hook"]


def test_escape_hatch_empty_reason_gets_placeholder_and_dedup():
    diff = (
        "+    a = 1  # backend-specific:\n"
        "+    b = 2  # backend-specific: same reason\n"
        "+    c = 3  # backend-specific: same reason\n"
    )
    assert escape_hatch_justifications(diff) == ["(no reason given)", "same reason"]


def test_escape_hatch_ignores_marker_free_diff():
    assert escape_hatch_justifications("+    plain = 1\n-    gone = 2\n") == []


# ---------------------------------------------------------------------------
# evaluate_parity (the decision + message)
# ---------------------------------------------------------------------------


def test_claude_only_change_fails_naming_missing_backends():
    result = evaluate_parity({"claude"}, [])
    assert not result.passed
    assert result.touched == ("claude",)
    assert result.missing == ("codex", "copilot", "acp")
    assert "FAIL" in result.message
    assert "Missing backends: codex, copilot, acp" in result.message


def test_all_four_backends_pass():
    result = evaluate_parity(set(BACKENDS), [])
    assert result.passed
    assert result.missing == ()
    assert "PASS" in result.message


def test_zero_backend_change_passes():
    result = evaluate_parity(set(), [])
    assert result.passed
    assert result.touched == ()
    assert "no backend-specific surface touched" in result.message


def test_partial_change_with_escape_hatch_passes_with_justification():
    result = evaluate_parity({"claude"}, ["claude SDK exposes no session hook"])
    assert result.passed
    assert result.justifications == ("claude SDK exposes no session hook",)
    assert "escape hatch" in result.message
    assert "claude SDK exposes no session hook" in result.message


def test_three_backend_change_fails_naming_the_fourth():
    result = evaluate_parity({"claude", "codex", "copilot"}, [])
    assert not result.passed
    assert result.missing == ("acp",)
    assert "Missing backends: acp" in result.message


# ---------------------------------------------------------------------------
# Integration: real throwaway git repo (no mocks)
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def parity_repo(tmp_path: Path) -> Path:
    """A git repo with all four backend dirs and a factory-bearing agents.py."""
    repo = tmp_path / "repo"
    for backend in BACKENDS:
        target = repo / "culture_core" / "clients" / backend
        target.mkdir(parents=True)
        (target / "config.py").write_text(f"BACKEND = {backend!r}\n")
    shared = repo / "culture_core" / "clients" / "shared"
    shared.mkdir()
    (shared / "helpers.py").write_text("SHARED = True\n")
    agents = repo / AGENTS_CLI_PATH
    agents.parent.mkdir(parents=True)
    agents.write_text(
        "\n".join(
            f'def _create_{backend}_daemon(config, agent):\n    return "{backend}"\n'
            for backend in BACKENDS
        )
    )
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def test_check_parity_claude_only_probe_fails_end_to_end(parity_repo: Path):
    config = parity_repo / "culture_core" / "clients" / "claude" / "config.py"
    config.write_text(config.read_text() + "NEW_FEATURE = True\n")
    _git(parity_repo, "commit", "-qam", "claude-only feature")

    result = check_parity("HEAD~1", "HEAD", cwd=parity_repo)
    assert not result.passed
    assert result.touched == ("claude",)
    assert "Missing backends: codex, copilot, acp" in result.message


def test_check_parity_escape_hatch_passes_with_visible_justification(parity_repo: Path):
    config = parity_repo / "culture_core" / "clients" / "claude" / "config.py"
    config.write_text(
        config.read_text()
        + f"SESSION_HOOK = None  {ESCAPE_HATCH_MARKER} claude SDK only exposes this\n"
    )
    _git(parity_repo, "commit", "-qam", "claude-only, justified")

    result = check_parity("HEAD~1", "HEAD", cwd=parity_repo)
    assert result.passed
    assert result.justifications == ("claude SDK only exposes this",)
    assert "claude SDK only exposes this" in result.message


def test_check_parity_factory_edit_counts_as_backend_touch(parity_repo: Path):
    agents = parity_repo / AGENTS_CLI_PATH
    agents.write_text(agents.read_text().replace('return "claude"', 'return "claude-wrapped"'))
    _git(parity_repo, "commit", "-qam", "claude factory only")

    result = check_parity("HEAD~1", "HEAD", cwd=parity_repo)
    assert not result.passed
    assert result.touched == ("claude",)
    assert "Missing backends: codex, copilot, acp" in result.message


def test_check_parity_all_backends_and_shared_pass(parity_repo: Path):
    for backend in BACKENDS:
        config = parity_repo / "culture_core" / "clients" / backend / "config.py"
        config.write_text(config.read_text() + "NEW_FEATURE = True\n")
    shared = parity_repo / "culture_core" / "clients" / "shared" / "helpers.py"
    shared.write_text(shared.read_text() + "NEW_SHARED = True\n")
    _git(parity_repo, "commit", "-qam", "all backends + shared")

    result = check_parity("HEAD~1", "HEAD", cwd=parity_repo)
    assert result.passed
    assert result.touched == BACKENDS


def test_check_parity_unrelated_change_passes(parity_repo: Path):
    (parity_repo / "README.md").write_text("docs only\n")
    _git(parity_repo, "add", "-A")
    _git(parity_repo, "commit", "-qm", "docs")

    result = check_parity("HEAD~1", "HEAD", cwd=parity_repo)
    assert result.passed
    assert result.touched == ()


def test_check_parity_agents_py_new_at_head(tmp_path: Path):
    # agents.py absent at base (``git show`` fails, tolerated) and introduced at
    # head with a claude-only factory — still a single-backend touch.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("base\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")

    agents = repo / AGENTS_CLI_PATH
    agents.parent.mkdir(parents=True)
    agents.write_text('def _create_claude_daemon(config, agent):\n    return "claude"\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add claude factory")

    result = check_parity("HEAD~1", "HEAD", cwd=repo)
    assert not result.passed
    assert result.touched == ("claude",)
    assert "Missing backends: codex, copilot, acp" in result.message


def test_check_parity_bad_ref_raises(parity_repo: Path):
    with pytest.raises(RuntimeError, match="git diff"):
        check_parity("no-such-ref", "HEAD", cwd=parity_repo)


def test_main_cli_exit_codes_and_output(parity_repo: Path, monkeypatch, capsys):
    config = parity_repo / "culture_core" / "clients" / "claude" / "config.py"
    config.write_text(config.read_text() + "NEW_FEATURE = True\n")
    _git(parity_repo, "commit", "-qam", "claude-only feature")
    monkeypatch.chdir(parity_repo)

    assert main(["--base", "HEAD~1", "--head", "HEAD"]) == 1
    assert "Missing backends: codex, copilot, acp" in capsys.readouterr().out

    assert main(["--base", "HEAD", "--head", "HEAD"]) == 0
    assert "PASS" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Ref validation — refs reach git argv; forbid option/junk injection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_ref",
    [
        "--upload-pack=/tmp/evil",
        "-C/etc",
        "ref with spaces",
        "ref;rm",
        "$(cmd)",
        "",
    ],
)
def test_check_parity_rejects_unsafe_refs(bad_ref):
    from culture_core.devtools.backend_parity import check_parity

    with pytest.raises(ValueError, match="unsafe git ref"):
        check_parity(bad_ref, "HEAD")
    with pytest.raises(ValueError, match="unsafe git ref"):
        check_parity("origin/main", bad_ref)


@pytest.mark.parametrize("good_ref", ["origin/main", "HEAD", "v1.2.3", "feature/x_y-z", "abc123"])
def test_validate_ref_accepts_normal_refs(good_ref):
    from culture_core.devtools.backend_parity import _validate_ref

    assert _validate_ref(good_ref) == good_ref


# ---------------------------------------------------------------------------
# Review hardening: docstring-only factory edits + string-literal markers
# ---------------------------------------------------------------------------

_FACTORY_TEMPLATE = """
def _create_claude_daemon(config, agent):
    {docstring}
    return AgentDaemon(_make_backend_config(config, ClaudeDaemonConfig), agent)
"""


def test_docstring_only_factory_edit_is_not_a_backend_touch():
    from culture_core.devtools.backend_parity import factory_backends_changed

    base = _FACTORY_TEMPLATE.format(docstring='"""Create the default Claude backend daemon."""')
    head = _FACTORY_TEMPLATE.format(docstring='"""Reworded docstring, same behavior."""')
    assert factory_backends_changed(base, head) == set()


def test_behavioral_factory_edit_is_a_backend_touch():
    from culture_core.devtools.backend_parity import factory_backends_changed

    base = _FACTORY_TEMPLATE.format(docstring='"""doc"""')
    head = base.replace("agent)", "agent, extra=True)")
    assert factory_backends_changed(base, head) == {"claude"}


def test_comment_only_factory_edit_is_not_a_backend_touch():
    from culture_core.devtools.backend_parity import factory_backends_changed

    base = _FACTORY_TEMPLATE.format(docstring='"""doc"""')
    head = base.replace(
        "    return AgentDaemon",
        "    # a clarifying comment\n    return AgentDaemon",
    )
    assert factory_backends_changed(base, head) == set()


def test_marker_inside_string_literal_does_not_open_escape_hatch():
    from culture_core.devtools.backend_parity import escape_hatch_justifications

    diff = '+MARKER = "# backend-specific: not a real comment"\n'
    assert escape_hatch_justifications(diff) == []


def test_marker_as_trailing_comment_opens_escape_hatch():
    from culture_core.devtools.backend_parity import escape_hatch_justifications

    diff = "+x = 1  # backend-specific: claude-only SDK knob\n"
    assert escape_hatch_justifications(diff) == ["claude-only SDK knob"]


def test_marker_as_standalone_comment_opens_escape_hatch():
    from culture_core.devtools.backend_parity import escape_hatch_justifications

    diff = "+# backend-specific: copilot token endpoint differs\n"
    assert escape_hatch_justifications(diff) == ["copilot token endpoint differs"]
