"""Tests for the table that decides what a step may do.

`plan.md` Risk 3 is the concern these stand against, and it is not fully answerable: a
first-word allowlist does not bound what `git` can be told to do. What is testable is that
the obvious ways past it are closed, that writes cannot leave the workspace, and that a
tool nobody granted is refused whatever declared it — which is the shape that was measured.
"""

from __future__ import annotations

import inspect
import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from coscc.agent import policy
from coscc.agent.policy import READ_TOOLS, Grant, check_command, grant_for, grant_for_step

IMPL = grant_for("impl")


def says(grant, tool, tool_input, roots=("/tmp/ws",), agent_id=None, **places) -> str:
    """What `critical` says of one call, the session's places being `roots` and `places`."""
    return policy.critical(grant, policy.Places(roots=roots, **places), tool, tool_input, agent_id)


# The deny list's own reading is still `integrate`'s, and is pinned here on a grant that holds
# nothing else.
MERGING = Grant(
    tools=policy.EXEC_TOOLS, commands=("git", "gh", "node"), denied=policy.MERGE_IS_SHIPS
)


class OnlyImplAndOnlyAutonomous(unittest.TestCase):
    def test_impl_carries_tools_when_it_is_set_to_run_itself(self):
        self.assertTrue(IMPL.opens_anything)
        self.assertIn("Write", IMPL.tools)
        self.assertIn("Bash", IMPL.tools)
        self.assertGreater(IMPL.max_turns, 1)
        self.assertGreater(IMPL.max_budget_usd, 0)

    def test_a_stage_the_table_does_not_name_carries_nothing(self):
        """Was `test_the_same_stage_in_manual_carries_nothing`.

        Md` `## Answers`, answer 1: grants follow the stage, not the mode, so there is no `manual`
        left to carry nothing. What stays locked is a stage the table does not name."""
        self.assertEqual(grant_for("idea"), Grant(submits=True, max_turns=policy.SUBMIT_TURNS))
        self.assertEqual(grant_for("no-such-stage"), Grant())

    def test_impl_writes_its_own_artifact_and_prose_stages_do_not(self):
        self.assertFalse(IMPL.app_writes_artifact)
        self.assertTrue(grant_for("spec").app_writes_artifact)


class SubmitIsTheOneToolAddedToAProseStage(unittest.TestCase):
    """The prose stages gain exactly `mcp__cos__submit` beyond their old grant, and that tool writes
    nothing and runs nothing."""

    def test_every_prose_stage_holding_a_result_gains_submit_and_nothing_else(self):
        from coscc.units import submit

        self.assertEqual(set(policy.SUBMITTING), {*submit.STAGE_RESULT, submit.ROUND})
        self.assertEqual(policy.SUBMIT_TOOL, submit.NAME)
        for stage in policy.SUBMITTING:
            g = grant_for(stage)
            self.assertTrue(g.submits, stage)
            old = policy.GRANTS.get(stage, Grant())
            self.assertEqual(replace(g, submits=False, max_turns=old.max_turns), old, stage)
            self.assertGreaterEqual(g.max_turns, policy.SUBMIT_TURNS, stage)
            self.assertNotIn(submit.NAME, g.tools, stage)
            self.assertEqual(says(g, submit.NAME, {"stage": stage}), "", stage)
            if policy.is_prose_stage(stage):
                self.assertEqual(policy.beyond_reading(g), (), stage)

    def test_no_other_mcp_tool_and_no_other_stage_gets_through(self):
        for stage in ("pr", "ship", "x"):
            self.assertIn(policy.HELD, says(grant_for(stage), policy.SUBMIT_TOOL, {}), stage)
        for name in ("mcp__cos__other", "mcp__other__submit"):
            self.assertIn(policy.HELD, says(grant_for("spec"), name, {}), name)

    def test_gebo_and_the_estimate_gain_submit_and_nothing_else(self):
        """The sessions that are no stage, each with its old grant but `submits` and at least
        `SUBMIT_TURNS` turns."""
        from coscc.units import submit

        self.assertEqual(set(policy.SUBMITTING_SESSIONS), set(submit.SESSIONS))
        for kind in policy.SUBMITTING_SESSIONS:
            g, old = grant_for(kind), policy.GRANTS[kind]
            self.assertEqual(replace(g, submits=False, max_turns=old.max_turns), old, kind)
            turns = old.max_turns if kind in policy.OWN_TURNS else policy.SUBMIT_TURNS
            self.assertEqual(g.max_turns, max(old.max_turns, turns), kind)
            self.assertEqual(says(g, submit.NAME, {}), "", kind)
            self.assertIn(policy.HELD, says(g, "mcp__cos__other", {}), kind)

    def test_the_tool_touches_no_disk_and_runs_nothing(self):
        import ast

        from coscc.units import submit

        tree = ast.parse(Path(submit.__file__).read_text(encoding="utf-8"))
        imported = {
            a.name
            for n in ast.walk(tree)
            if isinstance(n, (ast.Import, ast.ImportFrom))
            for a in n.names
        }
        imported |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertFalse(
            imported & {"subprocess", "os", "shutil", "sqlite3", "coscc.store.db"}, imported
        )
        called = {
            n.func.attr
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        self.assertFalse(
            called & {"write_text", "write_bytes", "open", "unlink", "mkdir", "write"}, called
        )


class OneGrantPerStage(unittest.TestCase):
    """Md` `## Answers`, answer 1: tools follow the stage, in every mode."""

    def test_grants_are_keyed_by_stage_alone(self):
        for key in policy.GRANTS:
            self.assertIsInstance(key, str, key)

    def test_grant_for_takes_no_mode(self):
        self.assertEqual(list(inspect.signature(grant_for).parameters), ["stage"])

    def test_spec_has_its_own_grant(self):
        """Without it `spec` fell to `Grant()`: no tools, one turn."""
        self.assertNotEqual(grant_for("spec"), Grant())

    def test_spec_reads_and_only_reads(self):
        spec = grant_for("spec")
        self.assertEqual(spec.tools, READ_TOOLS)
        self.assertEqual(spec.commands, ())
        self.assertEqual(policy.beyond_reading(spec), ())
        self.assertTrue(spec.app_writes_artifact)

    def test_spec_has_the_turns_plan_has(self):
        """Copied from `plan`, not measured for `spec`."""
        self.assertEqual(grant_for("spec").max_turns, grant_for("plan").max_turns)
        self.assertEqual(grant_for("spec").max_budget_usd, grant_for("plan").max_budget_usd)

    def test_every_other_grant_is_unchanged(self):
        """The values each stage held before this unit, written out."""
        rw = READ_TOOLS + policy.WRITE_TOOLS + policy.EXEC_TOOLS
        expected = {
            # `submits` on the stages that hand back a stage result.
            "impl": Grant(
                tools=rw + (policy.AGENT_TOOL, policy.SEND_MESSAGE),
                commands=policy.IMPL_COMMANDS,
                max_turns=120,
                max_budget_usd=8.0,
                app_writes_artifact=False,
                submits=True,
            ),
            "plan": Grant(tools=READ_TOOLS, max_turns=40, max_budget_usd=4.0, submits=True),
            "review": Grant(tools=READ_TOOLS, max_turns=40, max_budget_usd=4.0, submits=True),
        }
        added = policy.SUBMITTING_SESSIONS - {"estimate", "integrate"}
        self.assertEqual(
            set(policy.GRANTS) - added,
            set(expected) | {"intent", "spec", "integrate", "spike", "estimate"},
        )
        for stage, grant in expected.items():
            self.assertEqual(grant_for(stage), grant, stage)

    def test_pr_and_ship_hold_no_grant(self):
        """Both are the PR machine's, with no session, so neither is in the table and a step of
        either would start from the locked position."""
        for stage in ("pr", "ship"):
            with self.subTest(stage=stage):
                self.assertNotIn(stage, policy.GRANTS)
                self.assertEqual(grant_for(stage), Grant())
                self.assertEqual(grant_for_step(stage, "novel"), Grant())


class OnlyImplStartsHelpers(unittest.TestCase):
    def test_impl_holds_the_agent_tool_and_no_other_grant_does(self):
        self.assertIn(policy.AGENT_TOOL, IMPL.tools)
        for stage, grant in policy.GRANTS.items():
            if stage != "impl":
                with self.subTest(stage=stage):
                    self.assertNotIn(policy.AGENT_TOOL, grant.tools)

    def test_a_helper_holds_no_tool_impl_does_not(self):
        for name, spec in policy.SUBAGENTS.items():
            with self.subTest(helper=name):
                # The kernel's `peers` is an MCP tool, which no grant lists.
                self.assertTrue(set(spec["tools"]) - {policy.PEERS_TOOL} <= set(IMPL.tools))
                self.assertNotIn(policy.AGENT_TOOL, spec["tools"])
        self.assertEqual(policy.SUBAGENTS["scout"]["tools"], list(READ_TOOLS))
        self.assertEqual(list(policy.SUBAGENTS), ["scout", "worker"])

    def test_a_worker_is_sonnet_and_holds_what_a_parallel_step_needs(self):
        worker = policy.SUBAGENTS["worker"]
        self.assertEqual(worker["model"], "sonnet")
        self.assertEqual(
            set(worker["tools"]),
            {"Read", "Glob", "Grep", "Write", "Edit", "Bash", "SendMessage", "mcp__cos__peers"},
        )

    def test_peers_is_open_to_a_grant_that_starts_helpers_only(self):
        self.assertEqual(says(IMPL, policy.PEERS_TOOL, {}, agent_id="a1"), "")
        self.assertEqual(says(IMPL, policy.PEERS_TOOL, {}), "")
        self.assertIn(policy.HELD, says(grant_for("review"), policy.PEERS_TOOL, {}))

    def test_only_a_named_helper_may_be_started(self):
        self.assertEqual(says(IMPL, policy.AGENT_TOOL, {"subagent_type": "scout"}), "")
        for other in ("general-purpose", "Explore", None):
            with self.subTest(subagent_type=other):
                self.assertIn(
                    "only these helpers", says(IMPL, policy.AGENT_TOOL, {"subagent_type": other})
                )


# What the app bounds a unit's ram directory to (`coscc.units.scratch.RAM_CAP`).
CAP = 64 * 2**20


class WritesBelowTheUnitsScratch(unittest.TestCase):
    """Outside its worktree and its unit a step's write tools reach only its unit's ram and disk
    directories, and only with Bash."""

    WS = "/tmp/ws"
    UNIT_DIR = "/tmp/data/units/ws-abc/.cos/0060_x"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.ram, self.disk, self.other = (self.root / n for n in ("ram", "disk", "other"))
        for d in (self.ram, self.disk, self.other):
            d.mkdir()
        self.scratch = (str(self.ram), str(self.disk))

    def write(self, path, grant=IMPL, tool="Write", scratch="own", cap=CAP):
        scratch = self.scratch if scratch == "own" else scratch
        return says(
            grant,
            tool,
            {"file_path": str(path)},
            roots=(self.WS, self.UNIT_DIR),
            scratch=scratch,
            ram_cap=cap,
        )

    def test_a_tmp_directory_naming_the_unit_is_no_place_to_write(self):
        d = Path(tempfile.mkdtemp(prefix="coscc-0060_x-"))
        self.addCleanup(d.rmdir)
        self.assertIn(policy.WRITES, self.write(d / "f"))
        self.assertIn(policy.WRITES, self.write(d / "f", scratch=None))

    def test_a_write_tool_may_write_below_either_directory(self):
        for where in (self.ram, self.disk):
            for tool in policy.WRITE_TOOLS:
                with self.subTest(where=where.name, tool=tool):
                    self.assertEqual(self.write(where / "f", tool=tool), "")
                    self.assertEqual(self.write(where / "sub" / "f", tool=tool), "")

    def test_the_directory_itself_and_a_way_out_are_refused(self):
        for path in (self.ram, self.ram / ".." / "other" / "f", self.ram.parent / "ram-2" / "f"):
            with self.subTest(path=path):
                self.assertIn(policy.WRITES, self.write(path))

    def test_a_step_without_exec_tools_may_not_write_there(self):
        grant = Grant(tools=("Write", "Read"))
        self.assertIn(policy.WRITES, self.write(self.disk / "f", grant=grant))
        self.assertIn(policy.WRITES, self.write(self.ram / "f", grant=grant))
        self.assertEqual(self.write(self.disk / "f", grant=IMPL), "")

    def test_without_scratch_nothing_outside_the_workspace_may_be_written(self):
        self.assertIn(policy.WRITES, self.write(self.disk / "f", scratch=None))

    def test_another_units_scratch_is_refused(self):
        self.assertIn(policy.WRITES, self.write(self.other / "f"))

    def test_a_symlink_inside_scratch_pointing_out_is_refused(self):
        for where in (self.ram, self.disk):
            with self.subTest(where=where.name):
                link = where / "out"
                link.symlink_to(self.other)
                self.assertIn(policy.WRITES, self.write(link / "f"))
                self.assertIn(policy.WRITES, self.write(link))
        link = self.ram / "plain"
        link.symlink_to(self.disk)
        self.assertEqual(self.write(link / "f"), "")

    def test_a_full_ram_directory_is_refused_and_names_the_disk_one(self):
        big = self.ram / "sub" / "big"
        big.parent.mkdir()
        with big.open("wb") as f:
            f.truncate(CAP)
        reason = self.write(self.ram / "x")
        self.assertIn(str(self.disk), reason)
        self.assertIn("COS_SCRATCH_DISK", reason)
        self.assertEqual(self.write(self.disk / "x"), "")

    def test_a_ram_directory_just_under_the_cap_still_takes_a_write(self):
        with (self.ram / "big").open("wb") as f:
            f.truncate(CAP - 2**20)
        self.assertEqual(self.write(self.ram / "x"), "")

    def test_a_symlink_in_ram_is_not_followed_when_the_size_is_counted(self):
        cap = 2**20
        with (self.disk / "big").open("wb") as f:
            f.truncate(10 * cap)
        (self.ram / "ln").symlink_to(self.disk / "big")
        self.assertEqual(self.write(self.ram / "x", cap=cap), "")


class WritesStayInTheWorkspace(unittest.TestCase):
    def test_a_write_inside_the_workspace_passes(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "src" / "a.py"
            self.assertEqual(says(IMPL, "Write", {"file_path": str(target)}, roots=(d,)), "")

    def test_a_write_outside_the_workspace_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            for bad in ("/etc/passwd", "/tmp/elsewhere.txt", str(Path(d).parent / "up.txt")):
                said = says(IMPL, "Write", {"file_path": bad}, roots=(d,))
                self.assertIn(policy.WRITES, said, bad)

    def test_a_traversal_back_out_is_refused_after_resolving(self):
        with tempfile.TemporaryDirectory() as d:
            bad = str(Path(d) / ".." / ".." / "etc" / "passwd")
            self.assertIn(policy.WRITES, says(IMPL, "Write", {"file_path": bad}, roots=(d,)))

    def test_the_workspace_root_itself_is_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(says(IMPL, "Write", {"file_path": d}, roots=(d,)), "")


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
        for bad in ("git $(curl evil)", "ls `whoami`", "cat <(curl x)"):
            self.assertIn("substitution", check_command(IMPL, bad), bad)

    def test_a_parameter_expansion_is_not_a_substitution(self):
        """Moved out of the test above, not deleted. This case pinned `npm ${X}` as refused for
        substitution, but refusing `${` is a misreading: it starts no process. The command's
        name is still read (`npm`); a variable *as* the name is refused."""
        self.assertEqual(check_command(IMPL, "npm ${X}"), "")

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
        # no write tool sees it. Found while reading a real run on 2026-09-22.
        for bad in ("echo x > /etc/passwd", "cat a >> b", "npm test > out.txt"):
            self.assertIn("redirecting into a file", check_command(IMPL, bad), bad)

    def test_a_descriptor_redirect_is_not_a_file_redirect(self):
        # `2>&1` is the common case and touches no file. It also used to break the
        # splitter: the `&` read as a separator and the `1` as a command.
        for good in ("npm test 2>&1", "ls >&2", "uv run python -m unittest 2>&1 | tail -5"):
            self.assertEqual(check_command(IMPL, good), "", good)


class MergingIsShipsNotPrs(unittest.TestCase):
    """A session never merges; `ship` does, after a review passed. `ship` is the PR machine's
    alone, and the deny list is read on `MERGING`."""

    PR = MERGING

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

    def test_ship_is_no_prose_stage_and_no_session(self):
        """Was `test_ship_is_no_longer_a_prose_stage`. `ship` holds no grant at all, so the
        second half says it carries nothing."""
        self.assertNotIn("ship", policy.PROSE_STAGES)
        self.assertEqual(grant_for("ship").commands, ())

    def test_review_reads_and_only_reads(self):
        review = grant_for("review")
        self.assertEqual(review.tools, policy.READ_TOOLS)
        self.assertEqual(policy.beyond_reading(review), ())
        self.assertIn("review", policy.PROSE_STAGES)

    def test_a_flag_in_front_does_not_walk_past_the_deny_list(self):
        """Flags are removed before the prefix is compared."""
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
        """`node -e` may spawn `gh`, and an alias defined before the step runs under its own name.
        Pinned so that nobody reads this grant as a guarantee; `.claude/CLAUDE.md` says the same."""
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


class TheEstimateGrantOpensNothing(unittest.TestCase):
    """$2.00, no tool, no command, and a warning for the page."""

    def test_the_grant(self):
        g = grant_for("estimate")
        self.assertFalse(g.opens_anything)
        self.assertEqual((g.max_turns, g.max_budget_usd), (policy.SUBMIT_TURNS, 2.0))
        self.assertIn("paid session", g.warning)
        self.assertIn("password", g.warning)


class AFeatureAddsItsSession(unittest.TestCase):
    """`add_session`: a feature's grant, submitting, with its own turns when it asks."""

    def tearDown(self):
        policy.GRANTS.pop("planted", None)
        policy.SUBMITTING_SESSIONS.discard("planted")
        policy.OWN_TURNS.discard("planted")

    def test_an_added_session_submits_and_keeps_its_own_turns(self):
        grant = Grant(max_turns=2, max_budget_usd=0.5)
        policy.add_session("planted", grant, own_turns=True)
        policy.add_session("planted", grant, own_turns=True)
        g = grant_for("planted")
        self.assertTrue(g.submits)
        self.assertEqual((g.max_turns, g.max_budget_usd), (2, 0.5))
        self.assertEqual(says(g, "mcp__cos__submit", {}), "")

    def test_a_name_another_grant_holds_is_refused(self):
        with self.assertRaises(ValueError):
            policy.add_session("impl", Grant(), own_turns=False)


if __name__ == "__main__":
    unittest.main()


class TheWriteBoundaryIsTheWorkspacePlusOneDirectory(unittest.TestCase):
    """Every artifact lives in the product's store, outside the workspace, so a step that writes
    its own admits **one** more directory: the step's own unit."""

    def setUp(self):
        self.grant = policy.grant_for("impl")
        self.workspace = "/tmp/ws"
        self.unit = "/tmp/data/units/ws-abc/.cos/0001_a-problem"

    def _says(self, path: str, unit_dir: str | None = None) -> str:
        roots = (self.workspace, unit_dir) if unit_dir else (self.workspace,)
        return says(self.grant, "Write", {"file_path": path}, roots=roots)

    def test_a_step_may_write_its_own_artifact(self):
        self.assertEqual(self._says(f"{self.unit}/impl.md", self.unit), "")

    def test_a_step_may_still_write_code_in_the_workspace(self):
        self.assertEqual(self._says(f"{self.workspace}/src/a.py", self.unit), "")

    def test_it_is_one_directory_and_not_the_whole_store(self):
        # The sibling unit is the case that matters: a prefix check on the store would
        # let any step rewrite any other unit's artifacts.
        sibling = "/tmp/data/units/ws-abc/.cos/0002_another/impl.md"
        self.assertIn(policy.WRITES, self._says(sibling, self.unit))
        store = "/tmp/data/units/ws-abc/.cos/anything.md"
        self.assertIn(policy.WRITES, self._says(store, self.unit))

    def test_everywhere_else_is_still_refused(self):
        for path in ("/etc/passwd", "/tmp/data/cos.db", "/tmp/ws/../elsewhere/x.py", "~/.ssh/id"):
            self.assertIn(policy.WRITES, self._says(path, self.unit), path)

    def test_without_a_unit_directory_the_boundary_is_the_workspace(self):
        self.assertEqual(self._says(f"{self.workspace}/src/a.py"), "")
        self.assertIn(policy.WRITES, self._says(f"{self.unit}/impl.md"))


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
            IMPL.max_turns,
            worst,
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
    def test_the_plan_ceiling_is_above_the_one_that_was_hit(self):
        self.assertGreater(grant_for("plan").max_turns, 20)

    def test_plan_still_only_reads(self):
        # Raising the ceiling must not widen what the stage may do.
        self.assertEqual(grant_for("plan").tools, READ_TOOLS)
        self.assertEqual(grant_for("plan").commands, ())


class GeboPushesOnlyWithTheLease(unittest.TestCase):
    """One push, leased to the head the step began at, on the unit's branch."""

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
        self.assertEqual(
            self.run_(f"git push --force-with-lease=feat/x:{self.HEAD} origin HEAD:feat/x"), ""
        )
        self.assertEqual(
            self.run_(f"git push -u --force-with-lease=feat/x:{self.HEAD} origin feat/x"), ""
        )

    def test_every_other_push_is_refused_with_its_own_reason(self):
        cases = {
            "git push origin feat/x": "exactly one",
            "git push --force origin feat/x": "--force",
            "git push -f origin feat/x": "--force",
            "git push --force-with-lease origin feat/x": "needs a value",
            f"git push --force-with-lease=feat/x:{'b' * 40} origin feat/x": "bound to",
            f"git push --force-with-lease=feat/x:{'a' * 7} origin feat/x": "bound to",
            f"git push --force-with-lease=feat/x:{self.HEAD} origin main": "push with",
            f"git push --force-with-lease=feat/x:{self.HEAD} origin feat/x:main": "push with",
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
        """`push` as a word, not as the subcommand."""
        for command in ("git log --grep push", "git commit -m push", "git branch push-fix"):
            with self.subTest(command=command):
                self.assertEqual(self.run_(command), "")
        for command in ("git --no-pager push origin feat/x", "git -c a=b push origin feat/x"):
            with self.subTest(command=command):
                self.assertIn("nothing between", self.run_(command))

    def test_no_lease_means_no_push(self):
        self.assertIn("no lease", self.run_(self.OK, lease=None))

    def test_merge_pull_and_update_branch_are_refused(self):
        for command in (
            "git merge origin/main",
            "git pull --rebase origin main",
            "gh pr merge 7",
            "gh -R o/r pr update-branch 7 --rebase",
            "gh api -X PUT repos/o/r/pulls/7/merge",
        ):
            with self.subTest(command=command):
                self.assertNotEqual(self.run_(command), "")

    def test_rebase_and_tests_run(self):
        for command in (
            "git rebase origin/main",
            "git rebase --continue",
            "git rebase --abort",
            "npm test",
            "uv run python -m unittest",
            "gh pr checks 7",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.run_(command), "")

    def test_roads_past_the_lease_that_the_grant_holds_are_refused(self):
        """Moving the branch without saying `git push`."""
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
        for command in (
            "gh pr view 7 --json headRefOid",
            "gh pr checks 7",
            "git config user.name",
            "GIT_EDITOR=true git rebase --continue",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.run_(command), "")

    def test_the_known_limit(self):
        """Tokens, not what runs. A program the grant may start can spawn `git push --force` itself
        — `node -e`, `python -c`, or a script the step wrote and then ran through `npm test`."""
        for command in (
            "node -e 'require(\"child_process\")'",
            "python -c 'import subprocess'",
            "python3 push.py",
            "npm test",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.run_(command), "")

    def test_the_new_refusals_are_geboes_alone(self):
        self.assertEqual(check_command(MERGING, "gh api repos/o/r/pulls/7"), "")
        self.assertEqual(check_command(grant_for("impl"), "git config alias.st status"), "")

    def test_other_grants_push_as_before(self):
        self.assertEqual(check_command(MERGING, "git push origin feat/x"), "")
        self.assertEqual(check_command(IMPL, "git push origin feat/x"), "")


class IntegrateKeepsItsDenyList(unittest.TestCase):
    """`integrate` keeps its own deny list. `pr` holds no grant, so the rebase, pull and
    forced-push refusals it carried are gone with it."""

    def test_integrate_is_unchanged(self):
        self.assertIs(grant_for("integrate").denied, policy.INTEGRATE_DENIED)
        self.assertIn("ship stage's", check_command(grant_for("integrate"), "gh pr merge 7"))


class IntentReadsWhatSpecReads(unittest.TestCase):
    """`intent` checks the idea's problem against the worktree's code, so it reads as `spec`
    reads and does nothing else."""

    def test_it_holds_the_read_tools_and_nothing_beyond_reading(self):
        grant = grant_for("intent")
        self.assertEqual(grant.tools, READ_TOOLS)
        self.assertEqual(grant.commands, ())
        self.assertEqual(policy.beyond_reading(grant), ())

    def test_its_ceilings_are_specs(self):
        grant = grant_for("intent")
        self.assertEqual(grant.max_turns, 40)
        self.assertEqual(grant.max_budget_usd, 4.0)

    def test_it_holds_what_spec_holds(self):
        self.assertEqual(grant_for("intent").tools, grant_for("spec").tools)


class SpikeWritesOnlyItsScratch(unittest.TestCase):
    """`spike` runs code, writes only its throwaway `cwd`, and never `git`."""

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
        return says(self.G, tool, {"file_path": str(path)}, roots=(str(self.scratch),))

    def test_the_grant_is_the_one_the_spec_names(self):
        self.assertEqual(self.G.tools, READ_TOOLS + policy.WRITE_TOOLS + policy.EXEC_TOOLS)
        self.assertEqual((self.G.max_turns, self.G.max_budget_usd), (80, 8.0))
        self.assertTrue(self.G.app_writes_artifact)
        self.assertIn("arbitrary code", self.G.warning)
        self.assertNotIn("spike", policy.PROSE_STAGES)

    def test_git_is_not_among_its_commands(self):
        # `beyond_reading` does not guard this grant, so this line does.
        self.assertNotIn("git", self.G.commands)
        self.assertIn(
            "this step may not run 'git'", check_command(self.G, "git -C /tmp commit -m x")
        )
        self.assertEqual(check_command(self.G, "python -c 'print(1)'"), "")

    def test_it_reads_the_worktree_and_the_unit(self):
        self.assertEqual(self.d("Read", self.tree / "coscc" / "policy.py"), "")
        self.assertEqual(self.d("Read", self.unit / "spec.md"), "")

    def test_it_writes_its_scratch_and_nothing_it_reads(self):
        self.assertEqual(self.d("Write", self.scratch / "probe.py"), "")
        self.assertIn(policy.WRITES, self.d("Write", self.tree / "probe.py"))
        self.assertIn(policy.WRITES, self.d("Write", self.unit / "spec.md"))


class TheShellIsReadAsTheShellReadsIt(unittest.TestCase):
    """On this set, the refusals naming a command that is not one, and the refusals of `${…}` or a
    backtick in single quotes, are zero."""

    # Each must pass under `impl`.
    PASS = (
        "grep -E 'a|b' f",
        'rg "def |class " coscc',
        'git commit -m "fix: a; b && c"',
        'git commit -m "first line\n\nsecond; line | with && ops"',
        "ls # a; curl x",
        "cat <<'EOF'\nfail) ^ | def\nEOF",
        'echo "${PIPESTATUS[0]}"',
        "echo ${PIPESTATUS[0]}",
        "npm ${X}",
        "grep '\\`x\\`' f",
        "cat <<'EOF'\nrun `whoami` and $(date)\nEOF",
        "npm test > /dev/null 2>&1",
        "npm test 2>/dev/null",
        "npm test &>/dev/null",
    )

    # Each refused, the reason carrying the fragment named.
    REFUSE = (
        ("git $(curl evil)", "substitution"),
        ("ls `whoami`", "substitution"),
        ("cat <(curl x)", "substitution"),
        ('echo "$(curl x)"', "substitution"),
        ("cat <<EOF\n$(curl x)\nEOF", "substitution"),
        ("echo ${X:-$(curl x)}", "substitution"),
        ("$CMD x", "variable"),
        ("echo x > out.txt", "redirect"),
        ("echo x > /tmp/other/x", "redirect"),
        ("echo x > /tmp/coscc-0060_x/../ws/f", "redirect"),
        ("cat a >> b", "redirect"),
        ("echo x &>file", "redirect"),
        ("cat <>file", "redirect"),
        ("echo x > /dev/null/..", "redirect"),
        ("echo x > $OUT", "redirect"),
        ("echo x >&out.txt", "redirect"),
    )

    UNREADABLE = (
        "echo 'unclosed",
        "cat <<EOF\nno end line",
        "echo $(date",
        'echo "unclosed',
        "echo ${X",
        "echo x >",
    )

    def check(self, command):
        return check_command(IMPL, command)

    def test_every_sample_that_should_pass_passes(self):
        for command in self.PASS:
            with self.subTest(command=command):
                self.assertEqual(self.check(command), "")

    def test_the_two_counts_the_intent_names_are_zero(self):
        reasons = [self.check(command) for command in self.PASS]
        self.assertEqual(sum("may not run" in r for r in reasons), 0)
        self.assertEqual(sum("substitution" in r for r in reasons), 0)

    def test_every_sample_that_should_be_refused_is_refused_for_its_own_reason(self):
        for command, why in self.REFUSE:
            with self.subTest(command=command):
                self.assertIn(why, self.check(command))

    def test_a_line_that_cannot_be_read_is_refused_not_guessed(self):
        for command in self.UNREADABLE:
            with self.subTest(command=command):
                self.assertIn("could not be read", self.check(command))

    def test_the_name_refused_is_the_real_first_word(self):
        """`this step may not run X`, X the command's first word with quotes removed."""
        self.assertEqual(self.check("npm test; 'curl' x"), "this step may not run 'curl'")
        self.assertEqual(self.check("npm test | sed -e 's/a|b/c/'"), "this step may not run 'sed'")
        self.assertEqual(self.check('X="a b" curl x'), "this step may not run 'curl'")

    def test_an_assignment_alone_is_refused_by_what_it_is(self):
        """Measured on the refusals of 2026-09-23..24: `S=/home/…/coscc-fb0599d12eeb; node …`
        must not be refused as `may not run 'coscc-fb0599d12eeb'`, a name taken from the
        value's last `/`."""
        reason = self.check("S=/home/bd/.cos/units/coscc-fb0599d12eeb; node x $S")
        self.assertIn("only assigns (S=", reason)
        self.assertNotIn("may not run", reason)

    def test_arithmetic_is_refused_and_named_as_such(self):
        reason = self.check("echo $((1+1))")
        self.assertIn("arithmetic substitution is not allowed: $((", reason)
        self.assertNotIn("$( ", reason)

    def test_a_backslashed_dollar_or_backtick_is_text(self):
        for command in ("echo \\$(x)", "echo \\`x\\`", 'echo "\\$(x)"', "echo $'`x` $(y)'"):
            with self.subTest(command=command):
                self.assertEqual(self.check(command), "")

    def test_a_variable_as_the_name_is_refused(self):
        for command in ("$CMD x", "${CMD} x", 'X=1 "$CMD" x', "/usr/bin/$C x"):
            with self.subTest(command=command):
                self.assertIn("name is a variable", self.check(command))

    def test_a_variable_passed_to_git_or_gh_is_refused_where_words_are_denied(self):
        pr, integrate = MERGING, grant_for("integrate")
        self.assertIn("may not pass ${P} to gh", check_command(pr, "gh ${P} merge"))
        self.assertIn("may not pass $P to gh", check_command(pr, "gh $P merge"))
        self.assertIn("may not pass $SUB to git", check_command(integrate, "git $SUB --force"))
        # `impl` denies nothing, so it keeps what it had.
        self.assertEqual(check_command(IMPL, 'git commit -m "$MSG"'), "")

    def test_quotes_no_longer_hide_a_refused_word(self):
        """Read after quote removal: bash runs `gh pr merge` for all of these."""
        pr = MERGING
        for command in ("gh pr 'merge' 7", 'gh "pr" merge', "gh pr m\\erge", "gh pr $'merge'"):
            with self.subTest(command=command):
                self.assertIn("ship stage's", check_command(pr, command))
        self.assertIn("alias", check_command(grant_for("integrate"), "git -c al\\ias.p=push p"))

    def test_a_separator_inside_a_heredoc_body_starts_no_command(self):
        self.assertEqual(self.check("cat <<EOF\nx; curl y\nEOF\nls"), "")
        self.assertIn("may not run 'curl'", self.check("cat <<EOF\nx\nEOF\ncurl y"))


class TheReaderFollowsBash(unittest.TestCase):
    """One line per rule `_read` copies from bash 5.3."""

    def read(self, command):
        parsed = policy._read(command)
        self.assertIsInstance(parsed, policy._Parsed, command)
        return parsed

    def words(self, command):
        return [list(c.words) for c in self.read(command).commands]

    def test_quotes_are_removed_and_keep_their_contents_whole(self):
        self.assertEqual(self.words("""a 'b c' "d e" f\\ g"""), [["a", "b c", "d e", "f g"]])
        self.assertEqual(self.words("a ''"), [["a", ""]])

    def test_ansi_c_quotes_are_decoded(self):
        self.assertEqual(self.words("a $'x\\ty\\x41\\'z'"), [["a", "x\tyA'z"]])

    def test_a_backslash_newline_is_removed_even_inside_an_operator(self):
        self.assertEqual(self.words("a b\\\nc"), [["a", "bc"]])
        self.assertEqual(self.words("a&\\\n&b"), [["a"], ["b"]])

    def test_a_comment_starts_only_a_word(self):
        self.assertEqual(self.words("a b#c #d; e\nf"), [["a", "b#c"], ["f"]])

    def test_every_separator_ends_a_command(self):
        self.assertEqual(
            self.words("a; b && c || d | e |& f & g\nh"),
            [["a"], ["b"], ["c"], ["d"], ["e"], ["f"], ["g"], ["h"]],
        )

    def test_a_subshell_opens_only_where_a_command_starts(self):
        self.assertEqual(self.words("(a; b) && c"), [["a"], ["b"], ["c"]])
        self.assertEqual(self.words("a x(y)z"), [["a", "x(y)z"]])
        self.assertEqual(self.words("A=(1 2 3) b"), [["A=(1 2 3)", "b"]])

    def test_redirects_carry_their_operator_descriptor_and_target(self):
        (cmd,) = self.read("a 2>&1 >o 3>>'p q' <i &>/dev/null >&2 5>&-").commands
        self.assertEqual(cmd.words, ("a",))
        self.assertEqual(
            [(r.op, r.fd, r.target) for r in cmd.redirects],
            [
                (">&", "2", "1"),
                (">", "", "o"),
                (">>", "3", "p q"),
                ("<", "", "i"),
                ("&>", "", "/dev/null"),
                (">&", "", "2"),
                (">&", "5", "-"),
            ],
        )

    def test_a_digit_is_a_descriptor_only_when_it_is_the_whole_word(self):
        (cmd,) = self.read("echo a2>o").commands
        self.assertEqual((cmd.words, cmd.redirects[0].fd), (("echo", "a2"), ""))

    def test_heredoc_bodies_are_not_commands_and_queue_in_order(self):
        parsed = self.read("a <<X <<-'Y'\nx; b\nX\n\t$(c)\n\tY\nd")
        self.assertEqual([list(c.words) for c in parsed.commands], [["a"], ["d"]])
        self.assertEqual(parsed.substitutions, ())

    def test_an_expanding_heredoc_body_carries_its_substitutions(self):
        parsed = self.read("a <<X\n\\$(no) $(yes)\nX")
        self.assertEqual([t for t, _ in parsed.substitutions], ["$("])

    def test_a_heredoc_line_ending_in_a_backslash_joins_the_next(self):
        # Measured on bash 5.3: the first `EOF` below is part of the body.
        self.assertEqual(self.words("cat <<EOF\na\\\nEOF\nEOF\nls"), [["cat"], ["ls"]])

    def test_expanded_marks_parameter_expansion_outside_single_quotes(self):
        (cmd,) = self.read("""a $X "$Y" '$Z' ${W} $1 x""").commands
        self.assertEqual(cmd.expanded, (False, True, True, False, True, True, False))

    def test_a_redirect_target_that_bash_would_rewrite_is_expanded(self):
        (cmd,) = self.read("a >~/x >'~/x' >/t/* >'/t/*' >$O").commands
        self.assertEqual([r.expanded for r in cmd.redirects], [True, False, True, False, True])

    def test_substitutions_in_effect_and_not(self):
        parsed = self.read("""a $(b) `c` <(d) "$(e)" '$(f)' $'`g`' \\$(h) ${i:-$(j)} $((1))""")
        self.assertEqual([t for t, _ in parsed.substitutions], ["$(", "`", "<(", "$(", "$(", "$(("])

    def test_braces_nest_and_quotes_hold_inside_them(self):
        self.assertEqual(
            self.words("a ${X:-{b}} ${Y:-'}'} c"), [["a", "${X:-{b}}", "${Y:-'}'}", "c"]]
        )

    def test_what_cannot_be_read_says_where(self):
        for command, what in (
            ("a 'b", "'"),
            ("a $(b", "$("),
            ("a <<E\nb", "here-document"),
            ("a >", "no target"),
            ("(a", "("),
        ):
            with self.subTest(command=command):
                parsed = policy._read(command)
                self.assertIsInstance(parsed, policy._Unreadable)
                self.assertIn(what, parsed.what)


class TheLabelDecidesImplsCeilings(unittest.TestCase):
    """A `novel` impl gets its own ceilings, and nothing else changes with them."""

    LABELS = ("routine", "novel", None, "", "NOVEL", "weird")

    def test_a_novel_impl_gets_the_novel_ceilings(self):
        novel = grant_for_step("impl", "novel")
        self.assertEqual((novel.max_turns, novel.max_budget_usd), (250, 16.0))

    def test_a_routine_or_unlabelled_impl_is_unchanged(self):
        rw = (
            READ_TOOLS
            + policy.WRITE_TOOLS
            + policy.EXEC_TOOLS
            + (policy.AGENT_TOOL, policy.SEND_MESSAGE)
        )
        written = Grant(
            tools=rw,
            commands=policy.IMPL_COMMANDS,
            max_turns=120,
            max_budget_usd=8.0,
            app_writes_artifact=False,
            submits=True,
        )
        for label in ("routine", None):
            with self.subTest(label=label):
                self.assertEqual(grant_for_step("impl", label), grant_for("impl"))
                self.assertEqual(grant_for_step("impl", label), written)

    def test_novel_differs_from_routine_in_the_ceilings_alone(self):
        """No tool, command or refusal comes with the higher ceilings."""
        routine, novel = grant_for_step("impl", "routine"), grant_for_step("impl", "novel")
        self.assertNotEqual(novel, routine)
        self.assertEqual(
            replace(novel, max_turns=routine.max_turns, max_budget_usd=routine.max_budget_usd),
            routine,
        )

    def test_the_stages_after_impl_read_the_label_and_keep_their_grant(self):
        for stage in ("pr", "review", "ship"):
            for label in ("routine", "novel", None):
                with self.subTest(stage=stage, label=label):
                    self.assertEqual(grant_for_step(stage, label), grant_for(stage))

    def test_a_stage_with_no_novel_ceilings_keeps_its_grant(self):
        """The stages that carry no label, and one invented tomorrow, keep their grant."""
        for stage in (
            "integrate",
            "spec",
            "plan",
            "spike",
            "idea",
            "intent",
            "a-stage-invented-tomorrow",
        ):
            with self.subTest(stage=stage):
                self.assertEqual(grant_for_step(stage, "novel"), grant_for(stage))

    def test_every_grant_that_opens_anything_keeps_both_ceilings(self):
        """No label on any stage takes a ceiling away."""
        for stage in set(policy.GRANTS) | {"idea", "intent", "a-stage-invented-tomorrow"}:
            for label in self.LABELS:
                grant = grant_for_step(stage, label)
                if not grant.opens_anything:
                    continue
                with self.subTest(stage=stage, label=label):
                    self.assertIsInstance(grant.max_turns, int)
                    self.assertGreaterEqual(grant.max_turns, 1)
                    self.assertTrue(math.isfinite(grant.max_budget_usd))
                    self.assertGreater(grant.max_budget_usd, 0)

    def test_only_the_exact_label_counts(self):
        """A label spelled any other way is not `novel`."""
        for label in ("", "NOVEL", "Novel", "novel ", "weird"):
            with self.subTest(label=label):
                self.assertEqual(grant_for_step("impl", label), grant_for("impl"))

    def test_the_budget_does_not_stop_a_step_the_turns_would_allow(self):
        """The margin is thin, and 181–250 turns were never measured; this pins only that the budget
        is not lower than the turns at the worst rate seen."""
        novel = grant_for_step("impl", "novel")
        self.assertGreater(novel.max_budget_usd, novel.max_turns * 0.0568)


class ABackgroundCommandIsRefused(unittest.TestCase):
    """A step's session ends with its turn, so nothing it starts in the background is ever read
    back."""

    STAGES = ("impl", "spike", "integrate")
    UNIT_DIR = "/data/units/slot/.cos/0130_x"

    def d(self, stage, tool_input):
        return says(grant_for(stage), "Bash", tool_input, roots=("/w", self.UNIT_DIR))

    def test_run_in_background_is_refused_for_every_grant_with_bash(self):
        for stage in self.STAGES:
            with self.subTest(stage=stage):
                self.assertIn("Bash", grant_for(stage).tools)
                reason = self.d(stage, {"command": "npm test", "run_in_background": True})
                self.assertIn(policy.BACKGROUND_REFUSAL, reason)

    def test_run_in_background_false_or_absent_decides_as_before(self):
        for stage in self.STAGES:
            with self.subTest(stage=stage):
                self.assertEqual(
                    self.d(stage, {"command": "npm test", "run_in_background": False}),
                    self.d(stage, {"command": "npm test"}),
                )
        self.assertEqual(self.d("impl", {"command": "npm test"}), "")

    def test_a_lone_ampersand_is_refused(self):
        for command in (
            "npm run e2e &",
            "npm test & git status",
            "npm test &\ngit status",
            "(npm test &)",
            "npm test & wait",
        ):
            with self.subTest(command=command):
                self.assertIn(policy.BACKGROUND_REFUSAL, says(IMPL, "Bash", {"command": command}))

    def test_the_other_ampersand_operators_read_as_before(self):
        for command in (
            "npm test && git status",
            "npm test &> /dev/null",
            "npm test &>> /dev/null",
            "npm test 2>&1",
            "git status >&2",
            "npm test |& git status",
            "git commit -m 'a & b'",
            'grep "a & b" f',
        ):
            with self.subTest(command=command):
                self.assertEqual(says(IMPL, "Bash", {"command": command}), "")


class AFeaturesMcpToolIsAllowedByItsExactNameAndNothingElse(unittest.TestCase):
    PING = "mcp__fake__ping"

    def _says(self, grant, tool):
        return says(grant, tool, {})

    def test_the_granted_name_is_allowed_and_only_that(self):
        grant = Grant(mcp=(self.PING,))
        self.assertEqual(self._says(grant, self.PING), "")
        for tool in ("mcp__fake__other", "mcp__fake__ping2", "mcp__other__ping"):
            self.assertNotEqual(self._says(grant, tool), "", tool)

    def test_it_does_not_grant_submit(self):
        self.assertNotEqual(self._says(Grant(mcp=(self.PING,)), policy.SUBMIT_TOOL), "")
        self.assertEqual(self._says(Grant(mcp=(self.PING,), submits=True), policy.SUBMIT_TOOL), "")

    def test_a_plain_grant_denies_every_mcp_name(self):
        for tool in (self.PING, "mcp__fake__other", policy.SUBMIT_TOOL, "mcp__x__y"):
            self.assertNotEqual(self._says(Grant(), tool), "", tool)
            if tool != policy.SUBMIT_TOOL:
                self.assertNotEqual(self._says(IMPL, tool), "", tool)

    def test_mcp_is_neither_a_tool_nor_a_reason_to_open(self):
        grant = Grant(mcp=(self.PING,))
        self.assertEqual(grant.tools, ())
        self.assertFalse(grant.opens_anything)

    def test_a_name_that_is_not_a_features_tool_never_enters_mcp(self):
        for bad in (
            "Bash",
            "mcp__cos__submit",
            "mcp__cos__other",
            "mcp__x__A",
            "mcp__X__a",
            "mcp__x__",
            "mcp____a",
            "mcp__x__a b",
            "xmcp__x__a",
            "mcp__x__a\n",
            "",
        ):
            with self.subTest(name=bad), self.assertRaises(ValueError):
                Grant(mcp=(bad,))

    def test_replace_revalidates(self):
        with self.assertRaises(ValueError):
            replace(Grant(), mcp=("Bash",))


class AHelperRunsGitOnlyToRead(unittest.TestCase):
    def bash(self, command: str, agent_id: str | None) -> str:
        return says(IMPL, "Bash", {"command": command}, ("/tmp",), agent_id, branch="x")

    def test_a_helpers_commit_is_refused_saying_only_the_leading_session_commits(self):
        for command in (
            "git commit -m x",
            "git -C . commit -m x",
            "git push origin x",
            "git add .",
            "git checkout main",
            "git stash",
            "git status && git commit -m x",
            "git",
            "uv run git commit -m x",
            "uv run --frozen git push",
            "find . -name x -exec git add {} ;",
        ):
            with self.subTest(command=command):
                self.assertIn("only the leading session commits", self.bash(command, "a1"))

    def test_a_helper_may_read_with_git(self):
        for command in (
            "git diff",
            "git status --porcelain",
            "git log --oneline -3",
            "git show HEAD",
            "git blame x.py",
            "git --no-pager diff | head",
            "uv run pytest tests/x.py",
            "uv run git diff",
            "find . -name '*.py' -exec grep -n git {} ;",
            "grep -n git x.py",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command, "a1"), "")

    def test_the_leading_sessions_commit_runs(self):
        self.assertEqual(self.bash("git commit -m x", None), "")


class TheSecretsAreOutOfEveryCommandsReach(unittest.TestCase):
    """K1 of 0069: a word pointing into the vault's store or `~/.config/coscc/` is refused at
    every stage, whatever the grant's commands."""

    PROTECTED = policy.protected_paths("/srv/cos", "/home/u/.config", "/home/u")

    def test_both_paths_are_refused_at_every_stage_that_runs_commands(self):
        lines = (
            "cat /srv/cos/vault/a.age",
            "ls /srv/cos/vault",
            "cat ~/.config/coscc/vault.key",
            "cat $HOME/.config/coscc/env",
            "grep -r x /home/u/.config/coscc",
            "git log --output=/srv/cos/vault/x",
            "cat < /srv/cos/./vault/a.age",
        )
        for stage in (*policy.GRANTS, "impl"):
            grant = replace(grant_for(stage), commands=("cat", "ls", "grep", "git"))
            grant = replace(grant, protected=self.PROTECTED)
            for line in lines:
                with self.subTest(stage=stage, line=line):
                    self.assertIn("the app's secrets", check_command(grant, line))

    def test_a_glob_that_could_expand_into_one_is_refused(self):
        grant = replace(IMPL, protected=self.PROTECTED, commands=("cat", "ls"))
        for line in (
            "cat ~/.config/cos*/vault.key",
            "cat $HOME/.config/coscc/vault.k?y",
            "ls /srv/*/vault",
            "ls /srv/cos/[v]ault/",
            "cat --file=/home/u/.config/c*",
        ):
            with self.subTest(line=line):
                self.assertIn("the app's secrets", check_command(grant, line))

    def test_a_glob_that_stops_short_of_one_is_not_refused(self):
        grant = replace(IMPL, protected=self.PROTECTED, commands=("cat", "ls"))
        for line in ("ls /srv/*", "ls *.py", "ls /home/u/.config/x*", "cat /srv/cos/v*s/a"):
            with self.subTest(line=line):
                self.assertEqual(check_command(grant, line), "")

    def test_a_neighbour_of_a_protected_path_is_not_refused(self):
        grant = replace(IMPL, protected=self.PROTECTED)
        for line in ("ls /srv/cos/vaults", "cat /home/u/.config/cosccx/a", "ls /srv/cos"):
            with self.subTest(line=line):
                self.assertEqual(check_command(grant, line), "")

    def test_allow_does_not_widen_it(self):
        grant = policy.with_lists(replace(IMPL, protected=self.PROTECTED), ("curl",), ())
        self.assertIn("the app's secrets", check_command(grant, "curl file:///srv/cos/vault/a"))


class AWorkspacesListsChangeOnlyImplsCommands(unittest.TestCase):
    """K2 of 0069: `allow` adds, `block` takes out and wins; nothing else of the grant moves."""

    def test_allow_adds_and_block_wins(self):
        grant = policy.with_lists(IMPL, ("curl", "psql", "git"), ("psql", "rm", "npm"))
        self.assertIn("curl", grant.commands)
        self.assertNotIn("psql", grant.commands)
        self.assertNotIn("npm", grant.commands)
        self.assertEqual(grant.commands.count("git"), 1)
        self.assertEqual(replace(grant, commands=IMPL.commands), IMPL)

    def test_empty_lists_keep_impls_commands(self):
        self.assertEqual(policy.with_lists(IMPL, (), ()), IMPL)

    def test_programs_are_read_as_check_command_reads_them(self):
        self.assertEqual(
            policy.programs_of("A=1 /usr/bin/psql -c 'x; y' | grep z && echo ok"),
            ("psql", "grep", "echo"),
        )
        self.assertEqual(policy.programs_of("echo 'unclosed"), ())

    def test_the_pref_is_read_per_workspace_and_a_path_is_no_name(self):
        stored = {"/w": {"allow": ["curl", "/usr/bin/nc", "x" * 65, 3], "block": ["rm"]}}
        self.assertEqual(policy.lists_of(stored, "/w"), (("curl",), ("rm",)))
        self.assertEqual(policy.lists_of(stored, "/other"), ((), ()))
        self.assertEqual(policy.lists_of("junk", "/w"), ((), ()))


class _Unit(unittest.TestCase):
    """A worktree, the unit's folder and its two scratch directories on disk, and the secrets."""

    def setUp(self):
        made = tempfile.TemporaryDirectory()
        self.addCleanup(made.cleanup)
        self.root = Path(made.name).resolve()
        self.tree, self.unit = self.root / "tree", self.root / "data" / "ws" / "0001_x"
        self.ram, self.disk = self.root / "ram", self.root / "disk"
        for d in (self.tree, self.unit, self.ram, self.disk):
            d.mkdir(parents=True)
        self.home = self.root / "home"
        self.secrets = policy.protected_paths(
            str(self.root / "data"), str(self.home / ".config"), str(self.home)
        )
        self.places = policy.Places(
            roots=(str(self.tree), str(self.unit)),
            scratch=(str(self.ram), str(self.disk)),
            ram_cap=10,
            branch="feat/x",
            secrets=self.secrets,
            home=str(self.home),
        )

    def critical(self, tool, tool_input, grant=IMPL, places=None, agent_id=None) -> str:
        return policy.critical(grant, places or self.places, tool, tool_input, agent_id)

    def bash(self, command, **kw) -> str:
        return self.critical("Bash", {"command": command}, **kw)


class AWriteOutsideTheUnitsPlacesIsRefused(_Unit):
    """The write tools reach the worktree, the unit's folder and, with Bash, the scratch."""

    def test_the_worktree_the_unit_and_the_scratch_are_written(self):
        for path in (self.tree / "a.py", self.unit / "impl.md", self.ram / "f", self.disk / "f"):
            for tool in policy.WRITE_TOOLS:
                with self.subTest(tool=tool, path=path):
                    self.assertEqual(self.critical(tool, {"file_path": str(path)}), "")

    def test_anywhere_else_is_refused(self):
        for path in (
            self.root / "outside.txt",
            self.unit.parent / "0002_y" / "impl.md",
            self.tree / ".." / "x",
            self.ram,
        ):
            for tool in policy.WRITE_TOOLS:
                with self.subTest(tool=tool, path=path):
                    self.assertIn(policy.WRITES, self.critical(tool, {"file_path": str(path)}))

    def test_a_spike_writes_only_its_cwd(self):
        spike = replace(self.places, roots=(str(self.tree),))
        inside = {"file_path": str(self.tree / "a")}
        self.assertEqual(self.critical("Write", inside, places=spike), "")
        unit = {"file_path": str(self.unit / "spike.md")}
        self.assertIn(policy.WRITES, self.critical("Write", unit, places=spike))

    def test_the_scratch_is_only_for_a_step_with_bash(self):
        plan = Grant(tools=READ_TOOLS + ("Write",))
        self.assertIn(
            policy.WRITES, self.critical("Write", {"file_path": str(self.disk / "f")}, plan)
        )

    def test_the_ram_scratch_is_refused_once_full(self):
        (self.ram / "big").write_bytes(b"x" * 10)
        said = self.critical("Write", {"file_path": str(self.ram / "f")})
        self.assertIn(str(self.disk), said)
        self.assertEqual(self.critical("Write", {"file_path": str(self.disk / "f")}), "")

    def test_a_session_with_no_place_writes_nothing(self):
        inside = {"file_path": str(self.tree / "a")}
        self.assertIn(policy.WRITES, self.critical("Write", inside, places=policy.Places()))

    def test_a_write_with_no_path_is_refused(self):
        for tool_input in ({}, {"file_path": ""}, {"file_path": 3}, {"content": "x"}):
            for tool in policy.WRITE_TOOLS:
                with self.subTest(tool=tool, tool_input=tool_input):
                    self.assertIn(policy.WRITES, self.critical(tool, tool_input))


class ASecretIsOutOfEveryToolsReach(_Unit):
    """The vault, the app's config and database, and the machine's keys, for every tool."""

    def test_the_list(self):
        data, cfg = str(self.root / "data"), str(self.home / ".config")
        for path in (
            f"{data}/vault",
            f"{data}/cos.db",
            f"{data}/cos.db-wal",
            f"{data}/cos.db-shm",
            f"{cfg}/coscc",
            f"{cfg}/gh",
            f"{self.home}/.ssh",
            f"{self.home}/.aws",
            f"{self.home}/.gnupg",
        ):
            self.assertIn(path, self.secrets)

    def test_the_database_is_the_apps(self):
        from coscc.store.db import DB_FILENAME

        self.assertEqual(policy.DB_FILE, DB_FILENAME)

    def test_a_file_tool_naming_one_is_refused(self):
        key = str(self.home / ".ssh" / "id_rsa")
        for tool, tool_input in (
            ("Read", {"file_path": key}),
            ("Read", {"file_path": str(self.root / "data" / "cos.db")}),
            ("Write", {"file_path": str(self.home / ".config" / "coscc" / "env")}),
            ("Edit", {"file_path": str(self.root / "data" / "vault" / "a.age")}),
            ("Grep", {"pattern": "x", "path": str(self.home / ".aws")}),
            ("Glob", {"pattern": f"{self.home}/.gnupg/*"}),
        ):
            with self.subTest(tool=tool, tool_input=tool_input):
                self.assertIn(policy.SECRETS, self.critical(tool, tool_input))

    def test_grep_and_glob_over_a_folder_holding_one_are_refused(self):
        for tool, tool_input in (
            ("Grep", {"pattern": "x", "path": str(self.home)}),
            ("Glob", {"pattern": "**/*", "path": str(self.home)}),
            ("Glob", {"pattern": f"{self.home}/**/id_*"}),
            ("Glob", {"pattern": "../home/**"}),
            ("Glob", {"pattern": "/**/id_ed25519"}),
        ):
            with self.subTest(tool=tool, tool_input=tool_input):
                self.assertIn(policy.SECRETS, self.critical(tool, tool_input))

    def test_reading_anything_else_runs(self):
        for tool, tool_input in (
            ("Read", {"file_path": str(self.root / "elsewhere.txt")}),
            ("Read", {"file_path": str(self.home / ".config" / "other" / "x")}),
            ("Grep", {"pattern": "x", "path": str(self.root / "data" / "ws")}),
            ("Glob", {"pattern": "**/*.py"}),
            ("Glob", {"pattern": "*.md", "path": str(self.unit)}),
        ):
            with self.subTest(tool=tool, tool_input=tool_input):
                self.assertEqual(self.critical(tool, tool_input), "")

    def test_a_bash_word_or_redirect_naming_one_is_refused(self):
        cfg = self.home / ".config"
        for line in (
            "cat ~/.ssh/id_rsa",
            "cat $HOME/.aws/credentials",
            f"sqlite3 {self.root}/data/cos.db .tables",
            f"echo x > {cfg}/gh/hosts.yml",
            f"cat < {cfg}/coscc/env",
            f"ls {cfg}/cos*/vault.key",
            f"python3 - <<EOF\nopen('{self.home}/.ssh/id_rsa')\nEOF",
        ):
            with self.subTest(line=line):
                self.assertIn(policy.SECRETS, self.bash(line))

    def test_a_relative_word_a_link_or_a_cd_reaching_one_is_refused(self):
        (self.tree / "k").symlink_to(self.home / ".ssh")
        for line in (
            "cat ../home/.ssh/id_ed25519",
            "ln -s ../home/.ssh k2 && cat k2/id_*",
            "cat k/id_ed25519",
            "cat k/id_*",
            "cd ../data/ws && sqlite3 ../cos.db",
            "cd ../data && sqlite3 cos.db",
            "cd ~ && cat .ssh/id_ed25519",
            "cd $HOME && cat .aws/credentials",
            "pushd ../home && cat .ssh/id_ed25519",
            "cd ~",
            "cd /",
            "cat ~/.{ssh,aws}/id_ed25519",
            "cp -r ~/.s{s,}h /x",
            f"cat {self.home}/.{{gnupg,x}}/a",
            "gh auth token",
        ):
            with self.subTest(line=line):
                self.assertIn(policy.SECRETS, self.bash(line))

    def test_a_bash_line_near_one_runs(self):
        for line in (
            f"ls {self.home}/.config",
            "cat ~/.sshx/a",
            f"ls {self.root}/data",
            "cd ../data/ws && ls",
            "cd build && cat a.txt",
            "echo {a,b}.txt",
        ):
            with self.subTest(line=line):
                self.assertEqual(self.bash(line), "")


class OnlyTheUnitsBranchIsPushed(_Unit):
    """One push, `origin <the unit's branch>`; no other road to the host."""

    def test_the_units_branch_is_pushed(self):
        for line in (
            "git push origin feat/x",
            "git push -u origin HEAD:feat/x",
            "npm test && git push origin feat/x",
        ):
            with self.subTest(line=line):
                self.assertEqual(self.bash(line), "")

    def test_every_other_push_is_refused(self):
        for line in (
            "git push origin main",
            "git push",
            "git push origin feat/x --force",
            "git push -f origin feat/x",
            "git push origin +feat/x",
            "git push --all origin",
            "git push --tags origin feat/x",
            "git push --delete origin feat/x",
            "git push --force-with-lease origin feat/x",
            "git -C . push origin main",
            "uv run git push origin main",
            "timeout 60 git push origin main",
            "bash -c 'git push origin main'",
            "git $X origin main",
            "git send-pack origin main",
            "git http-push origin main",
            "git -c alias.p=push p origin main",
            "git config alias.p push",
            "GIT_CONFIG_COUNT=1 git status",
        ):
            with self.subTest(line=line):
                self.assertIn(policy.HOST, self.bash(line))

    def test_a_script_behind_a_wrapper_or_a_runner_is_read(self):
        push = "git push origin main"
        for line in (
            f"timeout 5 bash -c '{push}'",
            f"env sh -c '{push}'",
            f"nohup bash -c '{push}'",
            f"xargs sh -c '{push}'",
            f"sudo sh -c '{push}'",
            f"bash -c -- '{push}'",
            f"script -qc '{push}' /dev/null",
            f"watch -n 99 '{push}'",
            f"flock f -c '{push}'",
            f"ssh localhost '{push}'",
            f"busybox sh -c '{push}'",
            f"ksh -c '{push}'",
            f"fish -c '{push}'",
            f"eval '{push}'",
            f"echo '{push}' | bash",
            f"S='{push}'; bash -c \"$S\"",
            "timeout 5 sh -c 'gh pr merge 1'",
            f"bash -c \"sh -c '{push}'\"",
        ):
            with self.subTest(line=line):
                self.assertIn(policy.HOST, self.bash(line))

    def test_the_push_config_and_the_remotes_are_not_changed(self):
        for line in (
            "git config remote.origin.push refs/heads/feat/x:refs/heads/main",
            "git config remote.origin.pushurl https://example.com/o/r",
            "git config url.https://example.com/.pushInsteadOf https://x/",
            "git config core.hooksPath hooks",
            "git config push.default upstream",
            "git config --global push.default current",
            "git config --unset remote.origin.push",
            "git config set push.default current",
            "git -C . config remote.origin.push x",
            "git remote add o2 https://example.com/o/r",
            "git remote set-url origin https://example.com/o/r",
            "git remote rename origin x",
            "git remote remove origin",
            "git remote rm origin",
        ):
            with self.subTest(line=line):
                self.assertIn(policy.HOST, self.bash(line))

    def test_reading_the_config_and_the_remotes_runs(self):
        for line in (
            "git config --get user.name",
            "git config --get-all remote.origin.push",
            "git config --list",
            "git config -l",
            "git config --get-regexp remote",
            "git config user.email",
            "git config get user.name",
            "git remote -v",
            "git remote show origin",
        ):
            with self.subTest(line=line):
                self.assertEqual(self.bash(line), "")

    def test_head_spelled_with_the_branch_is_pushed_and_head_alone_is_not(self):
        for line in ("git push origin HEAD:refs/heads/feat/x", "git push -u origin HEAD:feat/x"):
            with self.subTest(line=line):
                self.assertEqual(self.bash(line), "")
        for line in (
            "git push origin HEAD",
            "git push -u origin HEAD",
            "git push",
            "git commit -qm x && git push -q origin HEAD",
            "git push origin HEAD:refs/heads/main",
            "git push origin HEAD:refs/tags/feat/x",
        ):
            with self.subTest(line=line):
                said = self.bash(line)
                self.assertIn(policy.HOST, said)
                self.assertIn("git push origin feat/x", said)

    def test_no_branch_pushes_nothing(self):
        places = replace(self.places, branch="")
        self.assertIn(policy.HOST, self.bash("git push origin feat/x", places=places))

    def test_the_lease_is_gebos_and_bound_to_its_head(self):
        head = "a" * 40
        gebo = replace(self.places, lease=head)
        leased = f"git push --force-with-lease=feat/x:{head} origin feat/x"
        self.assertEqual(self.bash(leased, places=gebo), "")
        for line in (
            "git push origin feat/x",
            f"git push --force-with-lease=feat/x:{'b' * 40} origin feat/x",
            "git push --force-with-lease origin feat/x",
        ):
            with self.subTest(line=line):
                self.assertIn(policy.HOST, self.bash(line, places=gebo))

    def test_merging_and_writing_on_the_host_are_refused(self):
        for line in (
            "gh pr merge 7",
            "gh -R o/r pr merge 7 --squash",
            "gh api -X PUT repos/o/r/pulls/7/merge",
            "gh api --method=PATCH repos/o/r/git/refs/heads/x -f sha=1",
            "gh api repos/o/r/git/refs -f ref=x",
            "gh repo sync",
            "gh release create v1",
            "gh alias set m 'pr merge'",
            "gh pr update-branch 7",
            "gh $P merge 7",
            "gh auth login",
            "gh auth logout",
            "gh auth refresh",
            "gh auth setup-git",
            "gh pr create -t x -b y",
            "gh pr edit 7 -t x",
            "gh pr close 7",
            "gh pr reopen 7",
            "gh pr review 7 --approve",
            "gh pr comment 7 -b x",
            "gh pr ready 7",
            "gh issue create -t x",
            "gh issue edit 7",
            "gh issue close 7",
            "gh issue comment 7 -b x",
            "gh issue delete 7",
            "gh repo create o/x",
            "gh repo edit --visibility public",
            "gh repo delete o/r",
            "gh repo rename x",
            "gh repo archive",
            "gh repo set-default o/r",
            "gh release list",
            "gh workflow run ci",
            "gh workflow enable ci",
            "gh workflow disable ci",
            "gh run rerun 7",
            "gh run cancel 7",
            "gh secret list",
            "gh secret set X",
            "gh variable set X",
            "gh label create x",
            "gh label edit x",
            "gh label delete x",
            "gh extension install o/x",
            "gh api graphql -f query='mutation { x }'",
            "gh some-extension",
        ):
            with self.subTest(line=line):
                self.assertIn(policy.HOST, self.bash(line))

    def test_reading_the_host_runs(self):
        for line in (
            "gh pr view 7 --json headRefOid",
            "gh pr checks 7",
            "gh api repos/o/r/pulls/7",
            "gh pr list --state open",
            "gh pr diff 7",
            "gh pr status",
            "gh issue view 7",
            "gh run view 7 --log-failed",
            "gh run list",
            "gh auth status",
            "gh pr view --help",
            "git fetch origin && git rebase origin/main",
            "git log --grep push",
            'git commit -m "$MSG"',
        ):
            with self.subTest(line=line):
                self.assertEqual(self.bash(line), "")


class RecursiveRmStaysInsideAndNoClaudeIsNested(_Unit):
    """No `rm -r` outside, and no Claude Code inside."""

    def test_rm_inside_the_places_runs(self):
        for line in (
            "rm -rf build",
            f"rm -r {self.tree}/dist",
            f"rm -rf {self.disk}/old",
            "rm -fr ./node_modules/*",
            "rm a.txt ../b.txt",
            "git rm -r --cached build",
        ):
            with self.subTest(line=line):
                self.assertEqual(self.bash(line), "")

    def test_rm_outside_or_of_a_variable_is_refused(self):
        for line in (
            "rm -rf ..",
            "rm -rf ~",
            "rm -Rf /",
            f"rm -rf {self.root}/elsewhere",
            "rm --recursive ../x",
            'rm -rf "$TMPDIR/x"',
            "rm -rf -- -x ../y",
            "find . -exec rm -rf ../y ;",
            "find .. -exec rm -rf {} +",
            "find / -maxdepth 0 -exec rm -rf {} ;",
            "ls .. | xargs rm -rf",
            "ls | xargs -I X rm -rf X",
            "rm -rf",
            "cd ../sibling && rm -rf outside",
            "pushd ../sibling && rm -rf outside",
            "cd $X && rm -rf build",
            "find .. -delete",
            f"find {self.root}/elsewhere -name x -delete",
            "watch -n 99 'rm -rf ~'",
            "ssh localhost 'rm -rf ~'",
            "env bash -c 'claude -p x'",
            "bash -c -- 'claude -p x'",
        ):
            with self.subTest(line=line):
                self.assertIn(policy.REMOVAL, self.bash(line))

    def test_a_cd_above_the_worktree_and_an_rm_there_is_refused(self):
        # Here the folder above holds the secrets, so the `cd` is what is refused.
        for line in ("cd .. && rm -rf outside", "pushd .. && rm -rf outside"):
            with self.subTest(line=line):
                self.assertNotEqual(self.bash(line), "")

    def test_a_find_or_a_cd_inside_runs(self):
        for line in (
            "find . -name '*.pyc' -delete",
            "find build -type f -delete",
            "cd build && rm -rf dist",
            f"find {self.disk}/old -delete",
        ):
            with self.subTest(line=line):
                self.assertEqual(self.bash(line), "")

    def test_claude_is_not_started_inside_a_session(self):
        for line in ("claude -p hi", "npx claude -p hi", "env X=1 claude", "uv run claude"):
            with self.subTest(line=line):
                self.assertIn(policy.REMOVAL, self.bash(line))
        self.assertEqual(self.bash("grep -rn claude coscc"), "")
        self.assertEqual(self.bash("ls ~/.claude"), "")


class TextNoShellRunsIsNotReadAsAScript(_Unit):
    """A commit's or a tag's message and a search's pattern are text: read again as a line, or
    scanned for a secret's name, they would refuse what no shell runs."""

    def test_a_message_or_a_pattern_naming_a_road_or_a_secret_runs(self):
        for line in (
            'git commit -q -m "docs: drop git push origin main from the guide"',
            'git commit -m "feat(x): rm -r ../y and the redirect > are named; cd ~ too"',
            'git add -A && git commit -qm "fix: gh pr merge stays the app\'s"',
            'git commit --message="names cd ~/.ssh && cat id_rsa"',
            'git commit -m "the vault key sits in ~/.config/coscc/vault.key"',
            'git tag -a v1 -m "git push origin main is refused"',
            'grep -rn "git push origin main" coscc',
            'rg -e "rm -rf ~" -g "*.md" .',
            'grep -A 3 "cat ~/.ssh/id_rsa" notes.md',
            "git log --oneline $(git merge-base HEAD origin/main)..HEAD",
        ):
            with self.subTest(line=line):
                self.assertEqual(self.bash(line), "")

    def test_the_same_words_where_a_shell_runs_them_are_refused(self):
        for line, rule in (
            ("bash -c 'git push origin main'", policy.HOST),
            ('git commit -qm "x" && git push origin main', policy.HOST),
            ('git commit -m "$(cat ~/.ssh/id_rsa)"', policy.SECRETS),
            ("git commit -F ~/.config/coscc/env", policy.SECRETS),
            ("grep -rn token ~/.ssh", policy.SECRETS),
            ("grep -e x -- ~/.aws/credentials", policy.SECRETS),
            ("echo 'git push origin main' | sh", policy.HOST),
            ("python3 - <<EOF\nopen('~/.config/coscc/env')\nEOF", policy.SECRETS),
            ("echo $(git merge origin/main)", "hides what runs"),
        ):
            with self.subTest(line=line):
                self.assertIn(rule, self.bash(line))


class TheScratchIsNamedByItsVariables(_Unit):
    """`$COS_SCRATCH_RAM` and `$COS_SCRATCH_DISK` are the unit's scratch, and a variable the line
    sets from them is too; any other variable stays unknown."""

    def test_rm_below_the_scratch_by_its_name_runs(self):
        for line in (
            "rm -rf $COS_SCRATCH_RAM/pr",
            'rm -rf "${COS_SCRATCH_DISK}/shot"',
            "S=$COS_SCRATCH_DISK/shot && rm -rf $S && mkdir -p $S",
            f"S={self.disk}/sim; rm -rf $S",
            "cd $COS_SCRATCH_RAM && rm -rf old",
        ):
            with self.subTest(line=line):
                self.assertEqual(self.bash(line), "")

    def test_a_name_set_elsewhere_or_pointing_out_is_refused(self):
        for line in (
            "rm -rf $COS_SCRATCH_RAM/../..",
            "COS_SCRATCH_RAM=/ && rm -rf $COS_SCRATCH_RAM",
            "COS_SCRATCH_DISK=$X; rm -rf $COS_SCRATCH_DISK/a",
            "S=$X/y && rm -rf $S",
            "rm -rf $OTHER/x",
            "S=$COS_SCRATCH_DISK; bash -c 'rm -rf $S/x'",
            "bash -c 'S=/; rm -rf $S'",
            "find $COS_SCRATCH_RAM/.. -delete",
        ):
            with self.subTest(line=line):
                self.assertIn(policy.REMOVAL, self.bash(line))

    def test_a_session_without_a_scratch_knows_no_name(self):
        places = replace(self.places, scratch=None)
        self.assertIn(policy.REMOVAL, self.bash("rm -rf $COS_SCRATCH_RAM/pr", places=places))


class TheHelperRulesHold(_Unit):
    """Who may be started, what a helper runs, and nothing in the background."""

    def test_only_a_named_helper_in_the_foreground_from_the_leading_session(self):
        self.assertEqual(self.critical("Agent", {"subagent_type": "worker"}), "")
        for tool_input, agent_id in (
            ({"subagent_type": "general-purpose"}, None),
            ({"subagent_type": "worker", "run_in_background": True}, None),
            ({"subagent_type": "worker"}, "a1"),
        ):
            with self.subTest(tool_input=tool_input, agent_id=agent_id):
                self.assertIn(policy.HELPERS, self.critical("Agent", tool_input, agent_id=agent_id))

    def test_list_agents_is_refused(self):
        self.assertIn(policy.HELPERS, self.critical("ListAgents", {}))

    def test_a_helper_runs_git_only_to_read(self):
        self.assertEqual(self.bash("git diff HEAD", agent_id="a1"), "")
        self.assertIn(policy.HELPERS, self.bash("git commit -m x", agent_id="a1"))
        self.assertEqual(self.bash("git commit -m x"), "")

    def test_a_helpers_git_behind_a_wrapper_is_read(self):
        for line in (
            "env git commit -m x",
            "timeout 5 git commit -m x",
            "nohup git commit -m x",
            "command git commit -m x",
            "echo x | xargs git add",
            "bash -c 'git commit -m x'",
        ):
            with self.subTest(line=line):
                self.assertIn(policy.HELPERS, self.bash(line, agent_id="a1"))
        for line in ("grep -rn git .", "echo git commit", "timeout 5 git log"):
            with self.subTest(line=line):
                self.assertEqual(self.bash(line, agent_id="a1"), "")

    def test_task_is_the_agent_tool(self):
        self.assertIn(policy.HELPERS, self.critical("Task", {"subagent_type": "general-purpose"}))
        self.assertEqual(self.critical("Task", {"subagent_type": "worker"}), "")

    def test_nothing_runs_in_the_background(self):
        background = {"command": "npm test", "run_in_background": True}
        self.assertIn(policy.HELPERS, self.critical("Bash", background))
        self.assertIn(policy.HELPERS, self.bash("npm run dev &"))
        self.assertIn(policy.BACKGROUND_REFUSAL, self.bash("sleep 1 & wait"))
        self.assertEqual(self.bash("npm test && echo ok 2>&1"), "")

    def test_a_helper_hands_back_its_result(self):
        handback = self.critical("SubagentHandback", {"message": "done"}, agent_id="a1")
        self.assertEqual(handback, "")


class AnMcpToolNotGrantedIsRefused(_Unit):
    """`submit` with `submits`, `peers` with `Agent`, and `Grant.mcp`; nothing else."""

    PING = "mcp__vault__vault_exec"

    def test_what_the_grant_holds_runs(self):
        self.assertEqual(self.critical(policy.SUBMIT_TOOL, {}, Grant(submits=True)), "")
        self.assertEqual(self.critical(policy.PEERS_TOOL, {}, IMPL), "")
        self.assertEqual(self.critical(self.PING, {}, Grant(mcp=(self.PING,))), "")

    def test_anything_else_is_refused(self):
        for grant, tool in (
            (Grant(), policy.SUBMIT_TOOL),
            (grant_for("review"), policy.PEERS_TOOL),
            (Grant(submits=True), "mcp__cos__other"),
            (Grant(), self.PING),
            (IMPL, "mcp__github__merge_pull_request"),
        ):
            with self.subTest(tool=tool):
                self.assertIn(policy.HELD, self.critical(tool, {}, grant))

    def test_what_skips_the_classifier_is_what_the_grant_holds(self):
        self.assertEqual(
            policy.allowed_mcp(replace(IMPL, submits=True, mcp=(self.PING,))),
            (policy.SUBMIT_TOOL, policy.PEERS_TOOL, self.PING),
        )
        self.assertEqual(policy.allowed_mcp(Grant()), ())


class AnUnreadableLineIsRefused(_Unit):
    """The shell reader is today's: what it cannot read is refused, and so is a substitution or a
    variable program on a line that names a critical road."""

    def test_an_unreadable_line(self):
        for line in ("echo 'unclosed", "cat <<EOF\nno end", "echo $(ls"):
            with self.subTest(line=line):
                self.assertIn("could not be read", self.bash(line))

    def test_a_hidden_command_on_a_line_naming_a_road(self):
        for line in (
            "echo $(git push origin main)",
            "x=`gh pr merge 7`",
            "diff <(rm -rf ..) a",
            "$CMD push origin main",
            "git push origin $(git branch --show-current)",
        ):
            with self.subTest(line=line):
                self.assertIn("hides what runs", self.bash(line))

    def test_a_hidden_command_elsewhere_runs(self):
        for line in (
            "echo $(date)",
            "x=$((1 + 2)) && echo $x",
            'cd "$(git rev-parse --show-toplevel)"',
        ):
            with self.subTest(line=line):
                self.assertEqual(self.bash(line), "")


class ACommandOffTheOldListsRuns(_Unit):
    """No list of programs and no read boundary: what is not critical is `auto`'s."""

    def test_programs_no_list_names(self):
        for line in (
            "curl -s https://example.com",
            "jq .x a.json",
            "make test",
            "cd ../sibling && ls",
            "echo x > notes.txt",
            "git -C ../sibling status",
            "cat /etc/hosts",
            "pip install x",
        ):
            with self.subTest(line=line):
                self.assertEqual(self.bash(line), "")

    def test_reads_outside_the_worktree(self):
        for tool, tool_input in (
            ("Read", {"file_path": "/etc/hosts"}),
            ("Grep", {"pattern": "x", "path": "/usr/share"}),
            ("Glob", {"pattern": "/usr/lib/*.so"}),
        ):
            with self.subTest(tool=tool):
                self.assertEqual(self.critical(tool, tool_input, Grant(tools=READ_TOOLS)), "")


class WhatGoesToTheClassifier(_Unit):
    """`classified`: an estimate of the calls `auto` sends to its classifier, as measured."""

    def test_reads_edits_inside_the_worktree_and_read_only_commands_do_not(self):
        grant = replace(IMPL, submits=True)
        for tool, tool_input in (
            ("Read", {"file_path": "/etc/hosts"}),
            ("Grep", {"pattern": "x"}),
            ("Edit", {"file_path": str(self.tree / "a.py")}),
            ("Bash", {"command": "ls -la && git status"}),
            ("Bash", {"command": "grep -rn x . | head -5"}),
            (policy.SUBMIT_TOOL, {}),
        ):
            with self.subTest(tool=tool, tool_input=tool_input):
                self.assertFalse(policy.classified(grant, self.places, tool, tool_input))

    def test_commands_writes_elsewhere_and_helpers_do(self):
        for tool, tool_input in (
            ("Bash", {"command": "npm test"}),
            ("Bash", {"command": "ls > out.txt"}),
            ("Write", {"file_path": str(self.unit / "impl.md")}),
            ("Write", {"file_path": str(self.tree / ".claude" / "x.md")}),
            ("Agent", {"subagent_type": "worker"}),
            ("SendMessage", {"to": "main"}),
            ("SubagentHandback", {}),
        ):
            with self.subTest(tool=tool, tool_input=tool_input):
                self.assertTrue(policy.classified(IMPL, self.places, tool, tool_input))
