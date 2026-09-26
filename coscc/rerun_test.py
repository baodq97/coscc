"""`0054` R10. A person runs `pr` again from the board, in-process, spending nothing.

The chain the spec's outcome names: the stages offered, a rerun of `pr` with a note, a `pr`
session that ends `done`, then `cos.mjs next` naming the review and the `ship` gate closed.
`cos.mjs` is the real one, asked through `coscc/board.py` as the app asks it; only the
worktree, the pull-request lookup, the sync onto GitHub and `Runner` itself stand in.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc import board as board_reader
from coscc import integrate, worktrees
from coscc import service as service_mod
from coscc.config import Config
from coscc.runner import RunError
from coscc.service import Invalid, Service
from coscc.sessions import Sessions

SHA = "a" * 40
PR_MD = "# PR: x\nPR: https://github.com/o/r/pull/7. Status: accepted.\n\nthân cũ\n"
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
        config = Config(workspaces=(str(self.repo),), working_dir=str(root / "work"), data_dir=str(root / "data"))
        self.service = Service(config, Sessions(config))
        self.cwd = str(self.repo)
        self.unit = asyncio.run(self.service.create_unit(self.cwd, "awaiting-ship", "words"))["unit"]
        self.dir = self.service._unit_dir(self.cwd, self.unit)
        for name, text in ARTIFACTS.items():
            (self.dir / name).write_text(text, encoding="utf-8")
        self.store = self.service._units_root(self.cwd)
        self.seen: list[dict] = []
        self.items: list[tuple] = []

    def run_step(self, stage: str, write: str | None = "# PR: x\nPR: https://github.com/o/r/pull/7. Status: accepted.\n\nthân mới\n", **kw):
        """`Service.run_step`, with `Runner` a stand-in that writes `pr.md` as `write` and
        ends `done` — or, `write` None, fails before writing anything."""
        seen, directory = self.seen, self.dir

        class StandIn:
            def __init__(self, *a, **k):
                pass

            async def run(self, **k):
                seen.append(k)
                if write is None:
                    raise RunError("a stand-in runner")
                (directory / "pr.md").write_text(write, encoding="utf-8")
                extra = await k["end_fields"]() if k.get("end_fields") else {}
                yield ("done", {"outcome": "done", **extra})

        async def tree(*a, **k):
            return {"path": self.cwd, "branch": "feat/awaiting-ship", "base": None}

        async def no_pr(*a, **k):
            return {"state": "none", "url": ""}

        async def no_sync(*a, **k):
            return {}

        async def go():
            async for item in self.service.run_step(self.cwd, self.unit, stage, **kw):
                self.items.append(item)

        with mock.patch.object(service_mod, "Runner", StandIn), \
                mock.patch.object(self.service, "_worktree", tree), \
                mock.patch.object(self.service, "_sync_pr", no_sync), \
                mock.patch.object(worktrees, "read_prepare", lambda *a: {"ok": True}), \
                mock.patch.object(integrate, "pr_for_branch", no_pr):
            asyncio.run(go())

    def intent(self) -> str:
        return (self.dir / "intent.md").read_text(encoding="utf-8")

    def ask(self, coro):
        return asyncio.run(coro)

    def test_the_offers_are_cos_mjs_answer(self):
        offers = self.ask(self.service.rerun_offers(self.cwd, self.unit))
        self.assertEqual([o["stage"] for o in offers["offers"]], ["intent", "spec", "plan", "pr"])
        self.assertEqual(next(o for o in offers["offers"] if o["stage"] == "pr")["later"], ["review", "ship"])

    def test_without_rerun_no_key_reaches_the_runner_and_intent_is_untouched(self):
        (self.dir / "review.md").unlink()
        self.run_step("pr")
        self.assertNotIn("rerun", self.seen[-1])
        self.assertNotIn("rerun_note", self.seen[-1])
        self.assertEqual(self.intent(), ARTIFACTS["intent.md"])

    def test_r10_rerun_pr_then_next_is_review_and_ship_is_closed(self):
        open_before, said_before = self.ask(board_reader.gate(self.store, self.unit, "ship"))
        self.assertNotIn("stale", said_before)
        self.run_step("pr", rerun=True, note="Sửa tiêu đề: nêu R10.")
        kw = self.seen[-1]
        self.assertEqual((kw["rerun"], kw["rerun_note"]), (True, "Sửa tiêu đề: nêu R10."))
        self.assertEqual(self.items[-1][0], "done")
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
        # Review F3. The card reads the stale review as the wait on CI a missing one is.
        row = next(u for u in self.ask(board_reader.read(self.store))["units"] if u["name"] == self.unit)
        self.assertEqual((row["at"], row["why"], row["between_pr_and_ship"]), ("review", "stale", True))
        # The fixture's `intent.md` leaves Câu 1 open, and *Needs you* is tried first.
        self.assertEqual(service_mod.unit_state(row, None, None)["state"], "needs-you")
        got = service_mod.unit_state({**row, "open": 0}, None, None)
        self.assertEqual((got["state"], got["ci"]), ("awaiting", {"read": False, "red": [], "at": ""}))

    def test_the_prompt_the_runner_builds_carries_the_note(self):
        from coscc.runner import compose_prompt

        self.run_step("pr", rerun=True, note="NOTE-0054")
        kw = self.seen[-1]
        prompt = compose_prompt(
            kw["cwd"], kw["directory"], kw["unit"], kw["stage"], kw["stages"], kw["artifact"],
            writes_own=True, rerun=kw["rerun"], rerun_note=kw["rerun_note"],
        )[0]
        self.assertIn("NOTE-0054", prompt)
        self.assertIn("# Why this stage runs again", prompt)

    def test_a_rerun_that_never_ran_leaves_the_stage_offered_again(self):
        with self.assertRaises(Invalid):
            self.run_step("pr", write=None, rerun=True)
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

    def test_an_empty_note_is_not_refused(self):
        self.run_step("pr", rerun=True, note="   ")
        self.assertEqual(self.seen[-1]["rerun_note"], "")

    def test_pr_md_that_loses_its_answers_is_said_to(self):
        (self.dir / "pr.md").write_text(PR_MD + ANSWERS, encoding="utf-8")
        self.run_step("pr", rerun=True)
        self.assertEqual(self.items[-1][1].get("answers_kept"), False)
        self.assertTrue(self.items[-1][1].get("answers_lost"))

    def test_pr_md_that_keeps_its_answers_says_nothing_lost(self):
        (self.dir / "pr.md").write_text(PR_MD + ANSWERS, encoding="utf-8")
        self.run_step("pr", write="# PR: x\nPR: https://github.com/o/r/pull/7. Status: accepted.\n\nmới\n" + ANSWERS, rerun=True)
        self.assertEqual(self.items[-1][1].get("answers_kept"), True)
        self.assertNotIn("answers_lost", self.items[-1][1])


if __name__ == "__main__":
    unittest.main()
