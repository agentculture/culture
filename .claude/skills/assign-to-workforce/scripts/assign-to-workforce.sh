#!/usr/bin/env bash
# assign-to-workforce.sh — fan out devague plan waves to parallel agents.
#
# The skill is named `assign-to-workforce`; it reads `devague plan waves --json`
# — the enriched payload (devague#53 t9: {"plan", "waves", "tasks"}, the latter
# keyed by task id with summary/instruction/acceptance_criteria/covers) — and
# renders the implementation split plan: a wave listing (each task id carries
# an `[instruction: yes|no, accept: N]` marker, verbatim from the payload) plus
# a single four-column per-task table — Wave, Task, Model, Task summary (#69)
# — and a go/no-go prompt for the human. The Model cell is a presentation-only
# default (`sonnet`); the devague CLI stays model-agnostic (#20) and the human
# is expected to edit it to a real model token (and harness, when it matters)
# before approving. The actual fan-out (worktree creation, spawning, TDD-gated
# merges) is performed by the operator/main agent once the human approves the
# split plan. Output ends with an End state section (devague#70 t6) — the
# verbatim output of `devague plan deliverables` — so the human can see what
# the plan actually produces before saying go; on an older devague lacking
# that verb the section degrades to a one-line hint instead of failing.
#
# The devague CLI is non-orchestrating (#20): `devague plan waves` describes
# the dependency graph; it does not spawn agents, manage worktrees, or pick
# a backend. This wrapper is the operator-facing helper.
#
# `split-plan --write` (issue #82, decision c25, artifact-only) additionally
# persists a durable gate-2 record to `docs/plans/<created-date>-<slug>-split.md`
# — the same per-task content as the stdout table/waves listing, an
# owner/model annotation block the script reads back on re-run (so a human's
# edited assignments survive a regeneration instead of being clobbered), and
# the End state section. Re-running overwrites the same dated path in place.
# This adds no plan schema change and no new CLI verb — the written file IS
# the record; `devague plan waves`/`show`/`deliverables` stay read-only.
#
# Origin: authored and maintained in agentculture/devague. guildmaster pulls this
# skill from here and broadcasts it to the rest of the AgentCulture mesh, so
# it is written to run anywhere — portable bash, no devague-checkout assumptions.
#
# Plans persist under .devague/ in the current directory, so run from the repo
# you are implementing.

set -euo pipefail

# ── resolve the devague CLI (mesh-first, then local-dev fallback) ───────────
DEVAGUE=()
resolve_devague() {
    if command -v devague >/dev/null 2>&1; then
        DEVAGUE=(devague)            # installed tool — the normal mesh case
        return 0
    fi
    # Local-dev fallback: inside the devague checkout, run via uv.
    local dir="$PWD"
    while [ -n "$dir" ] && [ "$dir" != "/" ]; do
        if [ -f "$dir/pyproject.toml" ] \
            && grep -q '^name = "devague"' "$dir/pyproject.toml" 2>/dev/null; then
            if command -v uv >/dev/null 2>&1; then
                DEVAGUE=(uv run devague)
                return 0
            fi
            break
        fi
        dir=$(dirname "$dir")
    done
    cat >&2 <<'EOF'
error: devague CLI not found.
hint: install it with `uv tool install devague` (or `pipx install devague`),
      or run from inside the devague checkout with `uv` available.
      https://github.com/agentculture/devague
EOF
    return 1
}

usage() {
    cat <<'EOF'
assign-to-workforce.sh — fan out devague plan waves to parallel agents.

Usage:
  assign-to-workforce.sh split-plan [--plan <slug>] [--write]
                                                       print the implementation split plan
  assign-to-workforce.sh waves      [--plan <slug>] [--json]   list dependency waves
  assign-to-workforce.sh help                         this help

Commands:
  split-plan   Read `devague plan waves --json` and render the human-facing
               implementation split plan: a wave listing (with per-task
               instruction/acceptance-count markers, verbatim from the
               payload) + a four-column Wave/Task/Model/Task-summary table
               (Model defaults to `sonnet`; edit it before approving) +
               go/no-go, ending with an End state section — the verbatim
               output of `devague plan deliverables` (degrades to a one-line
               hint on an older devague lacking that verb). Present this to
               the human before any fan-out; do not proceed without approval.

               --write   Also persist a durable gate-2 record to
                         `docs/plans/<created-date>-<slug>-split.md` (issue
                         #82, artifact-only): real per-task summaries,
                         instructions, and acceptance criteria; an
                         owner/model annotation table this script reads back
                         on the next `--write` run (so a human's edits
                         survive a regeneration); and the End state section.
                         Re-running overwrites the same dated path in place.
  waves        Forward `devague plan waves` (and any extra flags) verbatim.
               On a converged plan exits 0 and lists the dependency waves.

Plans persist under .devague/ in the current directory — run from the repo
you are implementing. Results go to stdout, diagnostics to stderr.

Human gates (three only):
  1. The exported spec (already closed by the /think leg).
  2. This implementation split plan (go/no-go to assign to workforce).
  3. The final PR (opened by the main agent via `cicd` / `agex pr open`).

The devague CLI is non-orchestrating (#20): `devague plan waves` describes
the graph; the operator performs the fan-out. One worktree per task; TDD
gates every merge (tests pass before AND after merge); no human per task.
EOF
}

# ── split-plan: render the implementation split plan for human review ────────
cmd_split_plan() {
    local extra_args=()
    local write_mode=0
    # Forward any --plan flag so waves/show/deliverables all target the right
    # plan; --write is a script-only flag and is never forwarded to devague.
    while [ $# -gt 0 ]; do
        case "$1" in
            --write)
                write_mode=1
                shift
                ;;
            *)
                extra_args+=("$1")
                shift
                ;;
        esac
    done

    local waves_json tmp_err waves_rc old_exit_trap
    # Clean up the temp file on any exit path — including a signal after its
    # creation — WITHOUT permanently changing the script's process-global EXIT
    # handling. Capture any prior EXIT trap BEFORE mktemp (that capture forks a
    # subshell, so doing it first keeps it out of the untracked-file window),
    # then install our cleanup trap on the line immediately after mktemp, and
    # restore the prior trap once the file is safely gone (#30; PR #31 review;
    # devague#32).
    old_exit_trap="$(trap -p EXIT)"
    tmp_err="$(mktemp)"
    trap 'rm -f "$tmp_err"' EXIT
    set +e
    waves_json="$("${DEVAGUE[@]}" plan waves --json "${extra_args[@]}" 2>"$tmp_err")"
    waves_rc=$?
    set -e
    local waves_err
    waves_err="$(cat "$tmp_err")"
    rm -f "$tmp_err"
    trap - EXIT
    eval "${old_exit_trap}"  # empty string is a no-op; re-installs a prior trap if any

    if [ "$waves_rc" -ne 0 ]; then
        printf '%s\n' "$waves_err" >&2
        return "$waves_rc"
    fi

    DEVAGUE_WAVES_JSON="$waves_json" python3 - <<'PY'
import json
import os
import sys

raw = os.environ.get("DEVAGUE_WAVES_JSON", "").strip()
if not raw:
    print("error: no waves output from devague plan waves", file=sys.stderr)
    print("hint: ensure a converged plan exists (devague plan converge)", file=sys.stderr)
    sys.exit(1)

try:
    data = json.loads(raw)
except json.JSONDecodeError as exc:
    print(f"error: could not parse waves JSON: {exc}", file=sys.stderr)
    sys.exit(1)

plan_slug = data.get("plan", "(unknown)")
waves = data.get("waves") or []
# The enriched payload (devague#53 t9): every active task's full working
# contract, keyed by id — summary, instruction, acceptance criteria, covers.
tasks_meta = data.get("tasks") or {}

# Presentation-only default (issue #69): the devague CLI itself stays
# model-agnostic (#20) — this is just what the table proposes before a human
# edits it.
DEFAULT_MODEL = "sonnet"
MAX_SUMMARY_LEN = 72
ELLIPSIS = "..."


def marker(task_id):
    """`[instruction: yes|no, accept: N]`, verbatim from the payload (#53 t13,
    c12/h5) — no operator paraphrasing of the underlying summary/instruction."""
    meta = tasks_meta.get(task_id) or {}
    has_instruction = "yes" if meta.get("instruction") else "no"
    accept_count = len(meta.get("acceptance_criteria") or [])
    return f"{task_id} [instruction: {has_instruction}, accept: {accept_count}]"


def truncate(summary):
    if len(summary) <= MAX_SUMMARY_LEN:
        return summary
    # Reserve room for the ellipsis so the rendered width (ellipsis included)
    # never exceeds MAX_SUMMARY_LEN (issue #77). The trailing clamp keeps the
    # contract intact even in the degenerate MAX_SUMMARY_LEN <= len(ELLIPSIS)
    # case (PR #78 review), where the reserved slice would otherwise underflow.
    slice_len = max(0, MAX_SUMMARY_LEN - len(ELLIPSIS))
    return (summary[:slice_len] + ELLIPSIS)[:MAX_SUMMARY_LEN]


print(f"Implementation split plan — plan: {plan_slug}")
print()
print("Dependency waves (from `devague plan waves`):")
for i, wave in enumerate(waves, 1):
    markers = ", ".join(marker(task_id) for task_id in wave)
    print(f"  Wave {i}: [{markers}]")

print()
print("Task assignments (proposed — edit the Model column before approving):")
print()

headers = ("Wave", "Task", "Model", "Task summary")
rows = []
for i, wave in enumerate(waves, 1):
    for task_id in wave:
        meta = tasks_meta.get(task_id) or {}
        # Verbatim from the payload — no operator paraphrasing (#53 t13, c12/h5) —
        # never a placeholder unless the payload truly has no summary recorded.
        summary = meta.get("summary") or "(no summary recorded)"
        rows.append((str(i), task_id, DEFAULT_MODEL, truncate(summary)))

col_widths = [max(len(h), max((len(r[j]) for r in rows), default=0))
              for j, h in enumerate(headers)]

def row_str(cells):
    return "| " + " | ".join(c.ljust(w) for c, w in zip(cells, col_widths)) + " |"

sep = "| " + " | ".join("-" * w for w in col_widths) + " |"
print(row_str(headers))
print(sep)
for row in rows:
    print(row_str(row))

print()
print("Model column: edit each row to a real model token (e.g. haiku, sonnet,")
print("opus, fable), optionally qualified with the harness when it matters")
print("(e.g. colleague, codex) — the default above is a starting proposal, not")
print("a recommendation.")
print()
print("Go/no-go: review the table above, edit the Model column as needed, then")
print("confirm: \"Approved — assign to workforce\" or \"Edit first\".")
print()
print("Once approved, fan out wave by wave:")
print("  1. Create one git worktree per task in the wave, under this repo's own")
print("     root beside the repo dir: <parent>/.worktrees.<repo-name>/agent-<id>")
print("     (never a shared ../worktrees/, never inside the repo).")
print("  2. Spawn a task agent per worktree — its brief quotes `devague plan")
print("     waves --json`'s summary, instruction, and acceptance criteria for")
print("     that task id verbatim (no paraphrasing).")
print("  3. Await all tasks in the wave; then TDD-gate each merge (tests before + after).")
print("  4. Advance to the next wave.")
print("  5. Open the final PR (human gate 3) after all waves merge and tests pass.")
PY

    # ── End state: quote `devague plan deliverables` verbatim (#70 t6) ───────
    # Never composed freehand — it is the single synthesized "what do we have
    # in the end?" view (confirmed after-state claims, terminal tasks, and
    # surviving open items). Forward the same --plan args used for waves.
    # Degrades gracefully on an older devague that predates the verb: print
    # one hint line naming the minimum version and keep exiting 0 — this
    # section must never fail the script.
    local deliverables_out deliverables_rc
    set +e
    deliverables_out="$("${DEVAGUE[@]}" plan deliverables "${extra_args[@]}" 2>/dev/null)"
    deliverables_rc=$?
    set -e

    echo
    if [ "$deliverables_rc" -eq 0 ]; then
        echo 'End state (from `devague plan deliverables`):'
        echo
        printf '%s\n' "$deliverables_out"
    else
        echo 'hint: End state view requires devague >= 0.18.0 (devague plan deliverables)'
    fi

    # ── --write: persist a durable gate-2 split artifact (#82 asks 2+3) ──────
    # Artifact-only per decision c25: the written file IS the record — no plan
    # schema change, no new CLI verb. `devague plan show --json` is the only
    # extra read-only call (for the plan's `created`/`title`, to match the
    # date-prefixed filename convention `devague plan export` already uses).
    if [ "$write_mode" -eq 1 ]; then
        local show_json show_rc show_err show_tmp_err show_old_exit_trap
        # A successful `devague plan show --json` now also emits a one-line
        # `next: ...` stderr hint (next-leg-hints t1) — stderr must stay OFF
        # the captured JSON string (a plain `2>&1` merge would corrupt it), so
        # it is captured separately and only surfaced on a real failure,
        # mirroring the `plan waves --json` capture above — including that
        # capture's trap discipline: prior EXIT trap saved before mktemp,
        # cleanup installed on the line after it, prior trap restored once the
        # file is gone (PR #110 review).
        show_old_exit_trap="$(trap -p EXIT)"
        show_tmp_err="$(mktemp)"
        trap 'rm -f "$show_tmp_err"' EXIT
        set +e
        show_json="$("${DEVAGUE[@]}" plan show --json "${extra_args[@]}" 2>"$show_tmp_err")"
        show_rc=$?
        set -e
        show_err="$(cat "$show_tmp_err")"
        rm -f "$show_tmp_err"
        trap - EXIT
        eval "${show_old_exit_trap}"  # empty string is a no-op; re-installs a prior trap if any
        if [ "$show_rc" -ne 0 ]; then
            printf '%s\n' "$show_err" >&2
            return "$show_rc"
        fi

        DEVAGUE_WAVES_JSON="$waves_json" \
        DEVAGUE_SHOW_JSON="$show_json" \
        DEVAGUE_DELIVERABLES_OK="$([ "$deliverables_rc" -eq 0 ] && echo 1 || echo 0)" \
        DEVAGUE_DELIVERABLES_MD="$deliverables_out" \
        python3 - <<'PY'
import datetime
import json
import os
import re
import sys
from pathlib import Path

raw_waves = os.environ.get("DEVAGUE_WAVES_JSON", "").strip()
raw_show = os.environ.get("DEVAGUE_SHOW_JSON", "").strip()
deliverables_ok = os.environ.get("DEVAGUE_DELIVERABLES_OK") == "1"
deliverables_md = os.environ.get("DEVAGUE_DELIVERABLES_MD", "")

try:
    waves_data = json.loads(raw_waves)
    show_data = json.loads(raw_show)
except json.JSONDecodeError as exc:
    print(f"error: could not parse devague JSON for split artifact: {exc}", file=sys.stderr)
    sys.exit(1)

plan_slug = waves_data.get("plan", "(unknown)")
waves = waves_data.get("waves") or []
tasks_meta = waves_data.get("tasks") or {}
plan_title = show_data.get("title") or plan_slug
plan_created = show_data.get("created") or ""

# ── date prefix: mirrors devague.cli._paths.dated_name's rationale (the date
# comes from the object's persisted `created` stamp, never today's date, so
# re-running this script is idempotent — same path, not a dated duplicate).
# Re-implemented here rather than imported: this script stays portable and
# does not assume a devague checkout is importable (mesh-first resolution).
UNDATED_PREFIX = "0000-00-00"


def dated_prefix(created):
    date = (created or "")[:10]
    try:
        datetime.datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return UNDATED_PREFIX
    return date


out_dir = Path("docs/plans")
out_path = out_dir / f"{dated_prefix(plan_created)}-{plan_slug}-split.md"

# ── markdown-safety helpers — mirror devague/render/_md_safety.py exactly
# (duplicated, not imported, for the same portability reason as above): task
# summaries/instructions/acceptance criteria are free-form prose that can
# legitimately contain bare URLs (MD034) and — since this content also
# carries backtick-quoted CLI paths like `docs/plans/<created-date>-<slug>...`
# — angle-bracket placeholder tokens that trip MD033 if they ever land
# outside a code span. `backtick_bare_angle_brackets` is this script's own
# defensive addition (not in the upstream helper) since raw task/instruction
# text is interpolated here, unlike the CLI's own renderers.
_HEADING_TRAILING_PUNCT_RE = re.compile(r"\s*[.,;:!。，；：！]+$")
_BARE_URL_RE = re.compile(r"(?<!<)(?<!\]\()https?://[^\s<>()]+")
_URL_TRAILING_PUNCT = ".,;:!?'\""
_CODE_SPAN_RE = re.compile(r"`[^`]*`")
_ANGLE_TOKEN_RE = re.compile(r"<[^<>\s][^<>]*>")

# The `md_safe_text` half of the upstream helper. Task instructions routinely
# name underscore-bearing identifiers (`__init__.py`, `_build_parser`), which
# markdown reads as emphasis/strong (MD037, MD050) unless wrapped in a code
# span. Ported rather than imported for the same portability reason as above.
_IDENTIFIER_EXTENSIONS = "py|md|rst|json|ya?ml|toml|cfg|ini|sh|js|ts|rb|go"
_IDENTIFIER_RE = re.compile(rf"[A-Za-z0-9_]*_[A-Za-z0-9_]*(?:\.(?:{_IDENTIFIER_EXTENSIONS}))?")
_STRAY_CONTROL_CHAR_RE = re.compile(r"(?<!\\)([*\[\]])")
_STRAY_BACKTICK_RE = re.compile(r"(?<!\\)`")
_PROTECTED_RE = re.compile(r"`[^`]*`|<https?://[^\s<>]*>|https?://[^\s<>()]+")


def _strip_url_trailing_punct(url):
    trail = ""
    while url:
        ch = url[-1]
        if ch in _URL_TRAILING_PUNCT:
            trail = ch + trail
            url = url[:-1]
        elif ch == ")" and url.count("(") < url.count(")"):
            trail = ch + trail
            url = url[:-1]
        else:
            break
    return url, trail


def _autolink_segment(segment):
    def _wrap(match):
        url, trail = _strip_url_trailing_punct(match.group(0))
        return f"<{url}>{trail}"

    return _BARE_URL_RE.sub(_wrap, segment)


def _split_code_spans(text, transform):
    if not text:
        return text
    parts = []
    last = 0
    for m in _CODE_SPAN_RE.finditer(text):
        parts.append(transform(text[last : m.start()]))
        parts.append(m.group(0))  # code span: verbatim, never rewritten
        last = m.end()
    parts.append(transform(text[last:]))
    return "".join(parts)


def autolink_urls(text):
    if "https://" not in text and "http://" not in text:
        return text
    return _split_code_spans(text, _autolink_segment)


def backtick_bare_angle_brackets(text):
    """Defensively code-span any `<placeholder>`-shaped token not already
    inside a code span, so a stray angle-bracket placeholder in task prose
    can never render as raw HTML (MD033)."""
    if "<" not in text:
        return text
    return _split_code_spans(
        text, lambda seg: _ANGLE_TOKEN_RE.sub(lambda m: f"`{m.group(0)}`", seg)
    )


def heading_safe(text):
    return _HEADING_TRAILING_PUNCT_RE.sub("", autolink_urls(text))


def _escape_segment(segment):
    """Escape + wrap one non-code-span slice. Order matters for idempotence:
    stray backticks and control chars first, identifier wrapping last."""
    segment = _STRAY_BACKTICK_RE.sub(r"\\`", segment)
    segment = _STRAY_CONTROL_CHAR_RE.sub(r"\\\1", segment)
    return _IDENTIFIER_RE.sub(lambda m: f"`{m.group(0)}`", segment)


def md_safe_text(text):
    """Wrap underscore/dunder identifiers in code spans and backslash-escape
    the remaining stray control characters. Code spans and URLs pass through
    byte-for-byte; idempotent, so composing it with the helpers above in
    either order is safe."""
    if not text:
        return text
    parts = []
    last = 0
    for m in _PROTECTED_RE.finditer(text):
        parts.append(_escape_segment(text[last : m.start()]))
        parts.append(m.group(0))  # code span or URL: verbatim, never touched
        last = m.end()
    parts.append(_escape_segment(text[last:]))
    result = "".join(parts)
    if result.startswith("#"):
        result = "\\" + result
    return result


def safe_body(text):
    return md_safe_text(autolink_urls(backtick_bare_angle_brackets(text)))


def safe_heading(text):
    return heading_safe(md_safe_text(backtick_bare_angle_brackets(text)))


def parse_existing_assignments(path):
    """Read back the `## Task assignments` table from a prior run, keyed by
    task id -> (owner, model) — the annotation-block round-trip (#82 ask 2):
    a human's edited Owner/Model cells survive the next `--write` rather than
    being clobbered by a fresh default."""
    if not path.exists():
        return {}
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        start = lines.index("## Task assignments")
    except ValueError:
        return {}
    result = {}
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) != 3:
            continue
        if cells == ["Task", "Owner", "Model"]:
            continue
        if all(set(c) <= {"-"} for c in cells):
            continue  # the "| --- | --- | --- |" separator row
        task_id = cells[0].strip("`")
        result[task_id] = (cells[1], cells[2])
    return result


DEFAULT_MODEL = "sonnet"
task_order = [tid for wave in waves for tid in wave]
prior_assignments = parse_existing_assignments(out_path)

lines = []
lines.append(f"# Implementation Split Plan — {safe_heading(plan_title)}")
lines.append("")
lines.append(
    f"slug: `{plan_slug}` · generated by `assign-to-workforce.sh split-plan "
    "--write` from `devague plan waves --json` (issue #82). Re-running this "
    "command overwrites this file in place; edits made to the **Task "
    "assignments** table below survive a regeneration, matched by task id."
)
lines.append("")

lines.append("## Dependency waves")
lines.append("")
for i, wave in enumerate(waves, 1):
    ids = ", ".join(f"`{tid}`" for tid in wave)
    lines.append(f"- Wave {i}: {ids}")
lines.append("")

lines.append("## Task assignments")
lines.append("")
lines.append(
    "Edit the Owner/Model columns before approving gate 2 — the default "
    "Model is a presentation-only proposal (`sonnet`), never a "
    "recommendation. Re-running `split-plan --write` preserves your edits "
    "here, matched by task id."
)
lines.append("")
lines.append("| Task | Owner | Model |")
lines.append("| --- | --- | --- |")
for tid in task_order:
    owner, model = prior_assignments.get(tid, ("", DEFAULT_MODEL))
    lines.append(f"| `{tid}` | {owner} | {model} |")
lines.append("")

for i, wave in enumerate(waves, 1):
    lines.append(f"## Wave {i}")
    lines.append("")
    for tid in wave:
        meta = tasks_meta.get(tid) or {}
        summary = meta.get("summary") or "(no summary recorded)"
        lines.append(f"### {tid} — {safe_heading(summary)}")
        lines.append("")
        body = []
        instruction = meta.get("instruction") or ""
        if instruction:
            body.append(f"- instruction: {safe_body(instruction)}")
        covers = meta.get("covers") or []
        if covers:
            body.append(f"- covers: {', '.join(covers)}")
        accept = meta.get("acceptance_criteria") or []
        if accept:
            body.append("- acceptance:")
            body.extend(f"  - {safe_body(a)}" for a in accept)
        if body:
            lines.extend(body)
            lines.append("")

lines.append("## End state")
lines.append("")
if deliverables_ok and deliverables_md.strip():
    body_lines = deliverables_md.rstrip("\n").splitlines()
    if body_lines and body_lines[0].startswith("# "):
        body_lines = body_lines[1:]  # drop the render's own title (MD025)
    while body_lines and body_lines[0] == "":
        # Drop the blank line the title left behind too — our own "## End
        # state" heading above already supplied the one separating blank
        # (MD012 no-multiple-blanks).
        body_lines = body_lines[1:]
    for line in body_lines:
        if line.startswith("## "):
            lines.append("#" + line)  # demote so it nests under "## End state"
        else:
            lines.append(line)
    lines.append("")
else:
    lines.append(
        "hint: End state view requires devague >= 0.18.0 "
        "(`devague plan deliverables`)."
    )
    lines.append("")

content = "\n".join(lines).rstrip("\n") + "\n"
out_dir.mkdir(parents=True, exist_ok=True)
action = "updated" if out_path.exists() else "wrote"
out_path.write_text(content, encoding="utf-8")
print(f"{action} split artifact: {out_path}")
PY
    fi
}

main() {
    case "${1:-help}" in
        help | -h | --help)
            usage
            return 0
            ;;
        split-plan)
            shift
            resolve_devague
            cmd_split_plan "$@"
            ;;
        waves)
            shift
            resolve_devague
            exec "${DEVAGUE[@]}" plan waves "$@"
            ;;
        *)
            printf 'error: unknown subcommand: %s\n' "$1" >&2
            printf 'hint: run `assign-to-workforce.sh help` for usage\n' >&2
            return 1
            ;;
    esac
}

main "$@"
