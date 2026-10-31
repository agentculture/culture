# `culture secret`

`culture secret <verb>` is exactly `grant <verb>`. [grant](https://github.com/agentculture/grant)
is the mesh's secrets manager (PyPI package `grant`).
culture depends on it as `grant>=0.11,<0.12` and embeds it in-process through
the same passthrough plumbing as `culture afi` and `culture devex`
(`culture_core/cli/secret.py`, `culture_core/cli/_passthrough.py`): arguments
after `secret` are forwarded verbatim to `grant.cli.main`, so output,
refusals and exit codes are grant's own.

## Usage

```bash
culture secret set API_TOKEN --purpose "mesh bot token"   # prompts or reads the value
culture secret list                 # names only
culture secret show API_TOKEN       # metadata, never the value
culture secret get API_TOKEN        # prints the value
culture secret set VAULT_KEY --hidden
culture secret get VAULT_KEY        # refused: hidden secrets are never printed
culture secret run --help           # inject secrets into a subprocess
```

Secret names must match `^[A-Z_][A-Z0-9_]{0,63}$`. Run `culture secret --help`
for the full verb list (`doctor`, `learn`, `explain`, `overview`, `set`, `show`,
`get`, `env`, `run`, `generate`, `list`, `delete`).

## Where secrets live

grant keeps a per-OS-user store at `$HOME/.local/share/grant/secrets.json`.
Every agent that runs as the same OS user shares that store; segregating agents
needs per-agent Unix users (grant's admin mode, `sudo grant set --user <user>`).

## Introspection

`culture explain secret` shows grant's self-description (grant's `learn`
output), `culture overview secret` shows grant's overview, and `culture explain`
lists `secret` as a live namespace.

## Tests

`tests/test_cli_secret.py` points `HOME` at a temporary directory for every
subprocess, so the real grant store is never read.
