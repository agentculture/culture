# cultureagent extraction — Phase 0a (test reinforcement) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring culture's integration tests up to cover the behaviors currently proven only by harness unit tests, then ratchet pytest's `[tool.coverage.report] fail_under` from PR #362's locked baseline (`56`) toward the projected post-Phase-0a floor (~73 project-wide; ~85 on `culture/clients/`). Optionally raise the SonarCloud Quality Gate threshold to match. The cutover PR in Phase 1 then has real gates blocking regressions.

**Architecture:** Audit current coverage → add targeted integration tests one-per-behavior → flip enforcement gates. Each integration test follows the `tests/test_integration_layer5.py` pattern (real `agentirc.IRCd` from the `server` fixture, real `AgentDaemon`, real `SkillClient`, observable assertions). Each task is one PR with a `patch` version bump; the closeout is one PR with a `minor` bump.

**Tech Stack:** `pytest` + `pytest-asyncio` + `pytest-xdist`; existing `server`/`make_client`/`tracing_exporter`/`metrics_reader` fixtures in `tests/conftest.py`; SonarCloud (`sonar.qualitygate.wait=true` already in `sonar-project.properties`).

**Spec:** [`docs/superpowers/specs/2026-05-09-cultureagent-extraction-design.md`](../specs/2026-05-09-cultureagent-extraction-design.md)

**Sequencing note:** Tasks 1 and 9 are gates (Task 1 produces the audit doc that Tasks 2–8 reference; Task 9 is the closeout that depends on Tasks 2–8 reaching the post-Phase-0a projection ~73% project-wide). Tasks 2–8 are independent in scope but each runs `/version-bump patch`, so they will conflict on `CHANGELOG.md` and `pyproject.toml` if merged in parallel. Recommended cadence: open Tasks 2–8 PRs in parallel for review, but **rebase and merge them sequentially** — the cicd skill's `await` flow surfaces the conflict cleanly when it appears.

**Convention — temp artifact paths:** All bash commands in this plan that write coverage/test artifacts use `/tmp/culture-tests/<filename>` rather than bare `/tmp/<filename>` to avoid collisions with parallel agents on the same machine. Create the directory first (`mkdir -p /tmp/culture-tests`) at the start of any task that writes there. Python's `tempfile.mkdtemp()` already creates uniquely-named dirs and is unaffected.

---

## File Structure

**Created (one per integration test task):**

| Path | Responsibility |
|---|---|
| `docs/superpowers/notes/2026-05-09-cultureagent-coverage-audit.md` | Audit output: gap list, per-behavior decision (add integration / accept loss / already covered) |
| `tests/test_integration_attention.py` | Attention transitions + dynamic levels end-to-end |
| `tests/test_integration_message_buffer.py` | Buffer overflow drain ordering |
| `tests/test_integration_irc_transport.py` | IRCv3 tag propagation + reconnect |
| `tests/test_integration_webhook.py` | HTTP fanout + IRC alert channel |
| `tests/test_integration_telemetry.py` | Counter + span emission during real ops |
| `tests/test_integration_supervisor.py` | Restart-on-crash via real subprocess |
| `tests/test_integration_agent_runner.py` | Per-backend timeout, parameterized over 4 backends |

**Modified:**

| Path | Change |
|---|---|
| `.github/workflows/tests.yml` | (no changes — `--cov-fail-under` already enforced via `[tool.coverage.report]` since PR #362; only `pyproject.toml`'s `fail_under` value ratchets at closeout) |
| `pyproject.toml` + `CHANGELOG.md` + `culture/__init__.py` | One `patch` bump per integration-test PR; one `minor` bump for the closeout PR (handled via `/version-bump`) |

**SonarCloud (out-of-tree):**

- Update Quality Gate at https://sonarcloud.io/project/quality_gate?id=agentculture_culture (optional Path A in Task 9, after Tasks 2–8 raise `culture/clients/` coverage to ~85%)

---

## Task 1: Coverage audit and gap analysis

**Files:**
- Create: `docs/superpowers/notes/2026-05-09-cultureagent-coverage-audit.md`

- [ ] **Step 1: Branch off main**

```bash
git checkout main && git pull --quiet
git checkout -b chore/cultureagent-extraction-coverage-audit
```

- [ ] **Step 2: Run coverage with line-level detail**

```bash
uv run pytest -n auto --cov=culture --cov-report=term-missing --cov-report=html --cov-report=xml -v 2>&1 | tee /tmp/culture-tests/coverage-audit.log
```

Expected: a coverage summary on stdout, `htmlcov/index.html` written, `coverage.xml` written. Coverage percentage is the audit baseline.

- [ ] **Step 3: Extract per-module coverage for harness modules**

```bash
uv run coverage report --include='culture/clients/shared/*,culture/clients/*/{daemon,agent_runner,supervisor,config,constants}.py' > /tmp/culture-tests/harness-coverage.txt
cat /tmp/culture-tests/harness-coverage.txt
```

This is the baseline for "what's covered today, including unit-test contributions." The numbers will *drop* when those unit tests are deleted in Phase 1 unless integration tests fill the gap.

- [ ] **Step 4: Map each harness unit test file to the production code lines it uniquely covers**

For each file in `tests/harness/*.py`, `tests/test_daemon*.py`, `tests/test_supervisor.py`, `tests/test_message_buffer.py`, `tests/test_irc_transport*.py`, `tests/test_socket_server.py`, `tests/test_skill_client.py`, `tests/test_webhook.py`, `tests/test_attention*.py`, `tests/test_agent_runner*.py`, `tests/test_telemetry*.py`:

```bash
# For one file at a time:
uv run pytest tests/harness/test_attention.py --cov=culture --cov-report=term-missing --no-cov-on-fail -q 2>&1 | tail -30
```

Compare against the integration-test-only baseline:

```bash
# Baseline: only test_integration_layer5.py
uv run pytest tests/test_integration_layer5.py --cov=culture --cov-report=term-missing --no-cov-on-fail -q 2>&1 | tail -30
```

The delta is the unit test's unique contribution.

- [ ] **Step 5: Write the audit document**

Create `docs/superpowers/notes/2026-05-09-cultureagent-coverage-audit.md` with this structure:

```markdown
# cultureagent extraction — Phase 0a coverage audit

**Date:** 2026-05-09
**Baseline coverage (with all current tests):** XX.X%
**Baseline coverage (integration-only):** XX.X%
**Delta to fill:** XX.X percentage points

## Per-behavior gap list

| Behavior | Unit test source | Production code uniquely covered | Decision |
|---|---|---|---|
| Attention band transitions | tests/harness/test_attention.py | culture/clients/shared/attention.py L42-L89, L120-L145 | **ADD integration** (Task 2) |
| Dynamic attention levels | tests/harness/test_attention_config.py | culture/clients/shared/attention.py L200-L240 | **ADD integration** (Task 2) |
| Message buffer overflow | tests/test_message_buffer.py | culture/clients/shared/message_buffer.py L50-L90 | **ADD integration** (Task 3) |
| IRC tag propagation | tests/harness/test_irc_transport_propagation.py | culture/clients/shared/irc_transport.py L300-L350 | **ADD integration** (Task 4) |
| IRC reconnect | tests/test_irc_transport.py | culture/clients/shared/irc_transport.py L400-L450 | **ADD integration** (Task 4) |
| HTTP webhook fanout | tests/test_webhook.py | culture/clients/shared/webhook.py L80-L120 | **ADD integration** (Task 5) |
| IRC alert channel | tests/harness/test_webhook_config_shared.py | culture/clients/shared/webhook.py L150-L180 | **ADD integration** (Task 5) |
| Telemetry counters | tests/harness/test_telemetry_module.py | culture/clients/shared/telemetry.py L30-L80 | **ADD integration** (Task 6) |
| Daemon telemetry spans | tests/harness/test_daemon_telemetry.py | culture/clients/<backend>/daemon.py span emission sites | **ADD integration** (Task 6) |
| Supervisor restart-on-crash | tests/test_supervisor.py | culture/clients/<backend>/supervisor.py L60-L120 | **ADD integration** (Task 7) |
| agent_runner timeout (4 backends) | tests/harness/test_agent_runner_*.py | culture/clients/<backend>/agent_runner.py timeout paths | **ADD integration** (Task 8, parameterized) |
| All-backends parity | tests/harness/test_all_backends_parity.py | (asserts byte-equivalence of cited files) | **ACCEPT** — moves to cultureagent; no integration analog |
| Daemon config validation | tests/test_daemon_config.py | culture/clients/<backend>/config.py | **ACCEPT** — schema-level, unit tests in cultureagent are sufficient |
| LLM call recording | tests/harness/test_record_llm_call.py | culture/clients/<backend>/agent_runner.py | **EVALUATE** — covered by Task 6 (telemetry) if recording emits a span; otherwise add to Task 8 |
| Daemon IPC primitives | tests/test_daemon_ipc.py | culture/clients/shared/ipc.py | **ALREADY COVERED** — test_integration_layer5.py exercises full IPC chain |
| Skill client commands | tests/test_skill_client.py | culture/clients/<backend>/skill/irc_client.py | **ALREADY COVERED** — test_integration_layer5.py uses SkillClient |
| Socket server | tests/test_socket_server.py | culture/clients/shared/socket_server.py | **ALREADY COVERED** — same chain |
| No-per-backend-copy guard | tests/harness/test_no_per_backend_copy_of_shared_modules.py | (architectural lint) | **ACCEPT** — moves to cultureagent or is replaced by an explicit test on the shim layout |

## Acceptance criteria for closing this audit

- Every row marked **ADD integration** has a Phase 0a task assigned (Tasks 2–8).
- Every row marked **ACCEPT** has a one-sentence justification.
- Every row marked **ALREADY COVERED** has a pointer to the existing test that covers it.
```

**Note on "ADD integration" rows:** the line ranges in the table above are illustrative — fill in real ones from `--cov-report=term-missing` output. If a row reveals more unique coverage than expected, split it into multiple Phase 0a tasks (insert between existing tasks).

- [ ] **Step 6: Run /version-bump patch**

```bash
python3 .claude/skills/version-bump/scripts/bump.py patch
```

Expected: `pyproject.toml`, `culture/__init__.py`, `CHANGELOG.md` updated. (See `culture/CLAUDE.md` "Format Before Commit" section.)

- [ ] **Step 7: Commit**

```bash
git add docs/superpowers/notes/2026-05-09-cultureagent-coverage-audit.md pyproject.toml culture/__init__.py CHANGELOG.md uv.lock
git commit -m "docs: cultureagent extraction phase 0a coverage audit"
```

- [ ] **Step 8: Push and open PR**

```bash
git push -u origin chore/cultureagent-extraction-coverage-audit
bash .claude/skills/cicd/scripts/workflow.sh open-pr --title "Phase 0a: coverage audit for cultureagent extraction"
```

---

## Task 2: Attention behaviors integration test

**Files:**
- Create: `tests/test_integration_attention.py`

- [ ] **Step 1: Branch off main**

```bash
git checkout main && git pull --quiet
git checkout -b test/integration-attention
```

- [ ] **Step 2: Write the integration test**

```python
"""End-to-end attention behaviors — proves the attention state machine
through the full daemon import chain (the unit test in
tests/harness/test_attention.py moves to cultureagent in Phase 1)."""

import asyncio
import os
import tempfile

import pytest

from culture.clients.claude.config import (
    AgentConfig,
    DaemonConfig,
    ServerConnConfig,
    WebhookConfig,
)
from culture.clients.claude.daemon import AgentDaemon
from culture.clients.claude.skill.irc_client import SkillClient
from culture.clients.shared.attention import Band


@pytest.mark.asyncio
async def test_mention_bumps_attention_band(server, make_client):
    """A direct mention bumps the agent's attention band to ACTIVE_RECENT."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        webhooks=WebhookConfig(url=None),
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general"])
    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)
    await daemon.start()
    await asyncio.sleep(0.5)
    try:
        # Baseline: agent should start in IDLE
        assert daemon.attention.current_band() == Band.IDLE

        human = await make_client(nick="testserv-ori", user="ori")
        await human.send("JOIN #general")
        await human.recv_all(timeout=0.3)
        await human.send("PRIVMSG #general :testserv-bot are you there?")
        await asyncio.sleep(0.5)

        # Direct mention should bump to ACTIVE_RECENT
        assert daemon.attention.current_band() == Band.ACTIVE_RECENT
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_attention_decays_after_idle_window(server, make_client):
    """Without further mentions, attention decays to lower band after idle window."""
    # Set short attention windows for test speed
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        webhooks=WebhookConfig(url=None),
        attention_active_window_seconds=1,
        attention_passive_window_seconds=2,
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general"])
    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)
    await daemon.start()
    await asyncio.sleep(0.5)
    try:
        human = await make_client(nick="testserv-ori", user="ori")
        await human.send("JOIN #general")
        await human.recv_all(timeout=0.3)
        await human.send("PRIVMSG #general :testserv-bot ping")
        await asyncio.sleep(0.5)

        assert daemon.attention.current_band() == Band.ACTIVE_RECENT
        await asyncio.sleep(1.5)  # past active window, into passive
        # Decay step is triggered on next attention check
        assert daemon.attention.current_band() in (Band.PASSIVE, Band.IDLE)
        await asyncio.sleep(2.5)  # past passive window, into idle
        assert daemon.attention.current_band() == Band.IDLE
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_dynamic_attention_levels_per_channel(server, make_client):
    """Different channels can carry different attention configs (dynamic levels)."""
    # NOTE: fill in real config field name from culture/clients/shared/attention.py.
    # The test must drive different bands in two channels via different mention
    # patterns, then assert daemon.attention.band_for_channel(channel) returns
    # different values. Refer to harness/test_attention_config.py for the unit
    # test that this integration test replaces.
    pytest.skip("Fill in based on AttentionConfig surface in shared/attention.py")
```

**Note:** the third test starts as `pytest.skip` because the config-field naming for dynamic levels needs to be read from `culture/clients/shared/attention.py` at write time. Replace the skip with the real assertion before the test PR is opened. If the surface is too complex to integration-test, file an issue and accept the loss in the audit doc.

- [ ] **Step 3: Run the test**

```bash
uv run pytest tests/test_integration_attention.py -v
```

Expected: first two tests pass; third skips with a TODO message until you fill it in.

- [ ] **Step 4: Verify it actually exercises the integration chain**

```bash
uv run pytest tests/test_integration_attention.py --cov=culture.clients.shared.attention --cov-report=term-missing -v
```

Expected: coverage of `culture/clients/shared/attention.py` should rise compared to the integration-only baseline. Specifically the lines flagged in the audit doc Task 1 should now be covered.

- [ ] **Step 5: Run /version-bump patch + format**

```bash
python3 .claude/skills/version-bump/scripts/bump.py patch
uv run black tests/test_integration_attention.py
uv run isort tests/test_integration_attention.py
```

- [ ] **Step 6: Commit and push**

```bash
git add tests/test_integration_attention.py pyproject.toml culture/__init__.py CHANGELOG.md uv.lock
git commit -m "test: integration coverage for attention transitions"
git push -u origin test/integration-attention
bash .claude/skills/cicd/scripts/workflow.sh open-pr --title "Phase 0a: integration test for attention behaviors"
```

---

## Task 3: Message buffer integration test

**Files:**
- Create: `tests/test_integration_message_buffer.py`

- [ ] **Step 1: Branch and write test**

```bash
git checkout main && git pull --quiet
git checkout -b test/integration-message-buffer
```

```python
"""End-to-end message buffer behavior — flood a real channel with messages
past the buffer size, verify drain order via SkillClient.irc_read.
Replaces tests/test_message_buffer.py at the integration layer."""

import asyncio
import os
import tempfile

import pytest

from culture.clients.claude.config import (
    AgentConfig,
    DaemonConfig,
    ServerConnConfig,
    WebhookConfig,
)
from culture.clients.claude.daemon import AgentDaemon
from culture.clients.claude.skill.irc_client import SkillClient


@pytest.mark.asyncio
async def test_buffer_retains_most_recent_messages_under_overflow(server, make_client):
    """Sending 2× the buffer size keeps the most-recent N (drops oldest)."""
    # Read the buffer size from production code so the test stays in sync.
    from culture.clients.shared.message_buffer import DEFAULT_BUFFER_SIZE

    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        webhooks=WebhookConfig(url=None),
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general"])
    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)
    await daemon.start()
    await asyncio.sleep(0.5)
    try:
        human = await make_client(nick="testserv-ori", user="ori")
        await human.send("JOIN #general")
        await human.recv_all(timeout=0.3)

        flood_count = DEFAULT_BUFFER_SIZE * 2
        for i in range(flood_count):
            await human.send(f"PRIVMSG #general :flood-msg-{i:04d}")
        await asyncio.sleep(2.0)  # let buffer settle

        sock_path = os.path.join(sock_dir, "culture-testserv-bot.sock")
        skill = SkillClient(sock_path)
        await skill.connect()
        try:
            result = await skill.irc_read("#general", limit=flood_count)
            assert result["ok"]
            messages = result["data"]["messages"]
            # Buffer should contain at most DEFAULT_BUFFER_SIZE messages
            assert len(messages) <= DEFAULT_BUFFER_SIZE
            # Last message must be the highest-numbered one
            texts = [m["text"] for m in messages]
            assert any(f"flood-msg-{flood_count - 1:04d}" in t for t in texts)
            # Earliest message must NOT be msg-0000 (it should have been dropped)
            assert not any("flood-msg-0000" in t for t in texts)
        finally:
            await skill.close()
    finally:
        await daemon.stop()
```

- [ ] **Step 2: Run, verify coverage delta**

```bash
uv run pytest tests/test_integration_message_buffer.py -v
uv run pytest tests/test_integration_message_buffer.py --cov=culture.clients.shared.message_buffer --cov-report=term-missing -v
```

Expected: pass; coverage of `culture/clients/shared/message_buffer.py` rises against integration-only baseline.

- [ ] **Step 3: Bump, format, commit, push, PR**

```bash
python3 .claude/skills/version-bump/scripts/bump.py patch
uv run black tests/test_integration_message_buffer.py
uv run isort tests/test_integration_message_buffer.py
git add tests/test_integration_message_buffer.py pyproject.toml culture/__init__.py CHANGELOG.md uv.lock
git commit -m "test: integration coverage for message buffer overflow"
git push -u origin test/integration-message-buffer
bash .claude/skills/cicd/scripts/workflow.sh open-pr --title "Phase 0a: integration test for message buffer overflow"
```

---

## Task 4: IRC transport integration test (tags + reconnect)

**Files:**
- Create: `tests/test_integration_irc_transport.py`

- [ ] **Step 1: Branch and write test**

```bash
git checkout main && git pull --quiet
git checkout -b test/integration-irc-transport
```

```python
"""End-to-end IRC transport behavior — IRCv3 tag propagation through the
real agentirc.IRCd, plus reconnect after server bounce.
Replaces tests/test_irc_transport*.py at the integration layer."""

import asyncio
import os
import tempfile

import pytest

from culture.clients.claude.config import (
    AgentConfig,
    DaemonConfig,
    ServerConnConfig,
    WebhookConfig,
)
from culture.clients.claude.daemon import AgentDaemon
from culture.clients.claude.skill.irc_client import SkillClient


@pytest.mark.asyncio
async def test_message_tags_propagate_through_transport(server, make_client):
    """IRCv3 message tags from human reach agent's buffer with tags intact."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        webhooks=WebhookConfig(url=None),
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general"])
    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)
    await daemon.start()
    await asyncio.sleep(0.5)
    try:
        human = await make_client(nick="testserv-ori", user="ori")
        await human.send("CAP REQ :message-tags")
        await human.recv_all(timeout=0.3)
        await human.send("JOIN #general")
        await human.recv_all(timeout=0.3)
        # Send with explicit IRCv3 tag
        await human.send("@+example=value PRIVMSG #general :tagged hello")
        await asyncio.sleep(0.5)

        sock_path = os.path.join(sock_dir, "culture-testserv-bot.sock")
        skill = SkillClient(sock_path)
        await skill.connect()
        try:
            result = await skill.irc_read("#general", limit=10)
            assert result["ok"]
            messages = result["data"]["messages"]
            tagged = [m for m in messages if "tagged hello" in m["text"]]
            assert len(tagged) == 1
            # Verify the tag survived the transport
            # NOTE: confirm the schema for `tags` in the SkillClient response
            # by checking culture/clients/shared/irc_transport.py and the
            # SkillClient implementation. Adjust the assertion to the real key.
            assert "tags" in tagged[0] or "+example" in str(tagged[0])
        finally:
            await skill.close()
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_transport_reconnects_after_server_bounce(server, make_client):
    """Agent's transport reconnects when the underlying connection drops.

    Note: this test exercises the resilience path. If the server fixture doesn't
    expose a way to bounce, the test asserts the daemon survives a forced
    socket close instead. Read culture/clients/shared/irc_transport.py for
    the reconnect entry point and adjust the trigger accordingly.
    """
    pytest.skip("Fill in based on irc_transport.py reconnect surface")
```

- [ ] **Step 2: Run + verify coverage**

```bash
uv run pytest tests/test_integration_irc_transport.py -v
uv run pytest tests/test_integration_irc_transport.py --cov=culture.clients.shared.irc_transport --cov-report=term-missing -v
```

- [ ] **Step 3: Bump, format, commit, push, PR**

```bash
python3 .claude/skills/version-bump/scripts/bump.py patch
uv run black tests/test_integration_irc_transport.py
uv run isort tests/test_integration_irc_transport.py
git add tests/test_integration_irc_transport.py pyproject.toml culture/__init__.py CHANGELOG.md uv.lock
git commit -m "test: integration coverage for IRC transport tags + reconnect"
git push -u origin test/integration-irc-transport
bash .claude/skills/cicd/scripts/workflow.sh open-pr --title "Phase 0a: integration test for IRC transport"
```

---

## Task 5: Webhook fanout integration test

**Files:**
- Create: `tests/test_integration_webhook.py`

- [ ] **Step 1: Branch and write test**

```bash
git checkout main && git pull --quiet
git checkout -b test/integration-webhook
```

```python
"""End-to-end webhook fanout — HTTP POST to a local capture server, plus
IRC alert delivery to a configured channel. Replaces tests/test_webhook.py
and tests/harness/test_webhook_config_shared.py at the integration layer."""

import asyncio
import json
import os
import tempfile
from aiohttp import web

import pytest

from culture.clients.claude.config import (
    AgentConfig,
    DaemonConfig,
    ServerConnConfig,
    WebhookConfig,
)
from culture.clients.claude.daemon import AgentDaemon


@pytest.mark.asyncio
async def test_webhook_http_fanout(server, make_client, unused_tcp_port):
    """A mention condition triggers an HTTP POST to the configured webhook URL."""
    received = []

    async def capture(request):
        received.append(await request.json())
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_post("/hook", capture)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", unused_tcp_port)
    await site.start()

    try:
        config = DaemonConfig(
            server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
            webhooks=WebhookConfig(url=f"http://127.0.0.1:{unused_tcp_port}/hook"),
        )
        agent = AgentConfig(
            nick="testserv-bot", directory="/tmp", channels=["#general"]
        )
        sock_dir = tempfile.mkdtemp()
        daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)
        await daemon.start()
        await asyncio.sleep(0.5)
        try:
            human = await make_client(nick="testserv-ori", user="ori")
            await human.send("JOIN #general")
            await human.recv_all(timeout=0.3)
            await human.send("PRIVMSG #general :testserv-bot urgent ping")
            # Wait for webhook delivery (HTTP fanout is async)
            for _ in range(20):
                if received:
                    break
                await asyncio.sleep(0.1)
            assert received, "expected webhook POST to capture server"
            assert "testserv-bot" in json.dumps(received[0])
        finally:
            await daemon.stop()
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_webhook_irc_alert_channel(server, make_client):
    """Configured IRC alert channel receives an alert message on trigger."""
    # NOTE: read culture/clients/shared/webhook.py and webhook_types.py for
    # the IRC alert config field name (likely WebhookConfig.irc_alert_channel
    # or similar) and the alert trigger conditions. Replace the skip with a
    # real assertion: human in #alerts observes a message from the bot when a
    # trigger fires in #general.
    pytest.skip("Fill in based on WebhookConfig IRC-alert surface")
```

**Dependency note:** `aiohttp` is already a transitive dep via existing webhook code; if the import fails, add `pytest-aiohttp` as a dev dep instead and use its server fixture.

- [ ] **Step 2: Run + verify coverage**

```bash
uv run pytest tests/test_integration_webhook.py -v
uv run pytest tests/test_integration_webhook.py --cov=culture.clients.shared.webhook --cov-report=term-missing -v
```

- [ ] **Step 3: Bump, format, commit, push, PR**

```bash
python3 .claude/skills/version-bump/scripts/bump.py patch
uv run black tests/test_integration_webhook.py
uv run isort tests/test_integration_webhook.py
git add tests/test_integration_webhook.py pyproject.toml culture/__init__.py CHANGELOG.md uv.lock
git commit -m "test: integration coverage for webhook fanout"
git push -u origin test/integration-webhook
bash .claude/skills/cicd/scripts/workflow.sh open-pr --title "Phase 0a: integration test for webhook fanout"
```

---

## Task 6: Telemetry integration test

**Files:**
- Create: `tests/test_integration_telemetry.py`

- [ ] **Step 1: Branch and write test**

```bash
git checkout main && git pull --quiet
git checkout -b test/integration-telemetry
```

The conftest already provides `tracing_exporter` (InMemorySpanExporter) and `metrics_reader` (InMemoryMetricReader). Use them.

```python
"""End-to-end telemetry — verify counters and spans emit during real agent
operation. Replaces tests/harness/test_telemetry_module.py and
tests/harness/test_daemon_telemetry.py at the integration layer."""

import asyncio
import os
import tempfile

import pytest

from culture.clients.claude.config import (
    AgentConfig,
    DaemonConfig,
    ServerConnConfig,
    WebhookConfig,
)
from culture.clients.claude.daemon import AgentDaemon
from culture.clients.claude.skill.irc_client import SkillClient


@pytest.mark.asyncio
async def test_irc_send_emits_counter(
    server, make_client, metrics_reader, tracing_exporter
):
    """An irc_send call increments the harness send counter and emits a span."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        webhooks=WebhookConfig(url=None),
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general"])
    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)
    await daemon.start()
    await asyncio.sleep(0.5)
    try:
        sock_path = os.path.join(sock_dir, "culture-testserv-bot.sock")
        skill = SkillClient(sock_path)
        await skill.connect()
        try:
            await skill.irc_send("#general", "telemetry probe")
        finally:
            await skill.close()
        await asyncio.sleep(0.3)
    finally:
        await daemon.stop()

    # Assert metrics — assert by exact constant name (read from telemetry.py
    # in Step 1.5 below, NOT by substring match).
    metrics_data = metrics_reader.get_metrics_data()
    metric_names = []
    for rm in metrics_data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                metric_names.append(metric.name)
    assert "<exact-counter-name>" in metric_names, f"saw metrics: {metric_names}"

    # Assert spans — same: assert by exact span name from Step 1.5.
    spans = tracing_exporter.get_finished_spans()
    span_names = [s.name for s in spans]
    assert any(s.name == "<exact-span-name>" for s in spans), \
        f"saw spans: {span_names}"
```

- [ ] **Step 1.5: Read telemetry.py for the canonical metric/span names**

Per the audit's recommendation #4, do NOT use substring matching for the assertions. Read `culture/clients/shared/telemetry.py` for the canonical constants:

```bash
grep -E "create_counter|start_as_current_span|harness\\." culture/clients/shared/telemetry.py
```

Capture the constant or string-literal names of:
- The IRC-send counter (likely `culture.harness.irc.send.count` or similar — confirm)
- The skill-call span (likely `culture.harness.skill.<verb>` or similar — confirm)

Replace `<exact-counter-name>` and `<exact-span-name>` placeholders in the test above with the real strings before opening the PR.

- [ ] **Step 2: Run + verify coverage**

```bash
uv run pytest tests/test_integration_telemetry.py -v
uv run pytest tests/test_integration_telemetry.py --cov=culture.clients.shared.telemetry --cov-report=term-missing -v
```

- [ ] **Step 3: Bump, format, commit, push, PR**

```bash
python3 .claude/skills/version-bump/scripts/bump.py patch
uv run black tests/test_integration_telemetry.py
uv run isort tests/test_integration_telemetry.py
git add tests/test_integration_telemetry.py pyproject.toml culture/__init__.py CHANGELOG.md uv.lock
git commit -m "test: integration coverage for harness telemetry"
git push -u origin test/integration-telemetry
bash .claude/skills/cicd/scripts/workflow.sh open-pr --title "Phase 0a: integration test for telemetry"
```

---

## Task 7: Per-backend supervisor restart-on-crash integration test (parameterized)

**Files:**
- Create: `tests/test_integration_supervisor.py`

Per audit recommendation #2, this test parameterizes over all four backends. Without parameterization, codex/copilot/acp `supervisor.py` stays at 0% under integration-only after Phase 0a closes (today's full-suite shows ~38–45% on the non-claude supervisors; integration-only drops them to 0%).

- [ ] **Step 1: Branch and write test**

```bash
git checkout main && git pull --quiet
git checkout -b test/integration-supervisor
```

```python
"""End-to-end supervisor restart — kill the daemon, observe the supervisor
restart it. Parameterized over all four backends. Replaces
tests/test_supervisor.py at the integration layer.

This test launches the supervisor as a real subprocess (matching how
`culture agent start` invokes it) so the integration covers the full
process-management chain, not just an in-process supervisor object."""

import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import time

import pytest


# Constant locations for Phase 1 retargeting (mirrors Task 8's BACKEND_MODULES):
BACKEND_MODULES = {
    "claude": "culture.clients.claude",
    "codex": "culture.clients.codex",
    "copilot": "culture.clients.copilot",
    "acp": "culture.clients.acp",
}


@pytest.mark.parametrize("backend", list(BACKEND_MODULES.keys()))
@pytest.mark.asyncio
async def test_supervisor_restarts_killed_daemon(backend, server, tmp_path):
    """When the daemon process is killed, the supervisor relaunches it."""
    # NOTE: read culture/cli/agent.py for how `culture agent start` builds the
    # supervisor invocation. Replicate the subprocess args here. The supervisor
    # writes a PID file; assert (a) the original daemon PID, (b) that PID dies,
    # (c) a NEW daemon PID appears in the PID file within the restart window.
    backend_module = BACKEND_MODULES[backend]
    sock_dir = tempfile.mkdtemp()
    pid_file = tmp_path / "supervisor.pid"
    daemon_pid_file = tmp_path / "daemon.pid"
    config_file = tmp_path / "agent.yaml"
    config_file.write_text(
        f"""
nick: testserv-bot-{backend}
directory: /tmp
channels: ["#general"]
server:
  host: 127.0.0.1
  port: {server.config.port}
"""
    )
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            f"{backend_module}.supervisor",
            "--config",
            str(config_file),
            "--socket-dir",
            sock_dir,
            "--daemon-pid-file",
            str(daemon_pid_file),
        ],
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        # Wait for daemon to start
        for _ in range(50):
            if daemon_pid_file.exists():
                break
            await asyncio.sleep(0.1)
        assert daemon_pid_file.exists(), \
            f"[{backend}] supervisor never wrote daemon PID file"
        original_pid = int(daemon_pid_file.read_text().strip())
        assert original_pid > 0

        # Kill the daemon
        os.kill(original_pid, signal.SIGKILL)

        # Wait for restart
        new_pid = original_pid
        for _ in range(100):
            await asyncio.sleep(0.1)
            if daemon_pid_file.exists():
                new_pid = int(daemon_pid_file.read_text().strip())
                if new_pid != original_pid:
                    break
        else:
            pytest.fail(f"[{backend}] supervisor did not restart daemon within 10s")

        assert new_pid != original_pid
        # Verify the new PID is alive
        os.kill(new_pid, 0)  # raises if not alive
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
```

**Critical for Phase 1:** the `BACKEND_MODULES` dict at the top of this file uses `culture.clients.<backend>` paths. In the Phase 1 cutover PR, those become `cultureagent.clients.<backend>`. The dict-at-the-top pattern (mirrored in Task 8) makes that one-line retargeting trivial.

- [ ] **Step 2: Run + verify**

```bash
uv run pytest tests/test_integration_supervisor.py -v --timeout=30
```

Expected: pass within ~15-20 seconds (multiple restart cycles take time).

- [ ] **Step 3: Bump, format, commit, push, PR**

```bash
python3 .claude/skills/version-bump/scripts/bump.py patch
uv run black tests/test_integration_supervisor.py
uv run isort tests/test_integration_supervisor.py
git add tests/test_integration_supervisor.py pyproject.toml culture/__init__.py CHANGELOG.md uv.lock
git commit -m "test: integration coverage for supervisor restart-on-crash"
git push -u origin test/integration-supervisor
bash .claude/skills/cicd/scripts/workflow.sh open-pr --title "Phase 0a: integration test for supervisor restart"
```

---

## Task 8: Per-backend agent_runner integration test

**Files:**
- Create: `tests/test_integration_agent_runner.py`

- [ ] **Step 1: Branch and write test**

```bash
git checkout main && git pull --quiet
git checkout -b test/integration-agent-runner
```

```python
"""End-to-end agent_runner timeout behavior, parameterized over all four
backends. Replaces tests/harness/test_agent_runner_*.py and the timeout
portion of test_record_llm_call.py at the integration layer.

Uses skip_claude=False with a deliberately-failing/timing-out underlying
SDK call so the runner's timeout path executes. The exact mechanism varies
per backend — see culture/clients/<backend>/agent_runner.py for the
SDK invocation point and the timeout constant.
"""

import asyncio
import os
import tempfile

import pytest


# Constant locations for Phase 1 retargeting:
BACKEND_MODULES = {
    "claude": "culture.clients.claude",
    "codex": "culture.clients.codex",
    "copilot": "culture.clients.copilot",
    "acp": "culture.clients.acp",
}


@pytest.mark.parametrize("backend", list(BACKEND_MODULES.keys()))
@pytest.mark.asyncio
async def test_agent_runner_respects_per_turn_timeout(backend, server, make_client):
    """Agent runner aborts a turn that exceeds the per-turn timeout."""
    # NOTE: read culture/clients/<backend>/agent_runner.py for the timeout
    # constant (PER_TURN_TIMEOUT_SECONDS or similar) and the path to inject
    # a fake-slow SDK call.
    #
    # Two test approaches, pick what works for your backend:
    # (a) Set a very low timeout (e.g. 0.1s) via DaemonConfig, then trigger a
    #     normal mention and verify the resulting irc_read shows a timeout
    #     marker (the runner's error path emits a recorded "turn timed out"
    #     observation).
    # (b) Monkeypatch the SDK's invocation function to await an
    #     uninterruptable sleep, then trigger a mention, then assert the
    #     daemon recovers and processes the next mention.
    #
    # Approach (a) is preferred — it doesn't require backend-specific patching.
    pytest.skip(f"Fill in for {backend} after reading agent_runner.py")
```

**Note on parameterization:** the test stays as `pytest.skip` until each backend's runner is read. Implement one backend at a time as a series of fixes to this same test file (still one Phase 0a PR — preferably get all four working before opening). If approach (a) doesn't work for one backend, fall back to (b) for that backend only.

- [ ] **Step 2: Run + verify (after fill-in)**

```bash
uv run pytest tests/test_integration_agent_runner.py -v --timeout=30
```

Expected: 4 tests pass (one per backend).

- [ ] **Step 3: Bump, format, commit, push, PR**

```bash
python3 .claude/skills/version-bump/scripts/bump.py patch
uv run black tests/test_integration_agent_runner.py
uv run isort tests/test_integration_agent_runner.py
git add tests/test_integration_agent_runner.py pyproject.toml culture/__init__.py CHANGELOG.md uv.lock
git commit -m "test: integration coverage for agent_runner per-turn timeout (4 backends)"
git push -u origin test/integration-agent-runner
bash .claude/skills/cicd/scripts/workflow.sh open-pr --title "Phase 0a: integration test for agent_runner timeout"
```

---

## Task 9: Coverage gate flip (closeout)

**Goal:** Bring culture's pytest `[tool.coverage.report] fail_under` from PR #362's locked baseline (`56`) to the post-Phase-0a measured floor (~73 project-wide; ~85 on `culture/clients/`). Optionally raise the SonarCloud Quality Gate threshold to match.

**Files:**
- Modify: `pyproject.toml` (`fail_under = 56` → measured post-Phase-0a value), `CHANGELOG.md`, `culture/__init__.py` (via `/version-bump minor`)
- External: SonarCloud project Quality Gate (UI operation, optional Path A)

**Note:** the SonarCloud scanner has been running on every PR since [PR #362](https://github.com/agentculture/culture/pull/362). The originally-required "wire SonarCloud first" prerequisite step has been **dropped** — that work is already on `main`.

- [ ] **Step 0a: Re-measure skill client coverage under integration-only**

Per audit recommendation #5 (row #20: `tests/test_skill_client.py` had unique coverage of `culture/clients/claude/skill/irc_client.py`), confirm that Tasks 4–6's integration tests have raised `irc_client.py` coverage above the 80% threshold:

```bash
mkdir -p /tmp/culture-tests
uv run pytest tests/test_integration_*.py \
    --cov=culture/clients/claude/skill/irc_client \
    --cov-report=term \
    -q 2>&1 | tee /tmp/culture-tests/skill-client-final.log
grep -E "irc_client.py|TOTAL" /tmp/culture-tests/skill-client-final.log
```

Expected: `culture/clients/claude/skill/irc_client.py` shows ≥80% line coverage. If <80%, **stop** and add a Task 8.5 (`tests/test_integration_skill_client.py`) before resuming Task 9.

- [ ] **Step 1: Verify coverage threshold is reachable**

```bash
git checkout main && git pull --quiet
uv run pytest -n auto --cov=culture --cov-report=term 2>&1 | tee /tmp/culture-tests/coverage-final.log
grep "TOTAL" /tmp/culture-tests/coverage-final.log
```

Expected: `TOTAL ... NN%` where NN is at or above the post-Phase-0a projection (~73). Capture the exact number — it becomes the new `fail_under` value in Step 3.

- [ ] **Step 2: Branch**

```bash
git checkout -b chore/coverage-gate-phase0a-closeout
```

- [ ] **Step 3: Ratchet `fail_under` to the measured post-Phase-0a value**

In `pyproject.toml`, find `[tool.coverage.report] fail_under = 56` (locked by PR #362) and update to the value measured in Step 1. Round down by 1 percentage point for headroom (same convention PR #362 used: measured 56.86%, locked at 56).

```toml
[tool.coverage.report]
# Locked 2026-05-09 at 56 (post-PR-#362 baseline). Ratcheted at Phase 0a
# closeout to <NN> (measured post-Phase-0a floor — see
# docs/coverage-baseline.md).
fail_under = <NN>
```

Update `docs/coverage-baseline.md` with the closeout footer (final overall coverage, per-domain numbers).

- [ ] **Step 4: Update SonarCloud Quality Gate threshold (optional, Path A)**

Two paths — pick one and document it in `docs/coverage-baseline.md`:

- **Path A (recommended):** Set a SonarCloud Quality Gate condition "Coverage on `culture/clients/**` is less than 80" — matches `docs/coverage-baseline.md`'s per-domain growth path. Done in SonarCloud's project UI at https://sonarcloud.io/project/quality_gate?id=agentculture_culture.
- **Path B:** Leave SonarCloud's default "Sonar way" gate (Coverage on New Code ≥ 80%) and rely on pytest's `--cov-fail-under` for overall floor enforcement.

Either choice works; this PR just ratchets the local pytest gate to whatever number Tasks 2–8 actually achieved.

- [ ] **Step 5: Bump version (minor — CI quality-process change)**

```bash
python3 .claude/skills/version-bump/scripts/bump.py minor
```

- [ ] **Step 6: Run tests locally to confirm the new gate passes**

```bash
uv run pytest -n auto --cov=culture --cov-report=term -v
# (the new fail_under value from Step 3's pyproject.toml edit is enforced automatically via [tool.coverage.report])
```

Expected: PASS. If it fails, you cannot land this PR — back to gate analysis.

- [ ] **Step 7: Commit, push, PR**

```bash
git add .github/workflows/tests.yml pyproject.toml culture/__init__.py CHANGELOG.md uv.lock
git commit -m "ci: enforce 95% coverage gate via pytest + SonarCloud"
git push -u origin chore/coverage-gate-95-percent
bash .claude/skills/cicd/scripts/workflow.sh open-pr --title "Phase 0a closeout: 95% coverage gate"
```

The PR description should call out the SonarCloud gate change explicitly so reviewers know the out-of-tree action is part of the change.

- [ ] **Step 8: After merge — close out Phase 0a**

Once this PR is merged, Phase 0a is complete. Next step is the Phase 0b kickoff brief to cultureagent (a separate writing-plans session, not in this plan).

Update `docs/superpowers/notes/2026-05-09-cultureagent-coverage-audit.md` with a closeout footer:

```markdown
## Closeout (post-merge)

Phase 0a complete on YYYY-MM-DD. Final overall coverage: NN.N%. Gate flipped via PR #XXX. Ready to kick off Phase 0b (cultureagent buildup brief).
```

---

## Self-review notes

- **Spec coverage check:** every behavior listed in spec §"Phase 0a — culture pre-cutover test reinforcement" has a task in this plan (attention → 2; message buffer → 3; IRC transport → 4; webhook → 5; telemetry → 6; supervisor → 7; agent_runner → 8). The two enforcement points (SonarCloud gate + `--cov-fail-under`) are both in Task 9. ✓
- **Placeholder check:** several tasks include `pytest.skip(...)` placeholders for behaviors whose surface needs reading at write time (dynamic attention levels in Task 2, IRC reconnect in Task 4, IRC alert channel in Task 5, all 4 backends in Task 8). Each skip has an explicit reading instruction (which file to consult) and a fill-in note. These are *not* TBD — they're "fill in from production code at write time" placeholders, which is the correct shape for an integration-test plan where the production API is the source of truth. ✓
- **Test consistency:** all integration tests use `server` and `make_client` fixtures; all teardown via `try/finally`; all use `skip_claude=True` (Tasks 2-7) or backend-specific runners (Task 8). ✓
- **Cross-task dependency:** Task 1's audit doc is referenced by Tasks 2-8 (line ranges, decisions). Task 1 must merge first. Tasks 2-8 are independent of each other and can run in parallel branches. Task 9 depends on Tasks 2-8 reaching the post-Phase-0a projection (~73% project-wide). ✓
- **Risk: Phase 1 retargeting.** Tasks 7 and 8 hard-code `culture.clients.<backend>` strings (subprocess args, BACKEND_MODULES dict). Phase 1's cutover PR must update these to `cultureagent.clients.<backend>`. Both task descriptions flag this; the cutover plan (separate, future) will reference these as known retargeting sites.
