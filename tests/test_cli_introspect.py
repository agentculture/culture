"""Tests for the universal introspection verb dispatcher."""

import subprocess
import sys

from culture.cli import introspect


def test_register_and_resolve_explain():
    introspect.register_topic("demo", explain=lambda _t: ("demo-explain", 0))
    try:
        stdout, code = introspect.explain("demo")
        assert stdout == "demo-explain"
        assert code == 0
    finally:
        introspect._clear_registry()  # test-only helper


def test_unknown_topic_exits_1_with_available_list():
    introspect._clear_registry()  # starts fresh with root handlers re-registered
    try:
        introspect.register_topic("alpha", explain=lambda _t: ("a", 0))
        stdout, code = introspect.explain("bogus")
        assert code == 1
        assert "bogus" in stdout
        assert "alpha" in stdout
        assert "culture" in stdout
    finally:
        introspect._clear_registry()


def test_default_topic_is_culture_when_registered():
    introspect.register_topic("culture", explain=lambda _t: ("root-ok", 0))
    try:
        stdout, code = introspect.explain(None)
        assert code == 0
        assert stdout == "root-ok"
    finally:
        introspect._clear_registry()


def test_verbs_have_independent_registries():
    introspect.register_topic("x", explain=lambda _t: ("e", 0))
    try:
        _, code = introspect.overview("x")
        assert code == 1  # no overview handler for x
    finally:
        introspect._clear_registry()


def test_root_explain_mentions_culture_and_namespaces():
    # Module import registers the root handler as a side effect
    stdout, code = introspect.explain(None)
    assert code == 0
    assert "culture" in stdout.lower()
    # All eight namespaces are listed.
    for ns in (
        "devex",
        "server",
        "agent",
        "mesh",
        "bot",
        "channel",
        "skills",
        "afi",
    ):
        assert ns in stdout
    # The six namespaces with explain handlers wired up directly in
    # introspect.py must not render with the "(coming soon)" marker (#330).
    # `devex` and `afi` self-register via their own modules' import-time
    # _passthrough.register_topic() calls — those modules aren't imported
    # in this unit test, so we don't assert on them here.
    for ns in ("agent", "server", "mesh", "channel", "bot", "skills"):
        assert (
            f"`culture {ns}`  (coming soon)" not in stdout
        ), f"shipped namespace {ns!r} still rendered as (coming soon)"
    # `identity` and `secret` are not shipped namespaces and were dropped
    # from the listing in #330. If/when they ship they should be added
    # back together with their explain handlers.
    assert "identity" not in stdout
    assert "secret" not in stdout


def test_root_overview_is_nonempty():
    stdout, code = introspect.overview(None)
    assert code == 0
    assert stdout.strip()


def test_root_learn_uses_generate_learn_prompt():
    stdout, code = introspect.learn(None)
    assert code == 0
    # Markers from generate_learn_prompt()'s template
    assert "Culture" in stdout
    assert "Install Skills" in stdout


def test_culture_explain_cli_lists_namespaces():
    result = subprocess.run(
        [sys.executable, "-m", "culture", "explain"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "culture" in result.stdout.lower()
    assert "devex" in result.stdout


def test_culture_overview_cli_runs():
    result = subprocess.run(
        [sys.executable, "-m", "culture", "overview"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def test_culture_learn_cli_runs():
    result = subprocess.run(
        [sys.executable, "-m", "culture", "learn"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Culture" in result.stdout


def test_culture_explain_unknown_topic_exits_1():
    result = subprocess.run(
        [sys.executable, "-m", "culture", "explain", "unknown-topic-xyz"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "unknown-topic-xyz" in result.stderr


def test_culture_explain_shipped_namespace_returns_real_content():
    # Each shipped namespace (#330) registers an explain handler so
    # `culture explain <ns>` returns a real description rather than the
    # legacy "coming soon" stub.
    for ns in ("agent", "server", "mesh", "channel", "bot", "skills"):
        result = subprocess.run(
            [sys.executable, "-m", "culture", "explain", ns],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert ns in result.stdout.lower()
        assert (
            "coming soon" not in result.stdout.lower()
        ), f"culture explain {ns} still says 'coming soon'"


def test_resolve_unit_coming_soon_for_namespace_without_handler():
    # Unit-level version of the above — exercises _resolve directly.
    introspect._clear_registry()
    try:
        stdout, code = introspect.explain("afi")
        assert code == 0
        assert "coming soon" in stdout.lower()
        assert "afi" in stdout
    finally:
        introspect._clear_registry()
