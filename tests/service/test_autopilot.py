"""The autopilot inside `Service`, with stand-in sessions: no quota is spent.

`OnTheRealLoop` runs the real `cos.mjs` gate and `next` over a workspace with no git, and only the
session is a stand-in."""

from __future__ import annotations

import asyncio
import dataclasses
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.units import autopilot
from coscc.config import Config
from coscc.github import prmachine
from tests.github import test_prmachine
from coscc.runlog.journal import Journal
from coscc.data import Busy
from coscc.service import Service
from coscc.service.common import Invalid
from coscc.service.common import Refused
from coscc.agent.sessions import Sessions
from tests.units.test_submit import submits as _submits
from tests.service.test_service import use_sessions, use_config


class _Replies:
    """A session that writes an artifact: `accepted` for the first `accepted` steps, then
    `draft`, so a chain stops where the test wants it to."""

    def __init__(self, accepted: int = 1) -> None:
        self.accepted = accepted
        self.calls = 0
        self.release = asyncio.Event()
        self.release.set()

    async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
        self.calls += 1
        status = "accepted" if self.calls <= self.accepted else "draft"
        # The title of the stage that asked. The first step here is always `spec`.
        yield ("chunk", "# Spec: x\n" if self.calls == 1 else "# Plan: x\n")
        await asyncio.wait_for(self.release.wait(), 20)
        yield ("chunk", f"Author: proof. Status: {status}.\n")
        # The object is what the app reads; the line above is for a reader.
        await _submits(kw, judgement="ready" if status == "accepted" else "not-ready")
        yield (
            "done",
            {
                "session_id": f"s{self.calls}",
                "terminal_reason": "success",
                "cost": {"output_tokens": 3, "turns": 1, "cost_usd": 0.01},
            },
        )


class _Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        workspace = root / "work" / "proj"
        workspace.mkdir(parents=True)
        self.config = Config(
            workspaces=(str(workspace),),
            working_dir=str(root / "work"),
            data_dir=str(root / "data"),
            host="127.0.0.1",
        )
        self.service = Service(self.config, Sessions(self.config))
        self.ws = str(workspace)
        self.key = self.service.ws.key(self.ws)
        # A pass every 5 minutes would never come in a test; the loop's first pass does.
        self.addAsyncCleanup(self.service.shutdown)

    async def unit(self, slug: str, intent: str = "Status: accepted.") -> str:
        made = await self.service.answers.create_unit(self.ws, slug, "words for the proof")
        (Path(made["path"]) / "intent.md").write_text(
            f"# Intent: x\nAuthor: proof. Type: fix. {intent}\n", encoding="utf-8"
        )
        await self.ingest(made["unit"])
        return made["unit"]

    async def ingest(self, unit: str) -> None:
        """Files written here by hand reach `cos.db` as a step's would."""
        self.assertEqual(
            await self.service.answers.ingest(self.ws, unit, {"outcome": "done", "stage": "test"}),
            {},
        )

    def rows(self, unit: str) -> list[tuple]:
        """The answers `cos.db` holds for `unit`, `(artifact, ref, answered_by, via, text)`."""
        with self.service.ws.unit_meta().data.connect() as conn:
            return [
                tuple(r)
                for r in conn.execute(
                    "SELECT artifact, ref, answered_by, via, text FROM unit_answers WHERE unit = ? ORDER BY id",
                    (unit,),
                )
            ]

    def starts(self) -> list[dict]:
        """The steps' `start`s."""
        return [
            r
            for r in Journal(self.config.working_dir, self.config.data_dir).records(kind="start")
            if autopilot.is_step(r)
        ]

    def listed(self, *names: str) -> None:
        """A `shortlist` record, written straight to the run log. The autopilot follows nothing
        else, so a test that wants a start writes one."""
        Journal(self.config.working_dir, self.config.data_dir).append(
            {
                "kind": "shortlist",
                "workspace": self.key,
                "unit": "",
                "units": list(names),
                "reason": "for the proof",
                "by": "proof",
            }
        )

    async def until(self, predicate, what: str) -> None:
        for _ in range(2000):
            if predicate():
                return
            await asyncio.sleep(0.01)
        self.fail(f"timed out waiting for {what}")

    async def stopped(self) -> None:
        """The loop's first pass has found a stop, and everything it started has ended."""
        await self.until(lambda: self.service.autopilot.stops.get(self.key), "a stop")
        await self.settled()

    async def settled(self) -> None:
        """Every pass, launch and step the autopilot started has ended."""

        async def busy():
            return (
                self.service.attempts.unfinished()
                or self.service.steps.tasks
                or self.service.holds.finishing
                or any(
                    not t.done()
                    for _, t in (self.service.autopilot.runs.get(self.key) or {}).values()
                )
                or self.service.autopilot.pending
                or self.service.autopilot.locks.get(self.key, asyncio.Lock()).locked()
            )

        for _ in range(3000):
            if not await busy():
                return
            await asyncio.sleep(0.01)
        self.fail("the autopilot did not settle")


class OnTheRealLoop(_Base):
    async def test_off_starts_nothing(self):
        use_sessions(self.service, _Replies(accepted=5))
        await self.unit("off")
        unit = (await self.service.board(self.ws))["units"][0]["name"]
        [_ async for _ in self.service.steps.run_step(self.ws, unit, "spec")]
        await self.settled()
        self.assertEqual([s["started_by"] for s in self.starts()], ["person"])
        self.assertEqual(self.service.autopilot.tasks, {})
        self.assertFalse((await self.service.board(self.ws))["autopilot"]["on"])

    async def test_a_done_step_starts_the_next_stage_and_a_draft_stops_it(self):
        use_sessions(self.service, _Replies(accepted=1))
        unit = await self.unit("chain")
        self.listed(unit)
        self.service.autopilot.set_setting(self.ws, "autopilot", True)
        await self.until(lambda: len(self.starts()) >= 2, "two steps")
        await self.settled()
        self.assertEqual(
            [(s["stage"], s["started_by"]) for s in self.starts()],
            [("spec", "autopilot"), ("plan", "autopilot")],
        )
        block = (await self.service.board(self.ws))["autopilot"]
        self.assertTrue(block["on"])
        [stop] = block["stops"]
        self.assertEqual((stop["unit"], stop["kind"]), (unit, "f"))
        self.assertIn("plan.md", stop["reason"])
        self.assertEqual(block["cap"]["spent"], 0.02)
        logged = Journal(self.config.working_dir, self.config.data_dir).records(
            kind="autopilot-stop"
        )
        self.assertEqual([(r["unit"], r["stop"]) for r in logged], [(unit, "f")])

    async def test_a_open_question_stops_it(self):
        use_sessions(self.service, _Replies(accepted=5))
        unit = await self.unit("asks", "Status: accepted.\n\n## Open questions\n\n1. Which one?")
        self.listed(unit)
        self.service.autopilot.set_setting(self.ws, "autopilot", True)
        await self.stopped()
        self.assertEqual(self.starts(), [])
        [stop] = (await self.service.board(self.ws))["autopilot"]["stops"]
        self.assertEqual((stop["unit"], stop["kind"]), (unit, "a"))
        self.assertIn("intent.md question 1", stop["reason"])

    async def test_a_note_under_open_questions_does_not_stop_it(self):
        """A bullet and a numbered line with no `?` are notes, so (a) never fires."""
        use_sessions(self.service, _Replies(accepted=1))
        unit = await self.unit(
            "notes",
            "Status: accepted.\n\n## Open questions\n\n"
            "Không còn câu hỏi mở.\n\n- Câu 1 là hạn.\n"
            "7. Người khởi xướng vẫn nên đọc lại file này.",
        )
        self.listed(unit)
        self.service.autopilot.set_setting(self.ws, "autopilot", True)
        await self.until(lambda: len(self.starts()) >= 2, "two steps")
        await self.settled()
        self.assertEqual(self.starts()[0]["stage"], "spec")
        self.assertEqual(self.starts()[0]["started_by"], "autopilot")
        [stop] = (await self.service.board(self.ws))["autopilot"]["stops"]
        self.assertEqual((stop["unit"], stop["kind"]), (unit, "f"))

    async def test_e_a_failed_step_is_not_run_again(self):
        use_sessions(self.service, _Replies(accepted=5))
        unit = await self.unit("failed")
        Journal(self.config.working_dir, self.config.data_dir).finished(
            self.key, unit, "spec", "failed"
        )
        self.listed(unit)
        self.service.autopilot.set_setting(self.ws, "autopilot", True)
        await self.stopped()
        self.assertEqual(self.starts(), [])
        [stop] = (await self.service.board(self.ws))["autopilot"]["stops"]
        self.assertEqual(stop["kind"], "e")

    async def test_the_cap_holds_the_autopilot_and_not_a_person(self):
        use_sessions(self.service, _Replies(accepted=5))
        unit = await self.unit("capped")
        self.listed(unit)
        self.service.autopilot.set_setting(self.ws, "daily_cap_usd", 1.0)
        self.service.autopilot.set_setting(self.ws, "autopilot", True)
        await self.stopped()
        self.assertEqual(self.starts(), [])
        [stop] = (await self.service.board(self.ws))["autopilot"]["stops"]
        self.assertEqual((stop["unit"], stop["kind"]), (unit, "cap"))
        self.service.autopilot.set_setting(self.ws, "autopilot", False)
        [_ async for _ in self.service.steps.run_step(self.ws, unit, "spec")]
        self.assertEqual([s["started_by"] for s in self.starts()], ["person"])

    async def test_turning_it_off_cancels_the_loop(self):
        use_sessions(self.service, _Replies(accepted=0))
        await self.unit("off-again")
        self.service.autopilot.set_setting(self.ws, "autopilot", True)
        task = self.service.autopilot.tasks[self.key]
        reader = self.service.autopilot.pr_readers[self.key]
        self.service.autopilot.set_setting(self.ws, "autopilot", False)
        await asyncio.sleep(0)
        self.assertTrue(task.cancelled() or task.done())
        self.assertNotIn(self.key, self.service.autopilot.tasks)
        # And its pull request reader, so no `gh` call is made for it.
        self.assertTrue(reader.cancelled() or reader.done())
        self.assertNotIn(self.key, self.service.autopilot.pr_readers)

    async def test_start_up_resumes_a_workspace_left_on(self):
        self.service.autopilot.set_setting(self.ws, "autopilot", True)
        self.service.autopilot.stop(self.key)
        self.assertEqual(self.service.autopilot.resume(), [self.ws])
        self.assertIn(self.key, self.service.autopilot.tasks)

    # --- , an answered draft runs again ------------------------------------

    def answers(self) -> list[dict]:
        return Journal(self.config.working_dir, self.config.data_dir).records(kind="answer")

    async def test_each_block_writes_an_answer_record_and_only_the_last_completes(self):
        unit = await self.unit("asked", "Status: draft.\n\n## Open questions\n\n1. Một?\n2. Hai?")
        await self.service.answers.answer(self.ws, unit, "intent.md", 1, "một", "")
        self.assertEqual([r["completes"] for r in self.answers()], [False])
        await self.service.answers.answer(self.ws, unit, "intent.md", 2, "hai", "")
        first, last = self.answers()
        self.assertEqual(
            {
                k: last[k]
                for k in (
                    "unit",
                    "stage",
                    "artifact",
                    "question",
                    "via",
                    "status",
                    "completes",
                    "autopilot",
                    "shortlisted",
                    "held",
                )
            },
            {
                "unit": unit,
                "stage": "intent",
                "artifact": "intent.md",
                "question": 2,
                "via": "product",
                "status": "draft",
                "completes": True,
                "autopilot": False,
                "shortlisted": False,
                "held": False,
            },
        )
        self.assertEqual(last["workspace"], self.key)
        self.assertEqual(self.starts(), [])

    async def test_the_last_answer_runs_the_draft_again_in_the_pass_it_wakes(self):
        use_sessions(self.service, _Intents())
        unit = await self.unit("rerun", "Status: draft.\n\n## Open questions\n\n1. Một?")
        self.listed(unit)
        self.service.autopilot.set_setting(self.ws, "autopilot", True)
        await self.stopped()
        self.assertEqual(self.starts(), [])
        [stop] = (await self.service.board(self.ws))["autopilot"]["stops"]
        self.assertEqual(stop["kind"], "a")
        # The loop's next poll is 300 s away: only the pass the answer wakes can start it.
        await self.service.answers.answer(self.ws, unit, "intent.md", 1, "một", "")
        await self.until(lambda: self.starts(), "the rerun's start")
        await self.settled()
        self.assertEqual(
            [(s["stage"], s["started_by"]) for s in self.starts()], [("intent", "autopilot")]
        )
        picks = Journal(self.config.working_dir, self.config.data_dir).records(
            kind="autopilot-pick"
        )
        self.assertEqual([(p["unit"], p["stage"]) for p in picks], [(unit, "intent")])
        [answer] = self.answers()
        self.assertEqual(
            (answer["completes"], answer["autopilot"], answer["shortlisted"]), (True, True, True)
        )
        self.assertEqual(self.rows(unit), [("intent.md", "1", "owner", "product", "một")])
        self.assertNotIn(
            "### Câu 1",
            (self.service.ws.unit_dir(self.ws, unit) / "intent.md").read_text(encoding="utf-8"),
        )

    async def test_a_rerun_that_keeps_its_answered_question_is_not_run_again(self):
        # The draft the rerun writes still asks question 1, which the kept block answers, so `next`
        # says `rerun` again with no new answer behind it.
        use_sessions(
            self.service, _Intents("\n## Open questions\n\n1. Một?\n", questions=((1, "Một?"),))
        )
        unit = await self.unit("kept", "Status: draft.\n\n## Open questions\n\n1. Một?")
        self.listed(unit)
        self.service.autopilot.set_setting(self.ws, "autopilot", True)
        await self.settled()
        await self.service.answers.answer(self.ws, unit, "intent.md", 1, "một", "")
        await self.until(lambda: self.starts(), "the rerun's start")
        await self.settled()
        self.assertEqual((await self.service.steps.next_step(self.ws, unit))["rerun"], "intent")
        await self.service.autopilot.run_pass(self.key)
        await self.settled()
        self.assertEqual(
            [(s["stage"], s["started_by"]) for s in self.starts()], [("intent", "autopilot")]
        )
        [stop] = (await self.service.board(self.ws))["autopilot"]["stops"]
        self.assertEqual(
            (stop["unit"], stop["kind"], stop["reason"]), (unit, "f", "finish and accept intent.md")
        )

    async def test_an_open_question_stops_the_unit_and_opens_no_session(self):
        """The stop `a` is what a person sees; nothing answers for them."""
        use_sessions(self.service, _Intents())
        unit = await self.unit("asks", "Status: accepted.\n\n## Open questions\n\n1. Which one?")
        self.listed(unit)
        self.service.autopilot.set_setting(self.ws, "autopilot", True)
        await self.stopped()
        self.assertEqual(self.starts(), [])
        self.assertEqual(
            Journal(self.config.working_dir, self.config.data_dir).records(kind="autopilot-pick"),
            [],
        )
        [stop] = (await self.service.board(self.ws))["autopilot"]["stops"]
        self.assertEqual((stop["unit"], stop["kind"]), (unit, "a"))

    # --- , a draft impl asks a person ---------------------------------------

    async def test_a_draft_impl_is_answered_and_journalled_as_impl(self):
        unit = await self.unit("impl-asks")
        d = self.service.ws.unit_dir(self.ws, unit)
        (d / "spec.md").write_text(
            "# Spec: x\nAuthor: proof. Status: accepted.\n", encoding="utf-8"
        )
        (d / "plan.md").write_text(
            "# Plan: x\nAuthor: proof. Status: accepted.\n", encoding="utf-8"
        )
        before = "# Impl: x\nAuthor: proof. Status: draft.\n\n## Open questions\n\n1. Chạy lệnh X rồi đưa kết quả?\n"
        (d / "impl.md").write_text(before, encoding="utf-8")
        await self.ingest(unit)
        await self.service.answers.answer(self.ws, unit, "impl.md", 1, "Đã chạy, ra 0.", "Leif")
        # A row, and not one byte of `impl.md`.
        self.assertEqual((d / "impl.md").read_text(encoding="utf-8"), before)
        self.assertEqual(self.rows(unit), [("impl.md", "1", "Leif", "product", "Đã chạy, ra 0.")])
        [u] = (await self.service.board(self.ws))["units"]
        self.assertEqual(
            [(q["artifact"], q["n"], q["answered"]) for q in u["questions"]], [("impl.md", 1, True)]
        )
        [record] = self.answers()
        self.assertEqual(
            (record["stage"], record["artifact"], record["completes"]), ("impl", "impl.md", True)
        )

    async def impl_asks(self, slug: str) -> tuple[str, Path, str]:
        unit = await self.unit(slug, "Status: accepted.\n\n## Open questions\n\n1. Một?")
        d = self.service.ws.unit_dir(self.ws, unit)
        (d / "spec.md").write_text(
            "# Spec: x\nAuthor: proof. Status: accepted.\n", encoding="utf-8"
        )
        (d / "plan.md").write_text(
            "# Plan: x\nAuthor: proof. Status: accepted.\n", encoding="utf-8"
        )
        before = "# Impl: x\nAuthor: proof. Status: draft.\n\n## Open questions\n\n1. Chạy lệnh X rồi đưa kết quả?\n"
        (d / "impl.md").write_text(before, encoding="utf-8")
        await self.ingest(unit)
        return unit, d, before

    async def test_an_answer_to_impl_md_is_refused_while_an_impl_step_runs(self):
        # The step writes `impl.md` with its own tools, so a block appended now could be written
        # over and nothing would say so.
        unit, d, before = await self.impl_asks("impl-busy")
        row = self.service.attempts.open("step", self.key, unit, "impl", state="running")
        with self.assertRaisesRegex(
            Invalid, r"^impl\.md cannot be answered while the impl step that writes it is running"
        ):
            await self.service.answers.answer(self.ws, unit, "impl.md", 1, "Đã chạy, ra 0.", "")
        self.assertEqual((d / "impl.md").read_text(encoding="utf-8"), before)
        self.assertEqual(self.answers(), [])
        # Another artifact of the same unit is not the step's to write.
        await self.service.answers.answer(self.ws, unit, "intent.md", 1, "một", "")
        self.service.attempts.move(row["id"], "ended", "done")
        await self.service.answers.answer(self.ws, unit, "impl.md", 1, "Đã chạy, ra 0.", "")
        self.assertEqual([r["artifact"] for r in self.answers()], ["intent.md", "impl.md"])

    async def test_a_prose_step_does_not_refuse_an_answer_to_its_artifact(self):
        # A prose stage's artifact is written by the app, which reads `## Answers` on disk as it
        # writes, so an answer given meanwhile is kept.
        unit, d, before = await self.impl_asks("intent-busy")
        row = self.service.attempts.open("step", self.key, unit, "intent", state="running")
        try:
            await self.service.answers.answer(self.ws, unit, "intent.md", 1, "một", "")
        finally:
            self.service.attempts.move(row["id"], "ended", "done")
        self.assertEqual([r["artifact"] for r in self.answers()], ["intent.md"])


class _Intents:
    """A session that rewrites `intent.md` as a draft, with no open questions unless `asks`."""

    def __init__(self, asks: str = "", questions: tuple = ()):
        self.asks = asks
        # The questions the object hands back, which the app reads; `asks` is prose.
        self.questions = [{"n": n, "text": t} for n, t in questions]

    async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
        yield (
            "chunk",
            "# Intent: x\nAuthor: proof. Type: fix. Status: draft.\n\n## Problem\n\nx\n"
            + self.asks,
        )
        await _submits(kw, judgement="not-ready", questions=self.questions)
        yield (
            "done",
            {
                "session_id": "s1",
                "terminal_reason": "success",
                "cost": {"output_tokens": 3, "turns": 1, "cost_usd": 0.01},
            },
        )


class Scripted(_Base):
    """The board, `next` and the step itself replaced, so each rule is set up on its own."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.units: dict[str, dict] = {}
        self.nexts: dict[str, dict] = {}
        self.launched: list[tuple[str, str, str]] = []
        # The launch tasks that reached the step's stand-in.
        self.began: set[asyncio.Task] = set()
        self.asked: list[str] = []
        self.shortlisted = False
        self.release = asyncio.Event()

        async def board(cwd):
            return {"units": list(self.units.values())}

        async def next_step(cwd, unit):
            self.asked.append(unit)
            return self.nexts[unit]

        def fake(kind):
            async def go(cwd, unit, stage="integrate", started_by="person"):
                # Held from the start, as the attempt the real step opens: opened running, so
                # the scheduler does not launch it again.
                row = self.service.attempts.open(
                    "integration" if kind == "integrate" else "step",
                    self.key,
                    unit,
                    "integrate" if kind == "integrate" else stage,
                    started_by=started_by,
                    state="running",
                )
                self.launched.append((unit, stage, started_by))
                self.began.add(asyncio.current_task())
                try:
                    yield ("chunk", "x")
                    await self.release.wait()
                    yield ("done", {"outcome": "done"})
                finally:
                    self.service.attempts.move(row["id"], "ended", "done")

            return go

        self.service.boards.read = board
        self.service.steps.next_step = next_step
        self.service.steps.run_step = fake("step")
        self.service.steps.integrate = lambda cwd, unit, started_by="person": fake("integrate")(
            cwd, unit, started_by=started_by
        )
        self.service.autopilot.cwds[self.key] = self.ws
        self.service.autopilot.set_setting(self.ws, "autopilot", True)
        self.service.autopilot.stop(self.key)
        # A loop that never passes on its own: each test asks for a pass.
        self.service.autopilot.tasks[self.key] = asyncio.get_running_loop().create_future()
        self.service.autopilot.cwds[self.key] = self.ws
        self.addCleanup(self.release.set)
        # The stand-in steps end their attempt on the bus like the real ones, which wakes the
        # autopilot; here each pass is asked for, so that wake is not taken. The pull request
        # reader's (which names its causes) still is.
        nudge = self.service.autopilot.nudge
        self.service.autopilot.nudge = lambda key, woken_by=None: (
            nudge(key, woken_by) if woken_by else None
        )

    def add(self, name, stage, action="", plan=None, reasons=(), **unit):
        self.units[name] = {
            "name": name,
            "next": action or f"write-{stage}",
            "questions": [],
            **unit,
        }
        self.nexts[name] = {
            "stage": stage,
            "action": action or f"write-{stage}",
            "waiting": [],
            "hold": None,
            "reasons": list(reasons),
        }
        if plan is not None:
            d = self.service.ws.unit_dir(self.ws, name)
            d.mkdir(parents=True, exist_ok=True)
            (d / "plan.md").write_text(
                f"# Plan\n\n## Files that change\n\n{plan}\n\n## Order\n", encoding="utf-8"
            )

    def listed(self, *names: str) -> None:
        """No names: every unit added, in the order it was added."""
        super().listed(*(names or self.units))
        self.shortlisted = True

    async def pass_(self):
        if not self.shortlisted:
            self.listed()
        await self.service.autopilot.run_pass(self.key)
        await self.started()

    async def started(self):
        """Every launch of the last pass has started its step, or ended without one."""
        await self.until(
            lambda: all(
                t in self.began or t.done()
                for _, t in (self.service.autopilot.runs.get(self.key) or {}).values()
            ),
            "the launches to start",
        )

    def picks(self) -> list[dict]:
        return Journal(self.config.working_dir, self.config.data_dir).records(kind="autopilot-pick")

    def stops(self):
        return {u: s["kind"] for u, s in self.service.autopilot.stops.get(self.key, {}).items()}

    async def test_max_parallel_one(self):
        self.service.autopilot.set_setting(self.ws, "max_parallel", 1)
        self.service.autopilot.stop(self.key)
        self.service.autopilot.tasks[self.key] = asyncio.get_running_loop().create_future()
        self.add("0001_a", "spec")
        self.add("0002_b", "spec")
        await self.pass_()
        self.assertEqual(self.launched, [("0001_a", "spec", "autopilot")])
        await self.pass_()
        self.assertEqual(len(self.launched), 1)

    async def test_overlapping_impls_run_one_after_the_other(self):
        self.add("0001_a", "impl", plan="- `coscc/x.py`\n- `coscc/y.py`")
        self.add("0002_b", "impl", plan="- `coscc/y.py`")
        self.add("0003_c", "impl", plan="- `coscc/z.py`")
        await self.pass_()
        self.assertEqual([u for u, _, _ in self.launched], ["0001_a", "0003_c"])
        # `spec.md ## Answers`, câu 1: one ranked above that overlaps does not hold the rest.
        [_, third] = self.picks()
        self.assertEqual(
            (third["unit"], third["passed"]),
            ("0003_c", [{"unit": "0002_b", "reason": "overlap", "detail": "0001_a"}]),
        )
        self.release.set()
        await self.settled()
        self.launched.clear()
        self.release.clear()
        del self.units["0001_a"], self.units["0003_c"]
        await self.pass_()
        self.assertEqual([u for u, _, _ in self.launched], ["0002_b"])

    async def test_turned_off_in_the_middle_of_a_pass_starts_nothing(self):
        read = self.service.boards.read
        reading, go_on = asyncio.Event(), asyncio.Event()

        async def slow_board(cwd):
            reading.set()
            await go_on.wait()
            return await read(cwd)

        self.service.boards.read = slow_board
        self.add("0001_a", "spec")
        self.listed()
        passing = asyncio.get_running_loop().create_task(self.service.autopilot.run_pass(self.key))
        await reading.wait()
        self.service.autopilot.set_setting(self.ws, "autopilot", False)
        go_on.set()
        await passing
        await self.started()
        self.assertEqual((self.launched, self.stops()), ([], {}))

    async def test_turned_off_before_a_launch_runs_starts_nothing(self):
        self.add("0001_a", "spec")
        self.listed()
        await self.service.autopilot.run_pass(self.key)
        self.service.autopilot.set_setting(self.ws, "autopilot", False)
        await self.started()
        self.assertEqual(self.launched, [])

    async def test_one_ship_at_a_time_and_only_when_allowed(self):
        self.add("0001_a", "ship")
        self.add("0002_b", "ship")
        await self.pass_()
        self.assertEqual((self.launched, self.stops()), ([], {"0001_a": "c", "0002_b": "c"}))
        self.service.autopilot.set_setting(self.ws, "autopilot_may_ship", True)
        await self.pass_()
        self.assertEqual(self.launched, [("0001_a", "ship", "autopilot")])

    async def test_b_and_d_stop(self):
        self.add("0001_a", "", action="answer F1")
        self.nexts["0001_a"]["waiting"] = ["F1"]
        self.add("0002_b", "review")
        Journal(self.config.working_dir, self.config.data_dir).append(
            {
                "kind": "integration",
                "workspace": self.key,
                "unit": "0002_b",
                "stage": "integrate",
                "outcome": "needs-person",
                "needs_person": ["x.py on both sides"],
            }
        )
        await self.pass_()
        self.assertEqual(self.launched, [])
        self.assertEqual(self.stops(), {"0001_a": "b", "0002_b": "d"})

    async def test_e_reads_past_what_an_earlier_version_wrote_on_the_unit(self):
        """A `precedent` line, from an earlier version, is no step. A done one does not lift a
        failed step's stop, and a failed one stops nothing."""
        journal = Journal(self.config.working_dir, self.config.data_dir)
        self.add("0001_a", "spec")
        journal.finished(self.key, "0001_a", "spec", "failed")
        journal.finished(self.key, "0001_a", "precedent", "done")
        self.add("0002_b", "plan")
        journal.finished(self.key, "0002_b", "spec", "done")
        journal.finished(self.key, "0002_b", "precedent", "failed")
        await self.pass_()
        self.assertEqual(self.stops(), {"0001_a": "e"})
        self.assertIn(
            "spec step ended failed", self.service.autopilot.stops[self.key]["0001_a"]["reason"]
        )
        self.assertEqual([(u, s) for u, s, _ in self.launched], [("0002_b", "plan")])

    async def test_e_after_screenshots_that_could_not_be_taken_again_until_they_are(self):
        """A retake that failed is no retry; the one after it, taken, lifts the stop."""
        journal = Journal(self.config.working_dir, self.config.data_dir)
        self.add("0001_a", "review")
        journal.finished(self.key, "0001_a", "impl", "done")
        screens = {"kind": "screens", "workspace": self.key, "unit": "0001_a", "stage": "review"}
        journal.append({**screens, "outcome": "failed", "detail": "capture_screens.py exited 2"})
        await self.pass_()
        self.assertEqual((self.launched, self.stops()), ([], {"0001_a": "e"}))
        self.assertIn(
            "screenshots could not be taken again",
            self.service.autopilot.stops[self.key]["0001_a"]["reason"],
        )
        journal.append({**screens, "outcome": "taken"})
        await self.pass_()
        self.assertEqual(self.launched, [("0001_a", "review", "autopilot")])

    async def test_integrate_when_behind_before_and_after_a_pass(self):
        """Was `test_integrate_when_behind_but_never_after_a_pass`. After a pass a unit behind is
        integrated too, where the autopilot may ship; where it may not, a person merges, and the
        unit stops `f` on the gate's reason — `next` names no `ship` for a head behind
        `origin/main`, the ref the board reads `behind` from."""
        behind = (
            "#7 is 2 commit(s) behind origin/main — integrate, then review again; "
            "a round that passes does not count toward the limit"
        )
        self.add(
            "0001_a",
            "",
            action="CI has not finished on #3: t — wait, then ask again",
            reasons=["ci-pending"],
            integration={"state": "behind"},
            rounds=[{"verdict": "changes-requested"}],
            between_pr_and_ship=True,
        )
        self.add(
            "0003_c",
            "",
            action=behind,
            integration={"state": "behind"},
            rounds=[{"verdict": "pass"}],
            between_pr_and_ship=True,
        )
        await self.pass_()
        self.assertEqual(self.launched, [("0001_a", "integrate", "autopilot")])
        self.assertEqual(self.stops(), {"0003_c": "f"})
        self.assertIn(
            "behind origin/main", self.service.autopilot.stops[self.key]["0003_c"]["reason"]
        )

        self.release.set()
        await self.settled()
        self.launched.clear()
        del self.units["0001_a"], self.units["0003_c"]
        self.add(
            "0002_b",
            "",
            action=behind,
            integration={"state": "behind"},
            rounds=[{"verdict": "pass"}],
            between_pr_and_ship=True,
        )
        self.listed("0002_b")
        self.service.autopilot.set_setting(self.ws, "autopilot_may_ship", True)
        await self.pass_()
        self.assertEqual(self.launched, [("0002_b", "integrate", "autopilot")])
        self.assertEqual(self.stops(), {})

    async def test_a_pass_behind_main_is_integrated_not_shipped(self):
        """`next` names no stage — the `ship` gate is closed on a head behind `origin/main` — and
        the board reads the unit `behind`. The pass integrates."""
        self.service.autopilot.set_setting(self.ws, "autopilot_may_ship", True)
        self.add(
            "0001_a",
            "",
            action="#7 is 2 commit(s) behind origin/main — integrate, then review again; "
            "a round that passes does not count toward the limit",
            integration={"state": "behind"},
            rounds=[{"verdict": "pass"}],
            between_pr_and_ship=True,
        )
        await self.pass_()
        self.assertEqual(self.launched, [("0001_a", "integrate", "autopilot")])
        self.assertEqual(self.stops(), {})
        self.assertEqual([p["stage"] for p in self.picks()], ["integrate"])

    async def test_a_refused_ship_stops_on_what_gh_said(self):
        """The stop, and the `autopilot-stop` record, carry the `Refused` line's words."""
        self.service.autopilot.set_setting(self.ws, "autopilot_may_ship", True)
        said = "ship was refused: you do not have permission to merge — finish and accept ship.md"
        self.add(
            "0001_a",
            "",
            action=said,
            integration={"state": "current"},
            rounds=[{"verdict": "pass"}],
            between_pr_and_ship=True,
        )
        await self.pass_()
        self.assertEqual((self.launched, self.stops()), ([], {"0001_a": "f"}))
        self.assertIn(
            "you do not have permission to merge",
            self.service.autopilot.stops[self.key]["0001_a"]["reason"],
        )
        [logged] = Journal(self.config.working_dir, self.config.data_dir).records(
            kind="autopilot-stop"
        )
        self.assertEqual((logged["unit"], logged["stop"]), ("0001_a", "f"))
        self.assertIn("you do not have permission to merge", logged["reason"])

    async def test_a_merge_github_refused_behind_main_is_integrated_not_stopped(self):
        """The merge GitHub refused is the unit's last word; behind `main` the pass integrates, and
        current it stops `f` on what `gh` said."""
        self.service.autopilot.set_setting(self.ws, "autopilot_may_ship", True)
        said = "the head branch is not up to date with the base branch"
        behind = (
            "#7 is 2 commit(s) behind origin/main — integrate, then review again; "
            "a round that passes does not count toward the limit"
        )
        log = Journal(self.config.working_dir, self.config.data_dir)
        for unit, action, state in (("0001_a", behind, "behind"), ("0002_b", "", "current")):
            self.add(
                unit,
                "",
                action=action,
                integration={"state": state},
                rounds=[{"verdict": "pass"}],
                between_pr_and_ship=True,
            )
            log.append(
                {
                    "kind": autopilot.PR_MACHINE,
                    "workspace": self.key,
                    "unit": unit,
                    "stage": "ship",
                    "outcome": "failed",
                    "result": "failed",
                    "reasons": [],
                    "detail": said,
                    "started_by": "autopilot",
                    "merge_refused": True,
                }
            )
        await self.pass_()
        self.assertEqual(self.launched, [("0001_a", "integrate", "autopilot")])
        self.assertEqual(self.stops(), {"0002_b": "f"})
        self.assertIn(said, self.service.autopilot.stops[self.key]["0002_b"]["reason"])

    async def test_red_after_its_own_integration_is_not_integrated_again(self):
        """CI red on its own integration runs the `impl` that `next` names, once. A person's
        integration is still integrated again."""
        red = "CI is red on #3: tests — back to impl: fix on the branch and push"
        for unit, action, plan in (("0001_a", red, "- `a/x.py`"), ("0002_b", "", "- `b/y.py`")):
            self.add(
                unit,
                "impl",
                action=action,
                plan=plan,
                integration={"state": "red-after-integration"},
                reasons=["changes-requested", "ci-red"] if action else [],
                rounds=[{"verdict": "changes-requested"}],
                between_pr_and_ship=True,
            )
        log = Journal(self.config.working_dir, self.config.data_dir)
        for unit, by in (("0001_a", "autopilot"), ("0002_b", "person")):
            log.append(
                {
                    "kind": "integration",
                    "workspace": self.key,
                    "unit": unit,
                    "stage": "integrate",
                    "outcome": "pushed",
                    "mode": "agent",
                    "started_by": by,
                }
            )
        await self.pass_()
        self.assertEqual(
            self.launched, [("0001_a", "impl", "autopilot"), ("0002_b", "integrate", "autopilot")]
        )
        self.assertEqual(self.stops(), {})

    async def test_a_red_branch_name_after_its_own_integration_stops_b_with_next_s_words(
        self,
    ):
        """#97's answer from `next` — no stage, a person needed — on a unit red after the
        autopilot's own integration. Neither `impl` nor `integrate` runs, and the stop is `b` with
        `next`'s words, not `e`."""
        head = "feat/open-questions-wait-for-the-originator-even-when-precedent-answers-them"
        action = (
            "needs a person — CI is red on #97: branch-name — branch-name checks the branch "
            f'name, and no rerun or impl can fix it: "{head}" is not a work branch: '
            "the slug is 71 characters, over the 60 allowed"
        )
        self.add(
            "0001_a",
            "",
            action=action,
            plan="- `a/x.py`",
            integration={"state": "red-after-integration"},
            reasons=["changes-requested", "ci-unfixable", "needs-person"],
            rounds=[{"verdict": "changes-requested"}],
            between_pr_and_ship=True,
        )
        Journal(self.config.working_dir, self.config.data_dir).append(
            {
                "kind": "integration",
                "workspace": self.key,
                "unit": "0001_a",
                "stage": "integrate",
                "outcome": "pushed",
                "mode": "mechanical",
                "started_by": "autopilot",
            }
        )
        await self.pass_()
        self.assertEqual(self.launched, [])
        self.assertEqual(self.stops(), {"0001_a": "b"})
        self.assertEqual(self.service.autopilot.stops[self.key]["0001_a"]["reason"], action)

    async def _impl_after_a_red_rebase(self, outcome: str = "done") -> Journal:
        """0115/#120 up to its `impl`: a `pass`, the autopilot's mechanical rebase, CI red on
        it, and `next` naming `impl` with `cos.mjs`'s words. Returns once that `impl` ended
        `outcome`."""
        self.service.autopilot.set_setting(self.ws, "autopilot_may_ship", True)
        red = "CI is red on #120: tests — back to impl: fix on the branch and push"
        self.add(
            "0001_a",
            "impl",
            action=red,
            plan="- `a/x.py`",
            integration={"state": "red-after-integration"},
            reasons=["ci-red"],
            rounds=[{"verdict": "pass"}],
            between_pr_and_ship=True,
        )
        log = Journal(self.config.working_dir, self.config.data_dir)
        log.append(
            {
                "kind": "integration",
                "workspace": self.key,
                "unit": "0001_a",
                "stage": "integrate",
                "outcome": "pushed",
                "mode": "mechanical",
                "started_by": "autopilot",
            }
        )
        await self.pass_()
        self.assertEqual(self.launched, [("0001_a", "impl", "autopilot")])
        self.assertEqual([p["stage"] for p in self.picks()], ["impl"])
        # The stand-in writes no `start`; the real step has written it by now.
        log.started(self.key, "0001_a", "impl", "autonomous", started_by="autopilot")
        # Its `start` is in the window while it runs, and that is no stop.
        await self.pass_()
        self.assertEqual((len(self.launched), self.stops()), (1, {}))
        self.release.set()
        await self.settled()
        log.finished(self.key, "0001_a", "impl", outcome)
        stops = [
            r for r in log.records(kind="autopilot-stop") if r["unit"] == "0001_a" and r["stop"]
        ]
        self.assertEqual(stops, [])
        return log

    async def test_an_exhausted_impl_after_its_own_integration_runs_once_more(self):
        log = await self._impl_after_a_red_rebase("exhausted")
        await self.pass_()
        self.assertEqual(self.launched, [("0001_a", "impl", "autopilot")] * 2)
        self.assertEqual(self.stops(), {})
        await self.settled()
        log.started(self.key, "0001_a", "impl", "autonomous", started_by="autopilot")
        log.finished(self.key, "0001_a", "impl", "exhausted")
        await self.pass_()
        self.assertEqual(len(self.launched), 2)
        self.assertEqual(self.stops(), {"0001_a": "e"})
        reason = self.service.autopilot.stops[self.key]["0001_a"]["reason"]
        self.assertEqual(reason, "the last impl step ended exhausted")

    async def test_the_impl_after_an_exhausted_one_is_the_one(self):
        log = await self._impl_after_a_red_rebase("exhausted")
        await self.pass_()
        await self.settled()
        log.started(self.key, "0001_a", "impl", "autonomous", started_by="autopilot")
        log.finished(self.key, "0001_a", "impl", "done")
        await self.pass_()
        self.assertEqual(len(self.launched), 2)
        self.assertEqual(self.stops(), {"0001_a": "e"})
        self.assertEqual(
            self.service.autopilot.stops[self.key]["0001_a"]["reason"], autopilot.STILL_RED
        )

    async def test_a_rebase_that_turns_ci_red_runs_impl_once_then_stops(self):
        await self._impl_after_a_red_rebase()
        # The `impl` pushed a commit, so the board no longer reads the integrated head.
        self.units["0001_a"]["integration"] = {"state": "current"}
        await self.pass_()
        self.assertEqual(len(self.launched), 1)
        self.assertEqual(self.stops(), {"0001_a": "e"})
        self.assertEqual(
            self.service.autopilot.stops[self.key]["0001_a"]["reason"], autopilot.STILL_RED
        )

    async def test_red_still_on_the_integrated_head_after_impl_stops(self):
        """The `impl` pushed nothing, and the head is still the one the rebase pushed."""
        await self._impl_after_a_red_rebase()
        await self.pass_()
        self.assertEqual(self.launched, [("0001_a", "impl", "autopilot")])
        self.assertEqual(self.stops(), {"0001_a": "e"})
        self.assertEqual(
            self.service.autopilot.stops[self.key]["0001_a"]["reason"], autopilot.STILL_RED
        )

    async def _still_red_once_main_moved(self, state: str) -> None:
        """The `impl` pushed, CI is still red, and `main` moved on or the pull request conflicts. No
        `integrate`, which would open a new window and a second `impl`."""
        await self._impl_after_a_red_rebase()
        self.units["0001_a"]["integration"] = {"state": state}
        await self.pass_()
        self.assertEqual(self.launched, [("0001_a", "impl", "autopilot")])
        self.assertEqual(self.stops(), {"0001_a": "e"})
        self.assertEqual(
            self.service.autopilot.stops[self.key]["0001_a"]["reason"], autopilot.STILL_RED
        )

    async def test_still_red_and_behind_after_impl_stops(self):
        await self._still_red_once_main_moved("behind")

    async def test_still_red_and_conflicting_after_impl_stops(self):
        await self._still_red_once_main_moved("conflicting")

    async def test_an_integration_before_its_mark_is_counted_against_the_cap(self):
        reading = asyncio.Event()
        self.addCleanup(reading.set)

        async def slow(cwd, unit, started_by="person"):
            # `integrate` fetches and asks `gh` before it takes its mark.
            self.began.add(asyncio.current_task())
            await reading.wait()
            yield ("done", {})

        self.service.steps.integrate = slow
        need = autopilot.reservation("integrate")
        self.service.autopilot.set_setting(
            self.ws, "daily_cap_usd", need + autopilot.reservation("spec") / 2
        )
        self.add(
            "0001_a",
            "",
            action="CI has not finished on #3: t — wait, then ask again",
            reasons=["ci-pending"],
            integration={"state": "behind"},
            rounds=[],
            between_pr_and_ship=True,
        )
        await self.pass_()
        self.assertEqual(self.service.attempts.unfinished(self.key, "0001_a"), [])
        self.assertEqual(self.service.autopilot.cap([], 100.0)["running"], need)
        self.add("0002_b", "spec")
        self.listed()
        await self.pass_()
        self.assertEqual((self.launched, self.stops()), ([], {"0002_b": "cap"}))

    async def test_an_unknown_cost_is_estimated_not_the_cap_reached(self):
        Journal(self.config.working_dir, self.config.data_dir).finished(
            self.key, "0009_z", "spec", "done"
        )
        self.add("0001_a", "spec")
        await self.pass_()
        self.assertEqual(self.launched, [("0001_a", "spec", "autopilot")])
        self.assertNotIn("0001_a", self.stops())

    async def test_a_cap_stop_says_the_estimate(self):
        Journal(self.config.working_dir, self.config.data_dir).finished(
            self.key, "0009_z", "spec", "done"
        )
        self.service.autopilot.set_setting(
            self.ws,
            "daily_cap_usd",
            autopilot.estimate("spec") + autopilot.reservation("spec") - 0.01,
        )
        self.add("0001_a", "spec")
        await self.pass_()
        stop = self.service.autopilot.stops[self.key]["0001_a"]
        self.assertEqual((self.launched, stop["kind"]), ([], "cap"))
        self.assertIn("estimated", stop["reason"])
        self.assertNotIn("cost_usd", stop["reason"])
        self.assertNotIn("reached", stop["reason"])

    async def test_the_cap_block_carries_the_estimate(self):
        log = Journal(self.config.working_dir, self.config.data_dir)
        log.finished(self.key, "0009_z", "spec", "done")
        cap = self.service.autopilot.cap(log.records(), 80.0)
        self.assertEqual(
            set(cap), {"limit", "spent", "known", "estimated", "estimated_count", "running", "day"}
        )
        self.assertEqual(cap["spent"], round(cap["known"] + cap["estimated"], 2))
        self.assertEqual(
            (cap["estimated"], cap["estimated_count"]), (autopilot.estimate("spec"), 1)
        )

    async def test_today_is_the_cap_figure_with_the_autopilot_off(self):
        log = Journal(self.config.working_dir, self.config.data_dir)
        log.finished(self.key, "0009_z", "spec", "done")
        self.service.autopilot.set_setting(self.ws, "daily_cap_usd", 30.0)
        self.assertEqual(
            self.service.autopilot.today(self.ws),
            (self.service.autopilot.cap(log.records(), 30.0)["spent"], 30.0),
        )

    async def test_ci_pending_is_quiet(self):
        self.add(
            "0001_a",
            "",
            action="CI has not finished on #3: t — wait, then ask again",
            reasons=["ci-pending"],
            between_pr_and_ship=True,
        )
        await self.pass_()
        self.assertEqual((self.launched, self.stops()), ([], {}))

    async def test_a_gate_refusal_is_a_stop_f_with_its_words(self):
        async def refused(cwd, unit, stage, started_by="person"):
            raise Invalid("blocked: plan.md is draft")
            yield  # pragma: no cover

        self.service.steps.run_step = refused
        self.add("0001_a", "impl", plan="- `a/b.py`")
        await self.pass_()
        self.assertEqual(
            self.service.autopilot.stops[self.key]["0001_a"],
            {"unit": "0001_a", "kind": "f", "reason": "blocked: plan.md is draft"},
        )

    async def test_a_gate_refusal_on_ci_pending_is_quiet_by_its_code_not_its_words(self):
        words = "blocked: review cannot proceed for 0001_a\n  - CI has not finished on #3: t — wait, then ask again"
        for error, stops in (
            (Refused("blocked: the words changed", ("ci-pending",)), {}),
            (Invalid(words), {"0001_a": "f"}),
        ):

            async def refused(cwd, unit, stage, started_by="person", error=error):
                raise error
                yield  # pragma: no cover

            self.service.steps.run_step = refused
            self.service.autopilot.stops.pop(self.key, None)
            self.add("0001_a", "review")
            await self.pass_()
            self.assertEqual(self.stops(), stops, error)

    async def test_a_race_at_launch_leaves_no_stop_and_another_refusal_leaves_its_code(self):
        for error, stops in (
            (Refused("busy", ("unit-busy",)), {}),
            (Refused("updating", ("updating",)), {}),
            (Refused("no tree", ("no-worktree",)), {"0001_a": "f"}),
        ):

            async def refused(cwd, unit, stage, started_by="person", error=error):
                raise error
                yield  # pragma: no cover

            self.service.steps.run_step = refused
            self.service.autopilot.stops.pop(self.key, None)
            self.add("0001_a", "impl", plan="- `a/b.py`")
            await self.pass_()
            self.assertEqual(self.stops(), stops, error)
        self.assertEqual(self.service.autopilot.stops[self.key]["0001_a"]["code"], "no-worktree")

    async def test_a_bare_invalid_at_launch_has_no_code(self):
        async def refused(cwd, unit, stage, started_by="person"):
            raise Invalid("name a work unit")
            yield  # pragma: no cover

        self.service.steps.run_step = refused
        self.add("0001_a", "impl", plan="- `a/b.py`")
        await self.pass_()
        self.assertNotIn("code", self.service.autopilot.stops[self.key]["0001_a"])

    async def test_a_race_at_next_passes_the_unit_over_and_starts_the_next(self):
        for code, passed in (
            ("unit-busy", "running"),
            ("updating", "running"),
            ("ci-pending", "ci"),
        ):
            self.launched.clear()
            self.units.clear()
            self.service.autopilot.stops.pop(self.key, None)
            self.add("0001_a", "spec")
            self.add("0002_b", "spec")
            real = self.service.steps.next_step

            async def next_step(cwd, unit, code=code, real=real):
                if unit == "0001_a":
                    raise Refused("a race", (code,))
                return await real(cwd, unit)

            with mock.patch.object(self.service.steps, "next_step", next_step):
                self.shortlisted = False
                await self.pass_()
            self.assertEqual(self.stops(), {}, code)
            self.assertEqual([u for u, _, _ in self.launched], ["0002_b"], code)
            self.release.set()
            await self.settled()
            self.release.clear()

    async def test_a_coded_refusal_at_next_is_a_stop_with_its_code(self):
        self.add("0001_a", "spec")

        async def next_step(cwd, unit):
            raise Refused("no tree", ("no-worktree",))

        self.service.steps.next_step = next_step
        await self.pass_()
        self.assertEqual(
            self.service.autopilot.stops[self.key]["0001_a"],
            {"unit": "0001_a", "kind": "f", "reason": "no tree", "code": "no-worktree"},
        )

    async def test_off_loopback_nothing_runs_and_the_board_says_why(self):
        use_config(self.service, dataclasses.replace(self.config, host="0.0.0.0"))
        self.add("0001_a", "spec")
        await self.pass_()
        self.assertEqual(self.launched, [])
        self.assertIn("127.0.0.1", self.service.autopilot.stops[self.key][""]["reason"])

    # --- , the workspace's own stop in the run log ---------------------

    def logged(self) -> list[tuple[str, str]]:
        return [
            (r["unit"], r["stop"])
            for r in Journal(self.config.working_dir, self.config.data_dir).records(
                kind="autopilot-stop"
            )
        ]

    async def test_an_empty_shortlist_is_logged_once_and_its_clearing_once(self):
        self.add("0001_a", "spec")
        for _ in range(2):
            await self.service.autopilot.run_pass(self.key)
            await self.started()
        self.assertEqual(self.logged(), [("", "shortlist")])
        self.listed()
        await self.service.autopilot.run_pass(self.key)
        await self.started()
        self.assertEqual(self.logged(), [("", "shortlist"), ("", "")])
        [row] = [
            r
            for r in Journal(self.config.working_dir, self.config.data_dir).records(
                kind="autopilot-stop"
            )
            if r["stop"]
        ]
        self.assertEqual((row["workspace"], row["reason"]), (self.key, autopilot.NO_SHORTLIST))

    async def test_off_loopback_is_logged_with_no_unit(self):
        use_config(self.service, dataclasses.replace(self.config, host="0.0.0.0"))
        self.add("0001_a", "spec")
        await self.pass_()
        await self.pass_()
        self.assertEqual(self.logged(), [("", "f")])

    async def test_a_unit_s_launch_failing_keeps_the_workspace_stop_and_logs_only_the_unit(self):
        # A call that looked at one unit did not look at the workspace.
        workspace = {"unit": "", "kind": "shortlist", "reason": autopilot.NO_SHORTLIST}
        self.service.autopilot.set_stops(self.key, {"": workspace})
        self.service.autopilot.set_stops(
            self.key,
            {"0001_a": {"unit": "0001_a", "kind": "f", "reason": "said"}},
            {"0001_a"},
        )
        self.assertEqual(self.service.autopilot.stops[self.key][""], workspace)
        self.assertEqual(self.logged(), [("", "shortlist"), ("0001_a", "f")])

    # --- , the shortlist's order -----------------------------------------

    def one_at_a_time(self):
        self.service.autopilot.set_setting(self.ws, "max_parallel", 1)
        self.service.autopilot.stop(self.key)
        self.service.autopilot.tasks[self.key] = asyncio.get_running_loop().create_future()

    async def test_the_last_shortlist_record_orders_the_pass(self):
        self.one_at_a_time()
        self.add("0001_a", "spec")
        self.add("0002_b", "spec")
        self.listed("0001_a", "0002_b")
        self.listed("0002_b", "0001_a")
        await self.pass_()
        self.assertEqual(self.launched, [("0002_b", "spec", "autopilot")])

    async def test_a_unit_off_the_shortlist_is_neither_asked_nor_started(self):
        self.add("0001_a", "spec")
        self.add("0002_b", "spec")
        self.listed("0002_b")
        await self.pass_()
        self.assertEqual(
            (self.launched, self.asked, self.stops()),
            ([("0002_b", "spec", "autopilot")], ["0002_b"], {}),
        )

    async def test_no_shortlist_starts_nothing_and_says_so(self):
        self.add("0001_a", "spec")
        await self.service.autopilot.run_pass(self.key)
        await self.started()
        self.assertEqual((self.launched, self.asked, self.stops()), ([], [], {"": "shortlist"}))
        self.assertEqual(
            self.service.autopilot.stops[self.key][""]["reason"], autopilot.NO_SHORTLIST
        )
        self.listed()
        await self.service.autopilot.run_pass(self.key)
        await self.started()
        self.assertEqual((self.launched, self.stops()), ([("0001_a", "spec", "autopilot")], {}))

    async def test_an_empty_shortlist_is_none(self):
        self.add("0001_a", "spec")
        self.listed()
        _Base.listed(self)
        await self.pass_()
        self.assertEqual((self.launched, self.stops()), ([], {"": "shortlist"}))

    async def test_rank_not_number(self):
        self.one_at_a_time()
        self.add("0001_a", "spec")
        self.add("0003_c", "spec")
        self.listed("0003_c", "0001_a")
        await self.pass_()
        self.assertEqual(self.launched, [("0003_c", "spec", "autopilot")])
        [pick] = self.picks()
        self.assertEqual((pick["rank"], pick["passed"]), (1, []))

    async def test_every_pass_asks_every_shortlisted_unit(self):
        self.add("0001_a", "spec")
        self.add("0002_b", "", action="finished", reasons=["finished"])
        self.add(
            "0003_c",
            "",
            action="CI has not finished on #3: t — wait, then ask again",
            reasons=["ci-pending"],
            between_pr_and_ship=True,
        )
        await self.pass_()
        self.assertEqual(self.asked, ["0001_a", "0002_b", "0003_c"])
        await self.pass_()
        self.assertEqual(len(self.asked), 6)
        self.assertEqual(self.launched, [("0001_a", "spec", "autopilot")])

    async def test_a_pick_record_names_what_it_passed(self):
        self.add("0001_a", "")
        self.nexts["0001_a"]["hold"] = {
            "state": "paused",
            "reason": "later",
            "by": "Leif",
            "date": "2026-09-25",
        }
        self.add("0002_b", "spec")
        self.add("0003_c", "spec")
        self.listed("0001_a", "0002_b", "0003_c")
        await self.pass_()
        second, third = self.picks()
        self.assertEqual(
            (second["unit"], second["stage"], second["rank"], second["passed"]),
            ("0002_b", "spec", 2, [{"unit": "0001_a", "reason": "held", "detail": "paused"}]),
        )
        self.assertEqual((third["unit"], third["pass"]), ("0003_c", second["pass"]))
        self.assertEqual(third["passed"], second["passed"])
        self.assertEqual(second["shortlist"]["units"], ["0001_a", "0002_b", "0003_c"])
        self.assertEqual(second["shortlist"]["n"], 1)
        self.assertTrue(second["shortlist"]["at"])
        self.assertEqual(self.stops(), {})

    async def test_no_record_no_start(self):
        self.add("0001_a", "spec")
        self.add("0002_b", "spec")
        append = Journal.append

        def refusing(journal, record, timeout=None):
            if record.get("kind") == "autopilot-pick":
                raise Busy("the run log is busy")
            return append(journal, record, timeout)

        self.listed()
        with mock.patch.object(Journal, "append", refusing):
            await self.pass_()
        self.assertEqual((self.launched, self.stops()), ([], {"": "f"}))
        self.assertIn("could not record", self.service.autopilot.stops[self.key][""]["reason"])

    async def test_a_held_unit_is_no_candidate(self):
        """And a shortlisted unit whose gate is closed is still not started."""
        self.add("0001_a", "")
        self.nexts["0001_a"]["hold"] = {
            "state": "dropped",
            "reason": "no",
            "by": "Leif",
            "date": "2026-09-25",
        }
        await self.pass_()
        self.assertEqual((self.launched, self.picks(), self.stops()), ([], [], {}))

        async def refused(cwd, unit, stage, started_by="person"):
            raise Invalid("blocked: plan.md is draft")
            yield  # pragma: no cover

        self.service.steps.run_step = refused
        self.add("0002_b", "impl", plan="- `a/b.py`")
        self.listed()
        await self.pass_()
        self.assertEqual(self.stops(), {"0002_b": "f"})

    # --- , an answered draft runs again ------------------------------------

    def add_rerun(self, name, stage="intent", *before, plan=None):
        """`next` says `rerun: stage`, after `before`'s records and then one `answer`. `plan` is for
        a code stage, whose files the autopilot reads off `plan.md`."""
        self.add(
            name,
            "",
            action=f"finish and accept {stage}.md",
            plan=plan,
            stages=[{"stage": stage, "file": f"{stage}.md", "status": "draft"}],
        )
        self.nexts[name]["rerun"] = stage
        log = Journal(self.config.working_dir, self.config.data_dir)
        for kind in (*before, "answer"):
            log.append(
                {
                    "kind": kind,
                    "workspace": self.key,
                    "unit": name,
                    "stage": stage,
                    "started_by": "person",
                }
            )

    async def test_a_rerun_is_picked_like_any_step_and_in_rank(self):
        self.add_rerun("0001_a")
        self.add("0002_b", "spec")
        await self.pass_()
        self.assertEqual(
            self.launched, [("0001_a", "intent", "autopilot"), ("0002_b", "spec", "autopilot")]
        )
        self.assertEqual(
            [(p["unit"], p["stage"]) for p in self.picks()],
            [("0001_a", "intent"), ("0002_b", "spec")],
        )
        self.assertEqual(self.stops(), {})

    async def test_off_the_shortlist_it_is_not_asked(self):
        self.add_rerun("0001_a")
        self.add("0002_b", "spec")
        self.listed("0002_b")
        await self.pass_()
        self.assertEqual((self.asked, [u for u, _, _ in self.launched]), (["0002_b"], ["0002_b"]))

    async def test_a_closed_gate_is_still_a_stop_f(self):
        async def refused(cwd, unit, stage, started_by="person"):
            raise Invalid("blocked: idea.md is draft")
            yield  # pragma: no cover

        self.service.steps.run_step = refused
        self.add_rerun("0001_a")
        await self.pass_()
        self.assertEqual(
            self.service.autopilot.stops[self.key]["0001_a"],
            {"unit": "0001_a", "kind": "f", "reason": "blocked: idea.md is draft"},
        )

    async def test_two_reruns_after_answers_stop_it(self):
        self.add_rerun("0001_a", "spec", "start", "answer", "start", "answer", "start")
        self.add("0002_b", "spec")
        await self.pass_()
        self.assertEqual(
            (self.picks()[0]["unit"], [u for u, _, _ in self.launched]), ("0002_b", ["0002_b"])
        )
        self.assertEqual(self.stops(), {"0001_a": "reruns"})
        self.assertEqual(
            self.service.autopilot.stops[self.key]["0001_a"]["reason"],
            "spec.md was run again 2 times after its answers; a person decides the next run.",
        )
        self.assertEqual(self.picks()[0]["passed"][0]["reason"], "stop")

    async def test_one_rerun_before_still_runs(self):
        self.add_rerun("0001_a", "intent", "start", "answer", "start")
        await self.pass_()
        self.assertEqual(self.launched, [("0001_a", "intent", "autopilot")])

    async def test_no_answer_since_the_last_run_is_the_stop_f(self):
        # `start, answer, start`: the rerun ended `draft` still read as answered.
        self.add_rerun("0001_a", "intent", "start")
        Journal(self.config.working_dir, self.config.data_dir).append(
            {
                "kind": "start",
                "workspace": self.key,
                "unit": "0001_a",
                "stage": "intent",
                "started_by": "autopilot",
            }
        )
        self.add("0002_b", "spec")
        await self.pass_()
        self.assertEqual(self.launched, [("0002_b", "spec", "autopilot")])
        self.assertEqual(
            self.service.autopilot.stops[self.key]["0001_a"],
            {"unit": "0001_a", "kind": "f", "reason": "finish and accept intent.md"},
        )
        await self.pass_()
        self.assertEqual(len(self.launched), 1)

    async def test_an_answered_draft_with_no_answer_record_is_the_stop_f(self):
        self.add_rerun("0001_a")
        Journal(self.config.working_dir, self.config.data_dir).append(
            {
                "kind": "start",
                "workspace": self.key,
                "unit": "0001_a",
                "stage": "intent",
                "started_by": "person",
            }
        )
        await self.pass_()
        self.assertEqual((self.launched, self.stops()), ([], {"0001_a": "f"}))

    async def test_held_back_by_max_parallel_it_says_full(self):
        self.one_at_a_time()
        self.add("0001_a", "spec")
        await self.pass_()
        self.assertEqual(self.launched, [("0001_a", "spec", "autopilot")])
        self.add_rerun("0002_b")
        self.add("0003_c", "spec")
        self.listed()
        await self.pass_()
        self.assertEqual(len(self.launched), 1)
        # Only the rerun says so; an ordinary candidate held back the same way still does not.
        self.assertEqual(self.stops(), {"0002_b": "full"})
        self.assertEqual(
            self.service.autopilot.stops[self.key]["0002_b"]["reason"],
            "intent waits to run again: 1 step is already running, the most this workspace allows.",
        )
        self.release.set()
        await self.settled()
        self.release.clear()
        del self.units["0001_a"]
        await self.pass_()
        self.assertEqual(self.launched[1], ("0002_b", "intent", "autopilot"))
        self.assertEqual(self.stops(), {})
        logged = Journal(self.config.working_dir, self.config.data_dir).records(
            kind="autopilot-stop"
        )
        self.assertEqual(
            [(r["unit"], r["stop"]) for r in logged], [("0002_b", "full"), ("0002_b", "")]
        )

    # --- , an answered draft impl runs again --------------------------------

    async def test_an_answered_draft_impl_runs_impl_again(self):
        self.add_rerun("0001_a", "impl", "start", plan="- `coscc/x.py`")
        await self.pass_()
        self.assertEqual(self.launched, [("0001_a", "impl", "autopilot")])
        self.assertEqual([(p["unit"], p["stage"]) for p in self.picks()], [("0001_a", "impl")])
        self.assertEqual(self.stops(), {})

    async def test_two_impl_reruns_after_answers_stop_it(self):
        self.add_rerun(
            "0001_a", "impl", "start", "answer", "start", "answer", "start", plan="- `coscc/x.py`"
        )
        await self.pass_()
        self.assertEqual((self.launched, self.stops()), ([], {"0001_a": "reruns"}))
        self.assertEqual(
            self.service.autopilot.stops[self.key]["0001_a"]["reason"],
            "impl.md was run again 2 times after its answers; a person decides the next run.",
        )

    async def test_no_answer_since_the_last_impl_is_the_stop_f(self):
        self.add_rerun("0001_a", "impl", "start", plan="- `coscc/x.py`")
        Journal(self.config.working_dir, self.config.data_dir).append(
            {
                "kind": "start",
                "workspace": self.key,
                "unit": "0001_a",
                "stage": "impl",
                "started_by": "autopilot",
            }
        )
        await self.pass_()
        self.assertEqual(self.launched, [])
        self.assertEqual(
            self.service.autopilot.stops[self.key]["0001_a"],
            {"unit": "0001_a", "kind": "f", "reason": "finish and accept impl.md"},
        )

    async def test_off_starts_no_impl(self):
        self.add_rerun("0001_a", "impl", "start", plan="- `coscc/x.py`")
        self.service.autopilot.set_setting(self.ws, "autopilot", False)
        await self.pass_()
        self.assertEqual(self.launched, [])

    # --- , a step that only ran out of turns runs once more -----------------

    def ran_out(self, unit, stage):
        Journal(self.config.working_dir, self.config.data_dir).finished(
            self.key, unit, stage, "exhausted"
        )

    async def test_a_first_exhausted_step_runs_its_stage_again(self):
        self.ran_out("0001_a", "plan")
        self.add("0001_a", "plan")
        await self.pass_()
        self.assertEqual(self.launched, [("0001_a", "plan", "autopilot")])
        self.assertEqual([(p["unit"], p["stage"]) for p in self.picks()], [("0001_a", "plan")])
        self.assertEqual(self.stops(), {})

    async def test_the_rerun_that_runs_out_again_stops_e(self):
        self.ran_out("0001_a", "plan")
        self.add("0001_a", "plan")
        await self.pass_()
        self.assertEqual(len(self.picks()), 1)
        self.release.set()
        await self.settled()
        self.ran_out("0001_a", "plan")
        await self.pass_()
        self.assertEqual(self.stops(), {"0001_a": "e"})
        self.assertEqual(
            self.service.autopilot.stops[self.key]["0001_a"]["reason"],
            "the last plan step ended exhausted",
        )
        self.assertEqual((len(self.picks()), len(self.launched)), (1, 1))

    async def test_an_exhausted_ship_stops_the_first_time(self):
        self.service.autopilot.set_setting(self.ws, "autopilot_may_ship", True)
        self.ran_out("0001_a", "ship")
        self.add("0001_a", "ship")
        await self.pass_()
        self.assertEqual((self.launched, self.stops()), ([], {"0001_a": "e"}))

    async def test_a_held_unit_or_a_closed_gate_after_a_first_exhausted_step(self):
        self.ran_out("0001_a", "plan")
        self.add("0001_a", "")
        self.nexts["0001_a"]["hold"] = {
            "state": "paused",
            "reason": "later",
            "by": "Leif",
            "date": "2026-09-27",
        }

        async def refused(cwd, unit, stage, started_by="person"):
            raise Invalid("blocked: plan.md is draft")
            yield  # pragma: no cover

        self.service.steps.run_step = refused
        self.ran_out("0002_b", "impl")
        self.add("0002_b", "impl", plan="- `a/b.py`")
        await self.pass_()
        self.assertEqual([p["unit"] for p in self.picks()], ["0002_b"])
        self.assertEqual(self.stops(), {"0002_b": "f"})
        self.assertEqual(
            self.service.autopilot.stops[self.key]["0002_b"],
            {"unit": "0002_b", "kind": "f", "reason": "blocked: plan.md is draft"},
        )

    async def test_the_rerun_branch_passes_the_count_too_when_it_ran_out(self):
        # `start, answer, start, end exhausted`: no answer since the last start, so the stop is
        # what `stop_for` says with no `rerun` — `f`, not the `e` a count left at 0 would give.
        self.add_rerun("0001_a", "plan", "start")
        Journal(self.config.working_dir, self.config.data_dir).append(
            {
                "kind": "start",
                "workspace": self.key,
                "unit": "0001_a",
                "stage": "plan",
                "started_by": "autopilot",
            }
        )
        self.ran_out("0001_a", "plan")
        await self.pass_()
        self.assertEqual(self.launched, [])
        self.assertEqual(
            self.service.autopilot.stops[self.key]["0001_a"],
            {"unit": "0001_a", "kind": "f", "reason": "finish and accept plan.md"},
        )

    # --- , a prose step whose reply lacked its opening runs once more ---------

    def unopened(self, unit, stage):
        Journal(self.config.working_dir, self.config.data_dir).finished(
            self.key,
            unit,
            stage,
            "failed",
            detail=f"{stage}.md lacks its opening: no `Status:` line in its header",
        )

    async def test_a_first_opening_failure_runs_its_stage_again(self):
        self.unopened("0001_a", "plan")
        self.add("0001_a", "plan")
        await self.pass_()
        self.assertEqual(self.launched, [("0001_a", "plan", "autopilot")])
        self.assertEqual(self.stops(), {})

    async def test_the_rerun_that_fails_the_same_way_stops_e(self):
        self.unopened("0001_a", "plan")
        self.add("0001_a", "plan")
        await self.pass_()
        self.release.set()
        await self.settled()
        self.unopened("0001_a", "plan")
        await self.pass_()
        self.assertEqual(self.stops(), {"0001_a": "e"})
        self.assertEqual(
            self.service.autopilot.stops[self.key]["0001_a"]["reason"],
            "the last plan step ended failed",
        )
        self.assertEqual((len(self.picks()), len(self.launched)), (1, 1))

    async def test_the_two_counts_are_apart(self):
        self.ran_out("0001_a", "plan")
        self.add("0001_a", "plan")
        await self.pass_()
        self.release.set()
        await self.settled()
        self.unopened("0001_a", "plan")
        await self.pass_()
        self.assertEqual((len(self.launched), self.stops()), (2, {}))
        await self.settled()
        Journal(self.config.working_dir, self.config.data_dir).finished(
            self.key, "0001_a", "plan", "failed", detail="the session returned nothing"
        )
        await self.pass_()
        self.assertEqual(self.stops(), {"0001_a": "e"})
        self.assertEqual(len(self.launched), 2)

    async def test_the_rerun_branch_passes_the_count_too_when_unopened(self):
        self.add_rerun("0001_a", "plan", "start")
        Journal(self.config.working_dir, self.config.data_dir).append(
            {
                "kind": "start",
                "workspace": self.key,
                "unit": "0001_a",
                "stage": "plan",
                "started_by": "autopilot",
            }
        )
        self.unopened("0001_a", "plan")
        await self.pass_()
        self.assertEqual(self.launched, [])
        self.assertEqual(
            self.service.autopilot.stops[self.key]["0001_a"],
            {"unit": "0001_a", "kind": "f", "reason": "finish and accept plan.md"},
        )

    # --- , an old exhausted `ship` before one that only records ---------------

    RECORDING = "ship — #95 was merged as abc1234 at 2026-09-20T00:00:00Z: record it in ship.md; do not merge"
    MERGING = "ship — merge with --match-head-commit abc1234"

    def shipped(self, unit, **start):
        Journal(self.config.working_dir, self.config.data_dir).append(
            {
                "kind": "start",
                "workspace": self.key,
                "unit": unit,
                "stage": "ship",
                "started_by": "person",
                **start,
            }
        )
        self.ran_out(unit, "ship")

    def ran_out_at(self, unit):
        ends = Journal(self.config.working_dir, self.config.data_dir).records(kind="end")
        return [e for e in ends if e.get("unit") == unit and e.get("outcome") == "exhausted"][-1][
            "at"
        ]

    async def test_an_old_exhausted_ship_starts_a_recording_ship(self):
        self.service.autopilot.set_setting(self.ws, "autopilot_may_ship", True)
        self.shipped("0001_a")
        self.add("0001_a", "ship", action=self.RECORDING, reasons=["recording-ship"])
        await self.pass_()
        self.assertEqual((self.launched, self.stops()), ([("0001_a", "ship", "autopilot")], {}))
        [pick] = self.picks()
        self.assertEqual(
            (pick["stage"], pick["past_exhausted"]), ("ship", {"at": self.ran_out_at("0001_a")})
        )

    async def test_an_old_exhausted_ship_still_stops_a_merging_ship(self):
        self.service.autopilot.set_setting(self.ws, "autopilot_may_ship", True)
        self.shipped("0001_a")
        self.add("0001_a", "ship", action=self.MERGING)
        await self.pass_()
        self.assertEqual((self.launched, self.picks(), self.stops()), ([], [], {"0001_a": "e"}))
        self.assertEqual(
            self.service.autopilot.stops[self.key]["0001_a"]["reason"],
            "the last ship step ended exhausted",
        )

    async def test_a_recording_ship_that_ran_out_is_not_run_again(self):
        self.service.autopilot.set_setting(self.ws, "autopilot_may_ship", True)
        self.shipped("0001_a", ship_mode="record")
        self.add("0001_a", "ship", action=self.RECORDING, reasons=["recording-ship"])
        await self.pass_()
        self.assertEqual((self.launched, self.stops()), ([], {"0001_a": "e"}))

    async def test_a_pick_with_nothing_skipped_has_no_past_exhausted(self):
        self.add("0001_a", "plan")
        await self.pass_()
        self.assertEqual(self.launched, [("0001_a", "plan", "autopilot")])
        self.assertNotIn("past_exhausted", self.picks()[0])

    # --- an open question waits for a person -------------------------------------

    def asks(self, name, stage="spec", n=1):
        """A unit whose `intent.md` has `n` open questions, and `next` naming `stage`."""
        self.add(name, stage)
        self.units[name].update(
            stages=[{"stage": "intent", "file": "intent.md"}, {"stage": "spec", "file": "spec.md"}],
            questions=[
                {
                    "artifact": "intent.md",
                    "n": k,
                    "text": f"q{k}?",
                    "answered": False,
                    "counted": True,
                }
                for k in range(1, n + 1)
            ],
        )

    async def test_the_stop_names_each_question_that_waits(self):
        self.asks("0001_a", n=2)
        await self.pass_()
        self.assertEqual((self.launched, self.stops(), self.picks()), ([], {"0001_a": "a"}, []))
        self.assertEqual(
            self.service.autopilot.stops[self.key]["0001_a"]["reason"],
            "open questions: intent.md question 1, intent.md question 2",
        )

    async def test_a_unit_that_waits_for_a_person_takes_no_place_under_max_parallel(self):
        self.service.autopilot.set_setting(self.ws, "max_parallel", 1)
        self.service.autopilot.stop(self.key)
        self.service.autopilot.tasks[self.key] = asyncio.get_running_loop().create_future()
        self.service.autopilot.cwds[self.key] = self.ws
        self.asks("0001_a")
        self.add("0002_b", "spec")
        await self.pass_()
        self.assertEqual(self.launched, [("0002_b", "spec", "autopilot")])
        self.assertEqual(self.stops(), {"0001_a": "a"})

    # --- the pull request reader ---------------------------------------------------
    #
    # The loop here is a future that never resolves, so no pass comes on its own: each read
    # below stands for one tick of `ci_poll_seconds`, and a pass that follows it is one the
    # read scheduled, well before `POLL_SECONDS`.

    def a_machine(self, gh, files=("coscc/a.py",)):
        """The service's PR machine over its own database, with `gh`, push and diff faked."""

        async def push(tree, branch):
            return None

        async def head(tree):
            return test_prmachine.HEAD

        async def read_files(tree, head):
            return list(files)

        machine = prmachine.Machine(
            self.service.ws.unit_meta().history,
            self.service.ws.journal(),
            gh=gh,
            push=push,
            head=head,
            files=read_files,
        )
        self.service.steps.pr_machine = lambda: machine
        return machine

    async def an_open_pr(self, machine, name):
        d = self.service.ws.unit_dir(self.ws, name)
        d.mkdir(parents=True, exist_ok=True)
        out = await machine.open_pr(
            prmachine.Unit(self.key, name, d, self.ws, "fix/x", "fix/x", "fix")
        )
        self.assertEqual(out.result, "opened")

    def waiting(self, machine, until, reason):
        """`next` as `cos.mjs` answers it: the scripted stage once `until()`, else nothing."""

        async def next_step(cwd, unit):
            self.asked.append(unit)
            if until():
                return self.nexts[unit]
            return {"stage": "", "action": reason, "waiting": [], "hold": None, "reasons": [reason]}

        self.service.steps.next_step = next_step

    async def test_green_ci_starts_a_pass_before_the_poll(self):
        gh = test_prmachine.FakeGh(buckets=("pending",))
        machine = self.a_machine(gh)
        await self.an_open_pr(machine, "0001_a")
        self.add("0001_a", "review")
        self.waiting(
            machine,
            lambda: prmachine.state(machine.history, self.key, "0001_a")["ci"] == "green",
            "ci-pending",
        )
        self.listed()
        self.assertEqual((await self.service.autopilot.pr_read(self.key)).moved, [("0001_a", "ci")])
        await self.settled()
        self.assertEqual(
            (self.asked, self.launched), (["0001_a"], []), "pending scheduled one pass"
        )
        gh.buckets = ("pass",)
        green = await self.service.autopilot.pr_read(self.key)
        self.assertEqual(green.moved, [("0001_a", "ci")])
        await self.until(lambda: self.launched, "the pass the green read scheduled")
        self.assertEqual(self.launched, [("0001_a", "review", "autopilot")])
        # Design: the pass's record names the transition that scheduled it, by its row.
        [row] = [
            r
            for r in machine.history.transitions(self.key, "0001_a", "pr.md")
            if r["guard"] == "ci-at-head"
        ][-1:]
        [pick] = self.picks()
        self.assertEqual(
            pick["woken_by"], [{"unit": "0001_a", "transition": "ci", "id": row["id"]}]
        )
        # Green at the same head is settled: the next read asks only for the list.
        got = await self.service.autopilot.pr_read(self.key)
        self.assertEqual((got.moved, got.calls), ([], 1))

    async def test_a_merge_frees_a_unit_waiting_on_it(self):
        gh = test_prmachine.FakeGh(buckets=("pass",))
        machine = self.a_machine(gh)
        await self.an_open_pr(machine, "0001_a")
        self.add("0002_b", "impl", plan="- `coscc/b.py`")
        self.waiting(
            machine,
            lambda: prmachine.state(machine.history, self.key, "0001_a")["state"] == "merged",
            "waiting-on",
        )
        self.listed("0002_b")
        await self.pass_()
        self.assertEqual(self.launched, [])
        gh.open_prs, gh.state = [], "MERGED"
        cleaned = []

        async def cleanup(cwd, unit):
            cleaned.append(unit)
            return {"removed": True}

        self.service.steps.cleanup = cleanup
        self.assertEqual(
            (await self.service.autopilot.pr_read(self.key)).moved, [("0001_a", "merged")]
        )
        await self.until(lambda: self.launched, "the pass the merge scheduled")
        self.assertEqual(self.launched, [("0002_b", "impl", "autopilot")])
        self.assertEqual(gh.count("pr", "merge"), 0, "a merge made elsewhere is only recorded")
        # No `ship` step follows it, so the reader writes what one did.
        ships = [(r["unit"], r["result"]) for r in self.service.ws.journal().records(kind="ship")]
        self.assertEqual((ships, cleaned), ([("0001_a", "shipped")], ["0001_a"]))

    async def test_a_merge_the_start_up_reconcile_records_leaves_the_ship_row(self):
        """At a restart: a `ship` that merged and died before its row."""
        gh = test_prmachine.FakeGh(buckets=("pass",), crash_after_merge=True)
        machine = self.a_machine(gh)
        await self.an_open_pr(machine, "0001_a")
        with machine.history.data.write() as conn:
            conn.execute(
                "INSERT INTO review_rounds (at, root, workspace, unit, n, run, head, verdict, screens) "
                "VALUES ('2026-09-29', ?, ?, '0001_a', 1, 'r', ?, 'pass', '[]')",
                (str(machine.history.working_dir), self.key, test_prmachine.HEAD),
            )
        d = self.service.ws.unit_dir(self.ws, "0001_a")
        with self.assertRaises(test_prmachine.Crash):
            await machine.ship(
                prmachine.Unit(self.key, "0001_a", d, self.ws, "fix/x", "fix/x", "fix")
            )
        cleaned = []

        async def cleanup(cwd, unit):
            cleaned.append(unit)
            return {"removed": True}

        self.service.steps.cleanup = cleanup
        got = await self.service.steps.reconcile_prs()
        self.assertEqual([o["result"] for o in got], ["recorded"])
        ships = [(r["unit"], r["result"]) for r in self.service.ws.journal().records(kind="ship")]
        self.assertEqual(
            (ships, cleaned, gh.count("pr", "merge")), ([("0001_a", "shipped")], ["0001_a"], 1)
        )

    async def test_an_impl_waits_on_an_open_pull_request_the_machine_holds_until_it_merges(self):
        """Over the machine's own rows: the files are the ones the reader read."""
        gh = test_prmachine.FakeGh(buckets=("pass",))
        machine = self.a_machine(gh)
        await self.an_open_pr(machine, "0001_a")
        await self.service.autopilot.pr_read(self.key)
        await self.settled()
        self.add("0002_b", "impl", plan="- `coscc/a.py`")
        self.add("0003_c", "impl", plan="- `coscc/c.py`")
        await self.pass_()
        self.assertEqual(self.launched, [("0003_c", "impl", "autopilot")])
        [pick] = self.picks()
        self.assertEqual(
            pick["passed"], [{"unit": "0002_b", "reason": "overlap-pr", "detail": "#7"}]
        )
        # What the board's card shows, kept from the same pass.
        self.assertEqual(self.service.autopilot.held[self.key].get("0002_b"), ("overlap-pr", "#7"))
        gh.open_prs, gh.state = [], "MERGED"
        await self.service.autopilot.pr_read(self.key)
        await self.until(lambda: len(self.launched) == 2, "the pass the merge scheduled")
        self.assertEqual(self.launched[1], ("0002_b", "impl", "autopilot"))
        self.assertNotIn("0002_b", self.service.autopilot.held[self.key])
        self.service.autopilot.stop(self.key)
        self.assertNotIn(self.key, self.service.autopilot.held)

    async def test_off_the_reader_calls_no_gh(self):
        gh = test_prmachine.FakeGh()
        machine = self.a_machine(gh)
        await self.an_open_pr(machine, "0001_a")
        calls = len(gh.calls)
        self.service.autopilot.stop(self.key)
        got = await self.service.autopilot.pr_read(self.key)
        self.assertEqual((got.calls, len(gh.calls)), (0, calls))


class ResumedAtStartUp(unittest.TestCase):
    """The Reflex lifespan task, since the real stack never runs `api.py`'s.

    Sending `lifespan.startup` through `served()` compiles the page (about 14 s), so the
    test checks the registration and the call; `impl.md` records the run through `served()`."""

    def test_the_app_resumes_the_autopilot_when_it_starts(self):
        # Synchronous: Reflex registers its states in a context an async test's task lacks.
        import coscc.coscc as composed
        from coscc.state.app import API

        # Through the one task that takes paused sessions up first.
        self.assertIn(composed.resume_after_update, composed.app._lifespan_tasks)
        calls: list[int] = []

        class Stand:
            def __init__(self):
                self.resume = self

            async def resume_after_update(self):
                calls.append(1)
                return []

        real = API.state.service
        API.state.service = Stand()
        try:
            asyncio.run(composed.resume_after_update())
        finally:
            API.state.service = real
        self.assertEqual(calls, [1])


if __name__ == "__main__":
    unittest.main()
