"""`run`: one way to run any agent, with one `start`, one `end` of the same fields and a recorded
run whether or not a unit holds it."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any

from unittest import mock

from coscc.agent import policy
from coscc.agent.policy import Row
from coscc.agent.sessions import Refused, Suspended
from coscc.runner import run as run_mod
from coscc.store.db import Data
from coscc.store.journal import Journal
from coscc.units import submit

# The fields every `end` has, whatever ran and however it ended.
SAME = {"agent", "status", "outcome", "session_id", "model", "denials", "denied", "background"}
SAME |= {"classified", "run", "events_lost"}


class Fake:
    """A stand-in `Sessions`: says `text`, hands `obj` to the channel, then ends with `terminal`;
    or raises `error` before anything."""

    def __init__(self, obj=None, terminal="success", error: BaseException | None = None):
        self.obj, self.terminal, self.error = obj, terminal, error
        self.calls: list[dict[str, Any]] = []

    async def stream(self, cwd, text, session_id=None, **kw):
        self.calls.append({"cwd": cwd, "text": text, "session_id": session_id, **kw})
        if self.error is not None:
            raise self.error
        yield ("session", "sess-1")
        yield ("chunk", "said")
        yield ("tool", "Read")
        server = (kw.get("mcp_servers") or {}).get(submit.SERVER)
        if server is not None and self.obj is not None:
            await COLLECTORS[-1].handle(self.obj)
        yield (
            "done",
            {
                "session_id": "sess-1",
                "cost": {"cost_usd": 0.25, "turns": 2, "input_tokens": 10},
                "terminal_reason": self.terminal,
                "models_used": ["m"],
            },
        )


COLLECTORS: list[submit.Collector] = []


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "work").mkdir()
        self.data_dir = root / "data"
        self.journal = Journal(root / "work", Data(self.data_dir))
        self.agent = run_mod.Agent("estimate", Row(max_turns=4, max_budget_usd=1.0), model="m")

    def go(self, sessions, channel=True, finish=None, **given):
        collector = submit.Collector("estimate") if channel else None
        if collector is not None:
            COLLECTORS.append(collector)
        items: list[tuple[str, Any]] = []

        async def drain():
            async for item in run_mod.run(
                given.pop("agent", self.agent),
                run_mod.Input("/w", "the prompt", "ws", channel=collector, **given),
                ctx=run_mod.Ctx(sessions, self.journal, self.data_dir),
                finish=finish,
            ):
                items.append(item)

        asyncio.run(drain())
        return items

    def rows(self, kind):
        return [r for r in self.journal.records("ws") if r["kind"] == kind]


class EachStatus(Base):
    def test_done_hands_back_the_object_with_one_start_and_one_end(self):
        items = self.go(Fake(obj={"units": []}))
        got = items[-1][1]
        self.assertEqual([k for k, _ in items], ["chunk", "tool", "done"])
        self.assertEqual((got.status, got.output, got.session), ("done", {"units": []}, "sess-1"))
        self.assertEqual((got.cost["cost_usd"], got.turns), (0.25, 2))
        [start], [end] = self.rows("start"), self.rows("end")
        self.assertEqual((start["unit"], start["stage"], start["run"]), ("", "estimate", got.run))
        self.assertLessEqual(SAME, set(end))
        self.assertEqual(
            (end["status"], end["outcome"], end["agent"]), ("done", "done", "estimate")
        )
        self.assertEqual((end["cost_usd"], end["run"]), (0.25, got.run))

    def test_a_ceiling_is_paused_budget(self):
        got = self.go(Fake(obj={"units": []}, terminal="error_max_budget_usd"))[-1][1]
        self.assertEqual((got.status, got.output), ("paused-budget", None))
        self.assertEqual(self.rows("end")[0]["status"], "paused-budget")

    def test_an_empty_channel_fails(self):
        got = self.go(Fake(obj=None))[-1][1]
        self.assertEqual(got.status, "failed")
        self.assertTrue(got.detail.startswith(run_mod.NO_SUBMISSION))

    def test_refused_and_failed_still_end(self):
        refused = self.go(Fake(error=Refused("not a workspace")))[-1][1]
        failed = self.go(Fake(error=RuntimeError("boom")))[-1][1]
        self.assertEqual((refused.status, failed.status), ("refused", "failed"))
        ends = self.rows("end")
        self.assertEqual([e["status"] for e in ends], ["refused", "failed"])
        self.assertTrue(all(SAME <= set(e) for e in ends))
        self.assertTrue(all(e["cost_unknown"] for e in ends))

    def test_a_cancel_ends_cancelled(self):
        with self.assertRaises(asyncio.CancelledError):
            self.go(Fake(error=asyncio.CancelledError()))
        [end] = self.rows("end")
        self.assertEqual((end["status"], end["outcome"]), ("cancelled", "cancelled"))

    def test_an_update_writes_no_end(self):
        with self.assertRaises(Suspended):
            self.go(Fake(error=Suspended("paused")))
        self.assertEqual((len(self.rows("start")), self.rows("end")), (1, []))

    def test_finish_decides_before_the_end(self):
        async def finish(got):
            got.status, got.detail = "failed", "nothing written"
            return {"written": 0}

        got = self.go(Fake(obj={"units": []}), finish=finish)[-1][1]
        self.assertEqual((got.status, got.output), ("failed", None))
        [end] = self.rows("end")
        self.assertEqual(
            (end["status"], end["detail"], end["written"]), ("failed", "nothing written", 0)
        )

    def test_a_finish_that_fails_or_is_cancelled_still_ends_the_run(self):
        async def fails(got):
            raise RuntimeError("gh is down")

        got = self.go(Fake(obj={"units": []}), finish=fails)[-1][1]
        self.assertEqual((got.status, got.output), ("failed", None))

        async def cancelled(got):
            raise asyncio.CancelledError

        with self.assertRaises(asyncio.CancelledError):
            self.go(Fake(obj={"units": []}), finish=cancelled)
        self.assertEqual([e["status"] for e in self.rows("end")], ["failed", "cancelled"])
        self.assertNotIn(got.run, run_mod.LIVE)


class TheRunIsRecorded(Base):
    def test_a_run_with_no_unit_has_its_events(self):
        got = self.go(Fake(obj={"units": []}))[-1][1]
        row = Data(self.data_dir).step_run(got.run)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual((row["unit"], row["stage"], row["workspace"]), ("", "estimate", "ws"))
        self.assertIsNotNone(row["ended_at"])
        self.assertNotIn(got.run, run_mod.LIVE)

    def test_a_session_other_than_chat_gets_a_handle_with_the_recorder(self):
        sessions = Fake(obj={"units": []})
        got = self.go(sessions)[-1][1]
        call = sessions.calls[0]
        self.assertEqual(call["step"].recorder.run, got.run)
        self.assertNotIn("recorder", call)

    def test_chat_keeps_its_client_and_its_output_is_its_reply(self):
        sessions = Fake()
        agent = run_mod.Agent("chat", Row(tools=("Read",)))
        got = self.go(sessions, channel=False, agent=agent, keep=True, session_id="s0")[-1][1]
        call = sessions.calls[0]
        self.assertEqual((got.status, got.output), ("done", "said"))
        self.assertEqual((call["session_id"], call["recorder"].run), ("s0", got.run))
        self.assertNotIn("step", call)
        self.assertEqual(self.rows("end")[0]["agent"], "chat")


class TheRunIsIssuedItsGrant(Base):
    """`issue`: the per-run grant, nothing beyond what the row holds and where the run stands."""

    IMPL = policy.row_for("impl")

    def test_a_grant_without_its_secrets_is_never_issued(self):
        with mock.patch.object(run_mod, "secrets_of", return_value=()):
            with self.assertRaises(ValueError):
                run_mod.issue(Row(), Fake(), cwd="/w")

    def test_a_row_without_a_write_tool_writes_nowhere(self):
        for key in ("plan", "spec", "intent", "review", "estimate"):
            with self.subTest(key=key):
                g = run_mod.issue(policy.row_for(key), Fake(), cwd="/w", unit_dir="/u")
                self.assertEqual((g.write, g.scratch, g.helpers), ((), None, ()))
                self.assertTrue(g.secrets)

    def test_a_writing_row_writes_its_cwd_and_its_unit_and_with_bash_its_scratch(self):
        g = run_mod.issue(self.IMPL, Fake(), cwd="/w", unit_dir="/u", scratch=("/r", "/d"))
        self.assertEqual((g.cwd, g.write, g.scratch), ("/w", ("/w", "/u"), ("/r", "/d")))
        self.assertGreater(g.ram_cap, 0)
        writes = Row(tools=("Read", "Write"))
        g = run_mod.issue(writes, Fake(), cwd="/w", unit_dir="/u", scratch=("/r", "/d"))
        self.assertEqual((g.write, g.scratch), (("/w", "/u"), None))

    def test_helpers_and_peers_come_only_with_agent_and_submit_with_an_output(self):
        g = run_mod.issue(self.IMPL, Fake(), cwd="/w")
        self.assertEqual(g.helpers, tuple(policy.SUBAGENTS))
        self.assertEqual(g.mcp, (policy.SUBMIT_TOOL, policy.PEERS_TOOL))
        g = run_mod.issue(policy.row_for("review"), Fake(), cwd="/w")
        self.assertEqual((g.helpers, g.mcp), ((), (policy.SUBMIT_TOOL,)))

    def test_a_lease_needs_a_branch_and_features_stay_out_of_the_cli_tools(self):
        g = run_mod.issue(self.IMPL, Fake(), cwd="/w", lease="abc")
        self.assertEqual((g.branch, g.lease), ("", ""))
        g = run_mod.issue(
            self.IMPL,
            Fake(),
            cwd="/w",
            branch="feat/x",
            features=("vault", "codegraph"),
            held=("vault",),
            mcp=("mcp__vault__vault_exec",),
            use=(("vault", "ws:db"),),
        )
        self.assertNotIn("vault", g.tools)
        self.assertIn("Bash", g.tools)
        self.assertEqual(g.held, ("vault",))
        self.assertEqual(
            policy.granted(g)[1:],
            ["push: feat/x", "helpers: scout, worker", "submit", "vault: ws:db"],
        )

    def test_the_start_and_the_first_event_say_what_was_granted(self):
        agent = run_mod.Agent("estimate", policy.row_for("estimate"), model="m")
        got = self.go(Fake(obj={"units": []}), agent=agent)[-1][1]
        [start] = self.rows("start")
        self.assertEqual(start["grants"]["mcp"], [policy.SUBMIT_TOOL])
        self.assertEqual(start["grants"]["granted"], ["submit"])
        self.assertEqual(start["grants"]["write"], [])
        self.assertNotIn("secrets", start["grants"])
        self.assertEqual(start["run"], got.run)


class Resuming(Base):
    ROW = {
        "session_id": "sess-0",
        "safe_uuid": "u-1",
        "message": "carry on",
        "api_calls": 1,
        "spent_usd": 0.4,
        "owner": {"start_at": "2026-10-06T00:00:00Z"},
    }

    def test_it_goes_on_under_what_is_left_with_no_second_start(self):
        sessions = Fake(obj={"units": []})
        got = self.go(sessions, resume=self.ROW)[-1][1]
        call = sessions.calls[0]
        self.assertEqual(
            (call["text"], call["session_id"], call["resume_at"]), ("carry on", "sess-0", "u-1")
        )
        self.assertEqual((call["max_turns"], call["max_budget_usd"]), (3, 0.6))
        self.assertEqual((self.rows("start"), got.status), ([], "done"))
        self.assertEqual(len(self.rows("end")), 1)

    def test_a_used_up_ceiling_opens_nothing(self):
        sessions = Fake(obj={"units": []})
        got = self.go(sessions, resume={**self.ROW, "spent_usd": 1.0})[-1][1]
        self.assertEqual((sessions.calls, got.status), ([], "paused-budget"))


if __name__ == "__main__":
    unittest.main()
