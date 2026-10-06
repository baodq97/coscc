"""Which files `main` changed since a unit's plan was written.

The pure parts are tested on record lists; `compute` on a temporary repository
whose `origin` is a bare directory, so no network is touched. The expected list is always
`git diff --name-only` run by subprocess, never something this test worked out itself."""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
import unittest
from pathlib import Path

from coscc.git import drift


def start(head: str = "a" * 40, **kw) -> dict:
    return {"kind": "start", "stage": "plan", "head": head, **kw}


def end(outcome: str = "done") -> dict:
    return {"kind": "end", "stage": "plan", "outcome": outcome}


class ChoosingTheRunOfPlan(unittest.TestCase):
    """The `head` of the last `done` run of `plan`."""

    def test_the_last_done_run_wins_over_a_later_failed_one(self):
        records = [start("1" * 40), end(), start("2" * 40), end(), start("3" * 40), end("failed")]
        self.assertEqual(drift.plan_head(records), ("2" * 40, ""))

    def test_two_starts_in_a_row_pair_the_second_with_the_end(self):
        records = [start("1" * 40), start("2" * 40), end()]
        self.assertEqual(drift.plan_head(records), ("2" * 40, ""))

    def test_attempts_denials_and_other_stages_do_not_break_the_pairing(self):
        records = [
            start("1" * 40),
            {"kind": "denial", "stage": "plan"},
            {"kind": "start", "stage": "spec", "head": "9" * 40},
            {"kind": "attempt", "stage": "plan"},
            {"kind": "end", "stage": "spec", "outcome": "failed"},
            end(),
        ]
        self.assertEqual(drift.plan_head(records), ("1" * 40, ""))

    def test_each_reason_it_cannot_name_a_commit(self):
        for records in (
            [],
            [start("1" * 40), end("failed")],
            [start(""), end()],
            [start("abc1234"), end()],
        ):
            sha, reason = drift.plan_head(records)
            self.assertEqual(sha, "", records)
            self.assertTrue(reason, records)


class ComputingOnARealRepository(unittest.TestCase):
    """`compute` against `git`, with `origin` a bare directory. R4a, R4b and each R4c."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.remote = root / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        self.repo = root / "repo"
        self.repo.mkdir()
        self._git("init", "-q", "-b", "main")
        for name in ("src/a.py", "src/b.py", "lib/src/a.py"):
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / name).write_text("one\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "A")
        self.a = self._git("rev-parse", "HEAD").strip()
        self._git("remote", "add", "origin", str(self.remote))
        self._git("push", "-q", "origin", "main")
        self.files = ["src/a.py", "src/b.py"]

    def _git(self, *args: str) -> str:
        return subprocess.run(
            [
                "git",
                "-C",
                str(self.repo),
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@example.invalid",
                "-c",
                "commit.gpgsign=false",
                *args,
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    def _merge(self, *names: str) -> str:
        for name in names:
            (self.repo / name).write_text("two\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "B")
        self._git("push", "-q", "origin", "main")
        self._git("fetch", "-q", "origin")
        return self._git("rev-parse", "HEAD").strip()

    def _compute(self, records=None, files: object = "plan", tree="repo") -> dict:
        records = [start(self.a), end()] if records is None else records
        tree = self.repo if tree == "repo" else tree
        files = self.files if files == "plan" else files
        return asyncio.run(drift.compute(records, files, tree))

    def test_the_changed_files_the_plan_names(self):
        b = self._merge("src/a.py", "lib/src/a.py")
        got = self._compute()
        diff = self._git("diff", f"{self.a}..{b}", "--name-only").split()
        self.assertEqual(got["files"], [d for d in sorted(diff) if d in ("src/a.py", "src/b.py")])
        self.assertEqual(got["files"], ["src/a.py"])
        self.assertEqual((got["plan_sha"], got["main_sha"]), (self.a, b))
        self.assertTrue(got["checked"])
        self.assertEqual(got["reason"], "")

    def test_nothing_the_plan_names_changed(self):
        self._merge("lib/src/a.py")
        got = self._compute()
        self.assertEqual(got["files"], [])
        self.assertTrue(got["checked"])

    def test_only_the_records_files_count_not_a_longer_path(self):
        self._merge("lib/src/a.py", "src/b.py")
        self.assertEqual(self._compute()["files"], ["src/b.py"])

    def test_each_cause_is_unchecked_with_a_reason(self):
        self._merge("src/a.py")
        cases = {
            "no run of plan": dict(records=[]),
            "empty head": dict(records=[start(""), end()]),
            "no plan record": dict(files=None),
            "no files named": dict(files=[]),
            "unknown commit": dict(records=[start("0" * 40), end()]),
            "no worktree": dict(tree=None),
        }
        for name, kw in cases.items():
            got = self._compute(**kw)
            self.assertFalse(got["checked"], name)
            self.assertIsNone(got["files"], name)
            self.assertTrue(got["reason"], name)

    def test_no_origin_main(self):
        self._git("update-ref", "-d", "refs/remotes/origin/main")
        got = self._compute()
        self.assertFalse(got["checked"])
        self.assertIsNone(got["files"])
        self.assertEqual(got["plan_sha"], self.a)
        self.assertIsNone(got["main_sha"])
        self.assertIn("origin/main", got["reason"])


if __name__ == "__main__":
    unittest.main()
