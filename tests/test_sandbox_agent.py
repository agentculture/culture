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
    fit_prompt,
    knowledge_budget,
    load_knowledge,
    resolve_key,
    system_prompt,
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
        from culture_core.sandbox.agent import _Job

        assert agent.complete(_Job("g", "g", "g", "", "q")) == "from-second"
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
    text = load_knowledge(kb, budget=400)  # three 100-char files + headers fit, the fourth does not
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


# --- several agents: the loop guard ------------------------------------------


def _guarded(tmp_path, **kw) -> tuple[SandboxAgent, list]:
    """An agent whose admitted jobs are recorded instead of run."""
    cfg = SandboxConfig(
        nick="sbx-ask",
        peer_agents={"sbx-bolt": "a second model"},
        knowledge_dir=str(tmp_path),
        **kw,
    )
    agent = SandboxAgent(cfg, knowledge="")
    agent.rooms.add("#g-1")  # the guest room these tests talk in
    jobs: list = []

    async def admit(job):
        jobs.append(job)

    agent._admit = admit
    return agent, jobs


@pytest.mark.asyncio
async def test_human_untagged_answered_tagging_a_peer_is_left_to_it(tmp_path):
    agent, jobs = _guarded(tmp_path)
    await agent.handle("sbx-ann", "#g-1", "what is culture?")
    await agent.handle("sbx-ann", "#g-1", "@sbx-bolt what do you think?")
    await agent.handle("sbx-ann", "#g-1", "@sbx-bolt and @sbx-ask, both of you?")
    assert [j.question for j in jobs] == ["what is culture?", "@sbx-bolt and both of you?"]


@pytest.mark.asyncio
async def test_peer_reply_prefix_or_bare_nick_is_not_a_tag(tmp_path):
    """The leading "<nick>: " every reply starts with, or a bare nick (an
    agent telling a guest about another agent), must not trigger an answer,
    or two agents answer each other forever (seen in a two-agent demo)."""
    agent, jobs = _guarded(tmp_path)
    await agent.handle("sbx-bolt", "#g-1", "sbx-ask: I agree with you.")
    await agent.handle(
        "sbx-bolt", "#g-1", "sbx-ann: for docs questions, sbx-ask is the one to ask."
    )
    assert jobs == []
    await agent.handle("sbx-bolt", "#g-1", "sbx-ask: good point. @sbx-ask what about docs?")
    assert len(jobs) == 1


@pytest.mark.asyncio
async def test_one_agent_hop_per_human_message(tmp_path):
    agent, jobs = _guarded(tmp_path)
    await agent.handle("sbx-ann", "#g-1", "@sbx-bolt ask sbx-ask for me")  # left to bolt
    for _ in range(3):
        await agent.handle("sbx-bolt", "#g-1", "sbx-ask: @sbx-ask and then?")
    assert len(jobs) == 1  # one hop, then quiet until a human speaks
    await agent.handle("sbx-ann", "#g-1", "@sbx-bolt again please")
    await agent.handle("sbx-bolt", "#g-1", "@sbx-ask one more?")
    assert len(jobs) == 2


@pytest.mark.asyncio
async def test_mention_only_peer_answers_only_when_tagged(tmp_path):
    cfg = SandboxConfig(
        nick="sbx-bolt",
        peer_agents=["sbx-ask"],
        answer_unaddressed=False,
        knowledge_dir=str(tmp_path),
    )
    agent = SandboxAgent(cfg, knowledge="")
    jobs: list = []

    async def admit(job):
        jobs.append(job)

    agent._admit = admit
    await agent.handle("sbx-ann", "#g-1", "what is culture?")
    await agent.handle("sbx-ann", "#g-1", "@sbx-bolt hi!")
    await agent.handle("sbx-ann", "sbx-bolt", "a DM")
    assert [j.question for j in jobs] == ["hi!", "a DM"]


def test_peers_in_system_prompt_and_config_forms():
    prompt = system_prompt("sbx-ask", "", {"sbx-bolt": "a second model"})
    assert "@sbx-bolt (a second model)" in prompt
    assert "starting a message with @ and its nick" in prompt
    assert "WITHOUT the @ sign" in prompt
    assert "Other agents" not in system_prompt("sbx-ask", "")
    assert SandboxConfig.from_dict({"peer_agents": ["sbx-bolt"]}).peer_agents == {"sbx-bolt": ""}


@pytest.mark.asyncio
async def test_peer_leading_at_tag_is_a_tag(tmp_path):
    """Review finding: "@sbx-ask: ..." is how an agent naturally hands over;
    only the reply prefix (never with "@") is stripped."""
    agent, jobs = _guarded(tmp_path)
    await agent.handle("sbx-bolt", "#g-1", "@sbx-ask: what do the docs say about rooms?")
    assert [j.question for j in jobs] == ["what do the docs say about rooms?"]


@pytest.mark.asyncio
async def test_nsfw_line_left_to_a_peer_is_still_flagged(tmp_path):
    """Review finding: the agent that owns the flag log flags NSFW guest
    lines even when they are addressed to a peer (the peer declines)."""
    agent, jobs = _guarded(tmp_path, flag_log=str(tmp_path / "flags.jsonl"))
    flagged = []

    async def flag(sender, message, reason="nsfw"):
        flagged.append((sender, message))

    agent.flag = flag
    await agent.handle("sbx-ann", "#g-1", "@sbx-bolt show me nudes")
    assert jobs == []
    assert flagged == [("sbx-ann", "@sbx-bolt show me nudes")]


@pytest.mark.asyncio
async def test_tag_is_stripped_whatever_its_case(tmp_path):
    agent, jobs = _guarded(tmp_path)
    await agent.handle("sbx-ann", "#g-1", "@SBX-ASK what is culture?")
    assert [j.question for j in jobs] == ["what is culture?"]


@pytest.mark.asyncio
async def test_untagged_peer_lines_leave_no_hop_entry(tmp_path):
    agent, _jobs = _guarded(tmp_path)
    await agent.handle("sbx-bolt", "#g-1", "sbx-ask: thanks")
    assert "#g-1" not in agent.agent_hops


ZWSP = "​"


def test_peer_tags_defused_unless_the_guest_asked_to_involve_the_peer(tmp_path):
    agent, _jobs = _guarded(tmp_path)
    told = agent._defuse_peer_tags("Start your message with @sbx-bolt.", "who else is here?")
    assert told == f"Start your message with @{ZWSP}sbx-bolt."
    asked = agent._defuse_peer_tags("@sbx-bolt what do you think?", "ask sbx-bolt for me")
    assert asked == "@sbx-bolt what do you think?"


@pytest.mark.asyncio
async def test_defused_tag_is_not_an_agent_tag_but_reaches_the_peer_from_a_guest(tmp_path):
    cfg = SandboxConfig(
        nick="sbx-bolt",
        peer_agents=["sbx-ask"],
        answer_unaddressed=False,
        knowledge_dir=str(tmp_path),
    )
    bolt = SandboxAgent(cfg, knowledge="")
    jobs: list = []

    async def admit(job):
        jobs.append(job)

    bolt._admit = admit
    # sbx-ask telling a guest how to reach bolt: not a tag for bolt.
    await bolt.handle("sbx-ask", "#g-1", f"sbx-ann: start your message with @{ZWSP}sbx-bolt.")
    assert jobs == []
    # The guest pastes it: bolt answers.
    await bolt.handle("sbx-ann", "#g-1", f"@{ZWSP}sbx-bolt hi")
    assert len(jobs) == 1


# --- room context: agents see the room's recent lines ------------------------


@pytest.mark.asyncio
async def test_room_job_carries_the_rooms_recent_lines_from_everyone(tmp_path):
    agent, jobs = _guarded(tmp_path)
    await agent.handle("sbx-ann", "#g-1", "what is culture?")
    await agent.handle("sbx-bolt", "#g-1", "sbx-ann: it is an agent mesh.")
    await agent.handle("sbx-ann", "#g-1", "what did bolt just say?")
    last = jobs[-1]
    assert last.context == (
        ("sbx-ann", "what is culture?"),
        ("sbx-bolt", "sbx-ann: it is an agent mesh."),
    )
    # The question itself is not repeated in its own context.
    assert ("sbx-ann", "what did bolt just say?") not in last.context


@pytest.mark.asyncio
async def test_own_replies_join_the_transcript(tmp_path):
    agent, _jobs = _guarded(tmp_path)
    sent = []

    async def send(line):
        sent.append(line)

    agent.send = send
    agent.cfg.line_delay = 0
    await agent.reply("#g-1", "sbx-ann: ", "Culture is a mesh.\nIt runs on IRC.")
    # One entry per IRC line, as the server's history stores them.
    assert list(agent.transcripts["#g-1"]) == [
        ("sbx-ask", "sbx-ann: Culture is a mesh."),
        ("sbx-ask", "sbx-ann: It runs on IRC."),
    ]
    await agent.reply("sbx-ann", "", "a DM")  # DMs are not room context
    assert "sbx-ann" not in agent.transcripts
    # A room the agent has left is not re-created by a late reply.
    agent.rooms.discard("#g-1")
    agent.transcripts.pop("#g-1")
    await agent.reply("#g-1", "sbx-ann: ", "late answer")
    assert "#g-1" not in agent.transcripts


def _room_job(question, context=(), hits=()):
    from culture_core.sandbox.agent import _Job

    return _Job("sbx-ann", "#g-1/sbx-ann", "#g-1", "sbx-ann: ", question, context, hits)


def test_room_messages_keep_the_newest_lines_within_budget(tmp_path):
    agent, _jobs = _guarded(tmp_path, room_context_chars=40)
    context = (("a", "x" * 30), ("b", "second line"), ("c", "third line"))
    _system, history, question = agent._prompt(_room_job("and now?", context))
    assert question == "sbx-ann: and now?"
    body = history[0]["content"]
    assert "b: second line\nc: third line" in body
    assert "x" * 30 not in body


@pytest.mark.asyncio
async def test_dm_keeps_per_guest_history_and_zero_lines_turns_context_off(tmp_path):
    agent, jobs = _guarded(tmp_path)
    await agent.handle("sbx-ann", "sbx-ask", "a DM question")
    assert jobs[-1].context == ()
    off, off_jobs = _guarded(tmp_path, room_context_lines=0)
    await off.handle("sbx-ann", "#g-1", "first")
    await off.handle("sbx-ann", "#g-1", "second")
    assert off_jobs[-1].context == ()


async def _seeding_agent(tmp_path):
    agent, jobs = _guarded(tmp_path)
    agent.rooms.discard("#g-1")
    sent = []

    async def send(line):
        sent.append(line)

    agent.send = send
    await agent._follow("#g-1", "sbx-ann")
    return agent, jobs, sent


@pytest.mark.asyncio
async def test_history_recent_rebuilds_the_transcript_in_order(tmp_path):
    agent, _jobs, sent = await _seeding_agent(tmp_path)
    assert "HISTORY RECENT #g-1 120" in sent  # 30 lines x 4: system-* lines are skipped
    await agent._on_line(":sbx HISTORY #g-1 sbx-ann 1.0 :earlier question")
    await agent._on_line(":sbx HISTORY #g-1 system-sbx 2.0 :sbx-ann joined")
    await agent._on_line(":sbx HISTORY #g-1 sbx-ann 3.0 :show me nudes")  # never context
    agent._remember("#g-1", "sbx-ann", "live line")  # heard while seeding
    await agent._on_line(":sbx HISTORYEND #g-1 :End of history")
    assert list(agent.transcripts["#g-1"]) == [
        ("sbx-ann", "earlier question"),
        ("sbx-ann", "live line"),
    ]


@pytest.mark.asyncio
async def test_reconnect_keeps_order_and_repeated_lines(tmp_path):
    """Review finding: an old transcript plus a replay put the newest line
    first; and exact-text dedup collapsed a line said twice."""
    agent, _jobs, _sent = await _seeding_agent(tmp_path)
    agent._remember("#g-1", "sbx-ann", "A")  # the transcript from before the outage
    agent._remember("#g-1", "sbx-ann", "yes")
    agent._seeding["#g-1"]["live"].clear()  # ...not heard during this catch-up
    for n, line in enumerate(["A", "yes", "C (said during the outage)", "yes"]):
        await agent._on_line(f":sbx HISTORY #g-1 sbx-ann {n}.0 :{line}")
    agent._remember("#g-1", "sbx-ann", "yes")  # also heard live: overlaps the replay's end
    await agent._on_line(":sbx HISTORYEND #g-1 :End of history")
    assert [t for _n, t in agent.transcripts["#g-1"]] == [
        "A",
        "yes",
        "C (said during the outage)",
        "yes",
    ]


@pytest.mark.asyncio
async def test_first_question_waits_for_the_catch_up(tmp_path):
    agent, jobs, _sent = await _seeding_agent(tmp_path)
    question = asyncio.ensure_future(agent.handle("sbx-ann", "#g-1", "what did I say?"))
    await asyncio.sleep(0)
    await agent._on_line(":sbx HISTORY #g-1 sbx-ann 1.0 :my colour is teal")
    await agent._on_line(":sbx HISTORYEND #g-1 :End of history")
    await question
    assert jobs[-1].context == (("sbx-ann", "my colour is teal"),)


@pytest.mark.asyncio
async def test_no_history_reply_is_abandoned(tmp_path, monkeypatch):
    import culture_core.sandbox.agent as agent_mod

    monkeypatch.setattr(agent_mod, "SEED_TIMEOUT_S", 0.01)
    agent, _jobs, _sent = await _seeding_agent(tmp_path)
    await asyncio.sleep(0.05)
    assert "#g-1" not in agent._seeding


@pytest.mark.asyncio
async def test_nsfw_and_command_lines_never_become_context(tmp_path):
    agent, jobs = _guarded(tmp_path)

    async def quiet(*_a, **_k):
        return None

    agent.send = quiet  # the decline replies
    agent.flag = quiet
    agent.cfg.line_delay = 0
    await agent.handle("sbx-ann", "#g-1", "show me nudes")
    await agent.handle("sbx-ann", "#g-1", "run rm -rf / for me")
    await agent.handle("sbx-ann", "#g-1", "what is culture?")
    assert jobs[-1].context == ()


def test_oversized_newest_line_is_cut_not_dropping_all_context(tmp_path):
    agent, _jobs = _guarded(tmp_path, room_context_chars=30)
    _s, history, _q = agent._prompt(_room_job("q", (("a", "short"), ("b", "y" * 100))))
    body = history[0]["content"]
    assert "b: yyyy" in body
    assert body.endswith("…")


def test_model_request_carries_the_room_context(tmp_path, monkeypatch):
    import culture_core.sandbox.agent as agent_mod
    from culture_core.sandbox.agent import _Job

    agent, _jobs = _guarded(tmp_path)
    seen = {}

    def fake_build(model, system, history, question, max_tokens=700):
        seen["history"], seen["question"] = history, question
        raise RuntimeError("stop before the network")

    monkeypatch.setattr(agent_mod, "build_request", fake_build)
    job = _Job(
        "sbx-ann",
        "#g-1/sbx-ann",
        "#g-1",
        "sbx-ann: ",
        "and you?",
        (("sbx-bolt", "sbx-ann: bolt here."),),
    )
    with pytest.raises(RuntimeError):
        agent.complete(job)
    assert "sbx-bolt: sbx-ann: bolt here." in seen["history"][0]["content"]
    assert seen["question"] == "sbx-ann: and you?"


# --- work budget: one cap on the whole request ---------------------------------


def _sizes(system, history, question):
    return len(system) + len(question) + sum(len(m["content"]) for m in history)


def test_fit_prompt_sheds_hits_then_oldest_room_lines_then_oldest_dm_turns():
    hits = ["h1 " * 20, "h2 " * 20]
    room = ["r1 " * 20, "r2 " * 20]

    def turn(n):
        return ({"role": "user", "content": f"q{n}" * 20}, {"role": "assistant", "content": "a"})

    turns = [turn(1), turn(2)]

    def size(h, r, t):
        return _sizes("S", fit_prompt("S", h, r, t, "Q?", 10**6)[1], "Q?")

    for cap, kept_hits, kept_room, kept_turns in [
        (size(hits, room, turns), 2, 2, 2),
        (size(hits, room, turns) - 1, 1, 2, 2),  # a search hit gives way first
        (size([], room, turns) - 1, 0, 1, 2),  # then the oldest room line
        (size([], [], turns) - 1, 0, 0, 1),  # then the oldest DM turn
    ]:
        system, history = fit_prompt("S", hits, room, turns, "Q?", cap)
        text = "\n".join(m["content"] for m in history)
        assert _sizes(system, history, "Q?") <= cap
        assert sum(h in text for h in hits) == kept_hits
        assert sum(r in text for r in room) == kept_room
        assert sum(f"q{n}" * 20 in text for n in (1, 2)) == kept_turns
        if kept_hits == 1:
            assert hits[1] in text  # the newest hit stays
        if kept_room == 1:
            assert room[1] in text


def test_fit_prompt_never_cuts_the_question_but_cuts_the_docs_last():
    system, history = fit_prompt("rules. DOCS" + "d" * 100, ["hit"], ["room"], [], "Q" * 50, 60)
    assert history == []
    assert system.startswith("rules.")
    assert len(system) == 10  # the docs' end gives way
    assert _sizes(system, history, "Q" * 50) == 60


def test_docs_budget_shrinks_to_fit_the_request_cap(tmp_path):
    cfg = SandboxConfig(knowledge_dir=str(tmp_path))
    budget = knowledge_budget(cfg)
    assert 20_000 < budget < cfg.knowledge_budget_chars  # 40k cap: the docs give way
    rest = cfg.room_context_chars + cfg.search_chars + cfg.history_chars
    assert len(system_prompt(cfg.nick, "", {})) + budget + rest < cfg.max_prompt_chars
    assert knowledge_budget(SandboxConfig(max_prompt_chars=1000)) == 0
    roomy = SandboxConfig(max_prompt_chars=10**6)
    assert knowledge_budget(roomy) == roomy.knowledge_budget_chars


def test_every_request_fits_the_cap(tmp_path):
    (tmp_path / "README.md").write_text("doc " * 30_000)  # far over any budget
    agent = SandboxAgent(SandboxConfig(knowledge_dir=str(tmp_path), max_prompt_chars=8_000))
    assert "doc doc" not in agent.system  # the oversized file is skipped
    agent.history["sbx-ann"].extend(
        [{"role": "user", "content": "x" * 900}, {"role": "assistant", "content": "y" * 900}] * 6
    )
    context = tuple(("sbx-ann", "z" * 200) for _ in range(30))
    hits = tuple(("sbx-bolt", "w" * 180) for _ in range(8))
    from culture_core.sandbox.agent import _Job

    for job in (
        _room_job("a room question", context, hits),
        _Job("sbx-ann", "sbx-ann", "sbx-ann", "", "a DM question"),
    ):
        system, history, question = agent._prompt(job)
        assert _sizes(system, history, question) <= 8_000
        assert question.endswith("question")


def test_dm_history_is_capped_in_characters_too(tmp_path):
    agent, _jobs = _guarded(tmp_path, history_chars=1_000)
    h = agent.history["sbx-ann"]
    for n in range(4):
        h.extend(
            [
                {"role": "user", "content": f"q{n} " + "x" * 300},
                {"role": "assistant", "content": f"a{n} " + "y" * 150},
            ]
        )
    turns = agent._dm_turns("sbx-ann")
    assert [t[0]["content"][:2] for t in turns] == ["q2", "q3"]  # newest pairs, whole
    assert agent._dm_turns("nobody") == []
    assert "nobody" not in agent.history


# --- per-request chat search ---------------------------------------------------


async def _searching_agent(tmp_path, **kw):
    agent, jobs = _guarded(tmp_path, **kw)
    agent._history_ok = True  # the server has answered HISTORY before
    sent = []

    async def send(line):
        sent.append(line)
        if line.startswith("HISTORY SEARCH"):
            word = line.split()[-1]
            for ts, nick, text in agent.server_history:
                if word in text.lower():
                    agent._on_history_line(f"#g-1 {nick} {ts} :{text}")
            agent._on_history_line("#g-1 system-sbx 0.5 :old join line with teal")
            agent._on_history_end("#g-1 :End of history")

    agent.send = send
    agent.server_history = []
    return agent, jobs, sent


@pytest.mark.asyncio
async def test_question_about_older_words_searches_the_room_for_this_request_only(tmp_path):
    agent, jobs, sent = await _searching_agent(tmp_path, room_context_lines=3)
    agent.server_history = [
        (1.0, "sbx-ann", "my favourite colour is teal"),
        (2.0, "sbx-ask", "sbx-ann: noted, teal it is."),
    ]
    for line in ("one", "two", "three"):
        await agent.handle("sbx-ann", "#g-1", line)
    agent.server_history.append((9.0, "sbx-ann", "what was my colour, teal or not?"))
    await agent.handle("sbx-ann", "#g-1", "what was my colour, teal or not?")
    searched = [line.split()[-1] for line in sent if line.startswith("HISTORY SEARCH #g-1")]
    assert searched == ["colour", "teal"]  # longest first; "what", "was", "not" skipped
    assert jobs[-1].hits == (
        ("sbx-ann", "my favourite colour is teal"),
        ("sbx-ask", "sbx-ann: noted, teal it is."),
    )  # oldest first; the question itself and system-* lines are not hits
    assert all("teal" not in text for _n, text in list(agent.transcripts["#g-1"])[:-1])
    _system, history, _q = agent._prompt(jobs[-1])
    assert "found by searching" in history[0]["content"]
    assert "my favourite colour is teal" in history[0]["content"]
    # The next request starts clean: nothing to search, no hits carried over.
    await agent.handle("sbx-ann", "#g-1", "three")
    assert jobs[-1].hits == ()


@pytest.mark.asyncio
async def test_search_is_capped_in_hits_and_characters(tmp_path):
    agent, jobs, _sent = await _searching_agent(
        tmp_path, search_hits=8, search_chars=300, room_context_lines=1
    )
    await agent.handle("sbx-ann", "#g-1", "hello")
    agent.server_history = [
        (float(n), "sbx-ann", f"rocket number {n} " + "x" * 40) for n in range(20)
    ]
    await agent.handle("sbx-ann", "#g-1", "tell me about the rocket")
    hits = jobs[-1].hits
    assert 0 < len(hits) <= 8
    assert sum(len(f"{n}: {t}") + 1 for n, t in hits) <= 300 + 1
    assert hits[-1][1].startswith("rocket number 19")  # the newest are kept


@pytest.mark.asyncio
async def test_words_already_in_the_window_nicks_and_off_switch_do_not_search(tmp_path):
    agent, _jobs, sent = await _searching_agent(tmp_path, room_context_lines=1)
    await agent.handle("sbx-ann", "#g-1", "the rocket launched")  # an empty room: no search
    await agent.handle("sbx-ann", "#g-1", "@sbx-ask when did the rocket launch? sbx-bolt said")
    assert [w.split()[-1] for w in sent if "SEARCH" in w] == ["launch"]
    off, off_jobs, off_sent = await _searching_agent(tmp_path, search_hits=0, room_context_lines=1)
    await off.handle("sbx-ann", "#g-1", "hello")
    await off.handle("sbx-ann", "#g-1", "remember the zebra?")
    assert not [w for w in off_sent if "SEARCH" in w]
    assert off_jobs[-1].hits == ()


@pytest.mark.asyncio
async def test_search_times_out_and_waits_for_nothing_without_history(tmp_path):
    agent, jobs = _guarded(tmp_path, search_timeout=0.05, room_context_lines=1)
    sent = []

    async def send(line):
        sent.append(line)  # the server never answers

    agent.send = send
    await agent.handle("sbx-ann", "#g-1", "about the zebra?")
    assert not sent  # no HISTORY reply seen yet: don't wait for one
    agent._history_ok = True
    await asyncio.wait_for(agent.handle("sbx-ann", "#g-1", "and the giraffe?"), 1)
    assert sent == ["HISTORY SEARCH #g-1 giraffe"]
    assert jobs[-1].hits == ()
    # A late reply is still taken as that search's, not a newer request's.
    await agent._request_history("#g-1")
    agent._on_history_line("#g-1 sbx-ann 1.0 :the giraffe line")
    agent._on_history_end("#g-1 :End of history")  # ends the timed-out search
    agent._on_history_line("#g-1 sbx-ann 2.0 :the catch-up line")
    agent._on_history_end("#g-1 :End of history")
    assert list(agent.transcripts["#g-1"])[0] == ("sbx-ann", "the catch-up line")
    assert ("sbx-ann", "the giraffe line") not in agent.transcripts["#g-1"]


@pytest.mark.asyncio
async def test_no_search_while_the_whole_room_fits_the_window(tmp_path):
    agent, _jobs, sent = await _searching_agent(tmp_path)
    await agent.handle("sbx-ann", "#g-1", "my colour is teal")
    await agent.handle("sbx-ann", "#g-1", "what about the zebra?")
    assert not [w for w in sent if "SEARCH" in w]
    tight, _j, tight_sent = await _searching_agent(tmp_path, room_context_chars=30)
    await tight.handle("sbx-ann", "#g-1", "x" * 40)  # trimmed out of the window...
    await tight.handle("sbx-ann", "#g-1", "hi")  # ...by this newer line
    await tight.handle("sbx-ann", "#g-1", "what about the zebra?")
    assert [w for w in tight_sent if "SEARCH" in w] == ["HISTORY SEARCH #g-1 zebra"]


@pytest.mark.asyncio
async def test_real_server_search_hits_reach_the_model_once(
    server, gateway, make_client, start_agent
):
    agent = await start_agent(room_context_lines=2, search_timeout=2.0)
    ann = await _guest(make_client, "testserv-ann")
    for line in ("my favourite colour is teal", "ok", "fine"):
        await ann.send(f"PRIVMSG #general :{line}")
        await _collect(ann, "canned answer")
    agent._history_ok = True  # also set by the catch-up's HISTORYEND
    await ann.send("PRIVMSG #general :which colour did I pick?")
    await _collect(ann, "canned answer")
    await ann.send("PRIVMSG #general :thanks")
    await _collect(ann, "canned answer")
    asked, after = gateway.requests[-2], gateway.requests[-1]
    found = [m["content"] for m in asked["messages"] if "found by searching" in m["content"]]
    assert found
    assert "testserv-ann: my favourite colour is teal" in found[0]
    assert not any("found by searching" in m["content"] for m in after["messages"])
    assert all("teal" not in t for _n, t in agent.transcripts["#general"])
