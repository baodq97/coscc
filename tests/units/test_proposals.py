"""The `proposals` table: the rules one proposal keeps, the cap a run gets, and the owner's press."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from coscc.store.db import Data
from coscc.units import Invalid, board, proposals

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
        keep, rejected = proposals.kept(
            [*bad, proposal(["rerun:runs:1", "rerun:runs:2"])], dict.fromkeys(ids, "rerun")
        )
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
        self.assertIn("lacks this trigger: a time of day, as every morning at 7.", item["problem"])
        self.assertTrue(proposals.SLUG.match(item["slug"]))

    def test_a_gap_ending_in_a_full_stop_does_not_double_it(self):
        gap = {
            "part": "trigger",
            "need": "a time of day.",
            "instead": "every 24 h, plus a manual run.",
        }
        problem = proposals.of_gap("w", gap, "r9")["problem"]
        self.assertNotIn("..", problem)
        self.assertIn("lacks this trigger: a time of day. ", problem)
        self.assertIn("plus a manual run.\n", problem)

    def test_a_long_need_keeps_a_slug_and_title_within_bounds(self):
        item = proposals.of_gap("w", {"part": "data", "need": "word " * 80, "instead": ""}, "r")
        self.assertLessEqual(len(item["slug"]), proposals.SLUG_MAX)
        self.assertTrue(proposals.SLUG.match(item["slug"]))
        self.assertLessEqual(len(item["title"]), proposals.TITLE_MAX)

    def test_a_gap_without_its_part_or_need_is_refused(self):
        for gap in ({"part": "tool", "need": " "}, {"need": "x"}):
            with self.assertRaises(Invalid):
                proposals.of_gap("w", gap, "r")


MEASURE = "Count rerun interventions on the spec stage over the 14 days after it ships."
KINDS = {"rerun:runs:1": "rerun", "rerun:runs:2": "rerun", "ci-red:runs:3": "ci-red"}


def measured(**over) -> dict:
    """A scan's proposal of one change, resting on two reruns and a red CI."""
    return proposal(
        ["rerun:runs:1", "rerun:runs:2", "ci-red:runs:3"],
        **{
            "change": {
                "kind": "skill",
                "path": "skills/spec.md",
                "text": "Name the file a step reads.",
            },
            "signal": {"kind": "rerun", "now": 2, "target": 0},
            "measure": MEASURE,
            "usd": "3.50",
            **over,
        },
    )


class AChangeKeepsItsRules(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tree = Path(tmp.name)
        (self.tree / "skills").mkdir()
        (self.tree / "skills" / "spec.md").write_text("x")

    def problems(self, p, kinds=KINDS):
        return proposals.problems_of(p, kinds, self.tree)

    def test_one_change_that_keeps_every_rule_is_kept(self):
        self.assertEqual(self.problems(measured()), [])
        new = {"kind": "check", "path": "skills/new.md", "text": "x"}
        self.assertEqual(self.problems(measured(change=new)), [])

    def test_each_rule_names_its_reason(self):
        change = measured()["change"]
        for over, said in (
            ({"change": {**change, "kind": "rewrite"}}, "change kind 'rewrite' is not one of"),
            ({"change": {**change, "path": "/etc/passwd"}}, "is not a path relative"),
            ({"change": {**change, "path": "skills/../../x.md"}}, "is not a path relative"),
            ({"change": {**change, "path": "nowhere/x.md"}}, "is no file of the tree"),
            ({"change": {**change, "path": "skills"}}, "is no file of the tree"),
            ({"change": {**change, "text": ""}}, "the change's text is not 1 to 500"),
            ({"change": {**change, "text": "x" * 501}}, "the change's text is not 1 to 500"),
            ({"change": [change, change]}, "it names no change"),
            ({"signal": {"kind": "refused", "now": 1, "target": 0}}, "is not the 0 refused"),
            ({"signal": {"kind": "rerun2", "now": 2, "target": 0}}, "signal kind 'rerun2'"),
            ({"signal": {"kind": "rerun", "now": 3, "target": 0}}, "is not the 2 rerun"),
            ({"signal": {"kind": "integrate", "now": 0, "target": 0}}, "is not the 0 integrate"),
            ({"signal": {"kind": "rerun", "now": 2, "target": 2}}, "signal target 2 is not 0 to 1"),
            ({"signal": {"kind": "rerun", "now": 2, "target": -1}}, "signal target -1"),
            ({"measure": "count reruns"}, "the measure is not 40 to 400"),
            ({"measure": "x" * 401}, "the measure is not 40 to 400"),
            ({"usd": "6.50"}, "estimated $6.50 is over the $5.00 ceiling"),
            ({"usd": "0"}, "estimated $0.00 is no cost"),
            ({"usd": "cheap"}, "estimate 'cheap' is no cost in dollars"),
        ):
            with self.subTest(over=over):
                got = self.problems(measured(**over))
                self.assertEqual(len(got), 1, got)
                self.assertIn(said, got[0])

    def test_one_field_alone_asks_for_the_other_three(self):
        got = self.problems(proposal(["rerun:runs:1"], usd="1"))
        self.assertEqual(
            got,
            [
                "it names no change",
                "it names no signal",
                "the measure is not 40 to 400 characters",
            ],
        )

    def test_a_signal_is_not_counted_without_the_runs_input_nor_a_path_without_its_tree(self):
        (said,) = proposals.problems_of(measured(), None, self.tree)
        self.assertIn("handed no interventions", said)
        (said,) = proposals.problems_of(measured(), KINDS)
        self.assertIn("the run read no tree", said)

    def test_a_dropped_one_is_rejected_with_its_reason_and_not_kept(self):
        keep, rejected = proposals.kept(
            [measured(usd="6.50"), measured(slug="ok")], KINDS, self.tree
        )
        self.assertEqual([p["slug"] for p in keep], ["ok"])
        self.assertEqual(rejected, ["steps-stop: estimated $6.50 is over the $5.00 ceiling"])

    def test_a_proposal_without_a_change_keeps_todays_rules(self):
        self.assertEqual(self.problems(proposal(["rerun:runs:1"])), [])
        gap = {"part": "tool", "need": "a time of day", "instead": "every 24 h"}
        made = [
            proposals.of_gap("It tells the person which units got stuck. " * 5, gap, "r9"),
            *proposals.of_verdict(
                "0141_a", [{"criterion": "O1", "met": "no", "evidence": "a.py:1-2 " * 30}]
            ),
        ]
        for item in made:
            self.assertEqual(proposals.problems_of(item, None, None), [], item)


class AChangeIsKeptWithItsProposal(_Table):
    def setUp(self):
        super().setUp()
        (self.measured,) = proposals.add(
            self.data,
            WS,
            "scan",
            "",
            [measured(slug="name-the-file")],
            run="r2",
            sources={
                k: proposals.Source(id=k, kind=v, unit="0001_a", at="t1") for k, v in KINDS.items()
            },
        )

    def test_listed_gives_the_four_and_a_proposal_without_them_reads_empty(self):
        got, old = proposals.listed(self.data, WS)
        self.assertEqual(got["change"], measured()["change"])
        self.assertEqual(got["signal"], {"kind": "rerun", "now": 2, "target": 0})
        self.assertEqual((got["measure"], got["usd"]), (MEASURE, 3.5))
        self.assertEqual(
            (old["change"], old["signal"], old["measure"], old["usd"]), (None, None, "", None)
        )
        self.assertEqual(proposals.one(self.data, WS, self.measured)["usd"], 3.5)

    async def test_the_brief_holds_the_four_after_the_problem_and_before_the_sources(self):
        await proposals.accept(self.data, WS, self.measured, "name-the-file", self.create)
        ((_, brief),) = self.units
        order = [
            brief.index(said)
            for said in (
                PROBLEM.strip(),
                "Change (skill, `skills/spec.md`): Name the file a step reads.",
                "Signal: rerun: 2 → 0",
                f"Measure: {MEASURE}",
                "Estimated cost: $3.50",
                "Proposed by scan, from:",
            )
        ]
        self.assertEqual(order, sorted(order))
        await proposals.accept(self.data, WS, self.pid, "steps-stop", self.create)
        self.assertNotIn("Change (", self.units[1][1])

    def test_the_next_run_reads_the_file_a_change_was_proposed_on(self):
        said = proposals.lists_of(proposals.listed(self.data, WS))
        self.assertIn("- [fix] Steps stop (changes skills/spec.md)", said)
        self.assertIn("- [fix] Steps stop\n", said)


def screen(criterion="S3", path="ui/src/Board.tsx", lines="12-14", **over) -> dict:
    return {
        "id": "F1",
        "criterion": criterion,
        "path": path,
        "lines": lines,
        "text": "The card gutter is 6px where the standard says 8px.",
        "why": "screen-untouched",
        **over,
    }


class AFindingThatDidNotBlockBecomesOneProposal(_Table):
    UNIT = "0173_the-screens-rule"

    def made(self):
        return proposals.listed(self.data, WS)

    def test_one_fix_per_rule_and_file_carrying_where_the_sentence_and_the_unit(self):
        found = [screen(), screen("S5", lines="3"), screen(path="ui/src/Card.tsx")]
        fresh, refused = proposals.of_screens(self.UNIT, found, [])
        self.assertEqual(refused, [])
        self.assertEqual(
            [(p["type"], p["slug"]) for p in fresh],
            [
                ("fix", "s3-ui-src-board-tsx"),
                ("fix", "s5-ui-src-board-tsx"),
                ("fix", "s3-ui-src-card-tsx"),
            ],
        )
        first = fresh[0]
        self.assertIn("S3 ui/src/Board.tsx:12-14", first["title"])
        for word in (
            "S3",
            "ui/src/Board.tsx:12-14",
            "The card gutter is 6px",
            self.UNIT,
            "not one this unit's patch changes",
        ):
            self.assertIn(word, first["problem"] + first["sources"][0])
        self.assertEqual(proposals.problems_of(first, None), [])

    def test_added_it_waits_pending_for_a_person_under_the_unit(self):
        fresh, _ = proposals.of_screens(self.UNIT, [screen()], [])
        proposals.add(self.data, WS, "ship", self.UNIT, fresh)
        new = self.made()[0]
        self.assertEqual((new["state"], new["agent"], new["unit"]), ("pending", "ship", self.UNIT))
        self.assertEqual(new["by"], "")

    def test_a_second_call_gives_none_whether_the_first_is_pending_or_accepted(self):
        found = [screen(), screen("S5")]
        fresh, _ = proposals.of_screens(self.UNIT, found, [])
        ids = proposals.add(self.data, WS, "ship", self.UNIT, fresh)
        again, refused = proposals.of_screens("0180_other", found, self.made())
        self.assertEqual((again, refused), ([], []))
        # the other unit's later round names the same pair again, once accepted too
        run = asyncio.run
        run(proposals.accept(self.data, WS, ids[0], "s3-board", self.create))
        again, _ = proposals.of_screens("0180_other", found, self.made())
        self.assertEqual(again, [])
        self.assertEqual(len([p for p in self.made() if p["agent"] == "ship"]), 2)

    def test_a_dismissed_one_does_not_hold_the_pair(self):
        fresh, _ = proposals.of_screens(self.UNIT, [screen()], [])
        (pid,) = proposals.add(self.data, WS, "ship", self.UNIT, fresh)
        asyncio.run(proposals.dismiss(self.data, WS, pid, "not worth a unit"))
        again, _ = proposals.of_screens("0180_other", [screen()], self.made())
        self.assertEqual(len(again), 1)

    def test_one_pair_twice_in_a_batch_is_one(self):
        fresh, _ = proposals.of_screens(self.UNIT, [screen(), screen(lines="40")], [])
        self.assertEqual(len(fresh), 1)

    def test_one_that_breaks_a_rule_comes_back_with_its_reason_and_is_not_proposed(self):
        short = screen(criterion="", path="")
        fresh, refused = proposals.of_screens(self.UNIT, [short, screen()], [])
        self.assertEqual(len(fresh), 1)
        (said,) = refused
        self.assertIn("was not proposed", said)
        self.assertIn("is not a slug", said)

    def test_a_reason_the_app_does_not_know_is_said_as_given(self):
        fresh, _ = proposals.of_screens(self.UNIT, [screen(why="screen-new")], [])
        self.assertIn("because screen-new.", fresh[0]["problem"])


class TheGateHandsOnWhatItLetThrough(unittest.TestCase):
    def test_passed_is_read_as_text_and_absent_is_none(self):
        got = {
            "id": "F1",
            "criterion": "S3",
            "path": "a.tsx",
            "lines": None,
            "text": "t",
            "why": "w",
        }
        (read,) = board._passed({"passed": [got, "junk", 4]})
        self.assertEqual(read, {**got, "lines": ""})
        for bad in ({}, {"passed": None}, {"passed": "S3"}, {"passed": {}}):
            self.assertEqual(board._passed(bad), [], bad)
        self.assertEqual(board.Gate(True, "open", passed=[read]).passed, [read])
        self.assertEqual(board.Gate(True, "open").passed, [])


if __name__ == "__main__":
    unittest.main()
