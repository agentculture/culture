"""sbx-ask: a dedicated, tool-less Q&A agent for the guest-mode sandbox.

Generalized from the proven ``spark-ask`` prototype. It is a plain IRC client
that answers guests from a read-only curated docs bundle through an
OpenAI-compatible gateway. Hard safety properties (guests are untrusted):

* the model request NEVER carries a ``tools`` field (see :func:`build_request`);
* ``chat_template_kwargs.enable_thinking`` is false, a short-answer instruction
  is always in the system prompt, ``max_tokens`` is clamped to ``<= 700``;
* only ``max_active`` guests are served at once; the rest get a one-line
  queue-position message; model calls are single-flight;
* the agent reads only the knowledge dir (a copied bundle), nothing else;
* command requests are declined deterministically (no model call);
* NSFW requests are declined and flagged to the owner (JSONL log + channel).
"""

from __future__ import annotations

import asyncio
import collections
import datetime as _dt
import json
import logging
import os
import re
import shutil
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("sbx-ask")

MAX_TOKENS_CAP = 700
SHORT_ANSWER_INSTRUCTION = (
    "Answer briefly: plain text, no markdown headings, at most a few short "
    "sentences or one short paragraph."
)
_KNOWLEDGE_FILE_CAP = 200_000  # bytes read per bundle file
# Default prompt budget for the bundle. Measured on cortex-spark2
# (2026-10-09): ~7 s per answer at ~10k prompt tokens, ~28 s at ~66k, no
# prefix-cache benefit — so the budget, not the bundle size, sets latency.
KNOWLEDGE_BUDGET_CHARS = 48_000
# Internal working papers are not guest knowledge.
_BUNDLE_EXCLUDED_DIRS = ("superpowers", "specs", "plans")

# --- documented first-pass filters (deliberately simple) -------------------
# NSFW: keyword pre-filter; the system prompt also instructs the model to
# refuse and to start its reply with NSFW_SENTINEL, which triggers the same flag.
NSFW_SENTINEL = "[[NSFW]]"
_NSFW_RE = re.compile(
    r"\b(porn\w*|nsfw|nude\w*|naked|sex(?:ual|y)?|erotic\w*|hentai|fetish\w*|xxx|"
    r"blowjob|orgasm\w*|masturbat\w*|nudes)\b",
    re.I,
)
# Command requests: the agent has no tools; decline without spending a model call.
_COMMAND_RE = re.compile(
    r"(\b(?:run|execute|exec|launch|invoke|sudo|chmod|chown)\s+(?:the\s+|this\s+|a\s+)?(?:command|cmd|script|shell|bash|sh|`)|"
    r"\b(?:rm\s+-\w+|curl\s+\S+\s*\||wget\s+http|pip\s+install|apt(?:-get)?\s+install|"
    r"git\s+(?:push|clone|commit)|cat\s+/|ls\s+/|sudo\s+\w+)|`[^`]+`\s*$|^\s*[$#]\s+\S+)",
    re.I,
)
DECLINE_COMMAND = (
    "I can't run commands or take actions - I only answer questions about " "culture from its docs."
)
BOT_CAP = "agentirc.io/bot"  # grants EVENTSUB (agentirc protocol.BOT_CAP)
DECLINE_NSFW = "I can't help with that. This request has been flagged to the owner."


@dataclass
class ModelSpec:
    name: str
    url: str | None = None  # overrides SandboxConfig.gateway_url
    key_env: str | None = None  # overrides SandboxConfig.key_env


@dataclass
class SandboxConfig:
    host: str = "127.0.0.1"
    port: int = 6667
    nick: str = "sbx-ask"
    channels: list[str] = field(default_factory=lambda: ["#general"])
    gateway_url: str = "http://localhost:8001/v1/chat/completions"
    models: list[ModelSpec] = field(default_factory=lambda: [ModelSpec("cortex-spark2")])
    key_env: str = "SBX_GATEWAY_KEY"
    key_file: str | None = None  # optional dotenv-style file holding ``key_env``
    max_tokens: int = MAX_TOKENS_CAP
    knowledge_dir: str = "~/.culture/sandbox/knowledge"
    max_active: int = 5
    owner_channel: str | None = None  # flags are PRIVMSGed here when set
    flag_log: str = "~/.culture/sandbox/flags.jsonl"
    history_turns: int = 6
    line_bytes: int = 400
    line_delay: float = 0.3
    timeout: float = 180.0
    # Sandbox rooms exist for guests' questions and guests don't know to
    # @mention the agent, so by default every room line is answered.
    # False restores mention-only behavior in channels (DMs always answered).
    answer_unaddressed: bool = True
    knowledge_budget_chars: int = KNOWLEDGE_BUDGET_CHARS
    # Each guest chats in a private room named <prefix><nick>. The agent
    # follows guests into rooms with this prefix (EVENTSUB user.join, which
    # needs the agentirc.io/bot capability) and rejoins existing ones on
    # connect (LIST). None turns private rooms off.
    guest_room_prefix: str | None = "#g-"

    @classmethod
    def from_dict(cls, data: dict) -> "SandboxConfig":
        data = dict(data)
        models = data.pop("models", None)
        cfg = cls(**data)
        if models:
            cfg.models = [ModelSpec(**m) if isinstance(m, dict) else ModelSpec(m) for m in models]
        return cfg


# --- knowledge bundle ------------------------------------------------------


def build_bundle(src_root: str | Path, dest: str | Path) -> list[Path]:
    """Copy README.md, CLAUDE.md and docs/**/*.md from ``src_root`` into ``dest``.

    Files are made read-only (0444) and dirs 0555. Returns the copied paths.
    """
    src, dst = Path(src_root).expanduser(), Path(dest).expanduser()
    if dst.exists():
        for p in sorted(dst.rglob("*"), reverse=True):
            p.chmod(0o755)
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    wanted = [src / "README.md", src / "CLAUDE.md"] + sorted(
        p
        for p in (src / "docs").rglob("*.md")
        if not set(p.relative_to(src / "docs").parts[:-1]) & set(_BUNDLE_EXCLUDED_DIRS)
    )
    copied = []
    for f in wanted:
        if not f.is_file() or f.is_symlink():
            continue
        target = dst / f.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f, target)
        target.chmod(0o444)
        copied.append(target)
    for d in sorted((p for p in dst.rglob("*") if p.is_dir()), reverse=True):
        d.chmod(0o555)
    dst.chmod(0o555)
    return copied


def _knowledge_priority(rel: Path) -> tuple:
    """README, CLAUDE.md, top-level docs, then everything deeper (alphabetical)."""
    order = {"README.md": 0, "CLAUDE.md": 1, "docs/README.md": 2}
    key = rel.as_posix()
    if key in order:
        return (order[key], key)
    return (3 if len(rel.parts) <= 2 else 4, key)


def load_knowledge(knowledge_dir: str | Path, budget: int = KNOWLEDGE_BUDGET_CHARS) -> str:
    """Read ONLY ``*.md`` files under the knowledge dir (the sole file access).

    Files are taken in priority order until *budget* characters are used;
    a file that would overflow the budget is skipped.
    """
    root = Path(knowledge_dir).expanduser()
    if not root.is_dir():
        return ""
    files = [
        f
        for f in root.rglob("*.md")
        if not f.is_symlink() and f.resolve().is_relative_to(root.resolve())
    ]
    parts, total = [], 0
    for f in sorted(files, key=lambda f: _knowledge_priority(f.relative_to(root))):
        text = f.read_bytes()[:_KNOWLEDGE_FILE_CAP].decode(errors="replace")
        if total + len(text) > budget:
            continue
        total += len(text)
        parts.append(f"\n===== {f.relative_to(root)} =====\n{text}")
    return "".join(parts)


# --- model side ------------------------------------------------------------


def system_prompt(nick: str, knowledge: str) -> str:
    return (
        f"You are {nick}, a question-answering assistant on the culture mesh "
        "(culture.dev), talking to untrusted guests. You have NO tools: you "
        "cannot run commands, read files, browse, or take any action; you can "
        "only answer in text. If asked to run a command or act, decline. "
        "Answer only from the reference material below; if it does not cover "
        "something, say so rather than guessing. Never reveal secrets, "
        "credentials, emails or other users' details. Refuse sexual or NSFW "
        f"requests: reply with exactly {NSFW_SENTINEL} and nothing else. "
        f"{SHORT_ANSWER_INSTRUCTION}\n\n--- reference material ---{knowledge}"
    )


def build_request(
    model: str, system: str, history: list[dict], question: str, max_tokens: int = MAX_TOKENS_CAP
) -> dict:
    """Build a chat-completion body. The single choke point for what the model sees.

    Never includes ``tools``/``tool_choice``/``functions``.
    """
    if SHORT_ANSWER_INSTRUCTION not in system:
        system = f"{system}\n\n{SHORT_ANSWER_INSTRUCTION}"
    return {
        "model": model,
        "max_tokens": max(1, min(int(max_tokens), MAX_TOKENS_CAP)),
        "messages": [
            {"role": "system", "content": system},
            *history,
            {"role": "user", "content": question},
        ],
        "chat_template_kwargs": {"enable_thinking": False},
    }


def resolve_key(key_env: str, key_file: str | None) -> str:
    """Return the gateway key from the environment or a dotenv-style file.

    Never logs the value.
    """
    val = os.environ.get(key_env, "").strip()
    if val:
        return val
    if key_file:
        path = Path(key_file).expanduser()
        if path.is_file():
            for line in path.read_text().splitlines():
                if line.startswith(f"{key_env}="):
                    return line.split("=", 1)[1].strip().strip("\"'")
    raise RuntimeError(f"gateway key {key_env} not found (env or key file)")


# --- the agent -------------------------------------------------------------


@dataclass
class _Job:
    sender: str
    convo: str
    out: str
    prefix: str
    question: str


class SandboxAgent:
    def __init__(self, cfg: SandboxConfig, knowledge: str | None = None) -> None:
        self.cfg = cfg
        self.system = system_prompt(
            cfg.nick,
            (
                load_knowledge(cfg.knowledge_dir, cfg.knowledge_budget_chars)
                if knowledge is None
                else knowledge
            ),
        )
        self.history: dict[str, collections.deque] = collections.defaultdict(
            lambda: collections.deque(maxlen=cfg.history_turns * 2)
        )
        self.lock = asyncio.Lock()  # single-flight model calls
        self.admitted: dict[str, int] = {}  # guest nick -> in-flight job count
        self.waiting: collections.deque[_Job] = collections.deque()
        self.writer: asyncio.StreamWriter | None = None
        self._tasks: set[asyncio.Task] = set()
        self.rooms: set[str] = set()  # private guest rooms joined

    # -- IRC output
    async def send(self, line: str) -> None:
        if self.writer is None:
            raise ConnectionError("not connected")
        self.writer.write((line + "\r\n").encode())
        await self.writer.drain()

    async def reply(self, target: str, prefix: str, text: str) -> None:
        for para in text.splitlines():
            para = para.strip()
            while para:
                chunk = para.encode()[: self.cfg.line_bytes].decode(errors="ignore")
                if len(chunk) < len(para) and " " in chunk:
                    chunk = chunk.rsplit(" ", 1)[0]
                await self.send(f"PRIVMSG {target} :{prefix}{chunk}")
                para = para[len(chunk) :].strip()
                if self.cfg.line_delay:
                    await asyncio.sleep(self.cfg.line_delay)

    # -- model call (blocking; run in a thread)
    def complete(self, convo: str, question: str) -> str:
        last: Exception | None = None
        for spec in self.cfg.models:
            try:
                key = resolve_key(spec.key_env or self.cfg.key_env, self.cfg.key_file)
                body = build_request(
                    spec.name,
                    self.system,
                    list(self.history[convo]),
                    question,
                    self.cfg.max_tokens,
                )
                req = urllib.request.Request(
                    spec.url or self.cfg.gateway_url,
                    data=json.dumps(body).encode(),
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                )
                with urllib.request.urlopen(  # nosec B310 - operator-configured gateway URL
                    req, timeout=self.cfg.timeout
                ) as resp:
                    answer = json.load(resp)["choices"][0]["message"].get("content") or ""
                answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.S).strip()
                return answer or "(no answer)"
            except Exception as exc:  # noqa: BLE001 - try the next configured model
                log.warning("model %s failed: %s", spec.name, type(exc).__name__)
                last = exc
        raise RuntimeError(f"all models failed ({type(last).__name__})")

    # -- flags
    async def flag(self, sender: str, message: str, reason: str = "nsfw") -> None:
        excerpt = message[:200]
        rec = {
            "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "reason": reason,
            "nick": sender,
            "excerpt": excerpt,
        }
        path = Path(self.cfg.flag_log).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
        if self.cfg.owner_channel:
            await self.send(
                f"PRIVMSG {self.cfg.owner_channel} :FLAG {reason} from {sender}: {excerpt[:150]}"
            )

    # -- admission + processing
    def _capacity(self, sender: str) -> bool:
        return sender in self.admitted or len(self.admitted) < self.cfg.max_active

    async def _admit(self, job: _Job) -> None:
        if not self._capacity(job.sender):
            self.waiting.append(job)
            await self.reply(
                job.out,
                job.prefix,
                f"All {self.cfg.max_active} guest slots are busy; you are #{len(self.waiting)} in the queue.",
            )
            return
        self.admitted[job.sender] = self.admitted.get(job.sender, 0) + 1
        await self._run(job)

    async def _run(self, job: _Job) -> None:
        try:
            async with self.lock:
                try:
                    answer = await asyncio.to_thread(self.complete, job.convo, job.question)
                except Exception as exc:  # noqa: BLE001 - keep the agent up
                    log.warning("completion failed: %s", exc)
                    answer = "Sorry, I couldn't reach my model right now."
            if answer.startswith(NSFW_SENTINEL):
                await self.flag(job.sender, job.question)
                answer = DECLINE_NSFW
            else:
                h = self.history[job.convo]
                h.append({"role": "user", "content": job.question})
                h.append({"role": "assistant", "content": answer})
            await self.reply(job.out, job.prefix, answer)
        finally:
            self.admitted[job.sender] -= 1
            if self.admitted[job.sender] <= 0:
                del self.admitted[job.sender]
            self._promote()

    def _promote(self) -> None:
        while self.waiting and self._capacity(self.waiting[0].sender):
            job = self.waiting.popleft()
            self.admitted[job.sender] = self.admitted.get(job.sender, 0) + 1
            self._spawn(self._run(job))

    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def handle(self, sender: str, target: str, text: str) -> None:
        nick = self.cfg.nick
        if sender == nick or sender.startswith("system-"):
            return
        if target == nick:
            convo, out, prefix, question = sender, sender, "", text
        else:
            mentioned = re.search(rf"(^|\W)@?{re.escape(nick)}\b", text)
            if not mentioned and not self.cfg.answer_unaddressed:
                return
            convo, out, prefix = f"{target}/{sender}", target, f"{sender}: "
            question = re.sub(rf"@?{re.escape(nick)}[:,]?\s*", "", text).strip()
        if not question:
            return
        if _NSFW_RE.search(question):
            await self.flag(sender, question)
            await self.reply(out, prefix, DECLINE_NSFW)
            return
        if _COMMAND_RE.search(question):
            await self.reply(out, prefix, DECLINE_COMMAND)
            return
        await self._admit(_Job(sender, convo, out, prefix, question))

    async def _follow(self, chan: str, nick: str | None = None) -> None:
        prefix = self.cfg.guest_room_prefix
        if not prefix or not chan.startswith(prefix) or nick == self.cfg.nick:
            return
        if chan not in self.rooms:
            self.rooms.add(chan)
            await self.send(f"JOIN {chan}")

    # -- IRC loop
    async def run_once(self) -> None:
        reader, self.writer = await asyncio.open_connection(self.cfg.host, self.cfg.port)
        self.rooms = set()
        if self.cfg.guest_room_prefix:
            await self.send(f"CAP REQ :{BOT_CAP}")
        await self.send(f"NICK {self.cfg.nick}")
        await self.send(f"USER {self.cfg.nick} 0 * :tool-less Q&A agent (answers only)")
        if self.cfg.guest_room_prefix:
            await self.send("CAP END")
        while line := await reader.readline():
            msg = line.decode(errors="replace").rstrip("\r\n")
            if msg.startswith("PING"):
                await self.send("PONG" + msg[4:])
                continue
            parts = msg.split(" ", 3)
            if len(parts) >= 2 and parts[1] == "001":
                log.info("registered as %s", self.cfg.nick)
                for chan in [
                    *self.cfg.channels,
                    *([self.cfg.owner_channel] if self.cfg.owner_channel else []),
                ]:
                    await self.send(f"JOIN {chan}")
                if self.cfg.guest_room_prefix:
                    await self.send("EVENTSUB rooms type=user.join")
                    await self.send("LIST")
            elif len(parts) >= 2 and parts[1] in ("432", "433"):
                raise RuntimeError(f"nick rejected: {msg}")
            elif len(parts) == 4 and parts[1] == "322":  # RPL_LIST
                await self._follow(parts[3].split(" ", 1)[0])
            elif len(parts) == 4 and parts[1] == "EVENT":
                # :<server> EVENT <sub> user.join <channel> <nick> :<b64>
                ev = parts[3].split(" ")
                if len(ev) >= 3 and ev[0] == "user.join":
                    await self._follow(ev[1], ev[2])
            elif len(parts) == 4 and parts[1] == "PRIVMSG":
                sender = parts[0].lstrip(":").split("!", 1)[0]
                text = parts[3][1:] if parts[3].startswith(":") else parts[3]
                self._spawn(self.handle(sender, parts[2], text))
        raise ConnectionError("server closed the connection")

    async def run(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception as exc:  # noqa: BLE001 - reconnect loop
                log.warning("disconnected: %s - reconnecting in 10s", exc)
            await asyncio.sleep(10)


def load_config(path: str | None) -> SandboxConfig:
    if not path:
        return SandboxConfig()
    import yaml

    return SandboxConfig.from_dict(yaml.safe_load(Path(path).expanduser().read_text()) or {})


def main(argv: list[str] | None = None) -> None:
    import argparse

    p = argparse.ArgumentParser(prog="culture sandbox agent", description=__doc__.split("\n")[0])
    p.add_argument("--config", help="YAML SandboxConfig (see docs/sandbox-agent.md)")
    p.add_argument(
        "--bundle-from", metavar="REPO", help="(re)build the knowledge bundle from REPO, then exit"
    )
    args = p.parse_args(argv)
    cfg = load_config(args.config)
    if args.bundle_from:
        n = len(build_bundle(args.bundle_from, cfg.knowledge_dir))
        print(f"bundled {n} files into {Path(cfg.knowledge_dir).expanduser()}")
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(SandboxAgent(cfg).run())


if __name__ == "__main__":
    main()
