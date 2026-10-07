"""The `proposals` table: the rules one proposal keeps, the cap a run gets, and the owner's press."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coscc.store.db import Data
from coscc.units import Invalid, proposals

WS = "/ws"
PROBLEM = "Steps stop and a person runs them again by hand. " * 6


def proposal(sources: list[str], slug: str = "steps-stop", **over) -> dict:
    return {
        "type": "fix",
        "slug": slug,
        "title": "Steps stop",
        "problem": PROBLEM,
        "sources": sources,
        **over,
    }


class _Table(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Data(Path(tmp.name))
        source = proposals.Source(id="rerun:runs:1", kind="rerun", unit="0001_a", at="t1")
        (self.pid,) = proposals.add(
            self.data,
            WS,
            "scan",
            "",
            [proposal(["rerun:runs:1", "x.py:1-2"])],
            run="r1",
            sources={"rerun:runs:1": source},
        )
        self.units: list[tuple[str, str]] = []

    async def create(self, slug: str, brief: str) -> str:
        self.units.append((slug, brief))
        return f"0007_{slug}"


class AProposalKeepsItsRules(unittest.TestCase):
    def test_a_proposal_that_breaks_a_rule_is_dropped_and_the_rest_kept(self):
        ids = {"rerun:runs:1", "rerun:runs:2"}
        bad = [
            proposal(["rerun:runs:1"], slug="Bad Slug"),
            proposal(["rerun:runs:9"], slug="unknown-source"),
            proposal(["rerun:runs:1"], slug="short", problem="too short"),
            proposal(["rerun:runs:1"], slug="no-type", type="feature"),
            proposal([], slug="no-source"),
        ]
        keep, rejected = proposals.kept([*bad, proposal(["rerun:runs:1", "rerun:runs:2"])], ids)
        self.assertEqual([p["slug"] for p in keep], ["steps-stop"])
        self.assertEqual(len(rejected), len(bad))
        self.assertIn("rerun:runs:9 is not in this run's input", rejected[1])

    def test_past_eight_a_proposal_is_dropped(self):
        many = [proposal(["s"], slug=f"p{n}") for n in range(10)]
        keep, rejected = proposals.kept(many, None)
        self.assertEqual((len(keep), len(rejected)), (8, 2))

    def test_with_no_input_list_any_source_is_taken(self):
        self.assertEqual(proposals.problems_of(proposal(["coscc/x.py:1-9"]), None), [])

    def test_the_rules_are_the_loops_own(self):
        from coscc import loop

        self.assertEqual(proposals.TYPES, tuple(loop.BRANCH_TYPES))
        self.assertEqual(proposals.SLUG.pattern, loop.SLUG_RE.pattern)
        self.assertEqual(proposals.SLUG_MAX, loop.SLUG_MAX)


class AddedProposalsWaitPending(_Table):
    def test_each_is_pending_under_its_agent_with_its_sources(self):
        (p,) = proposals.listed(self.data, WS)
        self.assertEqual(
            (p["agent"], p["state"], p["run"], p["unit"]), ("scan", "pending", "r1", "")
        )
        self.assertEqual(p["sources"][0]["unit"], "0001_a")
        self.assertEqual(p["sources"][1], {"id": "x.py:1-2", "kind": "", "unit": "", "at": ""})
        self.assertEqual(proposals.listed(self.data, WS, "other"), [])


class AnOwnerDecides(_Table):
    async def test_accept_makes_a_unit_with_the_brief(self):
        done = await proposals.accept(self.data, WS, self.pid, "renamed-slug", self.create)
        self.assertEqual(
            (done["state"], done["made"], done["by"]), ("accepted", "0007_renamed-slug", "owner")
        )
        ((slug, brief),) = self.units
        self.assertEqual(slug, "renamed-slug")
        self.assertIn(PROBLEM.strip(), brief)
        self.assertIn("rerun:runs:1", brief)
        with self.assertRaises(Invalid):
            await proposals.accept(self.data, WS, self.pid, "again", self.create)

    async def test_a_unit_that_cannot_be_made_leaves_it_pending(self):
        with self.assertRaises(Invalid):
            await proposals.accept(self.data, WS, self.pid, "Not A Slug", self.create)

        async def refuse(slug: str, brief: str) -> str:
            raise Invalid("taken")

        with self.assertRaises(Invalid):
            await proposals.accept(self.data, WS, self.pid, "fine", refuse)
        self.assertEqual(proposals.one(self.data, WS, self.pid)["state"], "pending")

    async def test_dismiss_needs_a_reason_and_the_next_run_reads_it(self):
        for reason in ("", "   ", "r" * 501):
            with self.assertRaises(Invalid):
                await proposals.dismiss(self.data, WS, self.pid, reason)
        done = await proposals.dismiss(self.data, WS, self.pid, "already fixed in 0150")
        self.assertEqual(
            (done["state"], done["reason"], done["by"]),
            ("dismissed", "already fixed in 0150", "owner"),
        )
        said = proposals.lists_of(proposals.listed(self.data, WS))
        self.assertIn("Steps stop: already fixed in 0150", said)

    async def test_another_workspaces_proposal_is_not_found(self):
        with self.assertRaises(Invalid):
            await proposals.dismiss(self.data, "/other", self.pid, "why")


if __name__ == "__main__":
    unittest.main()


class AProposalKnowsTheUnitItMade(_Table):
    async def test_the_accepted_proposal_is_the_units_origin(self):
        self.assertIsNone(proposals.origin(self.data, WS, "0007_steps-stop"))
        await proposals.accept(self.data, WS, self.pid, "steps-stop", self.create)
        got = proposals.origin(self.data, WS, "0007_steps-stop")
        self.assertEqual((got["id"], got["agent"], got["run"]), (self.pid, "scan", "r1"))
        self.assertIsNone(proposals.origin(self.data, "/other", "0007_steps-stop"))

    async def test_a_unit_that_could_not_be_made_has_no_origin(self):
        async def fail(slug: str, brief: str) -> str:
            raise Invalid("no")

        with self.assertRaises(Invalid):
            await proposals.accept(self.data, WS, self.pid, "steps-stop", fail)
        self.assertIsNone(proposals.origin(self.data, WS, ""))


class AGapBecomesAProposal(unittest.TestCase):
    def test_a_gap_is_a_feat_resting_on_its_run(self):
        gap = {
            "part": "trigger",
            "need": "a time of day, as every morning at 7",
            "instead": "every 24 h",
        }
        item = proposals.of_gap("It tells the person which units got stuck.", gap, "r9")
        self.assertEqual((item["type"], item["sources"]), ("feat", ["r9"]))
        self.assertEqual(item["slug"], "trigger-a-time-of-day-as-every-morning-at-7")
        self.assertIn("every 24 h", item["problem"])
        # The page tells a gap proposed before by these words (`NewAgent.tsx` `Gaps`).
        self.assertIn("lacks a trigger: a time of day, as every morning at 7.", item["problem"])
        self.assertTrue(proposals.SLUG.match(item["slug"]))

    def test_a_long_need_keeps_a_slug_and_title_within_bounds(self):
        item = proposals.of_gap("w", {"part": "data", "need": "word " * 80, "instead": ""}, "r")
        self.assertLessEqual(len(item["slug"]), proposals.SLUG_MAX)
        self.assertTrue(proposals.SLUG.match(item["slug"]))
        self.assertLessEqual(len(item["title"]), proposals.TITLE_MAX)

    def test_a_gap_without_its_part_or_need_is_refused(self):
        for gap in ({"part": "tool", "need": " "}, {"need": "x"}):
            with self.assertRaises(Invalid):
                proposals.of_gap("w", gap, "r")
