"""What a unit has written that could carry a value out, and a value found in each."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.store.db import Data
from coscc.store.journal import Journal
from coscc.vault import filters, sources
from coscc.vault.sources import unit_sources

WS = "/ws/proj"
UNIT = "0001_demo"
VALUE = b"tok-9f8e7d6c5b4a"


def git(tree: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(tree), *args],
        check=True,
        capture_output=True,
    )


class WhereAUnitMayHaveLeftAValue(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.data = Data(self.root / "data")
        self.journal = Journal(self.root / "work", self.data)
        self.directory = self.root / "unit"
        self.tree = self.root / "tree"
        self.tree.mkdir()
        self.directory.mkdir()
        git(self.tree, "init", "-q", "-b", "main")
        (self.tree / "a.txt").write_text("nothing\n")
        git(self.tree, "add", ".")
        git(self.tree, "commit", "-q", "-m", "base")
        git(self.tree, "checkout", "-q", "-b", "unit")

    def sources(self, **more):
        return dict(unit_sources(self.journal, WS, UNIT, self.directory, str(self.tree), **more))

    def test_a_clean_unit_has_nothing_to_find(self):
        values = {"ws:k": VALUE}
        self.assertEqual(filters.scan(values, self.sources().items()), [])

    def test_a_commit_that_added_the_value_is_found_by_git_log_from_the_base(self):
        (self.tree / "b.txt").write_text(f"key={VALUE.decode()}\n")
        git(self.tree, "add", ".")
        git(self.tree, "commit", "-q", "-m", "oops")
        found = self.sources()
        self.assertIn(VALUE, found["commits"])
        self.assertEqual(
            [h.where for h in filters.scan({"ws:k": VALUE}, found.items())], ["commits"]
        )

    def test_a_value_taken_out_again_is_still_in_the_history_and_found(self):
        (self.tree / "b.txt").write_text(VALUE.decode())
        git(self.tree, "add", ".")
        git(self.tree, "commit", "-q", "-m", "add")
        git(self.tree, "rm", "-q", "b.txt")
        git(self.tree, "commit", "-q", "-m", "remove")
        self.assertIn(VALUE, self.sources()["commits"])

    def test_the_artifacts_are_read_pr_md_included_and_named_by_path(self):
        (self.directory / "pr.md").write_bytes(b"body " + VALUE)
        (self.directory / "sub").mkdir()
        (self.directory / "sub" / "notes.md").write_bytes(b"clean")
        found = self.sources()
        self.assertEqual(found["artifact:pr.md"], b"body " + VALUE)
        self.assertEqual(found["artifact:sub/notes.md"], b"clean")

    def test_run_log_lines_and_the_transcripts_of_the_units_runs_are_read(self):
        self.journal.started(WS, UNIT, "impl", "manual", run="run-1", note=VALUE.decode())
        self.data.step_run_open("run-1", str(self.journal.working_dir), WS, UNIT, "impl", 1)
        self.data.step_events_add(
            "run-1", [{"seq": 1, "at": 1, "kind": "text", "text": "said " + VALUE.decode()}]
        )
        self.journal.started(WS, "0002_other", "impl", "manual", run="run-2", note="other")
        found = self.sources()
        self.assertIn(VALUE, found["run-log"])
        self.assertIn(VALUE, found["transcript:run-1"])
        self.assertNotIn("transcript:run-2", found)
        self.assertEqual(
            sorted(h.where for h in filters.scan({"ws:k": VALUE}, found.items())),
            ["run-log", "transcript:run-1"],
        )

    def test_the_pull_request_is_asked_of_gh_only_when_it_is_wanted(self):
        with mock.patch.object(sources, "_call", return_value=b'{"body": "x"}') as called:
            without = self.sources()
            with_it = self.sources(pull_request=True)
        self.assertNotIn("pull-request", without)
        self.assertEqual(with_it["pull-request"], b'{"body": "x"}')
        self.assertEqual(called.call_args.args[0][:3], ["gh", "pr", "view"])

    def test_a_source_that_cannot_be_read_is_left_out_and_never_raised(self):
        unread: list[str] = []
        gone = unit_sources(
            None,
            WS,
            UNIT,
            self.root / "nowhere",
            str(self.root / "no-tree"),
            pull_request=True,
            unread=unread,
        )
        self.assertEqual((gone, unread), ([], ["commits", "pull-request"]))

    def test_commits_read_with_none_new_are_not_unread(self):
        unread: list[str] = []
        with mock.patch.object(sources, "_pull_request", return_value=[("pull-request", b"{}")]):
            found = dict(
                unit_sources(
                    None, WS, UNIT, self.directory, str(self.tree), pull_request=True, unread=unread
                )
            )
        self.assertEqual((found["commits"], unread), (b"", []))


if __name__ == "__main__":
    unittest.main()
