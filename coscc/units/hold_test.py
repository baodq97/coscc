"""Pausing, dropping and resuming a unit: the pure shapes, the two side effects of a drop, and the
whole move through `Service.hold` on a real repository with a bare-directory remote. `gh` is a
stand-in throughout; no session is opened and nothing is paid for."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.units import hold
from coscc.agent import steps
from coscc.github import prcomment
from coscc.git import worktrees
from coscc.config import Config
from coscc.data import Data
from coscc.runlog.journal import Journal
from coscc.service import Service
from coscc.service.common import Invalid
from coscc.agent.submit_test import submits as _submits

SLUG = "proof-of-hold"
# What `Service` hands `hold.refusal` while a spec step runs on the unit.
BUSY = steps.describe("0001_x", steps.Mark("step", "spec", "running", "2026-09-24T01:02:03+00:00"))
BRANCH = f"feat/{SLUG}"
PR = 7


def git(cwd: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    ).stdout.strip()


class FakeGh:
    """Stands in for `prcomment._gh`: records argv, answers `pr list` from `prs`."""

    def __init__(self, prs: list[dict] | None = None, fail: str = ""):
        self.prs = (
            prs
            if prs is not None
            else [
                {
                    "number": PR,
                    "headRefOid": "a" * 40,
                    "headRefName": BRANCH,
                    "mergeable": "MERGEABLE",
                }
            ]
        )
        self.fail = fail
        self.calls: list[list[str]] = []

    async def __call__(self, argv, cwd, stdin):
        self.calls.append(list(argv))
        if self.fail and argv[:2] == ["pr", self.fail]:
            return 1, "", "HTTP 401: Bad credentials"
        if argv[:2] == ["pr", "list"]:
            return 0, json.dumps(self.prs), ""
        if argv[:2] == ["pr", "close"]:
            return 0, "", ""
        return 1, "", f"stand-in gh: unexpected {argv}"


def unit_row(**over) -> dict:
    row = {
        "name": "0001_x",
        "next": "write-spec",
        "hold": None,
        "hold_moves": ["paused", "dropped"],
    }
    row.update(over)
    return row


class TheRefusals(unittest.TestCase):
    def test_the_code_picks_the_sentence_and_the_words_do_not(self):
        said = hold.refusal(
            unit_row(next="finished", why="", hold_moves=[]), "paused", "r", "b", ""
        )
        self.assertIn("has no intent.md", said)
        said = hold.refusal(
            unit_row(next="write-spec", why="finished", hold_moves=[]), "paused", "r", "b", ""
        )
        self.assertIn("write-spec", said)

    def test_in_the_spec_order(self):
        self.assertEqual(
            hold.refusal(None, "paused", "r", "b", ""), "no such work unit in this workspace"
        )
        paused = unit_row(hold={"state": "paused"}, hold_moves=["dropped", "active"])
        self.assertEqual(
            hold.refusal(paused, "paused", "r", "b", ""),
            "0001_x is paused; from there it can go to dropped, active, not paused",
        )
        dropped = unit_row(hold={"state": "dropped"}, hold_moves=["paused"])
        self.assertIn("not active", hold.refusal(dropped, "active", "r", "b", ""))
        self.assertIn(
            "finished",
            hold.refusal(
                unit_row(next="finished", why="finished", hold_moves=[]), "paused", "r", "b", ""
            ),
        )
        self.assertIn(
            "closed",
            hold.refusal(
                unit_row(next="closed — spec rejected", why="rejected", hold_moves=[]),
                "paused",
                "r",
                "b",
                "",
            ),
        )
        self.assertIn(
            "no intent.md",
            hold.refusal(unit_row(next="write-intent", hold_moves=[]), "paused", "r", "b", ""),
        )
        # The move is checked before the words, the words before the running step.
        self.assertIn("from there", hold.refusal(paused, "paused", "", "", BUSY))
        self.assertEqual(hold.refusal(unit_row(), "paused", "  ", "b", BUSY), "the reason is empty")

    def test_reason_and_name_are_one_line_not_read_as_a_heading(self):
        for reason, said in (
            ("", "the reason is empty"),
            ("a\nb", "the reason must be one line"),
            ("# x", "the reason may not start with #"),
        ):
            self.assertEqual(hold.refusal(unit_row(), "paused", reason, "b", ""), said)
        for by, said in (
            ("", "the name is empty"),
            ("a\rb", "the name must be one line"),
            ("#b", "the name may not start with #"),
        ):
            self.assertEqual(hold.refusal(unit_row(), "paused", "r", by, ""), said)

    def test_a_running_step_is_refused_and_nothing_is_stopped(self):
        # The sentence is `steps.describe`'s, and names the Stop and the time.
        self.assertEqual(
            hold.refusal(unit_row(), "dropped", "r", "b", BUSY),
            BUSY + "; a hold does not stop anything itself",
        )
        self.assertIn("its Stop button on the Board (0034)", BUSY)
        self.assertEqual(hold.refusal(unit_row(), "dropped", "r", "b", ""), "")


class TheShapes(unittest.TestCase):
    def test_the_record(self):
        rec = hold.record(
            workspace="w", unit="u", from_="active", to="paused", reason="r", by="b", effects=[]
        )
        self.assertEqual(
            rec,
            {
                "kind": "hold",
                "workspace": "w",
                "unit": "u",
                "stage": "",
                "from": "active",
                "to": "paused",
                "reason": "r",
                "by": "b",
                "effects": [],
            },
        )


class ClosingThePullRequest(unittest.TestCase):
    def test_only_the_units_branch_and_never_the_remote_branch(self):
        gh = FakeGh(
            prs=[
                {"number": 3, "headRefName": "feat/somebody-else"},
                {"number": PR, "headRefName": BRANCH},
            ]
        )
        got = asyncio.run(hold.close_pr("/tmp", BRANCH, gh))
        self.assertEqual(got, {"effect": "close-pr", "result": "done", "detail": f"closed #{PR}"})
        closes = [c for c in gh.calls if c[:2] == ["pr", "close"]]
        self.assertEqual(closes, [["pr", "close", str(PR)]])
        self.assertFalse(any("--delete-branch" in c for c in gh.calls))

    def test_no_open_pull_request_is_skipped(self):
        got = asyncio.run(
            hold.close_pr("/tmp", BRANCH, FakeGh(prs=[{"number": 3, "headRefName": "feat/other"}]))
        )
        self.assertEqual(got["result"], "skipped")
        self.assertEqual(got["detail"], f"no open pull request on {BRANCH}")
        self.assertEqual(asyncio.run(hold.close_pr("/tmp", "", FakeGh()))["result"], "skipped")

    def test_gh_failing_is_failed_not_raised(self):
        for which, detail in (
            ("list", "HTTP 401: Bad credentials"),
            ("close", f"#{PR}: HTTP 401: Bad credentials"),
        ):
            got = asyncio.run(hold.close_pr("/tmp", BRANCH, FakeGh(fail=which)))
            self.assertEqual((got["result"], got["detail"]), ("failed", detail), which)

    def test_a_close_failing_after_another_says_which_were_closed(self):
        gh = FakeGh(
            prs=[
                {"number": PR, "headRefName": BRANCH},
                {"number": 9, "headRefName": BRANCH},
                {"number": 11, "headRefName": BRANCH},
            ]
        )
        real = gh.__call__

        async def second_fails(argv, cwd, stdin):
            if argv == ["pr", "close", "9"]:
                gh.calls.append(list(argv))
                return 1, "", "HTTP 502: Bad Gateway"
            return await real(argv, cwd, stdin)

        got = asyncio.run(hold.close_pr("/tmp", BRANCH, second_fails))
        self.assertEqual(
            got,
            {
                "effect": "close-pr",
                "result": "failed",
                "detail": f"closed #{PR}; #9: HTTP 502: Bad Gateway",
            },
        )
        # It stops at the failure: #11 is neither closed nor claimed.
        self.assertNotIn(["pr", "close", "11"], gh.calls)

        async def missing(argv, cwd, stdin):
            raise FileNotFoundError("gh")

        self.assertEqual(
            asyncio.run(hold.close_pr("/tmp", BRANCH, missing))["detail"],
            "gh is not installed or not on PATH",
        )


class Repo(unittest.TestCase):
    """A workspace cloned from a bare remote, a unit through `pr`, its branch and worktree."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.remote = root / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        seed = root / "seed"
        subprocess.run(
            ["git", "clone", "-q", str(self.remote), str(seed)], check=True, capture_output=True
        )
        (seed / "f.txt").write_text("one\n", encoding="utf-8")
        git(seed, "add", "-A")
        git(seed, "commit", "-q", "-m", "seed")
        git(seed, "push", "-q", "origin", "main")
        git(seed, "push", "-q", "origin", f"main:{BRANCH}")
        self.workspace = root / "work" / "proj"
        subprocess.run(
            ["git", "clone", "-q", str(self.remote), str(self.workspace)],
            check=True,
            capture_output=True,
        )
        git(self.workspace, "branch", BRANCH, f"origin/{BRANCH}")
        self.cwd = str(self.workspace)
        env = {"COS_DATA_DIR": str(root / "data"), "COS_WORKING_DIR": str(root / "work")}
        patch = mock.patch.dict(os.environ, env)
        patch.start()
        self.addCleanup(patch.stop)
        self.config = Config(
            workspaces=(self.cwd,), working_dir=str(root / "work"), data_dir=str(root / "data")
        )
        self.sessions = NoSession()
        self.service = Service(self.config, self.sessions)
        made = asyncio.run(self.service.create_unit(self.cwd, SLUG, "fixture"))
        self.unit, self.directory = made["unit"], Path(made["path"])
        for name in ("intent.md", "spec.md", "plan.md", "impl.md"):
            extra = " Type: feat." if name == "intent.md" else ""
            (self.directory / name).write_text(
                f"# X: fixture\nAuthor: t.{extra} Status: accepted.\n", encoding="utf-8"
            )
        (self.directory / "pr.md").write_text(
            f"# PR: fixture\nPR: https://github.com/o/r/pull/{PR}. Status: accepted.\n",
            encoding="utf-8",
        )
        self.tree = Path(asyncio.run(self.service._worktree(self.cwd, self.unit))["path"])
        self.key = self.service._journal_key(self.cwd)
        self.gh = FakeGh()
        gh_patch = mock.patch.object(prcomment, "_gh", self.gh)
        gh_patch.start()
        self.addCleanup(gh_patch.stop)

    def find(self):
        return asyncio.run(worktrees.find(self.cwd, self.unit, self.config.data_dir))

    def intent(self) -> bytes:
        return (self.directory / "intent.md").read_bytes()

    def move(self, to: str, reason: str = "lý do", by: str = "Leif") -> dict:
        return asyncio.run(self.service.hold(self.cwd, self.unit, to, reason, by))

    def board_unit(self) -> dict:
        data = asyncio.run(self.service.board(self.cwd))
        return next(u for u in data["units"] if u["name"] == self.unit)

    def records(self) -> list[dict]:
        return self.service._journal().records(self.key, kind="hold")

    def holds(self) -> list[dict]:
        with Data(self.config.data_dir).connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT move, reason FROM unit_holds WHERE unit = ? ORDER BY id", (self.unit,)
                )
            ]


class NoSession:
    """`Sessions` for a service that must never open one."""

    def __init__(self):
        self.membership = None
        self.opened = 0

    async def stream(self, *a, **kw):
        self.opened += 1
        await _submits(kw)
        yield ("done", {"session_id": "none"})


class RemovingTheWorktree(Repo):
    def test_a_clean_tree_is_removed_and_its_branch_kept(self):
        self.assertIsNotNone(self.find())
        got = asyncio.run(hold.remove_tree(self.cwd, self.unit, self.config.data_dir))
        self.assertEqual(got["result"], "done")
        self.assertIsNone(self.find())
        self.assertEqual(git(self.workspace, "branch", "--list", BRANCH).strip("* +"), BRANCH)
        again = asyncio.run(hold.remove_tree(self.cwd, self.unit, self.config.data_dir))
        self.assertEqual(
            (again["result"], again["detail"]), ("skipped", "the unit has no worktree")
        )

    def test_a_dirty_tree_is_left_and_reported(self):
        (self.tree / "f.txt").write_text("changed\n", encoding="utf-8")
        got = asyncio.run(hold.remove_tree(self.cwd, self.unit, self.config.data_dir))
        self.assertEqual(
            (got["result"], got["detail"]), ("failed", "worktree has uncommitted changes")
        )
        self.assertIsNotNone(self.find())
        self.assertEqual((self.tree / "f.txt").read_text(encoding="utf-8"), "changed\n")


class HoldThroughTheService(Repo):
    def test_the_five_moves_are_rows_and_the_board_reads_them(self):
        # A hold is a row in `unit_holds`; `intent.md` is not touched.
        path = [
            ("paused", "active"),
            ("dropped", "paused"),
            ("paused", "dropped"),
            ("active", "paused"),
            ("dropped", "active"),
        ]
        before = self.intent()
        for to, from_ in path:
            got = self.move(to, reason=f"vì {to}")
            self.assertEqual(
                (got["from"], got["to"], got["reason"], got["by"]), (from_, to, f"vì {to}", "Leif")
            )
            self.assertEqual(self.intent(), before, to)
            u = self.board_unit()
            self.assertEqual((u.get("hold") or {}).get("state"), None if to == "active" else to)
        self.assertEqual([(r["from"], r["to"]) for r in self.records()], [(f, t) for t, f in path])
        self.assertEqual([r["move"] for r in self.holds()], [t for t, _ in path])

    def test_a_hold_changes_no_byte_of_intent_md_and_writes_its_journal_row_in_the_same_transaction(
        self,
    ):
        before = self.intent()
        self.move("paused", reason="chờ")
        self.assertEqual(self.intent(), before)
        self.assertEqual((len(self.holds()), len(self.records())), (1, 1))
        # The run-log row fails after the hold's row was written: neither is kept.
        with mock.patch.object(
            Journal, "_insert", side_effect=sqlite3.OperationalError("disk I/O error")
        ):
            with self.assertRaises(Invalid) as said:
                self.move("active", reason="tiếp")
        self.assertIn("the hold was not recorded", str(said.exception))
        self.assertNotIn("disk I/O error", str(said.exception))
        self.assertEqual((len(self.holds()), len(self.records())), (1, 1))
        self.assertEqual(self.board_unit()["hold"]["state"], "paused")

    def test_pause_touches_nothing_and_starts_nothing(self):
        got = self.move("paused")
        self.assertEqual(got["effects"], [])
        self.assertEqual(self.gh.calls, [])
        self.assertIsNotNone(self.find())
        self.move("active")
        self.assertEqual(self.sessions.opened, 0)
        self.assertEqual(self.service._journal().records(self.key, kind="start"), [])

    def test_a_refusal_changes_no_byte(self):
        before = self.intent()
        for to, reason, by in (
            ("active", "r", "b"),
            ("frozen", "r", "b"),
            ("paused", "", "b"),
            ("paused", "r", "a\nb"),
            ("paused", "# r", "b"),
        ):
            with self.assertRaises(Invalid, msg=(to, reason, by)):
                self.move(to, reason, by)
        self.move("dropped")
        dropped = self.intent()
        with self.assertRaises(Invalid) as said:
            self.move("active")
        self.assertIn("dropped; from there it can go to paused", str(said.exception))
        with self.assertRaises(Invalid):
            self.move("dropped")
        self.assertEqual(self.intent(), dropped)
        self.assertTrue(dropped.startswith(before))
        self.assertEqual(len(self.records()), 1)

    def test_a_running_step_refuses_and_keeps_its_mark(self):
        mark = self.service._take(self.key, self.unit, "step", "spec")
        mark.phase = "running"
        before = self.intent()
        with self.assertRaises(Invalid) as said:
            self.move("paused")
        self.assertIn("its Stop button on the Board (0034)", str(said.exception))
        self.assertEqual(self.intent(), before)
        self.assertIs(self.service._active.get((self.key, self.unit)), mark)
        self.service._release(self.key, self.unit, mark)
        self.move("paused")
        self.assertEqual(self.service._active, {})

    def test_a_section_after_answers_no_longer_refuses_a_hold(self):
        # It is a row now, and the file is not read for it.
        with (self.directory / "intent.md").open("a", encoding="utf-8") as f:
            f.write("\n## Answers\n\n## Later\n")
        before = self.intent()
        self.move("paused")
        self.assertEqual(self.intent(), before)
        self.assertEqual(self.board_unit()["hold"]["state"], "paused")

    def test_drop_closes_the_pull_request_and_removes_the_tree(self):
        got = self.move("dropped", reason="không chứng minh được giá trị")
        self.assertEqual([e["result"] for e in got["effects"]], ["done", "done"])
        self.assertIn(["pr", "close", str(PR)], self.gh.calls)
        self.assertFalse(any("--delete-branch" in c for c in self.gh.calls))
        self.assertIsNone(self.find())
        self.assertIn(BRANCH, git(self.workspace, "branch", "--list", BRANCH))
        rec = self.records()[-1]
        self.assertEqual([e["effect"] for e in rec["effects"]], ["close-pr", "remove-worktree"])
        nxt = asyncio.run(self.service.next_step(self.cwd, self.unit))
        self.assertEqual(nxt["stage"], "")
        self.assertEqual(nxt["hold"]["state"], "dropped")
        self.assertEqual(self.board_unit()["hold"]["state"], "dropped")

        async def run():
            async for _ in self.service.run_step(self.cwd, self.unit, "review"):
                pass

        with self.assertRaises(Invalid) as said:
            asyncio.run(run())
        self.assertIn("is dropped", str(said.exception))
        self.assertIsNone(self.find())
        self.assertEqual(self.sessions.opened, 0)
        # Back to paused: no pull request reopened, no tree recreated.
        calls = len(self.gh.calls)
        self.assertEqual(self.move("paused")["effects"], [])
        self.assertEqual(len(self.gh.calls), calls)
        self.assertIsNone(self.find())

    def test_a_failed_side_effect_still_records_the_drop(self):
        self.gh.fail = "list"
        (self.tree / "f.txt").write_text("changed\n", encoding="utf-8")
        got = self.move("dropped")
        self.assertEqual([e["result"] for e in got["effects"]], ["failed", "failed"])
        self.assertEqual([r["move"] for r in self.holds()], ["dropped"])
        self.assertEqual(self.records()[-1]["effects"], got["effects"])
        self.assertEqual(self.board_unit()["hold"]["state"], "dropped")

    def test_activity_carries_the_move(self):
        self.move("paused", reason="chờ 0034")
        ev = next(e for e in self.service.activity(self.cwd)["events"] if e["kind"] == "hold")
        self.assertEqual(
            (ev["from"], ev["to"], ev["reason"], ev["by"], ev["effects"]),
            ("active", "paused", "chờ 0034", "Leif", []),
        )


if __name__ == "__main__":
    unittest.main()
