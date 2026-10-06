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

from coscc.agent import helpers, pack, policy
from coscc.agent.policy import READ_TOOLS, Row, row_for, row_for_step

IMPL = row_for("impl")


def issued(row, roots=("/tmp/ws",), scratch=None, mcp=(), **fields) -> policy.Grant:
    """The grant a run of `row` holds, as `run.issue` gives one: `roots` its `cwd` first and where
    it writes, the scratch only with Bash, helpers only with `Agent`, `submit` when it submits and
    `peers` with helpers, then `mcp`; `fields` the rest (`branch`, `lease`, `secrets`, ...)."""
    bash = any(t in policy.EXEC_TOOLS for t in row.tools)
    helpers = row.helpers if policy.AGENT_TOOL in row.tools else ()
    return policy.Grant(
        cwd=roots[0] if roots else "",
        write=tuple(roots),
        scratch=scratch if bash else None,
        helpers=helpers,
        mcp=(
            *((policy.SUBMIT_TOOL,) if row.submits else ()),
            *((policy.PEERS_TOOL,) if helpers else ()),
            *mcp,
        ),
        tools=row.tools,
        **fields,
    )


def says(row, tool, tool_input, roots=("/tmp/ws",), agent_id=None, kind="worker", **fields) -> str:
    """What `critical` says of one call, the run's grant issued from `row` with `roots` and
    `fields`; a helper's (`agent_id`) is a `worker` unless `kind` says otherwise."""
    return policy.critical(issued(row, roots, **fields), tool, tool_input, agent_id, kind)


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
        self.assertEqual(
            row_for("idea"), Row(submits=True, prose=True, max_turns=policy.SUBMIT_TURNS)
        )
        self.assertEqual(row_for("no-such-stage"), Row())

    def test_impl_writes_its_own_artifact_and_prose_stages_do_not(self):
        self.assertFalse(IMPL.app_writes_artifact)
        self.assertTrue(row_for("spec").app_writes_artifact)


class SubmitIsTheOneToolAddedToAProseStage(unittest.TestCase):
    """The prose stages gain exactly `mcp__cos__submit` beyond their old grant, and that tool writes
    nothing and runs nothing."""

    def test_every_prose_stage_holding_a_result_gains_submit_and_nothing_else(self):
        from coscc.units import submit

        self.assertEqual(policy.SUBMIT_TOOL, submit.NAME)
        for stage in (*submit.STAGE_RESULT, submit.ROUND):
            g = row_for(stage)
            self.assertTrue(g.submits, stage)
            self.assertGreaterEqual(g.max_turns, policy.SUBMIT_TURNS, stage)
            self.assertNotIn(submit.NAME, g.tools, stage)
            self.assertEqual(says(g, submit.NAME, {"stage": stage}), "", stage)

    def test_no_other_mcp_tool_and_no_other_stage_gets_through(self):
        for stage in ("pr", "ship", "x"):
            self.assertIn(policy.HELD, says(row_for(stage), policy.SUBMIT_TOOL, {}), stage)
        for name in ("mcp__cos__other", "mcp__other__submit"):
            self.assertIn(policy.HELD, says(row_for("spec"), name, {}), name)

    def test_gebo_and_the_estimate_gain_submit_and_nothing_else(self):
        """The sessions that are no stage submit too, with at least `SUBMIT_TURNS` turns."""
        from coscc.units import submit

        for kind in ("integrate", "estimate"):
            g = row_for(kind)
            self.assertTrue(g.submits, kind)
            self.assertGreaterEqual(g.max_turns, policy.SUBMIT_TURNS, kind)
            self.assertNotIn(submit.NAME, g.tools, kind)
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
        for key in pack.rows():
            self.assertIsInstance(key, str, key)

    def test_grant_for_takes_no_mode(self):
        self.assertEqual(list(inspect.signature(row_for).parameters), ["key"])

    def test_spec_has_its_own_grant(self):
        """Without it `spec` fell to `Row()`: no tools, one turn."""
        self.assertNotEqual(row_for("spec"), Row())

    def test_spec_reads_and_only_reads(self):
        spec = row_for("spec")
        self.assertEqual(spec.tools, READ_TOOLS)
        self.assertTrue(spec.app_writes_artifact)

    def test_spec_has_the_turns_plan_has(self):
        """Copied from `plan`, not measured for `spec`."""
        self.assertEqual(row_for("spec").max_turns, row_for("plan").max_turns)
        self.assertEqual(row_for("spec").max_budget_usd, row_for("plan").max_budget_usd)

    def test_every_other_grant_is_unchanged(self):
        """The values each stage held before this unit, written out."""
        rw = READ_TOOLS + policy.WRITE_TOOLS + policy.EXEC_TOOLS
        expected = {
            "impl": Row(
                tools=rw + (policy.AGENT_TOOL, policy.SEND_MESSAGE, "vault", "codegraph"),
                max_turns=120,
                max_budget_usd=8.0,
                app_writes_artifact=False,
                submits=True,
                helpers=("scout", "worker"),
            ),
            "plan": Row(
                tools=READ_TOOLS, max_turns=40, max_budget_usd=4.0, submits=True, prose=True
            ),
            "review": Row(
                tools=READ_TOOLS + ("codegraph",),
                max_turns=40,
                max_budget_usd=4.0,
                submits=True,
                prose=True,
            ),
        }
        for stage, grant in expected.items():
            self.assertEqual(row_for(stage), grant, stage)

    def test_pr_and_ship_hold_no_grant(self):
        """Both are the PR machine's, with no session, so neither is in the table and a step of
        either would start from the locked position."""
        for stage in ("pr", "ship"):
            with self.subTest(stage=stage):
                self.assertIsNone(pack.row(stage))
                self.assertEqual(row_for(stage), Row())
                self.assertEqual(row_for_step(stage, "novel"), Row())


class OnlyImplStartsHelpers(unittest.TestCase):
    def test_impl_holds_the_agent_tool_and_no_other_grant_does(self):
        self.assertIn(policy.AGENT_TOOL, IMPL.tools)
        for stage in pack.rows():
            if stage != "impl":
                with self.subTest(stage=stage):
                    self.assertNotIn(policy.AGENT_TOOL, row_for(stage).tools)

    def test_a_helper_holds_no_tool_impl_does_not(self):
        for name in IMPL.helpers:
            tools = policy.helper_tools(name)
            with self.subTest(helper=name):
                self.assertTrue(set(tools) <= set(IMPL.tools))
                self.assertNotIn(policy.AGENT_TOOL, tools)
        self.assertEqual(policy.helper_tools("scout"), READ_TOOLS)
        self.assertEqual(IMPL.helpers, ("scout", "worker"))
        self.assertEqual(policy.helper_tools("impl"), ())

    def test_a_worker_is_sonnet_and_holds_what_a_parallel_step_needs(self):
        worker = helpers.definitions(("worker",))["worker"]
        self.assertEqual(worker["model"], "sonnet")
        self.assertEqual(
            set(worker["tools"]),
            {"Read", "Glob", "Grep", "Write", "Edit", "Bash", "SendMessage", "mcp__cos__peers"},
        )

    def test_peers_is_open_to_a_grant_that_starts_helpers_only(self):
        self.assertEqual(says(IMPL, policy.PEERS_TOOL, {}, agent_id="a1"), "")
        self.assertEqual(says(IMPL, policy.PEERS_TOOL, {}), "")
        self.assertIn(policy.HELD, says(row_for("review"), policy.PEERS_TOOL, {}))

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
        grant = Row(tools=("Write", "Read"))
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


class TheEstimateGrantOpensNothing(unittest.TestCase):
    """$2.00, no tool, no command, and a warning for the page."""

    def test_the_grant(self):
        g = row_for("estimate")
        self.assertFalse(g.opens_anything)
        self.assertEqual((g.max_turns, g.max_budget_usd), (policy.SUBMIT_TURNS, 2.0))
        self.assertIn("paid session", g.warning)
        self.assertIn("password", g.warning)


class AFeatureAddsItsSession(unittest.TestCase):
    """`add_session`: a feature's grant, submitting, with its own turns when it asks."""

    def tearDown(self):
        policy.ADDED.pop("planted", None)
        policy.OWN_TURNS.discard("planted")

    def test_an_added_session_submits_and_keeps_its_own_turns(self):
        grant = Row(max_turns=2, max_budget_usd=0.5)
        policy.add_session("planted", grant, own_turns=True)
        policy.add_session("planted", grant, own_turns=True)
        g = row_for("planted")
        self.assertTrue(g.submits)
        self.assertEqual((g.max_turns, g.max_budget_usd), (2, 0.5))
        self.assertEqual(says(g, "mcp__cos__submit", {}), "")

    def test_a_name_another_grant_holds_is_refused(self):
        with self.assertRaises(ValueError):
            policy.add_session("impl", Row(), own_turns=False)


if __name__ == "__main__":
    unittest.main()


class TheWriteBoundaryIsTheWorkspacePlusOneDirectory(unittest.TestCase):
    """Every artifact lives in the product's store, outside the workspace, so a step that writes
    its own admits **one** more directory: the step's own unit."""

    def setUp(self):
        self.grant = policy.row_for("impl")
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
        self.assertGreater(row_for("plan").max_turns, 20)

    def test_plan_still_only_reads(self):
        # Raising the ceiling must not widen what the stage may do.
        self.assertEqual(row_for("plan").tools, READ_TOOLS)


class IntentReadsWhatSpecReads(unittest.TestCase):
    """`intent` checks the idea's problem against the worktree's code, so it reads as `spec`
    reads and does nothing else."""

    def test_it_holds_the_read_tools_and_nothing_beyond_reading(self):
        grant = row_for("intent")
        self.assertEqual(grant.tools, READ_TOOLS)

    def test_its_ceilings_are_specs(self):
        grant = row_for("intent")
        self.assertEqual(grant.max_turns, 40)
        self.assertEqual(grant.max_budget_usd, 4.0)

    def test_it_holds_what_spec_holds(self):
        self.assertEqual(row_for("intent").tools, row_for("spec").tools)


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
        self.G = row_for("spike")

    def tearDown(self):
        self._tmp.cleanup()

    def d(self, tool, path):
        return says(self.G, tool, {"file_path": str(path)}, roots=(str(self.scratch),))

    def test_the_grant_is_the_one_the_spec_names(self):
        self.assertEqual(
            self.G.tools, READ_TOOLS + policy.WRITE_TOOLS + policy.EXEC_TOOLS + ("vault",)
        )
        self.assertEqual((self.G.max_turns, self.G.max_budget_usd), (80, 8.0))
        self.assertTrue(self.G.app_writes_artifact)
        self.assertIn("arbitrary code", self.G.warning)
        self.assertFalse(self.G.prose)

    def test_it_reads_the_worktree_and_the_unit(self):
        self.assertEqual(self.d("Read", self.tree / "coscc" / "policy.py"), "")
        self.assertEqual(self.d("Read", self.unit / "spec.md"), "")

    def test_it_writes_its_scratch_and_nothing_it_reads(self):
        self.assertEqual(self.d("Write", self.scratch / "probe.py"), "")
        self.assertIn(policy.WRITES, self.d("Write", self.tree / "probe.py"))
        self.assertIn(policy.WRITES, self.d("Write", self.unit / "spec.md"))


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
        novel = row_for_step("impl", "novel")
        self.assertEqual((novel.max_turns, novel.max_budget_usd), (250, 16.0))

    def test_a_routine_or_unlabelled_impl_is_unchanged(self):
        rw = (
            READ_TOOLS
            + policy.WRITE_TOOLS
            + policy.EXEC_TOOLS
            + (policy.AGENT_TOOL, policy.SEND_MESSAGE, "vault", "codegraph")
        )
        written = Row(
            tools=rw,
            max_turns=120,
            max_budget_usd=8.0,
            app_writes_artifact=False,
            submits=True,
            helpers=("scout", "worker"),
        )
        for label in ("routine", None):
            with self.subTest(label=label):
                self.assertEqual(row_for_step("impl", label), row_for("impl"))
                self.assertEqual(row_for_step("impl", label), written)

    def test_novel_differs_from_routine_in_the_ceilings_alone(self):
        """No tool, command or refusal comes with the higher ceilings."""
        routine, novel = row_for_step("impl", "routine"), row_for_step("impl", "novel")
        self.assertNotEqual(novel, routine)
        self.assertEqual(
            replace(novel, max_turns=routine.max_turns, max_budget_usd=routine.max_budget_usd),
            routine,
        )

    def test_the_stages_after_impl_read_the_label_and_keep_their_grant(self):
        for stage in ("pr", "review", "ship"):
            for label in ("routine", "novel", None):
                with self.subTest(stage=stage, label=label):
                    self.assertEqual(row_for_step(stage, label), row_for(stage))

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
                self.assertEqual(row_for_step(stage, "novel"), row_for(stage))

    def test_every_grant_that_opens_anything_keeps_both_ceilings(self):
        """No label on any stage takes a ceiling away."""
        for stage in (set(pack.rows()) - {"scout", "worker"}) | {
            "idea",
            "intent",
            "a-stage-invented-tomorrow",
        }:
            for label in self.LABELS:
                grant = row_for_step(stage, label)
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
                self.assertEqual(row_for_step("impl", label), row_for("impl"))

    def test_the_budget_does_not_stop_a_step_the_turns_would_allow(self):
        """The margin is thin, and 181–250 turns were never measured; this pins only that the budget
        is not lower than the turns at the worst rate seen."""
        novel = row_for_step("impl", "novel")
        self.assertGreater(novel.max_budget_usd, novel.max_turns * 0.0568)


class ABackgroundCommandIsRefused(unittest.TestCase):
    """A step's session ends with its turn, so nothing it starts in the background is ever read
    back."""

    STAGES = ("impl", "spike", "integrate")
    UNIT_DIR = "/data/units/slot/.cos/0130_x"

    def d(self, stage, tool_input):
        return says(row_for(stage), "Bash", tool_input, roots=("/w", self.UNIT_DIR))

    def test_run_in_background_is_refused_for_every_grant_with_bash(self):
        for stage in self.STAGES:
            with self.subTest(stage=stage):
                self.assertIn("Bash", row_for(stage).tools)
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

    def _says(self, row, tool, mcp=()):
        return says(row, tool, {}, mcp=mcp)

    def test_the_granted_name_is_allowed_and_only_that(self):
        self.assertEqual(self._says(Row(), self.PING, (self.PING,)), "")
        for tool in ("mcp__fake__other", "mcp__fake__ping2", "mcp__other__ping"):
            self.assertNotEqual(self._says(Row(), tool, (self.PING,)), "", tool)

    def test_it_does_not_grant_submit(self):
        self.assertNotEqual(self._says(Row(), policy.SUBMIT_TOOL, (self.PING,)), "")
        self.assertEqual(self._says(Row(submits=True), policy.SUBMIT_TOOL, (self.PING,)), "")

    def test_a_plain_grant_denies_every_mcp_name(self):
        for tool in (self.PING, "mcp__fake__other", policy.SUBMIT_TOOL, "mcp__x__y"):
            self.assertNotEqual(self._says(Row(), tool), "", tool)
            if tool != policy.SUBMIT_TOOL:
                self.assertNotEqual(self._says(IMPL, tool), "", tool)

    def test_mcp_is_neither_a_tool_nor_a_reason_to_open(self):
        grant = policy.Grant(mcp=(self.PING,))
        self.assertEqual(grant.tools, ())
        self.assertFalse(Row().opens_anything)

    def test_a_name_that_is_not_a_features_tool_never_enters_mcp(self):
        """`submit` and `peers` are the kernel's own, issued by the engine; no other `cos` name and
        no other spelling is a tool a grant may hold."""
        for bad in (
            "Bash",
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
                policy.Grant(mcp=(bad,))
        policy.Grant(mcp=(policy.SUBMIT_TOOL, policy.PEERS_TOOL))

    def test_replace_revalidates(self):
        with self.assertRaises(ValueError):
            replace(policy.Grant(), mcp=("Bash",))


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
        self.places = dict(
            roots=(str(self.tree), str(self.unit)),
            scratch=(str(self.ram), str(self.disk)),
            ram_cap=10,
            branch="feat/x",
            secrets=self.secrets,
            home=str(self.home),
        )
        self.grant = issued(IMPL, **self.places)

    def critical(
        self, tool, tool_input, grant=IMPL, places=None, agent_id=None, kind="worker"
    ) -> str:
        fields = self.places if places is None else places
        return policy.critical(issued(grant, **fields), tool, tool_input, agent_id, kind)

    def bash(self, command, **kw) -> str:
        return self.critical("Bash", {"command": command}, **kw)


class ARowHoldingEveryToolStaysInsideTheCriticalCalls(_Unit):
    """Whatever a row's tools say, the critical calls read only the grant: a row the owner gave
    every built-in tool reaches no secret, writes nowhere outside, pushes only its branch and
    starts no background command or nested helper."""

    EVERY = Row(
        tools=(*READ_TOOLS, *policy.WRITE_TOOLS, "Bash", policy.AGENT_TOOL, policy.SEND_MESSAGE),
        helpers=("scout",),
        submits=True,
    )

    def test_every_critical_call_is_still_refused(self):
        places = self.places
        said = [
            self.critical("Read", {"file_path": f"{self.home}/.ssh/id"}, self.EVERY, places),
            self.critical("Write", {"file_path": str(self.root / "x")}, self.EVERY, places),
            self.critical("Bash", {"command": "git push origin main"}, self.EVERY, places),
            self.critical("Bash", {"command": "ls", "run_in_background": True}, self.EVERY, places),
            self.critical("Agent", {"subagent_type": "worker"}, self.EVERY, places),
            self.critical("Agent", {"subagent_type": "scout"}, self.EVERY, places, agent_id="a"),
            self.critical("mcp__other__x", {}, self.EVERY, places),
        ]
        self.assertTrue(all(said), said)

    def test_a_tool_set_to_ask_is_refused_naming_it(self):
        row = replace(self.EVERY, asks=("Grep",))
        grant = replace(issued(row, **self.places), asks=("Grep", "mcp__vault__get"))
        for tool in ("Grep", "mcp__vault__get"):
            said = policy.critical(grant, tool, {"pattern": "x"}, None)
            self.assertEqual(said.split(":")[0], policy.ASKS)
            self.assertIn(tool, said)
            self.assertEqual(policy.lacked(said), "asks-a-person")
        self.assertEqual(policy.critical(grant, "Glob", {"pattern": "*.md"}, None), "")


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
        spike = {**self.places, "roots": (str(self.tree),)}
        inside = {"file_path": str(self.tree / "a")}
        self.assertEqual(self.critical("Write", inside, places=spike), "")
        unit = {"file_path": str(self.unit / "spike.md")}
        self.assertIn(policy.WRITES, self.critical("Write", unit, places=spike))

    def test_the_scratch_is_only_for_a_step_with_bash(self):
        plan = Row(tools=READ_TOOLS + ("Write",))
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
        self.assertIn(policy.WRITES, self.critical("Write", inside, places={"roots": ()}))

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

    def test_head_spelled_with_the_branch_is_pushed_and_any_other_head_is_not(self):
        for line in (
            "git push origin HEAD:refs/heads/feat/x",
            "git push -u origin HEAD:feat/x",
        ):
            with self.subTest(line=line):
                self.assertEqual(self.bash(line), "")
        for line in (
            "git push origin HEAD",
            "git push -u origin HEAD",
            "git push",
            "git push origin HEAD:refs/heads/main",
            "git push origin HEAD:refs/heads/feat/xy",
            "git push origin HEAD:refs/tags/feat/x",
            "git push origin HEAD:refs/heads/feat/x/y",
        ):
            with self.subTest(line=line):
                said = self.bash(line)
                self.assertIn(policy.HOST, said)
                self.assertIn("git push origin feat/x", said)

    def test_no_branch_pushes_nothing(self):
        places = {**self.places, "branch": ""}
        self.assertIn(policy.HOST, self.bash("git push origin feat/x", places=places))

    def test_the_lease_is_gebos_and_bound_to_its_head(self):
        head = "a" * 40
        gebo = {**self.places, "lease": head}
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


class MergeBaseIsNoMerge(_Unit):
    """`merge-base` reads; a substitution beside it is not one that hides a merge."""

    def test_merge_base_in_a_substitution_runs_and_merge_does_not(self):
        for line in (
            "git log --oneline $(git merge-base HEAD origin/main)..HEAD",
            "git merge-base HEAD origin/main",
        ):
            with self.subTest(line=line):
                self.assertEqual(self.bash(line), "")
        for line in ("echo $(git merge origin/main)", "x=$(git merge-base a b) && git merge $x"):
            with self.subTest(line=line):
                self.assertIn("hides what runs", self.bash(line))


class AMessageOrAPatternIsNotReadAsAScript(_Unit):
    """The message of a commit or a tag, and the pattern of a search, its own program never runs:
    the script re-read skips them, only where that program is the one the command runs. The
    secret checks never skip them."""

    def test_the_message_or_the_pattern_of_the_program_run_is_skipped(self):
        for line in (
            'git commit -q -m "docs: drop git push origin main from the guide"',
            'git add -A && git commit -qm "fix: gh pr merge and rm -rf ~ are named"',
            'git commit -am "a; git push origin main"',
            'git commit --message="rm -rf ../y; git push origin main"',
            'git commit --message "git push origin main"',
            'git tag -a v1 -m "git push origin main is refused"',
            'timeout 60 git commit -m "x; git push origin main"',
            'env X=1 git commit -m "x; git push origin main"',
            'grep -rn "git push origin main" coscc',
            'rg -e "rm -rf ~" -g "*.md" .',
            "env | grep -i -E 'anthropic|claude'",
            'grep -A 3 "x; git push origin main" notes.md',
            "bash -c 'git commit -m \"x; git push origin main\"'",
        ):
            with self.subTest(line=line):
                self.assertEqual(self.bash(line), "")

    def test_the_same_words_where_a_shell_runs_them_are_refused(self):
        for line in (
            'eval git commit -m "x; git push origin main"',
            'watch git commit -m "x; git push origin main"',
            'ssh host git commit -m "x; git push origin main"',
            'echo git commit -m "x; git push origin main" | sh',
            "bash -c 'git commit -m x; git push origin main'",
            'eval grep "x; git push origin main" .',
            'watch grep -e "x; git push origin main" .',
            'ssh host grep "x; git push origin main" .',
            'echo grep "x; git push origin main" | sh',
            'grep -f pats.txt "x; git push origin main"',
            'grep --file=pats.txt "x; git push origin main"',
            'git commit -m x -- "a; git push origin main"',
            'git commit -Cm "a; git push origin main"',
            'git commit -tm "a; git push origin main"',
            'git log -m "a; git push origin main"',
            'git commit -m "$(echo x; git push origin main)"',
        ):
            with self.subTest(line=line):
                self.assertNotEqual(self.bash(line), "")

    def test_a_skipped_word_is_still_read_for_a_secret(self):
        for line in (
            'git commit -m "the key is in ~/.config/coscc/vault.key"',
            'grep -e "~/.ssh/id_rsa" notes.md',
            "grep -f ~/.ssh/id_rsa x",
            "grep -rn token ~/.ssh",
        ):
            with self.subTest(line=line):
                self.assertIn(policy.SECRETS, self.bash(line))


class TheScratchIsKnownByItsTwoNames(_Unit):
    """`$COS_SCRATCH_RAM` and `$COS_SCRATCH_DISK` are the unit's scratch, unless the line does
    anything to the name but expand it. No other variable is followed."""

    def test_a_removal_below_the_scratch_by_its_name_runs(self):
        for line in (
            "rm -rf $COS_SCRATCH_RAM/pr",
            'rm -rf "${COS_SCRATCH_DISK}/shot"',
            "find $COS_SCRATCH_DISK/old -delete",
        ):
            with self.subTest(line=line):
                self.assertEqual(self.bash(line), "")

    def test_a_name_the_line_touches_or_any_other_variable_is_refused(self):
        for line in (
            "rm -rf $COS_SCRATCH_RAM/../..",
            "COS_SCRATCH_RAM=/ && rm -rf $COS_SCRATCH_RAM",
            "true && COS_SCRATCH_RAM=/ ; rm -rf $COS_SCRATCH_RAM/x",
            "COS_SCRATCH_RAM+=/.. ; rm -rf $COS_SCRATCH_RAM",
            "export COS_SCRATCH_DISK=/; rm -rf $COS_SCRATCH_DISK/x",
            "declare COS_SCRATCH_RAM=/; rm -rf $COS_SCRATCH_RAM/x",
            "read COS_SCRATCH_RAM < f; rm -rf $COS_SCRATCH_RAM/x",
            "printf -v COS_SCRATCH_RAM /; rm -rf $COS_SCRATCH_RAM/x",
            "for COS_SCRATCH_RAM in /; do rm -rf $COS_SCRATCH_RAM/x; done",
            "COS_SCRATCH_RAM=/ bash -c 'rm -rf $COS_SCRATCH_RAM/x'",
            "rm -rf ${COS_SCRATCH_RAM:-/}",
            "S=$COS_SCRATCH_DISK/shot && rm -rf $S",
            "rm -rf $OTHER/x",
            "find $COS_SCRATCH_RAM/.. -delete",
        ):
            with self.subTest(line=line):
                self.assertNotEqual(self.bash(line), "")

    def test_a_session_without_a_scratch_knows_neither_name(self):
        places = {**self.places, "scratch": None}
        self.assertIn(policy.REMOVAL, self.bash("rm -rf $COS_SCRATCH_RAM/pr", places=places))


class TheHelperRulesHold(_Unit):
    """Who may be started, what a helper runs, and nothing in the background."""

    def test_a_helper_holds_no_mcp_tool_but_peers_and_a_scout_writes_nothing(self):
        vault = ("mcp__vault__vault_exec",)
        for tool in ("mcp__cos__submit", *vault):
            with self.subTest(tool=tool):
                self.assertEqual(says(IMPL, tool, {}, mcp=vault), "")
                said = says(IMPL, tool, {}, mcp=vault, agent_id="a1")
                self.assertTrue(said.startswith(policy.HELD), said)
        self.assertEqual(says(IMPL, policy.PEERS_TOOL, {}, agent_id="a1"), "")
        path = str(self.tree / "x.py")
        write = {"file_path": path, "content": "x"}
        edit = {"file_path": path, "old_string": "a", "new_string": "b"}
        for tool, tool_input in (("Write", write), ("Edit", edit), ("Bash", {"command": "ls"})):
            with self.subTest(tool=tool):
                self.assertEqual(self.critical(tool, tool_input, agent_id="a1"), "")
                for kind in ("scout", ""):
                    said = self.critical(tool, tool_input, agent_id="a1", kind=kind)
                    self.assertTrue(said.startswith(policy.HELPERS), said)
        read = self.critical("Read", {"file_path": path}, agent_id="a1", kind="scout")
        self.assertEqual(read, "")

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
    """`submit` with `submits`, `peers` with helpers, and the catalog tools issued; nothing else."""

    PING = "mcp__vault__vault_exec"

    def test_what_the_grant_holds_runs(self):
        self.assertEqual(self.critical(policy.SUBMIT_TOOL, {}, Row(submits=True)), "")
        self.assertEqual(self.critical(policy.PEERS_TOOL, {}, IMPL), "")
        self.assertEqual(
            self.critical(self.PING, {}, places={**self.places, "mcp": (self.PING,)}), ""
        )

    def test_anything_else_is_refused(self):
        for grant, tool in (
            (Row(), policy.SUBMIT_TOOL),
            (row_for("review"), policy.PEERS_TOOL),
            (Row(submits=True), "mcp__cos__other"),
            (Row(), self.PING),
            (IMPL, "mcp__github__merge_pull_request"),
        ):
            with self.subTest(tool=tool):
                self.assertIn(policy.HELD, self.critical(tool, {}, grant))

    def test_what_skips_the_classifier_is_what_the_grant_holds(self):
        self.assertEqual(
            issued(replace(IMPL, submits=True), mcp=(self.PING,)).mcp,
            (policy.SUBMIT_TOOL, policy.PEERS_TOOL, self.PING),
        )
        self.assertEqual(issued(Row()).mcp, ())


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
                self.assertEqual(self.critical(tool, tool_input, Row(tools=READ_TOOLS)), "")


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
                self.assertFalse(policy.classified(issued(grant, **self.places), tool, tool_input))

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
                self.assertTrue(policy.classified(self.grant, tool, tool_input))


class ADenialNamesTheGrantItLacked(unittest.TestCase):
    def test_each_rule_names_its_grant_and_a_secret_none(self):
        g = issued(IMPL, branch="feat/x", secrets=("/data/cos.db",))
        for tool, tool_input, lacked in (
            ("Write", {"file_path": "/etc/x"}, "write"),
            ("Bash", {"command": "git push origin main"}, "push"),
            ("Agent", {"subagent_type": "general-purpose"}, "helpers"),
            ("mcp__x__y", {}, "mcp"),
            ("Read", {"file_path": "/data/cos.db"}, policy.NEVER),
        ):
            with self.subTest(tool=tool):
                self.assertEqual(policy.lacked(policy.critical(g, tool, tool_input, None)), lacked)
        self.assertEqual(policy.lacked("the auto mode classifier refused it"), "")

    def test_a_run_holding_no_helpers_starts_none(self):
        g = issued(row_for("review"))
        said = policy.critical(g, policy.AGENT_TOOL, {"subagent_type": "scout"}, None)
        self.assertIn("holds no helpers", said)


class NoCommandListIsLeft(unittest.TestCase):
    def test_policy_holds_no_command_filter(self):
        for name in (
            "IMPL_COMMANDS",
            "SPIKE_COMMANDS",
            "INTEGRATE_COMMANDS",
            "INTEGRATE_DENIED",
            "MERGE_IS_SHIPS",
            "check_command",
            "lists_of",
            "with_lists",
            "GRANTS_PREF",
            "LISTED_STAGE",
            "COMMAND_NAME",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(policy, name))

    def test_a_grant_carries_no_command_field(self):
        fields = set(Row.__dataclass_fields__) | set(policy.Grant.__dataclass_fields__)
        self.assertFalse(fields & {"commands", "denied", "push_needs_lease", "protected"})
        self.assertFalse(Row().opens_anything)
        self.assertTrue(Row(tools=("Read",)).opens_anything)


class ProgramsOfNamesWhatEachCommandRuns(unittest.TestCase):
    def test_it_names_each_program_past_assignments_and_directories(self):
        line = "A=1 /usr/bin/psql -c 'x; y' | grep z && echo ok"
        self.assertEqual(policy.programs_of(line), ("psql", "grep", "echo"))

    def test_a_line_it_cannot_read_names_none(self):
        self.assertEqual(policy.programs_of("echo 'unclosed"), ())


class TheStrictCheckRefusesAnySubstitution(_Unit):
    """`strict` is the vault's: its line runs with secrets in it, so what the line spells must be
    what runs."""

    def strict(self, line: str) -> str:
        return policy.bash_refused(self.grant, line, strict=True)

    def test_every_substitution_is_refused(self):
        for line in ("echo $(date)", "echo `date`", "diff <(ls) a", "tee >(cat)", "echo $((1+2))"):
            with self.subTest(line=line):
                self.assertIn("substitution", self.strict(line))

    def test_an_unreadable_line_is_refused(self):
        self.assertIn("could not be read", self.strict("echo 'open"))

    def test_a_plain_line_runs(self):
        for line in ("curl -s https://example.com", "make test", "echo $HOME | wc -c"):
            with self.subTest(line=line):
                self.assertEqual(self.strict(line), "")

    def test_the_critical_roads_still_hold(self):
        store, key = self.secrets[0], f"{self.home}/.config/coscc/vault.key"
        for line in (
            f"cat {store}/ws/x.age",
            f"base64 {key}",
            "git push origin main",
            "gh pr merge 7",
            "rm -rf /etc/x",
            "echo hi &",
        ):
            with self.subTest(line=line):
                self.assertNotEqual(self.strict(line), "", line)


class GeboHoldsTheCeilingsAndTheWarning(unittest.TestCase):
    """The lease and the merge refusals are `critical`'s (`OnlyTheUnitsBranchIsPushed`); the grant
    holds only what the page shows and the ceilings."""

    def test_the_grant(self):
        g = row_for("integrate")
        self.assertEqual((g.max_turns, g.max_budget_usd), (120, 8.0))
        self.assertFalse(g.app_writes_artifact)
        self.assertEqual(g.warning, pack.row("integrate")["warning"])
        self.assertIn("force-pushes", g.warning)
        self.assertEqual(g.tools, READ_TOOLS + policy.WRITE_TOOLS + policy.EXEC_TOOLS)

    def test_a_merge_is_refused_for_every_session(self):
        for stage in ("impl", "integrate", "spike"):
            self.assertNotEqual(says(row_for(stage), "Bash", {"command": "gh pr merge 7"}), "")


class TheShellIsReadAsTheShellReadsIt(_Unit):
    """What a person's own commands spell is not refused by a reader that misreads quotes,
    heredocs or `${...}`."""

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
        "npm test &>/dev/null",
        "echo x > out.txt",
        "curl -s https://example.com | jq .",
    )
    UNREADABLE = (
        "echo 'unclosed",
        "cat <<EOF\nno end line",
        "echo $(date",
        'echo "unclosed',
        "echo ${X",
        "echo x >",
    )

    def test_every_sample_that_should_pass_passes(self):
        for command in self.PASS:
            with self.subTest(command=command):
                self.assertEqual(self.bash(command), "")

    def test_a_line_that_cannot_be_read_is_refused_not_guessed(self):
        for command in self.UNREADABLE:
            with self.subTest(command=command):
                self.assertIn("could not be read", self.bash(command))
