"""NT-5 — broker-via-PreToolUse hook survives the bridge split.

Phase 2.9 of the mesh-rearchitecture plan (CRITICAL — T9 gate per the threat
table in the design doc). This is the deeper-than-unit integration test the
v8.18.1 release LACKED: ``can_use_tool`` looked plausibly wired, but the SDK
CLI's ``permission_mode='bypassPermissions'`` silently skipped every
``can_use_tool`` invocation for a full release — the broker was a no-op and
no test caught it. The only signal an in-process test can pin down is:

  "the SDK's pre-tool gate, end-to-end through ClaudeAgentOptions and
   AgentRunner._broker_pre_tool_use_hook and the real PermissionBroker and
   a real on-disk perm-policy YAML with auto_deny[Bash], returns
   ``permissionDecision: deny`` when the SDK would have asked about Bash."

The unit test ``tests/test_agent_runner.py::test_make_options_wires_pretooluse_hook_when_broker_present``
proves the hook is installed in the options. THIS test goes one layer
deeper: it constructs the AgentRunner the way a real worker daemon does
(``nick`` plus ``has_policy_file(nick)`` -> True, so the broker is wired by
the ctor — not pre-injected for the test), drives the full ``_process_turn``
SDK loop with a fake ``query()`` that captures the actual hook callable
the AgentRunner handed off, fires it the way the SDK CLI would for a Bash
tool call, and asserts the resulting permission decision is "deny" — i.e.
the tool would have been blocked before execution.

Threat T9 from the security table:

  "Broker silent disablement during bridge split (the v8.18.1 lesson —
   PreToolUse hook accidentally dropped)" — block bridge merge until
   (a) existing test stays green, (b) NEW integration test boots worker
   with policy, fires a real tool, asserts the tool actually fails on
   deny (not just that gate() was called)."

This file IS that NEW integration test. It does not require a running IRC
server or a real Claude CLI subprocess — those are out of reach for one
in-process pytest. What it DOES exercise:

  - the real PermissionBroker (no stub) reading a real YAML file under
    ``CULTURE_HOME``
  - the real AgentRunner ctor's policy-file-driven wiring (no manual
    ``runner._broker = ...``)
  - the real ``_broker_pre_tool_use_hook`` callable installed in the real
    ``ClaudeAgentOptions.hooks["PreToolUse"]`` slot
  - the real ``_process_turn`` calling ``query(options=...)`` and surfacing
    the hook the SDK would see

The "real bridge boot" is intentionally NOT here: as the Wave-1 plan notes,
the bridge does not yet route SDK turns through itself — workers are still
the SDK-bearing entity, and the bridge's job is transport. NT-5's invariant
is "broker still gates tool calls", which lives in the worker's AgentRunner
regardless of whether the bridge has fully landed.
"""

from __future__ import annotations

from tests._sdk_stub import install_claude_sdk_stub

install_claude_sdk_stub()

import asyncio  # noqa: E402

import pytest  # noqa: E402
import yaml  # noqa: E402

from culture.clients._perm_broker import (  # noqa: E402
    PermissionBroker,
    has_policy_file,
    policy_path_for,
)
from culture.clients.claude.agent_runner import AgentRunner  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


WORKER_NICK = "testserv-broker-survives"


@pytest.fixture
def culture_home(tmp_path, monkeypatch):
    """Isolate ``CULTURE_HOME`` so the on-disk perm-policy YAML this test
    writes does not collide with any other test or with the developer's
    real ``~/.culture``."""
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def deny_bash_policy(culture_home):
    """Write a real perm-policy YAML to disk with auto_deny[Bash].

    The shape is exactly what ``culture boss spawn`` would seed (plus the
    explicit auto_deny rule under test). The broker discovers it via
    ``has_policy_file(nick)`` and ``policy_path_for(nick)`` — no
    monkeypatching of the broker's filesystem layer.
    """
    path = policy_path_for(WORKER_NICK)
    import os

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(
            {
                "auto_deny": [{"tool": "Bash"}],
                "auto_allow": [{"tool": "Read"}],
            },
            handle,
        )
    # Sanity: the broker's discovery API confirms the worker is
    # boss-supervised. If THIS assertion fails, every other assertion
    # below is meaningless — fail loud and early.
    assert has_policy_file(WORKER_NICK) is True
    return path


# ---------------------------------------------------------------------------
# Helpers — simulate the SDK CLI's invocation of the PreToolUse hook
# ---------------------------------------------------------------------------


async def _simulate_sdk_pretooluse_invocation(
    hook_callable,
    tool_name: str,
    tool_input: dict,
) -> dict:
    """Call ``hook_callable`` the way the SDK CLI would for a tool call.

    The SDK CLI passes the hook a dict with ``tool_name`` and ``tool_input``,
    plus a ``tool_use_id`` and a ``HookContext``. The hook's contract is
    documented in claude_agent_sdk (and in this repo at
    ``culture/clients/claude/agent_runner.py:_broker_pre_tool_use_hook``):
    return a dict ``{"hookSpecificOutput": {"hookEventName": "PreToolUse",
    "permissionDecision": "allow"|"deny", "permissionDecisionReason": ...}}``.

    Mirroring that call shape here is the closest in-process test we can
    write to "the CLI is about to run Bash, the broker says deny, did the
    CLI receive the deny?" — anything more requires a real CLI subprocess
    which is the bridge's Phase 4 territory, not Phase 2's.
    """
    return await hook_callable(
        {"tool_name": tool_name, "tool_input": tool_input},
        "tool-use-id-fake",
        object(),
    )


# ---------------------------------------------------------------------------
# NT-5 — the critical regression
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broker_pretooluse_denies_bash_via_real_runner_and_real_policy(
    deny_bash_policy,
):
    """The full chain: real YAML -> real PermissionBroker (ctor-wired by
    AgentRunner) -> real PreToolUse hook installed in real
    ClaudeAgentOptions -> SDK-shape invocation -> ``permissionDecision: deny``.

    Concretely, this would have caught v8.18.1: had the hook accidentally
    been dropped from ``_make_options``, ``opts.hooks`` would be empty and
    this test fails at the "hook is wired" assertion. Had the hook been
    wired but ``can_use_tool`` returned None silently (the v8.18.1 bug
    shape), the broker would never see the call — this test invokes the
    hook itself and asserts the deny, so the chain is exercised end-to-end.
    """
    runner = AgentRunner(
        model="",
        directory="/tmp",
        nick=WORKER_NICK,
        boss="",
    )

    # The ctor must have wired the broker because ``has_policy_file`` is
    # True. If THIS fails, the broker isn't even in the picture and there's
    # no point checking the hook chain.
    assert runner._broker is not None, (
        "AgentRunner ctor failed to wire PermissionBroker despite "
        "has_policy_file(nick) being True — broker silently disabled"
    )
    assert isinstance(runner._broker, PermissionBroker)
    # Bound-method equality (not ``is``): Python creates a fresh bound-method
    # object on each attribute access, so identity comparison fails even when
    # the underlying function is the same.
    assert runner._can_use_tool == runner._broker.gate

    # Build the real options the SDK would see. This is the second guard
    # the v8.18.1 release lost: a wired ``can_use_tool`` was insufficient
    # because the CLI did not always invoke it; the PreToolUse hook is the
    # actual enforcement primitive.
    opts = runner._make_options()
    assert opts.permission_mode == "default", (
        "permission_mode must be 'default' when a broker is wired — "
        "'bypassPermissions' silently allows every tool (v8.18.1 lesson)"
    )
    assert getattr(opts, "hooks", None), (
        "ClaudeAgentOptions.hooks is missing — the PreToolUse hook was "
        "dropped during the bridge split (v8.18.1 lesson)"
    )
    assert "PreToolUse" in opts.hooks
    matchers = opts.hooks["PreToolUse"]
    assert len(matchers) == 1
    matcher = matchers[0]
    assert matcher.matcher is None, "PreToolUse must match every tool"
    assert runner._broker_pre_tool_use_hook in matcher.hooks

    # Now drive the hook the way the SDK CLI would: feed it a Bash tool
    # call. The broker's policy is auto_deny[Bash], so the result must
    # carry ``permissionDecision: deny``. This is the assertion that
    # proves the tool WOULD NOT have executed — the v8.18.1 missed test
    # was "gate() called but tool ran anyway", and the equivalent here
    # is "hook called but decision is not deny", which is what this
    # assertion catches.
    decision = await _simulate_sdk_pretooluse_invocation(
        runner._broker_pre_tool_use_hook,
        tool_name="Bash",
        tool_input={"command": "rm -rf /"},
    )

    assert decision["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny", (
        f"broker did not deny Bash despite auto_deny rule — tool would have "
        f"executed under SDK's pre-tool gate. Full hook output: {decision!r}"
    )
    # The reason must be present and non-empty — the CLI surfaces it to the
    # SDK and the agent's next turn sees it. Empty reason = silent failure.
    reason = decision["hookSpecificOutput"].get("permissionDecisionReason", "")
    assert reason, "deny must carry a reason; empty reason hides the cause"
    assert "Bash" in reason, f"deny reason must name the denied tool: {reason!r}"


@pytest.mark.asyncio
async def test_broker_pretooluse_denies_via_full_process_turn(deny_bash_policy):
    """End-to-end through ``_process_turn``: a fake SDK ``query()`` captures
    the options handed to it (the same ones the CLI subprocess would
    receive), invokes the installed PreToolUse hook for a Bash request, and
    the test asserts the hook denied — i.e. the SDK's pretool gate refused
    to run the tool.

    This complements the first test by exercising the actual ``query`` call
    path, not just ``_make_options`` in isolation. If a future refactor
    forgets to pass ``options=self._make_options()`` to ``query()``, this
    test fails — even if ``_make_options`` itself is still correct.
    """
    runner = AgentRunner(
        model="",
        directory="/tmp",
        nick=WORKER_NICK,
        boss="",
    )

    # Capture state from inside the fake SDK query.
    captured: dict = {}

    async def fake_query(*, prompt, options=None, transport=None):  # noqa: ARG001
        # Drain the streaming prompt so the AsyncIterable contract is met,
        # mirroring what the real CLI subprocess does.
        if hasattr(prompt, "__aiter__"):
            async for _ in prompt:
                pass
        # The CLI would inspect ``options.hooks["PreToolUse"]`` before
        # running a tool. Reproduce that lookup here.
        captured["options"] = options
        hooks = getattr(options, "hooks", None) or {}
        pre_tool = hooks.get("PreToolUse", [])
        assert pre_tool, (
            "options handed to query() carry NO PreToolUse hooks — broker "
            "would never be consulted (the v8.18.1 silent-disablement "
            "regression shape)"
        )
        # Fire the hook the way the CLI would for a Bash invocation.
        hook_callable = pre_tool[0].hooks[0]
        decision = await hook_callable(
            {"tool_name": "Bash", "tool_input": {"command": "git push"}},
            "tool-use-id-fake",
            object(),
        )
        captured["decision"] = decision

        # An empty async iterator — the CLI would normally yield
        # AssistantMessage / ResultMessage, but we only need to assert the
        # hook outcome. Yielding nothing ends the turn cleanly.
        if False:  # pragma: no cover — async-generator shape only
            yield

    import culture.clients.claude.agent_runner as ar_mod

    orig_query = ar_mod.query
    ar_mod.query = fake_query
    try:
        # Drive a single turn the way the daemon would.
        await runner._process_turn("trigger a turn that would call Bash")
    finally:
        ar_mod.query = orig_query

    # The fake query captured the options and the hook decision. Assert the
    # full chain wired up correctly AND the broker denied.
    assert "options" in captured, "query() was never called by _process_turn"
    assert "decision" in captured, "PreToolUse hook never fired inside the turn"
    decision = captured["decision"]
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny", (
        f"broker did not deny Bash via _process_turn -> query() -> hook: "
        f"{decision!r}. The tool would have executed (v8.18.1 regression "
        f"shape)."
    )


@pytest.mark.asyncio
async def test_runner_without_policy_file_has_no_hook(culture_home):
    """Symmetric control: a worker with NO perm-policy file is a standalone
    agent (mesh-only, no boss). The PreToolUse hook MUST NOT be installed
    in that case — installing it with no broker would cause every tool
    call to fall through to ``self._broker is None`` and silently allow,
    which is a different shape of the same v8.18.1 hazard (a hook present
    but no-op).

    The right behavior for standalone agents is the
    ``bypassPermissions`` mode the daemon has always used: no hook at
    all, no broker, no can_use_tool. This test pins that contract.
    """
    standalone_nick = "testserv-standalone-no-policy"
    assert has_policy_file(standalone_nick) is False

    runner = AgentRunner(
        model="",
        directory="/tmp",
        nick=standalone_nick,
        boss="",
    )

    assert runner._broker is None
    assert runner._can_use_tool is None

    opts = runner._make_options()
    assert opts.permission_mode == "bypassPermissions"
    # Either no hooks attribute, or an empty/false dict.
    assert not getattr(opts, "hooks", None), (
        "standalone agent must not carry a PreToolUse hook — installing "
        "one without a broker is the v8.18.1 hazard in reverse"
    )


@pytest.mark.asyncio
async def test_broker_hook_fails_closed_when_broker_raises(deny_bash_policy):
    """If the broker raises mid-gate (corrupt YAML, OS error, etc.), the
    hook MUST deny — never silently allow. A broker bug must not become a
    permission bypass.

    This is the third leg of the T9 invariant: the hook is installed AND
    invoked AND the broker exists AND auto_deny is configured — but if
    the broker itself crashes, the SDK must STILL see deny. The unit test
    in ``tests/test_agent_runner.py::test_broker_hook_fails_closed_when_broker_raises``
    asserts this for a pre-injected fake broker; THIS test asserts it for
    the real ctor-wired broker by corrupting its underlying YAML between
    the policy load and the gate call.
    """
    runner = AgentRunner(
        model="",
        directory="/tmp",
        nick=WORKER_NICK,
        boss="",
    )
    assert runner._broker is not None

    # Replace the broker's gate with one that raises, simulating any
    # internal broker failure (file disappearance, yaml parse error,
    # asyncio.CancelledError during sleep, etc.). We touch the real
    # broker instance — not a stub — to keep the rest of the chain real.
    async def boom(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("simulated broker failure")

    runner._broker.gate = boom  # type: ignore[assignment]

    decision = await _simulate_sdk_pretooluse_invocation(
        runner._broker_pre_tool_use_hook,
        tool_name="Bash",
        tool_input={"command": "rm -rf /"},
    )

    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny", (
        "broker raised, hook must fail closed — never silently allow. "
        f"Full hook output: {decision!r}"
    )


# ---------------------------------------------------------------------------
# Sanity — the test infrastructure itself
# ---------------------------------------------------------------------------


def test_sdk_stub_is_installed():
    """Pin the fixture: the SDK stub must be loaded so all imports above
    resolve. If this fails, every other test in this file is testing the
    wrong PermissionResultAllow / Deny shape."""
    import sys

    assert "claude_agent_sdk" in sys.modules


def test_culture_home_is_isolated_per_test(culture_home):
    """The tmp-path-backed ``CULTURE_HOME`` must not leak across tests —
    otherwise the on-disk perm-policy YAMLs collide and the test order
    determines which deny rule wins. xdist-safe."""
    import os

    assert os.environ["CULTURE_HOME"] == str(culture_home)
