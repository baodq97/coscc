"""`0035` plan step 4: the pure half of `coscc/integrate.py`, one table per function."""

from __future__ import annotations

import unittest
from pathlib import Path

from coscc import integrate as ig

HEAD = "a" * 40
NEW = "b" * 40
MAIN = "c" * 40


def row(mergeable="MERGEABLE", head=HEAD):
    return {"number": 7, "headRefOid": head, "headRefName": "feat/x", "mergeable": mergeable}


class ClassifyGivesAllFiveStates(unittest.TestCase):
    def test_the_five(self):
        rec = {"head_after": HEAD}
        cases = [
            (("gh: not logged in", 0, MAIN, None, None), "unknown"),
            ((row(), "rev-list failed", MAIN, None, None), "unknown"),
            ((row("CONFLICTING"), 3, MAIN, None, None), "conflicting"),
            ((row(), 0, MAIN, rec, [{"name": "tests", "bucket": "fail"}]), "red-after-integration"),
            ((row(), 2, MAIN, None, None), "behind"),
            ((row("UNKNOWN"), 2, MAIN, None, None), "behind"),
            ((row(), 0, MAIN, None, None), "current"),
            ((row(), 0, MAIN, rec, [{"name": "tests", "bucket": "pass"}]), "current"),
            ((row(), 0, MAIN, rec, "gh checks failed"), "unknown"),
        ]
        seen = set()
        for args, want in cases:
            with self.subTest(args=args):
                got = ig.classify(*args)
                self.assertEqual(got["state"], want)
                seen.add(got["state"])
        self.assertEqual(seen, set(ig.STATES))

    def test_an_error_carries_the_words(self):
        self.assertEqual(ig.classify("gh: HTTP 401", 0, MAIN, None)["reason"], "gh: HTTP 401")

    def test_checks_are_asked_only_on_the_integrations_own_head(self):
        self.assertFalse(ig.needs_checks(row(), None))
        self.assertFalse(ig.needs_checks(row(head=NEW), {"head_after": HEAD}))
        self.assertFalse(ig.needs_checks(row(), {"head_after": ""}))
        self.assertTrue(ig.needs_checks(row(), {"head_after": HEAD}))


class RefusalNamesTheFirstConditionMissing(unittest.TestCase):
    OK = dict(in_window=True, active=False, clean=True, branch_ok=True,
              local_head=HEAD, pr_head=HEAD, state="behind")

    def test_each_condition(self):
        cases = [
            ({"in_window": False}, "not between pr and ship"),
            ({"active": True}, "a step is running"),
            ({"clean": False}, "uncommitted"),
            ({"branch_ok": False}, "not on the unit's branch"),
            ({"local_head": NEW}, "not the pull request's head"),
            ({"state": "current"}, "nothing to integrate"),
            ({"state": "unknown"}, "nothing to integrate"),
        ]
        for change, want in cases:
            with self.subTest(change=change):
                self.assertIn(want, ig.refusal(**{**self.OK, **change}))

    def test_all_hold(self):
        for state in ig.BUTTON_STATES:
            self.assertEqual(ig.refusal(**{**self.OK, "state": state}), "")

    def test_the_order_is_the_specs(self):
        self.assertIn("not between", ig.refusal(**{**self.OK, "in_window": False, "active": True}))


class Warnings(unittest.TestCase):
    def test_each_line_only_when_true(self):
        self.assertEqual(ig.warnings([], "accepted", False, "W"), [])
        self.assertIn("ship gate closes", ig.warnings([{"verdict": "pass"}], "accepted", False, "W")[0])
        self.assertIn("offers review", ig.warnings([{"verdict": "changes-requested"}], "changes-requested", False, "W")[0])
        self.assertEqual(ig.warnings([], "", True, "W"), ["W"])


class Related(unittest.TestCase):
    UNITS = [{"name": "0030_a", "pr": {"number": 41}}, {"name": "0035_x", "pr": {"number": 50}},
             {"name": "0040_z", "pr": None}]

    def test_both_groups(self):
        commits = [
            {"sha": "1" * 40, "subject": "feat: a (#41)", "files": ["coscc/runner.py", "README.md"]},
            {"sha": "2" * 40, "subject": "fix: b (#99)", "files": ["coscc/runner.py"]},
            {"sha": "3" * 40, "subject": "docs: c (#42)", "files": ["docs/x.md"]},
            {"sha": "4" * 40, "subject": "no number", "files": ["coscc/runner.py"]},
        ]
        others = [
            {"unit": "0036_b", "files": ["coscc/runner.py"]},
            {"unit": "0037_c", "files": ["other.py"]},
            {"unit": "0038_d", "files": None},
            {"unit": "0035_x", "files": ["coscc/runner.py"]},
        ]
        rel = ig.related(commits, ["coscc/runner.py"], self.UNITS, others, "0035_x")
        self.assertEqual([(m["subject"], m["unit"]) for m in rel["merged"]],
                         [("feat: a (#41)", "0030_a"), ("fix: b (#99)", None), ("no number", None)])
        self.assertEqual(rel["open"], [{"unit": "0036_b", "files": ["coscc/runner.py"]},
                                       {"unit": "0038_d", "files": None}])
        self.assertEqual(ig.related_units(rel), ["0030_a", "0036_b", "0038_d"])

    def test_read_paths_are_the_own_folder_and_three_files_each(self):
        rel = {"merged": [{"unit": "0030_a"}], "open": []}
        paths = ig.read_paths(Path("/u"), "0035_x", rel)
        self.assertEqual(paths, ("/u/0035_x", "/u/0030_a/intent.md", "/u/0030_a/spec.md", "/u/0030_a/plan.md"))


class NeedsPersonAndOutcome(unittest.TestCase):
    def test_parse(self):
        reply = "Tried.\n- [needs-person] A keeps x, B drops x\n[needs-person] second\nnot [needs-person] this"
        self.assertEqual(ig.parse_needs_person(reply), ["A keeps x, B drops x", "second"])

    def test_the_outcome_is_read_from_git_not_the_reply(self):
        self.assertEqual(ig.outcome_of_session(HEAD, NEW, ""), "pushed")
        self.assertEqual(ig.outcome_of_session(HEAD, HEAD, "I pushed it."), "failed")
        self.assertEqual(ig.outcome_of_session(HEAD, HEAD, "[needs-person] x"), "needs-person")


class Record(unittest.TestCase):
    def test_shape(self):
        rec = ig.record(workspace="w", unit="0035_x", pr=7, mode="mechanical", head_before=HEAD,
                        head_after=NEW, origin_sha=MAIN, outcome="pushed")
        self.assertEqual(rec["kind"], "integration")
        self.assertEqual(rec["stage"], "integrate")
        self.assertEqual(rec["head_after"], NEW)

    def test_head_after_is_empty_unless_pushed(self):
        rec = ig.record(workspace="w", unit="u", pr=7, mode="agent", head_before=HEAD,
                        head_after=NEW, origin_sha=MAIN, outcome="needs-person")
        self.assertEqual(rec["head_after"], "")

    def test_an_unknown_outcome_is_refused(self):
        with self.assertRaises(ValueError):
            ig.record(workspace="w", unit="u", pr=7, mode="agent", head_before=HEAD,
                      head_after="", origin_sha=MAIN, outcome="ok")

    def test_the_review_section_says_whose_word_it_is(self):
        text = ig.describe_for_review({"mode": "mechanical", "head_after": NEW})
        self.assertIn("An integration since the last round", text)
        self.assertIn("not by a person", text)
        self.assertIn("an agent session", ig.describe_for_review({"mode": "agent"}))


class ThePrompt(unittest.TestCase):
    def test_it_carries_the_lease_the_lists_and_the_artifacts(self):
        rel = {"merged": [{"sha": "1" * 40, "subject": "s (#41)", "unit": "0030_a", "files": ["f"]}],
               "open": [{"unit": "0036_b", "files": None}]}
        text = ig.build_prompt(skill="RULES", unit="0035_x", branch="feat/x", pr=7, state="conflicting",
                               reason="r", head_before=HEAD, origin_sha=MAIN, rel=rel,
                               units_root=Path("/u"), own_artifacts={"intent.md": "INTENT"})
        for want in ("RULES", f"--force-with-lease=feat/x:{HEAD}", MAIN, "0030_a", "no local commit",
                     "/u/0030_a/plan.md", "INTENT"):
            self.assertIn(want, text)


if __name__ == "__main__":
    unittest.main()
