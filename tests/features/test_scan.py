"""The scan feature: what one scan reads, what it hands the session, what it keeps."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from coscc.bus import Bus
from coscc.data import Data
from coscc.agent import policy
from coscc.features import scan
from coscc.plugin import create_tables
from coscc.kernel import Ctx, Invalid, Submitted
from coscc.runlog.journal import Intervention, Journal
from coscc.units import backlog, submit

WS = "/ws"
PROBLEM = "Steps stop and a person runs them again by hand. " * 6


def found(n: int, detail: str = "it happened") -> list[Intervention]:
    return [
        Intervention(
            f"rerun:runs:{i}",
            "rerun",
            f"2026-10-02T{i // 60:02d}:{i % 60:02d}:00+00:00",
            "0001_a",
            "spec",
            detail,
        )
        for i in range(1, n + 1)
    ]


def proposal(sources: list[str], slug: str = "steps-stop", **over) -> dict:
    return {
        "type": "fix",
        "slug": slug,
        "title": "Steps stop",
        "problem": PROBLEM,
        "sources": sources,
        **over,
    }


class _Feature(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.data = Data(self.root / "data")
        self.journal = Journal(self.root / "work", self.data)
        self.found: list[Intervention] = []
        self.asked: list[tuple[str, int]] = []
        self.prompts: list[str] = []
        self.reply = Submitted({"proposals": []}, {"cost_usd": 0.3}, "s1")
        self.units: list[tuple[str, str]] = []
        self.hours = {"scan": 24}
        self.on = True

        def interventions(cwd: str, after: str, limit: int) -> list[Intervention]:
            self.asked.append((after, limit))
            return [i for i in self.found if i.at > after][:limit]

        async def session(cwd: str, kind: str, prompt: str) -> Submitted:
            self.assertEqual(kind, "scan")
            self.prompts.append(prompt)
            return self.reply

        async def create_unit(cwd: str, slug: str, brief: str) -> str:
            self.units.append((slug, brief))
            return f"0007_{slug}"

        def set_schedule(feature: str, cwd: str, hours: int) -> None:
            self.hours[feature] = hours

        self.ctx = Ctx(
            lambda: self.journal,
            lambda cwd: cwd,
            lambda _f, _w: self.on,
            Bus(),
            self.data,
            interventions=interventions,
            session=session,
            create_unit=create_unit,
            schedule=lambda f, _w: self.hours.get(f, 0),
            set_schedule=set_schedule,
        )
        create_tables(self.ctx, scan.TABLES)
        self.store = scan.Tables(self.ctx)


class NothingNewOpensNoSession(_Feature):
    async def test_a_scan_with_no_intervention_is_skipped_for_nothing(self):
        run = await scan.scan(self.ctx, WS, "schedule")
        self.assertEqual((run["outcome"], run["cost_usd"], run["taken"]), ("skipped", 0.0, 0))
        self.assertEqual(self.prompts, [])
        self.assertEqual(self.asked, [("", scan.LIMIT + 1)])

    async def test_an_off_workspace_is_refused(self):
        self.on = False
        with self.assertRaises(Invalid):
            await scan.scan(self.ctx, WS, "owner")


class ThePromptIsBounded(_Feature):
    async def test_at_most_25_and_the_cursor_moves_to_the_last_one_taken(self):
        self.found = found(30)
        self.reply = Submitted({"proposals": [proposal(["rerun:runs:1"])]}, {"cost_usd": 0.3}, "s1")
        run = await scan.scan(self.ctx, WS, "owner")
        self.assertEqual((run["outcome"], run["taken"]), ("done", scan.LIMIT))
        self.assertLessEqual(len(self.prompts[0]), scan.PROMPT_MAX)
        self.assertIn("rerun:runs:25 |", self.prompts[0])
        self.assertNotIn("rerun:runs:26 |", self.prompts[0])
        self.assertEqual(self.store.cursor(WS), (self.found[24].at, ["rerun:runs:25"]))
        await scan.scan(self.ctx, WS, "owner")
        self.assertEqual(self.asked[-1], ("2026-10-02T00:24:59+00:00", scan.LIMIT + 2))
        self.assertIn("rerun:runs:26 |", self.prompts[-1])
        self.assertNotIn("rerun:runs:25 |", self.prompts[-1])

    async def test_the_character_ceiling_leaves_the_rest_for_the_next_scan(self):
        long = "x" * 300
        self.found = found(30, long)
        made = [proposal(["rerun:runs:1"], slug=f"s{n}", title="t" * 100) for n in range(30)]
        self.store.record(WS, "owner", "done", taken=self.found[:1], proposals=made)
        self.store.claim(WS, 1, "dismissed", "r" * 300)
        prompt, taken, cut = scan.prompt_of(self.found, self.store.proposals(WS))
        self.assertLessEqual(len(prompt), scan.PROMPT_MAX)
        self.assertLess(len(taken), scan.LIMIT)
        self.assertGreater(cut, 0)
        self.assertLessEqual(len(scan.INSTRUCTIONS), 3_000)

    async def test_a_second_the_cut_split_is_read_on_by_the_next_scan(self):
        self.found = [i._replace(at="2026-10-02T01:00:00+00:00") for i in found(60)]
        for _ in range(3):
            await scan.scan(self.ctx, WS, "owner")
        read = [line.split(" |")[0] for p in self.prompts for line in p.splitlines()]
        held = [i.removeprefix("- ") for i in read if i.startswith("- rerun:runs:")]
        self.assertEqual(sorted(held), sorted(i.id for i in self.found))
        self.assertEqual(len(self.store.cursor(WS)[1]), 60)
        self.assertEqual((await scan.scan(self.ctx, WS, "owner"))["outcome"], "skipped")


class TheObjectIsChecked(_Feature):
    async def test_a_proposal_that_breaks_a_rule_is_dropped_and_the_rest_kept(self):
        self.found = found(3)
        bad = [
            proposal(["rerun:runs:1"], slug="Bad Slug"),
            proposal(["rerun:runs:9"], slug="unknown-source"),
            proposal(["rerun:runs:1"], slug="short", problem="too short"),
            proposal(["rerun:runs:1"], slug="no-type", type="feature"),
            proposal([], slug="no-source"),
        ]
        good = proposal(["rerun:runs:1", "rerun:runs:2"])
        self.reply = Submitted({"proposals": [*bad, good]}, {"cost_usd": 0.3}, "s1")
        run = await scan.scan(self.ctx, WS, "owner")
        self.assertEqual(len(run["rejected"]), len(bad))
        (kept,) = self.store.proposals(WS)
        self.assertEqual(
            (kept["slug"], kept["state"], kept["run"]), ("steps-stop", "pending", run["id"])
        )
        self.assertEqual([s["id"] for s in kept["sources"]], ["rerun:runs:1", "rerun:runs:2"])
        self.assertEqual(kept["sources"][0]["unit"], "0001_a")

    async def test_past_eight_a_proposal_is_dropped(self):
        self.found = found(1)
        many = [proposal(["rerun:runs:1"], slug=f"p{n}") for n in range(10)]
        self.reply = Submitted({"proposals": many}, {"cost_usd": 0.3}, "s1")
        run = await scan.scan(self.ctx, WS, "owner")
        self.assertEqual((len(self.store.proposals(WS)), len(run["rejected"])), (8, 2))

    async def test_no_object_fails_the_scan_its_cost_kept_and_the_cursor_still(self):
        self.found = found(3)
        self.reply = Submitted(None, {"cost_usd": 0.4}, "s1", "no-submission")
        run = await scan.scan(self.ctx, WS, "owner")
        self.assertEqual(
            (run["outcome"], run["cost_usd"], run["detail"]), ("failed", 0.4, "no-submission")
        )
        self.assertEqual(self.store.cursor(WS), ("", []))

    async def test_a_scan_over_a_dollar_turns_the_schedule_off_and_says_why(self):
        self.found = found(1)
        self.reply = Submitted({"proposals": []}, {"cost_usd": 1.2}, "s1")
        run = await scan.scan(self.ctx, WS, "schedule")
        self.assertTrue(run["stopped"])
        self.assertEqual(self.hours["scan"], 0)
        self.assertIn("$1.20", scan.note_of(self.ctx, WS, self.store.runs(WS)))

    async def test_a_second_press_while_one_runs_is_refused(self):
        self.found = found(1)
        gate = asyncio.Event()

        async def slow(cwd: str, kind: str, prompt: str) -> Submitted:
            await gate.wait()
            return Submitted({"proposals": []}, {}, "s1")

        ctx = Ctx(
            *[getattr(self.ctx, f) for f in ("journal", "workspace_key", "enabled", "bus", "data")],
            interventions=self.ctx.interventions,
            session=slow,
        )
        first = asyncio.create_task(scan.scan(ctx, WS, "owner"))
        while WS not in scan._scanning:
            await asyncio.sleep(0)
        with self.assertRaises(Invalid) as said:
            await scan.scan(ctx, WS, "owner")
        self.assertIn("already running", str(said.exception))
        gate.set()
        self.assertEqual((await first)["outcome"], "done")


class APersonDecides(_Feature):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.found = found(2)
        self.reply = Submitted({"proposals": [proposal(["rerun:runs:1"])]}, {"cost_usd": 0.3}, "s1")
        await scan.scan(self.ctx, WS, "owner")
        (self.p,) = self.store.proposals(WS)

    async def test_accept_makes_a_unit_with_the_brief_and_leaves_the_shortlist(self):
        self.journal.append(
            {
                "kind": "shortlist",
                "workspace": WS,
                "unit": "",
                "units": ["0002_b"],
                "reason": "r",
                "by": "owner",
            }
        )
        before = backlog.shortlist_of(self.journal.records(WS))
        done = await scan.accept(self.ctx, WS, self.p["id"], "renamed-slug")
        self.assertEqual(
            (done["state"], done["unit"], done["by"]), ("accepted", "0007_renamed-slug", "owner")
        )
        ((slug, brief),) = self.units
        self.assertEqual(slug, "renamed-slug")
        self.assertIn(PROBLEM.strip(), brief)
        self.assertIn("rerun:runs:1", brief)
        self.assertEqual(backlog.shortlist_of(self.journal.records(WS)), before)
        with self.assertRaises(Invalid):
            await scan.accept(self.ctx, WS, self.p["id"], "again")

    async def test_a_unit_that_cannot_be_made_leaves_it_pending(self):
        with self.assertRaises(Invalid):
            await scan.accept(self.ctx, WS, self.p["id"], "Not A Slug")

        async def refuse(cwd: str, slug: str, brief: str) -> str:
            raise Invalid("taken")

        ctx = Ctx(
            *[getattr(self.ctx, f) for f in ("journal", "workspace_key", "enabled", "bus", "data")],
            create_unit=refuse,
        )
        with self.assertRaises(Invalid):
            await scan.accept(ctx, WS, self.p["id"], "fine")
        self.assertEqual(self.store.proposal(WS, self.p["id"])["state"], "pending")

    async def test_an_off_workspace_decides_nothing(self):
        self.on = False
        with self.assertRaises(Invalid):
            await scan.accept(self.ctx, WS, self.p["id"], "fine")
        with self.assertRaises(Invalid):
            await scan.dismiss(self.ctx, WS, self.p["id"], "a reason")
        self.assertEqual(self.store.proposal(WS, self.p["id"])["state"], "pending")
        self.assertEqual(self.units, [])

    async def test_dismiss_needs_a_reason_and_the_next_scan_reads_it(self):
        for reason in ("", "   ", "r" * 501):
            with self.assertRaises(Invalid):
                await scan.dismiss(self.ctx, WS, self.p["id"], reason)
        done = await scan.dismiss(self.ctx, WS, self.p["id"], "already fixed in 0150")
        self.assertEqual(
            (done["state"], done["reason"], done["by"]),
            ("dismissed", "already fixed in 0150", "owner"),
        )
        self.found = found(3)
        await scan.scan(self.ctx, WS, "owner")
        self.assertIn("Steps stop: already fixed in 0150", self.prompts[-1])


class TheSchedule(_Feature):
    async def test_turning_it_on_sets_24_hours(self):
        self.hours["scan"] = 0
        scan.on_set(self.ctx, WS, "on")
        self.assertEqual(self.hours["scan"], 24)

    async def test_a_tick_scans_once_the_hours_passed_since_the_last(self):
        await scan.tick(self.ctx, WS, 24)
        self.assertEqual(len(self.store.runs(WS)), 1)
        await scan.tick(self.ctx, WS, 24)
        self.assertEqual(len(self.store.runs(WS)), 1)


class ItsSessionIsDeclaredHere(unittest.TestCase):
    """What the core once named for scan: its grant, its `submit` schema, its slug rules."""

    def setUp(self):
        policy.add_session(scan.NAME, scan.SESSION.grant, scan.SESSION.own_turns)
        submit.add_session(scan.NAME, scan.SESSION.schema, scan.SESSION.purpose)

    def test_the_grant_opens_nothing_and_keeps_its_two_turns(self):
        g = policy.grant_for(scan.NAME)
        self.assertFalse(g.opens_anything)
        self.assertTrue(g.submits)
        self.assertEqual((g.max_turns, g.max_budget_usd), (2, 0.68))
        self.assertIn("paid session", g.warning)
        self.assertEqual(policy.decide(g, "mcp__cos__submit", {}, "/tmp/ws"), "")
        for tool in ("Read", "Bash", "Write"):
            self.assertIn("not granted", policy.decide(g, tool, {}, "/tmp/ws"), tool)

    def test_the_collector_keeps_proposals_and_refuses_a_bare_one(self):
        good = {
            "proposals": [
                {"type": "fix", "slug": "a-b", "title": "t", "problem": "p", "sources": ["x"]}
            ]
        }
        for obj, fits in ((good, True), ({"proposals": [{"type": "fix"}]}, False)):
            collector = submit.Collector(scan.NAME)
            server = collector.server()
            said = asyncio.run(_submit(server, obj))
            self.assertEqual(not said.get("is_error"), fits)
            self.assertEqual(collector.object(), obj if fits else None)

    def test_the_rules_are_the_loops_own(self):
        from coscc import loop

        self.assertEqual(scan.SCAN_TYPES, tuple(loop.BRANCH_TYPES))
        self.assertEqual(scan.SLUG.pattern, loop.SLUG_RE.pattern)
        self.assertEqual(scan.SLUG_MAX, loop.SLUG_MAX)


async def _submit(server, obj):
    from tests.units.test_submit import submits

    return await submits({"mcp_servers": {"cos": server}}, **obj)


if __name__ == "__main__":
    unittest.main()
