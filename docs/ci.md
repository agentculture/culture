---
title: "CI / Testing"
parent: "Server Architecture"
nav_order: 6
---

# CI: Test Workflow

## Overview

The `Tests` GitHub Actions workflow runs the project's test suite on every pull request targeting `main`. It ensures no PR merges with broken tests.

## Workflow File

`.github/workflows/tests.yml`

## Trigger

- **Event:** `pull_request`
- **Branches:** `main`

## What It Does

1. **Checkout** — clones the repository
2. **Setup uv** — installs the `uv` package manager via `astral-sh/setup-uv@v4`
3. **Install Python 3.12** — `uv python install 3.12`
4. **Sync dependencies** — `uv sync` (includes dev dependencies by default)
5. **Run tests** — `uv run pytest -v`

## Permissions

The workflow uses least-privilege token permissions (`contents: read`), since it only needs to check out code and run tests.

## Running Tests Locally

To reproduce the CI environment locally:

```bash
uv python install 3.12
uv sync
uv run pytest -v
```
