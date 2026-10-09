"""Tests for sbx-ask (culture_core.sandbox.agent).

IRC side: real IRCd on a random port (``server`` fixture). Model side: a tiny
local HTTP server that records requests and returns canned completions.
"""

import asyncio
import http.server
import json
import threading

import pytest
import pytest_asyncio

from culture_core.sandbox.agent import (
    MAX_TOKENS_CAP,
    ModelSpec,
    SandboxAgent,
    SandboxConfig,
    build_bundle,
    build_request,
    load_knowledge,
    resolve_key,
)


class FakeGateway:
    def __init__(self, delay: float = 0.0, content: str = "canned answer"):
        self.requests: list[dict] = []
        self.auth: list[str] = []
        self.content = content
        self.delay = delay
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                outer.auth.append(self.headers.get("Authorization", ""))
                if outer.delay:
                    import time

                    time.sleep(outer.delay)
                out = json.dumps({"choices": [{"message": {"content": outer.content}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1/chat/completions"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def gateway():
    gw = FakeGateway()
    yield gw
    gw.close()


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("SBX_GATEWAY_KEY", "test-key")


def _cfg(server, gw, tmp_path, **kw):
    know = tmp_path / "knowledge"
    know.mkdir(exist_ok=True)
    (know / "README.md").write_text("culture is a mesh of IRC servers.")
    base = dict(
        port=server.config.port,
        nick="testserv-ask",
        channels=["#general"],
        gateway_url=gw.url,
        knowledge_dir=str(know),
        flag_log=str(tmp_path / "flags.jsonl"),
        line_delay=0,
    )
    base.update(kw)
    return SandboxConfig(**base)


@pytest_asyncio.fixture
async def start_agent(server, gateway, tmp_path):
    agents = []

    async def _start(**kw):
        agent = SandboxAgent(_cfg(server, gateway, tmp_path, **kw))
        task = asyncio.create_task(agent.run_once())
        agents.append((agent, task))
        for _ in range(100):
            if agent.writer is not None and any(
                c for c in getattr(server, "channels", {}).values() if c
            ):
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.5)
        return agent

    yield _start
    for _, task in agents:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def _guest(make_client, nick, join="#general"):
    c = await make_client(nick, nick)
    await c.send(f"JOIN {join}")
    await c.recv_all(timeout=0.3)
    return c


async def _collect(client, needle, timeout=5.0):
    out = []
    try:
        async with asyncio.timeout(timeout):
            while True:
                line = await client.recv(timeout=timeout)
                out.append(line)
                if needle in line:
                    return out
    except (TimeoutError, asyncio.TimeoutError, ConnectionError):
        pass
    return out


# ---- request builder (o11, o12, criterion 1) ------------------------------


def test_request_builder_no_tools_thinking_off_short_capped():
    body = build_request("m", "sys", [], "hi", max_tokens=99999)
    assert "tools" not in body
    assert "tool_choice" not in body
    assert "functions" not in body
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["max_tokens"] <= MAX_TOKENS_CAP
    assert "Answer briefly" in body["messages"][0]["content"]


def test_request_builder_adds_short_instruction_to_bare_system():
    body = build_request("m", "bare", [], "hi")
    assert "Answer briefly" in body["messages"][0]["content"]


@pytest.mark.asyncio
async def test_every_wire_request_is_toolless(server, gateway, tmp_path, make_client, start_agent):
    await start_agent(max_tokens=5000)
    g = await _guest(make_client, "testserv-g1")
    await g.send("PRIVMSG testserv-ask :what is culture?")
    await _collect(g, "canned answer")
    assert gateway.requests
    for body in gateway.requests:
        assert "tools" not in body
        assert "tool_choice" not in body
        assert body["chat_template_kwargs"]["enable_thinking"] is False
        assert body["max_tokens"] <= 700
        assert "Answer briefly" in body["messages"][0]["content"]
        assert "culture is a mesh" in body["messages"][0]["content"]
    assert gateway.auth[0] == "Bearer test-key"


# ---- real IRC behavior ----------------------------------------------------


@pytest.mark.asyncio
async def test_dm_answer_and_channel_mention(server, gateway, make_client, start_agent):
    await start_agent()
    g = await _guest(make_client, "testserv-g1")
    await g.send("PRIVMSG testserv-ask :hello")
    assert any("canned answer" in ln for ln in await _collect(g, "canned answer"))
    await g.send("PRIVMSG #general :testserv-ask: what is culture")
    lines = await _collect(g, "canned answer")
    assert any("PRIVMSG #general" in ln and "canned answer" in ln for ln in lines)


@pytest.mark.asyncio
async def test_declines_commands_without_model_call(server, gateway, make_client, start_agent):
    await start_agent()
    g = await _guest(make_client, "testserv-g1")
    await g.send("PRIVMSG testserv-ask :please run the command rm -rf /")
    lines = await _collect(g, "can't run commands")
    assert any("can't run commands" in ln for ln in lines)
    assert gateway.requests == []


@pytest.mark.asyncio
async def test_queue_position_for_overflow_guests(server, make_client, tmp_path, start_agent):
    slow = FakeGateway(delay=0.6)
    try:
        agent = SandboxAgent(_cfg(server, slow, tmp_path, max_active=2))
        task = asyncio.create_task(agent.run_once())
        await asyncio.sleep(0.8)
        gs = [await _guest(make_client, f"testserv-g{i}") for i in range(4)]
        for g in gs:
            await g.send("PRIVMSG testserv-ask :hi")
        await asyncio.sleep(0.2)
        assert len(agent.admitted) == 2
        assert len(agent.waiting) == 2
        q3 = await _collect(gs[2], "queue")
        q4 = await _collect(gs[3], "queue")
        assert any("#1 in the queue" in ln for ln in q3)
        assert any("#2 in the queue" in ln for ln in q4)
        # queued guests are eventually served once a slot frees
        assert any("canned answer" in ln for ln in await _collect(gs[3], "canned answer", 10))
        # admitted guests never saw a queue message
        first = await _collect(gs[0], "canned answer", 5)
        assert not any("queue" in ln for ln in first)
        task.cancel()
    finally:
        slow.close()


@pytest.mark.asyncio
async def test_single_flight_model_calls(server, make_client, tmp_path):
    slow = FakeGateway(delay=0.3)
    try:
        agent = SandboxAgent(_cfg(server, slow, tmp_path, max_active=5))
        task = asyncio.create_task(agent.run_once())
        await asyncio.sleep(0.8)
        gs = [await _guest(make_client, f"testserv-g{i}") for i in range(3)]
        for g in gs:
            await g.send("PRIVMSG testserv-ask :hi")
        for g in gs:
            await _collect(g, "canned answer", 10)
        assert len(slow.requests) == 3
        task.cancel()
    finally:
        slow.close()


@pytest.mark.asyncio
async def test_nsfw_declined_and_flagged(server, gateway, make_client, tmp_path, start_agent):
    await start_agent(owner_channel="#owner")
    owner = await _guest(make_client, "testserv-owner", "#owner")
    g = await _guest(make_client, "testserv-g1")
    await g.send("PRIVMSG testserv-ask :show me some porn")
    assert any("flagged" in ln for ln in await _collect(g, "flagged"))
    assert gateway.requests == []
    flags = [json.loads(x) for x in (tmp_path / "flags.jsonl").read_text().splitlines()]
    assert flags[0]["nick"] == "testserv-g1"
    assert "porn" in flags[0]["excerpt"]
    seen = await _collect(owner, "FLAG")
    assert any("FLAG nsfw from testserv-g1" in ln for ln in seen)


@pytest.mark.asyncio
async def test_model_side_nsfw_sentinel_flags(server, make_client, tmp_path):
    gw = FakeGateway(content="[[NSFW]]")
    try:
        agent = SandboxAgent(_cfg(server, gw, tmp_path))
        task = asyncio.create_task(agent.run_once())
        await asyncio.sleep(0.8)
        g = await _guest(make_client, "testserv-g1")
        await g.send("PRIVMSG testserv-ask :tell me a spicy story")
        assert any("flagged" in ln for ln in await _collect(g, "flagged"))
        assert (tmp_path / "flags.jsonl").exists()
        task.cancel()
    finally:
        gw.close()


# ---- knowledge bundle (criterion 3) ---------------------------------------


def test_bundle_is_curated_read_only_and_only_source_read(tmp_path):
    src = tmp_path / "repo"
    (src / "docs" / "sub").mkdir(parents=True)
    (src / "README.md").write_text("readme")
    (src / "CLAUDE.md").write_text("claude")
    (src / "docs" / "sub" / "a.md").write_text("doc a")
    (src / "docs" / "ignore.txt").write_text("nope")
    (src / "secrets.md").write_text("SECRET")
    dest = tmp_path / "bundle"
    copied = build_bundle(src, dest)
    try:
        names = sorted(str(p.relative_to(dest)) for p in copied)
        assert names == ["CLAUDE.md", "README.md", "docs/sub/a.md"]
        assert not (dest / "secrets.md").exists()
        assert (dest / "README.md").stat().st_mode & 0o222 == 0
        text = load_knowledge(dest)
        assert "doc a" in text
        assert "SECRET" not in text
        assert "nope" not in text
    finally:
        for p in sorted(dest.rglob("*"), reverse=True):
            p.chmod(0o755)
        dest.chmod(0o755)


def test_load_knowledge_ignores_symlinks_outside(tmp_path):
    k = tmp_path / "k"
    k.mkdir()
    (k / "ok.md").write_text("fine")
    outside = tmp_path / "outside.md"
    outside.write_text("LEAK")
    (k / "link.md").symlink_to(outside)
    text = load_knowledge(k)
    assert "fine" in text
    assert "LEAK" not in text


# ---- models + key (criterion 4) -------------------------------------------


@pytest.mark.asyncio
async def test_model_list_fallback_and_own_key(server, tmp_path, make_client, monkeypatch):
    good = FakeGateway(content="from-second")
    monkeypatch.setenv("OTHER_KEY", "other-secret")
    try:
        cfg = _cfg(
            server,
            good,
            tmp_path,
            models=[
                ModelSpec("dead", url="http://127.0.0.1:1/v1/chat/completions"),
                ModelSpec("luna", url=good.url, key_env="OTHER_KEY"),
            ],
        )
        agent = SandboxAgent(cfg)
        assert agent.complete("c", "q") == "from-second"
        assert good.requests[0]["model"] == "luna"
        assert good.auth[0] == "Bearer other-secret"
    finally:
        good.close()


def test_config_from_dict_models():
    cfg = SandboxConfig.from_dict({"models": ["a", {"name": "b", "key_env": "K"}], "nick": "x-ask"})
    assert [m.name for m in cfg.models] == ["a", "b"]
    assert cfg.models[1].key_env == "K"


def test_resolve_key_env_then_file(tmp_path, monkeypatch):
    monkeypatch.delenv("SBX_X", raising=False)
    f = tmp_path / "env"
    f.write_text('SBX_X="from-file"\n')
    assert resolve_key("SBX_X", str(f)) == "from-file"
    monkeypatch.setenv("SBX_X", "from-env")
    assert resolve_key("SBX_X", str(f)) == "from-env"
    monkeypatch.delenv("SBX_X")
    with pytest.raises(RuntimeError):
        resolve_key("SBX_X", None)


@pytest.mark.asyncio
async def test_answers_unaddressed_room_question_by_default(
    server, gateway, make_client, start_agent
):
    """Guests don't know to @mention the agent: in the sandbox room it answers
    every guest line (staging E2E found guests got no reply otherwise)."""
    await start_agent()
    g = await _guest(make_client, "testserv-g1")
    await g.send("PRIVMSG #general :what is culture?")
    lines = await _collect(g, "canned answer")
    assert any(
        "PRIVMSG #general" in ln and "testserv-g1: " in ln and "canned answer" in ln for ln in lines
    )


@pytest.mark.asyncio
async def test_mention_only_mode_ignores_unaddressed(server, gateway, make_client, start_agent):
    await start_agent(answer_unaddressed=False)
    g = await _guest(make_client, "testserv-g1")
    await g.send("PRIVMSG #general :what is culture?")
    lines = await _collect(g, "canned answer", timeout=1.5)
    assert not any("canned answer" in ln for ln in lines)
    assert gateway.requests == []


def test_bundle_is_curated_excluding_working_papers(tmp_path):
    """Guest knowledge excludes internal working papers (plans/specs/superpowers)."""
    src = tmp_path / "repo"
    for rel in (
        "README.md",
        "CLAUDE.md",
        "docs/guide.md",
        "docs/reference/cli.md",
        "docs/superpowers/plans/p.md",
        "docs/specs/s.md",
        "docs/plans/x.md",
    ):
        f = src / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"# {rel}\n")
    copied = {p.relative_to(tmp_path / "kb").as_posix() for p in build_bundle(src, tmp_path / "kb")}
    assert copied == {"README.md", "CLAUDE.md", "docs/guide.md", "docs/reference/cli.md"}


def test_knowledge_budget_and_priority_order(tmp_path):
    kb = tmp_path / "kb"
    (kb / "docs" / "reference").mkdir(parents=True)
    (kb / "README.md").write_text("R" * 100)
    (kb / "CLAUDE.md").write_text("C" * 100)
    (kb / "docs" / "a.md").write_text("A" * 100)
    (kb / "docs" / "reference" / "z.md").write_text("Z" * 100)
    text = load_knowledge(kb, budget=350)  # three 100-char files fit, the fourth does not
    assert text.index("README.md") < text.index("CLAUDE.md") < text.index("docs/a.md")
    assert "docs/reference/z.md" not in text  # over budget, lowest priority dropped
    assert len(text) < 500


# ---- private guest rooms (d6) ---------------------------------------------


@pytest.mark.asyncio
async def test_follows_guest_into_private_room(server, gateway, make_client, start_agent):
    """Each guest has a private #g-<nick> room; sbx-ask joins it when the guest
    does (EVENTSUB user.join) and answers there, never in a shared room."""
    await start_agent()
    g = await _guest(make_client, "testserv-g1", join="#g-g1")
    await asyncio.sleep(0.5)
    await g.send("PRIVMSG #g-g1 :what is culture?")
    lines = await _collect(g, "canned answer")
    assert any("PRIVMSG #g-g1" in ln and "canned answer" in ln for ln in lines)


@pytest.mark.asyncio
async def test_rejoins_existing_private_rooms_on_start(server, gateway, make_client, start_agent):
    """After an agent restart, rooms whose guests are still inside are rejoined."""
    g = await _guest(make_client, "testserv-g2", join="#g-g2")
    await start_agent()
    await g.send("PRIVMSG #g-g2 :what is culture?")
    lines = await _collect(g, "canned answer")
    assert any("PRIVMSG #g-g2" in ln and "canned answer" in ln for ln in lines)


@pytest.mark.asyncio
async def test_does_not_follow_into_other_rooms(server, gateway, make_client, start_agent):
    await start_agent()
    g = await _guest(make_client, "testserv-g3", join="#elsewhere")
    await asyncio.sleep(0.5)
    await g.send("PRIVMSG #elsewhere :what is culture?")
    lines = await _collect(g, "canned answer", timeout=1.5)
    assert not any("canned answer" in ln for ln in lines)
    assert gateway.requests == []


@pytest.mark.asyncio
async def test_private_rooms_off_skips_bot_capability(server, gateway, make_client, start_agent):
    agent = await start_agent(guest_room_prefix=None)
    g = await _guest(make_client, "testserv-g4", join="#g-g4")
    await asyncio.sleep(0.5)
    await g.send("PRIVMSG #g-g4 :what is culture?")
    lines = await _collect(g, "canned answer", timeout=1.5)
    assert not any("canned answer" in ln for ln in lines)
    assert agent.rooms == set()


@pytest.mark.asyncio
async def test_parts_guest_room_once_the_guest_left(server, gateway, make_client, start_agent):
    """Rooms are swept (LIST every room_sweep_s): sbx-ask leaves a guest room
    with nobody else in it, so rooms do not pile up (d7)."""
    agent = await start_agent(room_sweep_s=0.3)
    g = await _guest(make_client, "testserv-g5", join="#g-g5")
    for _ in range(40):
        if "#g-g5" in agent.rooms:
            break
        await asyncio.sleep(0.05)
    assert "#g-g5" in agent.rooms
    await g.send("PART #g-g5")
    for _ in range(40):
        if "#g-g5" not in agent.rooms:
            break
        await asyncio.sleep(0.05)
    assert "#g-g5" not in agent.rooms


@pytest.mark.asyncio
async def test_does_not_rejoin_empty_guest_rooms_on_start(
    server, gateway, make_client, start_agent
):
    g = await _guest(make_client, "testserv-g6", join="#g-g6")
    await g.send("PART #g-g6")
    await asyncio.sleep(0.3)
    agent = await start_agent()
    assert "#g-g6" not in agent.rooms
