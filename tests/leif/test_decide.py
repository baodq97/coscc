"""The autopilot's decisions, with no session, no `gh` and no run log on disk."""

import unittest
from datetime import datetime, timedelta, timezone

from coscc.github import prmachine
from coscc.leif import decide
from coscc.units.board import open_questions
from coscc.agent import pack
from tests.agent.edit import set_part

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
        self.assertTrue(decide.is_recording_ship(RECORDING))
        self.assertFalse(decide.is_recording_ship(MERGING))
        self.assertFalse(decide.is_recording_ship(nxt("ship", RECORDING_SHIP)))
        self.assertFalse(decide.is_recording_ship({}))

    def test_a_gate_refusal_is_read_by_its_codes(self):
        class Refused(Exception):
            reasons = ("missing", "ci-pending")

        self.assertTrue(decide.is_ci_pending(Refused("anything at all")))
        self.assertFalse(
            decide.is_ci_pending(Exception("CI has not finished on #7: t — wait, then ask again"))
        )

    def test_a_skip_no_person_decided_stops_for_a_person(self):
        # The loop names no stage for it, since running the spec again would only skip again; the
        # stop is `b`, a person's, never `f`'s "no stage it can name".
        said = nxt(
            "",
            "spec.md is skipped by agent, not by a person — …",
            reasons=["agent-cannot-skip"],
        )
        self.assertTrue(decide.needs_a_person(said))
        self.assertEqual(decide.stop_for(unit(), said, None, False)["kind"], "b")


class AUnitWaitingOnADependency(unittest.TestCase):
    """`next` answers `stage: ""` while `impl` waits on a merge."""

    def test_a_unit_waiting_on_a_dependency_is_passed_over_not_stopped_and_no_notice_is_sent(self):
        said = nxt("", "waiting on api/0001_backend to merge", reasons=["dependency", "waiting-on"])
        # No stop is what keeps a notice from going out: only a stop is recorded and told.
        self.assertIsNone(decide.stop_for(unit(), said, None, False))
        self.assertEqual(decide.reason_for(said, "", None), ("dependency", said["action"]))
        self.assertIn("dependency", decide.REASONS)
        # Anything else with no stage is still the stop `f` it was.
        self.assertEqual(
            decide.stop_for(unit(), nxt("", "finish and accept intent.md"), None, False)["kind"],
            "f",
        )


class Stops(unittest.TestCase):
    def test_nothing_to_stop_on_a_stage_to_run(self):
        self.assertIsNone(decide.stop_for(unit(), nxt("spec", "write-spec"), None, False))

    def test_finished_rejected_and_held_are_not_stops(self):
        self.assertIsNone(
            decide.stop_for(unit(), nxt("", "finished", reasons=["finished"]), None, False)
        )
        self.assertIsNone(
            decide.stop_for(
                unit(),
                nxt("", "closed — spec rejected", reasons=["rejected", "closed"]),
                None,
                False,
            )
        )
        self.assertIsNone(
            decide.stop_for(unit(), nxt("", "paused — x", hold={"state": "paused"}), None, False)
        )

    def test_a_open_question_lists_each(self):
        qs = [
            {"artifact": "spec.md", "n": 2, "answered": False, "counted": True},
            {"artifact": "spec.md", "n": 1, "answered": True, "counted": True},
            {"artifact": "intent.md", "n": 9, "answered": False, "counted": False},
        ]
        got = decide.stop_for(unit(qs), nxt("plan", "write-plan"), None, False)
        self.assertEqual(got["kind"], "a")
        self.assertIn("spec.md question 2", got["reason"])
        self.assertNotIn("intent.md", got["reason"])

    def test_b_waiting_and_rounds_used(self):
        self.assertEqual(
            decide.stop_for(unit(), nxt("", "answer F2", waiting=["F2"]), None, False)["kind"], "b"
        )
        said = "needs a person — review used 3 of 3 rounds and findings are still open"
        got = decide.stop_for(unit(), nxt("", said, reasons=["needs-person"]), None, False)
        self.assertEqual((got["kind"], got["reason"]), ("b", said))
        # The words alone are no longer read: with no code it is the stop `f`.
        self.assertEqual(decide.stop_for(unit(), nxt("", said), None, False)["kind"], "f")

    def test_c_ship_only_when_allowed(self):
        self.assertEqual(decide.stop_for(unit(), nxt("ship", "ship"), None, False)["kind"], "c")
        self.assertIsNone(decide.stop_for(unit(), nxt("ship", "ship"), None, True))

    def test_d_gebo_needs_a_person(self):
        last = {
            "kind": "integration",
            "outcome": "needs-person",
            "needs_person": ["both sides edit x.py"],
        }
        got = decide.stop_for(unit(), nxt("review", "x"), last, True)
        self.assertEqual(got["kind"], "d")
        self.assertIn("both sides edit x.py", got["reason"])

    def test_e_a_step_that_did_not_end_done(self):
        for outcome in ("failed", "stopped"):
            got = decide.stop_for(
                unit(), nxt("impl", "x"), {"kind": "end", "stage": "impl", "outcome": outcome}, True
            )
            self.assertEqual(got["kind"], "e", outcome)
        self.assertIsNone(
            decide.stop_for(
                unit(), nxt("pr", "x"), {"kind": "end", "stage": "impl", "outcome": "done"}, True
            )
        )

    def test_e_failed_cancelled_and_stopped_ships_stop_before_a_recording_ship(self):
        for outcome in ("failed", "cancelled", "stopped"):
            last = {"kind": "end", "stage": "ship", "outcome": outcome}
            self.assertEqual(decide.stop_for(unit(), RECORDING, last, True)["kind"], "e", outcome)

    def test_e_failed_cancelled_and_stopped_stop_whatever_the_count(self):
        for outcome in ("failed", "cancelled", "stopped"):
            last = {"kind": "end", "stage": "plan", "outcome": outcome}
            self.assertEqual(
                decide.stop_for(unit(), nxt("plan", "write-plan"), last, False)["kind"],
                "e",
                outcome,
            )

    def test_e_a_step_paused_at_its_ceiling_stops_with_its_own_code(self):
        """The autopilot never raises a ceiling: only a person goes on, or reruns the stage."""
        for stage in ("impl", "plan", "ship"):
            paused = {"kind": "end", "stage": stage, "outcome": "paused-budget"}
            got = decide.stop_for(unit(), nxt(stage, "x"), paused, True)
            self.assertEqual(
                got,
                {
                    "kind": "e",
                    "reason": f"the last {stage} step paused at its ceiling",
                    "code": "budget-reached",
                },
            )
            # Nor does a second one, or a recording ship, pass it by.
            self.assertEqual(
                decide.stop_for(unit(), RECORDING, paused, True, unopened=1)["code"],
                "budget-reached",
            )

    def test_e_an_integration_that_failed_or_that_the_autopilot_had_refused(self):
        failed = {"kind": "integration", "outcome": "failed", "detail": "gh down"}
        self.assertEqual(decide.stop_for(unit(), nxt("review", "x"), failed, True)["kind"], "e")
        mine = {"kind": "integration", "outcome": "refused", "started_by": "autopilot"}
        self.assertEqual(decide.stop_for(unit(), nxt("review", "x"), mine, True)["kind"], "e")
        theirs = {"kind": "integration", "outcome": "refused", "started_by": "person"}
        self.assertIsNone(decide.stop_for(unit(), nxt("review", "x"), theirs, True))

    NOTHING = {
        "kind": "integration",
        "outcome": "refused",
        "started_by": "autopilot",
        "code": "nothing-to-integrate",
        "detail": "the unit is current against origin/main ccccccc (fetched), which has nothing "
        "to integrate",
    }

    def test_an_integration_with_nothing_to_integrate_is_no_stop_and_next_runs(self):
        """0157: CI green on the head, `review` next, and the autopilot's integration found the
        unit current. No stop: the pass queues `review`."""
        green = nxt("review", "CI is green: write-review")
        self.assertIsNone(decide.stop_for(unit(), green, self.NOTHING, True))
        self.assertIsNone(decide.stop_for(unit(), green, self.NOTHING, False))
        # It is no last word: what follows is decided as with none.
        for next_, may_ship in (
            (nxt("ship", "ship"), False),
            (nxt("", "finish and accept impl.md"), True),
            (nxt("", "answer F1", waiting=["F1"]), True),
        ):
            self.assertEqual(
                decide.stop_for(unit(), next_, self.NOTHING, may_ship),
                decide.stop_for(unit(), next_, None, may_ship),
                next_,
            )
        self.assertEqual(
            decide.stop_for(unit(), nxt("ship", "ship"), self.NOTHING, False)["kind"], "c"
        )

    def test_e_every_other_refusal_of_the_autopilots_integration_still_stops(self):
        """No code is a refusal a person looks at, and so is a record from before the code."""
        for detail in (
            "the unit's worktree has uncommitted changes",
            "the unit's worktree is not on the unit's branch",
            "the unit has no worktree to integrate in",
            "0010_a is busy: a spec step is running since 2026-09-24T01:02:03+00:00",
            "the pull request's head could not be read, so the unit is unknown: nothing to integrate",
            "the local head (bbbbbbb) is not the pull request's head (aaaaaaa)",
        ):
            with self.subTest(detail=detail):
                refused = {**self.NOTHING, "code": "", "detail": detail}
                got = decide.stop_for(unit(), nxt("review", "x"), refused, True)
                self.assertEqual(
                    got, {"kind": "e", "reason": f"the last integration was refused: {detail}"}
                )
        old = {k: v for k, v in self.NOTHING.items() if k != "code"}
        self.assertEqual(decide.stop_for(unit(), nxt("review", "x"), old, True)["kind"], "e")
        # `failed` and `needs-person` stop whatever their code.
        failed = {**self.NOTHING, "outcome": "failed"}
        self.assertEqual(decide.stop_for(unit(), nxt("review", "x"), failed, True)["kind"], "e")
        gebo = {**self.NOTHING, "outcome": "needs-person", "needs_person": ["x.py"]}
        self.assertEqual(decide.stop_for(unit(), nxt("review", "x"), gebo, True)["kind"], "d")

    MERGING_NOW = nxt("", "ship is merging #7 — wait", reasons=["ship-merging"])

    def test_a_ship_still_merging_is_no_stop_while_its_ship_runs(self):
        self.assertIsNone(decide.stop_for(unit(), self.MERGING_NOW, None, True, shipping=True))
        self.assertTrue(decide.is_ship_merging(self.MERGING_NOW))

    def test_e_a_ship_still_merging_with_no_ship_running(self):
        self.assertEqual(
            decide.stop_for(unit(), self.MERGING_NOW, None, True),
            {"kind": "e", "reason": "ship requested a merge and recorded no outcome"},
        )
        # Not the stop `f` on the action's words.
        done = {
            "kind": prmachine.RECORD_KIND,
            "stage": "ship",
            "outcome": "done",
            "result": "merged",
        }
        self.assertEqual(decide.stop_for(unit(), self.MERGING_NOW, done, True)["kind"], "e")

    def test_a_ship_that_failed_still_stops_while_next_reads_merging(self):
        """A `ship` the guard refused, or one that failed, is still its stop `e`; a merge GitHub
        refused is still `f`."""
        refused = {
            "kind": prmachine.RECORD_KIND,
            "stage": "ship",
            "outcome": "failed",
            "result": "refused",
            "reasons": ["head-moved"],
            "detail": "",
        }
        self.assertEqual(
            decide.stop_for(unit(), self.MERGING_NOW, refused, True),
            {"kind": "e", "reason": "the last ship was refused: head-moved"},
        )
        github = {**refused, "result": "failed", "detail": "not mergeable", "merge_refused": True}
        self.assertEqual(decide.stop_for(unit(), self.MERGING_NOW, github, True)["kind"], "f")

    def test_e_a_pr_or_ship_the_machine_failed_or_refused_and_none_once_one_did_its_work(self):
        """With no `end`, the machine's record is the last word."""
        refused = {
            "kind": prmachine.RECORD_KIND,
            "stage": "ship",
            "outcome": "failed",
            "result": "refused",
            "reasons": ["head-moved"],
            "detail": "",
        }
        got = decide.stop_for(unit(), nxt("ship", "x"), refused, True)
        self.assertEqual(got, {"kind": "e", "reason": "the last ship was refused: head-moved"})
        failed = {
            "kind": prmachine.RECORD_KIND,
            "stage": "pr",
            "outcome": "failed",
            "result": "failed",
            "detail": "gh down",
        }
        self.assertEqual(
            decide.stop_for(unit(), nxt("pr", "x"), failed, True)["reason"],
            "the last pr was failed: gh down",
        )
        done = {"kind": prmachine.RECORD_KIND, "stage": "pr", "outcome": "done", "result": "opened"}
        self.assertIsNone(decide.stop_for(unit(), nxt("review", "x"), done, True))

    def test_f_a_merge_github_refused_stops_on_what_gh_said(self):
        """A merge GitHub refused stops `f` on what `gh` said, so the pass may still integrate a
        unit the refusal left behind `main`."""
        said = "the head branch is not up to date with the base branch"
        refused = {
            "kind": prmachine.RECORD_KIND,
            "stage": "ship",
            "outcome": "failed",
            "result": "failed",
            "detail": said,
            "merge_refused": True,
        }
        self.assertEqual(
            decide.stop_for(unit(), nxt("", "behind"), refused, True),
            {"kind": "f", "reason": f"ship was refused: {said}"},
        )
        failed = {**refused, "merge_refused": False}
        self.assertEqual(decide.stop_for(unit(), nxt("", "behind"), failed, True)["kind"], "e")

    def test_e_screenshots_that_could_not_be_taken_again_and_none_once_they_were(self):
        # No retry; a retake that is taken, run by a person, lifts it.
        failed = {"kind": "screens", "stage": "review", "outcome": "failed", "detail": "exited 2"}
        got = decide.stop_for(unit(), nxt("review", "x"), failed, True)
        self.assertEqual(got, {"kind": "e", "reason": decide.SCREENS_FAILED})
        taken = {**failed, "outcome": "taken"}
        self.assertIsNone(decide.stop_for(unit(), nxt("review", "x"), taken, True))

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
        self.assertEqual(decide.since_integration(rows, "w", "0010_a"), [impl, end])
        self.assertIsNone(decide.since_integration(rows, "w", "0011_b"))
        self.assertIsNone(decide.since_integration(rows, "other", "0010_a"))
        closed = rows + [r("start", stage="review"), r("start")]
        self.assertIsNone(decide.since_integration(closed, "w", "0010_a"))
        self.assertEqual(decide.since_integration(closed + [first], "w", "0010_a"), [])

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
        self.assertEqual(decide.after_own_integration(red, mine, [], fix), ("impl", None))
        ran = [
            {"kind": "start", "stage": "impl", "started_by": "autopilot"},
            {"kind": "end", "stage": "impl"},
        ]
        still = ("", {"kind": "e", "reason": decide.STILL_RED})
        self.assertEqual(decide.after_own_integration(red, mine, ran, fix), still)
        # The `impl` pushed, so the board no longer reads the head as the integrated one.
        self.assertEqual(decide.after_own_integration({"state": "current"}, mine, ran, fix), still)
        self.assertEqual(decide.after_own_integration({"state": "behind"}, mine, ran, fix), still)
        # Green again, or not sent to `impl` for CI: the rest of the pass decides.
        self.assertIsNone(
            decide.after_own_integration(
                {"state": "current"}, mine, ran, nxt("review", "write-review")
            )
        )
        self.assertIsNone(decide.after_own_integration({"state": "current"}, mine, [], fix))

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
            decide.after_own_integration(red, mine, [], nxt("", "finish and accept plan.md")), today
        )
        # Past the window, today's stop too, `impl` or not.
        self.assertEqual(
            decide.after_own_integration(
                red, mine, None, nxt("impl", "CI is red on #7: t", reasons=["ci-red"])
            ),
            today,
        )
        self.assertIsNone(
            decide.after_own_integration(
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
        self.assertIsNone(decide.after_own_integration(red, theirs, [], fix))
        self.assertIsNone(decide.after_own_integration(red, theirs, ran, fix))
        self.assertIsNone(
            decide.after_own_integration(
                red, {"kind": "integration", "outcome": "pushed"}, ran, fix
            )
        )
        self.assertIsNone(decide.after_own_integration(red, None, None, fix))
        refused = {"kind": "integration", "outcome": "refused", "started_by": "autopilot"}
        self.assertIsNone(decide.after_own_integration(red, refused, [], fix))

    def test_after_own_integration_counts_only_the_autopilots_impl(self):
        red = {"state": "red-after-integration"}
        mine = {"kind": "integration", "outcome": "pushed", "started_by": "autopilot"}
        fix = nxt("impl", "CI is red on #7: t — back to impl", reasons=["ci-red"])
        theirs = [
            {"kind": "start", "stage": "impl", "started_by": "person"},
            {"kind": "start", "stage": "impl"},
            {"kind": "start", "stage": "pr", "started_by": "autopilot"},
        ]
        self.assertEqual(decide.after_own_integration(red, mine, theirs, fix), ("impl", None))

    def test_is_ci_red_reads_the_code_and_not_the_words(self):
        self.assertTrue(
            decide.is_ci_red(nxt("impl", "anything", reasons=["changes-requested", "ci-red"]))
        )
        self.assertFalse(
            decide.is_ci_red(
                nxt("impl", "CI is red on #7: tests — back to impl: fix on the branch and push")
            )
        )
        self.assertFalse(decide.is_ci_red(nxt("", "x", reasons=["ci-pending"])))
        self.assertFalse(decide.is_ci_red(None))

    UNOPENED = {
        "kind": "end",
        "stage": "plan",
        "outcome": "failed",
        "detail": "plan.md lacks its opening: no `Status:` line in its header",
    }

    def test_a_first_opening_failure_of_a_prose_stage_is_no_stop(self):
        self.assertIsNone(
            decide.stop_for(unit(), nxt("plan", "write-plan"), self.UNOPENED, False, unopened=1)
        )
        # A repair turn that failed too keeps the first refusal on its first line.
        repaired_not = {
            **self.UNOPENED,
            "detail": self.UNOPENED["detail"] + "\n--- plan.md: the repair turn failed ---",
        }
        self.assertIsNone(
            decide.stop_for(unit(), nxt("plan", "write-plan"), repaired_not, False, unopened=1)
        )
        self.assertEqual(
            decide.stop_for(unit(), nxt("plan", "write-plan"), self.UNOPENED, False)["kind"], "e"
        )

    def test_a_failure_for_another_reason_after_an_opening_failure_stops(self):
        other = {**self.UNOPENED, "detail": "the session returned nothing"}
        self.assertEqual(
            decide.stop_for(unit(), nxt("plan", "write-plan"), other, False, unopened=1)["kind"],
            "e",
        )
        # And the count of the other reason forgives nothing here.
        self.assertEqual(
            decide.stop_for(unit(), nxt("plan", "write-plan"), self.UNOPENED, False, unopened=2)[
                "kind"
            ],
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
            decide.stop_for(unit(), nxt("spike", "write-spike"), spike, False, unopened=1)["kind"],
            "e",
        )

    def test_ci_pending_is_not_a_stop(self):
        said = nxt(
            "",
            "CI has not finished on #7: test — wait, then ask again",
            reasons=["missing", "ci-pending"],
        )
        self.assertTrue(decide.is_ci_pending(said))
        self.assertIsNone(decide.stop_for(unit(), said, None, True))
        self.assertFalse(decide.is_ci_pending(nxt("impl", "x", reasons=["ci-red"])))

    def test_a_second_opening_failure_stops_e_with_the_same_words(self):
        self.assertEqual(
            decide.stop_for(unit(), nxt("plan", "write-plan"), self.UNOPENED, False, unopened=2),
            {"kind": "e", "reason": "the last plan step ended failed"},
        )


class TheDaysMoney(unittest.TestCase):
    def test_every_end_of_today_whoever_started_it(self):
        rows = [
            {"kind": "end", "at": at(), "cost_usd": 1.5, "workspace": "w1"},
            {"kind": "end", "at": at(-timedelta(minutes=5)), "cost_usd": 2.0, "workspace": "w2"},
            {"kind": "start", "at": at(), "workspace": "w1"},
        ]
        self.assertEqual(
            decide.spent_today(rows, NOW), {"known": 3.5, "estimated": 0.0, "estimated_count": 0}
        )

    def test_an_end_without_cost_is_not_the_cap_reached(self):
        rows = [
            {"kind": "end", "at": at(), "cost_usd": 1.0},
            {"kind": "end", "at": at(), "stage": "spec"},
        ]
        got = decide.spent_today(rows, NOW)
        self.assertEqual((got["known"], got["estimated"]), (1.0, decide.estimate("spec")))

    def test_the_cap_reads_from_before_the_day_and_every_open_start(self):
        since = decide.cap_since(NOW)
        midnight = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
        self.assertLessEqual(since, midnight.astimezone(timezone.utc).isoformat())
        self.assertLessEqual(since, (NOW - decide.OPEN_FOR).astimezone(timezone.utc).isoformat())
        self.assertGreater(since, (NOW - timedelta(days=3)).astimezone(timezone.utc).isoformat())

    def test_a_new_day_by_the_machines_clock_starts_again(self):
        local_midnight = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday = (local_midnight - timedelta(seconds=1)).astimezone(timezone.utc).isoformat()
        rows = [
            {"kind": "end", "at": yesterday, "cost_usd": 49.0},
            {"kind": "end", "at": yesterday},
        ]
        self.assertEqual(
            decide.spent_today(rows, NOW), {"known": 0.0, "estimated": 0.0, "estimated_count": 0}
        )

    def test_a_day_like_2026_09_25_fits_or_is_capped(self):
        rows = [{"kind": "end", "at": at(), "stage": "impl", "outcome": "failed"}] + [
            {"kind": "end", "at": at(), "stage": "integrate", "outcome": "done"} for _ in range(4)
        ]
        estimated = decide.spent_on(rows, decide.today(NOW))["estimated"]
        self.assertEqual(estimated, 48.0)
        spec = {"unit": "0010_a", "stage": "spec", "files": None, "need": 4.0, "rank": 1}
        got = decide.pick([spec], [], 4, 80.0 - (20.0 + estimated) - 0.0)
        self.assertEqual((got["chosen"], got["capped"]), ([spec], []))
        got = decide.pick([spec], [], 4, 80.0 - (30.0 + estimated) - 0.0)
        self.assertEqual((got["chosen"], got["capped"]), ([], [spec]))

    def test_a_raised_budget_is_reserved_and_estimated_in_full(self):
        set_part("impl", "variants.novel.ceilings.usd", 50.0)
        self.assertEqual(decide.reservation("impl"), 50.0)
        set_part("impl", "variants.novel.ceilings.usd", None)
        pack.write("impl", "ceilings", {"turns": 120, "usd": 30.0})
        self.assertEqual(decide.reservation("impl"), 30.0)
        set_part("spec", "ceilings.usd", 12.5)
        self.assertEqual(decide.reservation("spec"), 12.5)
        set_part("integrate", "ceilings.usd", 20.0)
        self.assertEqual(decide.reservation("integrate"), 20.0)
        # A lowered one is held at what it now is.
        set_part("review", "ceilings.usd", 1.0)
        self.assertEqual(decide.reservation("review"), 1.0)
        rows = [{"kind": "end", "at": at(), "stage": "impl", "outcome": "failed"}]
        pack.write("impl", "ceilings", {"turns": 120, "usd": 40.0})
        self.assertEqual(decide.spent_on(rows, decide.today(NOW))["estimated"], 40.0)
        set_part("spec", "ceilings.usd", 45.0)
        self.assertEqual(decide.estimate("idea"), 45.0)
        set_part("spec", "ceilings.usd", 9.0)
        start = {"kind": "start", "workspace": "w", "unit": "0010_a", "stage": "spec", "at": at()}
        self.assertEqual(decide.reserved([start], NOW), 9.0)

    def test_an_end_without_cost_counts_its_stages_ceiling(self):
        rows = [
            {"kind": "end", "at": at(), "stage": "impl", "outcome": "failed"},
            {"kind": "end", "at": at(), "stage": "review", "outcome": "done"},
            {"kind": "end", "at": at(), "stage": "integrate", "outcome": "done"},
        ]
        got = decide.spent_on(rows, decide.today(NOW))
        self.assertEqual(
            (got["known"], got["estimated"], got["estimated_count"]), (0.0, 16.0 + 4.0 + 8.0, 3)
        )

    def test_a_stage_without_a_ceiling_counts_the_tables_largest(self):
        ceilings = []
        for found in pack.rows().values():
            ceilings.append(found.get("ceilings") or {})
            ceilings += [v.get("ceilings") or {} for v in (found.get("variants") or {}).values()]
        largest = max(c.get("usd") or 0.0 for c in ceilings)
        self.assertGreater(largest, 0)
        self.assertEqual(decide.estimate("idea"), largest)
        self.assertEqual(largest, 16.0)

    def test_an_integration_record_is_never_added(self):
        rows = [
            {"kind": "end", "at": at(), "stage": "integrate", "cost_usd": 2.5},
            {"kind": "integration", "at": at(), "mode": "agent"},
            {"kind": "integration", "at": at(), "mode": "mechanical"},
        ]
        got = decide.spent_on(rows, decide.today(NOW))
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
        self.assertEqual(set(decide.open_starts(rows, NOW)), {("w", "0010_a")})
        self.assertEqual(decide.reserved(rows, NOW), 16.0)
        # A step this process holds that has not written its `start` yet counts too, once.
        self.assertEqual(
            decide.reserved(rows, NOW, [("w", "0010_a", "impl"), ("w", "0013_d", "review")]), 20.0
        )
        # A `pr` or `ship` opens no session, so it is counted at nothing.
        self.assertEqual(
            decide.reserved(rows, NOW, [("w", "0013_d", "pr"), ("w", "0014_e", "ship")]), 16.0
        )

    def test_a_reservation_is_the_largest_budget_a_label_can_give(self):
        self.assertEqual(decide.reservation("impl"), 16.0)
        self.assertEqual(decide.reservation("review"), 4.0)
        self.assertEqual(decide.reservation("integrate"), 8.0)
        self.assertEqual(decide.reservation("no-such-stage"), 0.0)


class Scheduling(unittest.TestCase):
    def c(self, unit, stage, files=None, need=1.0, rank=None):
        return {
            "unit": unit,
            "stage": stage,
            "files": files,
            "need": need,
            "rank": decide.unit_number(unit) if rank is None else rank,
        }

    def test_highest_rank_first_up_to_max_parallel(self):
        got = decide.pick(
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
        # The one `max_parallel` held back says so.
        self.assertEqual(got["held"], {"0010_a": ("full", "")})

    def test_running_steps_count_person_ones_included(self):
        got = decide.pick(
            [self.c("0010_a", "spec")],
            [{"unit": "0001_x", "stage": "review", "files": None}],
            1,
            100.0,
        )
        self.assertEqual(got["chosen"], [])

    def test_a_unit_already_running_is_skipped(self):
        got = decide.pick(
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
        got = decide.pick([a, b, c], [], 4, 100.0)
        self.assertEqual([x["unit"] for x in got["chosen"]], ["0010_a", "0012_c"])
        self.assertEqual(got["held"], {"0011_b": ("overlap", "0010_a")})

    def test_unknown_files_overlap_with_everything(self):
        got = decide.pick(
            [self.c("0010_a", "impl", None), self.c("0011_b", "impl", {"a/b.py"})], [], 4, 100.0
        )
        self.assertEqual([x["unit"] for x in got["chosen"]], ["0010_a"])
        got = decide.pick(
            [self.c("0011_b", "impl", {"a/b.py"})],
            [{"unit": "0001_x", "stage": "integrate", "files": None}],
            4,
            100.0,
        )
        self.assertEqual(got["chosen"], [])

    def test_prose_stages_ignore_files(self):
        got = decide.pick(
            [self.c("0010_a", "review"), self.c("0011_b", "pr")],
            [{"unit": "0001_x", "stage": "impl", "files": None}],
            4,
            100.0,
        )
        self.assertEqual(len(got["chosen"]), 2)

    def test_one_ship_in_the_workspace(self):
        got = decide.pick([self.c("0010_a", "ship"), self.c("0011_b", "ship")], [], 4, 100.0)
        self.assertEqual([x["unit"] for x in got["chosen"]], ["0010_a"])
        self.assertEqual(got["held"], {"0011_b": ("ship-busy", "0010_a")})
        got = decide.pick(
            [self.c("0011_b", "ship")],
            [{"unit": "0010_a", "stage": "ship", "files": None}],
            4,
            100.0,
        )
        self.assertEqual((got["chosen"], got["held"]), ([], {"0011_b": ("ship-busy", "0010_a")}))

    def test_an_open_pr_on_the_same_file_holds_impl_until_it_merges(self):
        b = self.c("0011_b", "impl", {"a.py"})
        pr = {"unit": "0010_a", "number": 7, "files": {"a.py", "c.py"}}
        got = decide.pick([b], [], 4, 100.0, open_prs=[pr])
        self.assertEqual((got["chosen"], got["held"]), ([], {"0011_b": ("overlap-pr", "#7")}))
        got = decide.pick([b], [], 4, 100.0, open_prs=[])
        self.assertEqual([x["unit"] for x in got["chosen"]], ["0011_b"])

    def test_a_unit_with_its_own_open_pr_is_not_held_by_another(self):
        # Back at impl after `changes-requested`: two open pull requests on one file must not
        # wait on each other for ever.
        b = self.c("0011_b", "impl", {"a.py"})
        prs = [
            {"unit": "0010_a", "number": 7, "files": {"a.py"}},
            {"unit": "0011_b", "number": 8, "files": {"a.py"}},
        ]
        got = decide.pick([b], [], 4, 100.0, open_prs=prs)
        self.assertEqual([x["unit"] for x in got["chosen"]], ["0011_b"])

    def test_a_pr_whose_files_could_not_be_read_overlaps_every_file(self):
        got = decide.pick(
            [self.c("0011_b", "impl", {"a.py"})],
            [],
            4,
            100.0,
            open_prs=[{"unit": "0010_a", "number": 7, "files": None}],
        )
        self.assertEqual(got["held"], {"0011_b": ("overlap-pr", "#7")})

    def test_an_open_pr_holds_only_impl(self):
        got = decide.pick(
            [self.c("0011_b", "review", {"a.py"})],
            [],
            4,
            100.0,
            open_prs=[{"unit": "0010_a", "number": 7, "files": {"a.py"}}],
        )
        self.assertEqual([x["unit"] for x in got["chosen"]], ["0011_b"])

    def test_the_cap_holds_back_what_does_not_fit(self):
        got = decide.pick(
            [self.c("0010_a", "impl", {"a"}, 16.0), self.c("0011_b", "review", None, 2.0)],
            [],
            4,
            3.0,
        )
        self.assertEqual([x["unit"] for x in got["chosen"]], ["0011_b"])
        self.assertEqual([x["unit"] for x in got["capped"]], ["0010_a"])
        got = decide.pick([self.c("0011_b", "review", None, 2.0)], [], 4, -1.0)
        self.assertEqual((got["chosen"], len(got["capped"])), ([], 1))

    def test_overlap_pr_is_a_code_of_the_one_reason_table(self):
        from coscc.units import guards

        self.assertIn("overlap-pr", decide.REASONS)
        self.assertIn("overlap-pr", guards.REASONS)


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
        got = decide.measure(self.rows(), "w", day, day)
        [u] = got["units"]
        self.assertTrue(u["reached"])
        self.assertEqual((u["autopilot"], u["person"]), (2, 3))
        self.assertEqual([o["stage"] for o in u["outside"]], ["impl"])
        self.assertEqual(got["met"], [])

    def test_a_unit_with_none_outside_is_met(self):
        day = NOW.date().isoformat()
        rows = [r for r in self.rows() if r["stage"] != "impl"]
        self.assertEqual(decide.measure(rows, "w", day, day)["met"], ["0010_a"])

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
        got = decide.measure(rows, "w", day, day)
        self.assertEqual(got["met"], ["0010_a"])
        self.assertEqual(got["units"][0]["person"], 1)

    def test_outside_the_dates_or_the_workspace_nothing_counts(self):
        self.assertEqual(
            decide.measure(self.rows(), "other", "2000-01-01", "2100-01-01")["units"], []
        )
        self.assertEqual(decide.measure(self.rows(), "w", "2000-01-01", "2000-01-02")["units"], [])


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
        got = decide.stop_for(row, {"stage": "plan", "action": "write-plan"}, None, False)
        self.assertEqual(got["kind"], "a")
        self.assertIn("spec.md question 1, spec.md question 2", got["reason"])
        self.assertNotIn("question 3", got["reason"])

    def test_a_running_start_is_reserved_at_its_stages_most(self):
        rows = [
            {"kind": "start", "workspace": "w", "unit": "0010_a", "stage": "impl", "at": at()},
            {"kind": "start", "workspace": "w", "unit": "0011_b", "stage": "spec", "at": at()},
        ]
        self.assertEqual(
            decide.reserved(rows, NOW), decide.reservation("impl") + decide.reservation("spec")
        )


class ReasonsAndPassed(unittest.TestCase):
    """A reason is read off what the pass had, and never made up."""

    def test_stop(self):
        stop = {"kind": "a", "reason": "open questions: spec.md question 2"}
        self.assertEqual(
            decide.reason_for(nxt("spec"), "spec", stop),
            ("stop", "a: open questions: spec.md question 2"),
        )

    def test_passed_skips_what_was_chosen_before_it_in_the_pass(self):
        reasons = {"0003_c": ("running", "impl")}
        got = decide.passed_for(["0001_a", "0003_c", "0002_b"], ["0001_a", "0002_b"], reasons)
        self.assertEqual(got, [[], [{"unit": "0003_c", "reason": "running", "detail": "impl"}]])

    def test_passed_keeps_the_shortlists_order(self):
        reasons = {"0005_e": ("held", "paused"), "0001_a": ("ci", "CI"), "0004_d": ("missing", "")}
        [got] = decide.passed_for(["0005_e", "0001_a", "0004_d", "0002_b"], ["0002_b"], reasons)
        self.assertEqual([p["unit"] for p in got], ["0005_e", "0001_a", "0004_d"])

    def test_held(self):
        hold = {"state": "paused", "reason": "later", "by": "Leif", "date": "2026-09-25"}
        self.assertEqual(decide.reason_for(nxt(hold=hold), "", None), ("held", "paused"))


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
        self.assertIsNone(decide.stop_for(unit(), rerun, None, False))
        self.assertEqual(
            decide.stop_for(unit(), nxt("", "finish and accept intent.md"), None, False)["kind"],
            "f",
        )

    def test_two_reruns_after_answers(self):
        rows = [
            row("start"),
            row("answer"),
            row("start"),
            row("answer"),
            row("start"),
            row("answer"),
        ]
        self.assertEqual(decide.reruns_of(rows, "w", "0001_a", "intent"), 2)

    def test_no_answer_between_is_no_rerun(self):
        self.assertEqual(decide.reruns_of([row("start"), row("start")], "w", "0001_a", "intent"), 0)

    def test_another_units_or_stages_answer_is_not_counted(self):
        rows = [
            row("start"),
            row("answer", unit="0002_b"),
            row("answer", stage="spec"),
            row("answer", workspace="other"),
            row("start"),
        ]
        self.assertEqual(decide.reruns_of(rows, "w", "0001_a", "intent"), 0)

    def test_whoever_started_counts(self):
        rows = [
            row("start", started_by="person"),
            row("answer"),
            row("start", started_by="autopilot"),
        ]
        self.assertEqual(decide.reruns_of(rows, "w", "0001_a", "intent"), 1)

    def test_only_an_answer_after_the_last_start_is_new(self):
        self.assertTrue(
            decide.answered_since_start([row("start"), row("answer")], "w", "0001_a", "intent")
        )
        self.assertTrue(decide.answered_since_start([row("answer")], "w", "0001_a", "intent"))
        # A rerun that ended `draft` still read as answered: no new answer, whatever `reruns_of` says.
        rows = [row("start"), row("answer"), row("start")]
        self.assertFalse(decide.answered_since_start(rows, "w", "0001_a", "intent"))
        self.assertEqual(decide.reruns_of(rows, "w", "0001_a", "intent"), 1)
        self.assertFalse(decide.answered_since_start([row("start")], "w", "0001_a", "intent"))
        others = [
            row("start"),
            row("answer", unit="0002_b"),
            row("answer", stage="spec"),
            row("answer", workspace="other"),
        ]
        self.assertFalse(decide.answered_since_start(others, "w", "0001_a", "intent"))

    def test_completes_only_on_the_last_question_of_a_rerun_stage(self):
        qs = [
            {"artifact": "intent.md", "n": 1, "answered": True},
            {"artifact": "intent.md", "n": 2, "answered": False},
        ]
        u = self.board_row(qs)
        self.assertTrue(decide.answer_completes(u, "intent.md", {2}))
        self.assertFalse(decide.answer_completes(u, "intent.md", set()))
        self.assertFalse(decide.answer_completes(self.board_row([]), "intent.md", {1}))

    def test_completes_counts_every_block_of_one_write(self):
        qs = [
            {"artifact": "spec.md", "n": 1, "answered": False},
            {"artifact": "spec.md", "n": 2, "answered": False},
        ]
        u = self.board_row(qs)
        self.assertFalse(decide.answer_completes(u, "spec.md", {1}))
        self.assertTrue(decide.answer_completes(u, "spec.md", {1, 2}))

    def test_impl_completes_and_review_never_does(self):
        """`impl` is one of the stages the loop lists; `review` is not."""
        qs = [
            {"artifact": "impl.md", "n": 1, "answered": True},
            {"artifact": "review.md", "n": 1, "answered": True},
        ]
        u = self.board_row(qs)
        self.assertTrue(decide.answer_completes(u, "impl.md", {1}))
        self.assertFalse(decide.answer_completes(u, "review.md", {"F1"}))

    def test_a_board_read_without_after_answers_completes_nothing(self):
        """An older loop sends no list, and no stage is guessed in its place."""
        qs = [{"artifact": "intent.md", "n": 1, "answered": True}]
        u = {k: v for k, v in self.board_row(qs).items() if k != "after_answers"}
        self.assertFalse(decide.answer_completes(u, "intent.md", {1}))


class TriesOnAHead(unittest.TestCase):
    """`impl` queued with a note of the app's is tried `MAX_TRIES` times on one head."""

    @staticmethod
    def pick(**extra):
        return {
            "kind": "autopilot-pick",
            "workspace": "w",
            "unit": "0001_a",
            "stage": "impl",
            **extra,
        }

    @staticmethod
    def start(head, unit="0001_a", by="autopilot"):
        return {
            "kind": "start",
            "workspace": "w",
            "unit": unit,
            "stage": "impl",
            "head": head,
            "started_by": by,
        }

    def test_each_pick_with_a_note_on_the_same_head_is_a_try(self):
        rows = [self.pick(continued=True), self.start("h1")]
        self.assertEqual(decide.tries_on_head(rows, "w", "0001_a"), 1)
        rows += [self.pick(ci_note="CI is red"), self.start("h1")]
        self.assertEqual(decide.tries_on_head(rows, "w", "0001_a"), 2)

    def test_a_pick_with_no_note_and_what_is_not_the_unit_s_is_none(self):
        rows = [
            self.pick(),
            self.start("h1"),
            {**self.pick(continued=True), "unit": "0002_b"},
            {**self.pick(continued=True), "workspace": "x"},
            self.start("h1", "0002_b"),
        ]
        self.assertEqual(decide.tries_on_head(rows, "w", "0001_a"), 0)

    def test_the_count_starts_again_when_the_head_changes(self):
        rows = [
            self.pick(continued=True),
            self.start("h1"),
            self.pick(continued=True),
            self.start("h1"),
            self.pick(continued=True),
            self.start("h2"),
        ]
        # The third was made on `h2`, a head nothing had been tried on before it.
        self.assertEqual(decide.tries_on_head(rows, "w", "0001_a"), 1)
        self.assertEqual(decide.tries_on_head(rows[:4], "w", "0001_a"), 2)

    def test_a_pick_whose_step_never_began_is_no_try(self):
        # Refused at once, or by the gate: no `start` follows it, and the next one is one try.
        rows = [self.pick(continued=True), self.start("h1"), self.pick(continued=True)]
        self.assertEqual(decide.tries_on_head(rows, "w", "0001_a"), 1)
        rows += [self.pick(continued=True), self.start("h1")]
        self.assertEqual(decide.tries_on_head(rows, "w", "0001_a"), 2)

    def test_a_persons_start_after_a_pick_is_no_try(self):
        rows = [self.pick(continued=True), self.start("h1", by="person")]
        self.assertEqual(decide.tries_on_head(rows, "w", "0001_a"), 0)

    def test_a_head_the_pr_machine_reads_that_the_last_start_did_not_see_is_a_new_one(self):
        rows = [
            self.pick(ci_note="x"),
            self.start("h1"),
            self.pick(ci_note="x"),
            self.start("h1"),
        ]
        self.assertEqual(decide.tries_on_head(rows, "w", "0001_a", "h1"), 2)
        self.assertEqual(decide.tries_on_head(rows, "w", "0001_a", "h2"), 0)

    def test_the_third_try_on_a_head_is_stop_e_that_says_how_many(self):
        stop = decide.tries_stop("impl", 2)
        self.assertEqual(stop["kind"], "e")
        self.assertIn("2 times", stop["reason"])
        self.assertEqual(decide.MAX_TRIES, 2)
        self.assertIn("e", decide.STOP_KINDS)


class NotesOfTheApp(unittest.TestCase):
    def test_the_note_of_a_red_ci_names_the_red_required_checks_and_the_head(self):
        checks = [
            {"name": "tests", "bucket": "fail"},
            {"name": "lint", "bucket": "pass"},
            {"name": "build", "bucket": "cancel"},
        ]
        self.assertEqual(
            decide.ci_note("0123456789abcdef", checks),
            "CI is red at 0123456789ab: tests, build failed. Fix them and push.",
        )
        self.assertEqual(
            decide.ci_note("", []), "CI is red: the required checks failed. Fix them and push."
        )

    def test_a_draft_impl_next_says_to_go_on_with_is_no_stop_f(self):
        draft = {**nxt("", "impl.md is still a draft", reasons=["draft"]), "continue": "impl"}
        self.assertTrue(decide.continues(draft))
        self.assertIsNone(decide.stop_for(unit(), draft, None, False))
        self.assertEqual(
            decide.stop_for(
                unit(), nxt("", "impl.md is still a draft", reasons=["draft"]), None, False
            )["kind"],
            "f",
        )
        self.assertFalse(decide.continues({**draft, "continue": "plan"}))


class AfterARefusal(unittest.TestCase):
    """What the gate's refusal of an attempt the autopilot queued makes of the next pass."""

    @staticmethod
    def refused(code, stage="impl", ago=timedelta(seconds=1)):
        return {"stage": stage, "code": code, "at": at(-ago)}

    def test_a_refusal_is_the_stop_f_of_the_same_stage_with_its_code(self):
        stop, why = decide.after_refusal(self.refused("no-worktree"), "impl", NOW)
        self.assertEqual(
            (stop, why),
            (
                {
                    "kind": "f",
                    "reason": "impl was refused: no-worktree",
                    "code": "no-worktree",
                },
                None,
            ),
        )

    def test_a_code_the_gate_has_not_is_a_stop_with_no_code(self):
        stop, _ = decide.after_refusal(self.refused("invalid"), "impl", NOW)
        self.assertEqual(stop, {"kind": "f", "reason": "impl was refused: invalid"})

    def test_no_refusal_or_another_stage_is_no_part_of_it(self):
        self.assertEqual(decide.after_refusal(None, "impl", NOW), (None, None))
        self.assertEqual(
            decide.after_refusal(self.refused("no-worktree", "review"), "impl", NOW), (None, None)
        )

    def test_ci_pending_and_updating_make_no_stop_and_hold_for_a_while(self):
        self.assertEqual(
            decide.after_refusal(self.refused("ci-pending"), "impl", NOW), (None, ("ci", ""))
        )
        self.assertEqual(
            decide.after_refusal(self.refused("updating"), "impl", NOW), (None, ("running", ""))
        )
        late = decide.REFUSAL_HOLD + timedelta(seconds=1)
        self.assertEqual(
            decide.after_refusal(self.refused("ci-pending", ago=late), "impl", NOW), (None, None)
        )

    def test_a_read_of_the_pull_requests_ends_the_hold_and_unit_busy_has_none(self):
        self.assertEqual(
            decide.after_refusal(self.refused("ci-pending"), "impl", NOW, fresh=True), (None, None)
        )
        self.assertEqual(decide.after_refusal(self.refused("unit-busy"), "impl", NOW), (None, None))

    def test_an_integration_with_nothing_to_integrate_is_no_stop_and_no_wait(self):
        refused = self.refused("nothing-to-integrate", "integrate")
        self.assertEqual(decide.after_refusal(refused, "integrate", NOW), (None, None))
        # Another refusal of the integration is still the stop `f`.
        stop, _ = decide.after_refusal(self.refused("invalid", "integrate"), "integrate", NOW)
        self.assertEqual(stop, {"kind": "f", "reason": "integrate was refused: invalid"})


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

    def test_another_workspace_unit_stage_outcome_or_detail_is_not_counted(self):
        rows = [
            unopened_row(workspace="other"),
            unopened_row(unit="0002_b"),
            unopened_row(stage="spec"),
            unopened_row(outcome="stopped"),
            unopened_row(detail="the session returned nothing"),
            unopened_row(detail="spec.md lacks its opening: no `# Spec:` title"),
            row("start", stage="plan"),
        ]
        self.assertEqual(decide.unopened_of(rows, "w", "0001_a", "plan"), 0)

    def test_counts_every_such_failed_end_whoever_started_it(self):
        rows = [unopened_row(started_by="person"), unopened_row(1, started_by="autopilot")]
        self.assertEqual(decide.unopened_of(rows, "w", "0001_a", "plan"), 2)


def unopened_stop(seconds=0, stage="plan", unit="0001_a", note=""):
    return stop_row("e", seconds, unit, f"the last {stage} step ended failed" + note)


def ran_out_row(seconds=0, stage="plan", unit="0001_a"):
    return row("end", unit, stage, seconds, outcome="paused-budget")


def ran_out_stop(seconds=0, stage="plan", unit="0001_a", note=""):
    return stop_row("e", seconds, unit, f"the last {stage} step paused at its ceiling" + note)


class AWorkspaceStopIsNoUnitsStop(unittest.TestCase):
    """The workspace's own stop is logged, as unit `""`; no measurement that reads `autopilot-stop`
    may count it for a unit."""

    def test_a_workspace_stop_record_changes_no_measurement(self):
        since, until = decide.today(NOW - timedelta(days=1)), decide.today(NOW + timedelta(days=1))
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
            stop_row("shortlist", 0.5, unit="", reason=decide.NO_SHORTLIST),
            stop_row("f", 1.5, unit="", reason="the autopilot's pass failed: boom"),
            stop_row("", 2.5, unit=""),
        ]
        for rows in logs:
            with_it = sorted(rows + workspace, key=lambda r: r["at"])
            self.assertEqual(
                decide.measure(with_it, "w", since, until),
                decide.measure(rows, "w", since, until),
                str([r["kind"] for r in rows]),
            )

    def test_open_questions_is_the_stop_a_set(self):
        asked = unit(
            [
                {"artifact": "spec.md", "n": 1, "counted": True, "answered": False},
                {"artifact": "spec.md", "n": 2, "counted": True, "answered": True},
                {"artifact": "intent.md", "n": 1, "counted": False, "answered": False},
            ]
        )
        self.assertEqual([q["n"] for q in open_questions(asked)], [1])
        self.assertEqual(decide.stop_for(asked, nxt(), None, False)["kind"], "a")
        self.assertEqual(open_questions(unit()), [])


if __name__ == "__main__":
    unittest.main()


class AfterASessionLimit(unittest.TestCase):
    """A step the account's session limit stopped waits for its reset, then runs again."""

    @staticmethod
    def end(resets_at="", minutes=0.0, outcome="session-limit"):
        return {
            "kind": "end",
            "workspace": "w",
            "unit": "0001_a",
            "stage": "impl",
            "outcome": outcome,
            "resets_at": resets_at,
            "at": at(-timedelta(minutes=minutes)),
        }

    def test_it_is_no_stop_e(self):
        self.assertIsNone(decide.stop_for(unit(), nxt("impl", "write-impl"), self.end(), False))

    def test_it_waits_before_its_reset_and_runs_after(self):
        later = (NOW + timedelta(minutes=30)).isoformat()
        last = self.end(later)
        self.assertEqual(
            decide.after_session_limit(last, [last], NOW), (None, ("session-limit", later))
        )
        self.assertEqual(
            decide.after_session_limit(last, [last], NOW + timedelta(minutes=31)), (None, None)
        )

    def test_with_no_reset_said_it_runs_on_the_next_pass(self):
        last = self.end()
        self.assertEqual(decide.after_session_limit(last, [last], NOW), (None, None))

    def test_the_third_of_a_day_is_the_stop_e(self):
        ends = [self.end(minutes=m) for m in (20, 10, 1)]
        self.assertEqual(decide.after_session_limit(ends[1], ends[:2], NOW), (None, None))
        stop, why = decide.after_session_limit(ends[2], ends, NOW)
        self.assertEqual((stop["kind"], why), ("e", None))
        # Another day's are not counted.
        old = [self.end(minutes=60 * 24 * 2), self.end(minutes=60 * 24 * 2), ends[2]]
        self.assertEqual(decide.after_session_limit(ends[2], old, NOW), (None, None))

    def test_any_other_last_record_is_no_part_of_it(self):
        self.assertEqual(
            decide.after_session_limit(self.end(outcome="done"), [], NOW), (None, None)
        )
        self.assertEqual(decide.after_session_limit(None, [], NOW), (None, None))

    def test_what_the_cap_holds_is_not_queued(self):
        """A run again after the reset still goes through the cap."""
        got = decide.pick(
            [{"unit": "0001_a", "stage": "impl", "files": set(), "rank": 1, "need": 5.0}],
            [],
            4,
            0.0,
        )
        self.assertEqual(([c["unit"] for c in got["capped"]], got["chosen"]), (["0001_a"], []))


class AConflictWhileItsStepRuns(unittest.TestCase):
    def test_integrated_after_or_by_a_person(self):
        self.assertEqual(decide.conflict_running("impl", True), ("conflict-running", "impl"))
        self.assertEqual(decide.conflict_running("impl", False), ("conflict-person", "impl"))
