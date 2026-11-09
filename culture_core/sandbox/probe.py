"""End-to-end demo probe: act as a guest on the sandbox IRCd and check the agents answer.

``culture sandbox probe`` connects to the sandbox server as a throwaway guest,
joins a private guest room (``#g-<nick>``), waits for the sandbox agents to
follow it in, asks each one the same fixed question by @-tagging it, and checks
each answer against a regular expression. One run exercises the whole path a
guest's question takes: the sandbox IRCd, the agent's room-following, the
agent's model call through the gateway, and the reply.

The grading is deterministic code, not a model judge: a fixed question whose
answer is a fact in the agents' docs bundle (by default culture's base protocol,
RFC 2812) and a fixed pattern (``\\b2812\\b``). The model's wording can vary;
the check does not.

The result is one JSON object on stdout, so a scheduler can read it (a
culture-rules probe trigger turns the keys into ``trigger.data``)::

    {"ok": true, "summary": "Demo OK: sbx-ask 2.4s, sbx-bolt 2.3s",
     "checks": [{"agent": "sbx-ask", "ok": true, "seconds": 2.4, "reason": ""}, ...]}

The exit status is 0 whenever a report was printed, pass or fail, so a probe
runner that drops failed commands still sees a failure report. ``--exit-code``
returns 1 when the demo failed, for a systemd timer or a shell.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import secrets
import sys
import time
from dataclasses import asdict, dataclass

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 6668
DEFAULT_AGENTS = ("sbx-ask", "sbx-bolt")
DEFAULT_QUESTION = "Which IRC RFC is culture's protocol based on? Reply with just the RFC number."
DEFAULT_EXPECT = r"\b2812\b"
DEFAULT_NICK_PREFIX = "sbx-probe"
DEFAULT_ROOM_PREFIX = "#g-"
JOIN_TIMEOUT_S = 30.0
ANSWER_TIMEOUT_S = 180.0
# After an agent's first reply line that doesn't match, wait this long for more
# chunks (long answers are split across several PRIVMSGs) before failing.
MORE_LINES_S = 5.0
NAMES_EVERY_S = 2.0
_ANSWER_EXCERPT = 200


@dataclass
class Check:
    agent: str
    ok: bool
    seconds: float | None
    reason: str = ""
    answer: str = ""


@dataclass
class ProbeConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    agents: tuple[str, ...] = DEFAULT_AGENTS
    question: str = DEFAULT_QUESTION
    expect: str = DEFAULT_EXPECT
    nick_prefix: str = DEFAULT_NICK_PREFIX
    room_prefix: str = DEFAULT_ROOM_PREFIX
    join_timeout: float = JOIN_TIMEOUT_S
    answer_timeout: float = ANSWER_TIMEOUT_S


class _Conn:
    """A minimal IRC client: line I/O with PING handled out of band."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer

    async def send(self, line: str) -> None:
        self.writer.write(f"{line}\r\n".encode())
        await self.writer.drain()

    async def recv(self, deadline: float) -> str | None:
        """Next non-PING line, or None at the deadline. Raises ConnectionError on EOF."""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                raw = await asyncio.wait_for(self.reader.readline(), remaining)
            except TimeoutError:
                return None
            if not raw:
                raise ConnectionError("server closed the connection")
            line = raw.decode(errors="replace").rstrip("\r\n")
            if line.startswith("PING"):
                await self.send("PONG" + line[4:])
                continue
            return line

    async def close(self) -> None:
        try:
            await self.send("QUIT :probe done")
        except OSError:  # ConnectionError is an OSError
            pass
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except OSError:
            pass


def _sender(line: str) -> str:
    return line[1:].split("!", 1)[0] if line.startswith(":") else ""


def _privmsg(line: str, room: str) -> tuple[str, str] | None:
    """(sender, text) of a PRIVMSG to ``room``, else None."""
    parts = line.split(" ", 3)
    if len(parts) < 4 or parts[1] != "PRIVMSG" or parts[2].lower() != room.lower():
        return None
    text = parts[3][1:] if parts[3].startswith(":") else parts[3]
    return _sender(line), text


def _names(line: str, room: str) -> set[str]:
    """Nicks in a 353 (RPL_NAMREPLY) for ``room``, prefixes stripped."""
    parts = line.split(" ")
    if len(parts) < 6 or parts[1] != "353" or parts[4].lower() != room.lower():
        return set()
    names = " ".join(parts[5:]).lstrip(":").split()
    return {n.lstrip("@+%~&") for n in names}


async def _register(conn: _Conn, nick: str, deadline: float) -> None:
    await conn.send(f"NICK {nick}")
    await conn.send("USER probe 0 * :culture demo probe")
    while True:
        line = await conn.recv(deadline)
        if line is None:
            raise TimeoutError("no welcome from the server")
        parts = line.split(" ")
        if len(parts) > 1 and parts[1] == "001":
            return
        if len(parts) > 1 and parts[1] in {"432", "433", "436"}:
            raise ConnectionError(f"nick {nick} refused ({parts[1]})")


async def _await_agents(conn: _Conn, room: str, agents: set[str], deadline: float) -> set[str]:
    """Wait until ``deadline`` for the agents to follow the guest into its room.

    Returns whoever arrived, so a missing agent is reported by name.
    """
    present: set[str] = set()
    next_names = 0.0
    while not agents <= present:
        now = time.monotonic()
        if now >= deadline:
            break
        if now >= next_names:
            await conn.send(f"NAMES {room}")
            next_names = now + NAMES_EVERY_S
        line = await conn.recv(min(deadline, next_names))
        if line is None:
            continue
        present |= _names(line, room) & agents
        parts = line.split(" ")
        if len(parts) > 2 and parts[1] == "JOIN" and parts[2].lstrip(":").lower() == room.lower():
            present |= {_sender(line)} & agents
    return present


def _strip_address(text: str, nick: str) -> str:
    """Agents prefix replies with ``<guest>: ``; drop it before matching."""
    prefix = f"{nick}:"
    return text[len(prefix) :].lstrip() if text.startswith(prefix) else text


async def _ask(conn: _Conn, cfg: ProbeConfig, room: str, nick: str, agent: str) -> Check:
    pattern = re.compile(cfg.expect, re.IGNORECASE)
    started = time.monotonic()
    await conn.send(f"PRIVMSG {room} :@{agent} {cfg.question}")
    deadline = started + cfg.answer_timeout
    answer: list[str] = []
    while True:
        line = await conn.recv(deadline)
        if line is None:
            break
        msg = _privmsg(line, room)
        if msg is None or msg[0] != agent:
            continue
        answer.append(_strip_address(msg[1], nick))
        text = " ".join(answer)
        if pattern.search(text):
            return Check(agent, True, round(time.monotonic() - started, 1), answer=text)
        # Got a reply that doesn't match yet: give the rest of it a moment.
        deadline = min(deadline, time.monotonic() + MORE_LINES_S)
    seconds = round(time.monotonic() - started, 1)
    if not answer:
        return Check(agent, False, None, f"no answer in {cfg.answer_timeout:g}s")
    excerpt = " ".join(answer)[:_ANSWER_EXCERPT]
    return Check(agent, False, seconds, "answer did not match the expected fact", excerpt)


def summarize(checks: list[Check], error: str = "") -> dict:
    ok = not error and bool(checks) and all(c.ok for c in checks)
    parts = []
    for c in checks:
        parts.append(f"{c.agent} {c.seconds}s" if c.ok else f"{c.agent}: {c.reason}")
    if error:
        parts.insert(0, error)
    summary = ("Demo OK: " if ok else "Demo FAILED: ") + ", ".join(parts)
    return {"ok": ok, "summary": summary, "checks": [asdict(c) for c in checks]}


async def run_probe(cfg: ProbeConfig) -> dict:
    """Run one probe and return the report (never raises for a demo fault)."""
    nick = f"{cfg.nick_prefix}-{secrets.token_hex(3)}"
    room = f"{cfg.room_prefix}{nick}"
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(cfg.host, cfg.port), timeout=10
        )
    except OSError:  # asyncio.TimeoutError is TimeoutError, an OSError (3.11+)
        return summarize([], f"cannot connect to sandbox IRCd {cfg.host}:{cfg.port}")
    conn = _Conn(reader, writer)
    checks: list[Check] = []
    try:
        await _register(conn, nick, time.monotonic() + 10)
        await conn.send(f"JOIN {room}")
        wanted = set(cfg.agents)
        present = await _await_agents(conn, room, wanted, time.monotonic() + cfg.join_timeout)
        for agent in cfg.agents:
            if agent not in present:
                checks.append(
                    Check(
                        agent, False, None, f"did not join the guest room in {cfg.join_timeout:g}s"
                    )
                )
                continue
            checks.append(await _ask(conn, cfg, room, nick, agent))
    except OSError as exc:  # ConnectionError and TimeoutError are OSErrors
        return summarize(checks, f"sandbox IRCd: {exc}")
    finally:
        await conn.close()
    return summarize(checks)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="culture sandbox probe", description=__doc__.split("\n")[0])
    add_arguments(p)
    return p


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--host", default=DEFAULT_HOST, help="sandbox IRCd host")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help="sandbox IRCd port")
    p.add_argument(
        "--agent",
        action="append",
        dest="agents",
        metavar="NICK",
        help=f"agent to ask (repeatable; default: {', '.join(DEFAULT_AGENTS)})",
    )
    p.add_argument("--question", default=DEFAULT_QUESTION, help="the fixed question asked")
    p.add_argument("--expect", default=DEFAULT_EXPECT, help="regex a passing answer must match")
    p.add_argument(
        "--nick-prefix", default=DEFAULT_NICK_PREFIX, help="guest nick prefix (server name first)"
    )
    p.add_argument("--room-prefix", default=DEFAULT_ROOM_PREFIX, help="guest room prefix")
    p.add_argument("--join-timeout", type=float, default=JOIN_TIMEOUT_S)
    p.add_argument("--answer-timeout", type=float, default=ANSWER_TIMEOUT_S)
    p.add_argument("--exit-code", action="store_true", help="exit 1 when the demo failed")


def config_from_args(args: argparse.Namespace) -> ProbeConfig:
    re.compile(args.expect)  # fail fast on a bad pattern
    return ProbeConfig(
        host=args.host,
        port=args.port,
        agents=tuple(args.agents or DEFAULT_AGENTS),
        question=args.question,
        expect=args.expect,
        nick_prefix=args.nick_prefix,
        room_prefix=args.room_prefix,
        join_timeout=args.join_timeout,
        answer_timeout=args.answer_timeout,
    )


def run(args: argparse.Namespace) -> int:
    try:
        cfg = config_from_args(args)
    except re.error as exc:
        print(f"--expect is not a valid regex: {exc}", file=sys.stderr)
        return 2
    report = asyncio.run(run_probe(cfg))
    print(json.dumps(report))
    return 1 if args.exit_code and not report["ok"] else 0


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
