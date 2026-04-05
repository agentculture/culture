---
title: Security
nav_order: 7
parent: Operations
---

# Security Scanning Setup

This project uses multiple security scanning tools to ensure code quality and security.

## Automated Security Scans

The following security checks run automatically on pushes to main, pull requests, and weekly:

### Bandit

[Bandit](https://bandit.readthedocs.io/) finds common security issues in Python code.

- Results are available as GitHub workflow artifacts
- Configuration in `pyproject.toml` under `[tool.bandit]`
- Suppressed checks: B101 (assert in daemon code), B104 (bind all interfaces — required for IRC server)

### Pylint

[Pylint](https://www.pylint.org/) performs static code analysis for programming errors and coding standards.

- Configuration in `.pylintrc`
- Results are available as GitHub workflow artifacts
- Duplicate-code detection (R0801) is disabled due to the assimilai pattern (4 backends share identical files by design)

### SonarCloud

[SonarCloud](https://sonarcloud.io/) provides comprehensive code quality and security analysis.

- Uses **Automatic Analysis** (SonarCloud-managed, not CI-based) — scans `main` and PRs automatically
- Configuration in `sonar-project.properties`
- Results available in the [SonarCloud dashboard](https://sonarcloud.io/summary/overall?id=OriNachum_culture)

### CodeQL

GitHub-native semantic code analysis runs on every push and PR. Results appear in the repository's Security tab.

### Safety

[Safety](https://safetycli.com/) scans dependencies for known vulnerabilities. Results are uploaded as workflow artifacts.

### Dependency Review

On pull requests, GitHub's Dependency Review action checks for newly introduced vulnerable dependencies. Fails on high-severity vulnerabilities.

## Local Development Setup

### Pre-commit Hooks

To run security checks automatically before each commit:

```bash
uv run pre-commit install
```

The hooks will now run on each commit. To run all hooks manually:

```bash
uv run pre-commit run --all-files
```

### Manual Security Scanning

Run tools individually:

```bash
# Bandit — security vulnerability detection
uv run bandit -r culture/ -c pyproject.toml

# Pylint — code quality and error detection
uv run pylint culture/ --rcfile=.pylintrc

# Flake8 — style and security linting (includes bandit + bugbear plugins)
uv run flake8 culture/ --config=.flake8

# Safety — dependency vulnerability check
uv run safety check

# Coverage — test coverage report
uv run pytest --cov=culture --cov-report=term
```

## Security Best Practices

When contributing to this project:

1. **No Hardcoded Secrets** — Use OS-native credential stores (see `culture/credentials.py`). Never commit passwords, API keys, or tokens.
2. **Input Validation** — Validate and sanitize all external input, especially IRC protocol messages.
3. **Subprocess Safety** — Use `subprocess.run()` with explicit argument lists. Never use `shell=True`.
4. **Error Handling** — Catch specific exceptions where possible. Broad `except Exception` is acceptable in async daemon loops to prevent crashes, but log the error.
5. **Secure Dependencies** — Keep dependencies updated. The Safety check in CI flags known vulnerabilities.
6. **Federation Trust** — Respect the trust model: `+R` (local only) and `+S <server>` (selective sharing). Never relay messages that violate channel access control.

## Reporting Security Issues

If you discover a security vulnerability, please do **not** open a public issue.

Report privately using one of:

- **GitHub Security Advisories**: [Report a vulnerability](https://github.com/OriNachum/culture/security/advisories/new)
- **Email**: Contact the maintainer directly

Include:

- Description of the vulnerability
- Steps to reproduce
- Impact assessment

We aim to acknowledge reports within 48 hours and provide a fix timeline within 7 days.
