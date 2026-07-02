# Install & Extras

As part of Phase C of the merge-back (issue #462) the backend SDKs moved
from base dependencies to **optional extras**. The default install is slim — it ships
the full engine and CLI but **no** backend SDKs.

## Default (slim) install

```bash
uv tool install culture
# or
pip install culture
```

This gives you the `culture` CLI, the `culture_core` engine, and all
non-backend dependencies. You can run the mesh server, use `culture doctor`,
and operate the workspace — but starting an agent whose backend SDK is missing
will fail with a remediation hint naming the exact install command.

## Per-backend extras

Each backend has a named extra that pulls in the SDK it needs:

| Extra | Installs |
|-------|----------|
| `culture[claude]` | `cultureagent[backend-claude]` (anthropic + claude-agent-sdk) |
| `culture[acp]` | `cultureagent[backend-acp]` (same SDK set) |
| `culture[copilot]` | `cultureagent[backend-copilot]` + `github-copilot-sdk==0.2.0` |
| `culture[codex]` | *(empty — the codex backend needs no SDK, kept for symmetry)* |
| `culture[all-backends]` | everything (all four backends) |

Install with:

```bash
pip install culture[claude]
pip install culture[all-backends]
```

### Copilot SDK note

`github-copilot-sdk` is pinned to `0.2.0`. Version `0.2.3` fails a test and
hangs in teardown, and `1.0` is a breaking API change. Bump only alongside
backend code that adapts to the new release, and re-run `pytest -k copilot`.

## Missing SDK at runtime

When you start an agent whose backend SDK is not installed, the CLI fails
with a remediation hint naming the exact `pip install culture[<extra>]`
command you need.

## Dev dependency group

The `[dependency-groups] dev` section in `pyproject.toml` keeps the backend
SDKs so the test suite is unaffected — tests run against the real SDKs.

## Which install do I want?

| Scenario | Install |
|----------|---------|
| Operator running Claude agents | `culture[claude]` or `culture[all-backends]` |
| Mesh server only (no agents) | slim default |
| CI / test suite | dev dependency group (`uv sync --all-groups`) |
