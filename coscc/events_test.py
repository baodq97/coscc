"""Tests for a step's recorder (`0073` step 3), with SDK messages built by hand.

The messages are the SDK's own classes, as `uv.lock` pins them, so a renamed field shows up
here as a kind that fell into `system` (`plan.md` Risk 7).
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from unittest import mock

from claude_agent_sdk import (
    AssistantMessage,
    RateLimitEvent,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from coscc import events
from coscc.data import Busy, Data
from coscc.journal import Journal


def assistant(mid, *blocks):
    return AssistantMessage(content=list(blocks), model="m", message_id=mid)


def result(turns=3, cost=0.25):
    return ResultMessage(
        subtype="success", duration_ms=1200, duration_api_ms=1000, is_error=False,
        num_turns=turns, session_id="s", total_cost_usd=cost,
        model_usage={"m": {"inputTokens": 10, "outputTokens": 20,
                           "cacheReadInputTokens": 30, "cacheCreationInputTokens": 40}},
    )


class Odd:
    """A message kind no SDK sends today."""

    def __init__(self):
        self.what = "odd"


def recorder(data=None):
    return events.Recorder("r1", data or mock.Mock(), "/w", "/w/ws", "0001_a", "impl")


class WhatIsRecorded(unittest.TestCase):
    def test_every_kind_of_message_becomes_its_event(self):
        rec = recorder()
        rec.message(assistant("m1", TextBlock("hello"), ThinkingBlock("hmm", "sig")))
        rec.message(assistant("m1", ToolUseBlock("t1", "Bash", {"command": "ls"})))
        rec.message(UserMessage(
            content=[ToolResultBlock("t1", "a.txt", False)],
            tool_use_result={"persistedOutputPath": "/p/out.txt", "persistedOutputSize": 90000},
        ))
        rec.message(assistant("m2", TextBlock("again")))
        rec.message(SystemMessage(subtype="init", data={"x": 1}))
        rec.message(RateLimitEvent(rate_limit_info=mock.Mock(), uuid="u", session_id="s"))
        rec.message(Odd())
        rec.message(result())
        kinds = [e["kind"] for e in rec.events]
        self.assertEqual(kinds, [
            "turn", "text", "thinking", "tool_use", "tool_result", "turn", "text",
            "system", "system", "system", "result",
        ])
        self.assertEqual([e["seq"] for e in rec.events], list(range(1, 12)))
        # Two messages with one id are one turn.
        self.assertEqual([e["n"] for e in rec.events if e["kind"] == "turn"], [1, 2])
        tool_result = rec.events[4]
        self.assertEqual(tool_result["persisted_path"], "/p/out.txt")
        self.assertEqual(tool_result["persisted_size"], 90000)
        self.assertEqual(rec.events[7]["class"], "SystemMessage")
        # The follow route's NDJSON envelope has a `type` of its own; an event must not.
        self.assertFalse(any("type" in e for e in rec.events))
        self.assertEqual(rec.events[9]["class"], "Odd")
        done = rec.events[-1]
        self.assertEqual((done["num_turns"], done["cost_usd"]), (3, 0.25))
        self.assertEqual(
            [done[k] for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")],
            [10, 20, 30, 40],
        )
        self.assertEqual(rec.lost, 0)

    def test_a_long_field_is_cut_and_says_so(self):
        rec = recorder()
        rec.message(assistant("m", TextBlock("a" * 70_000)))
        text = rec.events[-1]
        self.assertEqual(len(text["text"]), events.FIELD_MAX)
        self.assertTrue(text["truncated"])
        self.assertEqual(text["length"], 70_000)
        rec.denied("Bash", {"command": "b" * 70_000}, "no")
        cut = rec.events[-1]
        self.assertIsInstance(cut["input"], str)
        self.assertEqual(len(cut["input"]), events.FIELD_MAX)
        self.assertTrue(cut["truncated"])

    def test_every_refusal_is_an_event(self):
        rec = recorder()
        for _ in range(7):
            rec.denied("Bash", {"command": "rm -rf /"}, "not granted")
        self.assertEqual([e["kind"] for e in rec.events], ["denied"] * 7)
        self.assertEqual(rec.events[0]["reason"], "not granted")

    def test_a_message_that_breaks_the_recorder_is_counted_not_raised(self):
        rec = recorder()
        broken = assistant("m", TextBlock("x"))
        broken.content = 5  # not iterable
        rec.message(broken)
        self.assertEqual(rec.lost, 1)


class Followers(unittest.IsolatedAsyncioTestCase):
    async def test_subscribing_gets_what_was_there_then_what_comes(self):
        rec = recorder()
        for i in range(3):
            rec.message(assistant(f"m{i}", TextBlock(str(i))))
        q, backlog = rec.subscribe(after=2)
        self.assertEqual([e["seq"] for e in backlog], [3, 4, 5, 6])
        rec.message(assistant("m9", TextBlock("new")))
        kind, event = q.get_nowait()
        self.assertEqual((kind, event["seq"]), ("event", 7))

    async def test_a_slow_follower_is_cut_and_the_step_goes_on(self):
        rec = recorder()
        q, _ = rec.subscribe(0)
        for i in range(events.SUB_LIMIT + 10):
            rec.denied("Bash", {}, str(i))
        self.assertNotIn(q, rec.subscribers)
        items = [q.get_nowait() for _ in range(q.qsize())]
        self.assertEqual(items[-1], ("cut", events.SUB_LIMIT + 1))
        self.assertEqual(len(rec.events), events.SUB_LIMIT + 10)
        self.assertEqual(rec.lost, 0)


class TheDisk(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data = Data(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    async def test_everything_is_on_disk_when_close_returns(self):
        rec = recorder(self.data)
        rec.start()
        for i in range(5):
            rec.message(assistant(f"m{i}", TextBlock(str(i))))
        lost = await rec.close("done", "")
        self.assertEqual(lost, 0)
        row = self.data.step_run("r1")
        self.assertEqual(row["events"], 11)
        self.assertIsNotNone(row["ended_at"])
        stored, _ = self.data.step_events_page("r1", None, 100)
        self.assertEqual([e["seq"] for e in stored], list(range(1, 12)))
        self.assertEqual(stored[-1]["kind"], "end")

    async def test_a_busy_first_write_is_kept_for_the_next(self):
        rec = recorder(self.data)
        rec.message(assistant("m", TextBlock("x")))
        real = self.data.step_events_add
        calls = []

        def once_busy(*a, **k):
            calls.append(1)
            if len(calls) == 1:
                raise Busy("held")
            return real(*a, **k)

        with mock.patch.object(self.data, "step_events_add", once_busy):
            self.assertFalse(await rec._flush(1.0))
            self.assertEqual(len(rec.pending), 2)
            self.assertTrue(await rec._flush(1.0))
        self.assertEqual(rec.pending, [])
        self.assertEqual(self.data.step_run("r1")["events"], 2)

    async def test_busy_to_the_end_is_lost_and_close_still_returns(self):
        rec = recorder(self.data)
        rec.message(assistant("m", TextBlock("x")))
        with mock.patch.object(self.data, "step_events_add", side_effect=Busy("held")):
            lost = await rec.close("failed", "why")
        self.assertEqual(lost, 3)
        self.assertEqual(self.data.step_run("r1")["lost"], 3)

    async def test_abandon_writes_what_it_can_and_no_end(self):
        rec = recorder(self.data)
        rec.start()
        rec.message(assistant("m", TextBlock("x")))
        await rec.abandon()
        row = self.data.step_run("r1")
        self.assertIsNone(row["ended_at"])
        self.assertEqual(row["events"], 2)


class Collapsing(unittest.TestCase):
    def test_long_bodies_are_collapsed_and_short_ones_are_not(self):
        long = "\n".join(f"line {i}" for i in range(50))
        view = events.collapse({"seq": 1, "at": 0, "kind": "text", "text": long})
        self.assertTrue(view["collapsed"])
        self.assertEqual(view["body"].count("\n"), events.COLLAPSE_LINES - 1)
        wide = events.collapse({"seq": 1, "at": 0, "kind": "thinking", "thinking": "w" * 5000})
        self.assertEqual(len(wide["body"]), events.COLLAPSE_CHARS)
        short = events.collapse({"seq": 1, "at": 0, "kind": "text", "text": "hi"})
        self.assertFalse(short["collapsed"])

    def test_the_labels_carry_what_r10_names(self):
        self.assertEqual(events.collapse({"kind": "turn", "n": 4})["label"], "lượt 4")
        denied = events.collapse({"kind": "denied", "tool": "Bash", "reason": "not granted", "input": {}})
        self.assertIn("not granted", denied["label"])
        paid = events.collapse({"kind": "result", "num_turns": 3, "cost_usd": 0.5, "input_tokens": 7,
                                "terminal_reason": "completed"})
        self.assertIn("3 lượt", paid["label"])
        self.assertIn("$0.5000", paid["label"])
        self.assertIn("7 token", paid["label"])

    def test_a_persisted_output_is_named_and_not_read(self):
        view = events.collapse({"kind": "tool_result", "content": "preview", "persisted_path": "/p/x",
                                "persisted_size": 12})
        self.assertEqual(
            view["persisted"],
            "đầu ra đầy đủ (12 ký tự) nằm ở /p/x trên máy chạy app; board không đọc file này",
        )


class Purging(unittest.IsolatedAsyncioTestCase):
    async def test_one_purge_row_when_something_went_and_none_otherwise(self):
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            journal = Journal(d, data)
            day = 24 * 3600 * 1000
            data.step_run_open("old", "/w", "/w/ws", "0001_a", "impl", 1000)
            data.step_events_add("old", [{"run": "old", "seq": 1, "at": 1, "kind": "text", "text": "x"}])
            runs, freed = await events.purge(data, journal, now=40 * day)
            self.assertEqual(runs, 1)
            self.assertGreater(freed, 0)
            self.assertEqual(await events.purge(data, journal, now=40 * day), (0, 0))
            rows = journal.records(kind="events-purge")
            self.assertEqual(len(rows), 1)
            self.assertEqual((rows[0]["workspace"], rows[0]["runs"]), ("", 1))


if __name__ == "__main__":
    unittest.main()
