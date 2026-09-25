"""`0043`. The autopilot's decisions, with no session, no `gh` and no run log on disk."""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from coscc import autopilot as ap
from coscc.policy import GRANTS, NOVEL_CEILINGS

COS_MJS = Path(__file__).resolve().parent.parent / ".claude" / "scripts" / "cos.mjs"
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc).astimezone()


def at(delta: timedelta = timedelta()) -> str:
    return (NOW + delta).astimezone(timezone.utc).isoformat()


def unit(questions=()):
    return {"name": "0010_a", "questions": list(questions)}


def nxt(stage="", action="", waiting=(), hold=None):
    return {"stage": stage, "action": action, "waiting": list(waiting), "hold": hold}


class TheWordsAreCosMjs(unittest.TestCase):
    """Plan Risk 3: `next` hands out no `why`, so its words are read. Red when they change."""

    def test_each_phrase_is_still_in_cos_mjs(self):
        text = COS_MJS.read_text(encoding="utf-8")
        self.assertIn("`" + ap.CI_PENDING + "${pr.number}", text)
        self.assertIn("`" + ap.NEEDS_A_PERSON + " — review used", text)
        self.assertIn(f"action: '{ap.FINISHED}'", text)
        self.assertIn("`" + ap.CLOSED + "${s.name} rejected`", text)


class Stops(unittest.TestCase):
    def test_nothing_to_stop_on_a_stage_to_run(self):
        self.assertIsNone(ap.stop_for(unit(), nxt("spec", "write-spec"), None, False))

    def test_finished_rejected_and_held_are_not_stops(self):
        self.assertIsNone(ap.stop_for(unit(), nxt("", "finished"), None, False))
        self.assertIsNone(ap.stop_for(unit(), nxt("", "closed — spec rejected"), None, False))
        self.assertIsNone(ap.stop_for(unit(), nxt("", "paused — x", hold={"state": "paused"}), None, False))

    def test_a_open_question_lists_each(self):
        qs = [
            {"artifact": "spec.md", "n": 2, "answered": False, "counted": True},
            {"artifact": "spec.md", "n": 1, "answered": True, "counted": True},
            {"artifact": "intent.md", "n": 9, "answered": False, "counted": False},
        ]
        got = ap.stop_for(unit(qs), nxt("plan", "write-plan"), None, False)
        self.assertEqual(got["kind"], "a")
        self.assertIn("spec.md question 2", got["reason"])
        self.assertNotIn("intent.md", got["reason"])

    def test_b_waiting_and_rounds_used(self):
        self.assertEqual(ap.stop_for(unit(), nxt("", "answer F2", waiting=["F2"]), None, False)["kind"], "b")
        said = "needs a person — review used 3 of 3 rounds and findings are still open"
        got = ap.stop_for(unit(), nxt("", said), None, False)
        self.assertEqual((got["kind"], got["reason"]), ("b", said))

    def test_c_ship_only_when_allowed(self):
        self.assertEqual(ap.stop_for(unit(), nxt("ship", "write-ship"), None, False)["kind"], "c")
        self.assertIsNone(ap.stop_for(unit(), nxt("ship", "write-ship"), None, True))

    def test_d_gebo_needs_a_person(self):
        last = {"kind": "integration", "outcome": "needs-person", "needs_person": ["both sides edit x.py"]}
        got = ap.stop_for(unit(), nxt("review", "x"), last, True)
        self.assertEqual(got["kind"], "d")
        self.assertIn("both sides edit x.py", got["reason"])

    def test_e_a_step_that_did_not_end_done(self):
        for outcome in ("failed", "exhausted", "stopped"):
            got = ap.stop_for(unit(), nxt("impl", "x"), {"kind": "end", "stage": "impl", "outcome": outcome}, True)
            self.assertEqual(got["kind"], "e", outcome)
        self.assertIsNone(ap.stop_for(unit(), nxt("pr", "x"), {"kind": "end", "stage": "impl", "outcome": "done"}, True))

    def test_e_an_integration_that_failed_or_that_the_autopilot_had_refused(self):
        failed = {"kind": "integration", "outcome": "failed", "detail": "gh down"}
        self.assertEqual(ap.stop_for(unit(), nxt("review", "x"), failed, True)["kind"], "e")
        mine = {"kind": "integration", "outcome": "refused", "started_by": "autopilot"}
        self.assertEqual(ap.stop_for(unit(), nxt("review", "x"), mine, True)["kind"], "e")
        theirs = {"kind": "integration", "outcome": "refused", "started_by": "person"}
        self.assertIsNone(ap.stop_for(unit(), nxt("review", "x"), theirs, True))

    def test_e_red_again_after_the_autopilots_own_integration(self):
        red = {"state": "red-after-integration"}
        mine = {"kind": "integration", "outcome": "pushed", "started_by": "autopilot"}
        self.assertEqual(ap.red_again(red, mine)["kind"], "e")
        self.assertIsNone(ap.red_again(red, {**mine, "started_by": "person"}))
        self.assertIsNone(ap.red_again(red, {"kind": "integration", "outcome": "pushed"}))
        self.assertIsNone(ap.red_again({"state": "behind"}, mine))
        self.assertIsNone(ap.red_again(None, None))

    def test_f_no_stage_and_not_ci(self):
        got = ap.stop_for(unit(), nxt("", "finish and accept plan.md"), None, True)
        self.assertEqual((got["kind"], got["reason"]), ("f", "finish and accept plan.md"))

    def test_ci_pending_is_not_a_stop(self):
        said = "CI has not finished on #7: test — wait, then ask again"
        self.assertTrue(ap.is_ci_pending(said))
        self.assertIsNone(ap.stop_for(unit(), nxt("", said), None, True))
        self.assertFalse(ap.is_ci_pending("CI is red on #7"))


class TheDaysMoney(unittest.TestCase):
    def test_every_end_of_today_whoever_started_it(self):
        rows = [
            {"kind": "end", "at": at(), "cost_usd": 1.5, "workspace": "w1"},
            {"kind": "end", "at": at(-timedelta(minutes=5)), "cost_usd": 2.0, "workspace": "w2"},
            {"kind": "start", "at": at(), "workspace": "w1"},
        ]
        self.assertEqual(ap.spent_today(rows, NOW), {"known": 3.5, "estimated": 0.0, "estimated_count": 0})

    def test_an_end_without_cost_is_not_the_cap_reached(self):
        rows = [{"kind": "end", "at": at(), "cost_usd": 1.0}, {"kind": "end", "at": at(), "stage": "spec"}]
        got = ap.spent_today(rows, NOW)
        self.assertEqual((got["known"], got["estimated"]), (1.0, ap.estimate("spec")))
        self.assertTrue(ap.cap_allows(got["known"] + got["estimated"], 0.0, 0.0, 50.0))

    def test_a_new_day_by_the_machines_clock_starts_again(self):
        local_midnight = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday = (local_midnight - timedelta(seconds=1)).astimezone(timezone.utc).isoformat()
        rows = [{"kind": "end", "at": yesterday, "cost_usd": 49.0}, {"kind": "end", "at": yesterday}]
        self.assertEqual(ap.spent_today(rows, NOW), {"known": 0.0, "estimated": 0.0, "estimated_count": 0})

    def test_the_cap_fits_or_does_not(self):
        self.assertTrue(ap.cap_allows(40.0, 2.0, 8.0, 50.0))
        self.assertFalse(ap.cap_allows(40.0, 2.0, 8.01, 50.0))

    def test_a_day_like_2026_09_25_fits_or_is_capped(self):
        rows = [{"kind": "end", "at": at(), "stage": "impl", "outcome": "failed"}] + [
            {"kind": "end", "at": at(), "stage": "integrate", "outcome": "done"} for _ in range(4)
        ]
        estimated = ap.spent_on(rows, ap.today(NOW))["estimated"]
        self.assertEqual(estimated, 48.0)
        spec = {"unit": "0010_a", "stage": "spec", "files": None, "need": 4.0}
        got = ap.pick([spec], [], 4, 80.0 - (20.0 + estimated) - 0.0)
        self.assertEqual((got["chosen"], got["capped"]), ([spec], []))
        got = ap.pick([spec], [], 4, 80.0 - (30.0 + estimated) - 0.0)
        self.assertEqual((got["chosen"], got["capped"]), ([], [spec]))

    def test_a_reservation_is_the_largest_budget_a_label_can_give(self):
        self.assertEqual(ap.reservation("impl"), 16.0)
        self.assertEqual(ap.reservation("review"), 4.0)
        self.assertEqual(ap.reservation("integrate"), 8.0)
        self.assertEqual(ap.reservation("no-such-stage"), 0.0)

    def test_an_end_without_cost_counts_its_stages_ceiling(self):
        rows = [
            {"kind": "end", "at": at(), "stage": "impl", "outcome": "failed"},
            {"kind": "end", "at": at(), "stage": "review", "outcome": "done"},
            {"kind": "end", "at": at(), "stage": "integrate", "outcome": "done"},
        ]
        got = ap.spent_on(rows, ap.today(NOW))
        self.assertEqual((got["known"], got["estimated"], got["estimated_count"]), (0.0, 16.0 + 4.0 + 8.0, 3))

    def test_a_stage_without_a_ceiling_counts_the_tables_largest(self):
        largest = max(
            [g.max_budget_usd for g in GRANTS.values()] + [b for _, b in NOVEL_CEILINGS.values()]
        )
        self.assertGreater(largest, 0)
        self.assertEqual(ap.estimate("intent"), largest)
        self.assertEqual(ap.estimate("idea"), largest)
        self.assertEqual(largest, 16.0)

    def test_an_integration_record_is_never_added(self):
        rows = [
            {"kind": "end", "at": at(), "stage": "integrate", "cost_usd": 2.5},
            {"kind": "integration", "at": at(), "mode": "agent"},
            {"kind": "integration", "at": at(), "mode": "mechanical"},
        ]
        got = ap.spent_on(rows, ap.today(NOW))
        self.assertEqual((got["known"], got["estimated"], got["estimated_count"]), (2.5, 0.0, 0))

    def test_an_open_start_counts_for_24_hours(self):
        rows = [
            {"kind": "start", "workspace": "w", "unit": "0010_a", "stage": "impl", "at": at(-timedelta(hours=1))},
            {"kind": "start", "workspace": "w", "unit": "0011_b", "stage": "review", "at": at(-timedelta(hours=25))},
            {"kind": "start", "workspace": "w", "unit": "0012_c", "stage": "spec", "at": at(-timedelta(hours=2))},
            {"kind": "end", "workspace": "w", "unit": "0012_c", "stage": "spec", "at": at(), "cost_usd": 1},
        ]
        self.assertEqual(set(ap.open_starts(rows, NOW)), {("w", "0010_a")})
        self.assertEqual(ap.reserved(rows, NOW), 16.0)
        # A step this process holds that has not written its `start` yet counts too, once.
        self.assertEqual(ap.reserved(rows, NOW, [("w", "0010_a", "impl"), ("w", "0013_d", "pr")]), 19.0)


class Scheduling(unittest.TestCase):
    def c(self, unit, stage, files=None, need=1.0):
        return {"unit": unit, "stage": stage, "files": files, "need": need}

    def test_lowest_unit_first_up_to_max_parallel(self):
        got = ap.pick([self.c("0012_c", "spec"), self.c("0010_a", "spec"), self.c("0011_b", "pr")], [], 2, 100.0)
        self.assertEqual([c["unit"] for c in got["chosen"]], ["0010_a", "0011_b"])

    def test_running_steps_count_person_ones_included(self):
        got = ap.pick([self.c("0010_a", "spec")], [{"unit": "0001_x", "stage": "review", "files": None}], 1, 100.0)
        self.assertEqual(got["chosen"], [])

    def test_a_unit_already_running_is_skipped(self):
        got = ap.pick([self.c("0010_a", "review")], [{"unit": "0010_a", "stage": "pr", "files": None}], 4, 100.0)
        self.assertEqual(got["chosen"], [])

    def test_code_stages_with_overlapping_files_run_one_after_the_other(self):
        a = self.c("0010_a", "impl", {"coscc/x.py", "coscc/y.py"})
        b = self.c("0011_b", "impl", {"coscc/y.py"})
        c = self.c("0012_c", "integrate", {"coscc/z.py"})
        got = ap.pick([a, b, c], [], 4, 100.0)
        self.assertEqual([x["unit"] for x in got["chosen"]], ["0010_a", "0012_c"])

    def test_unknown_files_overlap_with_everything(self):
        got = ap.pick([self.c("0010_a", "impl", None), self.c("0011_b", "impl", {"a/b.py"})], [], 4, 100.0)
        self.assertEqual([x["unit"] for x in got["chosen"]], ["0010_a"])
        got = ap.pick([self.c("0011_b", "impl", {"a/b.py"})], [{"unit": "0001_x", "stage": "integrate", "files": None}], 4, 100.0)
        self.assertEqual(got["chosen"], [])

    def test_prose_stages_ignore_files(self):
        got = ap.pick([self.c("0010_a", "review"), self.c("0011_b", "pr")], [{"unit": "0001_x", "stage": "impl", "files": None}], 4, 100.0)
        self.assertEqual(len(got["chosen"]), 2)

    def test_one_ship_in_the_workspace(self):
        got = ap.pick([self.c("0010_a", "ship"), self.c("0011_b", "ship")], [], 4, 100.0)
        self.assertEqual([x["unit"] for x in got["chosen"]], ["0010_a"])
        got = ap.pick([self.c("0011_b", "ship")], [{"unit": "0010_a", "stage": "ship", "files": None}], 4, 100.0)
        self.assertEqual(got["chosen"], [])

    def test_the_cap_holds_back_what_does_not_fit(self):
        got = ap.pick([self.c("0010_a", "impl", {"a"}, 16.0), self.c("0011_b", "review", None, 2.0)], [], 4, 3.0)
        self.assertEqual([x["unit"] for x in got["chosen"]], ["0011_b"])
        self.assertEqual([x["unit"] for x in got["capped"]], ["0010_a"])
        got = ap.pick([self.c("0011_b", "review", None, 2.0)], [], 4, -1.0)
        self.assertEqual((got["chosen"], len(got["capped"])), ([], 1))

    def test_files_of_keeps_paths_only(self):
        plan = "# Plan\n\n## Files that change\n\n- `coscc/a.py` (new): phần mới\n- `README.md`\n\n## Order\n"
        self.assertEqual(ap.files_of(plan), {"coscc/a.py", "README.md"})
        self.assertIsNone(ap.files_of("# Plan\n\n## Order\n"))
        self.assertIsNone(ap.files_of(None))


class Measuring(unittest.TestCase):
    def rows(self):
        w, u = "w", "0010_a"

        def r(kind, stage, delta, **kw):
            return {"kind": kind, "workspace": w, "unit": u, "stage": stage, "at": at(timedelta(minutes=delta)), **kw}

        return [
            r("start", "intent", 0),  # before the count, and written before `0043`
            r("end", "intent", 1, outcome="done"),
            r("start", "spec", 2, started_by="autopilot"),
            r("end", "spec", 3, outcome="done"),
            r("autopilot-stop", "", 4, stop="a"),
            r("start", "plan", 5, started_by="person"),  # at a stop
            r("end", "plan", 6, outcome="done"),
            r("autopilot-stop", "", 7, stop=""),
            r("start", "impl", 8),  # an old record: person, and outside every stop
            r("end", "impl", 9, outcome="failed"),
            r("start", "impl", 10, started_by="person"),  # after a failed step: R6 e
            r("end", "impl", 11, outcome="done"),
            r("start", "review", 12, started_by="autopilot"),
            r("end", "review", 13, outcome="done", verdicts=["pass"]),
            r("start", "ship", 14, started_by="person"),  # after the stop before ship
        ]

    def test_person_starts_outside_the_stops(self):
        day = NOW.date().isoformat()
        got = ap.measure(self.rows(), "w", day, day)
        [u] = got["units"]
        self.assertTrue(u["reached"])
        self.assertEqual((u["autopilot"], u["person"]), (2, 3))
        self.assertEqual([o["stage"] for o in u["outside"]], ["impl"])
        self.assertEqual(got["met"], [])

    def test_a_unit_with_none_outside_is_met(self):
        day = NOW.date().isoformat()
        rows = [r for r in self.rows() if r["stage"] != "impl"]
        self.assertEqual(ap.measure(rows, "w", day, day)["met"], ["0010_a"])

    def test_outside_the_dates_or_the_workspace_nothing_counts(self):
        self.assertEqual(ap.measure(self.rows(), "other", "2000-01-01", "2100-01-01")["units"], [])
        self.assertEqual(ap.measure(self.rows(), "w", "2000-01-01", "2000-01-02")["units"], [])


class MeasuredDays(unittest.TestCase):
    def test_each_clause_of_the_outcome_per_day(self):
        one, two = ap.today(NOW), ap.today(NOW + timedelta(days=1))

        def r(kind, delta=timedelta(), workspace="w", **kw):
            return {"kind": kind, "workspace": workspace, "unit": "0010_a", "at": at(delta), **kw}

        tomorrow = timedelta(days=1)
        rows = (
            [r("integration", mode="mechanical") for _ in range(5)]
            + [r("end", stage="impl", outcome="failed"),
               r("start", stage="spec", started_by="autopilot"),
               r("end", stage="spec", outcome="done", cost_usd=3.0),
               r("end", workspace="other", stage="review", outcome="done", cost_usd=1.0)]
            + [r("integration", tomorrow, mode="agent") for _ in range(4)]
            + [r("end", tomorrow, stage="impl", outcome="exhausted", cost_usd=2.0),
               r("start", tomorrow, stage="plan", started_by="autopilot")]
        )
        first, second = ap.measure_days(rows, "w", one, two, 80.0)
        self.assertEqual((first["day"], second["day"]), (one, two))
        self.assertEqual((first["integrations"], first["failed"], first["autopilot_starts"]), (5, 1, 1))
        self.assertEqual(
            [first[k] for k in ("enough_integrations", "a_failure", "ran", "within", "met")], [True] * 5,
        )
        money = ap.spent_on(rows, one)
        self.assertEqual(first["spent"], money["known"] + money["estimated"])
        self.assertEqual(first["spent"], 3.0 + 1.0 + ap.estimate("impl"))
        self.assertEqual(second["integrations"], 4)
        self.assertEqual(
            [second[k] for k in ("enough_integrations", "a_failure", "ran", "within", "met")],
            [False, True, True, True, False],
        )
        self.assertFalse(ap.measure_days(rows, "w", one, one, 19.99)[0]["within"])
        self.assertEqual(first["utc_to"], second["utc_from"])


class ReasonsAndPassed(unittest.TestCase):
    """`0104` R6: a reason is read off what the pass had, and never made up."""

    def test_a_candidate_has_no_reason(self):
        self.assertIsNone(ap.reason_for(nxt("spec", "write-spec"), "spec", None))

    def test_stop(self):
        stop = {"kind": "a", "reason": "open questions: spec.md question 2"}
        self.assertEqual(ap.reason_for(nxt("spec"), "spec", stop), ("stop", "a: open questions: spec.md question 2"))

    def test_held(self):
        hold = {"state": "paused", "reason": "later", "by": "Leif", "date": "2026-09-25"}
        self.assertEqual(ap.reason_for(nxt(hold=hold), "", None), ("held", "paused"))

    def test_finished(self):
        self.assertEqual(ap.reason_for(nxt(action=ap.FINISHED), "", None), ("finished", ap.FINISHED))

    def test_closed(self):
        said = ap.CLOSED + "spec rejected"
        self.assertEqual(ap.reason_for(nxt(action=said), "", None), ("closed", said))

    def test_ci(self):
        said = ap.CI_PENDING + "3: t — wait, then ask again"
        self.assertEqual(ap.reason_for(nxt(action=said), "", None), ("ci", said))

    def test_nothing_to_read_it_off_raises(self):
        with self.assertRaises(ValueError):
            ap.reason_for(nxt(action="write-something"), "", None)

    def test_passed_skips_what_was_chosen_before_it_in_the_pass(self):
        reasons = {"0003_c": ("running", "impl")}
        got = ap.passed_for(["0001_a", "0003_c", "0002_b"], ["0001_a", "0002_b"], reasons)
        self.assertEqual(got, [[], [{"unit": "0003_c", "reason": "running", "detail": "impl"}]])

    def test_passed_keeps_the_shortlists_order(self):
        reasons = {"0005_e": ("held", "paused"), "0001_a": ("ci", "CI"), "0004_d": ("missing", "")}
        [got] = ap.passed_for(["0005_e", "0001_a", "0004_d", "0002_b"], ["0002_b"], reasons)
        self.assertEqual([p["unit"] for p in got], ["0005_e", "0001_a", "0004_d"])

    def test_passed_raises_on_a_unit_with_no_reason(self):
        with self.assertRaises(ValueError):
            ap.passed_for(["0001_a", "0002_b"], ["0002_b"], {})


def pick_row(unit, stage, units, pass_="p1", passed=(), delta=0, workspace="w"):
    return {"kind": "autopilot-pick", "workspace": workspace, "unit": unit, "stage": stage, "pass": pass_,
            "rank": units.index(unit) + 1 if unit in units else 0,
            "shortlist": {"n": 1, "at": at(), "units": list(units)}, "passed": list(passed),
            "at": at(timedelta(minutes=delta))}


def step_row(unit, stage, delta=0, kind="start", **kw):
    return {"kind": kind, "workspace": "w", "unit": unit, "stage": stage, "started_by": "autopilot",
            "at": at(timedelta(minutes=delta)), **kw}


def clean_log(steps: int) -> list[dict]:
    """`steps` autopilot steps, each picked first: three units in shortlist order, one mechanical
    integration among them, the one after the first passing over it as `running`."""
    units = ["0003_c", "0001_a", "0002_b"]
    rows: list[dict] = []
    for i in range(steps):
        unit = units[i % 3]
        passed = [{"unit": u, "reason": "running", "detail": "spec"} for u in units[:units.index(unit)]]
        if i == 4:
            rows += [pick_row(unit, "integrate", units, f"p{i}", passed, i),
                     step_row(unit, "integrate", i, kind="integration", mode="mechanical")]
        else:
            rows += [pick_row(unit, "spec", units, f"p{i}", passed, i), step_row(unit, "spec", i)]
    return rows


class MeasuringOrder(unittest.TestCase):
    """`0104` R8, on sample run logs."""

    def until(self):
        return ap.today(NOW + timedelta(days=1))

    def test_v1_a_pick_off_its_shortlist(self):
        got = ap.measure_order([pick_row("0009_z", "spec", ["0001_a"]), step_row("0009_z", "spec", 1)], "w", self.until())
        self.assertEqual([(v["v"], v["unit"]) for v in got["violations"]], [("V1", "0009_z")])

    def test_v2_passed_over_with_no_reason(self):
        rows = [pick_row("0002_b", "spec", ["0001_a", "0002_b"]), step_row("0002_b", "spec", 1)]
        got = ap.measure_order(rows, "w", self.until())
        self.assertEqual([(v["v"], v["unit"]) for v in got["violations"]], [("V2", "0002_b")])
        self.assertIn("0001_a", got["violations"][0]["why"])

    def test_v2_passed_over_for_a_reason_not_on_the_list(self):
        passed = [{"unit": "0001_a", "reason": "busy", "detail": ""}]
        rows = [pick_row("0002_b", "spec", ["0001_a", "0002_b"], passed=passed), step_row("0002_b", "spec", 1)]
        got = ap.measure_order(rows, "w", self.until())
        self.assertEqual([v["v"] for v in got["violations"]], ["V2"])

    def test_v2_one_chosen_earlier_in_the_pass_is_not_passed_over(self):
        units = ["0001_a", "0002_b"]
        rows = [pick_row("0001_a", "spec", units), pick_row("0002_b", "spec", units),
                step_row("0001_a", "spec", 1), step_row("0002_b", "spec", 1)]
        self.assertEqual(ap.measure_order(rows, "w", self.until())["violations"], [])
        rows[1]["pass"] = "p2"
        self.assertEqual([v["v"] for v in ap.measure_order(rows, "w", self.until())["violations"]], ["V2"])

    def test_v2_reads_only_a_units_first_pick(self):
        units = ["0001_a", "0002_b"]
        passed = [{"unit": "0001_a", "reason": "held", "detail": "paused"}]
        rows = [pick_row("0002_b", "spec", units, "p1", passed), step_row("0002_b", "spec", 1),
                pick_row("0002_b", "plan", units, "p2", (), 2), step_row("0002_b", "plan", 3)]
        self.assertEqual(ap.measure_order(rows, "w", self.until())["violations"], [])

    def test_v3_a_step_with_no_pick_since_the_last_one(self):
        rows = [pick_row("0001_a", "spec", ["0001_a"]), step_row("0001_a", "spec", 1), step_row("0001_a", "plan", 2)]
        got = ap.measure_order(rows, "w", self.until())
        self.assertEqual([(v["v"], v["stage"]) for v in got["violations"]], [("V3", "plan")])
        rows = [pick_row("0001_a", "spec", ["0001_a"]), step_row("0001_a", "spec", 1), step_row("0001_a", "spec", 2)]
        self.assertEqual([v["v"] for v in ap.measure_order(rows, "w", self.until())["violations"]], ["V3"])

    def test_nine_clean_steps(self):
        got = ap.measure_order(clean_log(9), "w", self.until())
        self.assertEqual((got["steps"], got["violations"]), (9, []))

    def test_ten_clean_steps(self):
        got = ap.measure_order(clean_log(10), "w", self.until())
        self.assertEqual((got["steps"], got["violations"], got["since"]), (10, [], clean_log(1)[0]["at"]))

    def test_the_window_opens_at_the_first_pick_and_closes_after_until(self):
        before = step_row("0001_a", "spec", -5)
        after = step_row("0001_a", "plan", 60 * 24 * 3)
        agent = step_row("0001_a", "integrate", 2, kind="integration", mode="agent")
        rows = [before] + clean_log(3) + [agent, after]
        got = ap.measure_order(rows, "w", self.until())
        self.assertEqual((got["steps"], got["violations"]), (3, []))
        self.assertEqual(ap.measure_order(clean_log(3), "other", self.until())["steps"], 0)


if __name__ == "__main__":
    unittest.main()
