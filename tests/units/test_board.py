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
from coscc.units.board import (
    COLLAPSED_STATES,
    STATE_COLOR,
    STATE_LABEL,
    Unavailable,
    attention_reason,
    shown_state,
    unit_state,
)

REPO = Path(__file__).resolve().parents[2]

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

UNFINISHED_ROUND = {
    **{k: v for k, v in AWAITING_PERSON.items() if k != "review.md"},
    "impl.md": "# Impl\nStatus: accepted.\n",
    "review.md": "# R\nStatus: changes-requested.\n"
    + _ROUND.format(n=1, v="changes-requested", f="- F1 [open] a\n- F2 [open] b\n- F3 [open] c")
    + _ROUND.format(n=2, v="changes-requested", f="- F2 [open] b"),
}

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

    def test_the_next_action_is_carried_through_not_recomputed(self):
        # Two answers to "what next" is exactly the drift `board.py` exists to avoid, so
        # the value must be the script's, verbatim.
        data = run(board.read(REPO))
        unit = next(u for u in data["units"] if _by_stage(u)["plan"] == "done")
        self.assertEqual(unit["next"], "finished")
        self.assertFalse(unit["blocked"])


class TheStageListNeedsNoWorkspace(unittest.TestCase):
    """Settings asks for the stages with no workspace, and gets the script's list, in the script's
    order."""

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


class AnEmptyWorkspaceIsAnAnswerNotAFailure(unittest.TestCase):
    def test_a_directory_that_is_not_there_names_itself(self):
        data = run(board.read("/nonexistent-workspace-for-a-test"))
        self.assertEqual(data["units"], [])
        self.assertIn("no such directory", data["empty_because"])


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
        # A snapshot handed in, or building one would ask the hanging loop first, for 30 s.
        with mock.patch.object(loop_run, "argv", return_value=hang):
            with self.assertRaises(Unavailable) as caught:
                run(board.read(REPO, timeout=0.5, state=_board.EMPTY_STATE))
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

    def test_a_loop_that_cannot_start_is_unavailable_not_a_closed_gate(self):
        """A gate that cannot be asked must not read as a gate that said no.

        The two are opposite instructions to a caller: one is "fix the install", the other
        is "finish the earlier stage".
        """
        with mock.patch.object(loop_run, "argv", return_value=["/nonexistent/python", "-m", "x"]):
            with self.assertRaises(Unavailable):
                run(board.gate(REPO, "0001_no-session-management", "spec"))

    def test_an_open_gate_comes_back_open_and_says_so(self):
        # A unit closed under the old loop: every stage behind it is settled, so any
        # stage's gate is open. Chosen by shape, not by number.
        data = run(board.read(REPO))
        unit = next(u for u in data["units"] if _by_stage(u)["plan"] == "done")
        allowed, said = run(board.gate(REPO, unit["name"], "impl"))
        self.assertTrue(allowed, said)
        self.assertIn("open", said.lower())


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

        out = loop(
            "--root", tmp, "--state", "-", "next", name, *extra, input=json.dumps(snapshot_of(tmp))
        ).stdout
        return json.loads(out)

    def test_a_unit_that_is_not_there_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(Unavailable):
                run(board.next_step(tmp, "0009_not-here"))

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


class TheStateOfAUnit(unittest.TestCase):
    """One state per unit, the first rule that matches deciding it."""

    @staticmethod
    def _unit(**kw) -> dict:
        base = {
            "name": "0001_x",
            "why": "missing",
            "at": "plan",
            "open": 0,
            "problems": [],
            "hold": None,
            "between_pr_and_ship": False,
            "integration": None,
            "stages": [{"stage": "intent", "status": "accepted"}],
        }
        return {**base, **kw}

    def _is(self, unit: dict, last_end=None, ci=None) -> str:
        return unit_state(unit, last_end, ci)["state"]

    def test_a_card_says_running_only_for_a_running_or_ending_attempt(self):
        ready = unit_state(self._unit(), None, None)

        def shown(*states: str) -> str:
            return shown_state(ready, [{"stage": "plan", "state": s} for s in states])["state"]

        self.assertEqual(shown("queued"), "starting")
        self.assertEqual(shown("preparing"), "starting")
        self.assertEqual(shown("queued", "preparing"), "starting")
        for state in ("running", "ending"):
            self.assertEqual(shown(state), "running", state)
            self.assertEqual(shown("preparing", state), "running", state)
        self.assertEqual(shown_state(ready, [])["state"], "ready")
        self.assertEqual(shown_state(ready, None)["state"], "ready")

    def test_starting_never_covers_a_collapsed_state(self):
        rows = [{"stage": "plan", "state": "preparing"}]
        for hold, why in (
            ({"state": "paused"}, "paused"),
            ({"state": "dropped"}, "dropped"),
            (None, "finished"),
        ):
            decided = unit_state(self._unit(why=why, hold=hold), None, None)
            self.assertIn(decided["state"], COLLAPSED_STATES)
            self.assertEqual(shown_state(decided, rows), decided)

    def test_no_state_reads_as_approval(self):
        for label in STATE_LABEL.values():
            self.assertNotIn("Approved", label)
            self.assertNotIn("Accepted", label)

    def test_a_rejected_unit_is_dropped_and_names_the_stage(self):
        unit = self._unit(
            why="rejected",
            stages=[
                {"stage": "intent", "status": "accepted"},
                {"stage": "spec", "status": "rejected"},
            ],
        )
        got = unit_state(unit, None, None)
        self.assertEqual((got["state"], got["label"]), ("dropped", "Dropped — spec rejected"))

    def test_each_pair_of_neighbouring_rules_goes_to_the_earlier_one(self):
        # 1/2: finished and dropped at once.
        self.assertEqual(self._is(self._unit(why="finished", hold={"state": "dropped"})), "done")
        # 2/3: rejected with a pause still on file.
        self.assertEqual(self._is(self._unit(why="rejected", hold={"state": "paused"})), "dropped")
        # 3/4: a paused unit with a session listed is still paused.
        paused = unit_state(self._unit(why="paused", hold={"state": "paused"}), None, None)
        self.assertEqual(
            shown_state(paused, [{"stage": "plan", "state": "running"}])["state"], "paused"
        )
        # 4/5: a running unit with an open question is running.
        asking = unit_state(self._unit(open=1), None, None)
        self.assertEqual(
            shown_state(asking, [{"stage": "plan", "state": "running"}])["state"], "running"
        )
        self.assertEqual(shown_state(asking, [])["state"], "needs-you")
        # 5/6: an open question beats an error.
        self.assertEqual(self._is(self._unit(open=1, problems=["x"])), "needs-you")
        self.assertEqual(self._is(self._unit(why="awaits-person", problems=["x"])), "needs-you")
        # 6/7: an error in the pr→ship window.
        self.assertEqual(
            self._is(self._unit(at="review", between_pr_and_ship=True, problems=["x"])), "error"
        )
        # 7/8: in the window and missing is awaiting, not ready.
        self.assertEqual(self._is(self._unit(at="review", between_pr_and_ship=True)), "awaiting")

    def test_each_cause_of_error(self):
        # (a)
        self.assertEqual(self._is(self._unit(problems=["x"])), "error")
        self.assertEqual(self._is(self._unit(why="unreadable")), "error")
        # (b)
        self.assertEqual(self._is(self._unit(), {"stage": "plan", "outcome": "failed"}), "error")
        self.assertEqual(self._is(self._unit(), {"stage": "plan", "outcome": "exhausted"}), "error")
        # (c)
        window = self._unit(at="review", between_pr_and_ship=True)
        self.assertEqual(
            self._is({**window, "integration": {"state": "red-after-integration"}}), "error"
        )
        # (d)
        ci = {
            "head": "a",
            "checks": [{"name": "tests", "bucket": "fail"}],
            "at": "2026-09-26T00:00:00+00:00",
        }
        self.assertEqual(self._is(window, None, ci), "error")
        cancelled = {**ci, "checks": [{"name": "lint", "bucket": "cancel"}]}
        self.assertEqual(self._is(window, None, cancelled), "error")

    def test_a_failure_at_another_stage_or_a_stop_is_not_an_error(self):
        self.assertEqual(
            self._is(self._unit(at="plan"), {"stage": "spec", "outcome": "failed"}), "ready"
        )
        self.assertEqual(
            self._is(self._unit(at="plan"), {"stage": "plan", "outcome": "stopped"}), "ready"
        )

    def test_a_stale_review_or_ship_in_the_window_is_awaiting(self):
        """A rerun of `pr` leaves `review.md` stale; `next` sends it down the missing review's wait
        on CI, so the card and the dialog say so too."""
        for at in ("review", "ship"):
            got = unit_state(self._unit(at=at, why="stale", between_pr_and_ship=True), None, None)
            self.assertEqual(got["state"], "awaiting", at)
        # A stale `pr.md` is `pr` to run again, not a wait.
        self.assertEqual(
            self._is(self._unit(at="pr", why="stale", between_pr_and_ship=True)), "ready"
        )

    def test_changes_requested_in_the_window_is_ready(self):
        """It waits on an `impl`, not on CI."""
        self.assertEqual(
            self._is(self._unit(at="review", why="changes-requested", between_pr_and_ship=True)),
            "ready",
        )

    def test_a_broken_or_dropped_unit_keeps_the_reason_its_artifacts_give(self):
        """A unit with `problems` is `Error`, yet `attention_reason` still reads "Needs a person"; a
        dropped unit with a draft still reads "<stage>.md is a draft"."""
        broken = self._unit(problems=["plan.md: no Status line"])
        self.assertEqual(attention_reason(broken), "Needs a person")
        self.assertEqual(unit_state(broken, None, None)["state"], "error")
        dropped = self._unit(
            why="dropped",
            hold={"state": "dropped"},
            stages=[
                {"stage": "intent", "status": "accepted"},
                {"stage": "spec", "status": "draft"},
            ],
        )
        self.assertEqual(attention_reason(dropped), "spec.md is a draft")

    def test_attention_reads_the_code_and_not_the_words(self):
        # A `next` whose words say finished, closed or waiting decides nothing.
        rows = [{"stage": "spec", "status": "draft"}]
        for nxt in ("finished", "closed — spec rejected", "waiting on api/0001_b to merge"):
            with self.subTest(nxt=nxt):
                self.assertEqual(
                    attention_reason({"next": nxt, "why": "", "stages": rows}), "spec.md is a draft"
                )
        self.assertEqual(attention_reason({"next": "x", "why": "finished", "stages": rows}), "")
        self.assertEqual(attention_reason({"next": "x", "why": "rejected", "stages": rows}), "")
        self.assertEqual(
            attention_reason({"next": "x", "why": "dependency", "stages": rows}), "Needs a person"
        )

    def test_a_ship_md_a_refused_merge_left_is_not_offered_for_acceptance(self):
        """`next` works that draft; one with no `Round` reads as before."""
        rows = [{"stage": "review", "status": "accepted"}, {"stage": "ship", "status": "draft"}]
        refused = self._unit(
            why="ship-refused",
            at="ship",
            between_pr_and_ship=True,
            stages=rows,
            next="ship after review round 1 did not merge — the next step says what runs now",
        )
        self.assertEqual(attention_reason(refused), "")
        self.assertEqual(unit_state(refused, None, None)["state"], "ready")
        old = self._unit(
            why="draft",
            at="ship",
            between_pr_and_ship=True,
            stages=rows,
            next="finish and accept ship.md",
        )
        self.assertEqual(attention_reason(old), "ship.md is a draft")

    def test_a_ship_still_merging_is_running_with_its_ship_and_an_error_without(self):
        """A merge asked for and not recorded is no refusal: `Running` while its `ship` runs, and
        an error saying so once no `ship` is left to record it."""
        rows = [{"stage": "review", "status": "accepted"}, {"stage": "ship", "status": "draft"}]
        merging = self._unit(
            why="ship-merging",
            at="ship",
            between_pr_and_ship=True,
            stages=rows,
            next="ship is merging #7 — wait",
        )
        decided = unit_state(merging, None, None)
        self.assertEqual(
            attention_reason(merging), "ship requested a merge and recorded no outcome"
        )
        # The ship's attempt runs.
        shown = shown_state(decided, [{"stage": "ship", "state": "running"}])
        self.assertEqual(shown["state"], "running")
        # None is left.
        alone = shown_state(decided, [])
        self.assertEqual(alone["state"], "error")

    def test_starting_is_laid_over_and_has_a_colour_of_its_own(self):
        self.assertEqual(STATE_LABEL["starting"], "Starting")
        others = [c for state, c in STATE_COLOR.items() if state != "starting"]
        self.assertNotIn(STATE_COLOR["starting"], others)
        # `unit_state` never decides it, whatever the unit.
        for kw in ({}, {"open": 1}, {"problems": ["x"]}, {"why": "finished"}):
            self.assertNotIn(self._is(self._unit(**kw)), ("starting", "running"))


class WhatABoardSaysOfAUnitsWait(unittest.TestCase):
    def test_each_kind_of_wait_has_its_reason_and_a_calm_unit_none(self):
        fixtures = {
            "intent.md is a draft": {
                "next": "accept intent.md",
                "stages": [{"stage": "intent", "status": "draft"}],
            },
            "Changes requested": {
                "next": "impl",
                "stages": [
                    {"stage": "intent", "status": "accepted"},
                    {"stage": "review", "status": "changes-requested"},
                ],
            },
            "Needs a person": {
                "next": "waiting",
                "stages": [{"stage": "review", "status": "changes-requested"}],
                "person_findings": [{"id": "F1", "answered": False}],
            },
        }
        self.assertEqual(
            {want: attention_reason(u) for want, u in fixtures.items()},
            {want: want for want in fixtures},
        )
        self.assertEqual(
            attention_reason(
                {"next": "spec", "stages": [{"stage": "intent", "status": "accepted"}]}
            ),
            "",
        )


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
