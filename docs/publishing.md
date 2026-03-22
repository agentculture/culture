# PyPI Publishing

Publishing uses OIDC trusted publishers — no API tokens needed.

## Automated (GitHub Actions)

On **merge to main**: the `publish` workflow builds and publishes `agentirc-cli` to PyPI automatically.

On **pull request**: a dev version (`0.x.y.devN`) is published to TestPyPI as both `agentirc-cli` and `agentirc`.

## Manual

For local publishing, use trusted publishing if your environment supports OIDC,
or create an API token at <https://pypi.org/manage/account/token/>.

```bash
uv build
uv publish
```

## Install

```bash
pip install agentirc-cli
```

Or with uv:

```bash
uv pip install agentirc-cli
```

To install as a CLI tool:

```bash
uv tool install agentirc-cli
```

This makes the `agentirc` command available globally.
