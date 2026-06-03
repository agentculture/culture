"""Regression: Qodo PR #50 #5 — input_regex type-confusion bypass.

The prior high-risk sticky-allow gate used a truthy check
(``bool(input_regex)``) at the entry boundary, while the persistence
path only stored ``input_regex`` when it was a non-empty ``str``. A
non-string truthy value (``[1, 2, 3]``, ``{"k": "v"}``, a custom
object) would therefore:

  1. PASS the gate (it is truthy → narrowing requirement satisfied)
  2. FAIL the persistence guard (it is not a ``str`` → dropped)

Net effect: an attacker who can write a decision file (the dashboard
JSON path is the realistic attack surface — its body is parsed and
forwarded unchecked) could produce a bare ``Bash`` / ``Edit`` /
``Write`` / ``mcp__*`` sticky-allow that auto-approves EVERY future
invocation of the tool — exactly what the gate exists to prevent.

The remediation is a single ``_is_valid_input_regex`` helper that
the gate calls at both ``write_decision`` (entry boundary) and
``_append_sticky_rule`` (persistence boundary), AND a type-check at
the dashboard's JSON-body boundary that returns HTTP 400 for non-
string fields.

This file locks every shape we considered: missing / None / empty
str / whitespace-only / list / dict / int / bool / valid str. Both
broker-level (``write_decision`` raising) and HTTP-level (dashboard
returning 400) are covered.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from tests._sdk_stub import install_claude_sdk_stub

install_claude_sdk_stub()

from culture.clients import _perm_broker as broker  # noqa: E402
from culture.dashboard.server import build_app  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    monkeypatch.setenv("CULTURE_NICK", "local-test")
    return tmp_path


def _request_id() -> str:
    """Mint a fresh broker-shaped request id for each test."""
    return broker._new_request_id()


class TestIsValidInputRegexHelper:
    """Direct unit tests for the centralized validator."""

    @pytest.mark.parametrize("value", ["a", "Bash:.*", "  not-empty  ", "^x$"])
    def test_non_empty_string_is_valid(self, value: str) -> None:
        assert broker._is_valid_input_regex(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            " ",
            "\t",
            "\n",
            "   \n\t  ",
            [],
            [1, 2, 3],
            ["Bash"],
            {},
            {"regex": "x"},
            0,
            1,
            True,
            False,
            object(),
        ],
    )
    def test_everything_else_is_invalid(self, value: object) -> None:
        assert broker._is_valid_input_regex(value) is False


class TestWriteDecisionRejectsNonStringRegex:
    """The broker entry boundary raises BareStickyApproveRefusedError
    on any non-string-truthy input_regex paired with a high-risk tool.
    """

    @pytest.mark.parametrize(
        "bogus_regex",
        [
            [1, 2, 3],
            ["pattern"],
            {"regex": "x"},
            {"k": "v"},
            42,
            True,
        ],
    )
    def test_high_risk_bash_with_non_str_regex_refused(self, home, bogus_regex) -> None:
        with pytest.raises(broker.BareStickyApproveRefusedError):
            broker.write_decision(
                _request_id(),
                verdict="allow",
                scope="always",
                tool_name="Bash",
                input_regex=bogus_regex,  # type: ignore[arg-type]
            )

    def test_high_risk_mcp_with_non_str_regex_refused(self, home) -> None:
        with pytest.raises(broker.BareStickyApproveRefusedError):
            broker.write_decision(
                _request_id(),
                verdict="allow",
                scope="always",
                tool_name="mcp__internal__send",
                input_regex=[1, 2, 3],  # type: ignore[arg-type]
            )

    def test_high_risk_pattern_smuggle_with_non_str_regex_refused(self, home) -> None:
        # ``--pattern Bash --tool Foo`` is the documented smuggle vector.
        # Pair it with a non-str regex and the gate must still fire.
        with pytest.raises(broker.BareStickyApproveRefusedError):
            broker.write_decision(
                _request_id(),
                verdict="allow",
                scope="always",
                tool_name="Foo",
                pattern="Bash",
                input_regex={"k": "v"},  # type: ignore[arg-type]
            )

    def test_whitespace_only_string_refused(self, home) -> None:
        with pytest.raises(broker.BareStickyApproveRefusedError):
            broker.write_decision(
                _request_id(),
                verdict="allow",
                scope="always",
                tool_name="Bash",
                input_regex="   \t\n   ",
            )

    def test_valid_string_regex_accepted(self, home) -> None:
        """The happy path still works."""
        path = broker.write_decision(
            _request_id(),
            verdict="allow",
            scope="always",
            tool_name="Bash",
            input_regex=r"^Bash:ls( |$)",
        )
        assert os.path.exists(path)


class TestPersistedRuleHasInputRegexWhenAccepted:
    """Belt+braces: confirm the persisted sticky rule actually carries
    the input_regex when the gate accepts the call."""

    def test_persisted_rule_includes_input_regex(self, home) -> None:
        # Simulate the boss-side append by calling _append_sticky_rule
        # directly with a decision the broker just persisted.
        b = broker.PermissionBroker(nick="local-test")
        b._append_sticky_rule(
            verdict="allow",
            tool_name="Bash",
            decision={
                "verdict": "allow",
                "scope": "always",
                "input_regex": r"^Bash:ls( |$)",
            },
        )
        # Read the policy back and check the rule has input_regex.
        import yaml

        with open(broker.policy_path_for("local-test"), encoding="utf-8") as fh:
            policy = yaml.safe_load(fh)
        rules = policy.get("auto_allow", [])
        assert len(rules) == 1
        assert rules[0]["tool"] == "Bash"
        assert rules[0]["input_regex"] == r"^Bash:ls( |$)"

    def test_append_with_non_str_regex_refused_at_persist_layer(self, home) -> None:
        """Defense-in-depth: even if write_decision were bypassed (a
        future writer skips it), _append_sticky_rule still rejects."""
        b = broker.PermissionBroker(nick="local-test")
        with pytest.raises(broker.BareStickyApproveRefusedError):
            b._append_sticky_rule(
                verdict="allow",
                tool_name="Bash",
                decision={
                    "verdict": "allow",
                    "scope": "always",
                    "input_regex": [1, 2, 3],
                },
            )


@pytest_asyncio.fixture
async def dashboard_client(home):
    app = build_app(config_path=os.path.join(str(home), "server.yaml"))
    async with TestClient(TestServer(app)) as c:
        yield c


class TestDashboardRejectsNonStringTypes:
    """At the JSON boundary the dashboard MUST reject non-string
    tool / input_regex / pattern with HTTP 400 — and never call
    write_decision with the bad value."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "field,bogus",
        [
            ("input_regex", [1, 2, 3]),
            ("input_regex", {"k": "v"}),
            ("input_regex", 42),
            ("tool", [1, 2]),
            ("tool", {"name": "Bash"}),
            ("pattern", [1, 2]),
            ("pattern", {"k": "v"}),
        ],
    )
    async def test_non_string_field_rejected_400(self, dashboard_client, field, bogus) -> None:
        req_id = _request_id()
        # Put a real pending request on disk so the broker has something
        # to write a decision against.
        os.makedirs(broker._queue_dir(), mode=0o700, exist_ok=True)
        with open(os.path.join(broker._queue_dir(), f"{req_id}.json"), "w") as fh:
            fh.write('{"id":"' + req_id + '","tool":"Bash","input":{},"requested_by":"x"}')

        body: dict = {"id": req_id, "always": True, "tool": "Bash"}
        body[field] = bogus
        resp = await dashboard_client.post("/api/approve", json=body)
        assert resp.status == 400, f"expected 400 for {field}={bogus!r}, got {resp.status}"
        data = await resp.json()
        assert field in data.get("error", ""), f"error did not mention {field}: {data!r}"

    @pytest.mark.asyncio
    async def test_valid_string_payload_accepted(self, dashboard_client) -> None:
        req_id = _request_id()
        os.makedirs(broker._queue_dir(), mode=0o700, exist_ok=True)
        with open(os.path.join(broker._queue_dir(), f"{req_id}.json"), "w") as fh:
            fh.write('{"id":"' + req_id + '","tool":"Bash","input":{},"requested_by":"x"}')
        resp = await dashboard_client.post(
            "/api/approve",
            json={
                "id": req_id,
                "always": True,
                "tool": "Bash",
                "input_regex": r"^Bash:ls( |$)",
            },
        )
        assert resp.status == 200
        data = await resp.json()
        assert data == {"ok": True}
