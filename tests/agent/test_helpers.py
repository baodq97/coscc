"""`coscc/agent/helpers.py`: the hooks that hold `Agent` and `SendMessage`, the run's ledger of
helpers, `peers` and the protocol block."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest

from claude_agent_sdk._internal.message_parser import parse_message
from claude_agent_sdk._internal.query import Query
from claude_agent_sdk.types import _hooks_to_internal_format

from coscc.agent import sessions
from coscc.agent.helpers import Helpers
from coscc.config import Config


class _Transport:
    """What `Query` writes back to the CLI, kept."""

    def __init__(self):
        self.written: list[dict] = []

    async def write(self, data: str) -> None:
        self.written.append(json.loads(data))


def _query(ledger: Helpers) -> tuple[Query, _Transport]:
    """A `Query` holding the hooks `_options` hands the SDK, each callback id named by its event."""
    options = sessions._options(
        Config(), "/p", None, data_dir=tempfile.gettempdir(), hooks=ledger.hooks()
    )
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


def _ask(ledger: Helpers, event: str, hook_input: dict) -> dict:
    """What the SDK sends the CLI for one hook call, as the CLI would send it."""
    query, transport = _query(ledger)
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


def _pre(ledger: Helpers, tool: str, tool_input: dict, agent_id: str | None = None) -> str:
    """The reason the hook denied the call, or "" when it said nothing."""
    said = _ask(
        ledger,
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


def _system(ledger: Helpers, raw: dict) -> None:
    ledger.system(parse_message({"type": "system", "uuid": "u", "session_id": "s", **raw}))


def _run(ledger: Helpers, agent_id: str, step: str, tokens: int) -> None:
    """One helper from start to stop, as the CLI and the stream tell it."""
    _ask(ledger, "SubagentStart", {"hook_event_name": "SubagentStart", "agent_id": agent_id, "agent_type": "worker"})  # fmt: skip
    _system(
        ledger,
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
        ledger,
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
        ledger,
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


class TheHookHoldsWhoMayBeStarted(unittest.TestCase):
    def test_a_named_helper_in_the_foreground_passes(self):
        for kind in ("scout", "worker"):
            with self.subTest(kind=kind):
                self.assertEqual(_pre(Helpers(), "Agent", {"subagent_type": kind}), "")

    def test_an_unnamed_helper_one_in_the_background_or_one_from_a_helper_is_denied(self):
        for tool_input, agent_id, said in (
            ({"subagent_type": "general-purpose"}, None, "only these helpers"),
            ({"subagent_type": "Explore"}, None, "only these helpers"),
            ({"subagent_type": "worker", "run_in_background": True}, None, "foreground"),
            ({"subagent_type": "scout"}, "a1", "may not start another helper"),
        ):
            with self.subTest(tool_input=tool_input, agent_id=agent_id):
                self.assertIn(said, _pre(Helpers(), "Agent", tool_input, agent_id))

    def test_list_agents_is_denied(self):
        self.assertIn("mcp__cos__peers", _pre(Helpers(), "ListAgents", {}))
        self.assertIn("mcp__cos__peers", _pre(Helpers(), "ListAgents", {}, "a1"))


class SendMessageStaysInTheStep(unittest.TestCase):
    def test_main_and_a_helper_of_this_run_pass_and_anyone_else_is_denied(self):
        ledger = Helpers()
        _run(ledger, "a1", "(a) parallel", 100)
        self.assertEqual(
            _pre(ledger, "SendMessage", {"to": "main", "message": "done: x"}, "a1"), ""
        )
        self.assertEqual(_pre(ledger, "SendMessage", {"to": "a1", "message": "need: y"}), "")
        for to in ("a2", "other-session", "", None):
            with self.subTest(to=to):
                self.assertIn(
                    "SendMessage goes only to",
                    _pre(ledger, "SendMessage", {"to": to, "message": "x"}, "a1"),
                )

    def test_a_task_that_is_no_helper_and_a_start_with_no_id_are_neither_peers_nor_addresses(self):
        told = Told()
        ledger = Helpers(told)
        _system(ledger, {"subtype": "task_started", "task_id": "b1", "tool_use_id": "toolu_b1", "description": "npm test", "task_type": "local_bash"})  # fmt: skip
        _ask(ledger, "SubagentStart", {"hook_event_name": "SubagentStart", "agent_type": "worker"})  # fmt: skip
        for to in ("b1", ""):
            with self.subTest(to=to):
                self.assertIn(
                    "SendMessage goes only to",
                    _pre(ledger, "SendMessage", {"to": to, "message": "x"}, "a1"),
                )
        self.assertEqual(ledger.listing(), "no helper has started in this step")
        ledger.close()
        self.assertEqual(told.events, [])


class TheLedgerFollowsEachHelper(unittest.TestCase):
    def test_a_start_and_an_end_with_tokens_for_each_worker_and_peers_counts_them(self):
        told = Told()
        ledger = Helpers(told)
        _run(ledger, "a1", "(a) parallel", 100)
        _run(ledger, "a2", "(b) write-plan", 200)
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
        _ask(ledger, "SubagentStart", {"hook_event_name": "SubagentStart", "agent_id": "a1", "agent_type": "worker"})  # fmt: skip
        self.assertEqual(told.of("worker_start"), [])
        ledger.close()
        self.assertEqual(len(told.of("worker_start")), 1)
        (end,) = told.of("worker_end")
        self.assertIsNone(end["total_tokens"])
        ledger.close()
        self.assertEqual(len(told.of("worker_end")), 1)


class EachWriteOfAWorkerIsTold(unittest.TestCase):
    def test_one_worker_write_per_write_or_edit_with_its_step_and_path(self):
        told = Told()
        ledger = Helpers(told)
        _run(ledger, "a1", "(a) parallel", 100)
        _pre(ledger, "Write", {"file_path": "/w/coscc/features/parallel.py", "content": "x"}, "a1")
        _pre(ledger, "Edit", {"file_path": "/w/tests/x.py", "old_string": "a", "new_string": "b"}, "a1")  # fmt: skip
        _pre(ledger, "Read", {"file_path": "/w/x.py"}, "a1")
        # The leading session's own write is not a worker's.
        _pre(ledger, "Write", {"file_path": "/w/y.py", "content": "x"})
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
