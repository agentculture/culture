"""Tests for the `culture residents` CLI verb and the shared resource-view seam.

Covers (task t5 of the resident-presence plan):

* ``serialize_residents`` — THE canonical JSON payload shared with the
  upcoming HTTP endpoint (t7): deterministic key order, residents sorted
  by nick, budget derivation edge cases, state-only residents.
* the human table renderer — flags column (HUNG? / BUDGET), dashes for
  missing fields.
* the supported path against a REAL running server (repo rule: no mocks
  for the server) — agentirc >= 9.12.0 answers the PRESENCE query, so the
  CLI reports ``supported: true`` (and an empty registry: no resident has
  published presence).
* graceful degrade against a pre-9.12 server (scripted 421 replier): the
  CLI must report ``supported: false`` and exit 0.
* unreachable server — actionable CultureError-style failure, nonzero
  exit, no traceback.
* the adopted wire contract (agentirc-cli 9.12.0 implements the shape
  proposed in the agentirc#53 brief verbatim), exercised against a real
  scripted TCP server speaking PRESENCELIST / PRESENCEEND — this is the
  seam t7 builds on.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket

import pytest

from culture_core.cli.residents import _render_table, dispatch
from culture_core.config import AgentConfig, ServerConfig, ServerConnConfig
from culture_core.resource_view import (
    PresenceUnsupportedError,
    Resident,
    _resident_from_wire,
    apply_budgets,
    fetch_residents_async,
    serialize_residents,
    to_json,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fixed_now():
    from datetime import datetime, timezone

    return datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)


def _resident(nick: str, **kwargs) -> Resident:
    return Resident(nick=nick, **kwargs)


def _make_args(config: str, json_mode: bool = False) -> argparse.Namespace:
    return argparse.Namespace(command="residents", config=config, json=json_mode)


def _run_dispatch(args: argparse.Namespace) -> int:
    """Run dispatch, normalising SystemExit to an exit code."""
    try:
        dispatch(args)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def _write_server_yaml(tmp_path, port: int, agents_yaml: str = "") -> str:
    cfg = tmp_path / "server.yaml"
    cfg.write_text(
        "server:\n" "  name: testserv\n" "  host: 127.0.0.1\n" f"  port: {port}\n" + agents_yaml
    )
    return str(cfg)


def _free_port() -> int:
    """Grab a port nothing is listening on (bind, read, close)."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ---------------------------------------------------------------------------
# serialize_residents — the canonical payload (shared with t7)
# ---------------------------------------------------------------------------


class TestSerializeResidents:
    def test_sorted_by_nick_and_deterministic(self):
        residents = [
            _resident("spark-zeta", state="idle"),
            _resident("spark-alpha", state="working"),
            _resident("spark-mid", state="thinking"),
        ]
        payload_a = serialize_residents(residents, True, now=_fixed_now())
        payload_b = serialize_residents(list(reversed(residents)), True, now=_fixed_now())
        # Byte-for-byte deterministic regardless of input order.
        assert json.dumps(payload_a) == json.dumps(payload_b)
        nicks = [r["nick"] for r in payload_a["residents"]]
        assert nicks == sorted(nicks) == ["spark-alpha", "spark-mid", "spark-zeta"]

    def test_top_level_shape(self):
        payload = serialize_residents([], False, now=_fixed_now())
        assert payload["supported"] is False
        assert payload["residents"] == []
        assert payload["generated_at"] == "2026-07-07T12:00:00Z"
        assert list(payload) == ["supported", "generated_at", "residents"]

    def test_resident_key_order_is_documented_schema(self):
        payload = serialize_residents([_resident("spark-a")], True, now=_fixed_now())
        assert list(payload["residents"][0]) == [
            "nick",
            "server",
            "state",
            "since",
            "task",
            "tokens_in",
            "tokens_out",
            "presumed_hung",
            "last_refresh",
            "token_budget",
            "budget_used_pct",
            "budget_warning",
        ]

    def test_state_only_resident_serialises_with_nones(self):
        """A state-only backend (no token counters) still yields every key."""
        payload = serialize_residents(
            [_resident("spark-a", state="listening", since="2026-07-07T11:00:00Z")],
            True,
            now=_fixed_now(),
        )
        rec = payload["residents"][0]
        assert rec["state"] == "listening"
        assert rec["tokens_in"] is None
        assert rec["tokens_out"] is None
        assert rec["token_budget"] is None
        assert rec["budget_used_pct"] is None
        assert rec["budget_warning"] is None
        assert rec["presumed_hung"] is False

    def test_supported_flag_is_normalised_to_bool(self):
        payload = serialize_residents([], 1, now=_fixed_now())
        assert payload["supported"] is True

    def test_generated_at_defaults_to_utc_now(self):
        payload = serialize_residents([], True)
        assert payload["generated_at"].endswith("Z")

    def test_naive_now_is_interpreted_as_utc(self):
        """A naive ``now`` means UTC by contract — it must never be shifted
        through the host's local timezone by ``astimezone``."""
        from datetime import datetime

        payload = serialize_residents([], True, now=datetime(2026, 7, 7, 12, 0, 0))
        assert payload["generated_at"] == "2026-07-07T12:00:00Z"


# ---------------------------------------------------------------------------
# Budget derivation — joining AgentConfig budgets onto fetched residents
# ---------------------------------------------------------------------------


def _agent(nick: str, budget: int | None = None, warn_pct: int = 80) -> AgentConfig:
    agent = AgentConfig(
        suffix=nick.split("-", 1)[1] if "-" in nick else nick,
        token_budget=budget,
        token_budget_warn_pct=warn_pct,
    )
    agent.nick = nick
    return agent


class TestBudgetDerivation:
    def test_budget_set_below_threshold(self):
        resident = _resident("spark-a", tokens_in=300, tokens_out=400)
        apply_budgets([resident], [_agent("spark-a", budget=1000, warn_pct=80)])
        assert resident.token_budget == 1000
        assert resident.budget_used_pct == 70.0
        assert resident.budget_warning is False

    def test_budget_warn_threshold_crossing_is_inclusive(self):
        resident = _resident("spark-a", tokens_in=400, tokens_out=400)
        apply_budgets([resident], [_agent("spark-a", budget=1000, warn_pct=80)])
        assert resident.budget_used_pct == 80.0
        assert resident.budget_warning is True

    def test_budget_unset_leaves_derived_fields_none(self):
        resident = _resident("spark-a", tokens_in=999999, tokens_out=999999)
        apply_budgets([resident], [_agent("spark-a", budget=None)])
        assert resident.token_budget is None
        assert resident.budget_used_pct is None
        assert resident.budget_warning is None

    def test_unregistered_nick_gets_no_budget(self):
        resident = _resident("other-b", tokens_in=10, tokens_out=10)
        apply_budgets([resident], [_agent("spark-a", budget=100)])
        assert resident.token_budget is None
        assert resident.budget_warning is None

    def test_state_only_resident_keeps_budget_but_unknown_spend(self):
        """Budget is configured but the backend sends no counters: spend is
        unknowable, so used-pct and warning stay None (never a false alarm)."""
        resident = _resident("spark-a", state="thinking")
        apply_budgets([resident], [_agent("spark-a", budget=1000)])
        assert resident.token_budget == 1000
        assert resident.budget_used_pct is None
        assert resident.budget_warning is None

    def test_single_sided_counters_count_the_present_side(self):
        resident = _resident("spark-a", tokens_in=500, tokens_out=None)
        apply_budgets([resident], [_agent("spark-a", budget=1000, warn_pct=50)])
        assert resident.budget_used_pct == 50.0
        assert resident.budget_warning is True

    def test_over_budget_pct_exceeds_100(self):
        resident = _resident("spark-a", tokens_in=1500, tokens_out=0)
        apply_budgets([resident], [_agent("spark-a", budget=1000)])
        assert resident.budget_used_pct == 150.0
        assert resident.budget_warning is True

    @pytest.mark.parametrize("bad", [0, True, -5])
    def test_unsanitized_budget_never_divides(self, bad):
        """AgentConfig objects can be constructed directly, bypassing config
        sanitizing, so token_budget 0 / True / negatives can reach the join
        — it must skip them (all derived fields stay None), never raise
        ZeroDivisionError or divide by a bool."""
        resident = _resident("spark-a", tokens_in=100, tokens_out=50)
        apply_budgets([resident], [_agent("spark-a", budget=bad)])
        assert resident.token_budget is None
        assert resident.budget_used_pct is None
        assert resident.budget_warning is None

    @pytest.mark.parametrize("bad", [0, -1, 101, 200, True, False])
    def test_unsanitized_warn_pct_falls_back_to_default(self, bad):
        """An out-of-range/bool warn-pct on a directly-constructed AgentConfig
        must fall back to the field default (80), never produce a misleading
        budget_warning flag (e.g. warn_pct=True would warn at 0.1% spend)."""
        over_default = _resident("spark-a", tokens_in=850, tokens_out=0)
        apply_budgets([over_default], [_agent("spark-a", budget=1000, warn_pct=bad)])
        assert over_default.budget_used_pct == 85.0
        assert over_default.budget_warning is True  # 85 >= default 80

        under_default = _resident("spark-a", tokens_in=700, tokens_out=0)
        apply_budgets([under_default], [_agent("spark-a", budget=1000, warn_pct=bad)])
        assert under_default.budget_used_pct == 70.0
        assert under_default.budget_warning is False  # 70 < default 80


class TestWireParsing:
    def test_bool_token_counters_are_rejected(self):
        """JSON `true` is not a token count — bools must not sneak in as ints."""
        resident = _resident_from_wire({"nick": "spark-a", "tokens_in": True, "tokens_out": False})
        assert resident.tokens_in is None
        assert resident.tokens_out is None

    def test_non_dict_and_missing_fields_default(self):
        resident = _resident_from_wire({"nick": "spark-a"})
        assert resident.nick == "spark-a"
        assert resident.state is None
        assert resident.presumed_hung is False

    def test_presumed_hung_truthiness(self):
        assert _resident_from_wire({"nick": "a", "presumed_hung": True}).presumed_hung is True
        assert _resident_from_wire({"nick": "a", "presumed_hung": None}).presumed_hung is False


# ---------------------------------------------------------------------------
# Human table renderer
# ---------------------------------------------------------------------------


class TestRenderTable:
    def test_flags_column_hung_and_budget(self):
        rows = _render_table(
            [
                _resident("spark-hung", state="working", presumed_hung=True),
                _resident(
                    "spark-hot",
                    state="thinking",
                    tokens_in=900,
                    tokens_out=100,
                    token_budget=1000,
                    budget_used_pct=100.0,
                    budget_warning=True,
                ),
                _resident("spark-ok", state="idle"),
            ]
        )
        lines = rows.splitlines()
        hung_line = next(ln for ln in lines if "spark-hung" in ln)
        hot_line = next(ln for ln in lines if "spark-hot" in ln)
        ok_line = next(ln for ln in lines if "spark-ok" in ln)
        assert "HUNG?" in hung_line
        assert "BUDGET" in hot_line
        assert "HUNG?" not in hot_line
        assert "HUNG?" not in ok_line and "BUDGET" not in ok_line

    def test_both_flags_together(self):
        rows = _render_table(
            [
                _resident(
                    "spark-bad",
                    presumed_hung=True,
                    budget_warning=True,
                    token_budget=10,
                    budget_used_pct=200.0,
                )
            ]
        )
        line = next(ln for ln in rows.splitlines() if "spark-bad" in ln)
        assert "HUNG?,BUDGET" in line

    def test_missing_fields_render_as_dashes(self):
        rows = _render_table([_resident("spark-bare")])
        line = next(ln for ln in rows.splitlines() if "spark-bare" in ln)
        # state, since, task, tokens, budget-pct, flags all dashed
        assert line.split()[1:] == ["-", "-", "-", "-", "-", "-", "-"]

    def test_tokens_column_formats_in_out(self):
        rows = _render_table([_resident("spark-a", tokens_in=1200, tokens_out=34)])
        line = next(ln for ln in rows.splitlines() if "spark-a" in ln)
        assert "1200/34" in line

    def test_single_sided_tokens(self):
        rows = _render_table([_resident("spark-a", tokens_in=7)])
        line = next(ln for ln in rows.splitlines() if "spark-a" in ln)
        assert "7/-" in line

    def test_header_names_all_columns(self):
        header = _render_table([_resident("spark-a")]).splitlines()[0]
        for col in ("NICK", "SERVER", "STATE", "SINCE", "TASK", "TOKENS", "BUDGET", "FLAGS"):
            assert col in header

    def test_rows_sorted_by_nick(self):
        rows = _render_table([_resident("spark-z"), _resident("spark-a")])
        lines = rows.splitlines()
        assert lines.index(next(ln for ln in lines if "spark-a" in ln)) < lines.index(
            next(ln for ln in lines if "spark-z" in ln)
        )


# ---------------------------------------------------------------------------
# Supported path against a REAL server (agentirc >= 9.12.0 answers the query)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_succeeds_against_real_server(server):
    """agentirc >= 9.12.0 answers PRESENCE LIST (agentirc#53). No resident
    has published presence here — including the query observer itself, which
    never sends PRESENCE — so the registry is empty, not an error."""
    config = ServerConfig(
        server=ServerConnConfig(name="testserv", host="127.0.0.1", port=server.config.port)
    )
    residents = await fetch_residents_async(config)
    assert residents == []


@pytest.mark.asyncio
async def test_cli_json_reports_supported_true_against_real_server(
    server, tmp_path, capsys, monkeypatch
):
    """`culture residents --json` against a real 9.12+ server: exit 0,
    {"supported": true, "residents": []} (empty presence registry)."""
    monkeypatch.delenv("CULTURE_NICK", raising=False)
    cfg = _write_server_yaml(tmp_path, server.config.port)
    # dispatch runs asyncio.run internally — hop to a thread since this
    # test already runs inside an event loop.
    code = await asyncio.to_thread(_run_dispatch, _make_args(cfg, json_mode=True))
    assert code == 0
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["supported"] is True
    assert payload["residents"] == []
    # Exactly the shared serializer's shape, byte-for-byte.
    assert list(payload) == ["supported", "generated_at", "residents"]
    # And literally the shared dumps site: re-serializing through the one
    # canonical to_json(serialize_residents(...)) reproduces the CLI bytes
    # exactly (the endpoint asserts the same in test_residents_endpoint.py).
    from datetime import datetime

    stamp = datetime.fromisoformat(payload["generated_at"].replace("Z", "+00:00"))
    assert out == to_json(serialize_residents([], True, now=stamp))


# ---------------------------------------------------------------------------
# Graceful degrade against a pre-9.12 server (stock 421 unknown-command path)
# ---------------------------------------------------------------------------


async def _scripted_421_server() -> tuple[asyncio.AbstractServer, int]:
    """A real TCP listener speaking the pre-9.12 answer: 421 to PRESENCE."""

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        buffer = ""
        nick = "*"
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    return
                buffer += data.decode()
                while "\r\n" in buffer:
                    line, buffer = buffer.split("\r\n", 1)
                    if line.startswith("NICK "):
                        nick = line.split(" ", 1)[1]
                        writer.write(f":scripted 001 {nick} :Welcome\r\n".encode())
                        await writer.drain()
                    elif line.startswith("PRESENCE"):
                        writer.write(f":scripted 421 {nick} PRESENCE :Unknown command\r\n".encode())
                        await writer.drain()
                    elif line.startswith("QUIT"):
                        writer.close()
                        return
        except (ConnectionError, OSError):
            return

    srv = await asyncio.start_server(_handle, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    return srv, port


@pytest.mark.asyncio
async def test_fetch_raises_unsupported_against_pre912_server():
    """A pre-9.12 server replies 421 to PRESENCE — surface as unsupported."""
    srv, port = await _scripted_421_server()
    try:
        config = ServerConfig(server=ServerConnConfig(name="scripted", host="127.0.0.1", port=port))
        with pytest.raises(PresenceUnsupportedError):
            await fetch_residents_async(config)
    finally:
        srv.close()
        await srv.wait_closed()


@pytest.mark.asyncio
async def test_cli_json_degrades_to_supported_false(tmp_path, capsys, monkeypatch):
    """`culture residents --json` against a presence-less server: exit 0,
    {"supported": false, "residents": []}."""
    monkeypatch.delenv("CULTURE_NICK", raising=False)
    srv, port = await _scripted_421_server()
    try:
        cfg = _write_server_yaml(tmp_path, port)
        code = await asyncio.to_thread(_run_dispatch, _make_args(cfg, json_mode=True))
    finally:
        srv.close()
        await srv.wait_closed()
    assert code == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["supported"] is False
    assert payload["residents"] == []


@pytest.mark.asyncio
async def test_cli_table_degrades_with_notice(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("CULTURE_NICK", raising=False)
    srv, port = await _scripted_421_server()
    try:
        cfg = _write_server_yaml(tmp_path, port)
        code = await asyncio.to_thread(_run_dispatch, _make_args(cfg, json_mode=False))
    finally:
        srv.close()
        await srv.wait_closed()
    assert code == 0
    out = capsys.readouterr().out
    assert "does not support PRESENCE" in out
    assert "agentirc#53" in out


# ---------------------------------------------------------------------------
# Unreachable server — actionable error, nonzero exit, no traceback
# ---------------------------------------------------------------------------


def test_cli_unreachable_server_errors_actionably(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("CULTURE_NICK", raising=False)
    cfg = _write_server_yaml(tmp_path, _free_port())
    code = _run_dispatch(_make_args(cfg, json_mode=False))
    assert code != 0
    captured = capsys.readouterr()
    assert "error:" in captured.err
    assert "cannot connect" in captured.err
    assert "hint:" in captured.err
    assert "Traceback" not in captured.err


def test_cli_unreachable_server_json_error_contract(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("CULTURE_NICK", raising=False)
    cfg = _write_server_yaml(tmp_path, _free_port())
    code = _run_dispatch(_make_args(cfg, json_mode=True))
    assert code != 0
    captured = capsys.readouterr()
    err_payload = json.loads(captured.err.strip())
    assert set(err_payload) == {"code", "message", "remediation"}
    assert "cannot connect" in err_payload["message"]


# ---------------------------------------------------------------------------
# Config errors are config errors — never misreported as "server down"
# ---------------------------------------------------------------------------


def test_cli_unreadable_config_reports_config_error_not_server_down(tmp_path, capsys, monkeypatch):
    """A config path that cannot be read (here: a directory) is a config
    error — it must not be swallowed by the connection handling and
    misreported as 'cannot connect to IRC server'."""
    monkeypatch.delenv("CULTURE_NICK", raising=False)
    bad_config = tmp_path / "server.yaml"
    bad_config.mkdir()  # open() raises IsADirectoryError (an OSError)
    code = _run_dispatch(_make_args(str(bad_config), json_mode=False))
    assert code != 0
    captured = capsys.readouterr()
    assert "cannot read server config" in captured.err
    assert str(bad_config) in captured.err
    assert "hint:" in captured.err
    assert "cannot connect" not in captured.err
    assert "Traceback" not in captured.err


def test_cli_unreadable_config_json_error_contract(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("CULTURE_NICK", raising=False)
    bad_config = tmp_path / "server.yaml"
    bad_config.mkdir()
    code = _run_dispatch(_make_args(str(bad_config), json_mode=True))
    assert code != 0
    err_payload = json.loads(capsys.readouterr().err.strip())
    assert set(err_payload) == {"code", "message", "remediation"}
    assert "cannot read server config" in err_payload["message"]
    assert "cannot connect" not in err_payload["message"]


def test_cli_config_validation_error_honors_json_contract(tmp_path, capsys, monkeypatch):
    """A CultureError from config validation (bad presence section) must be
    emitted through the verb's --json error format — previously it bypassed
    the json-aware emit path entirely."""
    monkeypatch.delenv("CULTURE_NICK", raising=False)
    cfg = tmp_path / "server.yaml"
    cfg.write_text("server:\n  name: testserv\npresence: false\n")
    code = _run_dispatch(_make_args(str(cfg), json_mode=True))
    assert code != 0
    err_payload = json.loads(capsys.readouterr().err.strip())
    assert set(err_payload) == {"code", "message", "remediation"}
    assert "presence" in err_payload["message"]


# ---------------------------------------------------------------------------
# Adopted wire contract (the seam t7 builds on) — real scripted TCP server
# ---------------------------------------------------------------------------


async def _scripted_presence_server(records: list[dict]) -> tuple[asyncio.AbstractServer, int]:
    """A real TCP listener speaking the PRESENCE query wire form.

    Registers any client (001 on NICK) and answers PRESENCE LIST with one
    PRESENCELIST line per record, then PRESENCEEND — the shape proposed in
    the t3 hand-off brief and adopted verbatim by agentirc-cli 9.12.0
    (agentirc#53). Payloads are raw UTF-8 JSON per the wire contract
    (``ensure_ascii=False``), matching what the real server emits.
    """

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        buffer = ""
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    return
                buffer += data.decode()
                while "\r\n" in buffer:
                    line, buffer = buffer.split("\r\n", 1)
                    if line.startswith("NICK "):
                        nick = line.split(" ", 1)[1]
                        writer.write(f":scripted 001 {nick} :Welcome\r\n".encode())
                        await writer.drain()
                    elif line.startswith("PRESENCE LIST"):
                        for rec in records:
                            payload = json.dumps(rec, ensure_ascii=False)
                            writer.write(f"PRESENCELIST :{payload}\r\n".encode())
                        writer.write(b"PRESENCEEND :End of presence list\r\n")
                        await writer.drain()
                    elif line.startswith("QUIT"):
                        writer.close()
                        return
        except (ConnectionError, OSError):
            return

    srv = await asyncio.start_server(_handle, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    return srv, port


@pytest.mark.asyncio
async def test_fetch_parses_anticipated_wire_and_joins_budgets():
    records = [
        {
            "nick": "spark-claude",
            "server": "spark",
            "state": "thinking",
            "since": "2026-07-07T11:00:00Z",
            "task": "review PR #471",
            "tokens_in": 900,
            "tokens_out": 100,
            "presumed_hung": False,
            "last_refresh": "2026-07-07T11:59:30Z",
        },
        {"nick": "thor-codex", "server": "thor", "state": "idle"},
    ]
    srv, port = await _scripted_presence_server(records)
    try:
        config = ServerConfig(
            server=ServerConnConfig(name="scripted", host="127.0.0.1", port=port),
            agents=[_agent("spark-claude", budget=1000, warn_pct=80)],
        )
        residents = await fetch_residents_async(config)
    finally:
        srv.close()
        await srv.wait_closed()

    assert [r.nick for r in sorted(residents, key=lambda r: r.nick)] == [
        "spark-claude",
        "thor-codex",
    ]
    claude = next(r for r in residents if r.nick == "spark-claude")
    assert claude.state == "thinking"
    assert claude.task == "review PR #471"
    assert claude.tokens_in == 900
    assert claude.token_budget == 1000
    assert claude.budget_used_pct == 100.0
    assert claude.budget_warning is True
    codex = next(r for r in residents if r.nick == "thor-codex")
    assert codex.state == "idle"
    assert codex.tokens_in is None
    assert codex.budget_warning is None


@pytest.mark.asyncio
async def test_fetch_preserves_non_ascii_payload():
    """The payload contract is UTF-8 JSON — task text with real multi-byte
    characters must round-trip the wire uncorrupted."""
    task_text = "café ☕ — résumé"
    srv, port = await _scripted_presence_server(
        [{"nick": "spark-a", "state": "working", "task": task_text}]
    )
    try:
        config = ServerConfig(server=ServerConnConfig(name="scripted", host="127.0.0.1", port=port))
        residents = await fetch_residents_async(config)
    finally:
        srv.close()
        await srv.wait_closed()
    assert residents[0].task == task_text


@pytest.mark.asyncio
async def test_utf8_char_split_across_reads_survives():
    """A multi-byte UTF-8 character split across TCP read boundaries must
    never be corrupted: framing is byte-level, decoding per complete line
    (the old per-chunk ``decode(errors="replace")`` mangled the char)."""
    from culture_core.resource_view import _drain_presence_buffer

    payload = json.dumps({"nick": "spark-a", "task": "café"}, ensure_ascii=False).encode()
    wire = b"PRESENCELIST :" + payload + b"\r\nPRESENCEEND :End of presence list\r\n"
    cut = wire.index(b"\xc3") + 1  # mid-character: between the two bytes of 'é'
    records: list[dict] = []

    done, remainder = await _drain_presence_buffer(wire[:cut], records, writer=None)
    assert done is False and records == []  # no complete line yet

    done, remainder = await _drain_presence_buffer(remainder + wire[cut:], records, writer=None)
    assert done is True
    assert remainder == b""
    assert records == [{"nick": "spark-a", "task": "café"}]


# ---------------------------------------------------------------------------
# Mid-stream stall/drop is a connection error, NOT "unsupported" — a server
# that already streamed PRESENCELIST records clearly speaks the surface;
# reporting it as a healthy supported:false would discard real residents.
# ---------------------------------------------------------------------------


async def _stalling_presence_server(
    records: list[dict], *, close_after: bool
) -> tuple[asyncio.AbstractServer, int]:
    """A scripted server that streams records but never sends PRESENCEEND.

    With ``close_after=True`` it drops the connection right after the last
    record; with ``close_after=False`` it goes silent (the client's recv
    timeout fires).
    """

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        buffer = ""
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    return
                buffer += data.decode()
                while "\r\n" in buffer:
                    line, buffer = buffer.split("\r\n", 1)
                    if line.startswith("NICK "):
                        nick = line.split(" ", 1)[1]
                        writer.write(f":scripted 001 {nick} :Welcome\r\n".encode())
                        await writer.drain()
                    elif line.startswith("PRESENCE LIST"):
                        for rec in records:
                            writer.write(f"PRESENCELIST :{json.dumps(rec)}\r\n".encode())
                        await writer.drain()
                        if close_after:
                            return
                        # else: go silent — never send PRESENCEEND
        except (ConnectionError, OSError):
            return
        finally:
            # Always close the server-side transport, or Server.wait_closed()
            # in the test teardown waits forever for the connection to detach.
            writer.close()

    srv = await asyncio.start_server(_handle, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    return srv, port


@pytest.mark.asyncio
async def test_mid_stream_close_is_connection_error_not_unsupported():
    srv, port = await _stalling_presence_server([{"nick": "spark-a"}], close_after=True)
    try:
        config = ServerConfig(server=ServerConnConfig(name="scripted", host="127.0.0.1", port=port))
        with pytest.raises(ConnectionError, match=r"stalled after 1 record"):
            await fetch_residents_async(config)
    finally:
        srv.close()
        await srv.wait_closed()


@pytest.mark.asyncio
async def test_mid_stream_silence_is_connection_error_not_unsupported(monkeypatch):
    monkeypatch.setattr("culture_core.resource_view.RECV_TIMEOUT", 0.3)
    srv, port = await _stalling_presence_server([{"nick": "spark-a"}], close_after=False)
    try:
        config = ServerConfig(server=ServerConnConfig(name="scripted", host="127.0.0.1", port=port))
        with pytest.raises(ConnectionError, match=r"stalled after 1 record"):
            await fetch_residents_async(config)
    finally:
        srv.close()
        await srv.wait_closed()


@pytest.mark.asyncio
async def test_cli_reports_stalled_stream_as_error_not_supported_false(
    tmp_path, capsys, monkeypatch
):
    """CLI path: a stalled stream exits nonzero through the connection-error
    branch — never exit 0 with a healthy-looking supported:false."""
    monkeypatch.delenv("CULTURE_NICK", raising=False)
    monkeypatch.setattr("culture_core.resource_view.RECV_TIMEOUT", 0.3)
    srv, port = await _stalling_presence_server([{"nick": "spark-a"}], close_after=False)
    try:
        cfg = _write_server_yaml(tmp_path, port)
        code = await asyncio.to_thread(_run_dispatch, _make_args(cfg, json_mode=False))
    finally:
        srv.close()
        await srv.wait_closed()
    assert code != 0
    captured = capsys.readouterr()
    assert "cannot connect" in captured.err
    assert "supported" not in captured.out
