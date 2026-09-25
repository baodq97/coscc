"""`0043`. The autopilot's decisions, with no session, no `gh` and no run log on disk."""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from coscc import autopilot as ap

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
        self.assertEqual(ap.spent_today(rows, NOW), (3.5, False))

    def test_an_end_without_cost_is_the_cap_reached(self):
        rows = [{"kind": "end", "at": at(), "cost_usd": 1.0}, {"kind": "end", "at": at()}]
        spent, unknown = ap.spent_today(rows, NOW)
        self.assertTrue(unknown)
        self.assertFalse(ap.cap_allows(spent, unknown, 0.0, 0.0, 50.0))

    def test_a_new_day_by_the_machines_clock_starts_again(self):
        local_midnight = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday = (local_midnight - timedelta(seconds=1)).astimezone(timezone.utc).isoformat()
        rows = [{"kind": "end", "at": yesterday, "cost_usd": 49.0}, {"kind": "end", "at": yesterday}]
        self.assertEqual(ap.spent_today(rows, NOW), (0.0, False))

    def test_the_cap_fits_or_does_not(self):
        self.assertTrue(ap.cap_allows(40.0, False, 2.0, 8.0, 50.0))
        self.assertFalse(ap.cap_allows(40.0, False, 2.0, 8.01, 50.0))

    def test_a_reservation_is_the_largest_budget_a_label_can_give(self):
        self.assertEqual(ap.reservation("impl"), 16.0)
        self.assertEqual(ap.reservation("review"), 4.0)
        self.assertEqual(ap.reservation("integrate"), 8.0)
        self.assertEqual(ap.reservation("no-such-stage"), 0.0)

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
        got = ap.pick([self.c("0011_b", "review", None, 2.0)], [], 4, None)
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


if __name__ == "__main__":
    unittest.main()
