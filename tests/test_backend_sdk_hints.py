"""Missing-SDK remediation hints for the backend daemon factories.

The backend SDKs are optional extras since Phase C of #462 (``culture[claude]``,
``culture[acp]``, ``culture[copilot]``; codex needs none). On a slim install the
daemon factories in :mod:`culture_core.cli.agents` must fail fast with a
:class:`CultureError` whose remediation names the exact extra install command —
not a bare ``ModuleNotFoundError`` traceback. claude/acp fail at daemon import
(top-level SDK imports in cultureagent) while copilot only fails lazily at
session start, so the factories probe SDK availability explicitly and
symmetrically (all-backends rule).
"""

from __future__ import annotations

import importlib
import importlib.util
import sys

import pytest

from culture_core.cli import agents as agents_cli
from culture_core.cli._errors import EXIT_ENV_ERROR, CultureError


class _BlockRoots:
    """Meta-path finder that makes the given top-level module roots unimportable."""

    def __init__(self, roots):
        self.roots = set(roots)

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in self.roots:
            raise ModuleNotFoundError(f"No module named '{fullname}'", name=fullname)
        return None


def _block(monkeypatch, *roots):
    """Simulate a slim install: *roots* become unimportable, caches purged."""
    monkeypatch.setattr(sys, "meta_path", [_BlockRoots(roots)] + sys.meta_path)
    purge = set(roots) | {"cultureagent"}
    for mod in list(sys.modules):
        if mod.split(".")[0] in purge:
            monkeypatch.delitem(sys.modules, mod, raising=False)


@pytest.mark.parametrize(
    ("backend", "factory", "extra", "sdk_module"),
    [
        ("claude", agents_cli._create_claude_daemon, "claude", "claude_agent_sdk"),
        ("acp", agents_cli._create_acp_daemon, "acp", "claude_agent_sdk"),
        ("copilot", agents_cli._create_copilot_daemon, "copilot", "copilot"),
    ],
)
def test_missing_sdk_raises_remediation_hint(monkeypatch, backend, factory, extra, sdk_module):
    _block(monkeypatch, sdk_module)
    # The probe fires before config/agent are touched, so placeholders suffice.
    with pytest.raises(CultureError) as excinfo:
        factory(None, None)
    err = excinfo.value
    assert err.code == EXIT_ENV_ERROR
    assert backend in err.message
    assert sdk_module in err.message
    assert f"culture[{extra}]" in err.remediation


def test_missing_anthropic_also_hints_for_claude(monkeypatch):
    """The claude extra provides anthropic too — a half-installed env still hints."""
    _block(monkeypatch, "anthropic")
    with pytest.raises(CultureError) as excinfo:
        agents_cli._create_claude_daemon(None, None)
    assert "culture[claude]" in excinfo.value.remediation


def test_codex_needs_no_sdk(monkeypatch):
    """codex declares no SDK: the probe is a symmetric no-op and the daemon imports."""
    _block(monkeypatch, "claude_agent_sdk", "anthropic", "copilot")
    agents_cli._require_backend_sdk("codex")
    assert importlib.import_module("cultureagent.clients.codex.daemon")


def test_internal_module_failure_reraises_unchanged(monkeypatch):
    """A missing cultureagent-internal module is real breakage, not a missing extra."""

    class _BlockExact:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "cultureagent.clients.claude.daemon":
                raise ModuleNotFoundError(f"No module named '{fullname}'", name=fullname)
            return None

    monkeypatch.setattr(sys, "meta_path", [_BlockExact()] + sys.meta_path)
    monkeypatch.delitem(sys.modules, "cultureagent.clients.claude.daemon", raising=False)
    with pytest.raises(ModuleNotFoundError):
        agents_cli._create_claude_daemon(None, None)


def test_probe_passes_when_sdks_installed():
    """In the dev environment (SDKs via the dev group) the probes are quiet."""
    for backend in ("claude", "acp", "codex"):
        agents_cli._require_backend_sdk(backend)


def test_copilot_probe_matches_sdk_availability():
    """The copilot SDK ships only via its extra; the probe mirrors reality."""
    if importlib.util.find_spec("copilot") is not None:
        agents_cli._require_backend_sdk("copilot")
    else:
        with pytest.raises(CultureError) as excinfo:
            agents_cli._require_backend_sdk("copilot")
        assert "culture[copilot]" in excinfo.value.remediation
