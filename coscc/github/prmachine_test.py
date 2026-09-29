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
PR_NUMBER = 7


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

    def opened(self, gh):
        m = self.machine(gh)
        run(m.open_pr(self.unit()))
        return m

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

    def test_a_clean_rebase_the_gate_read_merges_pinned_to_the_new_head(self):
        """Review round 1, F1: after a rebase `0067` accepts, `ship` merges the new head
        without another round; a rebase of some other commit does not."""
        new = "b" * 40
        gh = FakeGh(head=new)
        m = self.opened(gh)
        self.a_round(head=HEAD)
        refused = run(m.ship(self.unit(), rebased={"reviewed": "c" * 40, "head": new}))
        self.assertEqual((refused.result, refused.reasons), ("refused", ("head-moved",)))
        out = run(m.ship(self.unit(), rebased={"reviewed": HEAD, "head": new}))
        self.assertEqual(out.result, "merged")
        merge = next(c for c in gh.calls if c[:2] == ["pr", "merge"])
        self.assertEqual(merge[-1], new)
        [requested] = [r for r in self.rows("ship.md") if r["guard"] == "ship-ready"]
        inputs = json.loads(requested["inputs"])
        self.assertEqual((inputs["reviewed_head"], inputs["head"], inputs["rebased"]),
                         (HEAD, new, {"reviewed": HEAD, "head": new}))

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


class TheReaderRecordsWhatChanged(Fixture):
    """R23, spec Design "Người đọc PR": each change a read finds is a transition of the
    machine, and a read that finds none writes nothing."""

    def reader(self, gh, files=("a.py",)):
        m = self.opened(gh)
        self.file_reads: list[str] = []

        async def read_files(tree, head):
            self.file_reads.append(head)
            return None if files is None else list(files)

        m._files = read_files
        return m

    def read(self, m):
        return run(m.read(str(self.directory), WS, lambda name: self.directory))

    def test_no_pull_request_the_machine_watches_calls_nothing(self):
        gh = FakeGh()
        got = run(self.machine(gh).read(str(self.directory), WS, lambda name: self.directory))
        self.assertEqual((got.moved, got.calls, gh.calls), ([], 0, []))

    def test_pending_then_green_is_two_transitions_and_green_is_not_asked_again(self):
        gh = FakeGh(buckets=("pending",))
        m = self.reader(gh)
        self.assertEqual(self.read(m).moved, [(NAME, "ci")])
        self.assertEqual(prmachine.state(self.history, WS, NAME)["ci"], "pending")
        # Still pending: asked again, nothing written.
        self.assertEqual(self.read(m).moved, [])
        gh.buckets = ("pass",)
        self.assertEqual(self.read(m).moved, [(NAME, "ci")])
        self.assertEqual(prmachine.state(self.history, WS, NAME)["ci"], "green")
        checks = gh.count("pr", "checks")
        got = self.read(m)
        self.assertEqual((got.moved, got.calls, gh.count("pr", "checks")), ([], 1, checks),
                         "green at the same head is settled: only the list is read")
        rows = [r for r in self.rows("pr.md") if r["guard"] == "ci-at-head"]
        self.assertEqual([(r["to_state"], r["authority"], json.loads(r["inputs"])["ci"]) for r in rows],
                         [("accepted", "code", "pending"), ("accepted", "code", "green")])
        self.assertEqual(self.file_reads, [HEAD], "the files are read once for a head")

    def test_ci_is_written_only_through_ci_at_head(self):
        # `0139` R8: opening writes the row and no answer; the reader's `ci` transition and the
        # board's `record_ci` write it, each beside a `ci-at-head` row.
        gh = FakeGh(buckets=("pending",))
        m = self.reader(gh)
        self.assertIsNone(prmachine.ci_held(self.history, WS, PR_NUMBER, HEAD))
        self.read(m)
        held = prmachine.ci_held(self.history, WS, PR_NUMBER, HEAD)
        self.assertEqual((held["head"], held["ci"]), (HEAD, "pending"))
        self.assertTrue(m.record_ci(self.unit(), PR_NUMBER, HEAD, [{"name": "t", "bucket": "fail"}]))
        held = prmachine.ci_held(self.history, WS, PR_NUMBER, HEAD)
        self.assertEqual((held["ci"], held["checks"]), ("red", [{"name": "t", "bucket": "fail"}]))
        self.assertEqual(prmachine.state(self.history, WS, NAME)["ci"], "red")
        rows = [json.loads(r["inputs"])["ci"] for r in self.rows("pr.md") if r["guard"] == "ci-at-head"]
        self.assertEqual(rows, ["pending", "red"])
        # The same answer again is no transition; only when it was read moves.
        self.assertTrue(m.record_ci(self.unit(), PR_NUMBER, HEAD, [{"name": "t", "bucket": "fail"}]))
        self.assertEqual(len([r for r in self.rows("pr.md") if r["guard"] == "ci-at-head"]), 2)

    def test_a_new_head_is_read_again_with_its_files(self):
        gh = FakeGh(buckets=("pass",))
        m = self.reader(gh)
        self.read(m)
        other = "b" * 40
        gh.open_prs[0]["headRefOid"] = other
        self.assertEqual(self.read(m).moved, [(NAME, "ci")])
        now = prmachine.state(self.history, WS, NAME)
        self.assertEqual((now["head"], now["ci"]), (other, "green"))
        self.assertEqual(self.file_reads, [HEAD, other])
        self.assertEqual(prmachine.open_prs(self.history, WS), [{"unit": NAME, "number": 7, "files": {"a.py"}}])

    def test_files_that_cannot_be_read_are_none(self):
        """C12: `pick` counts that as every file."""
        m = self.reader(FakeGh(), files=None)
        self.read(m)
        self.assertEqual(prmachine.open_prs(self.history, WS), [{"unit": NAME, "number": 7, "files": None}])

    def test_a_merge_made_outside_is_recorded_and_merges_nothing(self):
        gh = FakeGh()
        m = self.reader(gh)
        gh.open_prs, gh.state = [], "MERGED"
        self.assertEqual(self.read(m).moved, [(NAME, "merged")])
        self.assertEqual(prmachine.state(self.history, WS, NAME)["state"], "merged")
        self.assertEqual(gh.count("pr", "merge"), 0)
        self.assertIn("Status: accepted", (self.directory / "ship.md").read_text(encoding="utf-8"))
        self.assertEqual(prmachine.open_prs(self.history, WS), [])
        calls = len(gh.calls)
        self.assertEqual((self.read(m).moved, len(gh.calls)), ([], calls), "a merged one is not watched")

    def test_a_closed_pull_request_sends_pr_back(self):
        gh = FakeGh()
        m = self.reader(gh)
        gh.open_prs, gh.state = [], "CLOSED"
        self.assertEqual(self.read(m).moved, [(NAME, "closed")])
        self.assertEqual(prmachine.state(self.history, WS, NAME)["state"], "closed")
        [row] = [r for r in self.rows("pr.md") if r["guard"] == "close-read"]
        self.assertEqual(row["to_state"], "draft")

    def test_a_list_that_cannot_be_read_records_nothing(self):
        gh = FakeGh()
        m = self.reader(gh)
        before = len(self.history.transitions(WS, NAME))

        async def broken(argv, cwd):
            return 1, "", "HTTP 502"

        m._gh = broken
        got = self.read(m)
        self.assertEqual((got.moved, got.error), ([], "HTTP 502"))
        self.assertEqual(len(self.history.transitions(WS, NAME)), before)


if __name__ == "__main__":
    unittest.main()
