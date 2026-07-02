"""Engine slimness guards for culture#462 merge-back.

These tests keep the retired ``culture-core`` distribution from creeping back
into the engine tree.  The original motivation: pin drift between
``culture-core~=0.5.0`` and ``culture-core~=0.17.0`` across the two
distributions forced the 14.0.0 merge-back (#462).  Since then the engine
must stay SDK-free by construction — ``claude_agent_sdk`` and ``anthropic``
are optional extras, never hard dependencies.

## What the guards cover

1. AST/grep guard: no ``.py`` file under ``culture_core/`` imports
   ``claude_agent_sdk`` or ``anthropic`` (module-level or inside functions).
2. Slim-environment proxy: a meta-path import blocker makes the SDKs
   unimportable, then asserts ``import culture`` works, ``culture.__version__``
   resolves, and building the CLI parser succeeds (``_build_parser()``).
"""

import ast
import importlib
import pathlib
import sys
from types import ModuleType

import pytest

_REPO_ROOT = pathlib.Path(__file__).parent.parent
_CULTURE_CORE = _REPO_ROOT / "culture_core"

# SDKs that must NOT be hard dependencies of the engine.
_BLOCKED_SDKS = {"claude_agent_sdk", "anthropic"}


class _SdkImportBlocker:
    """Meta-path finder/loader that raises ImportError for blocked SDK names.

    Installed at the front of ``sys.meta_path`` to simulate a slim install
    where the optional SDK extras are absent.
    """

    def __init__(self, blocked: set[str]):
        self._blocked = blocked

    def find_spec(self, fullname, path=None, target=None):
        if fullname in self._blocked:
            raise ImportError(f"Blocked import: {fullname!r} is an optional extra, not installed.")
        # Also block sub-packages like ``claude_agent_sdk.foo``.
        for prefix in self._blocked:
            if fullname.startswith(prefix + "."):
                raise ImportError(
                    f"Blocked import: {fullname!r} is an optional extra, not installed."
                )
        return None


def _collect_py_files(directory: pathlib.Path) -> list[pathlib.Path]:
    """Return every ``.py`` file under *directory* recursively."""
    return sorted(directory.rglob("*.py"))


def _has_blocked_import(tree: ast.AST) -> str | None:
    """Return the first blocked import name found in *tree*, or ``None``."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _BLOCKED_SDKS:
                    return alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module in _BLOCKED_SDKS:
                return node.module
    return None


def test_no_sdk_imports_in_culture_core():
    """No .py file under culture_core/ may import claude_agent_sdk or anthropic.

    The engine must stay SDK-free by construction since the SDKs are optional
    extras.  A hard import would break slim installs and re-introduce the
    pin-drift that motivated the #462 merge-back.
    """
    py_files = _collect_py_files(_CULTURE_CORE)
    failures: list[str] = []
    for py_file in py_files:
        try:
            source = py_file.read_text()
            tree = ast.parse(source, filename=str(py_file))
            blocked = _has_blocked_import(tree)
            if blocked is not None:
                failures.append(f"{py_file.relative_to(_REPO_ROOT)} imports {blocked!r}")
        except SyntaxError:
            pass  # Skip unparseable files (should not exist).

    assert not failures, "Blocked SDK imports found in culture_core/:\n" + "\n".join(failures)


def test_slim_environment_imports(monkeypatch):
    """A slim install (SDKs unimportable) can import culture and build the CLI parser.

    Proves that --help-level CLI paths work without the optional SDK extras,
    keeping the engine usable in minimal environments.
    """
    blocker = _SdkImportBlocker(_BLOCKED_SDKS)
    monkeypatch.setattr(sys, "meta_path", [blocker, *sys.meta_path])

    # Remove any previously cached culture/culture_core modules so the
    # import runs fresh under the blocker.
    for mod_name in list(sys.modules):
        if mod_name.startswith("culture") or mod_name.startswith("agentirc"):
            monkeypatch.delitem(sys.modules, mod_name, raising=False)

    # (a) import culture works
    culture = importlib.import_module("culture")

    # (b) culture.__version__ resolves
    assert hasattr(culture, "__version__"), "culture.__version__ must resolve"
    assert isinstance(culture.__version__, str), "__version__ must be a string"

    # (c) building the CLI parser succeeds
    cli = importlib.import_module("culture_core.cli")
    parser = cli._build_parser()
    assert parser is not None, "_build_parser() must return a parser"
    assert hasattr(parser, "parse_args"), "parser must have parse_args"
