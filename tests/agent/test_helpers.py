"""`coscc/agent/helpers.py`: the gate every session's calls go through, the run's ledger of
helpers, `peers` and the protocol block."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from claude_agent_sdk._internal.message_parser import parse_message
from claude_agent_sdk._internal.query import Query
from claude_agent_sdk.types import _hooks_to_internal_format

from coscc.agent import sessions
from coscc.agent.helpers import KINDS, MESSAGE_LINES, PROTOCOL, Denials, Gate, Helpers, malformed
from coscc.agent.policy import BACKGROUND_REFUSAL, HOST, SUBAGENTS, Grant
from coscc.config import Config

# The grant an impl run holds: its worktree, helpers and `peers`, and the secrets.
IMPL = Grant(
    cwd="/w",
    write=("/w",),
    helpers=tuple(SUBAGENTS),
    mcp=("mcp__cos__submit", "mcp__cos__peers"),
    secrets=("/data/cos.db",),
    tools=("Read", "Write", "Bash", "Agent", "SendMessage"),
)


class _Transport:
    """What `Query` writes back to the CLI, kept."""

    def __init__(self):
        self.written: list[dict] = []

    async def write(self, data: str) -> None:
        self.written.append(json.loads(data))


def _gate(ledger: Helpers | None = None, denials: Denials | None = None) -> Gate:
    return Gate(IMPL, denials, ledger)


def _query(gate: Gate) -> tuple[Query, _Transport]:
    """A `Query` holding the hooks `_options` hands the SDK, each callback id named by its event."""
    options = sessions._options(Config(), "/p", None, data_dir=tempfile.gettempdir(), gate=gate)
    transport = _Transport()
    query = Query(
        transport=transport,  # ty: ignore[invalid-argument-type] - a stand-in that only records writes
        is_streaming_mode=True,
        hooks=_hooks_to_internal_format(options.hooks or {}),
    )
    query.hook_callbacks = {
        event: matchers[0]["hooks"][0] for event, matchers in query.hooks.items()
    }
    return query, transport


def _ask(gate: Gate, event: str, hook_input: dict) -> dict:
    """What the SDK sends the CLI for one hook call, as the CLI would send it."""
    query, transport = _query(gate)
    request = {
        "request_id": "r1",
        "request": {
            "subtype": "hook_callback",
            "callback_id": event,
            "input": {"session_id": "s", "transcript_path": "/t", "cwd": "/p", **hook_input},
            "tool_use_id": hook_input.get("tool_use_id"),
        },
    }
    asyncio.run(query._handle_control_request(request))  # ty: ignore[invalid-argument-type] - the CLI's shape
    return transport.written[-1]["response"]["response"]


def _pre(gate: Gate, tool: str, tool_input: dict, agent_id: str | None = None) -> str:
    """The reason the hook denied the call, or "" when it said nothing."""
    said = _ask(
        gate,
        "PreToolUse",
        {
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "tool_input": tool_input,
            "tool_use_id": "toolu_1",
            **({"agent_id": agent_id, "agent_type": "worker"} if agent_id else {}),
        },
    )
    out = said.get("hookSpecificOutput") or {}
    if out.get("permissionDecision") == "deny":
        return out["permissionDecisionReason"]
    return ""


def _system(gate: Gate, raw: dict) -> None:
    gate.system(parse_message({"type": "system", "uuid": "u", "session_id": "s", **raw}))


def _run(gate: Gate, agent_id: str, step: str, tokens: int) -> None:
    """One helper from start to stop, as the CLI and the stream tell it."""
    _ask(gate, "SubagentStart", {"hook_event_name": "SubagentStart", "agent_id": agent_id, "agent_type": "worker"})  # fmt: skip
    _system(
        gate,
        {
            "subtype": "task_started",
            "task_id": agent_id,
            "tool_use_id": f"toolu_{agent_id}",
            "description": step,
            "subagent_type": "worker",
            "task_type": "local_agent",
        },
    )
    _system(
        gate,
        {
            "subtype": "task_notification",
            "task_id": agent_id,
            "status": "completed",
            "output_file": "",
            "summary": "",
            "usage": {"total_tokens": tokens, "tool_uses": 3, "duration_ms": 1200},
        },
    )
    _ask(
        gate,
        "SubagentStop",
        {
            "hook_event_name": "SubagentStop",
            "agent_id": agent_id,
            "agent_type": "worker",
            "agent_transcript_path": "/t",
            "stop_hook_active": False,
        },
    )


class Told:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def __call__(self, kind: str, fields: dict) -> None:
        self.events.append((kind, dict(fields)))

    def of(self, kind: str) -> list[dict]:
        return [f for k, f in self.events if k == kind]


class DenialsCountTheBackgroundRuns(unittest.TestCase):
    def test_a_background_refusal_is_counted_apart_from_the_rest(self):
        denials = Denials()
        denials.record("Bash", f"run_in_background is refused: {BACKGROUND_REFUSAL}")
        denials.record(
            "Bash", f"`&` at character 13 runs a command in the background: {BACKGROUND_REFUSAL}"
        )
        denials.record("Bash", "auto mode did not approve")
        self.assertEqual((denials.count, denials.background), (3, 2))


class TheGateFailsClosed(unittest.TestCase):
    """A hook that raises lets the call run (the CLI reads it as no opinion), so the gate turns
    any error of its own into a refusal, and records it."""

    def test_an_error_in_the_check_is_a_refusal(self):
        denials = Denials()
        gate = _gate(denials=denials)
        with mock.patch("coscc.agent.helpers.critical", side_effect=RuntimeError("boom")):
            said = _pre(gate, "Bash", {"command": "ls"})
        self.assertIn("refused", said)
        self.assertIn("boom", said)
        self.assertEqual(denials.count, 1)

    def test_a_malformed_call_is_a_refusal(self):
        denials = Denials()
        said = asyncio.run(_gate(denials=denials).pre_tool_use(None, None, None))
        self.assertEqual(said["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(denials.count, 1)

    def test_the_fallback_always_refuses(self):
        denials = Denials()
        context = SimpleNamespace(agent_id=None, decision_reason="3 consecutive actions")
        result = asyncio.run(_gate(denials=denials).can_use_tool("Bash", {"command": "x"}, context))
        self.assertEqual(type(result).__name__, "PermissionResultDeny")
        self.assertEqual(result.message, "auto mode did not approve")
        self.assertEqual(denials.count, 1)


class TheGateRecordsEveryDenial(unittest.TestCase):
    """The hook's, the fallback's and the classifier's, in the run's `denials`."""

    def test_three_sources(self):
        denials = Denials()
        heard: list[tuple] = []
        denials.listener = lambda *a: heard.append(a)
        gate = _gate(denials=denials)
        self.assertIn(HOST, _pre(gate, "Bash", {"command": "git push origin main"}))
        asyncio.run(gate.can_use_tool("Bash", {"command": "curl x"}, SimpleNamespace()))
        _system(
            gate,
            {
                "subtype": "permission_denied",
                "tool_name": "Bash",
                "tool_use_id": "toolu_2",
                "decision_reason_type": "classifier",
                "decision_reason": "[Data Exfiltration]",
                "message": "Permission for this action was denied",
            },
        )
        self.assertEqual(denials.count, 3)
        self.assertIn("[Data Exfiltration]", denials.reasons[2])
        self.assertEqual([h[0] for h in heard], ["Bash", "Bash", "Bash"])

    def test_a_background_run_is_counted_as_one(self):
        denials = Denials()
        _pre(_gate(denials=denials), "Bash", {"command": "npm run dev &"})
        self.assertEqual((denials.count, denials.background), (1, 1))

    def test_a_helper_hands_back_its_result(self):
        self.assertEqual(_pre(_gate(Helpers()), "SubagentHandback", {"message": "x"}, "a1"), "")


class TheGateCountsWhatGoesToTheClassifier(unittest.TestCase):
    def test_only_calls_let_through_and_sent_on_are_counted(self):
        denials = Denials()
        gate = _gate(denials=denials)
        _pre(gate, "Bash", {"command": "npm test"})
        _pre(gate, "Read", {"file_path": "/etc/hosts"})
        _pre(gate, "Bash", {"command": "ls"})
        _pre(gate, "Bash", {"command": "git push origin main"})
        self.assertEqual((denials.classified, denials.count), (1, 1))


class TheHookHoldsWhoMayBeStarted(unittest.TestCase):
    def test_a_named_helper_in_the_foreground_passes(self):
        for kind in ("scout", "worker"):
            with self.subTest(kind=kind):
                self.assertEqual(_pre(_gate(Helpers()), "Agent", {"subagent_type": kind}), "")

    def test_an_unnamed_helper_one_in_the_background_or_one_from_a_helper_is_denied(self):
        for tool_input, agent_id, said in (
            ({"subagent_type": "general-purpose"}, None, "only these helpers"),
            ({"subagent_type": "Explore"}, None, "only these helpers"),
            ({"subagent_type": "worker", "run_in_background": True}, None, "foreground"),
            ({"subagent_type": "scout"}, "a1", "may not start another helper"),
        ):
            with self.subTest(tool_input=tool_input, agent_id=agent_id):
                self.assertIn(said, _pre(_gate(Helpers()), "Agent", tool_input, agent_id))

    def test_list_agents_is_denied(self):
        self.assertIn("mcp__cos__peers", _pre(_gate(Helpers()), "ListAgents", {}))
        self.assertIn("mcp__cos__peers", _pre(_gate(Helpers()), "ListAgents", {}, "a1"))

    def test_a_session_with_no_helpers_holds_the_same_rules(self):
        self.assertIn("only these helpers", _pre(_gate(), "Agent", {"subagent_type": "x"}))
        self.assertIn("SendMessage goes only to", _pre(_gate(), "SendMessage", {"to": "a1"}))
        self.assertEqual(_pre(_gate(), "SendMessage", {"to": "main", "message": "need: x"}), "")


class SendMessageStaysInTheStep(unittest.TestCase):
    def test_main_and_a_helper_of_this_run_pass_and_anyone_else_is_denied(self):
        gate = _gate(Helpers())
        _run(gate, "a1", "(a) parallel", 100)
        done = {"to": "main", "message": "done: coscc/bus.py:12"}
        self.assertEqual(_pre(gate, "SendMessage", done, "a1"), "")
        self.assertEqual(_pre(gate, "SendMessage", {"to": "a1", "message": "need: y"}), "")
        for to in ("a2", "other-session", "", None):
            with self.subTest(to=to):
                self.assertIn(
                    "SendMessage goes only to",
                    _pre(gate, "SendMessage", {"to": to, "message": "x"}, "a1"),
                )

    def test_a_task_that_is_no_helper_and_a_start_with_no_id_are_neither_peers_nor_addresses(self):
        told = Told()
        ledger = Helpers(told)
        gate = _gate(ledger)
        _system(gate, {"subtype": "task_started", "task_id": "b1", "tool_use_id": "toolu_b1", "description": "npm test", "task_type": "local_bash"})  # fmt: skip
        _ask(gate, "SubagentStart", {"hook_event_name": "SubagentStart", "agent_type": "worker"})  # fmt: skip
        for to in ("b1", ""):
            with self.subTest(to=to):
                self.assertIn(
                    "SendMessage goes only to",
                    _pre(gate, "SendMessage", {"to": to, "message": "x"}, "a1"),
                )
        self.assertEqual(ledger.listing(), "no helper has started in this step")
        ledger.close()
        self.assertEqual(told.events, [])


class AMessageKeepsTheProtocolsShape(unittest.TestCase):
    """`PROTOCOL`'s shape: a kind first, at most `MESSAGE_LINES` lines, and a `changed:` or
    `done:` naming the files it means. A message out of it is denied with what to fix, in the run's
    denials."""

    def test_each_good_shape_passes(self):
        for message in (
            "need: the `Bus.publish` signature",
            "blocked: tests/test_bus.py fails to import",
            "changed: `publish` takes a payload, coscc/bus.py:120",
            "done: step (a)\n- `/w/t/coscc/bus.py:153-160` checks the payload",
            "done: x\n" + "- coscc/a.py:1\n" * (MESSAGE_LINES - 1),
            "done: src/screens/coverage/index.tsx rebuilt",
        ):
            with self.subTest(message=message):
                self.assertEqual(malformed(message), "")

    def test_each_malformed_shape_is_denied_with_its_reason(self):
        for message, said in (
            ("thanks, got it", "opens with one of `need:`"),
            ("Done: coscc/bus.py:1", "opens with one of"),
            ("", "as text"),
            ({"type": "shutdown_request"}, "as text"),
            ("done: x\n" + "- coscc/a.py:1\n" * MESSAGE_LINES, f"at most {MESSAGE_LINES} lines"),
            ("changed: the API at 10:30", "names each file"),
            ("done: all of it, finished.", "names each file"),
        ):
            with self.subTest(message=message):
                denials = Denials()
                gate = _gate(Helpers(), denials=denials)
                got = _pre(gate, "SendMessage", {"to": "main", "message": message}, "a1")
                self.assertIn(said, got)
                self.assertEqual((denials.count, denials.reasons), (1, [f"SendMessage: {got}"]))

    def test_the_prompt_says_what_the_gate_holds(self):
        self.assertIn(f"1 to {MESSAGE_LINES} lines", PROTOCOL)
        for kind in KINDS:
            self.assertIn(f"`{kind}:`", PROTOCOL)


class TheLedgerFollowsEachHelper(unittest.TestCase):
    def test_a_start_and_an_end_with_tokens_for_each_worker_and_peers_counts_them(self):
        told = Told()
        ledger = Helpers(told)
        gate = _gate(ledger)
        _run(gate, "a1", "(a) parallel", 100)
        _run(gate, "a2", "(b) write-plan", 200)
        starts, ends = told.of("worker_start"), told.of("worker_end")
        self.assertEqual([s["step"] for s in starts], ["(a) parallel", "(b) write-plan"])
        self.assertEqual([e["agent_id"] for e in ends], ["a1", "a2"])
        self.assertEqual([e["total_tokens"] for e in ends], [100, 200])
        for event in starts + ends:
            self.assertIsInstance(event["at_ms"], int)
        self.assertEqual([e["duration_ms"] for e in ends], [1200, 1200])
        self.assertEqual({e["status"] for e in ends}, {"completed"})
        listing = ledger.listing().splitlines()
        self.assertEqual(len(listing), 2)
        self.assertTrue(all(line.endswith("done") for line in listing))

    def test_an_end_that_never_came_is_told_when_the_run_closes(self):
        told = Told()
        ledger = Helpers(told)
        _ask(_gate(ledger), "SubagentStart", {"hook_event_name": "SubagentStart", "agent_id": "a1", "agent_type": "worker"})  # fmt: skip
        self.assertEqual(told.of("worker_start"), [])
        ledger.close()
        self.assertEqual(len(told.of("worker_start")), 1)
        (end,) = told.of("worker_end")
        self.assertIsNone(end["total_tokens"])
        ledger.close()
        self.assertEqual(len(told.of("worker_end")), 1)

    def test_a_gate_with_no_ledger_hooks_no_helper(self):
        self.assertEqual(set(_gate().hooks()), {"PreToolUse"})
        self.assertEqual(
            set(_gate(Helpers()).hooks()), {"PreToolUse", "SubagentStart", "SubagentStop"}
        )


class EachWriteOfAWorkerIsTold(unittest.TestCase):
    def test_one_worker_write_per_write_or_edit_with_its_step_and_path(self):
        told = Told()
        gate = _gate(Helpers(told))
        _run(gate, "a1", "(a) parallel", 100)
        _pre(gate, "Write", {"file_path": "/w/coscc/features/parallel.py", "content": "x"}, "a1")
        _pre(gate, "Edit", {"file_path": "/w/tests/x.py", "old_string": "a", "new_string": "b"}, "a1")  # fmt: skip
        _pre(gate, "Read", {"file_path": "/w/x.py"}, "a1")
        # The leading session's own write is not a worker's, nor is a refused one.
        _pre(gate, "Write", {"file_path": "/w/y.py", "content": "x"})
        _pre(gate, "Write", {"file_path": "/elsewhere/y.py", "content": "x"}, "a1")
        writes = told.of("worker_write")
        self.assertEqual(
            [(w["step"], w["tool"], w["path"]) for w in writes],
            [
                ("(a) parallel", "Write", "/w/coscc/features/parallel.py"),
                ("(a) parallel", "Edit", "/w/tests/x.py"),
            ],
        )


if __name__ == "__main__":
    unittest.main()


class AGateNeedsItsGrantsSecretsAndNamesWhatADenialLacked(unittest.TestCase):
    def test_a_grant_without_secrets_opens_no_gate(self):
        with self.assertRaises(ValueError):
            Gate(Grant(cwd="/w", write=("/w",)))

    def test_the_listener_hears_the_grant_a_refusal_lacked(self):
        denials = Denials()
        heard: list[tuple] = []
        denials.listener = lambda *a: heard.append(a)
        gate = _gate(denials=denials)
        _pre(gate, "Bash", {"command": "git push origin main"})
        _pre(gate, "Write", {"file_path": "/etc/x"})
        _pre(gate, "Read", {"file_path": "/data/cos.db"})
        self.assertEqual([a[3] for a in heard], ["push", "write", "never granted"])
