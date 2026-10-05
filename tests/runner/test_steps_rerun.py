"""A person runs `pr` again from the board, in-process, spending nothing.

The chain the spec's outcome names: the stages offered, a rerun of `pr` with a note, a `pr`
session that ends `done`, then `coscc.loop next` naming the review and the `ship` gate closed.
the loop is the real one, asked through `coscc/units/board.py` as the app asks it; only the
worktree, the sync onto GitHub and `Runner` itself stand in."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.bus import Bus
from coscc.units import board as _board
from tests.units.test_meta import WithSnapshot

board_reader = WithSnapshot(_board)
from coscc.units import worktrees
from coscc.config import Config
from coscc.runner.reply import RunError
from coscc.service import Service
from coscc.runner.queue import Refused
from coscc.kernel import Invalid
from coscc.agent.sessions import Sessions
from coscc.units.board import unit_state

SHA = "a" * 40
PR_MD = "# PR: feat(0001): x\nPR: https://github.com/o/r/pull/7. Status: accepted.\n\nthân cũ\n"
ARTIFACTS = {
    "intent.md": "# Intent: x\nType: feat. Status: accepted.\n\n## Open questions\n\n1. Một?\n",
    "spec.md": "# Spec: x\nIntent: intent.md. Status: accepted.\n\nR1.\n",
    "plan.md": "# Plan: x\nStatus: accepted.\n\n1. build it\n",
    "impl.md": "# Impl: x\nStatus: accepted.\n\nbuilt\n",
    "pr.md": PR_MD,
    "review.md": (
        "# Review: x\nStatus: accepted.\n\n## Round 1\n\n"
        f"Reviewed: {SHA}. Verdict: pass.\n\n### Findings\n\n\n\n### What was not reviewed\n\nnothing\n"
    ),
}
ANSWERS = "\n## Answers\n\n### Câu 1\nAnswered by: A. Date: 2026-09-26. Via: product.\n\ngiữ\n"


class APrRunAgainClosesShipUntilAReview(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.repo = root / "work" / "proj"
        (self.repo / ".git").mkdir(parents=True)
        config = Config(
            workspaces=(str(self.repo),),
            working_dir=str(root / "work"),
            data_dir=str(root / "data"),
        )
        self.service = Service(config, Sessions(config))
        self.cwd = str(self.repo)
        self.unit = asyncio.run(
            self.service.answers.create_unit(self.cwd, "awaiting-ship", "words")
        )["unit"]
        self.dir = self.service.ws.unit_dir(self.cwd, self.unit)
        for name, text in ARTIFACTS.items():
            (self.dir / name).write_text(text, encoding="utf-8")
        self.store = self.service.ws.units_root(self.cwd)
        self.seen: list[dict] = []
        self.items: list[tuple] = []

    def run_step(
        self,
        stage: str,
        write: str
        | None = "# PR: x\nPR: https://github.com/o/r/pull/7. Status: accepted.\n\nthân mới\n",
        file: str = "pr.md",
        **kw,
    ):
        """`Steps.run_step`, with `Runner` a stand-in that writes `file` as `write` and
        ends `done` — or, `write` None, fails before writing anything."""
        seen, directory = self.seen, self.dir

        class StandIn:
            bus = Bus()

            def __init__(self, *a, **k):
                pass

            async def run(self, **k):
                seen.append(k)
                if write is None:
                    raise RunError("a stand-in runner")
                (directory / file).write_text(write, encoding="utf-8")
                extra = await k["end_fields"]() if k.get("end_fields") else {}
                yield ("done", {"outcome": "done", **extra})

        async def tree(*a, **k):
            return {"path": self.cwd, "branch": "feat/awaiting-ship", "base": None}

        async def no_sync(*a, **k):
            return {}

        # `pr` runs no session. The PR machine is the real one, with `gh`, the push and the head in
        # memory; `write` None is a `gh` that cannot be reached.
        from coscc.git import gitops
        from coscc.github import prmachine
        from tests.github.test_prmachine import FakeGh

        gh = FakeGh()
        if write is None:

            async def gh(argv, cwd, stdin=None):
                seen.append({"gh": argv})
                return 1, "", "error connecting to api.github.com"

        async def pushed(*a):
            return None

        async def head(*a):
            return SHA

        def machine():
            meta = self.service.ws.unit_meta()
            return prmachine.Machine(
                meta.history, self.service.ws.journal(), gh=gh, push=pushed, head=head
            )

        async def on_branch(*a, **k):
            return "feat/awaiting-ship"

        async def go():
            async for item in self.service.steps.run_step(self.cwd, self.unit, stage, **kw):
                self.items.append(item)

        with (
            mock.patch.object(self.service.integration, "pr_machine", machine),
            mock.patch.object(gitops, "current_branch", on_branch),
            mock.patch("coscc.runner.steps.Runner", StandIn),
            mock.patch.object(self.service.steps, "worktree", tree),
            mock.patch.object(self.service.steps, "sync_pr", no_sync),
            mock.patch.object(worktrees, "read_prepare", lambda *a: {"ok": True}),
        ):
            asyncio.run(go())

    def intent(self) -> str:
        return (self.dir / "intent.md").read_text(encoding="utf-8")

    def ask(self, coro):
        return asyncio.run(coro)

    def test_the_offers_are_the_loops_answer(self):
        offers = self.ask(self.service.steps.rerun_offers(self.cwd, self.unit))
        self.assertEqual([o["stage"] for o in offers["offers"]], ["intent", "spec", "plan", "pr"])
        self.assertEqual(
            next(o for o in offers["offers"] if o["stage"] == "pr")["later"], ["review", "ship"]
        )

    def test_without_rerun_no_runner_is_made_and_intent_is_untouched(self):
        # No session at all, rerun or not.
        (self.dir / "review.md").unlink()
        self.run_step("pr")
        self.assertEqual(self.seen, [])
        self.assertEqual(self.items[-1][1]["outcome"], "done")
        self.assertEqual(self.intent(), ARTIFACTS["intent.md"])

    def test_rerun_pr_then_next_is_review_and_ship_is_closed(self):
        open_before, said_before = self.ask(board_reader.gate(self.store, self.unit, "ship"))
        self.assertNotIn("stale", said_before)
        self.run_step("pr", rerun=True, note="Sửa tiêu đề: nêu R10.")
        self.assertEqual(self.seen, [])
        self.assertEqual((self.items[-1][0], self.items[-1][1]["outcome"]), ("done", "done"))
        # Exactly one block, appended: not one byte above it moved.
        text = self.intent()
        self.assertTrue(text.startswith(ARTIFACTS["intent.md"]))
        self.assertEqual(text.count("### Rerun"), 1)
        self.assertIn("Requested by: owner.", text)
        self.assertNotIn("Sửa tiêu đề", text)

        allowed, said = self.ask(board_reader.gate(self.store, self.unit, "ship"))
        self.assertFalse(allowed)
        self.assertIn("review.md is stale: pr was rerun on", said)
        # With no --repo the ship gate was closed before too, but only for want of git and gh.
        self.assertFalse(open_before)
        self.assertIn("no repository given", said_before)
        nxt = self.ask(board_reader.next_step(self.store, self.unit))
        self.assertTrue(nxt["action"].startswith("review.md is stale"), nxt["action"])
        # `pr` was written again, so it is no longer offered by the run button.
        self.assertNotIn("pr.md is stale", nxt["action"])
        # The card reads the stale review as the wait on CI a missing one is.
        row = next(
            u for u in self.ask(board_reader.read(self.store))["units"] if u["name"] == self.unit
        )
        self.assertEqual(
            (row["at"], row["why"], row["between_pr_and_ship"]), ("review", "stale", True)
        )
        # The fixture's `intent.md` leaves Câu 1 open, and *Needs you* is tried first.
        self.assertEqual(unit_state(row, None, None)["state"], "needs-you")
        got = unit_state({**row, "open": 0}, None, None)
        self.assertEqual(got["state"], "awaiting")

    def test_the_note_reaches_no_session_and_no_file(self):
        self.run_step("pr", rerun=True, note="NOTE-0054")
        self.assertEqual(self.seen, [])
        self.assertNotIn("NOTE-0054", self.intent())
        self.assertNotIn("NOTE-0054", (self.dir / "pr.md").read_text(encoding="utf-8"))

    def test_a_rerun_that_never_ran_leaves_the_stage_offered_again(self):
        self.run_step("pr", write=None, rerun=True)
        self.assertEqual(self.items[-1][1]["outcome"], "failed")
        self.assertEqual(self.intent().count("### Rerun"), 1)
        nxt = self.ask(board_reader.next_step(self.store, self.unit))
        self.assertEqual(nxt["stage"], "pr")
        self.assertTrue(nxt["action"].startswith("pr.md is stale"), nxt["action"])

    def test_refusals_reach_no_runner_and_write_nothing(self):
        cases = [
            ({"started_by": "autopilot", "rerun": True}, "pr", "never by the autopilot"),
            ({"rerun": True, "note": "x" * 4001}, "pr", "4001 characters, over the 4000"),
            ({"rerun": True}, "review", "review cannot be run again from the board"),
            ({"rerun": True}, "impl", "impl cannot be run again from the board"),
        ]
        for kw, stage, words in cases:
            with self.subTest(stage=stage, kw=list(kw)):
                with self.assertRaises(Invalid) as refused:
                    self.run_step(stage, **kw)
                self.assertIn(words, str(refused.exception))
        self.assertEqual(self.seen, [])
        self.assertEqual(self.intent(), ARTIFACTS["intent.md"])

    def test_a_refusal_before_spend_carries_its_code(self):
        for kw, stage, code in (
            ({"started_by": "autopilot", "rerun": True}, "pr", "rerun-by-person"),
            ({}, "nope", "no-stage"),
        ):
            with self.subTest(code=code):
                with self.assertRaises(Refused) as refused:
                    self.run_step(stage, **kw)
                self.assertEqual(refused.exception.reasons, (code,))

    def test_an_empty_note_is_not_refused(self):
        self.run_step("pr", rerun=True, note="   ")
        self.assertEqual(self.items[-1][1]["outcome"], "done")

    def test_pr_md_written_again_keeps_its_answers(self):
        # Was *pr.md that loses its answers is said to*: the app writes `pr.md` now, and
        # keeps its `## Answers` below what it writes, so none is lost to say.
        (self.dir / "pr.md").write_text(PR_MD + ANSWERS, encoding="utf-8")
        self.run_step("pr", rerun=True)
        text = (self.dir / "pr.md").read_text(encoding="utf-8")
        self.assertTrue(text.endswith(ANSWERS))
        self.assertNotEqual(text, PR_MD + ANSWERS)

    def test_pr_md_that_keeps_its_answers_says_nothing_lost(self):
        (self.dir / "pr.md").write_text(PR_MD + ANSWERS, encoding="utf-8")
        self.run_step("pr", rerun=True)
        self.assertNotIn("answers_lost", self.items[-1][1])


IMPL_DRAFT = "# Impl: x\nStatus: draft.\n\n## Open questions\n\n1. Chạy lệnh X rồi đưa kết quả?\n"


class ADraftImplThatLosesItsAnswersSaysSo(unittest.TestCase):
    """An `impl` step writes `impl.md` itself; a `done` that no longer ends with the `## Answers` it
    started with says `answers_lost`. The app writes nothing back."""

    setUp = APrRunAgainClosesShipUntilAReview.setUp
    run_step = APrRunAgainClosesShipUntilAReview.run_step

    def impl(self, before: str, after: str) -> dict:
        for name in ("pr.md", "review.md"):
            (self.dir / name).unlink()
        (self.dir / "intent.md").write_text(
            "# Intent: x\nType: feat. Status: accepted.\n", encoding="utf-8"
        )
        (self.dir / "impl.md").write_text(before, encoding="utf-8")
        self.run_step("impl", write=after, file="impl.md")
        self.assertEqual(self.items[-1][0], "done")
        return self.items[-1][1]

    def test_an_impl_that_keeps_its_answers_says_nothing(self):
        done = self.impl(
            IMPL_DRAFT + ANSWERS, IMPL_DRAFT.replace("draft", "accepted") + "\nbuilt\n" + ANSWERS
        )
        self.assertEqual(done.get("answers_kept"), True)
        self.assertNotIn("answers_lost", done)

    def test_an_impl_that_drops_its_answers_says_answers_lost(self):
        done = self.impl(IMPL_DRAFT + ANSWERS, IMPL_DRAFT + "\nrewritten\n")
        self.assertEqual(done.get("answers_kept"), False)
        self.assertTrue(done.get("answers_lost"))
        # Nothing wrote the section back.
        self.assertNotIn("## Answers", (self.dir / "impl.md").read_text(encoding="utf-8"))

    def test_an_impl_that_appends_its_own_answer_block_says_answers_lost(self):
        own = "\n### Câu 2\nAnswered by: Claude. Date: 2026-09-27. Via: product.\n\ntự trả lời\n"
        done = self.impl(IMPL_DRAFT + ANSWERS, IMPL_DRAFT + "2. Hai?\n" + ANSWERS + own)
        self.assertTrue(done.get("answers_lost"))

    def test_an_impl_md_without_answers_is_not_compared(self):
        done = self.impl(IMPL_DRAFT, IMPL_DRAFT + "\nbuilt\n")
        self.assertNotIn("answers_kept", done)
        self.assertNotIn("answers_lost", done)


if __name__ == "__main__":
    unittest.main()
