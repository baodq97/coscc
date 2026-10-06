"""The guards, as pure functions, and the one table of reason codes."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from coscc.runner.queue import Refused
from coscc.runner.queue import Updating
from coscc.units import guards, states
from coscc.units.guards import OPEN, BadVerdict, Verdict

REPO = Path(__file__).resolve().parents[2]
LOOP = REPO / "coscc" / "loop"


def loop_lines() -> list[str]:
    """Every line of the loop's own source: where the `why` and the codes it hands out are written."""
    return [
        line
        for path in sorted(LOOP.glob("*.py"))
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


class EveryGuardIsNamed(unittest.TestCase):
    def test_every_transition_can_be_decided_by_a_guard_that_exists(self):
        for machine, table in guards.TRANSITIONS.items():
            for transition, allowed in table.items():
                self.assertTrue(allowed, f"{machine}.{transition}")
                for gid in allowed:
                    self.assertIn(gid, guards.GUARDS, f"{machine}.{transition}")


class AUnitOpensAcceptedOnlyFromABrief(unittest.TestCase):
    def test_a_brief_opens_it(self):
        self.assertEqual(guards.guard("unit-created").check({"brief": True}), OPEN)

    def test_no_brief_closes_it_with_a_code(self):
        for inputs in ({"brief": False}, {}):
            verdict = guards.guard("unit-created").check(inputs)
            self.assertFalse(verdict.open)
            self.assertEqual(verdict.reasons, ("no-brief",))

    def test_the_lane_names_it_for_the_create_transition(self):
        self.assertEqual(guards.TRANSITIONS["unit"]["create"], ("unit-created",))
        lane = states.default_lanes().lane("full")
        self.assertEqual(lane.guard_for("unit", "create"), "unit-created")


class AReviewGoesBackToDraftOnlyOnItsIncompleteRound(unittest.TestCase):
    def test_a_round_number_opens_it_and_none_closes_it_with_a_code(self):
        self.assertEqual(guards.guard("incomplete-round").check({"round": 2}), OPEN)
        for inputs in ({"round": 0}, {}):
            verdict = guards.guard("incomplete-round").check(inputs)
            self.assertFalse(verdict.open)
            self.assertEqual(verdict.reasons, ("no-round",))

    def test_the_lane_names_it_for_the_incomplete_transition(self):
        lane = states.default_lanes().lane("full")
        self.assertEqual(lane.guard_for("unit", "incomplete"), "incomplete-round")


class TheReasonTableIsClosed(unittest.TestCase):
    def test_a_closed_verdict_with_a_code_outside_the_table_is_refused(self):
        with self.assertRaises(BadVerdict):
            Verdict(False, ("ci is red",))

    def test_a_closed_verdict_names_a_reason(self):
        with self.assertRaises(BadVerdict):
            Verdict(False, ())

    def test_every_why_the_loop_writes_is_in_the_table(self):
        # The loop writes its own `why`, a literal or `code("…")`; each one must be a code here,
        # or the autopilot would meet one it cannot name.
        # Not a dependency's own `why` (`merged`, `dropped`), on a line carrying `"merged":`: it
        # describes the other unit and is no reason code.
        written = {
            code
            for line in loop_lines()
            if '"merged":' not in line
            for code in re.findall(r'"why": (?:code\()?"([a-z-]+)"', line)
        }
        self.assertGreaterEqual(len(written), 15)
        self.assertEqual(written - set(guards.REASONS), set())

    def test_every_code_the_loop_hands_out_is_in_the_table(self):
        # `gate --json` and `next` carry `reasons`, each written as `code("…")`.
        text = "\n".join(loop_lines())
        handed = set(re.findall(r'code\("([a-z-]+)"\)', text))
        self.assertGreaterEqual(
            handed,
            {
                "ci-pending",
                "ci-red",
                "ci-unfixable",
                "needs-person",
                "waiting-on",
                "recording-ship",
                "closed",
                "gate-closed",
            },
        )
        self.assertEqual(handed - set(guards.REASONS), set())
        # No code is written any other way: the only `reasons` added are `code(…)` or a `why`.
        self.assertNotRegex(text, r'codes\.(?:append|extend)\(\[?"')

    def test_a_step_refused_before_spend_is_in_the_table(self):
        for code in (
            "unit-busy",
            "updating",
            "unavailable",
            "held",
            "no-unit",
            "no-stage",
            "feature-refused",
        ):
            self.assertIn(code, guards.REASONS)

    def test_the_feature_refusal_is_the_app_s_alone(self):
        self.assertNotIn("feature-refused", "\n".join(loop_lines()))

    def test_an_integration_with_nothing_to_integrate_is_the_app_s_alone(self):
        self.assertIn("nothing-to-integrate", guards.REASONS)
        self.assertNotIn("nothing-to-integrate", "\n".join(loop_lines()))
        self.assertEqual(Refused("x", ("nothing-to-integrate",)).reasons, ("nothing-to-integrate",))

    def test_a_refusal_with_a_code_outside_the_table_is_refused(self):
        with self.assertRaises(ValueError):
            Refused("x", ("no-such",))
        self.assertEqual(Refused("x", ("unit-busy",)).reasons, ("unit-busy",))
        self.assertEqual(Updating("x").reasons, ("updating",))

    def test_no_code_is_listed_twice(self):
        self.assertEqual(len(guards.REASONS), len(set(guards.REASONS)))


class TheGuards(unittest.TestCase):
    def test_stage_result_needs_the_open_run_and_the_revision_the_app_read(self):
        ok = {"run": "r1", "open_run": "r1", "revision": "a", "computed_revision": "a"}
        self.assertEqual(guards.stage_result(ok), OPEN)
        self.assertEqual(guards.stage_result({**ok, "open_run": "r2"}).reasons, ("wrong-run",))
        self.assertEqual(
            guards.stage_result({**ok, "computed_revision": "b"}).reasons, ("stale-revision",)
        )
        self.assertEqual(guards.stage_result({}).reasons, ("wrong-run", "stale-revision"))

    def test_review_round_needs_the_head_the_app_recorded(self):
        self.assertEqual(guards.review_round({"run": "r", "open_run": "r", "head": "h"}), OPEN)
        self.assertEqual(guards.review_round({"run": "r", "open_run": "r"}).reasons, ("no-head",))

    def test_impl_may_claim_only_an_open_finding(self):
        self.assertEqual(guards.impl_claim({"claims": ["F1"], "open_findings": ["F1", "F2"]}), OPEN)
        self.assertEqual(guards.impl_claim({"claims": [], "open_findings": []}), OPEN)
        self.assertEqual(
            guards.impl_claim({"claims": ["F3"], "open_findings": ["F1"]}).reasons,
            ("not-open-finding",),
        )

    def test_only_a_person_skips(self):
        self.assertEqual(guards.skip_decision({"authority": "person"}), OPEN)
        self.assertNotEqual(guards.skip_decision({"authority": "delegated"}), OPEN)
        for who in ("agent", "code", None):
            self.assertEqual(
                guards.skip_decision({"authority": who}).reasons, ("agent-cannot-skip",)
            )

    def test_plan_waits_for_every_unmeasured_item_to_hold(self):
        self.assertEqual(guards.spike_holds({"unmeasured": []}), OPEN)
        self.assertEqual(
            guards.spike_holds({"unmeasured": ["U1"], "verdicts": {"U1": "holds"}}), OPEN
        )
        self.assertEqual(
            guards.spike_holds({"unmeasured": ["U1"], "verdicts": {}}).reasons, ("spike-missing",)
        )
        self.assertEqual(
            guards.spike_holds({"unmeasured": ["U1"], "verdicts": {"U1": "fails"}}).reasons,
            ("spike-fails",),
        )

    def test_impl_waits_on_a_dependency_that_has_not_merged(self):
        self.assertEqual(
            guards.dependency_merged({"depends": [{"ref": "a", "merged": True}]}), OPEN
        )
        self.assertEqual(
            guards.dependency_merged({"depends": [{"ref": "a", "merged": False}]}).reasons,
            ("waiting-on",),
        )

    def test_impl_waits_on_a_backlog_dependency_as_on_a_depends_on(self):
        merged = {"ref": "a", "merged": True}
        waiting = {"ref": "b", "merged": False, "source": "backlog"}
        self.assertEqual(
            guards.dependency_merged({"depends": [merged, waiting]}).reasons, ("waiting-on",)
        )
        self.assertEqual(
            guards.dependency_merged({"depends": [merged, {**waiting, "merged": True}]}), OPEN
        )

    def test_ship_needs_green_ci_and_a_pass_of_the_head_it_merges(self):
        ok = {"ci": "green", "verdict": "pass", "head": "h", "reviewed_head": "h"}
        self.assertEqual(guards.ship_ready(ok), OPEN)
        for ci, code in (
            ("pending", "ci-pending"),
            ("red", "ci-red"),
            ("unfixable", "ci-unfixable"),
        ):
            self.assertEqual(guards.ship_ready({**ok, "ci": ci}).reasons, (code,))
        self.assertEqual(guards.ship_ready({**ok, "reviewed_head": "g"}).reasons, ("head-moved",))
        self.assertEqual(
            guards.ship_ready({**ok, "verdict": "changes-requested"}).reasons,
            ("changes-requested",),
        )
        self.assertEqual(guards.ship_ready({**ok, "verdict": None}).reasons, ("review-incomplete",))

    def test_ship_takes_the_gates_clean_rebase_of_the_reviewed_head(self):
        """The gate's clean rebase stands in for a round of the new head, but only for the two
        commits the guard itself holds."""
        reviewed, head = "a" * 40, "b" * 40
        ok = {"ci": "green", "verdict": "pass", "head": head, "reviewed_head": reviewed}
        self.assertEqual(guards.ship_ready(ok).reasons, ("head-moved",))
        self.assertEqual(
            guards.ship_ready({**ok, "rebased": {"reviewed": reviewed, "head": head}}), OPEN
        )
        self.assertEqual(
            guards.ship_ready({**ok, "rebased": {"reviewed": reviewed[:7], "head": head}}),
            OPEN,
            "a round read from prose names a short SHA",
        )
        for other in (
            {"reviewed": "c" * 40, "head": head},
            {"reviewed": reviewed, "head": "c" * 40},
            {"reviewed": reviewed[:3], "head": head},
            {"head": head},
            "yes",
        ):
            self.assertEqual(
                guards.ship_ready({**ok, "rebased": other}).reasons, ("head-moved",), other
            )
        self.assertEqual(
            guards.ship_ready(
                {**ok, "ci": "red", "rebased": {"reviewed": reviewed, "head": head}}
            ).reasons,
            ("ci-red",),
        )

    def test_a_round_naming_the_head_by_a_short_sha_passes_it(self):
        """The loop's `ROUND_META` takes a `Reviewed:` of 7 to 40 characters."""
        head = "a" * 40
        ok = {"ci": "green", "verdict": "pass", "head": head}
        for reviewed in (head[:7], head[:39]):
            self.assertEqual(guards.ship_ready({**ok, "reviewed_head": reviewed}), OPEN, reviewed)
        for reviewed in (head[:6], "b" * 7):
            self.assertEqual(
                guards.ship_ready({**ok, "reviewed_head": reviewed}).reasons,
                ("head-moved",),
                reviewed,
            )

    def test_a_run_that_submitted_nothing_is_not_done(self):
        self.assertEqual(guards.run_submitted({"submitted": True}), OPEN)
        self.assertEqual(guards.run_submitted({}).reasons, ("no-submission",))

    def test_the_pull_request_machine_reads(self):
        self.assertEqual(guards.branch_named({"branch_ok": True}), OPEN)
        self.assertEqual(guards.branch_named({}).reasons, ("bad-branch",))
        self.assertEqual(guards.ci_at_head({"head": "h", "read_head": "h"}), OPEN)
        self.assertEqual(
            guards.ci_at_head({"head": "h", "read_head": "g"}).reasons, ("head-moved",)
        )
        self.assertEqual(guards.merge_read({"merge_commit": "m"}), OPEN)
        self.assertEqual(guards.merge_read({}).reasons, ("not-merged",))
        self.assertEqual(guards.close_read({"state": "CLOSED"}), OPEN)
        self.assertEqual(guards.close_read({"state": "OPEN"}).reasons, ("not-closed",))


class ThePackagedLaneUsesEveryGuardAMachineNeeds(unittest.TestCase):
    def test_lane_full_picks_a_guard_for_every_transition(self):
        lane = states.default_lanes().lane("full")
        for machine, table in guards.TRANSITIONS.items():
            for transition in table:
                self.assertIn(lane.guard_for(machine, transition), guards.GUARDS)

    def test_lane_full_runs_idea_intent_impl_and_review_always(self):
        # The four the lane may never pass over.
        lane = states.default_lanes().lane("full")
        always = {stage for stage, when in lane.path if when == "always"}
        self.assertEqual(always, {"idea", "intent", "impl", "review"})
        self.assertEqual(dict(lane.path)["spike"], "if-unmeasured")
        self.assertEqual(lane.end, "shipped")


if __name__ == "__main__":
    unittest.main()
