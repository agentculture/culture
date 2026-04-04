# Ops Tooling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistence, scaffolding, fleet updates, and self-healing to culture so machines survive reboots, new nodes join the mesh easily, code rolls out with one command, and S2S links auto-reconnect.

**Architecture:** Four features built bottom-up. Self-healing (S2S reconnect) is added to the existing `IRCd`/`ServerLink` classes. A new `mesh_config.py` module handles declarative mesh config (`mesh.yaml`). A new `persistence.py` module generates platform-specific auto-start entries (systemd/launchd/schtasks). CLI gets `--foreground`, `setup`, and `update` commands.

**Tech Stack:** Python 3.11+, asyncio, pyyaml (existing dep), pytest + pytest-asyncio. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-04-02-ops-tooling-design.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `culture/server/ircd.py` | **Modify** — add `_link_retry_state`, `_maybe_retry_link()`, `cancel_link_retry()`, update `_remove_link()` and `stop()` |
| `culture/server/server_link.py` | **Modify** — add `_squit_received` flag, pass squit reason to `_remove_link`, cancel retry on handshake success |
| `culture/mesh_config.py` | **Create** — `MeshConfig` dataclasses + YAML load/save |
| `culture/persistence.py` | **Create** — cross-platform service install/uninstall/list |
| `culture/cli.py` | **Modify** — add `--foreground` flag, `setup` command, `update` command, Windows platform guards |
| `tests/test_link_reconnect.py` | **Create** — S2S auto-reconnect tests |
| `tests/test_mesh_config.py` | **Create** — mesh.yaml round-trip tests |
| `tests/test_persistence.py` | **Create** — service file generation tests |
| `docs/ops-tooling.md` | **Create** — user-facing ops documentation |
| `docs/cli.md` | **Modify** — add setup, update, --foreground |
| `culture/skills/culture/SKILL.md` | **Modify** — add ops tooling reference |

---

## Task 1: S2S Link Auto-Reconnect — Core IRCd Changes

**Files:**

- Modify: `culture/server/ircd.py` (lines 19-106)
- Test: `tests/test_link_reconnect.py`

- [ ] **Step 1: Write failing test — link drop triggers retry**

Create `tests/test_link_reconnect.py`:

```python
# tests/test_link_reconnect.py
"""Self-healing: S2S link auto-reconnect tests."""

import asyncio
import pytest

from culture.server.config import LinkConfig, ServerConfig
from culture.server.ircd import IRCd


@pytest.mark.asyncio
async def test_link_drop_triggers_retry():
    """When a linked peer disconnects (not SQUIT), IRCd schedules a retry."""
    password = "testlink123"
    config_a = ServerConfig(
        name="alpha",
        host="127.0.0.1",
        port=0,
        links=[LinkConfig(name="beta", host="127.0.0.1", port=0, password=password)],
    )
    config_b = ServerConfig(
        name="beta",
        host="127.0.0.1",
        port=0,
        links=[LinkConfig(name="alpha", host="127.0.0.1", port=0, password=password)],
    )

    server_a = IRCd(config_a)
    server_b = IRCd(config_b)
    await server_a.start()
    await server_b.start()

    server_a.config.port = server_a._server.sockets[0].getsockname()[1]
    server_b.config.port = server_b._server.sockets[0].getsockname()[1]
    config_a.links[0].port = server_b.config.port
    config_b.links[0].port = server_a.config.port

    # Link them
    await server_a.connect_to_peer("127.0.0.1", server_b.config.port, password)
    for _ in range(50):
        if "beta" in server_a.links and "alpha" in server_b.links:
            break
        await asyncio.sleep(0.05)
    assert "beta" in server_a.links

    # Kill server_b — simulates a crash (not a graceful SQUIT)
    await server_b.stop()

    # Wait for server_a to detect the drop
    for _ in range(50):
        if "beta" not in server_a.links:
            break
        await asyncio.sleep(0.05)
    assert "beta" not in server_a.links

    # server_a should have a retry task scheduled for "beta"
    assert "beta" in server_a._link_retry_state
    assert server_a._link_retry_state["beta"]["task"] is not None

    # Clean up
    server_a._link_retry_state["beta"]["task"].cancel()
    await server_a.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/spark/git/culture && python -m pytest tests/test_link_reconnect.py::test_link_drop_triggers_retry -v`

Expected: FAIL — `IRCd` has no `_link_retry_state` attribute.

- [ ] **Step 3: Add retry state and scheduling to IRCd**

In `culture/server/ircd.py`, add to `__init__` after line 33:

```python
        self._link_retry_state: dict[str, dict] = {}
```

Add these methods after the existing `_remove_link` method (after line 206):

```python
    def _maybe_retry_link(self, peer_name: str) -> None:
        """Schedule a reconnect attempt for a dropped peer link."""
        # Only retry peers we have a config for (outbound links)
        link_config = None
        for lc in self.config.links:
            if lc.name == peer_name:
                link_config = lc
                break
        if not link_config:
            return

        # Don't schedule if already retrying
        state = self._link_retry_state.get(peer_name)
        if state and state.get("task") and not state["task"].done():
            return

        if peer_name not in self._link_retry_state:
            self._link_retry_state[peer_name] = {"delay": 5.0, "task": None}

        state = self._link_retry_state[peer_name]
        state["task"] = asyncio.create_task(
            self._retry_link_loop(peer_name, link_config)
        )

    async def _retry_link_loop(self, peer_name: str, link_config) -> None:
        """Background task: retry connecting to a peer with exponential backoff."""
        log = logging.getLogger(__name__)
        state = self._link_retry_state[peer_name]
        while True:
            delay = state["delay"]
            log.info("Retrying link to %s in %.0fs", peer_name, delay)
            await asyncio.sleep(delay)

            # Peer may have reconnected inbound while we were sleeping
            if peer_name in self.links:
                log.info("Peer %s reconnected while waiting — cancelling retry", peer_name)
                self._link_retry_state.pop(peer_name, None)
                return

            try:
                await self.connect_to_peer(
                    link_config.host, link_config.port,
                    link_config.password, link_config.trust,
                )
                # Wait briefly for handshake to complete
                for _ in range(20):
                    if peer_name in self.links:
                        break
                    await asyncio.sleep(0.1)

                if peer_name in self.links:
                    log.info("Re-linked to %s", peer_name)
                    state["delay"] = 5.0
                    self._link_retry_state.pop(peer_name, None)
                    return
                else:
                    log.warning("Link to %s connected but handshake failed", peer_name)
            except Exception as e:
                log.warning("Retry link to %s failed: %s", peer_name, e)

            # Exponential backoff, cap at 120s
            state["delay"] = min(state["delay"] * 2, 120.0)

    def cancel_link_retry(self, peer_name: str) -> None:
        """Cancel any pending retry for a peer (e.g. they connected inbound)."""
        state = self._link_retry_state.pop(peer_name, None)
        if state and state.get("task") and not state["task"].done():
            state["task"].cancel()
```

Modify `_remove_link` (line 173) to accept `squit` param and trigger retry:

Replace the existing `_remove_link` method signature and add retry trigger at the end:

```python
    def _remove_link(self, link: ServerLink, *, squit: bool = False) -> None:
```

And add at the end of `_remove_link`, after line 206 (`del self.remote_clients[nick]`):

```python

        # Schedule reconnect for non-intentional disconnects
        if peer_name and not squit:
            self._maybe_retry_link(peer_name)
```

Modify `stop()` (line 93) to cancel retry tasks. Add before the `# Close all S2S links` comment:

```python
        # Cancel all link retry tasks
        for state in self._link_retry_state.values():
            if state.get("task") and not state["task"].done():
                state["task"].cancel()
        self._link_retry_state.clear()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/spark/git/culture && python -m pytest tests/test_link_reconnect.py::test_link_drop_triggers_retry -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/spark/git/culture
git add culture/server/ircd.py tests/test_link_reconnect.py
git commit -m "feat: add S2S link retry state and scheduling to IRCd"
```

---

## Task 2: S2S Link Auto-Reconnect — ServerLink SQUIT Handling

**Files:**

- Modify: `culture/server/server_link.py` (lines 18-104, 135-170, 482-484)
- Test: `tests/test_link_reconnect.py`

- [ ] **Step 1: Write failing test — SQUIT does not trigger retry**

Add to `tests/test_link_reconnect.py`:

```python
@pytest.mark.asyncio
async def test_squit_does_not_trigger_retry():
    """When a peer sends SQUIT (intentional delink), no retry is scheduled."""
    password = "testlink123"
    config_a = ServerConfig(
        name="alpha",
        host="127.0.0.1",
        port=0,
        links=[LinkConfig(name="beta", host="127.0.0.1", port=0, password=password)],
    )
    config_b = ServerConfig(
        name="beta",
        host="127.0.0.1",
        port=0,
        links=[LinkConfig(name="alpha", host="127.0.0.1", port=0, password=password)],
    )

    server_a = IRCd(config_a)
    server_b = IRCd(config_b)
    await server_a.start()
    await server_b.start()

    server_a.config.port = server_a._server.sockets[0].getsockname()[1]
    server_b.config.port = server_b._server.sockets[0].getsockname()[1]
    config_a.links[0].port = server_b.config.port
    config_b.links[0].port = server_a.config.port

    await server_a.connect_to_peer("127.0.0.1", server_b.config.port, password)
    for _ in range(50):
        if "beta" in server_a.links and "alpha" in server_b.links:
            break
        await asyncio.sleep(0.05)
    assert "beta" in server_a.links

    # Server B sends SQUIT (intentional disconnect)
    link_b_to_a = server_b.links["alpha"]
    await link_b_to_a.send_raw("SQUIT alpha :Shutting down")

    # Wait for server_a to process the SQUIT
    for _ in range(50):
        if "beta" not in server_a.links:
            break
        await asyncio.sleep(0.05)
    assert "beta" not in server_a.links

    # No retry should be scheduled — SQUIT is intentional
    assert "beta" not in server_a._link_retry_state

    await server_a.stop()
    await server_b.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/spark/git/culture && python -m pytest tests/test_link_reconnect.py::test_squit_does_not_trigger_retry -v`

Expected: FAIL — `_remove_link` is called without `squit=True` because `ServerLink` doesn't pass the flag yet.

- [ ] **Step 3: Add SQUIT flag to ServerLink**

In `culture/server/server_link.py`:

Add `self._squit_received = False` to `__init__` after line 43 (`self.last_seen_seq: int = 0`):

```python
        self._squit_received = False
```

Modify `_handle_squit` (line 482-484) to set the flag before raising:

```python
    async def _handle_squit(self, msg: Message) -> None:
        """Handle peer announcing it's delinking."""
        self._squit_received = True
        raise ConnectionError("Peer sent SQUIT")
```

Modify the `finally` block in `handle()` (line 98-104) to pass the squit flag:

```python
        finally:
            self.server._remove_link(self, squit=self._squit_received)
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except (ConnectionError, BrokenPipeError):
                pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/spark/git/culture && python -m pytest tests/test_link_reconnect.py::test_squit_does_not_trigger_retry -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/spark/git/culture
git add culture/server/server_link.py tests/test_link_reconnect.py
git commit -m "feat: distinguish SQUIT from crash in S2S link drop"
```

---

## Task 3: S2S Link Auto-Reconnect — Cancel Retry on Incoming Connection

**Files:**

- Modify: `culture/server/server_link.py` (line 170)
- Test: `tests/test_link_reconnect.py`

- [ ] **Step 1: Write failing test — incoming connection cancels outbound retry**

Add to `tests/test_link_reconnect.py`:

```python
@pytest.mark.asyncio
async def test_incoming_connection_cancels_retry():
    """If peer reconnects inbound, our outbound retry is cancelled."""
    password = "testlink123"
    config_a = ServerConfig(
        name="alpha",
        host="127.0.0.1",
        port=0,
        links=[LinkConfig(name="beta", host="127.0.0.1", port=0, password=password)],
    )
    config_b = ServerConfig(
        name="beta",
        host="127.0.0.1",
        port=0,
        links=[LinkConfig(name="alpha", host="127.0.0.1", port=0, password=password)],
    )

    server_a = IRCd(config_a)
    server_b = IRCd(config_b)
    await server_a.start()
    await server_b.start()

    server_a.config.port = server_a._server.sockets[0].getsockname()[1]
    server_b.config.port = server_b._server.sockets[0].getsockname()[1]
    config_a.links[0].port = server_b.config.port
    config_b.links[0].port = server_a.config.port

    # Link them
    await server_a.connect_to_peer("127.0.0.1", server_b.config.port, password)
    for _ in range(50):
        if "beta" in server_a.links and "alpha" in server_b.links:
            break
        await asyncio.sleep(0.05)
    assert "beta" in server_a.links

    # Kill server_b to trigger retry on server_a
    await server_b.stop()
    for _ in range(50):
        if "beta" not in server_a.links:
            break
        await asyncio.sleep(0.05)
    assert "beta" in server_a._link_retry_state

    # Restart server_b and have it connect TO server_a (inbound)
    server_b = IRCd(config_b)
    await server_b.start()
    server_b.config.port = server_b._server.sockets[0].getsockname()[1]
    config_b.links[0].port = server_a.config.port

    await server_b.connect_to_peer("127.0.0.1", server_a.config.port, password)
    for _ in range(50):
        if "beta" in server_a.links:
            break
        await asyncio.sleep(0.05)
    assert "beta" in server_a.links

    # Retry state should be cancelled
    assert "beta" not in server_a._link_retry_state

    await server_a.stop()
    await server_b.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/spark/git/culture && python -m pytest tests/test_link_reconnect.py::test_incoming_connection_cancels_retry -v`

Expected: FAIL — `cancel_link_retry` is not called during handshake.

- [ ] **Step 3: Call cancel_link_retry on successful handshake**

In `culture/server/server_link.py`, in `_try_complete_handshake`, add after line 170 (`self.server.links[self.peer_name] = self`):

```python
        self.server.cancel_link_retry(self.peer_name)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/spark/git/culture && python -m pytest tests/test_link_reconnect.py::test_incoming_connection_cancels_retry -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/spark/git/culture
git add culture/server/server_link.py tests/test_link_reconnect.py
git commit -m "feat: cancel S2S link retry on incoming peer connection"
```

---

## Task 4: S2S Link Auto-Reconnect — Retry on Initial Startup Failure

**Files:**

- Modify: `culture/cli.py` (lines 307-313)
- Test: `tests/test_link_reconnect.py`

- [ ] **Step 1: Write failing test — reconnect after initial link failure**

Add to `tests/test_link_reconnect.py`:

```python
@pytest.mark.asyncio
async def test_reconnect_after_initial_failure():
    """If initial connect_to_peer fails, server eventually reconnects when peer comes up."""
    password = "testlink123"
    config_a = ServerConfig(
        name="alpha",
        host="127.0.0.1",
        port=0,
        links=[LinkConfig(name="beta", host="127.0.0.1", port=16999, password=password)],
    )

    server_a = IRCd(config_a)
    await server_a.start()
    server_a.config.port = server_a._server.sockets[0].getsockname()[1]

    # Simulate initial connection failure (port 16999 has nothing listening)
    for lc in config_a.links:
        try:
            await server_a.connect_to_peer(lc.host, lc.port, lc.password, lc.trust)
        except Exception:
            server_a._maybe_retry_link(lc.name)

    # Retry should be scheduled
    assert "beta" in server_a._link_retry_state

    # Clean up
    server_a._link_retry_state["beta"]["task"].cancel()
    await server_a.stop()
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd /home/spark/git/culture && python -m pytest tests/test_link_reconnect.py::test_reconnect_after_initial_failure -v`

Expected: PASS (this uses methods already implemented in Task 1).

- [ ] **Step 3: Wire up initial startup retry in cli.py**

In `culture/cli.py`, modify the `_run_server` function (lines 307-313). Replace:

```python
    # Connect to configured peers
    for lc in config.links:
        try:
            await ircd.connect_to_peer(lc.host, lc.port, lc.password, lc.trust)
            logger.info("Linking to %s at %s:%d", lc.name, lc.host, lc.port)
        except Exception as e:
            logger.error("Failed to link to %s: %s", lc.name, e)
```

With:

```python
    # Connect to configured peers
    for lc in config.links:
        try:
            await ircd.connect_to_peer(lc.host, lc.port, lc.password, lc.trust)
            logger.info("Linking to %s at %s:%d", lc.name, lc.host, lc.port)
        except Exception as e:
            logger.error("Failed to link to %s: %s — will retry", lc.name, e)
            ircd._maybe_retry_link(lc.name)
```

- [ ] **Step 4: Run full self-healing test suite**

Run: `cd /home/spark/git/culture && python -m pytest tests/test_link_reconnect.py -v`

Expected: All 4 tests PASS.

- [ ] **Step 5: Run existing federation tests to verify no regression**

Run: `cd /home/spark/git/culture && python -m pytest tests/test_federation.py -v`

Expected: All existing tests PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/spark/git/culture
git add culture/cli.py tests/test_link_reconnect.py
git commit -m "feat: retry S2S links on initial startup failure"
```

---

## Task 5: Mesh Config Module

**Files:**

- Create: `culture/mesh_config.py`
- Test: `tests/test_mesh_config.py`

- [ ] **Step 1: Write failing test — mesh config round-trip**

Create `tests/test_mesh_config.py`:

```python
# tests/test_mesh_config.py
"""Tests for mesh.yaml configuration module."""

import os
import tempfile
import pytest

from culture.mesh_config import (
    MeshConfig,
    MeshServerConfig,
    MeshLinkConfig,
    MeshAgentConfig,
    load_mesh_config,
    save_mesh_config,
)


def test_mesh_config_round_trip(tmp_path):
    """Save and reload a mesh config — all fields preserved."""
    config = MeshConfig(
        server=MeshServerConfig(
            name="spark",
            host="0.0.0.0",
            port=6667,
            links=[
                MeshLinkConfig(name="thor", host="192.168.1.12", port=6667, password="secret", trust="full"),
            ],
        ),
        agents=[
            MeshAgentConfig(nick="claude", type="claude", workdir="~/projects/myproject", channels=["#general"]),
            MeshAgentConfig(nick="codex", type="codex", workdir="~/projects/other", channels=["#general", "#dev"]),
        ],
    )

    path = tmp_path / "mesh.yaml"
    save_mesh_config(config, path)
    loaded = load_mesh_config(path)

    assert loaded.server.name == "spark"
    assert loaded.server.port == 6667
    assert len(loaded.server.links) == 1
    assert loaded.server.links[0].name == "thor"
    assert loaded.server.links[0].password == "secret"
    assert loaded.server.links[0].trust == "full"
    assert len(loaded.agents) == 2
    assert loaded.agents[0].nick == "claude"
    assert loaded.agents[0].type == "claude"
    assert loaded.agents[0].workdir == "~/projects/myproject"
    assert loaded.agents[1].nick == "codex"
    assert loaded.agents[1].channels == ["#general", "#dev"]


def test_mesh_config_defaults(tmp_path):
    """Minimal config uses sensible defaults."""
    config = MeshConfig(
        server=MeshServerConfig(name="test"),
    )
    path = tmp_path / "mesh.yaml"
    save_mesh_config(config, path)
    loaded = load_mesh_config(path)

    assert loaded.server.name == "test"
    assert loaded.server.host == "0.0.0.0"
    assert loaded.server.port == 6667
    assert loaded.server.links == []
    assert loaded.agents == []


def test_mesh_config_empty_password(tmp_path):
    """Empty password is preserved (signals 'prompt during setup')."""
    config = MeshConfig(
        server=MeshServerConfig(
            name="spark",
            links=[MeshLinkConfig(name="thor", host="1.2.3.4", port=6667, password="")],
        ),
    )
    path = tmp_path / "mesh.yaml"
    save_mesh_config(config, path)
    loaded = load_mesh_config(path)

    assert loaded.server.links[0].password == ""


def test_mesh_config_file_not_found():
    """Loading a missing file raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_mesh_config("/nonexistent/mesh.yaml")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/spark/git/culture && python -m pytest tests/test_mesh_config.py -v`

Expected: FAIL — `culture.mesh_config` module does not exist.

- [ ] **Step 3: Implement mesh_config.py**

Create `culture/mesh_config.py`:

```python
"""Declarative mesh configuration (mesh.yaml)."""

from __future__ import annotations

import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml


@dataclass
class MeshLinkConfig:
    """A peer server to link to."""

    name: str
    host: str
    port: int = 6667
    password: str = ""
    trust: str = "full"


@dataclass
class MeshServerConfig:
    """Local server settings."""

    name: str
    host: str = "0.0.0.0"
    port: int = 6667
    links: list[MeshLinkConfig] = field(default_factory=list)


@dataclass
class MeshAgentConfig:
    """An agent to run on this machine."""

    nick: str = ""
    type: str = "claude"
    workdir: str = "."
    channels: list[str] = field(default_factory=lambda: ["#general"])


@dataclass
class MeshConfig:
    """Top-level mesh configuration for one machine."""

    server: MeshServerConfig = field(default_factory=lambda: MeshServerConfig(name="culture"))
    agents: list[MeshAgentConfig] = field(default_factory=list)


DEFAULT_MESH_PATH = os.path.expanduser("~/.culture/mesh.yaml")


def load_mesh_config(path: str | Path = DEFAULT_MESH_PATH) -> MeshConfig:
    """Load mesh config from a YAML file."""
    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    server_raw = raw.get("server", {})
    links = [MeshLinkConfig(**lc) for lc in server_raw.pop("links", [])]
    server = MeshServerConfig(**server_raw, links=links)

    agents = [MeshAgentConfig(**a) for a in raw.get("agents", [])]

    return MeshConfig(server=server, agents=agents)


def save_mesh_config(config: MeshConfig, path: str | Path = DEFAULT_MESH_PATH) -> None:
    """Serialize mesh config to YAML and write atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = asdict(config)
    yaml_str = yaml.dump(data, default_flow_style=False, sort_keys=False)

    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".yaml.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(yaml_str)
        os.replace(tmp_path, str(path))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/spark/git/culture && python -m pytest tests/test_mesh_config.py -v`

Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/spark/git/culture
git add culture/mesh_config.py tests/test_mesh_config.py
git commit -m "feat: add mesh_config module for declarative mesh.yaml"
```

---

## Task 6: Persistence Module — Platform Auto-Start

**Files:**

- Create: `culture/persistence.py`
- Test: `tests/test_persistence.py`

- [ ] **Step 1: Write failing test — service file generation**

Create `tests/test_persistence.py`:

```python
# tests/test_persistence.py
"""Tests for platform-specific auto-start service generation."""

import os
import sys
from unittest.mock import patch

import pytest

from culture.persistence import (
    get_platform,
    _build_systemd_unit,
    _build_launchd_plist,
    _build_windows_bat,
    install_service,
    uninstall_service,
    list_services,
)


def test_get_platform_linux():
    with patch.object(sys, "platform", "linux"):
        assert get_platform() == "linux"


def test_get_platform_macos():
    with patch.object(sys, "platform", "darwin"):
        assert get_platform() == "macos"


def test_get_platform_windows():
    with patch.object(sys, "platform", "win32"):
        assert get_platform() == "windows"


def test_build_systemd_unit():
    unit = _build_systemd_unit(
        name="culture-server-spark",
        command=["culture", "server", "start", "--foreground", "--name", "spark"],
        description="culture server spark",
    )
    assert "[Unit]" in unit
    assert "Description=culture server spark" in unit
    assert "ExecStart=culture server start --foreground --name spark" in unit
    assert "Restart=on-failure" in unit
    assert "WantedBy=default.target" in unit


def test_build_launchd_plist():
    plist = _build_launchd_plist(
        name="com.culture.server-spark",
        command=["culture", "server", "start", "--foreground", "--name", "spark"],
        description="culture server spark",
    )
    assert "<key>Label</key>" in plist
    assert "com.culture.server-spark" in plist
    assert "<string>culture</string>" in plist
    assert "<key>KeepAlive</key>" in plist
    assert "<true/>" in plist


def test_build_windows_bat():
    bat = _build_windows_bat(
        command=["culture", "server", "start", "--foreground", "--name", "spark"],
    )
    assert ":loop" in bat
    assert "culture server start --foreground --name spark" in bat
    assert "timeout /t 5" in bat
    assert "goto loop" in bat


def test_install_service_linux(tmp_path):
    """Install writes a systemd unit file and returns its path."""
    unit_dir = tmp_path / "systemd" / "user"
    with patch("culture.persistence.get_platform", return_value="linux"), \
         patch("culture.persistence._systemd_user_dir", return_value=unit_dir), \
         patch("culture.persistence._run_cmd"):
        path = install_service(
            "culture-server-spark",
            ["culture", "server", "start", "--foreground", "--name", "spark"],
            "culture server spark",
        )
    assert path.exists()
    assert path.name == "culture-server-spark.service"
    content = path.read_text()
    assert "ExecStart=" in content


def test_list_services_linux(tmp_path):
    """list_services returns installed service names."""
    unit_dir = tmp_path / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    (unit_dir / "culture-server-spark.service").write_text("[Unit]\n")
    (unit_dir / "culture-agent-spark-claude.service").write_text("[Unit]\n")
    (unit_dir / "unrelated.service").write_text("[Unit]\n")

    with patch("culture.persistence.get_platform", return_value="linux"), \
         patch("culture.persistence._systemd_user_dir", return_value=unit_dir):
        services = list_services()

    assert "culture-server-spark" in services
    assert "culture-agent-spark-claude" in services
    assert "unrelated" not in services
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/spark/git/culture && python -m pytest tests/test_persistence.py -v`

Expected: FAIL — `culture.persistence` module does not exist.

- [ ] **Step 3: Implement persistence.py**

Create `culture/persistence.py`:

```python
"""Platform-specific auto-start service generation."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

LOG_DIR = os.path.expanduser("~/.culture/logs")


def get_platform() -> str:
    """Detect the current platform."""
    if sys.platform == "darwin":
        return "macos"
    elif sys.platform == "win32":
        return "windows"
    return "linux"


def _systemd_user_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def _launchd_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _windows_service_dir() -> Path:
    return Path(os.path.expandvars(r"%USERPROFILE%\.culture\services"))


def _run_cmd(args: list[str]) -> None:
    """Run a command, suppressing output."""
    subprocess.run(args, check=False, capture_output=True)


# ---------------------------------------------------------------------------
# Builders — generate file content
# ---------------------------------------------------------------------------

def _build_systemd_unit(name: str, command: list[str], description: str) -> str:
    exec_start = " ".join(command)
    return (
        f"[Unit]\n"
        f"Description={description}\n"
        f"\n"
        f"[Service]\n"
        f"Type=simple\n"
        f"ExecStart={exec_start}\n"
        f"Restart=on-failure\n"
        f"RestartSec=5\n"
        f"\n"
        f"[Install]\n"
        f"WantedBy=default.target\n"
    )


def _build_launchd_plist(name: str, command: list[str], description: str) -> str:
    args = "\n".join(f"        <string>{arg}</string>" for arg in command)
    log_path = os.path.join(LOG_DIR, f"{name}.log")
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"'
        f' "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        f'<plist version="1.0">\n'
        f"<dict>\n"
        f"    <key>Label</key>\n"
        f"    <string>{name}</string>\n"
        f"    <key>ProgramArguments</key>\n"
        f"    <array>\n"
        f"{args}\n"
        f"    </array>\n"
        f"    <key>RunAtLoad</key>\n"
        f"    <true/>\n"
        f"    <key>KeepAlive</key>\n"
        f"    <true/>\n"
        f"    <key>StandardOutPath</key>\n"
        f"    <string>{log_path}</string>\n"
        f"    <key>StandardErrorPath</key>\n"
        f"    <string>{log_path}</string>\n"
        f"</dict>\n"
        f"</plist>\n"
    )


def _build_windows_bat(command: list[str]) -> str:
    cmd_line = " ".join(command)
    return (
        f"@echo off\n"
        f":loop\n"
        f"{cmd_line}\n"
        f"timeout /t 5\n"
        f"goto loop\n"
    )


# ---------------------------------------------------------------------------
# Install / Uninstall / List
# ---------------------------------------------------------------------------

def install_service(name: str, command: list[str], description: str) -> Path:
    """Generate and install a platform-specific auto-start entry."""
    platform = get_platform()

    if platform == "linux":
        unit_dir = _systemd_user_dir()
        unit_dir.mkdir(parents=True, exist_ok=True)
        path = unit_dir / f"{name}.service"
        path.write_text(_build_systemd_unit(name, command, description))
        _run_cmd(["systemctl", "--user", "daemon-reload"])
        _run_cmd(["systemctl", "--user", "enable", name])
        return path

    elif platform == "macos":
        agent_dir = _launchd_dir()
        agent_dir.mkdir(parents=True, exist_ok=True)
        plist_name = f"com.culture.{name}"
        path = agent_dir / f"{plist_name}.plist"
        path.write_text(_build_launchd_plist(plist_name, command, description))
        _run_cmd(["launchctl", "load", str(path)])
        return path

    elif platform == "windows":
        svc_dir = _windows_service_dir()
        svc_dir.mkdir(parents=True, exist_ok=True)
        bat_path = svc_dir / f"{name}.bat"
        bat_path.write_text(_build_windows_bat(command))
        _run_cmd([
            "schtasks", "/Create",
            "/TN", f"culture\\{name}",
            "/TR", str(bat_path),
            "/SC", "ONLOGON",
            "/F",
        ])
        return bat_path

    raise RuntimeError(f"Unsupported platform: {platform}")


def uninstall_service(name: str) -> None:
    """Remove a platform-specific auto-start entry."""
    platform = get_platform()

    if platform == "linux":
        _run_cmd(["systemctl", "--user", "disable", name])
        _run_cmd(["systemctl", "--user", "stop", name])
        path = _systemd_user_dir() / f"{name}.service"
        if path.exists():
            path.unlink()
        _run_cmd(["systemctl", "--user", "daemon-reload"])

    elif platform == "macos":
        plist_name = f"com.culture.{name}"
        path = _launchd_dir() / f"{plist_name}.plist"
        if path.exists():
            _run_cmd(["launchctl", "unload", str(path)])
            path.unlink()

    elif platform == "windows":
        _run_cmd(["schtasks", "/Delete", "/TN", f"culture\\{name}", "/F"])
        bat_path = _windows_service_dir() / f"{name}.bat"
        if bat_path.exists():
            bat_path.unlink()


def list_services() -> list[str]:
    """Return names of installed culture auto-start services."""
    platform = get_platform()
    names = []

    if platform == "linux":
        unit_dir = _systemd_user_dir()
        if unit_dir.exists():
            for f in unit_dir.iterdir():
                if f.name.startswith("culture-") and f.name.endswith(".service"):
                    names.append(f.stem)

    elif platform == "macos":
        agent_dir = _launchd_dir()
        if agent_dir.exists():
            for f in agent_dir.iterdir():
                if f.name.startswith("com.culture.") and f.name.endswith(".plist"):
                    names.append(f.stem.removeprefix("com.culture."))

    elif platform == "windows":
        svc_dir = _windows_service_dir()
        if svc_dir.exists():
            for f in svc_dir.iterdir():
                if f.name.startswith("culture-") and f.name.endswith(".bat"):
                    names.append(f.stem)

    return names
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/spark/git/culture && python -m pytest tests/test_persistence.py -v`

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/spark/git/culture
git add culture/persistence.py tests/test_persistence.py
git commit -m "feat: add cross-platform persistence module"
```

---

## Task 7: CLI --foreground Flag

**Files:**

- Modify: `culture/cli.py` (lines 87-94, 247-294, 467-525, 605-634, 325-356, 669-728)

- [ ] **Step 1: Add --foreground to server start parser**

In `culture/cli.py`, after line 94 (the `--link` argument on `srv_start`), add:

```python
    srv_start.add_argument(
        "--foreground", action="store_true",
        help="Run in foreground (for service managers)",
    )
```

- [ ] **Step 2: Implement foreground mode for server start**

Replace `_server_start` (lines 247-294) with:

```python
def _server_start(args: argparse.Namespace) -> None:
    pid_name = f"server-{args.name}"

    # Check if already running
    existing = read_pid(pid_name)
    if existing and is_process_alive(existing):
        print(f"Server '{args.name}' is already running (PID {existing})")
        sys.exit(1)

    if args.foreground:
        # Foreground mode — run directly (for service managers)
        write_pid(pid_name, os.getpid())
        os.makedirs(LOG_DIR, exist_ok=True)
        log_path = os.path.join(LOG_DIR, f"server-{args.name}.log")
        logging.basicConfig(
            filename=log_path,
            level=logging.INFO,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
        )
        print(f"Server '{args.name}' starting in foreground (PID {os.getpid()})")
        print(f"  Listening on {args.host}:{args.port}")
        try:
            asyncio.run(_run_server(args.name, args.host, args.port, args.link))
        finally:
            remove_pid(pid_name)
        return

    if sys.platform == "win32":
        print("Daemon mode not supported on Windows. Use --foreground.", file=sys.stderr)
        sys.exit(1)

    # Fork to daemonize
    pid = os.fork()
    if pid > 0:
        # Parent: wait briefly to check child started, then exit
        time.sleep(0.2)
        if is_process_alive(pid):
            print(f"Server '{args.name}' started (PID {pid})")
            print(f"  Listening on {args.host}:{args.port}")
            print(f"  Logs: {LOG_DIR}/server-{args.name}.log")
        else:
            print(f"Server '{args.name}' failed to start", file=sys.stderr)
            sys.exit(1)
        return

    # Child: detach from parent session
    os.setsid()

    # Redirect stdout/stderr to log file
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, f"server-{args.name}.log")
    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
    os.close(log_fd)

    # Close stdin
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)

    # Write PID file
    write_pid(pid_name, os.getpid())

    # Run the server
    try:
        asyncio.run(_run_server(args.name, args.host, args.port, args.link))
    finally:
        remove_pid(pid_name)
        os._exit(0)
```

- [ ] **Step 3: Add --foreground to start parser**

In `culture/cli.py`, after line 114 (the `--config` argument on `start_parser`), add:

```python
    start_parser.add_argument(
        "--foreground", action="store_true",
        help="Run in foreground (for service managers)",
    )
```

- [ ] **Step 4: Implement foreground mode for agent start**

In `_cmd_start` (around line 518-525), replace the block that decides single vs multi:

```python
    if len(agents) == 1:
        # Run in foreground (single agent)
        agent = agents[0]
        print(f"Starting agent {agent.nick}...")
        asyncio.run(_run_single_agent(config, agent))
    else:
        if getattr(args, "foreground", False):
            print("--foreground requires a single agent nick, not --all", file=sys.stderr)
            sys.exit(1)
        if sys.platform == "win32":
            print("Multi-agent daemon mode not supported on Windows. Start agents individually with --foreground.", file=sys.stderr)
            sys.exit(1)
        # Fork each agent into background
        _run_multi_agents(config, agents)
```

- [ ] **Step 5: Add Windows platform guards for SIGKILL**

In `_server_stop` (around line 350-352), replace:

```python
    # Force kill
    print(f"Server '{args.name}' did not stop gracefully, sending SIGKILL")
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
```

With:

```python
    # Force kill
    if sys.platform == "win32":
        print(f"Server '{args.name}' did not stop gracefully, terminating")
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    else:
        print(f"Server '{args.name}' did not stop gracefully, sending SIGKILL")
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
```

Apply the same pattern in `_stop_agent` (around line 723-727):

```python
    if sys.platform == "win32":
        print(f"Agent '{nick}' did not stop gracefully, terminating")
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    else:
        print(f"Agent '{nick}' did not stop gracefully, sending SIGKILL")
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
```

- [ ] **Step 6: Run existing tests**

Run: `cd /home/spark/git/culture && python -m pytest tests/ -v --timeout=30`

Expected: All existing tests PASS (--foreground is additive, doesn't break existing behavior).

- [ ] **Step 7: Commit**

```bash
cd /home/spark/git/culture
git add culture/cli.py
git commit -m "feat: add --foreground flag and Windows platform guards"
```

---

## Task 8: CLI `setup` Command

**Files:**

- Modify: `culture/cli.py`

- [ ] **Step 1: Add setup subparser to _build_parser**

In `culture/cli.py`, add after the overview parser (before `return parser` on line 184):

```python
    # -- setup subcommand --------------------------------------------------
    setup_parser = sub.add_parser("setup", help="Set up mesh from mesh.yaml")
    setup_parser.add_argument(
        "--config", default=os.path.expanduser("~/.culture/mesh.yaml"),
        help="Path to mesh.yaml",
    )
    setup_parser.add_argument(
        "--uninstall", action="store_true",
        help="Remove auto-start entries and stop services",
    )
```

- [ ] **Step 2: Add setup to dispatch table**

In the `dispatch` dict (around line 201-215), add:

```python
            "setup": _cmd_setup,
```

- [ ] **Step 3: Implement _cmd_setup**

Add after the overview command handler section in `cli.py`:

```python
# -----------------------------------------------------------------------
# Setup — mesh.yaml → auto-start services
# -----------------------------------------------------------------------

def _cmd_setup(args: argparse.Namespace) -> None:
    import getpass
    from culture.mesh_config import load_mesh_config, save_mesh_config
    from culture.persistence import install_service, uninstall_service, list_services

    try:
        mesh = load_mesh_config(args.config)
    except FileNotFoundError:
        print(f"Mesh config not found: {args.config}", file=sys.stderr)
        print("Create it manually or ask your AI agent to generate it.", file=sys.stderr)
        sys.exit(1)

    server_name = mesh.server.name

    if args.uninstall:
        # Stop and remove all services
        print("Uninstalling culture services...")
        for svc in list_services():
            print(f"  Removing {svc}")
            uninstall_service(svc)
        # Stop running processes
        _server_stop_by_name(server_name)
        for agent in mesh.agents:
            full_nick = f"{server_name}-{agent.nick}"
            _stop_agent(full_nick)
        print("Done.")
        return

    # Prompt for missing link passwords
    changed = False
    for link in mesh.server.links:
        if not link.password:
            link.password = getpass.getpass(f"Link password for {link.name}: ")
            changed = True
    if changed:
        save_mesh_config(mesh, args.config)
        print(f"Passwords saved to {args.config}")

    # Generate agents.yaml for each workdir
    from culture.clients.claude.config import (
        AgentConfig as BaseAgentConfig,
        DaemonConfig,
        ServerConnConfig,
        save_config,
        load_config_or_default,
    )

    workdir_agents: dict[str, list] = {}
    for agent in mesh.agents:
        workdir = os.path.expanduser(agent.workdir)
        workdir_agents.setdefault(workdir, []).append(agent)

    for workdir, agents in workdir_agents.items():
        os.makedirs(workdir, exist_ok=True)
        config_path = os.path.join(workdir, ".culture", "agents.yaml")
        os.makedirs(os.path.dirname(config_path), exist_ok=True)

        agent_configs = []
        for a in agents:
            full_nick = f"{server_name}-{a.nick}"
            agent_configs.append(BaseAgentConfig(
                nick=full_nick,
                agent=a.type,
                directory=workdir,
                channels=list(a.channels),
            ))

        daemon_config = DaemonConfig(
            server=ServerConnConfig(name=server_name, host="localhost", port=mesh.server.port),
            agents=agent_configs,
        )
        save_config(config_path, daemon_config)
        print(f"  Wrote {config_path}")

    # Build link args for server command
    link_args = []
    for link in mesh.server.links:
        link_args.extend([
            "--link", f"{link.name}:{link.host}:{link.port}:{link.password}:{link.trust}"
        ])

    # Install auto-start services
    culture_bin = shutil.which("culture") or "culture"

    server_cmd = [
        culture_bin, "server", "start", "--foreground",
        "--name", server_name,
        "--host", mesh.server.host,
        "--port", str(mesh.server.port),
    ] + link_args
    svc_name = f"culture-server-{server_name}"
    path = install_service(svc_name, server_cmd, f"culture server {server_name}")
    print(f"  Installed {svc_name} → {path}")

    for agent in mesh.agents:
        full_nick = f"{server_name}-{agent.nick}"
        workdir = os.path.expanduser(agent.workdir)
        config_path = os.path.join(workdir, ".culture", "agents.yaml")
        agent_cmd = [culture_bin, "start", full_nick, "--foreground", "--config", config_path]
        agent_svc = f"culture-agent-{full_nick}"
        path = install_service(agent_svc, agent_cmd, f"culture agent {full_nick}")
        print(f"  Installed {agent_svc} → {path}")

    print(f"\nSetup complete for mesh node '{server_name}'.")
    print(f"Services installed. Start with your service manager or reboot.")


def _server_stop_by_name(name: str) -> None:
    """Stop a server by name (helper for setup --uninstall)."""
    pid_name = f"server-{name}"
    pid = read_pid(pid_name)
    if pid and is_process_alive(pid):
        os.kill(pid, signal.SIGTERM)
        for _ in range(50):
            if not is_process_alive(pid):
                break
            time.sleep(0.1)
        remove_pid(pid_name)
```

Add `import shutil` to the imports at the top of `cli.py`.

- [ ] **Step 4: Run to verify setup command is wired up**

Run: `cd /home/spark/git/culture && python -m culture.cli setup --help`

Expected: Shows help text for the setup command with `--config` and `--uninstall` options.

- [ ] **Step 5: Commit**

```bash
cd /home/spark/git/culture
git add culture/cli.py
git commit -m "feat: add 'culture setup' command for mesh bootstrapping"
```

---

## Task 9: CLI `update` Command

**Files:**

- Modify: `culture/cli.py`

- [ ] **Step 1: Add update subparser to _build_parser**

In `culture/cli.py`, add after the setup parser:

```python
    # -- update subcommand -------------------------------------------------
    update_parser = sub.add_parser("update", help="Upgrade and restart the mesh")
    update_parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would happen without executing",
    )
    update_parser.add_argument(
        "--skip-upgrade", action="store_true",
        help="Just restart, don't upgrade the package",
    )
    update_parser.add_argument(
        "--config", default=os.path.expanduser("~/.culture/mesh.yaml"),
        help="Path to mesh.yaml",
    )
```

- [ ] **Step 2: Add update to dispatch table**

In the `dispatch` dict, add:

```python
            "update": _cmd_update,
```

- [ ] **Step 3: Implement _cmd_update**

Add after the `_cmd_setup` section:

```python
# -----------------------------------------------------------------------
# Update — upgrade + restart
# -----------------------------------------------------------------------

def _cmd_update(args: argparse.Namespace) -> None:
    from culture.mesh_config import load_mesh_config
    import culture

    try:
        mesh = load_mesh_config(args.config)
    except FileNotFoundError:
        print(f"Mesh config not found: {args.config}", file=sys.stderr)
        sys.exit(1)

    server_name = mesh.server.name
    old_version = getattr(culture, "__version__", "unknown")

    if not args.skip_upgrade:
        print(f"Current version: {old_version}")
        if args.dry_run:
            print("[dry-run] Would run: uv tool upgrade culture")
            print("[dry-run] Would re-exec with --skip-upgrade")
            return

        # Upgrade the package
        uv = shutil.which("uv")
        if uv:
            print("Upgrading via uv...")
            result = subprocess.run(
                [uv, "tool", "upgrade", "culture"],
                capture_output=True, text=True,
            )
            print(result.stdout.strip() if result.stdout else "")
            if result.returncode != 0:
                print(f"uv upgrade failed: {result.stderr}", file=sys.stderr)
                sys.exit(1)
        else:
            pip = shutil.which("pip") or shutil.which("pip3")
            if pip:
                print("Upgrading via pip...")
                result = subprocess.run(
                    [pip, "install", "--upgrade", "culture"],
                    capture_output=True, text=True,
                )
                if result.returncode != 0:
                    print(f"pip upgrade failed: {result.stderr}", file=sys.stderr)
                    sys.exit(1)
            else:
                print("Neither uv nor pip found", file=sys.stderr)
                sys.exit(1)

        # Re-exec with new binary so restart uses new code
        culture_bin = shutil.which("culture") or "culture"
        reexec_args = [culture_bin, "update", "--skip-upgrade", "--config", args.config]
        print("Re-executing with updated code...")
        if sys.platform == "win32":
            # Windows: subprocess instead of execv
            sys.exit(subprocess.run(reexec_args).returncode)
        else:
            os.execv(culture_bin, reexec_args)

    # --skip-upgrade path: restart everything
    print(f"Restarting mesh node '{server_name}'...")

    if args.dry_run:
        for agent in mesh.agents:
            print(f"[dry-run] Would stop agent {server_name}-{agent.nick}")
        print(f"[dry-run] Would stop server {server_name}")
        print(f"[dry-run] Would regenerate auto-start entries")
        print(f"[dry-run] Would start server {server_name}")
        for agent in mesh.agents:
            print(f"[dry-run] Would start agent {server_name}-{agent.nick}")
        return

    # Stop agents
    for agent in mesh.agents:
        full_nick = f"{server_name}-{agent.nick}"
        print(f"  Stopping {full_nick}...")
        _stop_agent(full_nick)

    # Stop server
    print(f"  Stopping server {server_name}...")
    _server_stop_by_name(server_name)

    # Regenerate auto-start entries
    from culture.persistence import install_service

    culture_bin = shutil.which("culture") or "culture"
    link_args = []
    for link in mesh.server.links:
        link_args.extend([
            "--link", f"{link.name}:{link.host}:{link.port}:{link.password}:{link.trust}"
        ])

    server_cmd = [
        culture_bin, "server", "start", "--foreground",
        "--name", server_name,
        "--host", mesh.server.host,
        "--port", str(mesh.server.port),
    ] + link_args
    install_service(f"culture-server-{server_name}", server_cmd, f"culture server {server_name}")

    for agent in mesh.agents:
        full_nick = f"{server_name}-{agent.nick}"
        workdir = os.path.expanduser(agent.workdir)
        config_path = os.path.join(workdir, ".culture", "agents.yaml")
        agent_cmd = [culture_bin, "start", full_nick, "--foreground", "--config", config_path]
        install_service(f"culture-agent-{full_nick}", agent_cmd, f"culture agent {full_nick}")

    # Start server
    print(f"  Starting server {server_name}...")
    # Use subprocess so it runs as a new process with the updated code
    server_start_args = [
        culture_bin, "server", "start",
        "--name", server_name,
        "--host", mesh.server.host,
        "--port", str(mesh.server.port),
    ] + link_args
    subprocess.run(server_start_args, check=False)

    # Wait for server to be ready
    import socket as _socket
    for _ in range(50):
        try:
            with _socket.create_connection(("localhost", mesh.server.port), timeout=1):
                break
        except (ConnectionRefusedError, OSError):
            time.sleep(0.1)

    # Start agents
    for agent in mesh.agents:
        full_nick = f"{server_name}-{agent.nick}"
        workdir = os.path.expanduser(agent.workdir)
        config_path = os.path.join(workdir, ".culture", "agents.yaml")
        print(f"  Starting {full_nick}...")
        subprocess.run(
            [culture_bin, "start", full_nick, "--config", config_path],
            check=False,
        )

    new_version = "unknown"
    try:
        result = subprocess.run(
            [culture_bin, "--version"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            new_version = result.stdout.strip()
    except Exception:
        pass

    print(f"\nUpdate complete. All services restarted.")
```

Add `import subprocess` to the imports at the top of `cli.py` (if not already present).

- [ ] **Step 4: Run to verify update command is wired up**

Run: `cd /home/spark/git/culture && python -m culture.cli update --help`

Expected: Shows help text for the update command with `--dry-run`, `--skip-upgrade`, and `--config` options.

- [ ] **Step 5: Commit**

```bash
cd /home/spark/git/culture
git add culture/cli.py
git commit -m "feat: add 'culture update' command for fleet updates"
```

---

## Task 10: Documentation and Skill Updates

**Files:**

- Create: `docs/ops-tooling.md`
- Modify: `docs/cli.md`
- Modify: `culture/skills/culture/SKILL.md`

- [ ] **Step 1: Write ops-tooling.md**

Create `docs/ops-tooling.md` covering:

- `mesh.yaml` format and schema
- `culture setup` command usage
- `culture update` command usage
- Platform-specific auto-start details (systemd, launchd, Windows scheduled tasks)
- `--foreground` flag
- S2S auto-reconnect behavior (exponential backoff, SQUIT distinction)

- [ ] **Step 2: Update docs/cli.md**

Add entries for `setup`, `update`, and `--foreground` flag on `server start` and `start`.

- [ ] **Step 3: Update culture admin skill**

In `culture/skills/culture/SKILL.md`, add an "Ops Tooling" section covering `setup`, `update`, `--foreground`, and auto-reconnect.

- [ ] **Step 4: Run markdownlint**

Run: `markdownlint-cli2 "docs/ops-tooling.md" "docs/cli.md" "culture/skills/culture/SKILL.md"`

Fix any lint issues.

- [ ] **Step 5: Commit**

```bash
cd /home/spark/git/culture
git add docs/ops-tooling.md docs/cli.md culture/skills/culture/SKILL.md
git commit -m "docs: add ops tooling documentation and update CLI reference"
```

---

## Task 11: Full Regression Test

- [ ] **Step 1: Run the complete test suite**

Run: `cd /home/spark/git/culture && python -m pytest tests/ -v --timeout=30`

Expected: All tests PASS, including new ones (test_link_reconnect, test_mesh_config, test_persistence).

- [ ] **Step 2: Verify CLI commands**

Run:

```bash
cd /home/spark/git/culture
python -m culture.cli setup --help
python -m culture.cli update --help
python -m culture.cli server start --help  # should show --foreground
python -m culture.cli start --help          # should show --foreground
```

Expected: All commands show expected help text with new flags.

- [ ] **Step 3: Commit any fixes**

If any issues found, fix and commit.
