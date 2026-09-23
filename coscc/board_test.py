"""Tests for the board, run against the only real set of work units there is.

This repository's own `.cos/` is the fixture. That is deliberate: the thing most likely to
break here is not the parsing but the *agreement* between this module and
`.claude/scripts/cos.mjs`, and a hand-built fixture would keep passing after the two drift
apart. `spec.md` C8 is the concern these tests stand against.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc import board, harness
from coscc.board import Unavailable

REPO = Path(__file__).resolve().parent.parent

STAGES = ["idea", "intent", "spec", "plan", "impl", "pr", "review", "ship"]


def run(coro):
    return asyncio.run(coro)


def _by_stage(u) -> dict:
    return {row["stage"]: row["status"] for row in u["stages"]}


class TheRepositoryReadsAsABoard(unittest.TestCase):
    def test_every_unit_carries_all_eight_stages(self):
        data = run(board.read(REPO))
        self.assertEqual(data["stages"], STAGES)
        self.assertEqual(data["count"], len([d for d in (REPO / ".cos").iterdir() if d.is_dir()]))
        for unit in data["units"]:
            got = [row["stage"] for row in unit["stages"]]
            self.assertEqual(got, STAGES, f"{unit['name']} is missing a stage")

    def test_a_stage_with_no_artifact_reads_as_not_started(self):
        data = run(board.read(REPO))
        # Chosen by shape rather than by number: any unit closed under the old three-stage
        # loop will do, and naming one pins the test to a numbering that can change.
        unit = next(u for u in data["units"] if _by_stage(u)["plan"] == "done")
        by_stage = _by_stage(u=unit)
        self.assertEqual(by_stage["intent"], "accepted")
        self.assertEqual(by_stage["plan"], "done")
        for stage in ("impl", "pr", "review", "ship"):
            self.assertEqual(by_stage[stage], "not started")

    def test_the_next_action_is_carried_through_not_recomputed(self):
        # Two answers to "what next" is exactly the drift `board.py` exists to avoid, so
        # the value must be the script's, verbatim.
        data = run(board.read(REPO))
        unit = next(u for u in data["units"] if _by_stage(u)["plan"] == "done")
        self.assertEqual(unit["next"], "finished")
        self.assertFalse(unit["blocked"])

    def test_idea_is_marked_optional_so_a_reader_need_not_know_the_rule(self):
        data = run(board.read(REPO))
        first = data["units"][0]["stages"][0]
        self.assertEqual(first["stage"], "idea")
        self.assertTrue(first["optional"])


class TheStageListNeedsNoWorkspace(unittest.TestCase):
    """`0004_no-setting-says-which-model-runs-a-stage`: Settings asks for the stages with
    no workspace, and gets the script's list, in the script's order."""

    def test_stages_is_the_script_status_list(self):
        self.assertEqual(run(board.stages()), run(board.read(REPO))["stages"])
        self.assertEqual(run(board.stages()), STAGES)

    def test_no_node_is_unavailable_not_a_crash(self):
        with mock.patch.object(board, "_run", side_effect=OSError("no node")):
            with self.assertRaises(Unavailable):
                run(board.stages())


class ThePhaseIsCarriedFromTheScript(unittest.TestCase):
    def test_an_idea_only_unit_arrives_as_pre_intent_with_no_problems(self):
        with tempfile.TemporaryDirectory() as d:
            unit = Path(d) / ".cos" / "0001_fresh"
            unit.mkdir(parents=True)
            (unit / "idea.md").write_text("# Idea: fresh\nAuthor: x. Status: accepted.\n")
            data = run(board.read(d))
            [u] = data["units"]
            self.assertEqual(u["phase"], "pre-intent")
            self.assertEqual(u["problems"], [])

    def test_every_unit_in_this_repository_has_started(self):
        data = run(board.read(REPO))
        self.assertEqual({u["phase"] for u in data["units"]}, {"started"})


class QuestionsAreCarriedFromTheScript(unittest.TestCase):
    """`0016` R7. The board forwards what `cos.mjs` decided and recounts nothing."""

    TEXT = (
        "# Intent: q\nAuthor: t. Type: feat. Status: accepted.\n\n"
        "## Open questions\n\n1. One?\n2. Two?\n3. Three?\n\n"
        "## Answers\n\n### Câu 2\nAnswered by: A. Date: 2026-09-23. Via: product.\n\nCó.\n"
    )

    def test_open_and_questions_arrive_as_the_script_sent_them(self):
        with tempfile.TemporaryDirectory() as d:
            unit = Path(d) / ".cos" / "0001_q"
            unit.mkdir(parents=True)
            (unit / "intent.md").write_text(self.TEXT, encoding="utf-8")
            [u] = run(board.read(d))["units"]
            self.assertEqual(u["open"], 2)
            self.assertEqual(u["counted"], "intent.md")
            self.assertEqual(
                [(q["artifact"], q["n"], q["answered"]) for q in u["questions"]],
                [("intent.md", 1, False), ("intent.md", 2, True), ("intent.md", 3, False)],
            )

    def test_a_unit_with_no_questions_reads_as_none_open(self):
        with tempfile.TemporaryDirectory() as d:
            unit = Path(d) / ".cos" / "0001_q"
            unit.mkdir(parents=True)
            (unit / "intent.md").write_text("# I\nAuthor: t. Type: feat. Status: draft.\n")
            [u] = run(board.read(d))["units"]
            self.assertEqual((u["open"], u["questions"], u["counted"]), (0, [], ""))


REVIEW_TWO_ROUNDS = (
    "# Review\nStatus: changes-requested.\n\n"
    "## Round 1\n\nReviewed: abcdef1. Verdict: changes-requested.\n\n"
    "### Findings\n\n- F1 [open] the first thing\n- F2 [open] the second thing\n\n"
    "## Round 2\n\nReviewed: abcdef2. Verdict: changes-requested.\n\n"
    "### Findings\n\n- F1 [fixed abcdef2] the first thing\n- F2 [open] the second thing\n"
)


class PullRequestAndRoundsAreCarriedFromTheScript(unittest.TestCase):
    """`0021`. The board forwards the PR and each round's text; it splits nothing itself."""

    def test_two_rounds_arrive_with_their_text_and_the_pr_number(self):
        with tempfile.TemporaryDirectory() as d:
            unit = Path(d) / ".cos" / "0001_q"
            unit.mkdir(parents=True)
            (unit / "pr.md").write_text(
                "# PR\nStatus: accepted.\nPR: https://github.com/o/r/pull/7\n", encoding="utf-8"
            )
            (unit / "review.md").write_text(REVIEW_TWO_ROUNDS, encoding="utf-8")
            [u] = run(board.read(d))["units"]
            self.assertEqual(u["pr"], {"url": "https://github.com/o/r/pull/7", "number": 7})
            self.assertEqual([(r["n"], r["verdict"]) for r in u["rounds"]],
                             [(1, "changes-requested"), (2, "changes-requested")])
            self.assertTrue(u["rounds"][0]["text"].startswith("## Round 1\n"))
            self.assertIn("- F2 [open] the second thing", u["rounds"][0]["text"])
            self.assertNotIn("## Round 2", u["rounds"][0]["text"])
            self.assertIn("- F1 [fixed abcdef2] the first thing", u["rounds"][1]["text"])

    def test_the_script_and_the_runner_cut_rounds_at_the_same_place(self):
        """`0021` plan, Risk 8. `coscc/runner.py` `_rounds` is an older second reading of
        round edges; until it goes, the text posted and the text preserved must match."""
        from coscc.runner import _rounds

        text = REVIEW_TWO_ROUNDS + "\n## Answers\n\n### Câu 1\nnot a round\n"
        with tempfile.TemporaryDirectory() as d:
            unit = Path(d) / ".cos" / "0001_q"
            unit.mkdir(parents=True)
            (unit / "review.md").write_text(text, encoding="utf-8")
            [u] = run(board.read(d))["units"]
            self.assertEqual([r["text"] for r in u["rounds"]], _rounds(text))

    def test_an_older_script_that_sends_neither_reads_as_none(self):
        async def fake_run(argv, timeout):
            return 0, '{"stages": [], "units": [{"name": "0001_q", "artifacts": {}}]}', ""

        with tempfile.TemporaryDirectory() as d, mock.patch.object(board, "_run", fake_run):
            [u] = run(board.read(d))["units"]
            self.assertEqual((u["pr"], u["rounds"]), (None, []))


class AnEmptyWorkspaceIsAnAnswerNotAFailure(unittest.TestCase):
    def test_a_directory_with_no_cos_reports_why_rather_than_raising(self):
        with tempfile.TemporaryDirectory() as d:
            data = run(board.read(d))
            self.assertEqual(data["units"], [])
            self.assertEqual(data["count"], 0)
            self.assertIn("no .cos/", data["empty_because"])

    def test_a_cos_directory_with_no_units_says_something_different(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".cos").mkdir()
            data = run(board.read(d))
            self.assertEqual(data["units"], [])
            self.assertIn("holds no work units", data["empty_because"])

    def test_a_directory_that_is_not_there_names_itself(self):
        data = run(board.read("/nonexistent-workspace-for-a-test"))
        self.assertEqual(data["units"], [])
        self.assertIn("no such directory", data["empty_because"])

    def test_a_populated_board_leaves_empty_because_unset(self):
        data = run(board.read(REPO))
        self.assertIsNone(data["empty_because"])


class TheWorkspaceCopyOfTheHarnessIsNeverRun(unittest.TestCase):
    def test_a_script_planted_in_the_workspace_is_ignored(self):
        """A workspace is a cloned repository, so its `.claude/` is someone else's code.

        The planted script writes a file and exits non-zero. If the board ever ran the
        copy it found in the workspace, both the marker and the failure would show.
        """
        with tempfile.TemporaryDirectory() as d:
            workspace = Path(d)
            scripts = workspace / ".claude" / "scripts"
            scripts.mkdir(parents=True)
            marker = workspace / "PLANTED"
            (scripts / "cos.mjs").write_text(
                "import { writeFileSync } from 'node:fs'\n"
                f"writeFileSync({str(marker)!r}, 'ran')\n"
                "process.exit(3)\n"
            )
            (workspace / ".cos").mkdir()

            data = run(board.read(workspace))

            self.assertFalse(marker.exists(), "the workspace's own cos.mjs was executed")
            self.assertEqual(data["units"], [])
            self.assertIn("holds no work units", data["empty_because"])


class AnUnreadableBoardRaisesRatherThanReturningEmpty(unittest.TestCase):
    def test_a_missing_harness_script_is_not_reported_as_an_empty_board(self):
        # "No units" and "I could not look" are different answers, and a page that shows
        # the first when it means the second is the failure this test names.
        # Both roots are moved, not just one: `harness.root()` falls back to the
        # checkout, so blanking only the packaged side would still find a real script.
        originals = (harness.PACKAGE_HARNESS, harness.CHECKOUT_HARNESS)
        harness.PACKAGE_HARNESS = Path("/nonexistent/packaged")
        harness.CHECKOUT_HARNESS = Path("/nonexistent/checkout")
        try:
            with self.assertRaises(Unavailable) as caught:
                run(board.read(REPO))
            self.assertIn("harness script is missing", str(caught.exception))
        finally:
            harness.PACKAGE_HARNESS, harness.CHECKOUT_HARNESS = originals

    def test_the_child_environment_carries_no_secrets(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("COS_REVIEW_ROUNDS", None)
            env = board._child_env()
        self.assertEqual(set(env), {"PATH", "HOME", "LC_ALL", "NO_COLOR"})

    def test_the_review_round_limit_is_the_one_setting_passed_down(self):
        """`0015`: `COS_REVIEW_ROUNDS` reaches `cos.mjs`, and nothing else new does."""
        extra = {"COS_REVIEW_ROUNDS": "5", "GH_TOKEN": "secret", "COS_MODEL": "m"}
        with mock.patch.dict(os.environ, extra):
            env = board._child_env()
        self.assertEqual(env["COS_REVIEW_ROUNDS"], "5")
        self.assertEqual(set(env), {"PATH", "HOME", "LC_ALL", "NO_COLOR", "COS_REVIEW_ROUNDS"})


if __name__ == "__main__":
    unittest.main()


class TheGateIsAskedByTheApp(unittest.TestCase):
    """`.claude/CLAUDE.md` invariant 2, which this app walked past until 2026-09-23.

    The fixture is this repository's own `.cos/`, for the reason in the module docstring:
    what breaks here is agreement with `.claude/scripts/cos.mjs`, and a hand-built unit
    would keep passing after the two drift apart.
    """

    def test_an_open_gate_comes_back_open_and_says_so(self):
        # A unit closed under the old loop: every stage behind it is settled, so any
        # stage's gate is open. Chosen by shape, not by number.
        data = run(board.read(REPO))
        unit = next(u for u in data["units"] if _by_stage(u)["plan"] == "done")
        allowed, said = run(board.gate(REPO, unit["name"], "impl"))
        self.assertTrue(allowed, said)
        self.assertIn("open", said.lower())

    def test_a_blocked_gate_comes_back_blocked_and_carries_the_reasons(self):
        # A unit that does not exist cannot have an accepted intent, so every stage after
        # the first is blocked -- and the reasons are what a caller has to be able to show.
        allowed, said = run(board.gate(REPO, "9999_no-such-unit-here", "ship"))
        self.assertFalse(allowed)
        self.assertTrue(said.strip(), "a blocked gate that says nothing explains nothing")

    def test_a_non_zero_exit_is_an_answer_here_and_a_failure_in_read(self):
        """The one difference between the two callers of `_run`, pinned.

        `read` raises when the script exits non-zero: it asked and got no answer. `gate`
        returns: exit 1 *is* the answer. Extracting `_run` is only safe while this holds.
        """
        with tempfile.TemporaryDirectory() as tmp:
            # A root with no `.cos/` reads fine -- it is a known answer, no units.
            self.assertEqual(run(board.read(tmp))["count"], 0)
            allowed, said = run(board.gate(tmp, "0001_nothing-here", "spec"))
            self.assertFalse(allowed)
            self.assertTrue(said.strip())

    def _store_unit(self, tmp: str) -> str:
        """A unit in a store-shaped root: artifacts only, no git — what `0014` built."""
        name = "0001_needs-a-review"
        d = Path(tmp) / ".cos" / name
        d.mkdir(parents=True)
        (d / "intent.md").write_text("# I\nAuthor: t. Type: feat. Status: accepted.\n")
        for f in ("spec.md", "plan.md", "impl.md"):
            (d / f).write_text("Status: accepted.\n")
        (d / "pr.md").write_text("PR: https://github.com/o/r/pull/3. Status: accepted.\n")
        return name

    def test_without_a_repo_the_review_gate_says_so_instead_of_reading_the_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            name = self._store_unit(tmp)
            allowed, said = run(board.gate(tmp, name, "review"))
            self.assertFalse(allowed)
            self.assertIn("no repository given", said)

    def test_the_repo_reaches_the_gate_as_repo(self):
        """`0015`: the workspace is where `review` asks gh about the pull request."""
        seen = {}

        async def fake_run(argv, timeout):
            seen["argv"], seen["timeout"] = argv, timeout
            return 0, "open", ""

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(board, "_run", fake_run):
            name = self._store_unit(tmp)
            run(board.gate(tmp, name, "review", repo=tmp))
        argv = seen["argv"]
        self.assertEqual(argv[argv.index("--repo") + 1], str(Path(tmp).resolve()))
        self.assertEqual(seen["timeout"], board.GATE_TIMEOUT)

    def test_a_missing_script_is_unavailable_not_a_closed_gate(self):
        """A gate that cannot be asked must not read as a gate that said no.

        The two are opposite instructions to a caller: one is "fix the install", the other
        is "finish the earlier stage".
        """
        with tempfile.TemporaryDirectory() as tmp:
            original = harness.script
            harness.script = lambda: Path(tmp) / "not-here.mjs"
            try:
                with self.assertRaises(Unavailable):
                    run(board.gate(REPO, "0001_no-session-management", "spec"))
            finally:
                harness.script = original


class TheNextStageIsAskedNotWorkedOut(unittest.TestCase):
    """`0024`. The run button's stage is `cos.mjs next`'s answer, copied through."""

    def _unit(self, tmp: str, files: dict[str, str]) -> str:
        name = "0001_what-comes-next"
        d = Path(tmp) / ".cos" / name
        d.mkdir(parents=True)
        for f, text in files.items():
            (d / f).write_text(text)
        return name

    def _script_says(self, tmp: str, name: str, *extra: str) -> dict:
        import json
        import subprocess

        out = subprocess.run(
            ["node", str(harness.script()), "--root", tmp, "next", name, *extra],
            capture_output=True, text=True, check=True,
        ).stdout
        return json.loads(out)

    def test_the_stage_is_the_one_the_script_printed(self):
        chains = [
            {"intent.md": "# I\nAuthor: t. Type: fix. Status: accepted.\n"},
            {"intent.md": "# I\nAuthor: t. Type: fix. Status: draft.\n"},
            {
                "intent.md": "# I\nAuthor: t. Type: fix. Status: accepted.\n",
                "spec.md": "Status: skipped.\n", "plan.md": "Status: accepted.\n",
            },
            {
                "intent.md": "# I\nAuthor: t. Type: fix. Status: accepted.\n",
                "spec.md": "Status: accepted.\n", "plan.md": "Status: accepted.\n",
                "impl.md": "Status: accepted.\n",
                "pr.md": "PR: https://github.com/o/r/pull/3. Status: accepted.\n",
                "review.md": "# R\nStatus: changes-requested.\n\n## Round 1\n\n"
                "Reviewed: aaaaaaa. Verdict: changes-requested.\n\n### Findings\n\n- F1 [open] x\n",
            },
        ]
        for files in chains:
            with self.subTest(files=sorted(files)), tempfile.TemporaryDirectory() as tmp:
                name = self._unit(tmp, files)
                script = self._script_says(tmp, name)
                got = run(board.next_step(tmp, name))
                self.assertEqual(got["stage"], script["stage"])
                self.assertEqual(got["action"], script["action"])
                # `read` carries the file-only stage for the card, from the same script.
                [u] = run(board.read(tmp))["units"]
                self.assertEqual(u["next_stage"], script["stage"] if files.get("review.md") is None else "")

    def test_the_repo_reaches_next_as_repo_with_the_gate_timeout(self):
        seen = {}

        async def fake_run(argv, timeout):
            seen["argv"], seen["timeout"] = argv, timeout
            return 0, '{"unit": "u", "stage": "impl", "action": "a", "blocked": true}', ""

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(board, "_run", fake_run):
            got = run(board.next_step(tmp, "0001_x", repo=tmp))
        argv = seen["argv"]
        self.assertEqual(argv[argv.index("next") + 1], "0001_x")
        self.assertEqual(argv[argv.index("--repo") + 1], str(Path(tmp).resolve()))
        self.assertEqual(seen["timeout"], board.GATE_TIMEOUT)
        self.assertEqual(got["stage"], "impl")

    def test_read_asks_the_script_for_status_only_so_the_board_never_waits_on_gh(self):
        calls = []
        original = board._run

        async def spy(argv, timeout):
            calls.append(argv)
            return await original(argv, timeout)

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(board, "_run", spy):
            self._unit(tmp, {"intent.md": "# I\nAuthor: t. Type: fix. Status: accepted.\n"})
            run(board.read(tmp))
        self.assertEqual([a[a.index("--root") + 2:] for a in calls], [["status", "--json"]])

    def test_a_unit_that_is_not_there_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(Unavailable):
                run(board.next_step(tmp, "0009_not-here"))
