"""CI guard enforcing the all-backends rule (claude/codex/copilot/acp parity).

A feature added to one agent backend must be propagated to all of them — a
feature in only one backend is a bug (see CLAUDE.md, and culture-core#9 for
the bug class this guards against: ``attention_overrides`` landed for some
backends while ``_create_claude_daemon`` alone kept crashing).

The guard inspects a PR's touched surface between two git refs:

- files under ``culture_core/clients/<backend>/`` map to that backend
  (``culture_core/clients/shared/`` is shared code, not a single-backend touch);
- for ``culture_core/cli/agents.py``, the source of each
  ``_create_<backend>_daemon`` factory is extracted at base and head (via
  ``ast``) and compared, so only factory-body changes count as backend touches.

It fails when a change touches at least one backend but fewer than all four,
naming the missing backends explicitly. Genuinely backend-specific code can
carry a justification marker on an added line::

    # backend-specific: <reason>

which makes the guard pass and surfaces the justification in its output.

CI entry point (exit 0/1)::

    python -m culture_core.devtools.backend_parity --base origin/main [--head HEAD]

The git interaction lives behind thin subprocess helpers; the decision logic
(:func:`touched_backends`, :func:`factory_backends_changed`,
:func:`escape_hatch_justifications`, :func:`evaluate_parity`) is pure and can
be exercised with synthetic inputs.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

BACKENDS = ("claude", "codex", "copilot", "acp")

CLIENTS_PREFIX = "culture_core/clients/"
AGENTS_CLI_PATH = "culture_core/cli/agents.py"

#: Justification marker honored as the escape hatch for genuinely
#: backend-specific code. Must appear on an *added* line of the diff.
ESCAPE_HATCH_MARKER = "# backend-specific:"

_FACTORY_NAME_RE = re.compile(r"^_create_(claude|codex|copilot|acp)_daemon$")


@dataclass(frozen=True)
class ParityResult:
    """Outcome of a backend-parity check between two refs."""

    touched: tuple[str, ...]
    missing: tuple[str, ...]
    justifications: tuple[str, ...]
    passed: bool
    message: str


def touched_backends(changed_paths: list[str]) -> set[str]:
    """Map changed file paths to the backends they touch.

    Paths under ``culture_core/clients/<backend>/`` count as a touch of that
    backend. ``culture_core/clients/shared/`` is shared code — deliberately
    NOT a single-backend touch.
    """
    touched: set[str] = set()
    for path in changed_paths:
        normalized = path.replace("\\", "/")
        for backend in BACKENDS:
            if normalized.startswith(f"{CLIENTS_PREFIX}{backend}/"):
                touched.add(backend)
    return touched


def _normalized_body(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """The factory's *behavioral* body: AST dump minus the leading docstring.

    Comparing the AST dump (without attributes → no line numbers) instead of
    raw source means docstring rewording, comment edits, and pure reformatting
    of one factory do not count as a backend touch — only code changes do.
    """
    body = node.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return ast.dump(ast.Module(body=list(body), type_ignores=[]), include_attributes=False)


def _factory_sources(source: str | None) -> dict[str, str]:
    """Extract each ``_create_<backend>_daemon`` factory's normalized body.

    Returns a mapping of backend name to :func:`_normalized_body` output. A
    missing or unparsable ``source`` yields an empty mapping.
    """
    if source is None:
        return {}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    factories: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        match = _FACTORY_NAME_RE.match(node.name)
        if match:
            factories[match.group(1)] = _normalized_body(node)
    return factories


def factory_backends_changed(base_source: str | None, head_source: str | None) -> set[str]:
    """Diff the daemon factories between two versions of ``cli/agents.py``.

    Compares each ``_create_<backend>_daemon`` function's normalized body
    (docstrings/comments/formatting excluded); a backend whose factory was
    added, removed, or behaviorally edited counts as touched.
    """
    base_factories = _factory_sources(base_source)
    head_factories = _factory_sources(head_source)
    changed: set[str] = set()
    for backend in BACKENDS:
        if base_factories.get(backend) != head_factories.get(backend):
            changed.add(backend)
    return changed


def _marker_comment_reason(code_line: str) -> str | None:
    """Return the marker's reason iff it appears in a real ``#`` comment.

    A marker inside a string literal must NOT open the escape hatch — the
    documented policy is a *comment* marker. Tokenizing a lone diff line can
    fail (it's a fragment of a larger statement), so fall back to a
    quote-parity scan: the ``#`` introducing the marker must sit outside any
    open single/double quote.
    """
    idx = code_line.find(ESCAPE_HATCH_MARKER)
    if idx == -1:
        return None
    prefix = code_line[:idx]
    in_single = in_double = False
    i = 0
    while i < len(prefix):
        ch = prefix[i]
        if ch == "\\" and (in_single or in_double):
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        i += 1
    if in_single or in_double:
        return None
    reason = code_line[idx + len(ESCAPE_HATCH_MARKER) :].strip()
    return reason or "(no reason given)"


def escape_hatch_justifications(diff_text: str) -> list[str]:
    """Collect ``# backend-specific: <reason>`` justifications from a diff.

    Only *added* lines count (``+`` prefix, excluding ``+++`` file headers) —
    a marker merely present in context or on a removed line does not open the
    escape hatch — and only when the marker is a real comment, not text
    inside a string literal (see :func:`_marker_comment_reason`). Duplicate
    justifications are collapsed, order preserved.
    """
    justifications: list[str] = []
    for line in diff_text.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        reason = _marker_comment_reason(line[1:])
        if reason is not None and reason not in justifications:
            justifications.append(reason)
    return justifications


def evaluate_parity(touched: set[str], justifications: list[str]) -> ParityResult:
    """Apply the all-backends rule to a set of touched backends.

    Fails iff at least one but fewer than all four backends were touched and
    no escape-hatch justification is present. The failure message names the
    missing backends explicitly; a pass via escape hatch surfaces the
    justifications.
    """
    touched_ordered = tuple(b for b in BACKENDS if b in touched)
    missing = tuple(b for b in BACKENDS if b not in touched)
    partial = 0 < len(touched_ordered) < len(BACKENDS)
    passed = not partial or bool(justifications)

    lines: list[str] = []
    if not touched_ordered:
        lines.append("Backend parity guard: PASS — no backend-specific surface touched.")
    elif not partial:
        lines.append(
            "Backend parity guard: PASS — all backends touched: " + ", ".join(BACKENDS) + "."
        )
    elif justifications:
        lines.append("Backend parity guard: PASS — backend-specific escape hatch honored.")
        lines.append("Touched backends: " + ", ".join(touched_ordered))
        lines.append("Untouched backends: " + ", ".join(missing))
        lines.append("Justifications (# backend-specific):")
        lines.extend(f"  - {reason}" for reason in justifications)
    else:
        lines.append(
            "Backend parity guard: FAIL — feature-shaped change does not reach all backends."
        )
        lines.append("Touched backends: " + ", ".join(touched_ordered))
        lines.append("Missing backends: " + ", ".join(missing))
        lines.append(
            "The all-backends rule (CLAUDE.md): a feature added to one backend "
            "(claude/codex/copilot/acp) must be propagated to all of them."
        )
        lines.append(
            "Propagate the change to the missing backends, or mark genuinely "
            "backend-specific added lines with '# backend-specific: <reason>'."
        )

    return ParityResult(
        touched=touched_ordered,
        missing=missing if partial else (),
        justifications=tuple(justifications),
        passed=passed,
        message="\n".join(lines),
    )


def _git(args: list[str], cwd: str | Path | None = None, check: bool = True) -> str | None:
    """Run a git command and return stdout, or ``None`` on failure with ``check=False``."""
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        if check:
            raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
        return None
    return proc.stdout


def _changed_paths(base_ref: str, head_ref: str, cwd: str | Path | None) -> list[str]:
    """List paths changed between the merge base of ``base_ref`` and ``head_ref``."""
    out = _git(["diff", "--name-only", f"{base_ref}...{head_ref}"], cwd=cwd)
    return [line for line in (out or "").splitlines() if line.strip()]


def _show_file(ref: str, path: str, cwd: str | Path | None) -> str | None:
    """Return a file's content at ``ref``, or ``None`` if absent at that ref."""
    return _git(["show", f"{ref}:{path}"], cwd=cwd, check=False)


def _guarded_diff(base_ref: str, head_ref: str, cwd: str | Path | None) -> str:
    """Unified diff limited to the guarded surface (backend dirs + agents.py)."""
    pathspecs = [f"{CLIENTS_PREFIX}{backend}" for backend in BACKENDS] + [AGENTS_CLI_PATH]
    out = _git(["diff", f"{base_ref}...{head_ref}", "--", *pathspecs], cwd=cwd)
    return out or ""


# Refs reach `git diff` / `git show` argv: restrict to ref/revision
# characters (including ~ and ^ for HEAD~1-style suffixes) and forbid a
# leading `-` so a crafted --base/--head can't smuggle a git option
# (argument injection) into the subprocess.
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./~^-]*$")


def _validate_ref(ref: str) -> str:
    """Return *ref* if it is a safe git revision spec, else raise ValueError."""
    if not _SAFE_REF_RE.match(ref):
        raise ValueError(
            f"unsafe git ref {ref!r}: must start with an alphanumeric and "
            "contain only [A-Za-z0-9_./~^-]"
        )
    return ref


def check_parity(
    base_ref: str,
    head_ref: str = "HEAD",
    cwd: str | Path | None = None,
) -> ParityResult:
    """Run the full parity check between two git refs.

    Combines path-based backend detection, factory-body diffing for
    ``culture_core/cli/agents.py``, and the escape-hatch scan, then evaluates
    the all-backends rule. Refs are validated before touching git — see
    :func:`_validate_ref`.
    """
    base_ref = _validate_ref(base_ref)
    head_ref = _validate_ref(head_ref)
    changed = _changed_paths(base_ref, head_ref, cwd)
    touched = touched_backends(changed)

    if AGENTS_CLI_PATH in changed:
        base_source = _show_file(base_ref, AGENTS_CLI_PATH, cwd)
        head_source = _show_file(head_ref, AGENTS_CLI_PATH, cwd)
        touched |= factory_backends_changed(base_source, head_source)

    justifications = escape_hatch_justifications(_guarded_diff(base_ref, head_ref, cwd))
    return evaluate_parity(touched, justifications)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: print the guard's verdict and exit 0 (pass) / 1 (fail)."""
    parser = argparse.ArgumentParser(
        prog="backend-parity",
        description="Enforce the all-backends rule (claude/codex/copilot/acp parity) "
        "over a git ref range.",
    )
    parser.add_argument("--base", required=True, help="Base ref (e.g. origin/main)")
    parser.add_argument("--head", default="HEAD", help="Head ref (default: HEAD)")
    args = parser.parse_args(argv)

    result = check_parity(args.base, args.head)
    print(result.message)
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
