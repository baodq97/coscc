"""The autopilot's decisions, with no session, no `gh` and no run log on disk."""

import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from coscc.units import autopilot as ap
from coscc.agent.policy import GRANTS, NOVEL_CEILINGS

COS_MJS = Path(__file__).resolve().parents[2] / ".claude" / "scripts" / "cos.mjs"
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc).astimezone()
# `next`'s two actions for `ship`, one after the merge and one before it.
RECORDING_SHIP = (
    "ship — #95 was merged as abc1234 at 2026-09-20T00:00:00Z: record it in ship.md; do not merge"
)
MERGING_SHIP = "ship — merge with --match-head-commit abc1234"


def at(delta: timedelta = timedelta()) -> str:
    return (NOW + delta).astimezone(timezone.utc).isoformat()


def unit(questions=()):
    return {"name": "0010_a", "questions": list(questions)}


def nxt(stage="", action="", waiting=(), hold=None, reasons=()):
    return {
        "stage": stage,
        "action": action,
        "waiting": list(waiting),
        "hold": hold,
        "reasons": list(reasons),
    }


RECORDING = nxt("ship", RECORDING_SHIP, reasons=["recording-ship"])
MERGING = nxt("ship", MERGING_SHIP)


class TheCodesAreRead(unittest.TestCase):
    """`next` hands out `reasons`, and the words beside them decide nothing."""

    def test_is_recording_ship_reads_the_code_and_not_the_merged_line(self):
        self.assertTrue(ap.is_recording_ship(RECORDING))
        self.assertFalse(ap.is_recording_ship(MERGING))
        self.assertFalse(ap.is_recording_ship(nxt("ship", RECORDING_SHIP)))
        self.assertFalse(ap.is_recording_ship({}))

    def test_a_gate_refusal_is_read_by_its_codes(self):
        class Refused(Exception):
            reasons = ("missing", "ci-pending")

        self.assertTrue(ap.is_ci_pending(Refused("anything at all")))
        self.assertFalse(
            ap.is_ci_pending(Exception("CI has not finished on #7: t — wait, then ask again"))
        )

    def test_a_skip_no_person_decided_stops_for_a_person(self):
        # `cos.mjs` names no stage for it, since running the spec again would only skip again; the
        # stop is `b`, a person's, never `f`'s "no stage it can name".
        said = nxt(
            "",
            "spec.md is skipped by agent, not by a person or their delegate — …",
            reasons=["agent-cannot-skip"],
        )
        self.assertTrue(ap.needs_a_person(said))
        self.assertEqual(ap.stop_for(unit(), said, None, False)["kind"], "b")

    def test_the_autopilot_holds_none_of_cos_mjs_words(self):
        for name in (
            "CI_PENDING",
            "CI_RED",
            "NEEDS_A_PERSON",
            "FINISHED",
            "CLOSED",
            "WAITING_ON",
            "RECORDING",
        ):
            self.assertFalse(hasattr(ap, name), name)


class AUnitWaitingOnADependency(unittest.TestCase):
    """`next` answers `stage: ""` while `impl` waits on a merge."""

    def test_a_unit_waiting_on_a_dependency_is_passed_over_not_stopped_and_no_notice_is_sent(self):
        said = nxt("", "waiting on api/0001_backend to merge", reasons=["dependency", "waiting-on"])
        # No stop is what keeps a notice from going out: only a stop is recorded and told.
        self.assertIsNone(ap.stop_for(unit(), said, None, False))
        self.assertEqual(ap.reason_for(said, "", None), ("dependency", said["action"]))
        self.assertIn("dependency", ap.REASONS)
        # Anything else with no stage is still the stop `f` it was.
        self.assertEqual(
            ap.stop_for(unit(), nxt("", "fix the idea link — x"), None, False)["kind"], "f"
        )


class Stops(unittest.TestCase):
    def test_nothing_to_stop_on_a_stage_to_run(self):
        self.assertIsNone(ap.stop_for(unit(), nxt("spec", "write-spec"), None, False))

    def test_finished_rejected_and_held_are_not_stops(self):
        self.assertIsNone(
            ap.stop_for(unit(), nxt("", "finished", reasons=["finished"]), None, False)
        )
        self.assertIsNone(
            ap.stop_for(
                unit(),
                nxt("", "closed — spec rejected", reasons=["rejected", "closed"]),
                None,
                False,
            )
        )
        self.assertIsNone(
            ap.stop_for(unit(), nxt("", "paused — x", hold={"state": "paused"}), None, False)
        )

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
        self.assertEqual(
            ap.stop_for(unit(), nxt("", "answer F2", waiting=["F2"]), None, False)["kind"], "b"
        )
        said = "needs a person — review used 3 of 3 rounds and findings are still open"
        got = ap.stop_for(unit(), nxt("", said, reasons=["needs-person"]), None, False)
        self.assertEqual((got["kind"], got["reason"]), ("b", said))
        # The words alone are no longer read: with no code it is the stop `f`.
        self.assertEqual(ap.stop_for(unit(), nxt("", said), None, False)["kind"], "f")

    def test_c_ship_only_when_allowed(self):
        self.assertEqual(ap.stop_for(unit(), nxt("ship", "ship"), None, False)["kind"], "c")
        self.assertIsNone(ap.stop_for(unit(), nxt("ship", "ship"), None, True))

    def test_d_gebo_needs_a_person(self):
        last = {
            "kind": "integration",
            "outcome": "needs-person",
            "needs_person": ["both sides edit x.py"],
        }
        got = ap.stop_for(unit(), nxt("review", "x"), last, True)
        self.assertEqual(got["kind"], "d")
        self.assertIn("both sides edit x.py", got["reason"])

    def test_e_a_step_that_did_not_end_done(self):
        for outcome in ("failed", "exhausted", "stopped"):
            got = ap.stop_for(
                unit(), nxt("impl", "x"), {"kind": "end", "stage": "impl", "outcome": outcome}, True
            )
            self.assertEqual(got["kind"], "e", outcome)
        self.assertIsNone(
            ap.stop_for(
                unit(), nxt("pr", "x"), {"kind": "end", "stage": "impl", "outcome": "done"}, True
            )
        )

    def test_e_a_first_exhausted_step_is_no_stop(self):
        ran_out = {"kind": "end", "stage": "plan", "outcome": "exhausted"}
        self.assertIsNone(
            ap.stop_for(unit(), nxt("plan", "write-plan"), ran_out, False, exhausted=1)
        )

    def test_e_a_second_exhausted_step_stops_with_the_same_words(self):
        ran_out = {"kind": "end", "stage": "plan", "outcome": "exhausted"}
        self.assertEqual(
            ap.stop_for(unit(), nxt("plan", "write-plan"), ran_out, False, exhausted=2),
            {"kind": "e", "reason": "the last plan step ended exhausted"},
        )

    def test_e_an_exhausted_ship_stops_the_first_time(self):
        ran_out = {"kind": "end", "stage": "ship", "outcome": "exhausted"}
        self.assertEqual(
            ap.stop_for(unit(), nxt("ship", "ship"), ran_out, True, exhausted=1)["kind"], "e"
        )

    def test_e_an_exhausted_ship_is_no_stop_before_a_recording_ship(self):
        ran_out = {"kind": "end", "stage": "ship", "outcome": "exhausted"}
        for count in (1, 3):
            self.assertIsNone(ap.stop_for(unit(), RECORDING, ran_out, True, exhausted=count), count)

    def test_e_an_exhausted_ship_still_stops_before_a_merging_ship(self):
        ran_out = {"kind": "end", "stage": "ship", "outcome": "exhausted"}
        self.assertEqual(
            ap.stop_for(unit(), MERGING, ran_out, True, exhausted=1),
            {"kind": "e", "reason": "the last ship step ended exhausted"},
        )

    def test_e_a_recording_ship_that_ran_out_stops(self):
        ran_out = {"kind": "end", "stage": "ship", "outcome": "exhausted"}
        self.assertEqual(
            ap.stop_for(unit(), RECORDING, ran_out, True, exhausted=1, recorded=True),
            {"kind": "e", "reason": "the last ship step ended exhausted"},
        )

    def test_e_failed_cancelled_and_stopped_ships_stop_before_a_recording_ship(self):
        for outcome in ("failed", "cancelled", "stopped"):
            last = {"kind": "end", "stage": "ship", "outcome": outcome}
            self.assertEqual(
                ap.stop_for(unit(), RECORDING, last, True, exhausted=1)["kind"], "e", outcome
            )

    def test_e_a_recording_ship_still_meets_c(self):
        ran_out = {"kind": "end", "stage": "ship", "outcome": "exhausted"}
        self.assertEqual(ap.stop_for(unit(), RECORDING, ran_out, False, exhausted=1)["kind"], "c")

    def test_skips_exhausted_only_for_a_ship_end(self):
        self.assertFalse(
            ap.skips_exhausted(
                RECORDING, {"kind": "end", "stage": "plan", "outcome": "exhausted"}, False
            )
        )
        self.assertFalse(
            ap.skips_exhausted(
                RECORDING, {"kind": "integration", "stage": "ship", "outcome": "exhausted"}, False
            )
        )
        self.assertFalse(ap.skips_exhausted(RECORDING, None, False))
        self.assertTrue(
            ap.skips_exhausted(
                RECORDING, {"kind": "end", "stage": "ship", "outcome": "exhausted"}, False
            )
        )

    def test_e_failed_cancelled_and_stopped_stop_whatever_the_count(self):
        for outcome in ("failed", "cancelled", "stopped"):
            last = {"kind": "end", "stage": "plan", "outcome": outcome}
            self.assertEqual(
                ap.stop_for(unit(), nxt("plan", "write-plan"), last, False, exhausted=1)["kind"],
                "e",
                outcome,
            )

    def test_e_a_first_exhausted_step_still_meets_every_other_stop(self):
        ran_out = {"kind": "end", "stage": "plan", "outcome": "exhausted"}
        qs = [{"artifact": "plan.md", "n": 1, "answered": False, "counted": True}]
        cases = [
            (unit(qs), nxt("plan", "write-plan"), ran_out, "a"),
            (unit(), nxt("", "answer F1", waiting=["F1"]), ran_out, "b"),
            (unit(), nxt("", "finish and accept plan.md"), ran_out, "f"),
            (unit(), nxt("", "paused — x", hold={"state": "paused"}), ran_out, None),
            (unit(), nxt("ship", "ship"), {**ran_out, "stage": "review"}, "c"),
        ]
        for row_, next_, last, kind in cases:
            got = ap.stop_for(row_, next_, last, False, exhausted=1)
            self.assertEqual((got or {}).get("kind"), kind, next_)
            self.assertEqual(
                got, ap.stop_for(row_, next_, {**last, "outcome": "done"}, False), next_
            )

    def test_e_an_integration_that_failed_or_that_the_autopilot_had_refused(self):
        failed = {"kind": "integration", "outcome": "failed", "detail": "gh down"}
        self.assertEqual(ap.stop_for(unit(), nxt("review", "x"), failed, True)["kind"], "e")
        mine = {"kind": "integration", "outcome": "refused", "started_by": "autopilot"}
        self.assertEqual(ap.stop_for(unit(), nxt("review", "x"), mine, True)["kind"], "e")
        theirs = {"kind": "integration", "outcome": "refused", "started_by": "person"}
        self.assertIsNone(ap.stop_for(unit(), nxt("review", "x"), theirs, True))

    def test_e_a_pr_or_ship_the_machine_failed_or_refused_and_none_once_one_did_its_work(self):
        """With no `end`, the machine's record is the last word."""
        refused = {
            "kind": ap.PR_MACHINE,
            "stage": "ship",
            "outcome": "failed",
            "result": "refused",
            "reasons": ["head-moved"],
            "detail": "",
        }
        got = ap.stop_for(unit(), nxt("ship", "x"), refused, True)
        self.assertEqual(got, {"kind": "e", "reason": "the last ship was refused: head-moved"})
        failed = {
            "kind": ap.PR_MACHINE,
            "stage": "pr",
            "outcome": "failed",
            "result": "failed",
            "detail": "gh down",
        }
        self.assertEqual(
            ap.stop_for(unit(), nxt("pr", "x"), failed, True)["reason"],
            "the last pr was failed: gh down",
        )
        done = {"kind": ap.PR_MACHINE, "stage": "pr", "outcome": "done", "result": "opened"}
        self.assertIsNone(ap.stop_for(unit(), nxt("review", "x"), done, True))

    def test_f_a_merge_github_refused_stops_on_what_gh_said(self):
        """A merge GitHub refused stops `f` on what `gh` said, so the pass may still integrate a
        unit the refusal left behind `main`."""
        said = "the head branch is not up to date with the base branch"
        refused = {
            "kind": ap.PR_MACHINE,
            "stage": "ship",
            "outcome": "failed",
            "result": "failed",
            "detail": said,
            "merge_refused": True,
        }
        self.assertEqual(
            ap.stop_for(unit(), nxt("", "behind"), refused, True),
            {"kind": "f", "reason": f"ship was refused: {said}"},
        )
        failed = {**refused, "merge_refused": False}
        self.assertEqual(ap.stop_for(unit(), nxt("", "behind"), failed, True)["kind"], "e")

    def test_e_screenshots_that_could_not_be_taken_again_and_none_once_they_were(self):
        # No retry; a retake that is taken, run by a person, lifts it.
        failed = {"kind": "screens", "stage": "review", "outcome": "failed", "detail": "exited 2"}
        got = ap.stop_for(unit(), nxt("review", "x"), failed, True)
        self.assertEqual(got, {"kind": "e", "reason": ap.SCREENS_FAILED})
        taken = {**failed, "outcome": "taken"}
        self.assertIsNone(ap.stop_for(unit(), nxt("review", "x"), taken, True))

    def test_since_integration_starts_at_the_latest_integration_and_closes_at_review(self):
        def r(kind, unit="0010_a", stage="impl", **kw):
            return {"kind": kind, "workspace": "w", "unit": unit, "stage": stage, **kw}

        first, second = r("integration", stage="integrate"), r("integration", stage="integrate")
        impl, end = r("start"), r("end", outcome="done")
        rows = [
            first,
            r("start", stage="spec"),
            second,
            impl,
            r("start", unit="0011_b"),
            r("start", stage="precedent"),
            end,
        ]
        self.assertEqual(ap.since_integration(rows, "w", "0010_a"), [impl, end])
        self.assertIsNone(ap.since_integration(rows, "w", "0011_b"))
        self.assertIsNone(ap.since_integration(rows, "other", "0010_a"))
        closed = rows + [r("start", stage="review"), r("start")]
        self.assertIsNone(ap.since_integration(closed, "w", "0010_a"))
        self.assertEqual(ap.since_integration(closed + [first], "w", "0010_a"), [])

    def test_after_own_integration_runs_impl_once_then_stops(self):
        red = {"state": "red-after-integration"}
        mine = {
            "kind": "integration",
            "outcome": "pushed",
            "mode": "mechanical",
            "started_by": "autopilot",
        }
        fix = nxt(
            "impl",
            "CI is red on #120: tests — back to impl: fix on the branch and push",
            reasons=["ci-red"],
        )
        self.assertEqual(ap.after_own_integration(red, mine, [], fix), ("impl", None))
        ran = [
            {"kind": "start", "stage": "impl", "started_by": "autopilot"},
            {"kind": "end", "stage": "impl"},
        ]
        still = ("", {"kind": "e", "reason": ap.STILL_RED})
        self.assertEqual(ap.after_own_integration(red, mine, ran, fix), still)
        # The `impl` pushed, so the board no longer reads the head as the integrated one.
        self.assertEqual(ap.after_own_integration({"state": "current"}, mine, ran, fix), still)
        self.assertEqual(ap.after_own_integration({"state": "behind"}, mine, ran, fix), still)
        # Green again, or not sent to `impl` for CI: the rest of the pass decides.
        self.assertIsNone(
            ap.after_own_integration({"state": "current"}, mine, ran, nxt("review", "write-review"))
        )
        self.assertIsNone(ap.after_own_integration({"state": "current"}, mine, [], fix))

    def test_after_own_integration_keeps_todays_stop_when_next_names_no_impl(self):
        red = {"state": "red-after-integration"}
        mine = {
            "kind": "integration",
            "outcome": "pushed",
            "mode": "agent",
            "started_by": "autopilot",
        }
        today = (
            "",
            {"kind": "e", "reason": "CI is still red after the autopilot's last integration"},
        )
        self.assertEqual(
            ap.after_own_integration(red, mine, [], nxt("", "finish and accept plan.md")), today
        )
        # Past the window, today's stop too, `impl` or not.
        self.assertEqual(
            ap.after_own_integration(
                red, mine, None, nxt("impl", "CI is red on #7: t", reasons=["ci-red"])
            ),
            today,
        )
        self.assertIsNone(
            ap.after_own_integration(
                {"state": "current"},
                mine,
                None,
                nxt("impl", "CI is red on #7: t", reasons=["ci-red"]),
            )
        )

    def test_after_own_integration_leaves_a_persons_integration_alone(self):
        red = {"state": "red-after-integration"}
        fix = nxt("impl", "CI is red on #7: t — back to impl", reasons=["ci-red"])
        ran = [{"kind": "start", "stage": "impl", "started_by": "autopilot"}]
        theirs = {"kind": "integration", "outcome": "pushed", "started_by": "person"}
        self.assertIsNone(ap.after_own_integration(red, theirs, [], fix))
        self.assertIsNone(ap.after_own_integration(red, theirs, ran, fix))
        self.assertIsNone(
            ap.after_own_integration(red, {"kind": "integration", "outcome": "pushed"}, ran, fix)
        )
        self.assertIsNone(ap.after_own_integration(red, None, None, fix))
        refused = {"kind": "integration", "outcome": "refused", "started_by": "autopilot"}
        self.assertIsNone(ap.after_own_integration(red, refused, [], fix))

    def test_after_own_integration_counts_only_the_autopilots_impl(self):
        red = {"state": "red-after-integration"}
        mine = {"kind": "integration", "outcome": "pushed", "started_by": "autopilot"}
        fix = nxt("impl", "CI is red on #7: t — back to impl", reasons=["ci-red"])
        theirs = [
            {"kind": "start", "stage": "impl", "started_by": "person"},
            {"kind": "start", "stage": "impl"},
            {"kind": "start", "stage": "pr", "started_by": "autopilot"},
        ]
        self.assertEqual(ap.after_own_integration(red, mine, theirs, fix), ("impl", None))

    def test_after_own_integration_a_first_exhausted_impl_is_not_the_one(self):
        """The autopilot's `impl` that ran out runs once more; the one after it counts, and a second
        that runs out is not forgiven."""
        red = {"state": "red-after-integration"}
        mine = {"kind": "integration", "outcome": "pushed", "started_by": "autopilot"}
        fix = nxt("impl", "CI is red on #7: t — back to impl", reasons=["ci-red"])
        start = {"kind": "start", "stage": "impl", "started_by": "autopilot"}
        out = {"kind": "end", "stage": "impl", "outcome": "exhausted"}
        done = {**out, "outcome": "done"}
        still = ("", {"kind": "e", "reason": ap.STILL_RED})
        self.assertEqual(ap.after_own_integration(red, mine, [start, out], fix, 1), ("impl", None))
        self.assertEqual(ap.after_own_integration(red, mine, [start, out], fix), still)
        self.assertEqual(
            ap.after_own_integration(red, mine, [start, out, start, done], fix, 1), still
        )
        self.assertEqual(
            ap.after_own_integration(red, mine, [start, out, start, out], fix, 2), still
        )
        # A person's `impl` that ran out was never counted, and forgives nothing.
        theirs = [{**start, "started_by": "person"}, out, start, done]
        self.assertEqual(ap.after_own_integration(red, mine, theirs, fix, 1), still)

    def test_is_ci_red_reads_the_code_and_not_the_words(self):
        self.assertTrue(
            ap.is_ci_red(nxt("impl", "anything", reasons=["changes-requested", "ci-red"]))
        )
        self.assertFalse(
            ap.is_ci_red(
                nxt("impl", "CI is red on #7: tests — back to impl: fix on the branch and push")
            )
        )
        self.assertFalse(ap.is_ci_red(nxt("", "x", reasons=["ci-pending"])))
        self.assertFalse(ap.is_ci_red(None))

    UNOPENED = {
        "kind": "end",
        "stage": "plan",
        "outcome": "failed",
        "detail": "plan.md lacks its opening: no `Status:` line in its header",
    }

    def test_a_first_opening_failure_of_a_prose_stage_is_no_stop(self):
        self.assertIsNone(
            ap.stop_for(unit(), nxt("plan", "write-plan"), self.UNOPENED, False, unopened=1)
        )
        # A repair turn that failed too keeps the first refusal on its first line.
        repaired_not = {
            **self.UNOPENED,
            "detail": self.UNOPENED["detail"] + "\n--- plan.md: the repair turn failed ---",
        }
        self.assertIsNone(
            ap.stop_for(unit(), nxt("plan", "write-plan"), repaired_not, False, unopened=1)
        )
        self.assertEqual(
            ap.stop_for(unit(), nxt("plan", "write-plan"), self.UNOPENED, False)["kind"], "e"
        )

    def test_a_second_opening_failure_stops_e_with_the_same_words(self):
        self.assertEqual(
            ap.stop_for(unit(), nxt("plan", "write-plan"), self.UNOPENED, False, unopened=2),
            {"kind": "e", "reason": "the last plan step ended failed"},
        )

    def test_a_failure_for_another_reason_after_an_opening_failure_stops(self):
        other = {**self.UNOPENED, "detail": "the session returned nothing"}
        self.assertEqual(
            ap.stop_for(unit(), nxt("plan", "write-plan"), other, False, unopened=1)["kind"], "e"
        )
        # And the count of the other reason forgives nothing here.
        self.assertEqual(
            ap.stop_for(
                unit(), nxt("plan", "write-plan"), self.UNOPENED, False, exhausted=1, unopened=2
            )["kind"],
            "e",
        )

    def test_a_spike_opening_failure_stops(self):
        spike = {
            "kind": "end",
            "stage": "spike",
            "outcome": "failed",
            "detail": "spike.md lacks its opening: no `# Spike:` title",
        }
        self.assertEqual(
            ap.stop_for(unit(), nxt("spike", "write-spike"), spike, False, unopened=1)["kind"], "e"
        )

    def test_f_no_stage_and_not_ci(self):
        got = ap.stop_for(unit(), nxt("", "finish and accept plan.md"), None, True)
        self.assertEqual((got["kind"], got["reason"]), ("f", "finish and accept plan.md"))

    def test_ci_pending_is_not_a_stop(self):
        said = nxt(
            "",
            "CI has not finished on #7: test — wait, then ask again",
            reasons=["missing", "ci-pending"],
        )
        self.assertTrue(ap.is_ci_pending(said))
        self.assertIsNone(ap.stop_for(unit(), said, None, True))
        self.assertFalse(ap.is_ci_pending(nxt("impl", "x", reasons=["ci-red"])))


class TheDaysMoney(unittest.TestCase):
    def test_every_end_of_today_whoever_started_it(self):
        rows = [
            {"kind": "end", "at": at(), "cost_usd": 1.5, "workspace": "w1"},
            {"kind": "end", "at": at(-timedelta(minutes=5)), "cost_usd": 2.0, "workspace": "w2"},
            {"kind": "start", "at": at(), "workspace": "w1"},
        ]
        self.assertEqual(
            ap.spent_today(rows, NOW), {"known": 3.5, "estimated": 0.0, "estimated_count": 0}
        )

    def test_an_end_without_cost_is_not_the_cap_reached(self):
        rows = [
            {"kind": "end", "at": at(), "cost_usd": 1.0},
            {"kind": "end", "at": at(), "stage": "spec"},
        ]
        got = ap.spent_today(rows, NOW)
        self.assertEqual((got["known"], got["estimated"]), (1.0, ap.estimate("spec")))
        self.assertTrue(ap.cap_allows(got["known"] + got["estimated"], 0.0, 0.0, 50.0))

    def test_a_new_day_by_the_machines_clock_starts_again(self):
        local_midnight = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday = (local_midnight - timedelta(seconds=1)).astimezone(timezone.utc).isoformat()
        rows = [
            {"kind": "end", "at": yesterday, "cost_usd": 49.0},
            {"kind": "end", "at": yesterday},
        ]
        self.assertEqual(
            ap.spent_today(rows, NOW), {"known": 0.0, "estimated": 0.0, "estimated_count": 0}
        )

    def test_the_cap_fits_or_does_not(self):
        self.assertTrue(ap.cap_allows(40.0, 2.0, 8.0, 50.0))
        self.assertFalse(ap.cap_allows(40.0, 2.0, 8.01, 50.0))

    def test_a_day_like_2026_09_25_fits_or_is_capped(self):
        rows = [{"kind": "end", "at": at(), "stage": "impl", "outcome": "failed"}] + [
            {"kind": "end", "at": at(), "stage": "integrate", "outcome": "done"} for _ in range(4)
        ]
        estimated = ap.spent_on(rows, ap.today(NOW))["estimated"]
        self.assertEqual(estimated, 48.0)
        spec = {"unit": "0010_a", "stage": "spec", "files": None, "need": 4.0, "rank": 1}
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
        self.assertEqual(
            (got["known"], got["estimated"], got["estimated_count"]), (0.0, 16.0 + 4.0 + 8.0, 3)
        )

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
            {
                "kind": "start",
                "workspace": "w",
                "unit": "0010_a",
                "stage": "impl",
                "at": at(-timedelta(hours=1)),
            },
            {
                "kind": "start",
                "workspace": "w",
                "unit": "0011_b",
                "stage": "review",
                "at": at(-timedelta(hours=25)),
            },
            {
                "kind": "start",
                "workspace": "w",
                "unit": "0012_c",
                "stage": "spec",
                "at": at(-timedelta(hours=2)),
            },
            {
                "kind": "end",
                "workspace": "w",
                "unit": "0012_c",
                "stage": "spec",
                "at": at(),
                "cost_usd": 1,
            },
        ]
        self.assertEqual(set(ap.open_starts(rows, NOW)), {("w", "0010_a")})
        self.assertEqual(ap.reserved(rows, NOW), 16.0)
        # A step this process holds that has not written its `start` yet counts too, once.
        self.assertEqual(
            ap.reserved(rows, NOW, [("w", "0010_a", "impl"), ("w", "0013_d", "review")]), 20.0
        )
        # A `pr` or `ship` opens no session, so it is counted at nothing.
        self.assertEqual(
            ap.reserved(rows, NOW, [("w", "0013_d", "pr"), ("w", "0014_e", "ship")]), 16.0
        )


class Scheduling(unittest.TestCase):
    def c(self, unit, stage, files=None, need=1.0, rank=None):
        return {
            "unit": unit,
            "stage": stage,
            "files": files,
            "need": need,
            "rank": ap.unit_number(unit) if rank is None else rank,
        }

    def test_highest_rank_first_up_to_max_parallel(self):
        got = ap.pick(
            [
                self.c("0012_c", "spec", rank=1),
                self.c("0010_a", "spec", rank=3),
                self.c("0011_b", "pr", rank=2),
            ],
            [],
            2,
            100.0,
        )
        self.assertEqual([c["unit"] for c in got["chosen"]], ["0012_c", "0011_b"])
        self.assertEqual(got["held"], {})

    def test_running_steps_count_person_ones_included(self):
        got = ap.pick(
            [self.c("0010_a", "spec")],
            [{"unit": "0001_x", "stage": "review", "files": None}],
            1,
            100.0,
        )
        self.assertEqual(got["chosen"], [])

    def test_a_unit_already_running_is_skipped(self):
        got = ap.pick(
            [self.c("0010_a", "review")],
            [{"unit": "0010_a", "stage": "pr", "files": None}],
            4,
            100.0,
        )
        self.assertEqual((got["chosen"], got["held"]), ([], {"0010_a": ("running", "pr")}))

    def test_code_stages_with_overlapping_files_run_one_after_the_other(self):
        a = self.c("0010_a", "impl", {"coscc/x.py", "coscc/y.py"})
        b = self.c("0011_b", "impl", {"coscc/y.py"})
        c = self.c("0012_c", "integrate", {"coscc/z.py"})
        got = ap.pick([a, b, c], [], 4, 100.0)
        self.assertEqual([x["unit"] for x in got["chosen"]], ["0010_a", "0012_c"])
        self.assertEqual(got["held"], {"0011_b": ("overlap", "0010_a")})

    def test_unknown_files_overlap_with_everything(self):
        got = ap.pick(
            [self.c("0010_a", "impl", None), self.c("0011_b", "impl", {"a/b.py"})], [], 4, 100.0
        )
        self.assertEqual([x["unit"] for x in got["chosen"]], ["0010_a"])
        got = ap.pick(
            [self.c("0011_b", "impl", {"a/b.py"})],
            [{"unit": "0001_x", "stage": "integrate", "files": None}],
            4,
            100.0,
        )
        self.assertEqual(got["chosen"], [])

    def test_prose_stages_ignore_files(self):
        got = ap.pick(
            [self.c("0010_a", "review"), self.c("0011_b", "pr")],
            [{"unit": "0001_x", "stage": "impl", "files": None}],
            4,
            100.0,
        )
        self.assertEqual(len(got["chosen"]), 2)

    def test_one_ship_in_the_workspace(self):
        got = ap.pick([self.c("0010_a", "ship"), self.c("0011_b", "ship")], [], 4, 100.0)
        self.assertEqual([x["unit"] for x in got["chosen"]], ["0010_a"])
        self.assertEqual(got["held"], {"0011_b": ("ship-busy", "0010_a")})
        got = ap.pick(
            [self.c("0011_b", "ship")],
            [{"unit": "0010_a", "stage": "ship", "files": None}],
            4,
            100.0,
        )
        self.assertEqual((got["chosen"], got["held"]), ([], {"0011_b": ("ship-busy", "0010_a")}))

    def test_an_open_pr_on_the_same_file_holds_impl_until_it_merges(self):
        b = self.c("0011_b", "impl", {"a.py"})
        pr = {"unit": "0010_a", "number": 7, "files": {"a.py", "c.py"}}
        got = ap.pick([b], [], 4, 100.0, open_prs=[pr])
        self.assertEqual((got["chosen"], got["held"]), ([], {"0011_b": ("overlap-pr", "#7")}))
        got = ap.pick([b], [], 4, 100.0, open_prs=[])
        self.assertEqual([x["unit"] for x in got["chosen"]], ["0011_b"])

    def test_a_unit_with_its_own_open_pr_is_not_held_by_another(self):
        # Back at impl after `changes-requested`: two open pull requests on one file must not
        # wait on each other for ever.
        b = self.c("0011_b", "impl", {"a.py"})
        prs = [
            {"unit": "0010_a", "number": 7, "files": {"a.py"}},
            {"unit": "0011_b", "number": 8, "files": {"a.py"}},
        ]
        got = ap.pick([b], [], 4, 100.0, open_prs=prs)
        self.assertEqual([x["unit"] for x in got["chosen"]], ["0011_b"])

    def test_a_pr_whose_files_could_not_be_read_overlaps_every_file(self):
        got = ap.pick(
            [self.c("0011_b", "impl", {"a.py"})],
            [],
            4,
            100.0,
            open_prs=[{"unit": "0010_a", "number": 7, "files": None}],
        )
        self.assertEqual(got["held"], {"0011_b": ("overlap-pr", "#7")})

    def test_an_open_pr_holds_only_impl(self):
        got = ap.pick(
            [self.c("0011_b", "review", {"a.py"})],
            [],
            4,
            100.0,
            open_prs=[{"unit": "0010_a", "number": 7, "files": {"a.py"}}],
        )
        self.assertEqual([x["unit"] for x in got["chosen"]], ["0011_b"])

    def test_overlap_pr_is_a_code_of_the_one_reason_table(self):
        from coscc.units import guards

        self.assertIn("overlap-pr", ap.REASONS)
        self.assertIn("overlap-pr", guards.REASONS)

    def test_the_cap_holds_back_what_does_not_fit(self):
        got = ap.pick(
            [self.c("0010_a", "impl", {"a"}, 16.0), self.c("0011_b", "review", None, 2.0)],
            [],
            4,
            3.0,
        )
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
            return {
                "kind": kind,
                "workspace": w,
                "unit": u,
                "stage": stage,
                "at": at(timedelta(minutes=delta)),
                **kw,
            }

        return [
            r("start", "intent", 0),  # before the count, and written before the field existed
            r("end", "intent", 1, outcome="done"),
            r("start", "spec", 2, started_by="autopilot"),
            r("end", "spec", 3, outcome="done"),
            r("autopilot-stop", "", 4, stop="a"),
            r("start", "plan", 5, started_by="person"),  # at a stop
            r("end", "plan", 6, outcome="done"),
            r("autopilot-stop", "", 7, stop=""),
            r("start", "impl", 8),  # an old record: person, and outside every stop
            r("end", "impl", 9, outcome="failed"),
            r("start", "impl", 10, started_by="person"),  # after a failed step
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

    def test_lines_under_precedent_are_no_step(self):
        """Lines written under `precedent` are no start, and their `end` does not decide whether a
        person's start was at a stop."""
        day = NOW.date().isoformat()
        rows = [r for r in self.rows() if r["stage"] != "impl"]
        earlier = [
            {
                "kind": "start",
                "workspace": "w",
                "unit": "0010_a",
                "stage": "precedent",
                "at": at(timedelta(minutes=m)),
            }
            for m in (5.5, 12.5)
        ] + [
            {
                "kind": "end",
                "workspace": "w",
                "unit": "0010_a",
                "stage": "precedent",
                "outcome": "failed",
                "at": at(timedelta(minutes=11.5)),
            }
        ]
        rows = sorted(rows + earlier, key=lambda r: r["at"])
        got = ap.measure(rows, "w", day, day)
        self.assertEqual(got["met"], ["0010_a"])
        self.assertEqual(got["units"][0]["person"], 1)
        self.assertEqual(ap.measure_days(earlier, "w", day, day, 80.0)[0]["failed"], 0)

    def test_outside_the_dates_or_the_workspace_nothing_counts(self):
        self.assertEqual(ap.measure(self.rows(), "other", "2000-01-01", "2100-01-01")["units"], [])
        self.assertEqual(ap.measure(self.rows(), "w", "2000-01-01", "2000-01-02")["units"], [])


class TheAutopilotHasNoAnswerer(unittest.TestCase):
    """Every open question of the counted artifact is a stop `a`, and nothing else answers it."""

    def test_an_open_question_is_the_stop_a_naming_each_one(self):
        row = {
            "name": "0010_a",
            "questions": [
                {"artifact": "spec.md", "n": 1, "text": "a?", "answered": False, "counted": True},
                {"artifact": "spec.md", "n": 2, "text": "b?", "answered": False, "counted": True},
                {"artifact": "spec.md", "n": 3, "text": "c?", "answered": True, "counted": True},
                {
                    "artifact": "intent.md",
                    "n": 1,
                    "text": "d?",
                    "answered": False,
                    "counted": False,
                },
            ],
        }
        got = ap.stop_for(row, {"stage": "plan", "action": "write-plan"}, None, False)
        self.assertEqual(got["kind"], "a")
        self.assertIn("spec.md question 1, spec.md question 2", got["reason"])
        self.assertNotIn("question 3", got["reason"])

    def test_a_running_start_is_reserved_at_its_stages_most(self):
        rows = [
            {"kind": "start", "workspace": "w", "unit": "0010_a", "stage": "impl", "at": at()},
            {"kind": "start", "workspace": "w", "unit": "0011_b", "stage": "spec", "at": at()},
        ]
        self.assertEqual(ap.reserved(rows, NOW), ap.reservation("impl") + ap.reservation("spec"))


class MeasuredDays(unittest.TestCase):
    def test_each_clause_of_the_outcome_per_day(self):
        one, two = ap.today(NOW), ap.today(NOW + timedelta(days=1))

        def r(kind, delta=timedelta(), workspace="w", **kw):
            return {"kind": kind, "workspace": workspace, "unit": "0010_a", "at": at(delta), **kw}

        tomorrow = timedelta(days=1)
        rows = (
            [r("integration", mode="mechanical") for _ in range(5)]
            + [
                r("end", stage="impl", outcome="failed"),
                r("start", stage="spec", started_by="autopilot"),
                r("end", stage="spec", outcome="done", cost_usd=3.0),
                r("end", workspace="other", stage="review", outcome="done", cost_usd=1.0),
            ]
            + [r("integration", tomorrow, mode="agent") for _ in range(4)]
            + [
                r("end", tomorrow, stage="impl", outcome="exhausted", cost_usd=2.0),
                r("start", tomorrow, stage="plan", started_by="autopilot"),
            ]
        )
        first, second = ap.measure_days(rows, "w", one, two, 80.0)
        self.assertEqual((first["day"], second["day"]), (one, two))
        self.assertEqual(
            (first["integrations"], first["failed"], first["autopilot_starts"]), (5, 1, 1)
        )
        self.assertEqual(
            [first[k] for k in ("enough_integrations", "a_failure", "ran", "within", "met")],
            [True] * 5,
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
    """A reason is read off what the pass had, and never made up."""

    def test_a_candidate_has_no_reason(self):
        self.assertIsNone(ap.reason_for(nxt("spec", "write-spec"), "spec", None))

    def test_stop(self):
        stop = {"kind": "a", "reason": "open questions: spec.md question 2"}
        self.assertEqual(
            ap.reason_for(nxt("spec"), "spec", stop),
            ("stop", "a: open questions: spec.md question 2"),
        )

    def test_held(self):
        hold = {"state": "paused", "reason": "later", "by": "Leif", "date": "2026-09-25"}
        self.assertEqual(ap.reason_for(nxt(hold=hold), "", None), ("held", "paused"))

    def test_finished(self):
        self.assertEqual(
            ap.reason_for(nxt(action="finished", reasons=["finished"]), "", None),
            ("finished", "finished"),
        )

    def test_closed(self):
        said = "closed — spec rejected"
        self.assertEqual(
            ap.reason_for(nxt(action=said, reasons=["rejected", "closed"]), "", None),
            ("closed", said),
        )

    def test_ci(self):
        said = "CI has not finished on #3: t — wait, then ask again"
        self.assertEqual(
            ap.reason_for(nxt(action=said, reasons=["ci-pending"]), "", None), ("ci", said)
        )

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
    return {
        "kind": "autopilot-pick",
        "workspace": workspace,
        "unit": unit,
        "stage": stage,
        "pass": pass_,
        "rank": units.index(unit) + 1 if unit in units else 0,
        "shortlist": {"n": 1, "at": at(), "units": list(units)},
        "passed": list(passed),
        "at": at(timedelta(minutes=delta)),
    }


def step_row(unit, stage, delta=0, kind="start", **kw):
    return {
        "kind": kind,
        "workspace": "w",
        "unit": unit,
        "stage": stage,
        "started_by": "autopilot",
        "at": at(timedelta(minutes=delta)),
        **kw,
    }


def clean_log(steps: int) -> list[dict]:
    """`steps` autopilot steps, each picked first: three units in shortlist order, one mechanical
    integration among them, the one after the first passing over it as `running`."""
    units = ["0003_c", "0001_a", "0002_b"]
    rows: list[dict] = []
    for i in range(steps):
        unit = units[i % 3]
        passed = [
            {"unit": u, "reason": "running", "detail": "spec"} for u in units[: units.index(unit)]
        ]
        if i == 4:
            rows += [
                pick_row(unit, "integrate", units, f"p{i}", passed, i),
                step_row(unit, "integrate", i, kind="integration", mode="mechanical"),
            ]
        else:
            rows += [pick_row(unit, "spec", units, f"p{i}", passed, i), step_row(unit, "spec", i)]
    return rows


class MeasuringOrder(unittest.TestCase):
    def until(self):
        return ap.today(NOW + timedelta(days=1))

    def test_v1_a_pick_off_its_shortlist(self):
        got = ap.measure_order(
            [pick_row("0009_z", "spec", ["0001_a"]), step_row("0009_z", "spec", 1)],
            "w",
            self.until(),
        )
        self.assertEqual([(v["v"], v["unit"]) for v in got["violations"]], [("V1", "0009_z")])

    def test_v2_passed_over_with_no_reason(self):
        rows = [pick_row("0002_b", "spec", ["0001_a", "0002_b"]), step_row("0002_b", "spec", 1)]
        got = ap.measure_order(rows, "w", self.until())
        self.assertEqual([(v["v"], v["unit"]) for v in got["violations"]], [("V2", "0002_b")])
        self.assertIn("0001_a", got["violations"][0]["why"])

    def test_v2_passed_over_for_a_reason_not_on_the_list(self):
        passed = [{"unit": "0001_a", "reason": "busy", "detail": ""}]
        rows = [
            pick_row("0002_b", "spec", ["0001_a", "0002_b"], passed=passed),
            step_row("0002_b", "spec", 1),
        ]
        got = ap.measure_order(rows, "w", self.until())
        self.assertEqual([v["v"] for v in got["violations"]], ["V2"])

    def test_v2_one_chosen_earlier_in_the_pass_is_not_passed_over(self):
        units = ["0001_a", "0002_b"]
        rows = [
            pick_row("0001_a", "spec", units),
            pick_row("0002_b", "spec", units),
            step_row("0001_a", "spec", 1),
            step_row("0002_b", "spec", 1),
        ]
        self.assertEqual(ap.measure_order(rows, "w", self.until())["violations"], [])
        rows[1]["pass"] = "p2"
        self.assertEqual(
            [v["v"] for v in ap.measure_order(rows, "w", self.until())["violations"]], ["V2"]
        )

    def test_v2_reads_only_a_units_first_pick(self):
        units = ["0001_a", "0002_b"]
        passed = [{"unit": "0001_a", "reason": "held", "detail": "paused"}]
        rows = [
            pick_row("0002_b", "spec", units, "p1", passed),
            step_row("0002_b", "spec", 1),
            pick_row("0002_b", "plan", units, "p2", (), 2),
            step_row("0002_b", "plan", 3),
        ]
        self.assertEqual(ap.measure_order(rows, "w", self.until())["violations"], [])

    def test_v3_a_step_with_no_pick_since_the_last_one(self):
        rows = [
            pick_row("0001_a", "spec", ["0001_a"]),
            step_row("0001_a", "spec", 1),
            step_row("0001_a", "plan", 2),
        ]
        got = ap.measure_order(rows, "w", self.until())
        self.assertEqual([(v["v"], v["stage"]) for v in got["violations"]], [("V3", "plan")])
        rows = [
            pick_row("0001_a", "spec", ["0001_a"]),
            step_row("0001_a", "spec", 1),
            step_row("0001_a", "spec", 2),
        ]
        self.assertEqual(
            [v["v"] for v in ap.measure_order(rows, "w", self.until())["violations"]], ["V3"]
        )

    def test_nine_clean_steps(self):
        got = ap.measure_order(clean_log(9), "w", self.until())
        self.assertEqual((got["steps"], got["violations"]), (9, []))

    def test_ten_clean_steps(self):
        got = ap.measure_order(clean_log(10), "w", self.until())
        self.assertEqual(
            (got["steps"], got["violations"], got["since"]), (10, [], clean_log(1)[0]["at"])
        )

    def test_the_window_opens_at_the_first_pick_and_closes_after_until(self):
        before = step_row("0001_a", "spec", -5)
        after = step_row("0001_a", "plan", 60 * 24 * 3)
        agent = step_row("0001_a", "integrate", 2, kind="integration", mode="agent")
        rows = [before] + clean_log(3) + [agent, after]
        got = ap.measure_order(rows, "w", self.until())
        self.assertEqual((got["steps"], got["violations"]), (3, []))
        self.assertEqual(ap.measure_order(clean_log(3), "other", self.until())["steps"], 0)


def row(kind, unit="0001_a", stage="intent", seconds=0, workspace="w", **kw):
    return {
        "kind": kind,
        "workspace": workspace,
        "unit": unit,
        "stage": stage,
        "at": at(timedelta(seconds=seconds)),
        **kw,
    }


def answer_row(seconds=0, unit="0001_a", stage="intent", **kw):
    fields = {
        "artifact": f"{stage}.md",
        "question": 2,
        "via": "product",
        "status": "draft",
        "completes": True,
        "autopilot": True,
        "shortlisted": True,
        "held": False,
        **kw,
    }
    return row("answer", unit, stage, seconds, **fields)


def stop_row(stop, seconds=0, unit="0001_a", reason=""):
    return row("autopilot-stop", unit, "", seconds, stop=stop, reason=reason)


class Reruns(unittest.TestCase):
    """A draft answered in full is a step, at most `MAX_RERUNS` times."""

    def board_row(self, questions):
        return {
            "name": "0001_a",
            "questions": questions,
            "stages": [
                {"stage": "intent", "file": "intent.md"},
                {"stage": "spec", "file": "spec.md"},
                {"stage": "impl", "file": "impl.md"},
                {"stage": "review", "file": "review.md"},
            ],
            "after_answers": ["intent", "spec", "spike", "plan", "impl"],
        }

    def test_a_rerun_is_no_stop_f(self):
        rerun = {**nxt("", "finish and accept intent.md"), "rerun": "intent"}
        self.assertIsNone(ap.stop_for(unit(), rerun, None, False))
        self.assertEqual(
            ap.stop_for(unit(), nxt("", "finish and accept intent.md"), None, False)["kind"], "f"
        )

    def test_a_to_e_are_still_asked_first(self):
        rerun = {**nxt("", "finish and accept intent.md"), "rerun": "intent"}
        qs = [{"artifact": "intent.md", "n": 1, "answered": False, "counted": True}]
        self.assertEqual(ap.stop_for(unit(qs), rerun, None, False)["kind"], "a")
        failed = {"kind": "end", "stage": "intent", "outcome": "failed"}
        self.assertEqual(ap.stop_for(unit(), rerun, failed, False)["kind"], "e")

    def test_two_reruns_after_answers(self):
        rows = [
            row("start"),
            row("answer"),
            row("start"),
            row("answer"),
            row("start"),
            row("answer"),
        ]
        self.assertEqual(ap.reruns_of(rows, "w", "0001_a", "intent"), 2)

    def test_no_answer_between_is_no_rerun(self):
        self.assertEqual(ap.reruns_of([row("start"), row("start")], "w", "0001_a", "intent"), 0)

    def test_another_units_or_stages_answer_is_not_counted(self):
        rows = [
            row("start"),
            row("answer", unit="0002_b"),
            row("answer", stage="spec"),
            row("answer", workspace="other"),
            row("start"),
        ]
        self.assertEqual(ap.reruns_of(rows, "w", "0001_a", "intent"), 0)

    def test_whoever_started_counts(self):
        rows = [
            row("start", started_by="person"),
            row("answer"),
            row("start", started_by="autopilot"),
        ]
        self.assertEqual(ap.reruns_of(rows, "w", "0001_a", "intent"), 1)

    def test_only_an_answer_after_the_last_start_is_new(self):
        self.assertTrue(
            ap.answered_since_start([row("start"), row("answer")], "w", "0001_a", "intent")
        )
        self.assertTrue(ap.answered_since_start([row("answer")], "w", "0001_a", "intent"))
        # A rerun that ended `draft` still read as answered: no new answer, whatever `reruns_of` says.
        rows = [row("start"), row("answer"), row("start")]
        self.assertFalse(ap.answered_since_start(rows, "w", "0001_a", "intent"))
        self.assertEqual(ap.reruns_of(rows, "w", "0001_a", "intent"), 1)
        self.assertFalse(ap.answered_since_start([row("start")], "w", "0001_a", "intent"))
        others = [
            row("start"),
            row("answer", unit="0002_b"),
            row("answer", stage="spec"),
            row("answer", workspace="other"),
        ]
        self.assertFalse(ap.answered_since_start(others, "w", "0001_a", "intent"))

    def test_completes_only_on_the_last_question_of_a_rerun_stage(self):
        qs = [
            {"artifact": "intent.md", "n": 1, "answered": True},
            {"artifact": "intent.md", "n": 2, "answered": False},
        ]
        u = self.board_row(qs)
        self.assertTrue(ap.answer_completes(u, "intent.md", {2}))
        self.assertFalse(ap.answer_completes(u, "intent.md", set()))
        self.assertFalse(ap.answer_completes(self.board_row([]), "intent.md", {1}))

    def test_completes_counts_every_block_of_one_write(self):
        qs = [
            {"artifact": "spec.md", "n": 1, "answered": False},
            {"artifact": "spec.md", "n": 2, "answered": False},
        ]
        u = self.board_row(qs)
        self.assertFalse(ap.answer_completes(u, "spec.md", {1}))
        self.assertTrue(ap.answer_completes(u, "spec.md", {1, 2}))

    def test_impl_completes_and_review_never_does(self):
        """`impl` is one of the stages `cos.mjs` lists; `review` is not."""
        qs = [
            {"artifact": "impl.md", "n": 1, "answered": True},
            {"artifact": "review.md", "n": 1, "answered": True},
        ]
        u = self.board_row(qs)
        self.assertTrue(ap.answer_completes(u, "impl.md", {1}))
        self.assertFalse(ap.answer_completes(u, "review.md", {"F1"}))

    def test_a_board_read_without_after_answers_completes_nothing(self):
        """An older `cos.mjs` sends no list, and no stage is guessed in its place."""
        qs = [{"artifact": "intent.md", "n": 1, "answered": True}]
        u = {k: v for k, v in self.board_row(qs).items() if k != "after_answers"}
        self.assertFalse(ap.answer_completes(u, "intent.md", {1}))

    def test_no_module_keeps_its_own_copy_of_the_rerun_stages(self):
        """The list lives in `cos.mjs` alone. The four stages `intent`, `spec`, `spike` and `plan`,
        in that order, appear in no module of the app."""
        literal = re.compile(r"""["']intent["'],\s*["']spec["'],\s*["']spike["'],\s*["']plan["']""")
        package = Path(__file__).resolve().parents[1]
        copies = [
            p.relative_to(package).as_posix()
            for p in sorted(package.rglob("*.py"))
            if not p.name.endswith("_test.py")
            and p.relative_to(package).parts[0] not in ("_harness", "_web")
            and literal.search(p.read_text(encoding="utf-8"))
        ]
        self.assertEqual(copies, [])

    def test_the_two_stop_lines(self):
        self.assertEqual(
            ap.rerun_stop("intent.md"),
            {
                "kind": "reruns",
                "reason": "intent.md was run again 2 times after its answers; a person decides the next run.",
            },
        )
        full = ap.full_stop("spec", 3)
        self.assertEqual(full["kind"], "full")
        self.assertIn("3 steps are already running", full["reason"])
        self.assertIn(": 1 step is already running,", ap.full_stop("spec", 1)["reason"])
        for stop in (ap.rerun_stop("intent.md"), full):
            self.assertIn(stop["kind"], ap.STOP_KINDS)


class ExhaustedOf(unittest.TestCase):
    """How many times a stage ran out, the count `stop_for`'s e reads."""

    def test_counts_every_exhausted_end_of_the_stage_whoever_started_it(self):
        rows = [
            row(
                "end", stage="plan", outcome="exhausted", started_by="person", terminal="max_turns"
            ),
            row("end", stage="plan", outcome="exhausted", started_by="autopilot", budget_usd=1.0),
        ]
        self.assertEqual(ap.exhausted_of(rows, "w", "0001_a", "plan"), 2)

    def test_another_workspace_unit_stage_or_outcome_is_not_counted(self):
        rows = [
            row("end", stage="plan", outcome="exhausted", workspace="other"),
            row("end", unit="0002_b", stage="plan", outcome="exhausted"),
            row("end", stage="spec", outcome="exhausted"),
            row("end", stage="plan", outcome="failed"),
            row("start", stage="plan"),
        ]
        self.assertEqual(ap.exhausted_of(rows, "w", "0001_a", "plan"), 0)


UNOPENED_DETAIL = "plan.md lacks its opening: no `Status:` line in its header"


def unopened_row(seconds=0, stage="plan", unit="0001_a", **kw):
    fields = {
        "outcome": "failed",
        "detail": f"{stage}.md" + UNOPENED_DETAIL[len("plan.md") :],
        **kw,
    }
    return row("end", unit, stage, seconds, **fields)


class UnopenedOf(unittest.TestCase):
    """How many times a stage's reply lacked its opening, the count `stop_for`'s e reads."""

    def test_counts_every_such_failed_end_whoever_started_it(self):
        rows = [unopened_row(started_by="person"), unopened_row(1, started_by="autopilot")]
        self.assertEqual(ap.unopened_of(rows, "w", "0001_a", "plan"), 2)

    def test_another_workspace_unit_stage_outcome_or_detail_is_not_counted(self):
        rows = [
            unopened_row(workspace="other"),
            unopened_row(unit="0002_b"),
            unopened_row(stage="spec"),
            unopened_row(outcome="exhausted"),
            unopened_row(detail="the session returned nothing"),
            unopened_row(detail="spec.md lacks its opening: no `# Spec:` title"),
            row("start", stage="plan"),
        ]
        self.assertEqual(ap.unopened_of(rows, "w", "0001_a", "plan"), 0)

    def test_the_two_counts_do_not_read_each_other(self):
        rows = [
            unopened_row(),
            row("end", stage="plan", outcome="exhausted", detail=UNOPENED_DETAIL),
        ]
        self.assertEqual(ap.exhausted_of(rows, "w", "0001_a", "plan"), 1)
        self.assertEqual(ap.unopened_of(rows, "w", "0001_a", "plan"), 1)


def unopened_stop(seconds=0, stage="plan", unit="0001_a", note=""):
    return stop_row("e", seconds, unit, f"the last {stage} step ended failed" + note)


class MeasureOpening(unittest.TestCase):
    """What became of the prose steps whose reply lacked its opening."""

    def measure(self, rows):
        return ap.measure_opening(
            rows, "w", ap.today(NOW - timedelta(days=1)), ap.today(NOW + timedelta(days=1))
        )

    def test_a_stop_after_an_opening_failure_is_a_miss_and_says_which_time(self):
        rows = [
            row("start", stage="plan"),
            unopened_row(1),
            row("start", stage="plan", seconds=2, started_by="autopilot"),
            unopened_row(3),
            unopened_stop(4, note="; origin x"),
        ]
        got = self.measure(rows)
        self.assertIs(got["met"], False)
        self.assertEqual(
            got["stops"],
            [{"unit": "0001_a", "stage": "plan", "at": at(timedelta(seconds=4)), "attempt": 2}],
        )
        self.assertEqual(len(got["failed"]), 2)

    def test_a_repaired_step_counts_and_meets(self):
        done = row(
            "end",
            stage="spec",
            outcome="done",
            opening="repaired",
            closing={"terminal": "completed", "turns": 1, "cost_usd": 1.1},
        )
        got = self.measure([row("start", stage="spec"), done])
        self.assertIs(got["met"], True)
        self.assertEqual(
            got["repaired"], [{"unit": "0001_a", "stage": "spec", "at": at(), "cost_usd": 1.1}]
        )
        self.assertEqual((got["failed"], got["stops"], got["reruns"]), ([], [], []))

    def test_a_start_after_an_opening_failure_is_a_rerun(self):
        rows = [
            unopened_row(),
            row("start", stage="spec", seconds=1),
            row("start", stage="plan", seconds=2, started_by="autopilot"),
            row("end", stage="plan", seconds=3, outcome="done"),
            row("start", stage="plan", seconds=4),
        ]
        got = self.measure(rows)
        self.assertEqual(
            got["reruns"],
            [
                {
                    "unit": "0001_a",
                    "stage": "plan",
                    "at": at(timedelta(seconds=2)),
                    "started_by": "autopilot",
                }
            ],
        )
        self.assertIs(got["met"], True)

    def test_a_stop_after_another_failure_is_not_counted(self):
        rows = [
            unopened_row(),
            row("start", stage="plan", seconds=1),
            row(
                "end",
                stage="plan",
                seconds=2,
                outcome="failed",
                detail="the session returned nothing",
            ),
            unopened_stop(3),
        ]
        got = self.measure(rows)
        self.assertEqual(got["stops"], [])
        self.assertIs(got["met"], True)
        # Nor a stop on a spike, which gets no repair turn.
        spike = [unopened_row(stage="spike"), unopened_stop(1, "spike")]
        self.assertIsNone(self.measure(spike)["met"])

    def test_nothing_in_the_window_is_not_measured(self):
        self.assertIsNone(self.measure([row("end", stage="plan", outcome="done")])["met"])
        self.assertIsNone(
            self.measure([unopened_row(-3 * 86400), unopened_stop(-3 * 86400 + 1)])["met"]
        )
        self.assertIsNone(
            ap.measure_opening([unopened_row()], "other", "2026-01-01", "2026-12-31")["met"]
        )

    def test_the_measure_reads_the_words_stop_for_writes(self):
        said = ap.stop_for(unit(), nxt("plan", "write-plan"), unopened_row(), False, unopened=2)
        got = self.measure([unopened_row(), stop_row(said["kind"], 1, reason=said["reason"])])
        self.assertEqual([s["stage"] for s in got["stops"]], ["plan"])


class MeasuringReruns(unittest.TestCase):
    """One sample log per class."""

    def measure(self, rows):
        return ap.measure_reruns(
            rows, "w", ap.today(NOW - timedelta(days=1)), ap.today(NOW + timedelta(days=1))
        )

    def one(self, rows):
        got = self.measure(rows)
        self.assertEqual(len(got["cases"]), 1, got)
        return got["cases"][0]["class"], got["met"]

    def test_no_case_is_not_measured(self):
        self.assertIsNone(self.measure([])["met"])
        not_a_case = [
            answer_row(completes=False),
            answer_row(autopilot=False),
            answer_row(shortlisted=False),
            answer_row(status="accepted"),
        ]
        self.assertIsNone(self.measure(not_a_case)["met"])

    def test_on_time(self):
        rows = [
            answer_row(),
            stop_row(""),
            row("autopilot-pick", seconds=1),
            row("start", seconds=2),
        ]
        self.assertEqual(self.one(rows), ("on-time", True))

    def test_on_time_within_ten_minutes_when_full(self):
        rows = [answer_row(), stop_row("full", 1), row("autopilot-pick", seconds=550)]
        self.assertEqual(self.one(rows), ("on-time", True))
        self.assertEqual(
            self.one([answer_row(), row("autopilot-pick", seconds=550)]), ("late", False)
        )

    def test_late(self):
        self.assertEqual(
            self.one([answer_row(), row("start", seconds=700), stop_row("full", 800)]),
            ("late", False),
        )

    def test_held(self):
        self.assertEqual(self.one([answer_row(held=True)]), ("held", True))

    def test_reruns(self):
        self.assertEqual(self.one([answer_row(), stop_row("reruns", 1)]), ("reruns", True))

    def test_gate(self):
        self.assertEqual(
            self.one([answer_row(), stop_row("f", 1, reason="intent: idea.md is draft")]),
            ("gate", True),
        )
        late_pick = [
            answer_row(),
            row("autopilot-pick", seconds=900),
            stop_row("f", 901, reason="closed"),
        ]
        self.assertEqual(self.one(late_pick), ("gate", True))

    def test_cap(self):
        self.assertEqual(self.one([answer_row(), stop_row("cap", 1)]), ("cap", False))

    def test_another_stop(self):
        self.assertEqual(
            self.one([answer_row(), stop_row("f", 1, reason="finish and accept intent.md")]),
            ("stop:f", False),
        )
        self.assertEqual(self.one([answer_row(), stop_row("a", 1)]), ("stop:a", False))

    def test_none(self):
        self.assertEqual(
            self.one(
                [
                    answer_row(),
                    row("start", unit="0002_b", seconds=1),
                    row("start", stage="spec", seconds=2),
                ]
            ),
            ("none", False),
        )

    def test_the_window(self):
        old = answer_row(seconds=-3 * 86400)
        self.assertIsNone(self.measure([old])["met"])
        self.assertIsNone(
            ap.measure_reruns([answer_row()], "other", "2026-01-01", "2026-12-31")["met"]
        )


def ran_out_row(seconds=0, stage="plan", unit="0001_a"):
    return row("end", unit, stage, seconds, outcome="exhausted")


def ran_out_stop(seconds=0, stage="plan", unit="0001_a", note=""):
    return stop_row("e", seconds, unit, f"the last {stage} step ended exhausted" + note)


class MeasureExhausted(unittest.TestCase):
    """A stop at the first time a stage other than `ship` ran out is a violation."""

    def measure(self, rows):
        return ap.measure_exhausted(
            rows, "w", ap.today(NOW - timedelta(days=1)), ap.today(NOW + timedelta(days=1))
        )

    def test_a_stop_at_the_first_exhausted_step_is_a_violation(self):
        got = self.measure([ran_out_row(), ran_out_stop(1)])
        self.assertIs(got["met"], False)
        self.assertEqual(
            got["violations"], [{"unit": "0001_a", "stage": "plan", "at": at(timedelta(seconds=1))}]
        )
        self.assertEqual(got["exhausted"], 1)

    def test_a_second_exhausted_stop_and_a_ship_stop_are_not_violations(self):
        rows = [
            ran_out_row(),
            row("start", stage="plan", seconds=1),
            ran_out_row(2),
            ran_out_stop(3, note="; origin x"),
            ran_out_row(4, "ship", "0002_b"),
            ran_out_stop(5, "ship", "0002_b"),
        ]
        got = self.measure(rows)
        self.assertEqual((got["met"], got["violations"], got["exhausted"]), (True, [], 2))

    def test_no_exhausted_step_is_not_measured(self):
        self.assertIsNone(self.measure([row("end", stage="plan", outcome="done")])["met"])
        self.assertIsNone(self.measure([ran_out_row(0, "ship"), ran_out_stop(1, "ship")])["met"])

    def test_an_exhausted_step_before_the_window_does_not_count(self):
        got = self.measure([ran_out_row(-3 * 86400), ran_out_row(), ran_out_stop(1)])
        self.assertIs(got["met"], False)
        self.assertEqual(len(got["violations"]), 1)
        other = ap.measure_exhausted(
            [ran_out_row(), ran_out_stop(1)], "other", "2026-01-01", "2026-12-31"
        )
        self.assertIsNone(other["met"])

    def test_the_measure_reads_the_words_stop_for_writes(self):
        said = ap.stop_for(
            unit(),
            nxt("plan", "write-plan"),
            {"kind": "end", "stage": "plan", "outcome": "exhausted"},
            False,
            exhausted=2,
        )
        got = self.measure([ran_out_row(), stop_row(said["kind"], 1, reason=said["reason"])])
        self.assertEqual([v["stage"] for v in got["violations"]], ["plan"])


class AWorkspaceStopIsNoUnitsStop(unittest.TestCase):
    """The workspace's own stop is logged, as unit `""`; no measurement that reads `autopilot-stop`
    may count it for a unit."""

    def test_a_workspace_stop_record_changes_no_measurement(self):
        since, until = ap.today(NOW - timedelta(days=1)), ap.today(NOW + timedelta(days=1))
        logs = [
            [
                answer_row(),
                stop_row("", 1),
                row("autopilot-pick", seconds=2),
                row("start", seconds=3),
            ],
            [answer_row(), stop_row("a", 1)],
            [answer_row(), row("start", seconds=700), stop_row("full", 800)],
            [ran_out_row(), ran_out_stop(1)],
            [ran_out_row(), row("start", stage="plan", seconds=1), ran_out_row(2), ran_out_stop(3)],
            [
                unopened_row(),
                row("start", stage="plan", seconds=1),
                unopened_row(2),
                unopened_stop(3),
            ],
        ]
        workspace = [
            stop_row("shortlist", 0.5, unit="", reason=ap.NO_SHORTLIST),
            stop_row("f", 1.5, unit="", reason="the autopilot's pass failed: boom"),
            stop_row("", 2.5, unit=""),
        ]
        for rows in logs:
            with_it = sorted(rows + workspace, key=lambda r: r["at"])
            for measure in (
                ap.measure,
                ap.measure_reruns,
                ap.measure_exhausted,
                ap.measure_opening,
            ):
                self.assertEqual(
                    measure(with_it, "w", since, until),
                    measure(rows, "w", since, until),
                    f"{measure.__name__} on {[r['kind'] for r in rows]}",
                )

    def test_open_questions_is_the_stop_a_set(self):
        asked = unit(
            [
                {"artifact": "spec.md", "n": 1, "counted": True, "answered": False},
                {"artifact": "spec.md", "n": 2, "counted": True, "answered": True},
                {"artifact": "intent.md", "n": 1, "counted": False, "answered": False},
            ]
        )
        self.assertEqual([q["n"] for q in ap.open_questions(asked)], [1])
        self.assertEqual(ap.stop_for(asked, nxt(), None, False)["kind"], "a")
        self.assertEqual(ap.open_questions(unit()), [])


if __name__ == "__main__":
    unittest.main()
