"""Tests for the git import.

The fixtures are real git repositories built in a temporary directory, not mocks of
`subprocess`. What is under test is an agreement with `git`'s actual output — the block
format of `log --name-status`, the `cat-file --batch` protocol, what `--no-renames` does to
a moved file — and a mock agrees with whatever it was told.

This repository's own history is **not** the fixture here. It is a moving target: the
number of post-settlement edits in it went 39 → 41 → 42 → 43 over the three days this unit
was written, twice because of edits to this unit's own artifacts. That measurement belongs
in `scripts/verify_0013.py`, which recomputes it from git every run; a test asserting a
constant would be red by the next commit and would be fixed by editing the constant.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from coscc import backfill, states
from coscc.backfill import NotAGitCheckout
from coscc.data import Data
from coscc.history import UNKNOWN, History, settled_edits

WS = "fixture"

OTHER = {
    "name": "two-step",
    "absent": "nowhere",
    "settled": ["closed"],
    "stages": [
        {"name": "ticket", "artifact": "ticket.txt", "statuses": ["open", "closed"]},
        {"name": "wrap", "artifact": "wrap.txt", "statuses": ["open", "closed"]},
    ],
}


class Repo:
    """A throwaway git repository with an identity of its own.

    The identity is passed per command rather than written into a config, so the test
    cannot pick up whoever is running it — and cannot fail on a machine where git has no
    `user.name` set at all.
    """

    def __init__(self, path: Path):
        self.path = path
        self.path.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str) -> str:
        return subprocess.run(
            [
                "git", "-C", str(self.path),
                "-c", "user.name=Fixture",
                "-c", "user.email=fixture@example.invalid",
                "-c", "commit.gpgsign=false",
                *args,
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    def write(self, rel: str, text: str) -> None:
        target = self.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def commit(self, message: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)


def artifact(title: str, status: str) -> str:
    return f"# {title}\nAuthor: Fixture. Status: {status}.\n\n## Body\n"


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = Repo(self.root / "repo")
        self.data = Data(self.root / "data")
        self.history = History(self.root / "work", self.data)

    def run_import(self, history: History | None = None) -> dict:
        return backfill.run(history or self.history, self.repo.path, workspace=WS)


class HistoryInGitBecomesTransitions(Fixture):
    def setUp(self):
        super().setUp()
        self.repo.write(".cos/0001_a-problem/intent.md", artifact("A problem", "draft"))
        self.repo.commit("intent, draft")
        self.repo.write(".cos/0001_a-problem/intent.md", artifact("A problem", "accepted"))
        self.repo.commit("intent, accepted")
        # Settled, then rewritten in place. This is the event `0013` exists to count, and
        # the one the old arrangement destroyed: the status does not change.
        self.repo.write(
            ".cos/0001_a-problem/intent.md", artifact("A problem", "accepted") + "more\n"
        )
        self.repo.commit("intent, corrected after acceptance")

    def test_one_transition_per_commit_that_touched_the_artifact(self):
        summary = self.run_import()
        self.assertEqual(summary["scanned"], 3)
        self.assertEqual(summary["added"], 3)
        rows = self.history.transitions(WS)
        self.assertEqual(
            [(r["from_state"], r["to_state"]) for r in rows],
            [("not started", "draft"), ("draft", "accepted"), ("accepted", "accepted")],
        )

    def test_the_edit_after_acceptance_is_the_one_that_counts(self):
        self.run_import()
        counted = settled_edits(self.history.transitions(WS), self.history.machine)
        self.assertEqual(len(counted), 1)
        self.assertEqual(counted[0]["artifact"], "intent.md")

    def test_every_row_says_which_commit_it_was_inferred_from(self):
        self.run_import()
        shas = self.repo.git("log", "--reverse", "--format=%H").split()
        self.assertEqual(
            [r["source"] for r in self.history.transitions(WS)],
            [f"commit:{sha}" for sha in shas],
        )

    def test_the_actor_and_the_session_say_they_are_not_known(self):
        # `spec.md` C1. Git knows the commit author and this throws it away on purpose:
        # a transition's actor is whoever moved it, and filling it from the commit would
        # assert a person did work a session did.
        self.run_import()
        for row in self.history.transitions(WS):
            self.assertEqual(row["actor"], UNKNOWN)
            self.assertEqual(row["session"], UNKNOWN)

    def test_timestamps_are_utc_at_second_resolution(self):
        self.run_import()
        for row in self.history.transitions(WS):
            self.assertTrue(row["at"].endswith("+00:00"), row["at"])

    def test_the_projection_lands_on_the_state_the_file_carries_today(self):
        self.run_import()
        state = self.history.state(WS, "0001_a-problem")
        self.assertEqual(state["intent.md"], "accepted")
        self.assertEqual(state["ship.md"], states.default().absent)


class ImportingTwiceDoesNotDouble(Fixture):
    def test_the_second_run_adds_nothing_and_the_count_is_unchanged(self):
        self.repo.write(".cos/0001_a-problem/intent.md", artifact("A problem", "accepted"))
        self.repo.commit("one")
        first = self.run_import()
        second = self.run_import()
        self.assertEqual((first["added"], second["added"]), (1, 0))
        self.assertEqual(second["scanned"], 1)
        self.assertEqual(len(self.history.transitions(WS)), 1)

    def test_a_commit_made_after_the_first_import_is_picked_up(self):
        self.repo.write(".cos/0001_a-problem/intent.md", artifact("A problem", "draft"))
        self.repo.commit("one")
        self.run_import()
        self.repo.write(".cos/0001_a-problem/intent.md", artifact("A problem", "accepted"))
        self.repo.commit("two")
        self.assertEqual(self.run_import()["added"], 1)
        rows = self.history.transitions(WS)
        self.assertEqual(rows[-1]["from_state"], "draft")


class WhatLeavesAndWhatIsSkipped(Fixture):
    def test_a_deleted_artifact_transitions_into_the_absent_state(self):
        self.repo.write(".cos/0001_a-problem/intent.md", artifact("A problem", "accepted"))
        self.repo.commit("one")
        (self.repo.path / ".cos/0001_a-problem/intent.md").unlink()
        self.repo.commit("retire it")
        self.run_import()
        rows = self.history.transitions(WS)
        self.assertEqual(rows[-1]["to_state"], states.default().absent)
        # And a retired unit reads as retired rather than as frozen at its last status.
        self.assertEqual(
            self.history.state(WS, "0001_a-problem")["intent.md"], states.default().absent
        )

    def test_a_file_that_is_not_an_artifact_of_a_unit_is_skipped(self):
        self.repo.write(".cos/RENAMES.md", "# not an artifact\nStatus: accepted.\n")
        self.repo.write(".cos/0001_a-problem/notes.md", "Status: accepted.\n")
        self.repo.write(".cos/0001_a-problem/intent.md", artifact("A problem", "draft"))
        self.repo.commit("one")
        self.run_import()
        self.assertEqual(
            [r["artifact"] for r in self.history.transitions(WS)], ["intent.md"]
        )

    def test_an_artifact_with_no_readable_status_is_skipped_rather_than_guessed(self):
        self.repo.write(".cos/0001_a-problem/intent.md", "# A problem\nno status here\n")
        self.repo.commit("one")
        self.assertEqual(self.run_import()["scanned"], 0)

    def test_a_rename_ends_one_path_and_begins_another(self):
        # `f326765` in this repository renumbered six units. The cost of `--no-renames` is
        # recorded in the module docstring; this is what it looks like.
        self.repo.write(".cos/0002_a-problem/intent.md", artifact("A problem", "accepted"))
        self.repo.commit("one")
        self.repo.git("mv", ".cos/0002_a-problem", ".cos/0001_a-problem")
        self.repo.commit("renumber")
        self.run_import()
        rows = self.history.transitions(WS)
        self.assertEqual(
            [(r["unit"], r["from_state"], r["to_state"]) for r in rows],
            [
                ("0002_a-problem", "not started", "accepted"),
                # Within one commit the order is git's: `--name-status` lists paths
                # sorted, so the new number arrives before the old one leaves.
                ("0001_a-problem", "not started", "accepted"),
                ("0002_a-problem", "accepted", "not started"),
            ],
        )


class NothingIsWrittenIntoTheRepository(Fixture):
    """`spec.md` R7. This is the one component that touches somebody else's checkout."""

    def test_the_working_tree_is_untouched_and_no_commit_is_added(self):
        self.repo.write(".cos/0001_a-problem/intent.md", artifact("A problem", "accepted"))
        self.repo.commit("one")
        before = self.repo.git("rev-parse", "HEAD").strip()
        self.run_import()
        self.assertEqual(self.repo.git("status", "--porcelain"), "")
        self.assertEqual(self.repo.git("rev-parse", "HEAD").strip(), before)

    def test_a_directory_that_is_not_a_checkout_is_refused_by_name(self):
        plain = self.root / "plain"
        plain.mkdir()
        with self.assertRaises(NotAGitCheckout) as caught:
            backfill.scan(plain)
        self.assertIn(str(plain), str(caught.exception))

    def test_a_path_inside_the_checkout_resolves_to_the_checkout(self):
        # `--git-dir` succeeds from any subdirectory, so the wrong path would otherwise
        # produce an empty history and no complaint at all.
        self.repo.write(".cos/0001_a-problem/intent.md", artifact("A problem", "accepted"))
        self.repo.commit("one")
        inside = self.repo.path / ".cos" / "0001_a-problem"
        self.assertEqual(backfill.require_checkout(inside), self.repo.path.resolve())
        self.assertEqual(len(backfill.scan(inside)), 1)

    def test_a_path_that_is_not_there_at_all_is_refused(self):
        with self.assertRaises(NotAGitCheckout):
            backfill.scan(self.root / "nowhere")


class ADifferentStateSetImportsADifferentRepository(Fixture):
    """`spec.md` R6 reaching the import: no artifact name below is one the default knows."""

    def test_it_reads_the_artifacts_that_set_names_and_no_others(self):
        path = self.root / "other.json"
        path.write_text(json.dumps(OTHER), encoding="utf-8")
        history = History(self.root / "work", self.data, machine=states.load(path))

        self.repo.write(".cos/0001_a-problem/ticket.txt", "Status: open.\n")
        self.repo.write(".cos/0001_a-problem/intent.md", artifact("ignored here", "draft"))
        self.repo.commit("one")
        self.repo.write(".cos/0001_a-problem/ticket.txt", "Status: closed.\n")
        self.repo.commit("two")
        self.repo.write(".cos/0001_a-problem/ticket.txt", "Status: closed.\nmore\n")
        self.repo.commit("three")

        self.run_import(history)
        rows = history.transitions(WS)
        self.assertEqual({r["artifact"] for r in rows}, {"ticket.txt"})
        self.assertEqual(
            [(r["from_state"], r["to_state"]) for r in rows],
            [("nowhere", "open"), ("open", "closed"), ("closed", "closed")],
        )
        self.assertEqual(len(settled_edits(rows, history.machine)), 1)
        self.assertEqual({r["machine"] for r in rows}, {"two-step"})


if __name__ == "__main__":
    unittest.main()
