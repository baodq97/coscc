"""Tests for the board, run against the only real set of work units there is.

This repository's own `.cos/` is the fixture. That is deliberate: the thing most likely to break
here is not the parsing but the *agreement* between this module and `coscc.loop`, and a
hand-built fixture would keep passing after the two drift apart."""

from __future__ import annotations

import asyncio
import json
import os
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
    STATE_LABEL,
    Unavailable,
    attention_reason,
    shown_state,
    unit_state,
)

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
