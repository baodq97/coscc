"""`0042`: which files `main` changed since a unit's plan was written.

The pure parts are tested on strings and record lists; `compute` on a temporary repository
whose `origin` is a bare directory, so no network is touched. The expected list is always
`git diff --name-only` run by subprocess, never something this test worked out itself.
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
import unittest
from pathlib import Path

from coscc import drift

PLAN = """# Plan: x
Intent: intent.md. Status: accepted.

## Files that change

| Path | What |
|---|---|
| `coscc/a.py` | table form |
| `coscc/c.py:19-27` | table form with lines |

- `coscc/b.py:10` bullet form
- coscc/d.py, bare

### A subheading still inside

`coscc/e.py`

## Order of work

`coscc/f.py`

## Answers

`coscc/g.py`
"""


def start(head: str = "a" * 40, **kw) -> dict:
    return {"kind": "start", "stage": "plan", "head": head, **kw}


def end(outcome: str = "done") -> dict:
    return {"kind": "end", "stage": "plan", "outcome": outcome}


class TheSectionAndTheMatch(unittest.TestCase):
    """`spec.md` R3 and R9, the cases that need no repository."""

    def setUp(self):
        self.section = drift.files_section(PLAN)

    def test_the_section_stops_at_the_next_level_two_heading(self):
        self.assertIn("coscc/e.py", self.section)
        self.assertNotIn("coscc/f.py", self.section)
        self.assertNotIn("coscc/g.py", self.section)

    def test_no_section_is_none_not_empty(self):
        self.assertIsNone(drift.files_section("# Plan\n\n## Order of work\n\n`coscc/a.py`\n"))

    def test_the_last_section_runs_to_the_end_of_the_file(self):
        self.assertEqual(
            drift.files_section("x\n## Files that change\n`a.py`\n"), "## Files that change\n`a.py`"
        )

    def test_table_bullet_and_line_suffix_forms_all_match(self):
        got = drift.mentioned(
            self.section,
            ["coscc/a.py", "coscc/b.py", "coscc/c.py", "coscc/d.py", "coscc/e.py"],
        )
        self.assertEqual(got, ["coscc/a.py", "coscc/b.py", "coscc/c.py", "coscc/d.py", "coscc/e.py"])

    def test_a_path_inside_a_longer_one_does_not_match_either_way(self):
        self.assertEqual(
            drift.mentioned(self.section, ["lib/coscc/a.py", "coscc/a.pyx", "oscc/a.py", "coscc/a.p"]),
            [],
        )
        self.assertEqual(drift.mentioned("## Files that change\n`lib/coscc/a.py`\n", ["coscc/a.py"]), [])
        self.assertEqual(drift.mentioned("## Files that change\n`coscc/a.pyx`\n", ["coscc/a.py"]), [])

    def test_a_path_only_under_answers_or_a_later_section_is_not_counted(self):
        self.assertEqual(drift.mentioned(self.section, ["coscc/f.py", "coscc/g.py"]), [])

    def test_a_path_in_the_diff_the_plan_does_not_name_is_left_out(self):
        self.assertEqual(drift.mentioned(self.section, ["coscc/a.py", "README.md"]), ["coscc/a.py"])

    def test_the_result_is_in_byte_order(self):
        section = "## Files that change\n`b.py` `B.py` `a.py`\n"
        self.assertEqual(drift.mentioned(section, ["b.py", "a.py", "B.py"]), ["B.py", "a.py", "b.py"])


class ChoosingTheRunOfPlan(unittest.TestCase):
    """R1 with `spec.md ## Answers, câu 1`: the `head` of the last `done` run of `plan`."""

    def test_the_last_done_run_wins_over_a_later_failed_one(self):
        records = [start("1" * 40), end(), start("2" * 40), end(), start("3" * 40), end("failed")]
        self.assertEqual(drift.plan_head(records), ("2" * 40, ""))

    def test_a_start_with_no_end_is_not_chosen(self):
        records = [start("1" * 40), end(), start("2" * 40)]
        self.assertEqual(drift.plan_head(records), ("1" * 40, ""))

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
        for records in ([], [start("1" * 40), end("failed")], [start(""), end()], [start("abc1234"), end()]):
            sha, reason = drift.plan_head(records)
            self.assertEqual(sha, "", records)
            self.assertTrue(reason, records)


class TheSentence(unittest.TestCase):
    def test_nothing_changed_adds_nothing(self):
        self.assertEqual(
            drift.describe({"plan_sha": "a" * 40, "main_sha": "b" * 40, "files": [], "checked": True, "reason": ""}),
            "",
        )

    def test_unchecked_is_one_sentence_with_no_list(self):
        said = drift.describe(
            {"plan_sha": "a" * 40, "main_sha": None, "files": None, "checked": False, "reason": "no origin"}
        )
        self.assertTrue(said.startswith("The app could not check"))
        self.assertIn("no origin", said)
        self.assertEqual(said.count("\n"), 0)
        self.assertNotIn("git diff", said)

    def test_changed_files_each_carry_their_diff_command_and_the_three_instructions(self):
        said = drift.describe(
            {"plan_sha": "a" * 40, "main_sha": "b" * 40, "files": ["x.py", "y.py"], "checked": True, "reason": ""}
        )
        self.assertIn(f"git diff {'a' * 40}..{'b' * 40} -- x.py", said)
        self.assertIn(f"git diff {'a' * 40}..{'b' * 40} -- y.py", said)
        self.assertIn("## What is still open", said)
        self.assertIn("Status: draft", said)
        self.assertIn("do not edit `plan.md`", said)


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
        self.plan = "## Files that change\n\n| `src/a.py` | x |\n| `src/b.py:3` | y |\n"

    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repo), "-c", "user.name=T",
             "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
            capture_output=True, text=True, check=True,
        ).stdout

    def _merge(self, *names: str) -> str:
        for name in names:
            (self.repo / name).write_text("two\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "B")
        self._git("push", "-q", "origin", "main")
        self._git("fetch", "-q", "origin")
        return self._git("rev-parse", "HEAD").strip()

    def _compute(self, records=None, plan=None, tree="repo") -> dict:
        records = [start(self.a), end()] if records is None else records
        tree = self.repo if tree == "repo" else tree
        return asyncio.run(drift.compute(records, self.plan if plan is None else plan, tree))

    def test_r4a_the_changed_files_the_plan_names(self):
        b = self._merge("src/a.py", "lib/src/a.py")
        got = self._compute()
        diff = self._git("diff", f"{self.a}..{b}", "--name-only").split()
        self.assertEqual(got["files"], [d for d in sorted(diff) if d in ("src/a.py", "src/b.py")])
        self.assertEqual(got["files"], ["src/a.py"])
        self.assertEqual((got["plan_sha"], got["main_sha"]), (self.a, b))
        self.assertTrue(got["checked"])
        self.assertEqual(got["reason"], "")

    def test_r4b_nothing_the_plan_names_changed(self):
        self._merge("lib/src/a.py")
        got = self._compute()
        self.assertEqual(got["files"], [])
        self.assertTrue(got["checked"])

    def test_r4c_each_cause_is_unchecked_with_a_reason(self):
        self._merge("src/a.py")
        cases = {
            "no run of plan": dict(records=[]),
            "empty head": dict(records=[start(""), end()]),
            "no section": dict(plan="# Plan\n\n## Order of work\n\n`src/a.py`\n"),
            "unknown commit": dict(records=[start("0" * 40), end()]),
            "no worktree": dict(tree=None),
        }
        for name, kw in cases.items():
            got = self._compute(**kw)
            self.assertFalse(got["checked"], name)
            self.assertIsNone(got["files"], name)
            self.assertTrue(got["reason"], name)

    def test_r4c_no_origin_main(self):
        self._git("update-ref", "-d", "refs/remotes/origin/main")
        got = self._compute()
        self.assertFalse(got["checked"])
        self.assertIsNone(got["files"])
        self.assertEqual(got["plan_sha"], self.a)
        self.assertIsNone(got["main_sha"])
        self.assertIn("origin/main", got["reason"])


if __name__ == "__main__":
    unittest.main()
