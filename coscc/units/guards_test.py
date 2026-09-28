"""`0136` R1, R11: the guards, as pure functions, and the one table of reason codes."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from coscc.units import guards, states
from coscc.units.guards import OPEN, BadVerdict, Verdict

REPO = Path(__file__).resolve().parents[2]
COS = REPO / ".claude" / "scripts" / "cos.mjs"


class EveryGuardIsNamed(unittest.TestCase):
    def test_each_has_an_id_and_a_one_sentence_english_label(self):
        for gid, g in guards.GUARDS.items():
            self.assertEqual(g.id, gid)
            self.assertRegex(g.id, r"^[a-z]+(-[a-z]+)*$")
            self.assertTrue(g.label.endswith("."), g.id)
            self.assertEqual(g.label.count(". "), 0, g.id)

    def test_every_transition_can_be_decided_by_a_guard_that_exists(self):
        for machine, table in guards.TRANSITIONS.items():
            for transition, allowed in table.items():
                self.assertTrue(allowed, f"{machine}.{transition}")
                for gid in allowed:
                    self.assertIn(gid, guards.GUARDS, f"{machine}.{transition}")


class TheReasonTableIsClosed(unittest.TestCase):
    def test_a_closed_verdict_with_a_code_outside_the_table_is_refused(self):
        with self.assertRaises(BadVerdict):
            Verdict(False, ("ci is red",))

    def test_a_closed_verdict_names_a_reason(self):
        with self.assertRaises(BadVerdict):
            Verdict(False, ())

    def test_every_why_cos_mjs_writes_is_in_the_table(self):
        # Until step 5 makes `cos.mjs` a client of this module, it still writes its own `why`;
        # each one must already be a code here, or the autopilot would meet one it cannot name.
        # Not a dependency's own `why` (`merged`, `dropped`), on a line carrying `merged:`: it
        # describes the other unit and is no reason code.
        written = {
            code
            for line in COS.read_text(encoding="utf-8").splitlines()
            if "merged:" not in line
            for code in re.findall(r"why: '([a-z-]+)'", line)
        }
        self.assertGreaterEqual(len(written), 15)
        self.assertEqual(written - set(guards.REASONS), set())

    def test_no_code_is_listed_twice(self):
        self.assertEqual(len(guards.REASONS), len(set(guards.REASONS)))


class TheGuards(unittest.TestCase):
    def test_stage_result_needs_the_open_run_and_the_revision_the_app_read(self):
        ok = {"run": "r1", "open_run": "r1", "revision": "a", "computed_revision": "a"}
        self.assertEqual(guards.stage_result(ok), OPEN)
        self.assertEqual(guards.stage_result({**ok, "open_run": "r2"}).reasons, ("wrong-run",))
        self.assertEqual(guards.stage_result({**ok, "computed_revision": "b"}).reasons, ("stale-revision",))
        self.assertEqual(guards.stage_result({}).reasons, ("wrong-run", "stale-revision"))

    def test_review_round_needs_the_head_the_app_recorded(self):
        self.assertEqual(guards.review_round({"run": "r", "open_run": "r", "head": "h"}), OPEN)
        self.assertEqual(guards.review_round({"run": "r", "open_run": "r"}).reasons, ("no-head",))

    def test_impl_may_claim_only_an_open_finding(self):
        self.assertEqual(guards.impl_claim({"claims": ["F1"], "open_findings": ["F1", "F2"]}), OPEN)
        self.assertEqual(guards.impl_claim({"claims": [], "open_findings": []}), OPEN)
        self.assertEqual(
            guards.impl_claim({"claims": ["F3"], "open_findings": ["F1"]}).reasons, ("not-open-finding",)
        )

    def test_only_a_person_or_their_delegate_skips(self):
        self.assertEqual(guards.skip_decision({"authority": "person"}), OPEN)
        self.assertEqual(guards.skip_decision({"authority": "delegated"}), OPEN)
        for who in ("agent", "code", None):
            self.assertEqual(guards.skip_decision({"authority": who}).reasons, ("agent-cannot-skip",))

    def test_plan_waits_for_every_unmeasured_item_to_hold(self):
        self.assertEqual(guards.spike_holds({"unmeasured": []}), OPEN)
        self.assertEqual(guards.spike_holds({"unmeasured": ["U1"], "verdicts": {"U1": "holds"}}), OPEN)
        self.assertEqual(guards.spike_holds({"unmeasured": ["U1"], "verdicts": {}}).reasons, ("spike-missing",))
        self.assertEqual(
            guards.spike_holds({"unmeasured": ["U1"], "verdicts": {"U1": "fails"}}).reasons, ("spike-fails",)
        )

    def test_impl_waits_on_a_dependency_that_has_not_merged(self):
        self.assertEqual(guards.dependency_merged({"depends": [{"ref": "a", "merged": True}]}), OPEN)
        self.assertEqual(
            guards.dependency_merged({"depends": [{"ref": "a", "merged": False}]}).reasons, ("waiting-on",)
        )

    def test_ship_needs_green_ci_and_a_pass_of_the_head_it_merges(self):
        ok = {"ci": "green", "verdict": "pass", "head": "h", "reviewed_head": "h"}
        self.assertEqual(guards.ship_ready(ok), OPEN)
        for ci, code in (("pending", "ci-pending"), ("red", "ci-red"), ("unfixable", "ci-unfixable")):
            self.assertEqual(guards.ship_ready({**ok, "ci": ci}).reasons, (code,))
        self.assertEqual(guards.ship_ready({**ok, "reviewed_head": "g"}).reasons, ("head-moved",))
        self.assertEqual(
            guards.ship_ready({**ok, "verdict": "changes-requested"}).reasons, ("changes-requested",)
        )
        self.assertEqual(guards.ship_ready({**ok, "verdict": None}).reasons, ("review-incomplete",))

    def test_a_run_that_submitted_nothing_is_not_done(self):
        self.assertEqual(guards.run_submitted({"submitted": True}), OPEN)
        self.assertEqual(guards.run_submitted({}).reasons, ("no-submission",))

    def test_the_pull_request_machine_reads(self):
        self.assertEqual(guards.branch_named({"branch_ok": True}), OPEN)
        self.assertEqual(guards.branch_named({}).reasons, ("bad-branch",))
        self.assertEqual(guards.ci_at_head({"head": "h", "read_head": "h"}), OPEN)
        self.assertEqual(guards.ci_at_head({"head": "h", "read_head": "g"}).reasons, ("head-moved",))
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
        # R14: the four the lane may never pass over.
        lane = states.default_lanes().lane("full")
        always = {stage for stage, when in lane.path if when == "always"}
        self.assertEqual(always, {"idea", "intent", "impl", "review"})
        self.assertEqual(dict(lane.path)["spike"], "if-unmeasured")
        self.assertEqual(lane.end, "shipped")

    def test_ci_poll_seconds_is_sixty(self):
        # R23: the precedent of `CI_REFRESH` (`coscc/service/steps.py`), chosen, not measured.
        self.assertEqual(states.default_lanes().ci_poll_seconds, 60.0)


if __name__ == "__main__":
    unittest.main()
