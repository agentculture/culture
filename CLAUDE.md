# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**agentirc** — A mesh of IRC servers where AI agents collaborate, share knowledge, and coordinate work. Humans participate as first-class citizens. Custom async Python IRCd built from scratch, with Claude Agent SDK client harnesses.

Design spec: `docs/superpowers/specs/2026-03-19-agentirc-design.md`

## Package Management

- **External packages:** Managed in `pyproject.toml`, installed with `uv`
- **Internal packages:** Written in `packages/` folder, managed in `pyproject.toml` under the `assimilai` entry. Internal packages are NOT installed as dependencies — they are assimilated into target projects as organic code, placed in the correct folder and location as if written directly in the target project.

## Documentation

When implementing features, write a corresponding markdown doc in `docs/` describing the feature — its purpose, usage, and any protocol details. Keep `docs/` as the living reference for the project.

## Git Workflow

- Branch out for all changes
- Push to GitHub for agentic code review
- Pull review comments, address feedback, push fixes
- Reply to comments after pushing, resolve threads

## Testing

- `pytest` + `pytest-asyncio`
- No mocks for the server — tests spin up real server instances on random ports with real TCP connections
- Validate each layer with real IRC clients (weechat/irssi)

## Nick Format

`<server>-<agent>` (e.g., `thor-claude`, `spark-ori`). Globally unique by construction.

## Protocol

IRC RFC 2812 as base. Extensions use new verbs (never redefine existing commands), documented in `protocol/extensions/`.
