"""Tests for the board, run against the only real set of work units there is.

This repository's own `.cos/` is the fixture. That is deliberate: the thing most likely to break
here is not the parsing but the *agreement* between this module and `coscc.loop`, and a
hand-built fixture would keep passing after the two drift apart."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.units.test_meta import WithSnapshot, loop, snapshot_of
from coscc.units import board as _board

board = WithSnapshot(_board)
from coscc.agent import harness
from coscc.loop import run as loop_run
from coscc.units.board import Unavailable

REPO = Path(__file__).resolve().parents[2]

STAGES = ["idea", "intent", "spec", "spike", "plan", "impl", "pr", "review", "ship"]


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

    def test_the_type_is_the_loops_verbatim(self):
        # Copied from `status --json`, never read from `intent.md` here.
        data = run(board.read(REPO))
        out = loop(
            "--root",
            str(REPO),
            "--state",
            "-",
            "status",
            "--json",
            input=json.dumps(snapshot_of(REPO)),
        ).stdout
        want = {u["name"]: str(u.get("type") or "") for u in json.loads(out)["units"]}
        self.assertEqual({u["name"]: u["type"] for u in data["units"]}, want)
        self.assertIn("feat", want.values())

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
    """Settings asks for the stages with no workspace, and gets the script's list, in the script's
    order."""

    def test_stages_is_the_script_status_list(self):
        self.assertEqual(run(board.stages()), run(board.read(REPO))["stages"])
        self.assertEqual(run(board.stages()), STAGES)

    def test_a_loop_that_cannot_start_is_unavailable_not_a_crash(self):
        with mock.patch.object(board, "_run", side_effect=OSError("cannot start")):
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
    """The board forwards what the loop decided and recounts nothing."""

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

    def test_who_answered_and_every_answer_in_force_are_copied(self):
        """`by` on each question and `answers` on the unit, from the joined answer the loop sent;
        the last block for a number is the one in force."""
        text = (
            self.TEXT
            + "\n### Câu 2\nAnswered by: Jera. Date: 2026-09-25. Via: precedent.\n\nKhông.\n"
        )
        with tempfile.TemporaryDirectory() as d:
            unit = Path(d) / ".cos" / "0001_q"
            unit.mkdir(parents=True)
            (unit / "intent.md").write_text(text, encoding="utf-8")
            [u] = run(board.read(d))["units"]
            self.assertEqual([q["by"] for q in u["questions"]], ["", "Jera", ""])
            self.assertEqual(
                u["answers"],
                [
                    {
                        "artifact": "intent.md",
                        "n": 2,
                        "question": "Two?",
                        "by": "Jera",
                        "date": "2026-09-25",
                        # Whose it is, as the app's import classified `Via: precedent.`
                        "via": "precedent",
                        "text": "Không.",
                        "authority": "agent",
                    }
                ],
            )

    def test_an_older_script_sends_no_answers_and_no_names(self):
        async def fake_run(argv, timeout, stdin=None):
            return (
                0,
                (
                    '{"stages": [], "units": [{"name": "0001_q", "questions": '
                    '[{"artifact": "spec.md", "n": 1, "text": "x", "answered": true}]}]}'
                ),
                "",
            )

        with tempfile.TemporaryDirectory() as d, mock.patch.object(board, "_run", fake_run):
            [u] = run(board.read(d))["units"]
            self.assertEqual((u["answers"], u["questions"][0]["by"]), ([], ""))


REVIEW_TWO_ROUNDS = (
    "# Review\nStatus: changes-requested.\n\n"
    "## Round 1\n\nReviewed: abcdef1. Verdict: changes-requested.\n\n"
    "### Findings\n\n- F1 [open] the first thing\n- F2 [open] the second thing\n\n"
    "## Round 2\n\nReviewed: abcdef2. Verdict: changes-requested.\n\n"
    "### Findings\n\n- F1 [fixed abcdef2] the first thing\n- F2 [open] the second thing\n"
)


class PullRequestAndRoundsAreCarriedFromTheScript(unittest.TestCase):
    """The board forwards the PR and each round's text; it splits nothing itself."""

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
            self.assertEqual(
                [(r["n"], r["verdict"]) for r in u["rounds"]],
                [(1, "changes-requested"), (2, "changes-requested")],
            )
            self.assertTrue(u["rounds"][0]["text"].startswith("## Round 1\n"))
            self.assertIn("- F2 [open] the second thing", u["rounds"][0]["text"])
            self.assertNotIn("## Round 2", u["rounds"][0]["text"])
            self.assertIn("- F1 [fixed abcdef2] the first thing", u["rounds"][1]["text"])

    def test_each_round_carries_its_finding_counts(self):
        # Counted off `parseReview`'s findings, not parsed again here.
        with tempfile.TemporaryDirectory() as d:
            unit = Path(d) / ".cos" / "0001_q"
            unit.mkdir(parents=True)
            (unit / "review.md").write_text(REVIEW_TWO_ROUNDS, encoding="utf-8")
            [u] = run(board.read(d))["units"]
            self.assertEqual(
                [(r["findings"], r["findings_open"]) for r in u["rounds"]], [(2, 2), (2, 1)]
            )

    def test_the_script_and_the_runner_cut_rounds_at_the_same_place(self):
        """`coscc/runner/step.py` `_rounds` is an older second reading of round edges; until it
        goes, the text posted and the text preserved must match."""
        from coscc.runner.review import _rounds

        text = REVIEW_TWO_ROUNDS + "\n## Answers\n\n### Câu 1\nnot a round\n"
        with tempfile.TemporaryDirectory() as d:
            unit = Path(d) / ".cos" / "0001_q"
            unit.mkdir(parents=True)
            (unit / "review.md").write_text(text, encoding="utf-8")
            [u] = run(board.read(d))["units"]
            self.assertEqual([r["text"] for r in u["rounds"]], _rounds(text))

    def test_rounds_carry_what_cos_mjs_read_as_unfinished(self):
        """Round 2 lists the first finding alone, so it drops the second; the board carries what the
        script read and works out nothing itself."""
        text = REVIEW_TWO_ROUNDS.replace(
            "- F1 [fixed abcdef2] the first thing\n- F2 [open] the second thing\n",
            "- F1 [open] the first thing\n",
        )
        with tempfile.TemporaryDirectory() as d:
            unit = Path(d) / ".cos" / "0001_q"
            unit.mkdir(parents=True)
            (unit / "review.md").write_text(text, encoding="utf-8")
            [u] = run(board.read(d))["units"]
            self.assertEqual(
                [(r["dropped"], r["unfinished"]) for r in u["rounds"]],
                [([], False), (["F2"], True)],
            )
            (unit / "review.md").write_text(REVIEW_TWO_ROUNDS, encoding="utf-8")
            [u] = run(board.read(d))["units"]
            self.assertEqual(
                [(r["dropped"], r["unfinished"]) for r in u["rounds"]], [([], False), ([], False)]
            )

    def test_an_older_script_that_sends_neither_reads_as_none(self):
        async def fake_run(argv, timeout, stdin=None):
            return 0, '{"stages": [], "units": [{"name": "0001_q", "artifacts": {}}]}', ""

        with tempfile.TemporaryDirectory() as d, mock.patch.object(board, "_run", fake_run):
            [u] = run(board.read(d))["units"]
            self.assertEqual((u["pr"], u["rounds"]), (None, []))


class TheStageAtAndWhyAreCarriedFromTheScript(unittest.TestCase):
    """`at` and `next.why` arrive as `status --json` sent them."""

    def test_at_and_why_are_the_scripts(self):
        with tempfile.TemporaryDirectory() as d:
            for name, files in {
                "0001_open": {"intent.md": "# I\nType: feat. Status: accepted.\n"},
                "0002_draft": {
                    "intent.md": "# I\nType: feat. Status: accepted.\n",
                    "spec.md": "# S\nStatus: draft.\n",
                },
            }.items():
                unit = Path(d) / ".cos" / name
                unit.mkdir(parents=True)
                for f, text in files.items():
                    (unit / f).write_text(text, encoding="utf-8")
            said = json.loads(
                loop(
                    "--root",
                    d,
                    "--state",
                    "-",
                    "status",
                    "--json",
                    input=json.dumps(snapshot_of(d)),
                ).stdout
            )
            got = run(board.read(d))["units"]
        self.assertEqual(
            [(u["at"], u["why"]) for u in got],
            [(u["at"], u["next"]["why"]) for u in said["units"]],
        )
        self.assertEqual(
            [(u["at"], u["why"]) for u in got], [("spec", "missing"), ("spec", "draft")]
        )

    def test_an_older_script_that_sends_neither_reads_as_empty(self):
        async def fake_run(argv, timeout, stdin=None):
            return 0, '{"stages": [], "units": [{"name": "0001_q", "next": {"stage": ""}}]}', ""

        with tempfile.TemporaryDirectory() as d, mock.patch.object(board, "_run", fake_run):
            [u] = run(board.read(d))["units"]
            self.assertEqual((u["at"], u["why"]), ("", ""))


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


class TheWorkspaceCopyOfTheLoopIsNeverRun(unittest.TestCase):
    def test_a_loop_planted_in_the_workspace_is_ignored(self):
        """A workspace is a cloned repository, so a `coscc/` in it is someone else's code.

        The planted package writes a file and exits non-zero. The board runs with the
        workspace as its working directory, where a bare `python -m coscc.loop` would find the
        planted copy first; if the board ever ran it, both the marker and the failure would show.
        """
        with tempfile.TemporaryDirectory() as d:
            workspace = Path(d).resolve()
            planted = workspace / "coscc" / "loop"
            planted.mkdir(parents=True)
            marker = workspace / "PLANTED"
            (workspace / "coscc" / "__init__.py").write_text("")
            (planted / "__init__.py").write_text("")
            (planted / "__main__.py").write_text(
                f"import sys\nopen({str(marker)!r}, 'w').write('ran')\nsys.exit(3)\n"
            )
            (workspace / ".cos").mkdir()

            here = os.getcwd()
            os.chdir(workspace)
            try:
                data = run(board.read(workspace))
            finally:
                os.chdir(here)

            self.assertFalse(marker.exists(), "the workspace's own coscc/loop was executed")
            self.assertEqual(data["units"], [])
            self.assertIn("holds no work units", data["empty_because"])


class AnUnreadableBoardRaisesRatherThanReturningEmpty(unittest.TestCase):
    def test_a_loop_that_cannot_start_is_not_reported_as_an_empty_board(self):
        # "No units" and "I could not look" are different answers, and a page that shows
        # the first when it means the second is the failure this test names.
        with mock.patch.object(loop_run, "argv", return_value=["/nonexistent/python", "-m", "x"]):
            with self.assertRaises(Unavailable) as caught:
                run(board.read(REPO))
        self.assertIn("could not run coscc.loop", str(caught.exception))

    def test_a_loop_that_does_not_answer_is_unavailable_not_an_empty_board(self):
        hang = [sys.executable, "-c", "import time; time.sleep(60)"]
        with mock.patch.object(loop_run, "argv", return_value=hang):
            with self.assertRaises(Unavailable) as caught:
                run(board.read(REPO, timeout=0.5))
        self.assertIn("timed out", str(caught.exception))

    def test_the_child_environment_carries_no_secrets(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("COS_REVIEW_ROUNDS", None)
            env = harness.child_env()
        self.assertEqual(set(env), {"PATH", "HOME", "LC_ALL", "NO_COLOR"})

    def test_the_review_round_limit_is_the_one_setting_passed_down(self):
        """`COS_REVIEW_ROUNDS` reaches the loop, and nothing else new does."""
        extra = {"COS_REVIEW_ROUNDS": "5", "GH_TOKEN": "secret", "COS_MODEL": "m"}
        with mock.patch.dict(os.environ, extra):
            env = harness.child_env()
        self.assertEqual(env["COS_REVIEW_ROUNDS"], "5")
        self.assertEqual(set(env), {"PATH", "HOME", "LC_ALL", "NO_COLOR", "COS_REVIEW_ROUNDS"})


if __name__ == "__main__":
    unittest.main()


class TheGateIsAskedByTheApp(unittest.TestCase):
    """`.claude/CLAUDE.md` invariant 2, which this app walked past until 2026-09-23.

    The fixture is this repository's own `.cos/`, for the reason in the module docstring:
    what breaks here is agreement with `coscc.loop`, and a hand-built unit
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
        """A unit in a store-shaped root: artifacts only, no git."""
        name = "0001_needs-a-review"
        d = Path(tmp) / ".cos" / name
        d.mkdir(parents=True)
        (d / "intent.md").write_text("# I\nAuthor: t. Type: feat. Status: accepted.\n")
        for f in ("spec.md", "plan.md", "impl.md"):
            (d / f).write_text("Status: accepted.\n")
        (d / "pr.md").write_text(
            "# PR: feat(0001): x\nPR: https://github.com/o/r/pull/3. Status: accepted.\n"
        )
        return name

    def test_without_a_repo_the_review_gate_says_so_instead_of_reading_the_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            name = self._store_unit(tmp)
            allowed, said = run(board.gate(tmp, name, "review"))
            self.assertFalse(allowed)
            self.assertIn("no repository given", said)

    def test_the_repo_reaches_the_gate_as_repo(self):
        """The workspace is where `review` asks gh about the pull request."""
        seen = {}

        async def fake_run(argv, timeout, stdin=None):
            seen["argv"], seen["timeout"] = argv, timeout
            return 0, '{"ok": true, "lines": ["open"], "reasons": []}', ""

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(board, "_run", fake_run):
            name = self._store_unit(tmp)
            run(board.gate(tmp, name, "review", repo=tmp))
        argv = seen["argv"]
        self.assertEqual(argv[argv.index("--repo") + 1], str(Path(tmp).resolve()))
        self.assertEqual(seen["timeout"], board.GATE_TIMEOUT)

    def test_a_loop_that_cannot_start_is_unavailable_not_a_closed_gate(self):
        """A gate that cannot be asked must not read as a gate that said no.

        The two are opposite instructions to a caller: one is "fix the install", the other
        is "finish the earlier stage".
        """
        with mock.patch.object(loop_run, "argv", return_value=["/nonexistent/python", "-m", "x"]):
            with self.assertRaises(Unavailable):
                run(board.gate(REPO, "0001_no-session-management", "spec"))


class TheNextStageIsAskedNotWorkedOut(unittest.TestCase):
    """The run button's stage is the loop's `next` answer, copied through."""

    def _unit(self, tmp: str, files: dict[str, str]) -> str:
        name = "0001_what-comes-next"
        d = Path(tmp) / ".cos" / name
        d.mkdir(parents=True)
        for f, text in files.items():
            (d / f).write_text(text)
        return name

    def _script_says(self, tmp: str, name: str, *extra: str) -> dict:
        import json

        out = loop(
            "--root", tmp, "--state", "-", "next", name, *extra, input=json.dumps(snapshot_of(tmp))
        ).stdout
        return json.loads(out)

    def test_the_stage_is_the_one_the_script_printed(self):
        chains = [
            {"intent.md": "# I\nAuthor: t. Type: fix. Status: accepted.\n"},
            {"intent.md": "# I\nAuthor: t. Type: fix. Status: draft.\n"},
            {
                "intent.md": "# I\nAuthor: t. Type: fix. Status: accepted.\n",
                "spec.md": "Status: skipped.\n",
                "plan.md": "Status: accepted.\n",
            },
            {
                "intent.md": "# I\nAuthor: t. Type: fix. Status: accepted.\n",
                "spec.md": "Status: accepted.\n",
                "plan.md": "Status: accepted.\n",
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
                self.assertEqual(
                    u["next_stage"], script["stage"] if files.get("review.md") is None else ""
                )

    def test_the_repo_reaches_next_as_repo_with_the_gate_timeout(self):
        seen = {}

        async def fake_run(argv, timeout, stdin=None):
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

        async def spy(argv, timeout, stdin=None):
            calls.append(argv)
            return await original(argv, timeout, stdin)

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(board, "_run", spy):
            self._unit(tmp, {"intent.md": "# I\nAuthor: t. Type: fix. Status: accepted.\n"})
            run(board.read(tmp))
        self.assertEqual(
            [a[a.index("--root") + 2 :] for a in calls], [["--state", "-", "status", "--json"]]
        )

    def test_a_unit_that_is_not_there_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(Unavailable):
                run(board.next_step(tmp, "0009_not-here"))


# A review round that confirmed two findings need a person, one of them answered.
_ROUND = "\n## Round {n}\n\nReviewed: aaaaaaa. Verdict: {v}.\n\n### Findings\n\n{f}\n"
AWAITING_PERSON = {
    "intent.md": "# I\nAuthor: t. Type: fix. Status: accepted.\n",
    "spec.md": "Status: accepted.\n",
    "plan.md": "Status: accepted.\n",
    "impl.md": "# Impl\nStatus: accepted.\n\n## Needs a person\n\n- F2: no budget for --paid\n- F3: no gh\n",
    "pr.md": "PR: https://github.com/o/r/pull/3. Status: accepted.\n",
    "review.md": "# R\nStatus: changes-requested.\n"
    + _ROUND.format(n=1, v="changes-requested", f="- F2 [open] b\n- F3 [open] c")
    + _ROUND.format(n=2, v="needs-person", f="- F2 [needs-person] b\n- F3 [needs-person] c")
    + "\n## Answers\n\n### F2\nAnswered by: P. Date: 2026-09-24. Via: product.\n\nran it\n",
}


class WaitingForAPersonIsCarriedFromTheScript(unittest.TestCase):
    """`waiting` and `personFindings` are the loop's, copied and nothing more."""

    _unit = TheNextStageIsAskedNotWorkedOut._unit
    _script_says = TheNextStageIsAskedNotWorkedOut._script_says

    def test_next_step_copies_waiting(self):
        with tempfile.TemporaryDirectory() as tmp:
            name = self._unit(tmp, AWAITING_PERSON)
            script = self._script_says(tmp, name)
            got = run(board.next_step(tmp, name))
        self.assertEqual(script["waiting"], ["F3"])
        self.assertEqual(got["waiting"], ["F3"])
        self.assertEqual(got["stage"], "")

    def test_no_waiting_in_the_script_reads_as_none(self):
        async def fake_run(argv, timeout, stdin=None):
            return 0, '{"unit": "u", "stage": "impl", "action": "a", "blocked": true}', ""

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(board, "_run", fake_run):
            got = run(board.next_step(tmp, "0001_x"))
        self.assertEqual(got["waiting"], [])

    def test_read_copies_person_findings_and_waiting(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._unit(tmp, AWAITING_PERSON)
            [u] = run(board.read(tmp))["units"]
        self.assertEqual(
            u["person_findings"],
            [
                {"id": "F2", "reason": "no budget for --paid", "answered": True},
                {"id": "F3", "reason": "no gh", "answered": False},
            ],
        )
        self.assertEqual(u["waiting"], ["F3"])

    def test_a_unit_without_them_reads_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._unit(tmp, {"intent.md": "# I\nAuthor: t. Type: fix. Status: accepted.\n"})
            [u] = run(board.read(tmp))["units"]
        self.assertEqual((u["person_findings"], u["waiting"]), ([], []))


UNFINISHED_ROUND = {
    **{k: v for k, v in AWAITING_PERSON.items() if k != "review.md"},
    "impl.md": "# Impl\nStatus: accepted.\n",
    "review.md": "# R\nStatus: changes-requested.\n"
    + _ROUND.format(n=1, v="changes-requested", f="- F1 [open] a\n- F2 [open] b\n- F3 [open] c")
    + _ROUND.format(n=2, v="changes-requested", f="- F2 [open] b"),
}


class TheIdsARoundLeftOutAreCarriedFromTheScript(unittest.TestCase):
    """`next` hands the ids over as `dropped`, a list, and the board copies it; its sentence names
    none of them."""

    _unit = TheNextStageIsAskedNotWorkedOut._unit
    _script_says = TheNextStageIsAskedNotWorkedOut._script_says

    def test_next_step_copies_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            name = self._unit(tmp, UNFINISHED_ROUND)
            script = self._script_says(tmp, name)
            got = run(board.next_step(tmp, name))
        self.assertEqual(script["dropped"], ["F1", "F3"])
        self.assertEqual(got["dropped"], ["F1", "F3"])
        self.assertNotRegex(got["action"], r"F\d")

    def test_no_dropped_in_the_script_reads_as_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            name = self._unit(tmp, AWAITING_PERSON)
            got = run(board.next_step(tmp, name))
        self.assertEqual(got["dropped"], [])


OUTCOME_INTENT = (
    "# I\nAuthor: t. Type: feat. Status: accepted.\n\n"
    "## Proposed outcome\n\nBy 2026-10-07, three of three.\n\n"
    "## Answers\n\n### Outcome\nAnswered by: Linh. Date: 2026-10-08. Via: product.\n\n"
    "Result: trượt\nMeasured by: agent\nSource: board, 2026-10-08\n\n"
    "### Outcome\nno header\n"
)


class TheOutcomeIsCopiedFromTheScript(unittest.TestCase):
    """The board forwards the loop `unitOutcome`; it reads no block itself."""

    def _read(self, files: dict[str, str]):
        with tempfile.TemporaryDirectory() as d:
            unit = Path(d) / ".cos" / "0001_q"
            unit.mkdir(parents=True)
            for name, text in files.items():
                (unit / name).write_text(text, encoding="utf-8")
            [u] = run(board.read(d))["units"]
        return u

    def test_the_outcome_arrives_as_the_script_read_it(self):
        u = self._read({"intent.md": OUTCOME_INTENT})
        self.assertEqual(
            u["outcome"],
            {
                "deadline": "2026-10-07",
                "result": "missed",
                "by": "Linh",
                "date": "2026-10-08",
                "measured_by": "agent",
                "source": "board, 2026-10-08",
                "reason": None,
                "note": None,
                "invalid": 1,
            },
        )

    def test_a_unit_with_no_intent_has_none(self):
        self.assertIsNone(self._read({"idea.md": "# Idea\nStatus: accepted.\n"})["outcome"])

    def test_an_older_script_that_sends_no_outcome_reads_as_none(self):
        async def fake_run(argv, timeout, stdin=None):
            return 0, '{"stages": [], "units": [{"name": "0001_q", "artifacts": {}}]}', ""

        with tempfile.TemporaryDirectory() as d, mock.patch.object(board, "_run", fake_run):
            [u] = run(board.read(d))["units"]
        self.assertIsNone(u["outcome"])


PAUSED_INTENT = (
    "# I\nAuthor: t. Type: fix. Status: accepted.\n\n## Answers\n\n"
    "### Paused\nDecided by: Leif. Date: 2026-09-24. Via: product.\n\nchờ 0034\n"
)


class TheHoldIsCarriedFromTheScript(unittest.TestCase):
    """`hold` and `hold_moves` are the loop's, copied and nothing more."""

    _unit = TheNextStageIsAskedNotWorkedOut._unit

    def test_read_copies_hold_and_moves(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._unit(tmp, {"intent.md": PAUSED_INTENT})
            [u] = run(board.read(tmp))["units"]
        self.assertEqual(
            u["hold"], {"state": "paused", "reason": "chờ 0034", "by": "Leif", "date": "2026-09-24"}
        )
        self.assertEqual(u["hold_moves"], ["dropped", "active"])
        self.assertTrue(u["next"].startswith("paused — chờ 0034"))

    def test_next_step_copies_hold(self):
        with tempfile.TemporaryDirectory() as tmp:
            name = self._unit(tmp, {"intent.md": PAUSED_INTENT})
            got = run(board.next_step(tmp, name))
        self.assertEqual((got["stage"], got["hold"]["state"]), ("", "paused"))

    def test_an_older_script_reads_as_unheld(self):
        async def fake_run(argv, timeout, stdin=None):
            return 0, '{"root": "r", "stages": [], "units": [{"name": "0001_x"}]}', ""

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(board, "_run", fake_run):
            [u] = run(board.read(tmp))["units"]
        self.assertEqual((u["hold"], u["hold_moves"]), (None, []))


_STUCK_REVIEW = "# Review: q\nAuthor: t. Status: changes-requested.\n" + "".join(
    f"\n## Round {n}\n\nReviewed: aaaaaaa. Verdict: changes-requested.\n\n### Findings\n\n- F1 [open] a\n"
    for n in (1, 2, 3)
)
_MORE_ROUNDS = "\n## Answers\n\n### More rounds\nDecided by: owner. Date: 2026-09-27. Via: product.\nRounds: 1\n"


class MoreRoundsAreCarriedFromTheScript(unittest.TestCase):
    """`more_rounds` and `rounds_granted` are the loop's, copied and nothing more."""

    def _stuck(self, tmp: str, name: str, review: str) -> None:
        d = Path(tmp) / ".cos" / name
        d.mkdir(parents=True)
        (d / "intent.md").write_text("# I\nAuthor: t. Type: feat. Status: accepted.\n")
        for f in ("spec.md", "plan.md", "impl.md"):
            (d / f).write_text("Status: accepted.\n")
        (d / "pr.md").write_text("PR: https://github.com/o/r/pull/3. Status: accepted.\n")
        (d / "review.md").write_text(review)

    def test_more_rounds_and_rounds_granted_are_copied_and_absent_reads_as_none(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ):
            os.environ.pop("COS_REVIEW_ROUNDS", None)
            self._stuck(tmp, "0001_given", _STUCK_REVIEW + _MORE_ROUNDS)
            self._stuck(tmp, "0002_stuck", _STUCK_REVIEW)
            got = {u["name"]: u for u in run(board.read(tmp))["units"]}
        self.assertEqual(
            (got["0001_given"]["more_rounds"], got["0001_given"]["rounds_granted"]), (False, 1)
        )
        self.assertEqual(
            (got["0002_stuck"]["more_rounds"], got["0002_stuck"]["rounds_granted"]), (True, 0)
        )

    def test_an_older_script_reads_as_false_and_zero(self):
        async def fake_run(argv, timeout, stdin=None):
            return 0, '{"root": "r", "stages": [], "units": [{"name": "0001_x"}]}', ""

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(board, "_run", fake_run):
            [u] = run(board.read(tmp))["units"]
        self.assertEqual((u["more_rounds"], u["rounds_granted"]), (False, 0))


class TheStagesAnAnsweredDraftRunsAgainAreCarriedFromTheScript(unittest.TestCase):
    """`afterAnswers` is the loop's, copied onto the read and onto each unit."""

    def test_every_unit_carries_the_stages_an_answered_draft_runs_again(self):
        data = run(board.read(REPO))
        self.assertIn("impl", data["after_answers"])
        self.assertTrue(data["units"])
        for u in data["units"]:
            self.assertEqual(u["after_answers"], data["after_answers"], u["name"])

    def test_an_older_script_sends_none_and_reads_as_none(self):
        async def fake_run(argv, timeout, stdin=None):
            return 0, '{"root": "r", "stages": [], "units": [{"name": "0001_x"}]}', ""

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(board, "_run", fake_run):
            data = run(board.read(tmp))
        self.assertEqual((data["after_answers"], data["units"][0]["after_answers"]), ([], []))


PR_MD = (
    "# PR: fix(0001): a title\n"
    "Intent: intent.md. Impl: impl.md. PR: https://github.com/o/r/pull/7. Author: a. Status: accepted.\n"
    "\n## Where\n\nchecks pending.\n"
)


class ThePrTextIsCopiedFromTheScript(unittest.TestCase):
    """The title and body come from the loop `prText`; this module cuts nothing."""

    def test_a_unit_with_pr_md_gets_its_title_body_and_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            unit = Path(tmp) / ".cos" / "0001_a"
            unit.mkdir(parents=True)
            (unit / "pr.md").write_text(PR_MD, encoding="utf-8")
            (unit / "intent.md").write_text("# I\nType: fix. Status: accepted.\n", encoding="utf-8")
            got = run(board.pr_text(tmp, "0001_a"))
        self.assertEqual(
            got,
            {
                "unit": "0001_a",
                "title": "fix(0001): a title",
                "body": "## Where\n\nchecks pending.\n",
                "url": "https://github.com/o/r/pull/7",
                "scope": None,
                "status": "accepted",
                "titleProblem": None,
            },
        )

    def test_a_unit_without_pr_md_is_an_answer_with_code_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".cos" / "0001_a").mkdir(parents=True)
            got = run(board.pr_text(tmp, "0001_a"))
        self.assertEqual(got["code"], 1)
        self.assertIn("has no pr.md", got["error"])

    def test_a_loop_that_cannot_start_is_unavailable(self):
        async def cannot_start(argv, timeout, stdin=None):
            raise FileNotFoundError("python")

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(board, "_run", cannot_start):
            with self.assertRaises(Unavailable):
                run(board.pr_text(tmp, "0001_a"))


class TheScreensAnswerIsCopiedFromTheScript(unittest.TestCase):
    """`board.screens` runs the app's the loop's `screens` against a real git repository and store, and
    hands back what it printed."""

    def _repo(self, tmp: str) -> tuple[Path, Path, str]:
        store, repo = Path(tmp) / "store", Path(tmp) / "repo"
        (store / ".cos" / "0001_x").mkdir(parents=True)
        (store / ".cos" / "0001_x" / "intent.md").write_text("# I\nType: fix. Status: accepted.\n")
        (repo / ".claude" / "rules").mkdir(parents=True)
        (repo / "coscc").mkdir()

        def git(*args: str) -> str:
            return subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=T",
                    "-c",
                    "user.email=t@example.invalid",
                    "-c",
                    "commit.gpgsign=false",
                    *args,
                ],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

        git("init", "-q", "-b", "main")
        (repo / ".claude" / "rules" / "ui-standard.md").write_text(
            '---\npaths:\n  - "coscc/screens/__init__.py"\n---\n'
        )
        (repo / ".gitignore").write_text(".screens/\n")
        git("add", ".")
        git("commit", "-q", "-m", "first")
        git("switch", "-q", "-c", "fix/x")
        (repo / "coscc" / "screens").mkdir()
        (repo / "coscc" / "screens" / "__init__.py").write_text("# a screen\n")
        git("add", ".")
        git("commit", "-q", "-m", "a screen")
        taken = git("rev-parse", "HEAD")
        git("commit", "-q", "--amend", "-m", "a screen, rewritten")
        (repo / ".screens").mkdir()
        return store, repo, taken

    def test_a_rewritten_head_is_a_retake_with_the_manifest_carried(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, repo, taken = self._repo(tmp)
            hits = [{"address": "/board", "size": "390x844", "kind": "path", "snippet": "/tmp/x"}]
            (repo / ".screens" / "manifest.json").write_text(
                json.dumps({"head": taken, "dirty": False, "addresses": ["/board"], "hits": hits})
            )
            got = run(board.screens(store, "0001_x", repo))
        self.assertEqual(
            got,
            {
                "unit": "0001_x",
                "ui": ["coscc/screens/__init__.py"],
                "manifest": {"head": taken, "dirty": False, "addresses": ["/board"], "hits": hits},
                "rewritten": True,
                "retake": True,
                "why": "",
            },
        )

    def test_no_manifest_is_no_retake_and_says_why(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, repo, _ = self._repo(tmp)
            got = run(board.screens(store, "0001_x", repo))
        self.assertFalse(got["retake"])
        self.assertIsNone(got["manifest"])
        self.assertIn("manifest.json", got["why"])

    def test_the_repo_is_passed_as_repo_with_the_gate_timeout(self):
        seen = {}

        async def fake_run(argv, timeout, stdin=None):
            seen["argv"], seen["timeout"] = argv, timeout
            return 0, '{"retake": false}', ""

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(board, "_run", fake_run):
            run(board.screens(tmp, "0001_x", tmp))
        argv = seen["argv"]
        self.assertEqual(argv[argv.index("screens") + 1], "0001_x")
        self.assertEqual(argv[argv.index("--repo") + 1], str(Path(tmp).resolve()))
        self.assertEqual(seen["timeout"], board.GATE_TIMEOUT)

    def test_misuse_and_a_loop_that_cannot_start_are_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(Unavailable):
                run(board.screens(tmp, "0009_not-here", tmp))

        async def cannot_start(argv, timeout, stdin=None):
            raise FileNotFoundError("python")

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(board, "_run", cannot_start):
            with self.assertRaises(Unavailable):
                run(board.screens(tmp, "0001_x", tmp))


def _store(d: Path, units: dict[str, dict[str, str]], ideas: dict[str, str] | None = None) -> Path:
    for name, files in units.items():
        unit = d / ".cos" / name
        unit.mkdir(parents=True)
        for f, text in files.items():
            (unit / f).write_text(text, encoding="utf-8")
    for f, text in (ideas or {}).items():
        (d / ".cos" / "ideas").mkdir(parents=True, exist_ok=True)
        (d / ".cos" / "ideas" / f).write_text(text, encoding="utf-8")
    return d


_TO_IMPL = {"spec.md": "# S\nStatus: accepted.\n", "plan.md": "# P\nStatus: accepted.\n"}


class TheSnapshotReachesTheScriptOnStdin(unittest.TestCase):
    """Given a snapshot, `--state -` right after `--root`, and the JSON on stdin."""

    def test_read_next_and_gate_hand_the_snapshot_on_stdin(self):
        seen: list[tuple[list[str], str]] = []

        async def fake_run(argv, timeout, stdin=None):
            seen.append((argv, stdin))
            if "gate" in argv:
                return 0, '{"ok": true, "lines": ["open"], "reasons": []}', ""
            return 0, '{"stages": [], "units": []}' if "status" in argv else '{"stage": ""}', ""

        snapshot = {"workspace": "proj", "workspaces": ["proj"], "units": {}, "ideas": {}}
        with tempfile.TemporaryDirectory() as d, mock.patch.object(board, "_run", fake_run):
            run(board.read(d, state=snapshot))
            run(board.next_step(d, "0001_x", state=snapshot))
            run(board.gate(d, "0001_x", "impl", state=snapshot))
            run(board.rerun(d, "0001_x", state=snapshot))
            run(board.pr_text(d, "0001_x", state=snapshot))
        self.assertEqual(len(seen), 5)
        for argv, stdin in seen:
            self.assertEqual(
                argv[argv.index("--root") + 2 : argv.index("--root") + 4], ["--state", "-"]
            )
            self.assertNotIn("--peer", argv)
            self.assertEqual(json.loads(stdin), snapshot)


class LinksReachTheScriptInTheSnapshot(unittest.TestCase):
    """Another workspace's units reach the loop in the snapshot, under their workspace's name."""

    def test_a_dependency_is_read_across_workspaces_and_copied(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _store(Path(a), {"0001_x": {"intent.md": "# I\nType: feat. Status: accepted.\n"}})
            _store(
                Path(b),
                {
                    "0001_y": {
                        "intent.md": "# I\nType: feat. Status: accepted.\nIdea: ideas/0001_f.md. Repo: b. Depends on: a/0001_x.\n",
                        **_TO_IMPL,
                    }
                },
                {
                    "0001_f.md": "# Idea: f\nStatus: accepted.\n\n## Units\n\n- a/0001_x.\n- b/0001_y. Depends on: a/0001_x.\n"
                },
            )
            state = snapshot_of(b, [("a", a), ("b", b)])
            data = run(board.read(b, state=state))
            nxt = run(board.next_step(b, "0001_y", state=state))
        [u] = data["units"]
        self.assertEqual((u["idea"], u["repo"], u["why"]), ("ideas/0001_f.md", "b", "dependency"))
        self.assertEqual(
            u["depends_on"],
            [
                {
                    "ref": "a/0001_x",
                    "merged": False,
                    "why": "not merged: the app holds no merge of it",
                }
            ],
        )
        self.assertEqual(
            data["ideas"][0]["units"][1], {"ref": "b/0001_y", "depends_on": ["a/0001_x"]}
        )
        self.assertEqual(
            (nxt["stage"], nxt["why"], nxt["action"]),
            ("", "dependency", "waiting on a/0001_x to merge"),
        )

    def test_a_unit_without_links_reads_as_before(self):
        with tempfile.TemporaryDirectory() as d:
            _store(Path(d), {"0001_x": {"intent.md": "# I\nType: feat. Status: accepted.\n"}})
            [u] = run(board.read(d))["units"]
            nxt = run(board.next_step(d, "0001_x"))
        self.assertEqual((u["idea"], u["repo"], u["depends_on"], nxt["why"]), ("", "", [], ""))


class TheGateHandsOnACleanRebase(unittest.TestCase):
    """`gate --json`'s `rebased` reaches the app, and nothing else does."""

    def test_only_two_named_commits_are_taken(self):
        both = {"reviewed": "a" * 40, "head": "b" * 40}
        self.assertEqual(_board._rebased({"rebased": both}), both)
        for bad in (
            {},
            {"rebased": None},
            {"rebased": {"reviewed": "a"}},
            {"rebased": {"reviewed": "", "head": "b"}},
            {"rebased": "yes"},
        ):
            self.assertIsNone(_board._rebased(bad), bad)
        self.assertEqual(_board.Gate(True, "open", (), both).rebased, both)
        self.assertIsNone(_board.Gate(True, "open").rebased)
