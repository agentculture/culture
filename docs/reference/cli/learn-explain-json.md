# `learn` / `explain` JSON contract

`culture learn`, `culture explain`, and `culture overview` accept `--json`
for the AgentCulture sibling JSON contract that katvan's nightly
`reference-sync.yml` cron consumes. Once enabled in the katvan registry
(`docs_mode: pull-reference`), the cron renders
`culture.dev/culture/reference/` automatically from the output of these
calls — no in-repo markdown to maintain.

Tracked by [#401](https://github.com/agentculture/culture/issues/401);
the same contract ships in `afi-cli`, `agtag`, `cultureagent`,
`code-lens-cli`, `irc-lens`, `auntiepypi`, `ghafi`, `antoine`.

## Stream split

**Stdout** carries the success payload. **Stderr** carries diagnostics
and structured errors. They are never mixed — a successful `--json` call
produces JSON on stdout and nothing on stderr; a failing one produces
nothing on stdout and a JSON error on stderr.

## `culture learn --json`

The single call katvan invokes per sibling. Listing of nouns, version,
purpose, and the exit-code policy.

```json
{
  "tool": "culture",
  "version": "12.2.0",
  "summary": "AI agent IRC mesh — server, agents, channels, federation.",
  "purpose": "Culture is the framework of agreements ...",
  "nouns": ["agent", "server", "mesh", "channel", "bot", "skills"],
  "passthroughs": [
    {"noun": "devex",   "binary": "agex"},
    {"noun": "afi",     "binary": "afi"},
    {"noun": "console", "binary": "irc-lens"}
  ],
  "verbs": ["explain", "overview", "learn"],
  "exit_codes": {
    "0": "success",
    "1": "user-input error",
    "2": "environment/setup error"
  },
  "json_support": true,
  "explain_pointer": "culture explain <path>"
}
```

- **`nouns`** is the only key katvan reads (it iterates them and calls
  `culture explain <noun> --json` for each). The rest is sibling
  convention.
- **`passthroughs`** flags nouns whose real reference lives in a
  separate binary (`devex` → `agex`, `afi` → `afi`, `console` →
  `irc-lens`). Katvan pulls those siblings directly from their own
  registry entries, so they're deliberately *not* listed under `nouns`
  to avoid double-coverage.
- **`version`** is read from `culture.__version__` (the single source
  of truth in `pyproject.toml`).

## `culture explain [path] --json`

A path is either empty, a single noun (e.g. `agent`), or a noun/verb
pair (e.g. `agent/start`). Katvan passes the path as one argv token.

### Root: `culture explain --json` or `culture explain culture --json`

```json
{
  "path": [],
  "nouns": ["agent", "server", "mesh", "channel", "bot", "skills"],
  "passthroughs": [{"noun": "devex", "binary": "agex"}, ...],
  "markdown": "# Culture\n\nCulture is ..."
}
```

### Native noun: `culture explain agent --json`

```json
{
  "path": ["agent"],
  "verbs": ["archive", "assign", "create", "delete", "join", "learn",
            "message", "migrate", "read", "register", "rename", "sleep",
            "start", "status", "stop", "unarchive", "unregister", "wake"],
  "markdown": "# culture agent\n\nManage AI agents on the mesh ..."
}
```

- **`verbs`** comes from live argparse introspection of the registered
  subparsers; it stays in sync as groups add or remove subcommands.
- **`markdown`** is the noun's curated explainer text (the same text
  the text-mode call returns).

### Native noun/verb: `culture explain agent/start --json`

```json
{
  "path": ["agent", "start"],
  "markdown": "usage: culture agent start [-h] [--all] [--config CONFIG] ..."
}
```

- **`markdown`** is the argparse-derived `--help` text for the leaf
  command, formatted by argparse's standard formatter (usage line +
  help string + flag list). Auto-generated — no per-verb doc to
  maintain.

### Passthrough noun: `culture explain devex --json`

```json
{
  "path": ["devex"],
  "passthrough_to": "agex",
  "markdown": "`culture devex` is a passthrough to `agex`. Pull its reference from that sibling's `learn --json` / `explain --json` output directly.\n"
}
```

No `verbs` key — katvan does not recurse into passthrough nouns. The
agent or human reader gets a pointer to the underlying binary.

## `culture overview [path] --json`

Symmetric with `explain`; identical shape minus the `verbs` list at
the noun level. Out of scope for katvan; included for parity across
the three universal verbs.

## Errors (JSON mode)

Every JSON-mode failure emits a structured error to **stderr only**
(stdout stays empty) and exits with the appropriate code:

```json
{
  "code": 1,
  "message": "unknown noun 'nope' for explain",
  "remediation": "run 'culture explain' to see the registry of nouns"
}
```

### Exit-code policy

| Code | Meaning |
|------|---------|
| `0` | success |
| `1` | user-input error (bad noun, bad path, malformed flag) |
| `2` | environment/setup error |
| `3+` | reserved |

This is the same policy `learn --json` advertises under its
`exit_codes` key.

## Example: simulating katvan's pull

```python
import json
import subprocess

binary = ["culture"]
learn = json.loads(subprocess.check_output(binary + ["learn", "--json"]))
for noun in learn["nouns"]:
    explain = json.loads(
        subprocess.check_output(binary + ["explain", noun, "--json"])
    )
    for verb in explain["verbs"]:
        leaf = json.loads(
            subprocess.check_output(binary + ["explain", f"{noun}/{verb}", "--json"])
        )
        assert leaf["path"] == [noun, verb]
```

This is the exact call sequence katvan's
`katvan/cli/_commands/pull.py::_pull_one()` runs — a regression net
against the contract drifting from what the consumer expects.
