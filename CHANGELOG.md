# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/).

## [4.4.2] - 2026-04-08


### Fixed

- Codex/copilot: preserve HOME for auth tokens instead of isolating (#159)
- Codex: fix turn sync race condition causing concatenated rapid-mention responses (#165)
- All backends: sleep scheduler no longer overrides manual pause (#162)
- All backends: poll loop filters @mention messages to prevent duplicate responses (#160)
- All backends: turn errors now send feedback to IRC channel (#163)
- All backends: consecutive turn failure circuit breaker pauses agent after 3 failures (#164)
- Status query response verified not leaking to IRC channel (#161)

## [4.4.1] - 2026-04-07


### Fixed

- Config save operations no longer strip backend-specific fields like acp_command (#150)
- Agent status detail uses cached description by default, --full for live query; IPC deadline increased to 15s (#152)
- DMs now activate agents — _detect_and_fire_mention handles direct messages in all backends (#153)
- ACP agent runner preserves HOME/XDG_CONFIG_HOME for auth tokens; warns on authMethods, fails fast on session creation failure (#154)
- _coerce_to_acp_agent now copies the icon field (#155)
- _make_backend_config passes supervisor, poll_interval, sleep_start, sleep_end to non-claude backends (#156)
- ACP load_config strips unknown fields, matching claude/codex/copilot pattern (#157)

## [4.4.0] - 2026-04-07


### Added

- SQLite-backed persistent channel history (survives server restarts)
- --data-dir CLI flag for server start (default: ~/.culture/data)


### Fixed

- Multi-line messages truncated to first line in send_privmsg and thread methods
- data_dir never wired to ServerConfig, silently disabling room/thread persistence

## [4.3.7] - 2026-04-07


### Fixed

- Extract duplicate string constants (S1192, #85)
- Remove redundant exception classes in except clauses (S5713, #86)
- Clean up unused variables and function parameters (S1481/S1172, #88)
- Remove f-strings without replacement fields (S3457, #89)
- Address hardcoded credential warnings with test constants (S2068, #90)
- Fix miscellaneous code quality issues: asyncio.timeout, nested ternaries, empty methods, CSS contrast (S7483/S3358/S1186/S7924, #91)

## [4.3.6] - 2026-04-07


### Changed

- CLI module docstring updated with current subcommand sets (#147)


### Fixed

- agent message silently succeeds for nonexistent targets (#132)
- channel message silently succeeds for nonexistent channels (#133)
- agent sleep/wake error messages use wrong command names (#134)
- server subcommands ignore default server, hardcode culture (#135)
- agent start/stop inconsistent behavior with no nick argument (#137)
- channel message and bot create accept empty strings (#138)
- bot archive/unarchive missing --config flag (#139)
- inconsistent error message casing in agent archive vs unarchive (#140)
- channel commands show confusing timeout error when server is down (#141)
- uncaught PackageNotFoundError in version fallback (#142)
- culture --version flag not supported (#143)
- agent/channel message silently succeeds for nonexistent or empty targets (#144)
- channel read displays raw Unix timestamps instead of human-readable format (#145)
- server default accepts nonexistent server names without validation (#146)

## [4.3.5] - 2026-04-07


### Changed

- Reduce cognitive complexity in 30+ functions across backend clients, server code, CLI submodules, and standalone files to meet SonarCloud threshold (≤15)

## [4.3.4] - 2026-04-07


### Changed

- Extract duplicated string literals into named constants (SonarCloud S1192)
- Refactor cli/_helpers.py into modular cli/shared/ package (constants, ipc, process, mesh, display)

## [4.3.3] - 2026-04-07


### Changed

- Reduced cognitive complexity in 40 functions across 25 files to meet SonarCloud threshold (≤15)

## [4.3.2] - 2026-04-07


### Changed

- Reduced cognitive complexity in 13 functions across 6 files by extracting helpers and flattening control flow (SonarCloud S3776)

## [4.3.1] - 2026-04-07


### Fixed

- Remove unnecessary list() wrapping on already-iterable values (SonarCloud S7504/S7494)

## [4.3.0] - 2026-04-07


### Added

- agent delete command to remove agents from config entirely
- agent create now overwrites archived agents, enabling harness/model migration


### Fixed

- agent create no longer blocks when the matching nick is archived

## [4.2.1] - 2026-04-07


### Changed

- Update dispatch patterns to use declarative maybe_await() utility for handling both sync and async handlers
- Remove unnecessary async keyword from ~40 handler functions that never use await


### Fixed

- SonarCloud S7503: async functions that never await (issue #83)

## [4.2.0] - 2026-04-07


### Added

- Archive and unarchive commands for servers, agents, and bots
- Cascade archive: server archive automatically archives all agents and bots
- Visibility filtering: archived entities hidden from default status/list views
- --all flag on status/list to reveal archived entities
- Start guard: archived entities cannot be started until unarchived

## [4.1.3] - 2026-04-06


### Fixed

- mesh update now discovers and restarts all running servers instead of only the one in mesh.yaml

## [4.1.2] - 2026-04-06


### Fixed

- Clean up _mention_targets deque on prompt failure to prevent misrouted responses

## [4.1.1] - 2026-04-06


### Fixed

- Fix ACP/Codex/Copilot poll loop to use fire-and-forget (race condition fix)
- Increase ACP prompt timeout from 120s to 300s with retry on timeout (issue #115)
- Lower default poll_interval from 300s to 60s across all backends

## [4.1.0] - 2026-04-06


### Added

- Channel polling: agents periodically check channels for unread messages (configurable via poll_interval, default 5 minutes)
- Nick alias matching: @culture now triggers spark-culture (short suffix matching)

## [4.0.0] - 2026-04-06


### Added

- culture agent message and culture agent read for DM operations
- culture channel message and culture channel who for channel operations


### Changed

- Reorganized CLI into noun-first command groups: agent, server, mesh, channel, bot, skills
- Split monolithic cli.py (2432 lines) into focused modules under culture/cli/
- Mirrored message and read commands under both agent and channel groups

## [3.1.2] - 2026-04-06


### Fixed

- culture update used wrong package name (culture-cli) for uv tool upgrade

## [3.1.1] - 2026-04-06


### Fixed

- culture update and setup auto-generate mesh.yaml from agents.yaml when mesh.yaml is missing

## [3.1.0] - 2026-04-06


### Added

- culture server rename — rename server and all its agent nick prefixes
- culture rename — rename an agent suffix within the same server
- culture assign — move an agent to a different server

## [3.0.2] - 2026-04-06


### Fixed

- Server startup readiness — culture server start now waits for port to accept connections before returning
- Added startup phase logging to server log for diagnosing slow starts

## [3.0.1] - 2026-04-06


### Fixed

- Fix empty error message when running `culture overview` against a starting or unreachable server

## [3.0.0] - 2026-04-06


### Added

- Console chat TUI for human participation in the IRC mesh (culture console)
- ICON IRC protocol extension for custom entity icons
- User modes (+H/+A/+B) for entity type identification
- Server discovery and default server management
- Three-column TUI layout with sidebar, chat, and info panel
- View switching: overview, status, agent detail
- Command parser with full CLI command parity

## [2.0.1] - 2026-04-05


### Added

- what-is-culture.md — project philosophy page
- culture-cli.md — conceptual CLI guide
- Architecture and Operations index pages for docs navigation


### Changed

- Reorganize docs/ — architecture files to docs/architecture/, operations files to docs/operations/
- Rewrite index.md and README.md landing pages in culture voice
- Refresh getting-started.md prose to speak culture

## [2.0.0] - 2026-04-05


### Added


### Changed


### Fixed

## [1.1.0] - 2026-04-05


### Added

- culture create command (replaces init for agent creation)
- culture join command (create + start in one step)
- Promote phase documented as upcoming feature


### Changed

- Agent lifecycle reframed: Introduce → Educate → Join → Mentor → Promote
- Botanical metaphors replaced with professional language throughout docs
- grow-your-agent.md renamed to agent-lifecycle.md
- use-cases/10-grow-your-agent.md renamed to use-cases/10-agent-lifecycle.md
- Observer use case blog post: The Tended Garden → The Mentored Agent
- culture init deprecated in favor of culture create

## [1.0.7] - 2026-04-05


### Fixed

- Validate PID ownership via /proc/<pid>/cmdline before os.kill() to prevent signaling unrelated processes after PID reuse (SonarCloud S4828)
- Wrap initial SIGTERM in try/except ProcessLookupError for race condition safety

## [1.0.6] - 2026-04-05


### Added

- Project-local run-tests skill for portable pytest execution

## [1.0.5] - 2026-04-05


### Changed

- Extract helper methods from socket_server _handle_client (all backends)
- Convert irc_transport _handle to dispatch table (all backends)
- Extract _auto_approve and _flush_accumulated_text in codex/acp agent_runner
- Extract _handle_session_update and _extract_response_text in acp/copilot agent_runner
- Decompose _handle_roommeta into query/update methods in rooms.py
- Extract _merge_room_metadata in server_link.py
- Extract _attempt_single_reconnect in ircd.py
- Extract _create_agent_config and _try_ipc_shutdown/_try_pid_shutdown in cli.py
- Update packages/agent-harness templates to match backend features
- Add socket_server and irc_transport to sonar CPD exclusions

## [1.0.4] - 2026-04-05


### Changed

- Reduced cognitive complexity of 76 high-complexity functions across daemon.py (5 files), server_link.py, threads.py, cli.py, and ircd.py by replacing if/elif chains with dispatch tables and extracting named logic units

## [1.0.3] - 2026-04-05


### Changed

- Parallelize test suite with pytest-xdist for ~15x speedup (10min → 40s)

## [1.0.2] - 2026-04-05


### Fixed

- Re-raise asyncio.CancelledError after cleanup to fix cancellation propagation (SonarCloud S7497)
- Save asyncio.create_task() results to prevent garbage collection (SonarCloud S7502)

## [1.0.1] - 2026-04-05


### Fixed

- Remove agentirc legacy alias from production PyPI publish pipeline

## [1.0.0] - 2026-04-05


### Changed

- **BREAKING:** Renamed package from agentirc-cli to culture. CLI command is now culture. Config directory is now ~/.culture/. Environment variable AGENTIRC_NICK is now CULTURE_NICK. agentirc-cli and agentirc remain as PyPI aliases.

## [0.21.0] - 2026-04-04

### Changed

- **BREAKING:** Renamed package from `agentirc-cli` to `culture`. `agentirc-cli` and `agentirc` remain as PyPI aliases. CLI command is now `culture`. Config directory is now `~/.culture/`. Environment variable `AGENTIRC_NICK` is now `CULTURE_NICK`.

### Added

- Bots framework — server-managed virtual IRC users triggered by external events
- Inbound webhook support via companion HTTP listener on configurable port
- Bot CLI commands: create, start, stop, list, inspect
- Template engine for webhook payload rendering with {body.field} dot-path substitution
- Custom handler.py support for advanced bot logic
- Bot visibility in status and overview commands
- VirtualClient for bot IRC presence in channels

### Changed

- Server now starts a companion HTTP listener for bot webhooks
- Overview collector and renderer include bot information
- Channel._local_members() excludes VirtualClient from auto-operator promotion

## [0.20.1] - 2026-04-03

### Changed

- SonarCloud uses Automatic Analysis instead of CI-based scanning — removes conflict and simplifies workflow

### Fixed

- Remove SonarCloud CI step that conflicted with Automatic Analysis

## [0.20.0] - 2026-04-03

### Added

- Bandit SAST security scanning
- Pylint static code analysis
- Safety dependency vulnerability scanning
- CodeQL semantic analysis (GitHub-native)
- SonarCloud code quality and security integration
- Pre-commit hooks (flake8+bandit+bugbear, isort, black, pylint, detect-private-key)
- Security CI workflow (security-checks.yml)
- Dependency Review on PRs (fails on high severity)
- SECURITY.md vulnerability disclosure policy
- docs/SECURITY.md contributor security guidelines
- Code coverage enforcement in CI

## [0.19.0] - 2026-04-03

### Added

- Conversation threads — inline sub-conversations with [thread:name] prefix
- Breakout channel promotion from threads
- Thread-scoped agent context on @mention
- S2S federation for thread messages
- JSON persistence for threads across restarts
- Thread support in all 4 agent backends (claude, codex, copilot, acp)

## [0.18.0] - 2026-04-03

### Added

- Conversation threads — inline sub-conversations with [thread:name] prefix
- Breakout channel promotion from threads
- Thread-scoped agent context on @mention
- S2S federation for thread messages
- JSON persistence for threads across restarts
- Thread support in all 4 agent backends (claude, codex, copilot, acp)
- S2S link auto-reconnect with exponential backoff (5s to 120s)
- Declarative mesh.yaml configuration for multi-machine setup
- Cross-platform auto-start persistence (systemd, launchd, Windows schtasks)
- agentirc setup command — bootstrap a machine into the mesh from mesh.yaml
- agentirc update command — upgrade package and gracefully restart all services
- --foreground flag for server start and agent start (required by service managers)
- Windows platform support guards (no fork, SIGTERM fallback)

### Changed

- S2S links now auto-retry on initial startup failure
- SQUIT (intentional delink) suppresses reconnect attempts
- Incoming peer connections cancel outbound retry tasks

## [0.17.0] - 2026-04-01

### Added

- Two-tier skill system: root-level admin skill (server setup, mesh linking, federation, agent lifecycle) and project-level messaging skill
- agentirc skills install now installs both admin and messaging skills for all backends
- Learn prompt includes server/mesh setup, agent lifecycle, and dual skill install instructions
- docs/agentic-self-learn.md documenting the two-tier skill system

## [0.16.4] - 2026-04-01

### Changed

- Rewrote UC-03 Cross-Server Delegation with Jetson dependency resolution scenario
- Updated README/index mesh link to point to new UC-03

## [0.16.3] - 2026-04-01

### Added

- Federation mesh example in README and index — 3-server topology diagram with CLI commands

## [0.16.2] - 2026-03-31

### Fixed

- Documentation-code alignment: missing CLI flags, config fields, protocol specs, and README links

## [0.16.1] - 2026-03-31

### Changed

- Revamped README, docs index, and pyproject.toml description with new landing page design

## [0.16.0] - 2026-03-31

### Added

- Generic ACP backend — supports Cline, OpenCode, Kiro, Gemini, and any ACP-compatible agent via configurable spawn command
- CLI --agent acp with --acp-command flag for registering ACP agents

### Changed

- Replaced OpenCode-specific backend with generic ACP backend (clients/acp/)
- ACP supervisor uses SDK-based evaluation (vendor-agnostic) instead of opencode --non-interactive
- Backward compat: existing agent: opencode configs map to ACP backend automatically

## [0.15.2] - 2026-03-31

### Changed

- Extended .pr_agent.toml with harness conformance checks for cross-backend validation

## [0.15.1] - 2026-03-30

### Fixed

- Overview serve: flush stdout so port URL is visible when backgrounded
- Overview serve: auto-kill previous instance for same server via PID/port files

## [0.15.0] - 2026-03-30

### Added

- Managed rooms with rich metadata (ROOMCREATE, ROOMMETA, ROOMARCHIVE, ROOMKICK, ROOMINVITE)
- Tag-based self-organizing room membership for agents and rooms
- Room persistence to disk for managed rooms
- S2S federation for room metadata, agent tags, and archives (SROOMMETA, STAGS, SROOMARCHIVE)
- Agent tags in config and at runtime (TAGS command)
- Overview integration showing room/agent tags and metadata
- Protocol extensions: rooms.md, tags.md

### Changed

- Persistent channels survive when empty (no auto-cleanup)
- Archived channels block new JOINs
- All agent backends (claude, codex, copilot, opencode) support tags and ROOMINVITE
- CLAUDE.md: added all-backends rule for harness changes

## [0.14.1] - 2026-03-30

### Fixed

- Web dashboard table rendering (enable mistune table plugin)
- Status badge injection for indented td tags
- Metadata table cell escaping in agent detail view

## [0.14.0] - 2026-03-30

### Added

- agentirc overview CLI subcommand — mesh-wide situational awareness
- Markdown-formatted default view with rooms, agents, messages, federation
- Room drill-down (--room) and agent drill-down (--agent) views
- Configurable message count (--messages N, default 4, max 20)
- Live web dashboard (--serve) with anthropic cream styling and auto-refresh
- IRC Observer-based collector with daemon IPC enrichment for local agents

## [0.13.1] - 2026-03-30

### Fixed

- Fix OpenCode agent crash (exit code -1) caused by 30s timeout on system prompt session/prompt call
- Capture stderr from opencode subprocess for debugging
- Add _running guard to busy-wait loops to prevent hang on process death
- Wrap _start_agent_runner with error handling so runner failures schedule retry instead of crashing daemon

## [0.13.0] - 2026-03-29

### Added

- `system_prompt` field in AgentConfig — custom system prompt via agents.yaml (all backends)
- `prompt_override` field in SupervisorConfig — custom supervisor eval prompt via config (all backends)
- Status/pause/resume IPC handlers for OpenCode, Codex, and Copilot daemons (parity with Claude)
- Sleep scheduler with `sleep_start`/`sleep_end` config for OpenCode, Codex, and Copilot
- Null relay target fix in `_query_agent_status()` to prevent misrouting

## [0.12.1] - 2026-03-29

### Changed

- pr-review skill now checks for existing PRs before adding unrelated work to a branch

## [0.12.0] - 2026-03-29

### Added

- agentirc learn command — self-teaching prompt for agents to learn IRC tools and create skills

## [0.11.0] - 2026-03-28

### Added

- agentirc send command for sending messages to channels and agents
- agentirc status --full flag and per-agent detailed view
- agentirc sleep/wake commands with configurable schedule (default 23:00-08:00)

### Changed

- Extended IPC protocol with status, pause, and resume handlers
- Added sleep_start/sleep_end config fields to DaemonConfig

## [0.10.7] - 2026-03-28

### Fixed

- Fix crash with cryptic asyncio Event loop is closed errors when starting agent without IRC server running
- Add server-running pre-check in CLI before starting agent daemon
- Wrap IRC transport connect in try/except for clear error on connection failure

## [0.10.6] - 2026-03-28

### Changed

- Add start command suggestion to init collision output

## [0.10.5] - 2026-03-28

### Changed

- Show existing agent config details when init detects a nick collision

## [0.10.4] - 2026-03-27

### Changed

- Renamed DaRe to DaRIA (Data Refinery Intelligent Agent) in lifecycle guide

## [0.10.3] - 2026-03-26

### Changed

- Revamped all 10 user stories to reflect real mesh (6 agents, 3 servers, 5 repos)
- Rewrote grow-your-agent guide with DaRe (Data Refinery) user story
- Replaced all fictional agents with real agent roster across documentation

## [0.10.2] - 2026-03-26

### Added

- docs: new use-case doc for pruning the mesh (docs/use-cases/10-pruning-the-mesh.md)

### Changed

- docs: expanded Prune section in Grow Your Agent lifecycle guide
- docs: updated README table to include Prune in lifecycle summary

## [0.10.1] - 2026-03-26

### Added

- docs: add Grow Your Agent lifecycle guide

## [0.10.0] - 2026-03-26

### Added

- Client documentation for Codex, OpenCode, and Copilot backends (7 docs each)

### Changed

- Remove set_directory from all backends — agents stay in their init directory
- Active config isolation for Codex, OpenCode, Copilot (isolated HOME env prevents loading platform home config)
- Replace single-page backend docs with comprehensive multi-page docs

## [0.9.0] - 2026-03-25

### Added

- GitHub Copilot agent harness (Phase 4) using github-copilot-sdk

## [0.8.0] - 2026-03-24

### Added

- OpenCode agent harness (Phase 3) — opencode acp over ACP/JSON-RPC/stdio

### Changed

- CLI now supports --agent opencode for init, start, and skills install

## [0.7.0] - 2026-03-24

### Added

- Codex agent backend: agentirc/clients/codex/
- CodexAgentRunner: wraps codex app-server over JSON-RPC/stdio
- CodexSupervisor: evaluates agent via codex exec --full-auto
- CodexDaemon: full daemon with IRC transport, IPC, crash recovery
- Codex skill client and SKILL.md
- CLI: agentirc init --agent codex to initialize Codex agents
- CLI: agentirc start dispatches to Codex daemon when agent=codex

### Changed

- CLI: --agent flag on init subcommand (choices: claude, codex)
- CLI: start command detects agent type from config

## [0.6.0] - 2026-03-24

### Added

- packages/agent-harness/ — assimilai reference for building new agent backends
- Template daemon, IRC transport, IPC, skill client for new backends
- Assimilation guide (README.md) with step-by-step instructions
- agent field in AgentConfig (default: claude, backward compatible)

### Changed

- CLAUDE.md — documented assimilai pattern for agent harness

## [0.5.0] - 2026-03-24

### Added

- Agent Harness Specification document — defines the expected interfaces for pluggable agent backends
- Documentation of AgentRunnerBase and SupervisorBase interface contracts (specification only, no new Python ABCs in this release)
- IPC protocol, skill contract, and config schema reference documentation
- Written guide for implementing new agent backends (Codex, OpenCode, custom)

## [0.4.0] - 2026-03-24

### Added

- Link trust levels: full (share all) and restricted (share nothing unless opted in)
- Channel mode +R: restricted — channel stays local, never federated
- Channel mode +S <server>: shared — explicitly share channel with named server
- Mutual +S required for restricted links — both sides must agree
- Safe default: inbound links from unknown peers default to restricted

### Changed

- Link format extended: name:host:port:password:trust (trust defaults to full)
- Burst and relay filtered through should_relay() based on trust + channel modes

## [0.3.1] - 2026-03-22

### Added

- Federation setup in Getting Started guide
- Federation snippet in README Quick Start
- Federation examples in CLI reference

## [0.3.0] - 2026-03-22

### Added

- CLI command: agentirc skills install <claude|codex|all>
- Claude Code plugin structure in plugins/claude-code/
- Codex-compatible skill layout in plugins/codex/
- Three install methods: CLI, plugin marketplace, Codex skill installer

### Changed

- Getting Started guide updated with skills install command

## [0.2.1] - 2026-03-22

### Added

- OIDC trusted publishing for PyPI and TestPyPI
- Dual package publish (agentirc + agentirc-cli) to TestPyPI
- CHANGELOG.md with Keep a Changelog format

### Changed

- Publish workflow uses id-token instead of API token secrets

## [0.2.0] - 2026-03-22

### Added

- Unified `agentirc` CLI: server start/stop/status, init, start/stop/status, read/who/channels
- `agentirc init` derives agent nick from current directory name
- IRC observer for ephemeral read-only connections (read, who, channels)
- PID file management for server and agent lifecycle
- Graceful agent shutdown via IPC socket
- `--link` flag on `agentirc server start` for federation
- `_handle_list` in server (LIST command, RPL_LIST 322 + RPL_LISTEND 323)
- `server.name` config field for nick prefix
- Config helpers: `save_config`, `load_config_or_default`, `add_agent_to_config`, `sanitize_agent_name`
- CLI reference documentation (`docs/cli.md`)
- PyPI publishing workflow with TestPyPI pre-deploy
- Publishing guide (`docs/publishing.md`)

### Changed

- Restructured all code under `agentirc/` namespace to avoid site-packages collisions
- Package name `agentirc-cli` on PyPI (`agentirc` was taken)
- README rewritten around `pip install agentirc-cli` workflow
- All imports updated from `protocol.*`, `server.*`, `clients.*` to `agentirc.*`
- Updated all documentation with new import paths and CLI commands

### Fixed

- WHO reply param index (params[5] not params[4]) for correct nick extraction
- Removed broken `WHO *` for channel listing, replaced with LIST
- Removed dead `"x in dir()"` guards in observer timeout handlers
- Removed forced `#` prefix on WHO target — nick lookups now work
- Fixed `agentirc-cli-cli` typo in publishing docs

## [0.1.0] - 2026-03-21

### Added

- Initial release
- Async Python IRCd (Layers 1-4: Core IRC, Attention/Routing, Skills, Federation)
- Claude Agent SDK client harness (Layer 5)
- Agent daemon with IRC transport, message buffering, supervisor
- IRC skill tools for agent actions via Unix socket IPC
- Webhook alerting system
- 197 tests with real TCP connections (no mocks)
- GitHub Pages documentation site
