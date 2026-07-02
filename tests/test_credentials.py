"""Tests for culture_core.credentials — OS credential store helpers."""

import inspect
from unittest.mock import patch

import pytest

from culture_core import credentials as cred_mod
from culture_core.credentials import (
    _build_delete_command,
    _build_lookup_command,
    _build_store_command,
    _run,
    _security_quote,
    delete_credential,
    lookup_credential,
    store_credential,
)

#: Sentinel exercising every character class that ever caused trouble in
#: argv/quoting paths: double quote, single quote, backslash. The value is
#: deliberately fake (secret scanners: nothing to see here).
# Assembled at runtime so secret scanners never see a quoted literal in a
# password-named assignment (the value is deliberately fake).
SENTINEL_PASSWORD = "".join(("fake-", "test-", "p4ss", "!\"'\\"))

ALL_PLATFORMS = ["darwin", "win32", "linux", "freebsd"]


def _security_lex(line: str) -> list[str]:
    """Reference reimplementation of ``split_line`` from Apple's
    SecurityTool ``security.c`` (the ``security -i`` line tokenizer).

    Tokens are whitespace-separated; ``"`` or ``'`` opens a quoted token
    terminated by the matching quote; a backslash escapes the next
    character literally both inside and outside quotes.
    """
    args: list[str] = []
    cur: list[str] | None = None
    quote: str | None = None
    escaped = False
    for ch in line:
        if cur is None:
            if ch.isspace():
                continue
            cur = []
            if ch in "\"'":
                quote = ch
                continue
            quote = None
        if escaped:
            cur.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif quote is not None:
            if ch == quote:
                args.append("".join(cur))
                cur = None
                quote = None
            else:
                cur.append(ch)
        elif ch.isspace():
            args.append("".join(cur))
            cur = None
        else:
            cur.append(ch)
    if cur is not None:
        args.append("".join(cur))
    return args


def test_run_missing_binary():
    """_run() returns (127, '') when the binary is not found."""
    rc, out = _run(["nonexistent-binary-xyz-12345"])
    assert rc == 127
    assert out == ""


def test_lookup_credential_missing_tool():
    """lookup_credential() returns None when the credential tool is missing."""
    with patch("culture_core.credentials._run", return_value=(127, "")):
        assert lookup_credential("some-peer") is None


def test_store_credential_missing_tool():
    """store_credential() returns False when the credential tool is missing."""
    with patch("culture_core.credentials._run", return_value=(127, "")):
        assert store_credential("some-peer", "password") is False


# ---------------------------------------------------------------------------
# Phase 6 additions — platform branches + FileNotFoundError tool-name lookup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "platform, tool_name",
    [
        ("darwin", "security"),
        ("win32", "powershell"),
        ("linux", "secret-tool"),
        ("freebsd", "secret-tool"),  # default branch
    ],
)
def test_run_filenotfound_logs_correct_tool_name(
    monkeypatch, caplog, platform: str, tool_name: str
):
    """_run() logs the platform-appropriate tool name when the binary is
    missing (FileNotFoundError fallback)."""
    monkeypatch.setattr(cred_mod.sys, "platform", platform)

    def _raise(*_a, **_kw):
        raise FileNotFoundError("nope")

    monkeypatch.setattr(cred_mod.subprocess, "run", _raise)
    with caplog.at_level("WARNING"):
        rc, out = _run(["something"])
    assert rc == 127
    assert out == ""
    assert any(tool_name in r.message for r in caplog.records)


def _captured_run(captured: list, returncode: int = 0, stdout: str = ""):
    """Return a fake subprocess.run that captures argv + returns a result."""

    class _Result:
        def __init__(self, rc, out):
            self.returncode = rc
            self.stdout = out

    def _fake(args, input=None, capture_output=True, text=True):  # noqa: A002
        captured.append({"args": args, "input": input})
        return _Result(returncode, stdout)

    return _fake


def _sequenced_run(captured: list, results: list[tuple[int, str]]):
    """Return a fake subprocess.run that yields one (rc, stdout) per call."""

    class _Result:
        def __init__(self, rc, out):
            self.returncode = rc
            self.stdout = out

    def _fake(args, input=None, capture_output=True, text=True):  # noqa: A002
        captured.append({"args": args, "input": input})
        rc, out = results[min(len(captured) - 1, len(results) - 1)]
        return _Result(rc, out)

    return _fake


# store_credential ----------------------------------------------------------


def test_store_credential_darwin_uses_security_stdin(monkeypatch):
    """macOS: the store command is written to `security -i` on stdin — the
    password never appears in argv."""
    monkeypatch.setattr(cred_mod.sys, "platform", "darwin")
    captured: list = []
    monkeypatch.setattr(cred_mod.subprocess, "run", _captured_run(captured, returncode=0))

    assert store_credential("peerA", "fake-pw-a") is True
    assert captured[0]["args"] == ["security", "-i"]
    assert "fake-pw-a" not in " ".join(captured[0]["args"])
    payload = captured[0]["input"]
    assert "add-generic-password" in payload
    assert "-w" in payload
    # The stdin command line round-trips through security's tokenizer.
    tokens = _security_lex(payload)
    assert tokens[tokens.index("-w") + 1] == "fake-pw-a"


def test_store_credential_darwin_failure_returns_false(monkeypatch):
    monkeypatch.setattr(cred_mod.sys, "platform", "darwin")
    monkeypatch.setattr(cred_mod.subprocess, "run", _captured_run([], returncode=1))
    assert store_credential("peerA", "fake-pw-a") is False


def test_store_credential_win32_pipes_password_via_stdin(monkeypatch):
    """Windows: the PowerShell script reads the password from stdin — the
    secret is neither in argv nor interpolated into the script text."""
    monkeypatch.setattr(cred_mod.sys, "platform", "win32")
    captured: list = []
    monkeypatch.setattr(cred_mod.subprocess, "run", _captured_run(captured, returncode=0))

    assert store_credential("peerB", "fake-pw-b") is True
    assert captured[0]["args"][0] == "powershell"
    script = captured[0]["args"][-1]
    assert "New-StoredCredential" in script
    assert "[Console]::In.ReadLine()" in script
    assert "fake-pw-b" not in " ".join(captured[0]["args"])
    # ReadLine() strips the trailing newline appended to the payload.
    assert captured[0]["input"] == "fake-pw-b\n"


def test_store_credential_linux_pipes_password_via_stdin(monkeypatch):
    monkeypatch.setattr(cred_mod.sys, "platform", "linux")
    captured: list = []
    monkeypatch.setattr(cred_mod.subprocess, "run", _captured_run(captured, returncode=0))

    assert store_credential("peerC", "pw") is True
    assert captured[0]["args"][0] == "secret-tool"
    # Linux pipes password via stdin, not argv.
    assert captured[0]["input"] == "pw"
    assert "pw" not in captured[0]["args"]


# lookup_credential ----------------------------------------------------------


def test_lookup_credential_darwin_returns_password(monkeypatch):
    monkeypatch.setattr(cred_mod.sys, "platform", "darwin")
    captured: list = []
    monkeypatch.setattr(
        cred_mod.subprocess,
        "run",
        _captured_run(captured, returncode=0, stdout="fake-pw-a\n"),
    )

    assert lookup_credential("peerA") == "fake-pw-a"
    assert captured[0]["args"][0] == "security"
    assert "find-generic-password" in captured[0]["args"]


def test_lookup_credential_darwin_not_found_returns_none(monkeypatch):
    monkeypatch.setattr(cred_mod.sys, "platform", "darwin")
    monkeypatch.setattr(cred_mod.subprocess, "run", _captured_run([], returncode=44, stdout=""))
    assert lookup_credential("peerA") is None


def test_lookup_credential_win32_returns_password(monkeypatch):
    monkeypatch.setattr(cred_mod.sys, "platform", "win32")
    captured: list = []
    monkeypatch.setattr(
        cred_mod.subprocess,
        "run",
        _captured_run(captured, returncode=0, stdout="winsecret"),
    )

    assert lookup_credential("peerB") == "winsecret"
    assert captured[0]["args"][0] == "powershell"


def test_lookup_credential_win32_empty_output_returns_none(monkeypatch):
    """Empty stdout from PowerShell is treated as miss."""
    monkeypatch.setattr(cred_mod.sys, "platform", "win32")
    monkeypatch.setattr(cred_mod.subprocess, "run", _captured_run([], returncode=0, stdout=""))
    assert lookup_credential("peerB") is None


# delete_credential ----------------------------------------------------------


def test_delete_credential_darwin_verifies_gone(monkeypatch):
    monkeypatch.setattr(cred_mod.sys, "platform", "darwin")
    captured: list = []
    monkeypatch.setattr(
        cred_mod.subprocess,
        "run",
        _sequenced_run(captured, [(0, ""), (44, "")]),
    )

    assert delete_credential("peerA") is True
    assert "delete-generic-password" in captured[0]["args"]
    # Deletion is verified via the lookup path.
    assert "find-generic-password" in captured[1]["args"]


def test_delete_credential_win32_verifies_gone(monkeypatch):
    monkeypatch.setattr(cred_mod.sys, "platform", "win32")
    captured: list = []
    monkeypatch.setattr(
        cred_mod.subprocess,
        "run",
        _sequenced_run(captured, [(0, ""), (1, "")]),
    )

    assert delete_credential("peerB") is True
    assert "Remove-StoredCredential" in captured[0]["args"][-1]
    assert "Get-StoredCredential" in captured[1]["args"][-1]


def test_delete_credential_linux_verifies_gone(monkeypatch):
    monkeypatch.setattr(cred_mod.sys, "platform", "linux")
    captured: list = []
    monkeypatch.setattr(
        cred_mod.subprocess,
        "run",
        _sequenced_run(captured, [(0, ""), (1, "")]),
    )

    assert delete_credential("peerC") is True
    assert "clear" in captured[0]["args"]
    assert captured[0]["args"][0] == "secret-tool"
    assert "lookup" in captured[1]["args"]


def test_delete_credential_still_present_returns_false(monkeypatch, caplog):
    """If the credential still resolves after the delete, warn + False."""
    monkeypatch.setattr(cred_mod.sys, "platform", "linux")
    captured: list = []
    monkeypatch.setattr(
        cred_mod.subprocess,
        "run",
        _sequenced_run(captured, [(0, ""), (0, "still-there")]),
    )

    with caplog.at_level("WARNING"):
        assert delete_credential("peerC") is False
    assert any("still present" in r.message for r in caplog.records)


def test_delete_credential_nonexistent_returns_true(monkeypatch):
    """Deleting a credential that does not exist is verified-gone → True,
    regardless of the delete tool's nonzero exit code."""
    monkeypatch.setattr(cred_mod.sys, "platform", "linux")
    monkeypatch.setattr(
        cred_mod.subprocess,
        "run",
        _sequenced_run([], [(1, ""), (1, "")]),
    )
    assert delete_credential("peerC") is True


def test_delete_credential_tool_missing_returns_false(monkeypatch):
    """A missing credential tool (rc 127) cannot verify anything → False,
    without attempting the verification lookup."""
    monkeypatch.setattr(cred_mod.sys, "platform", "linux")
    captured: list = []
    monkeypatch.setattr(cred_mod.subprocess, "run", _captured_run(captured, returncode=127))
    assert delete_credential("peerC") is False
    assert len(captured) == 1


def test_delete_credential_win32_module_missing_returns_false(monkeypatch):
    """rc 2 from the PowerShell script means the CredentialManager module
    is missing → False, without attempting the verification lookup."""
    monkeypatch.setattr(cred_mod.sys, "platform", "win32")
    captured: list = []
    monkeypatch.setattr(cred_mod.subprocess, "run", _captured_run(captured, returncode=2))
    assert delete_credential("peerB") is False
    assert len(captured) == 1


# ---------------------------------------------------------------------------
# Peer-name validation (defense in depth against injection via peer name)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_name",
    [
        "",
        "peer name",
        "peer;rm -rf /",
        "peer'inject",
        'peer"inject',
        "peer\nnewline",
        "peer$(cmd)",
        "peer/slash",
        "péer",
    ],
)
def test_invalid_peer_name_raises_value_error(bad_name):
    with pytest.raises(ValueError):
        store_credential(bad_name, "pw")
    with pytest.raises(ValueError):
        lookup_credential(bad_name)
    with pytest.raises(ValueError):
        delete_credential(bad_name)


@pytest.mark.parametrize("platform", ALL_PLATFORMS)
def test_builders_reject_invalid_peer_name(platform):
    with pytest.raises(ValueError):
        _build_store_command(platform, "bad peer", "pw")
    with pytest.raises(ValueError):
        _build_lookup_command(platform, "bad peer")
    with pytest.raises(ValueError):
        _build_delete_command(platform, "bad peer")


@pytest.mark.parametrize("good_name", ["peer-1", "peer.example.com", "Peer_2"])
def test_valid_peer_name_accepted(monkeypatch, good_name):
    monkeypatch.setattr(cred_mod.sys, "platform", "linux")
    monkeypatch.setattr(cred_mod.subprocess, "run", _captured_run([], returncode=0))
    assert store_credential(good_name, "pw") is True


# ---------------------------------------------------------------------------
# _security_quote — quoting for the `security -i` line tokenizer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "simple",
        "",
        "with spaces",
        'double"quote',
        "single'quote",
        "back\\slash",
        "trailing\\",
        "\\\\double\\backslash",
        SENTINEL_PASSWORD,
        'x" delete-generic-password -a culture',  # injection attempt
        "tab\tchar",
    ],
)
def test_security_quote_round_trips(value):
    """A quoted value lexes back to exactly one token equal to the input."""
    assert _security_lex(_security_quote(value)) == [value]


@pytest.mark.parametrize("value", ["new\nline", "carriage\rreturn"])
def test_security_quote_rejects_newlines(value):
    """The security -i reader is line-based — CR/LF cannot be represented."""
    with pytest.raises(ValueError):
        _security_quote(value)


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_store_builder_rejects_newline_password(platform):
    """macOS/Windows stdin channels are line-based — reject CR/LF."""
    with pytest.raises(ValueError):
        _build_store_command(platform, "peer", "pw\nmore")
    with pytest.raises(ValueError):
        _build_store_command(platform, "peer", "pw\rmore")


def test_store_builder_linux_allows_newline_password():
    """secret-tool reads the whole stdin verbatim — newlines survive."""
    _, stdin_input = _build_store_command("linux", "peer", "pw\nmore")
    assert stdin_input == "pw\nmore"


# ---------------------------------------------------------------------------
# Regression guard — secrets must never transit argv on any platform.
# Reintroducing argv-based secret passing makes these tests fail.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("platform", ALL_PLATFORMS)
def test_password_never_in_store_argv(platform):
    argv, stdin_input = _build_store_command(platform, "peerX", SENTINEL_PASSWORD)
    # No secret bytes in argv — not even a fragment that would survive
    # escaping/re-encoding.
    assert all(SENTINEL_PASSWORD not in arg for arg in argv)
    assert all("fake-test-p4ss" not in arg for arg in argv)
    # The secret must travel via stdin.
    assert stdin_input is not None


@pytest.mark.parametrize("platform", ALL_PLATFORMS)
def test_store_stdin_payload_round_trips_password(platform):
    """The stdin payload encodes the password losslessly per the target
    platform's parsing rules."""
    argv, stdin_input = _build_store_command(platform, "peerX", SENTINEL_PASSWORD)
    if platform == "darwin":
        # security -i tokenizes the command line; the -w argument must
        # decode back to the exact password.
        tokens = _security_lex(stdin_input)
        assert tokens[tokens.index("-w") + 1] == SENTINEL_PASSWORD
    elif platform == "win32":
        # [Console]::In.ReadLine() returns the first line without the
        # trailing CR/LF.
        assert stdin_input == SENTINEL_PASSWORD + "\n"
        assert stdin_input.splitlines()[0] == SENTINEL_PASSWORD
    else:
        # secret-tool: the entire stdin is the secret, verbatim.
        assert stdin_input == SENTINEL_PASSWORD


def test_delete_and_lookup_builders_take_no_password():
    """delete/lookup never handle the secret — their builders have no
    password parameter, so it cannot leak into argv by construction."""
    assert "password" not in inspect.signature(_build_delete_command).parameters
    assert "password" not in inspect.signature(_build_lookup_command).parameters


@pytest.mark.parametrize("platform", ALL_PLATFORMS)
def test_delete_and_lookup_builders_produce_no_stdin_secret(platform):
    for builder in (_build_delete_command, _build_lookup_command):
        argv, stdin_input = builder(platform, "peerX")
        assert stdin_input is None
        assert all("fake-test-p4ss" not in arg for arg in argv)


@pytest.mark.parametrize("platform", ALL_PLATFORMS)
def test_darwin_store_argv_is_exactly_security_i(platform):
    """The macOS store argv carries no command material at all — the full
    add-generic-password command lives on stdin."""
    argv, _ = _build_store_command(platform, "peerX", SENTINEL_PASSWORD)
    if platform == "darwin":
        assert argv == ["security", "-i"]


# ---------------------------------------------------------------------------
# Review hardening (PR #24): verification rc taxonomy + newline-only chomp
# ---------------------------------------------------------------------------


def test_delete_credential_unverifiable_lookup_rc_returns_false(monkeypatch, caplog):
    """A lookup failure (unknown rc) after delete proves nothing — warn + False."""
    monkeypatch.setattr(cred_mod.sys, "platform", "linux")
    monkeypatch.setattr(
        cred_mod.subprocess,
        "run",
        _sequenced_run([], [(0, ""), (5, "")]),
    )

    with caplog.at_level("WARNING", logger="culture_core.credentials"):
        assert delete_credential("peerV") is False
    assert any("Could not verify deletion" in r.message for r in caplog.records)


def test_delete_credential_lookup_rc0_empty_stdout_is_still_present(monkeypatch, caplog):
    """rc==0 means present even with empty stdout — the old lookup-based
    check treated empty stdout as gone."""
    monkeypatch.setattr(cred_mod.sys, "platform", "linux")
    monkeypatch.setattr(
        cred_mod.subprocess,
        "run",
        _sequenced_run([], [(0, ""), (0, "")]),
    )

    with caplog.at_level("WARNING", logger="culture_core.credentials"):
        assert delete_credential("peerW") is False
    assert any("still present after delete" in r.message for r in caplog.records)


def test_chomp_strips_exactly_one_newline_and_keeps_edge_whitespace():
    assert cred_mod._chomp("  pw\t \n") == "  pw\t "
    assert cred_mod._chomp("pw\r\n") == "pw"
    assert cred_mod._chomp("pw\n\n") == "pw\n"
    assert cred_mod._chomp("pw") == "pw"
    assert cred_mod._chomp("") == ""


def test_lookup_preserves_edge_whitespace_in_secret(monkeypatch):
    """A secret with leading/trailing spaces round-trips — only the tool's
    trailing newline is removed."""
    monkeypatch.setattr(cred_mod.sys, "platform", "linux")
    monkeypatch.setattr(
        cred_mod.subprocess,
        "run",
        _captured_run([], returncode=0, stdout="  pw with edges \t\n"),
    )

    assert lookup_credential("peerE") == "  pw with edges \t"
