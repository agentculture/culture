"""Tests for culture.credentials — OS credential store helpers."""

from unittest.mock import patch

import pytest

from culture import credentials as cred_mod
from culture.credentials import (
    _run,
    delete_credential,
    lookup_credential,
    store_credential,
)


def test_run_missing_binary():
    """_run() returns (127, '') when the binary is not found."""
    rc, out = _run(["nonexistent-binary-xyz-12345"])
    assert rc == 127
    assert out == ""


def test_lookup_credential_missing_tool():
    """lookup_credential() returns None when the credential tool is missing."""
    with patch("culture.credentials._run", return_value=(127, "")):
        assert lookup_credential("some-peer") is None


def test_store_credential_missing_tool():
    """store_credential() returns False when the credential tool is missing."""
    with patch("culture.credentials._run", return_value=(127, "")):
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


# store_credential ----------------------------------------------------------


def test_store_credential_darwin_uses_security(monkeypatch):
    monkeypatch.setattr(cred_mod.sys, "platform", "darwin")
    captured: list = []
    monkeypatch.setattr(cred_mod.subprocess, "run", _captured_run(captured, returncode=0))

    assert store_credential("peerA", "secret") is True
    assert captured[0]["args"][0] == "security"
    assert "add-generic-password" in captured[0]["args"]
    # `-w` flag is present (password-by-arg path); the literal password
    # is intentionally NOT asserted here so future hardening (moving the
    # password to stdin or a secure-input API) doesn't break the test.
    assert "-w" in captured[0]["args"]


def test_store_credential_darwin_failure_returns_false(monkeypatch):
    monkeypatch.setattr(cred_mod.sys, "platform", "darwin")
    monkeypatch.setattr(cred_mod.subprocess, "run", _captured_run([], returncode=1))
    assert store_credential("peerA", "secret") is False


def test_store_credential_win32_invokes_powershell(monkeypatch):
    monkeypatch.setattr(cred_mod.sys, "platform", "win32")
    captured: list = []
    monkeypatch.setattr(cred_mod.subprocess, "run", _captured_run(captured, returncode=0))

    assert store_credential("peerB", "s3cret") is True
    assert captured[0]["args"][0] == "powershell"
    # The cmdlet name is the stable assertion; the literal password is
    # intentionally not pinned so a future move to a secure-input API
    # (Get-Credential / stdin) doesn't break the test.
    assert "New-StoredCredential" in captured[0]["args"][-1]


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
        _captured_run(captured, returncode=0, stdout="secret\n"),
    )

    assert lookup_credential("peerA") == "secret"
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


def test_delete_credential_darwin(monkeypatch):
    monkeypatch.setattr(cred_mod.sys, "platform", "darwin")
    captured: list = []
    monkeypatch.setattr(cred_mod.subprocess, "run", _captured_run(captured, returncode=0))

    assert delete_credential("peerA") is True
    assert "delete-generic-password" in captured[0]["args"]


def test_delete_credential_win32(monkeypatch):
    monkeypatch.setattr(cred_mod.sys, "platform", "win32")
    captured: list = []
    monkeypatch.setattr(cred_mod.subprocess, "run", _captured_run(captured, returncode=0))

    assert delete_credential("peerB") is True
    assert "Remove-StoredCredential" in captured[0]["args"][-1]


def test_delete_credential_linux(monkeypatch):
    monkeypatch.setattr(cred_mod.sys, "platform", "linux")
    captured: list = []
    monkeypatch.setattr(cred_mod.subprocess, "run", _captured_run(captured, returncode=0))

    assert delete_credential("peerC") is True
    assert "clear" in captured[0]["args"]
    assert captured[0]["args"][0] == "secret-tool"


def test_delete_credential_linux_failure_returns_false(monkeypatch):
    monkeypatch.setattr(cred_mod.sys, "platform", "linux")
    monkeypatch.setattr(cred_mod.subprocess, "run", _captured_run([], returncode=1))
    assert delete_credential("peerC") is False
