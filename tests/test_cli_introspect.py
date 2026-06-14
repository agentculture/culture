"""Tests for the universal introspection verb dispatcher."""

import json
import subprocess
import sys

from culture_core import __version__

# `culture learn --json` is served by culture_core.cli after the #454 cutover, so
# the payload version is the engine's version.
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
        "agents",
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
    for ns in ("agents", "server", "mesh", "channel", "bot", "skills"):
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
    for ns in ("agents", "server", "mesh", "channel", "bot", "skills"):
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
    assert set(payload["nouns"]) == {"agents", "server", "mesh", "channel", "bot", "skills"}
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
        assert "agents" in payload["nouns"]
        assert "Culture" in payload["markdown"]


def test_explain_native_noun_json_has_verbs():
    """`culture explain <noun> --json` exposes the noun's verbs (what katvan
    recurses into)."""
    result = _run_cli("explain", "agents", "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["path"] == ["agents"]
    # Cross-check against the live argparse registration in agents.py.
    assert {"start", "stop", "status", "create"}.issubset(set(payload["verbs"]))
    assert "culture agents" in payload["markdown"]


def test_explain_noun_verb_json_has_argparse_markdown():
    """`culture explain <noun>/<verb> --json` exposes argparse-derived help."""
    result = _run_cli("explain", "agents/start", "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["path"] == ["agents", "start"]
    assert payload["markdown"].startswith("usage:")
    assert "culture agents start" in payload["markdown"]


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
    assert "agents" in payload["nouns"]


def test_stdout_and_stderr_never_mixed_on_success():
    """Every --json success path writes exactly to stdout, nothing to stderr."""
    for argv in (
        ("learn", "--json"),
        ("explain", "--json"),
        ("explain", "agents", "--json"),
        ("explain", "agents/start", "--json"),
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


# --- In-process unit tests for the JSON payload helpers ----------------------
#
# The subprocess tests above are the integration contract. These in-process
# tests cover the same code paths but inside the test runner's process so
# coverage.py can see the branches — needed to keep the 90% project floor.


def test_split_path_normalises_topic_arg():
    assert introspect._split_path(None) == []
    assert introspect._split_path("") == []
    assert introspect._split_path("agent") == ["agent"]
    assert introspect._split_path("agent/start") == ["agent", "start"]
    assert introspect._split_path("//agent//start//") == ["agent", "start"]


def test_collect_verbs_returns_sorted_subcommands():
    verbs = introspect._collect_verbs("agents")
    assert verbs == sorted(verbs)
    assert "start" in verbs
    assert "stop" in verbs


def test_collect_verbs_empty_for_unknown_and_passthrough_nouns():
    assert introspect._collect_verbs("nope-unknown-noun") == []
    # devex registers via REMAINDER → no inner subparsers
    assert introspect._collect_verbs("devex") == []


def test_format_verb_help_returns_argparse_text():
    md = introspect._format_verb_help("agents", "start")
    assert md.startswith("usage:")
    assert "culture agents start" in md


def test_format_verb_help_raises_culture_error_for_unknown_noun():
    from culture.cli._errors import EXIT_USER_ERROR, CultureError

    try:
        introspect._format_verb_help("nope", "start")
    except CultureError as err:
        assert err.code == EXIT_USER_ERROR
        assert "nope" in err.message
    else:
        raise AssertionError("expected CultureError")


def test_format_verb_help_raises_culture_error_for_unknown_verb():
    from culture.cli._errors import CultureError

    try:
        introspect._format_verb_help("agents", "nope-verb-xyz")
    except CultureError as err:
        assert "nope-verb-xyz" in err.message
        assert "agent" in err.remediation
    else:
        raise AssertionError("expected CultureError")


def test_learn_root_payload_structure():
    payload = introspect._learn_root_payload()
    assert payload["tool"] == "culture"
    assert payload["json_support"] is True
    assert set(payload["nouns"]) == {"agents", "server", "mesh", "channel", "bot", "skills"}
    assert {p["binary"] for p in payload["passthroughs"]} == {"agex", "afi", "irc-lens"}


def test_explain_payload_branches():
    root = introspect._explain_payload([])
    assert root["path"] == []
    assert "agents" in root["nouns"]
    assert "Culture" in root["markdown"]
    # Culture alias resolves to root
    root_alias = introspect._explain_payload(["culture"])
    assert root_alias["path"] == []
    # Native noun
    agent = introspect._explain_payload(["agents"])
    assert agent["path"] == ["agents"]
    assert "start" in agent["verbs"]
    # Noun/verb leaf
    leaf = introspect._explain_payload(["agents", "start"])
    assert leaf["path"] == ["agents", "start"]
    assert leaf["markdown"].startswith("usage:")
    # Passthrough noun
    devex = introspect._explain_payload(["devex"])
    assert devex["passthrough_to"] == "agex"
    assert "verbs" not in devex


def test_explain_payload_unknown_noun_raises():
    from culture.cli._errors import CultureError

    try:
        introspect._explain_payload(["nope-unknown"])
    except CultureError as err:
        assert "nope-unknown" in err.message
    else:
        raise AssertionError("expected CultureError")


def test_explain_payload_too_deep_raises():
    from culture.cli._errors import CultureError

    try:
        introspect._explain_payload(["agents", "start", "extra"])
    except CultureError as err:
        assert "too deep" in err.message
    else:
        raise AssertionError("expected CultureError")


def test_overview_payload_root_and_drops_verbs():
    root = introspect._overview_payload([])
    assert root["path"] == []
    assert "agents" in root["nouns"]
    noun = introspect._overview_payload(["agents"])
    assert noun["path"] == ["agents"]
    assert "verbs" not in noun


def test_payload_for_unsupported_verb_raises():
    from culture.cli._errors import CultureError

    try:
        introspect._payload_for("bogus-verb", [])
    except CultureError as err:
        assert "bogus-verb" in err.message
    else:
        raise AssertionError("expected CultureError")


def test_payload_for_routes_to_each_verb():
    assert introspect._payload_for("learn", [])["tool"] == "culture"
    assert introspect._payload_for("explain", [])["path"] == []
    assert introspect._payload_for("overview", [])["path"] == []


def test_emit_result_json_to_stream():
    import io

    from culture.cli._output import emit_result

    buf = io.StringIO()
    emit_result({"a": 1}, json_mode=True, stream=buf)
    assert buf.getvalue() == '{"a": 1}\n'


def test_emit_result_text_to_stream_and_newline_handling():
    import io

    from culture.cli._output import emit_result

    buf = io.StringIO()
    emit_result("hi", json_mode=False, stream=buf)
    assert buf.getvalue() == "hi\n"
    buf2 = io.StringIO()
    emit_result("hi\n", json_mode=False, stream=buf2)
    assert buf2.getvalue() == "hi\n"


def test_emit_result_text_non_string_stringifies():
    import io

    from culture.cli._output import emit_result

    buf = io.StringIO()
    emit_result(42, json_mode=False, stream=buf)
    assert buf.getvalue() == "42\n"


def test_emit_error_json_and_text_modes():
    import io

    from culture.cli._errors import CultureError
    from culture.cli._output import emit_error

    err = CultureError(code=1, message="bad", remediation="fix it")
    buf_json = io.StringIO()
    emit_error(err, json_mode=True, stream=buf_json)
    payload = json.loads(buf_json.getvalue())
    assert payload == {"code": 1, "message": "bad", "remediation": "fix it"}

    buf_text = io.StringIO()
    emit_error(err, json_mode=False, stream=buf_text)
    assert buf_text.getvalue() == "error: bad\nhint: fix it\n"

    # No remediation → no hint line.
    buf_text2 = io.StringIO()
    emit_error(CultureError(2, "boom"), json_mode=False, stream=buf_text2)
    assert buf_text2.getvalue() == "error: boom\n"


def test_emit_diagnostic_to_stream():
    import io

    from culture.cli._output import emit_diagnostic

    buf = io.StringIO()
    emit_diagnostic("progress", stream=buf)
    assert buf.getvalue() == "progress\n"
    buf2 = io.StringIO()
    emit_diagnostic("progress\n", stream=buf2)
    assert buf2.getvalue() == "progress\n"


def test_culture_error_to_dict_round_trip():
    from culture.cli._errors import CultureError

    err = CultureError(1, "msg", "rem")
    assert err.to_dict() == {"code": 1, "message": "msg", "remediation": "rem"}
    err2 = CultureError(2, "msg")
    assert err2.to_dict() == {"code": 2, "message": "msg", "remediation": ""}


def test_dispatch_json_mode_success(capsys):
    """In-process exercise of dispatch() so coverage sees the JSON branch."""
    import argparse as _argparse

    args = _argparse.Namespace(command="learn", topic=None, json=True)
    introspect.dispatch(args)
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["tool"] == "culture"
    assert captured.err == ""


def test_dispatch_json_mode_error_exits_with_code(capsys):
    import argparse as _argparse

    args = _argparse.Namespace(command="explain", topic="nope-noun-zzz", json=True)
    try:
        introspect.dispatch(args)
    except SystemExit as exit_:
        assert exit_.code == 1
    else:
        raise AssertionError("expected SystemExit on dispatch error")
    captured = capsys.readouterr()
    assert captured.out == ""
    err = json.loads(captured.err)
    assert err["code"] == 1
    assert "nope-noun-zzz" in err["message"]


def test_dispatch_text_mode_success_preserved(capsys):
    import argparse as _argparse

    args = _argparse.Namespace(command="explain", topic=None, json=False)
    introspect.dispatch(args)
    captured = capsys.readouterr()
    assert "Culture" in captured.out
    assert captured.err == ""


def test_dispatch_text_mode_error_uses_emit_error(capsys):
    import argparse as _argparse

    args = _argparse.Namespace(command="explain", topic="nope-noun-text", json=False)
    try:
        introspect.dispatch(args)
    except SystemExit as exit_:
        assert exit_.code == 1
    else:
        raise AssertionError("expected SystemExit on dispatch error")
    captured = capsys.readouterr()
    assert captured.out == ""
    # Text-mode error format from emit_error.
    assert captured.err.startswith("error: ")
    assert "nope-noun-text" in captured.err
    assert "hint:" in captured.err


# --- Top-level argparse error contract under --json ---------------------------


def test_argparse_unknown_flag_in_json_mode_emits_structured_error():
    """`culture explain --bogus --json` must still emit JSON to stderr."""
    result = _run_cli("explain", "--bogus-flag-xyz", "--json")
    assert result.returncode != 0
    assert result.stdout == ""
    err = json.loads(result.stderr)
    assert err["code"] == 1
    assert "--bogus-flag-xyz" in err["message"]
    assert err["remediation"]


def test_argparse_unknown_flag_without_json_mode_uses_plain_text():
    """Regression guard: text mode keeps argparse's standard behavior."""
    result = _run_cli("explain", "--bogus-flag-xyz")
    assert result.returncode == 2  # argparse default
    assert "usage:" in result.stderr
    # No JSON trailer.
    try:
        json.loads(result.stderr)
    except json.JSONDecodeError:
        pass
    else:
        raise AssertionError("text-mode stderr should not be valid JSON")


def test_argparse_error_with_json_but_no_introspect_verb_uses_text():
    """`--json` is honored only when an introspect verb is also in argv. A
    top-level error with `--json` but no `explain`/`overview`/`learn` falls
    through to the plain-text path so non-introspect groups aren't pulled
    into the JSON contract by accident."""
    result = _run_cli("--bogus-top", "--json")
    assert result.returncode == 2  # argparse default
    assert "usage:" in result.stderr
