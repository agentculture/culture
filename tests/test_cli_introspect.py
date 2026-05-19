"""Tests for the universal introspection verb dispatcher."""

import json
import subprocess
import sys

from culture import __version__
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


# --- JSON contract tests (issue #401) ------------------------------------


def _run_cli(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "culture", *argv],
        capture_output=True,
        text=True,
        check=False,
    )


def test_learn_json_emits_required_keys():
    """`culture learn --json` is what katvan's reference-sync invokes."""
    result = _run_cli("learn", "--json")
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["tool"] == "culture"
    assert payload["version"] == __version__
    assert payload["json_support"] is True
    assert payload["explain_pointer"] == "culture explain <path>"
    # Native nouns katvan should recurse into — passthroughs go in a separate key.
    assert set(payload["nouns"]) == {"agent", "server", "mesh", "channel", "bot", "skills"}
    assert {p["binary"] for p in payload["passthroughs"]} == {"agex", "afi", "irc-lens"}
    assert payload["verbs"] == ["explain", "overview", "learn"]
    assert set(payload["exit_codes"].keys()) >= {"0", "1", "2"}


def test_explain_root_json_has_path_and_nouns():
    """`culture explain --json` (no path) and `culture explain culture --json`
    return the root shape with an empty path."""
    for argv in (("explain", "--json"), ("explain", "culture", "--json")):
        result = _run_cli(*argv)
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        payload = json.loads(result.stdout)
        assert payload["path"] == []
        assert "agent" in payload["nouns"]
        assert "Culture" in payload["markdown"]


def test_explain_native_noun_json_has_verbs():
    """`culture explain <noun> --json` exposes the noun's verbs (what katvan
    recurses into)."""
    result = _run_cli("explain", "agent", "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["path"] == ["agent"]
    # Cross-check against the live argparse registration in agent.py.
    assert {"start", "stop", "status", "create"}.issubset(set(payload["verbs"]))
    assert "culture agent" in payload["markdown"]


def test_explain_noun_verb_json_has_argparse_markdown():
    """`culture explain <noun>/<verb> --json` exposes argparse-derived help."""
    result = _run_cli("explain", "agent/start", "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["path"] == ["agent", "start"]
    assert payload["markdown"].startswith("usage:")
    assert "culture agent start" in payload["markdown"]


def test_explain_passthrough_noun_json_does_not_list_verbs():
    """Passthrough nouns surface `passthrough_to` and no `verbs` key — katvan
    won't recurse (they're listed under `passthroughs`, not `nouns`)."""
    result = _run_cli("explain", "devex", "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["path"] == ["devex"]
    assert payload["passthrough_to"] == "agex"
    assert "verbs" not in payload


def test_explain_bogus_topic_json_emits_structured_error():
    """JSON-mode errors go to stderr only with the {code, message, remediation}
    shape; stdout stays empty so JSON parsers don't choke on mixed output."""
    result = _run_cli("explain", "nope-noun-xyz", "--json")
    assert result.returncode == 1
    assert result.stdout == ""
    err = json.loads(result.stderr)
    assert err["code"] == 1
    assert "nope-noun-xyz" in err["message"]
    assert err["remediation"]


def test_overview_json_has_path_and_nouns():
    """Overview keeps the same shape minus `verbs` so the three universal
    verbs stay symmetric."""
    result = _run_cli("overview", "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["path"] == []
    assert "agent" in payload["nouns"]


def test_stdout_and_stderr_never_mixed_on_success():
    """Every --json success path writes exactly to stdout, nothing to stderr."""
    for argv in (
        ("learn", "--json"),
        ("explain", "--json"),
        ("explain", "agent", "--json"),
        ("explain", "agent/start", "--json"),
        ("overview", "--json"),
    ):
        result = _run_cli(*argv)
        assert result.returncode == 0, (argv, result.stderr)
        json.loads(result.stdout)  # raises on malformed stdout
        assert result.stderr == "", (argv, result.stderr)


def test_katvan_pull_one_schema_match():
    """End-to-end: run the same call sequence katvan's pull does, and assert
    the schema is parseable at every step. This is the regression net
    against #401 ever reopening."""
    learn = json.loads(_run_cli("learn", "--json").stdout)
    assert isinstance(learn.get("nouns"), list) and learn["nouns"]
    for noun in learn["nouns"]:
        explain = json.loads(_run_cli("explain", noun, "--json").stdout)
        assert isinstance(explain.get("verbs"), list), noun
        for verb in explain["verbs"]:
            leaf = json.loads(_run_cli("explain", f"{noun}/{verb}", "--json").stdout)
            assert leaf["path"] == [noun, verb]
            assert leaf["markdown"]
