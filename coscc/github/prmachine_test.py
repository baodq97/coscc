"""`0136` R10, R12, R13: `pr` and `ship` are the app's own actions, through the PR machine."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from coscc.data import Data
from coscc.github import prmachine
from coscc.runlog.journal import Journal
from coscc.units.history import History

WS = "repo"
NAME = "0007_a-problem"
BRANCH = "feat/a-problem"
HEAD = "a" * 40
MERGE = "m" * 40


class Crash(BaseException):
    """The process dying: not an `Exception`, so nothing on the way up catches it."""


class FakeGh:
    """`gh` as far as the machine asks it. `crash_after_merge` merges on GitHub and then dies
    before the caller hears back (R13)."""

    def __init__(self, *, open_prs=(), state="OPEN", head=HEAD, buckets=("pass",), merge_code=0,
                 crash_after_merge=False):
        self.calls: list[list[str]] = []
        self.open_prs = list(open_prs)
        self.state = state
        self.head = head
        self.buckets = buckets
        self.merge_code = merge_code
        self.crash_after_merge = crash_after_merge

    def count(self, *words):
        return sum(1 for c in self.calls if c[: len(words)] == list(words))

    async def __call__(self, argv, cwd):
        self.calls.append(list(argv))
        verb = argv[:2]
        if verb == ["pr", "list"]:
            return 0, json.dumps(self.open_prs), ""
        if verb == ["pr", "create"]:
            self.open_prs = [{"number": 7, "url": "https://github.com/o/r/pull/7", "headRefOid": self.head}]
            return 0, "https://github.com/o/r/pull/7\n", ""
        if verb == ["pr", "view"]:
            merged = {"oid": MERGE} if self.state == "MERGED" else None
            return 0, json.dumps({"state": self.state, "mergeCommit": merged, "headRefOid": self.head,
                                  "url": "https://github.com/o/r/pull/7"}), ""
        if verb == ["pr", "checks"]:
            return 0, json.dumps([{"name": "test", "bucket": b} for b in self.buckets]), ""
        if verb == ["pr", "merge"]:
            if self.merge_code == 0:
                self.state = "MERGED"
            if self.crash_after_merge:
                raise Crash()
            return self.merge_code, "", "" if self.merge_code == 0 else "Pull request is not mergeable"
        raise AssertionError(f"unexpected gh call {argv}")


def run(coro):
    return asyncio.run(coro)


class Fixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.data = Data(root / "data")
        self.history = History(root / "work", self.data)
        self.journal = Journal(root / "work", self.data)
        self.directory = root / "store" / NAME
        self.directory.mkdir(parents=True)
        self.pushed: list[tuple[str, str]] = []

    def unit(self, branch=BRANCH, expected=BRANCH):
        return prmachine.Unit(WS, NAME, self.directory, str(self.directory), branch, expected, "feat")

    def machine(self, gh):
        async def push(tree, branch):
            self.pushed.append((tree, branch))

        async def head(tree):
            return HEAD

        return prmachine.Machine(self.history, self.journal, gh=gh, push=push, head=head)

    def a_round(self, verdict="pass", head=HEAD, n=1):
        with self.data.write() as conn:
            conn.execute(
                "INSERT INTO review_rounds (at, root, workspace, unit, n, run, head, verdict, screens) "
                "VALUES ('2026-09-29', ?, ?, ?, ?, 'r', ?, ?, '[]')",
                (str(self.history.working_dir), WS, NAME, n, head, verdict),
            )

    def rows(self, artifact):
        return [r for r in self.history.transitions(WS, NAME) if r["artifact"] == artifact]

    def starts(self, stage):
        with self.data.connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM runs WHERE kind = 'start' AND stage = ?", (stage,)).fetchone()[0]


class PrIsMechanical(Fixture):
    """R12."""

    def test_pr_twice_creates_one_pull_request(self):
        gh = FakeGh()
        m = self.machine(gh)
        first = run(m.open_pr(self.unit()))
        second = run(m.open_pr(self.unit()))
        self.assertEqual((first.result, first.number), ("opened", 7))
        self.assertEqual((second.result, second.number), ("already", 7))
        self.assertEqual(gh.count("pr", "create"), 1)
        rows = self.rows("pr.md")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["to_state"], rows[0]["guard"], rows[0]["authority"]),
                         ("accepted", "branch-named", "code"))
        self.assertEqual(json.loads(rows[0]["inputs"])["head"], HEAD)
        self.assertEqual(self.starts("pr"), 0, "no session ran, so no start record names pr")

    def test_the_title_is_type_number_and_slug(self):
        gh = FakeGh()
        run(self.machine(gh).open_pr(self.unit()))
        create = next(c for c in gh.calls if c[:2] == ["pr", "create"])
        self.assertEqual(create[create.index("--title") + 1], "feat(0007): a problem")
        text = (self.directory / "pr.md").read_text(encoding="utf-8")
        self.assertIn("# PR: feat(0007): a problem", text)
        self.assertIn("PR: https://github.com/o/r/pull/7", text)

    def test_an_open_pull_request_of_the_branch_is_taken_not_created(self):
        gh = FakeGh(open_prs=[{"number": 3, "url": "https://github.com/o/r/pull/3", "headRefOid": HEAD}])
        out = run(self.machine(gh).open_pr(self.unit()))
        self.assertEqual((out.result, out.number), ("found", 3))
        self.assertEqual(gh.count("pr", "create"), 0)

    def test_a_branch_the_unit_does_not_name_is_refused_before_anything_is_pushed(self):
        gh = FakeGh()
        out = run(self.machine(gh).open_pr(self.unit(branch="feat/other")))
        self.assertEqual((out.result, out.reasons), ("refused", ("bad-branch",)))
        self.assertEqual((self.pushed, gh.calls, self.rows("pr.md")), ([], [], []))


class ShipIsMechanical(Fixture):
    """R10, R13."""

    def opened(self, gh):
        m = self.machine(gh)
        run(m.open_pr(self.unit()))
        return m

    def test_a_pass_on_green_ci_merges_pinned_to_the_head_it_read(self):
        gh = FakeGh()
        m = self.opened(gh)
        self.a_round()
        out = run(m.ship(self.unit()))
        self.assertEqual((out.result, out.merge_commit), ("merged", MERGE))
        merge = next(c for c in gh.calls if c[:2] == ["pr", "merge"])
        self.assertEqual(merge, ["pr", "merge", "7", "--squash", "--delete-branch", "--match-head-commit", HEAD])
        rows = self.rows("ship.md")
        self.assertEqual([(r["to_state"], r["guard"]) for r in rows],
                         [("draft", "ship-ready"), ("accepted", "merge-read")])
        self.assertEqual(prmachine.state(self.history, WS, NAME)["state"], "merged")
        self.assertIn("Status: accepted. Round: 1", (self.directory / "ship.md").read_text(encoding="utf-8"))

    def test_a_closed_guard_never_calls_merge(self):
        for gh, round_, reason in (
            (FakeGh(buckets=("pending",)), "pass", "ci-pending"),
            (FakeGh(buckets=("fail",)), "pass", "ci-red"),
            (FakeGh(), "changes-requested", "changes-requested"),
            (FakeGh(head="b" * 40), "pass", "head-moved"),
        ):
            with self.subTest(reason=reason):
                self.setUp()
                m = self.opened(gh)
                self.a_round(verdict=round_)
                out = run(m.ship(self.unit()))
                self.assertEqual(out.result, "refused")
                self.assertIn(reason, out.reasons)
                self.assertEqual(gh.count("pr", "merge"), 0)
                self.assertEqual(self.rows("ship.md"), [])

    def test_no_round_the_app_holds_is_no_pass(self):
        gh = FakeGh()
        m = self.opened(gh)
        out = run(m.ship(self.unit()))
        self.assertEqual(out.result, "refused")
        self.assertIn("review-incomplete", out.reasons)
        self.assertEqual(gh.count("pr", "merge"), 0)

    def test_a_crash_after_merge_merges_once_and_ships(self):
        gh = FakeGh(crash_after_merge=True)
        m = self.opened(gh)
        self.a_round()
        with self.assertRaises(Crash):
            run(m.ship(self.unit()))
        self.assertEqual(prmachine.state(self.history, WS, NAME)["state"], "merge-requested",
                         "step 1 was committed before the merge was asked")
        # The restart: a new machine over the same database.
        again = self.machine(gh)
        done = run(again.reconcile([self.unit()]))
        self.assertEqual([o.result for o in done], ["recorded"])
        self.assertEqual(gh.count("pr", "merge"), 1)
        self.assertEqual(prmachine.state(self.history, WS, NAME)["state"], "merged")
        # A ship pressed after it records nothing more and merges nothing.
        self.assertEqual(run(again.ship(self.unit())).result, "already")
        self.assertEqual(gh.count("pr", "merge"), 1)

    def test_a_merge_made_outside_is_only_recorded(self):
        gh = FakeGh()
        m = self.opened(gh)
        gh.state = "MERGED"
        out = run(m.ship(self.unit()))
        self.assertEqual((out.result, out.merge_commit), ("recorded", MERGE))
        self.assertEqual(gh.count("pr", "merge"), 0)
        self.assertEqual([r["guard"] for r in self.rows("ship.md")], ["merge-read"])

    def test_a_refused_merge_leaves_a_draft_that_names_the_refusal(self):
        gh = FakeGh(merge_code=1)
        m = self.opened(gh)
        self.a_round()
        out = run(m.ship(self.unit()))
        self.assertEqual(out.result, "failed")
        text = (self.directory / "ship.md").read_text(encoding="utf-8")
        self.assertIn("Status: draft. Round: 1", text)
        self.assertIn("Refused: Pull request is not mergeable", text)
        self.assertEqual(prmachine.state(self.history, WS, NAME)["state"], "merge-requested")

    def test_a_pull_request_a_session_opened_is_found_by_its_branch(self):
        gh = FakeGh(open_prs=[{"number": 3, "url": "https://github.com/o/r/pull/3", "headRefOid": HEAD}])
        self.a_round()
        out = run(self.machine(gh).ship(self.unit()))
        self.assertEqual(out.number, 3)
        self.assertEqual(gh.count("pr", "merge"), 1)


if __name__ == "__main__":
    unittest.main()
