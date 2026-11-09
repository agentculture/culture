"""`culture sandbox probe`: a guest's question answered end to end, graded deterministically.

The integration tests run the probe against a real in-process IRCd with fake
agents (plain IRC clients) that follow the guest into its room and answer.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket

import pytest

from culture_core.sandbox import probe as probe_mod
from culture_core.sandbox.probe import ProbeConfig, run_probe

GUEST = "testserv-probe-abc123"
ROOM = f"#g-{GUEST}"


@pytest.fixture(autouse=True)
def _fixed_nick(monkeypatch):
    monkeypatch.setattr(probe_mod.secrets, "token_hex", lambda _n: "abc123")


def _cfg(server, *agents: str, **kw) -> ProbeConfig:
    base = dict(
        host="127.0.0.1",
        port=server.config.port,
        agents=agents,
        nick_prefix="testserv-probe",
        join_timeout=5.0,
        answer_timeout=3.0,
    )
    base.update(kw)
    return ProbeConfig(**base)


async def _fake_agent(make_client, nick: str, reply: str | None, join: bool = True):
    """An agent that joins the guest room once it exists and answers @-tagged lines."""
    client = await make_client(nick=nick, user=nick.split("-", 1)[1])

    async def loop():
        if join:
            # The guest joins first; joining an empty room early is harmless too.
            await asyncio.sleep(0.3)
            await client.send(f"JOIN {ROOM}")
        while True:
            try:
                line = await client.recv(timeout=10)
            except (asyncio.TimeoutError, ConnectionError):
                return
            if f"PRIVMSG {ROOM} :@{nick} " in line and reply is not None:
                await client.send(f"PRIVMSG {ROOM} :{GUEST}: {reply}")

    return asyncio.create_task(loop())


async def _stop(*tasks):
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_probe_passes_when_every_agent_answers_the_fact(server, make_client):
    a = await _fake_agent(make_client, "testserv-ask", "It is based on RFC 2812.")
    b = await _fake_agent(make_client, "testserv-bolt", "2812")
    try:
        report = await run_probe(_cfg(server, "testserv-ask", "testserv-bolt"))
    finally:
        await _stop(a, b)
    assert report["ok"] is True, report
    assert report["summary"].startswith("Demo OK: testserv-ask ")
    assert [c["agent"] for c in report["checks"]] == ["testserv-ask", "testserv-bolt"]
    assert all(c["ok"] and c["seconds"] is not None for c in report["checks"])


@pytest.mark.asyncio
async def test_probe_fails_on_a_wrong_answer_and_keeps_an_excerpt(server, make_client):
    a = await _fake_agent(make_client, "testserv-ask", "RFC 1459, I think")
    try:
        report = await run_probe(_cfg(server, "testserv-ask"))
    finally:
        await _stop(a)
    assert report["ok"] is False
    check = report["checks"][0]
    assert check["reason"] == "answer did not match the expected fact"
    assert check["answer"] == "RFC 1459, I think"
    assert report["summary"].startswith("Demo FAILED: testserv-ask: answer did not match")


@pytest.mark.asyncio
async def test_probe_fails_when_an_agent_is_silent(server, make_client):
    a = await _fake_agent(make_client, "testserv-ask", None)
    try:
        report = await run_probe(_cfg(server, "testserv-ask", answer_timeout=1.0))
    finally:
        await _stop(a)
    assert report["ok"] is False
    assert report["checks"][0]["reason"] == "no answer in 1s"


@pytest.mark.asyncio
async def test_probe_fails_when_an_agent_never_joins_but_still_asks_the_rest(server, make_client):
    a = await _fake_agent(make_client, "testserv-ask", "2812")
    b = await _fake_agent(make_client, "testserv-bolt", "2812", join=False)
    try:
        report = await run_probe(_cfg(server, "testserv-ask", "testserv-bolt", join_timeout=2.0))
    finally:
        await _stop(a, b)
    assert report["ok"] is False
    by_agent = {c["agent"]: c for c in report["checks"]}
    assert by_agent["testserv-ask"]["ok"] is True
    assert by_agent["testserv-bolt"]["reason"] == "did not join the guest room in 2s"


@pytest.mark.asyncio
async def test_probe_reports_an_unreachable_server():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    report = await run_probe(ProbeConfig(port=port, agents=("sbx-ask",)))
    assert report == {
        "ok": False,
        "summary": f"Demo FAILED: cannot connect to sandbox IRCd 127.0.0.1:{port}",
        "checks": [],
    }


# --- parsing helpers ---------------------------------------------------------


def test_names_and_privmsg_parsers():
    assert probe_mod._names(":srv 353 me = #g-x :@me +sbx-ask sbx-bolt", "#g-x") == {
        "me",
        "sbx-ask",
        "sbx-bolt",
    }
    assert probe_mod._names(":srv 353 me = #other :sbx-ask", "#g-x") == set()
    assert probe_mod._privmsg(":sbx-ask!u@h PRIVMSG #g-x :me: 2812", "#g-x") == (
        "sbx-ask",
        "me: 2812",
    )
    assert probe_mod._privmsg(":sbx-ask!u@h PRIVMSG #elsewhere :hi", "#g-x") is None
    assert probe_mod._strip_address("me: RFC 2812", "me") == "RFC 2812"


# --- CLI: JSON on stdout, exit status ------------------------------------------


def _args(**kw) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    probe_mod.add_arguments(parser)
    ns = parser.parse_args([])
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


@pytest.mark.parametrize(
    "exit_code,ok,expected", [(False, False, 0), (True, False, 1), (True, True, 0)]
)
def test_cli_prints_json_and_exits_by_flag(monkeypatch, capsys, exit_code, ok, expected):
    async def fake(cfg):
        assert cfg.agents == ("sbx-ask", "sbx-bolt")
        return {"ok": ok, "summary": "s", "checks": []}

    monkeypatch.setattr(probe_mod, "run_probe", fake)
    assert probe_mod.run(_args(exit_code=exit_code)) == expected
    assert json.loads(capsys.readouterr().out)["ok"] is ok


def test_cli_rejects_a_bad_pattern(capsys):
    assert probe_mod.run(_args(expect="(")) == 2
    assert "not a valid regex" in capsys.readouterr().err


def test_culture_sandbox_probe_is_wired(monkeypatch, capsys):
    import sys

    from culture_core.cli import main as culture_main

    async def fake(cfg):
        assert cfg.port == 7001
        assert cfg.agents == ("sbx-ask",)
        return {"ok": True, "summary": "Demo OK", "checks": []}

    monkeypatch.setattr(probe_mod, "run_probe", fake)
    monkeypatch.setattr(
        sys, "argv", ["culture", "sandbox", "probe", "--port", "7001", "--agent", "sbx-ask"]
    )
    with pytest.raises(SystemExit) as exc:
        culture_main()
    assert exc.value.code == 0
    assert json.loads(capsys.readouterr().out)["summary"] == "Demo OK"
