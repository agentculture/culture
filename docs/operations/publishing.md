---
title: "PyPI Publishing"
parent: Operations
---

# PyPI Publishing

Publishing uses OIDC trusted publishers — no API tokens needed.

## Automated (GitHub Actions)

On **merge to main**: the `publish` workflow builds and publishes `culture` to PyPI automatically.

On **pull request**: a dev version (`0.x.y.devN`) is published to TestPyPI as `culture`, `agentirc-cli`, and `agentirc` (legacy aliases).

## Manual

For local publishing, use trusted publishing if your environment supports OIDC,
or create an API token at <https://pypi.org/manage/account/token/>.

```bash
uv build
uv publish
```

## Install

```bash
pip install culture
```

Or with uv:

```bash
uv pip install culture
```

To install as a CLI tool:

```bash
uv tool install culture
```

This makes the `culture` command available globally.
