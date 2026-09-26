"""Tests for `BacklogMixin` in `coscc/service_backlog.py`, split from `coscc/service_test.py` (`0095`).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from coscc import gitops, units, worktrees
from coscc.config import Config
from coscc.service_common import Invalid
from coscc.service import Service
from coscc.sessions import Sessions
from coscc.service_test import REPO, _service, create_sync


class TheUnitHistoryReadPath(unittest.TestCase):
    """`0013` R8. The log read through the one place logic lives.

    Written against a temporary working folder and data root rather than this repository's
    real `~/.cos` — `coscc/journal.py:108-109` names that hazard and the two new tables
    inherit it unchanged.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.work = self.root / "work"
        self.work.mkdir()
        self.service = _service(working_dir=str(self.work), data_dir=str(self.root / "data"))

    def _log(self):
        from coscc.history import History

        return History(self.work, self.root / "data")

    def test_a_unit_with_no_history_answers_with_empty_rather_than_refusing(self):
        found = self.service.unit_history(REPO, "0001_a-problem")
        self.assertTrue(found["recording"])
        self.assertEqual(found["transitions"], [])
        self.assertEqual(found["settled_edits"], 0)

    def test_the_state_it_returns_is_a_projection_of_the_rows_it_returns(self):
        log = self._log()
        log.record(REPO, "0001_a-problem", "intent.md", "draft")
        log.record(REPO, "0001_a-problem", "intent.md", "accepted")
        found = self.service.unit_history(REPO, "0001_a-problem")
        # Not two sources: the last transition's destination *is* the state.
        self.assertEqual(found["state"]["intent.md"], found["transitions"][-1]["to_state"])
        self.assertEqual(found["state"]["intent.md"], "accepted")

    def test_it_counts_the_edits_after_settling_that_0013_exists_to_count(self):
        log = self._log()
        log.record(REPO, "0001_a-problem", "intent.md", "draft")
        log.record(REPO, "0001_a-problem", "intent.md", "accepted")
        log.record(REPO, "0001_a-problem", "intent.md", "accepted", source="commit:abc")
        self.assertEqual(self.service.unit_history(REPO, "0001_a-problem")["settled_edits"], 1)

    def test_a_unit_retired_from_the_working_tree_still_has_a_history(self):
        log = self._log()
        log.record(REPO, "0099_retired", "intent.md", "accepted")
        self.assertEqual(self.service.units_with_history(REPO)["units"], ["0099_retired"])

    def test_0093_cost_and_unit_cost_read_the_run_log(self):
        j = self.service._journal()
        key = self.service._journal_key(REPO)
        j.finished(key, "0001_a-problem", "spec", "done", cost_usd=2.0)
        j.finished(key, "0001_a-problem", "spec", "failed")
        j.finished(key, "0002_other", "plan", "done", cost_usd=20.0)
        found = self.service.cost(REPO, {"0001_a-problem": ["changes-requested"]})
        self.assertTrue(found["recording"])
        self.assertEqual([(r["key"], r["usd"], r["over"]) for r in found["by_unit"]],
                         [("0002_other", 20.0, True), ("0001_a-problem", 2.0, False)])
        self.assertEqual(found["waste"][-1]["count"], 1)
        mine = self.service.unit_cost(REPO, "0001_a-problem")
        self.assertEqual([(r["key"], r["steps"], r["unknown"]) for r in mine["by_stage"]], [("spec", 2, 1)])
        self.assertEqual([a["kind"] for a in mine["anomalies"]], ["failed"])

    def test_0093_cost_with_no_working_folder_says_it_is_not_recording(self):
        service = _service()
        self.assertFalse(service.cost(REPO)["recording"])
        self.assertEqual(service.unit_cost(REPO, "0001_a-problem"),
                         {"by_stage": [], "anomalies": [], "recording": False})

    def test_the_gate_applies_to_both_reads(self):
        for call in (
            lambda: self.service.unit_history("/etc", "0001_a-problem"),
            lambda: self.service.units_with_history("/etc"),
        ):
            with self.assertRaises(Invalid):
                call()

    def test_with_no_working_folder_it_says_it_is_not_recording(self):
        service = _service()
        found = service.unit_history(REPO, "0001_a-problem")
        self.assertFalse(found["recording"])
        self.assertEqual(found["transitions"], [])
        self.assertFalse(service.units_with_history(REPO)["recording"])

    def test_a_unit_holding_rows_from_two_state_sets_says_so(self):
        """`spec.md` C5: said out loud rather than refused, because refusing a read
        would hide the only evidence that the two sets were ever mixed."""
        import json

        from coscc import states
        from coscc.history import History

        other = self.root / "other.json"
        other.write_text(
            json.dumps(
                {
                    "name": "two-step",
                    "absent": "nowhere",
                    "settled": ["closed"],
                    "stages": [
                        {"name": "ticket", "artifact": "ticket.txt", "statuses": ["open", "closed"]}
                    ],
                }
            ),
            encoding="utf-8",
        )
        self._log().record(REPO, "0001_a-problem", "intent.md", "draft")
        History(self.work, self.root / "data", machine=states.load(other)).record(
            REPO, "0001_a-problem", "ticket.txt", "open"
        )

        found = self.service.unit_history(REPO, "0001_a-problem")
        self.assertEqual(found["written_under"], ["coscc-default", "two-step"])
        self.assertIn("two-step", found["mixed_state_sets"])

    def test_one_state_set_reports_no_mixture(self):
        self._log().record(REPO, "0001_a-problem", "intent.md", "draft")
        found = self.service.unit_history(REPO, "0001_a-problem")
        self.assertEqual(found["written_under"], ["coscc-default"])
        self.assertIsNone(found["mixed_state_sets"])


class StartingAUnitAndItsBranch(unittest.TestCase):
    """`0014` R1 and R4, through the one place logic lives."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        # Since `0001_product-describes-a-state-it-is-not-in` a branch is cut from what
        # `origin` has, so the repository needs one. A bare directory: no network.
        self.remote = self.root / "remote.git"
        subprocess.run(
            ["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True
        )
        self.repo = self.root / "work" / "proj"
        self.repo.mkdir(parents=True)
        self._git("init", "-q", "-b", "main")
        (self.repo / "README.md").write_text("x\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "first")
        self._git("remote", "add", "origin", str(self.remote))
        self._git("push", "-q", "origin", "main")
        self.service = Service(
            Config(
                workspaces=(str(self.repo),),
                working_dir=str(self.root / "work"),
                data_dir=str(self.root / "data"),
            ),
            Sessions(Config(workspaces=(str(self.repo),))),
        )

    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repo), "-c", "user.name=T",
             "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
            capture_output=True, text=True, check=True,
        ).stdout

    def test_a_new_unit_appears_on_the_board_it_was_created_for(self):
        made = create_sync(self.service,str(self.repo), "a-first-problem", "some words")
        board = asyncio.run(self.service.board(str(self.repo)))
        self.assertEqual([u["name"] for u in board["units"]], [made["unit"]])

    def test_nothing_of_it_lands_in_the_repository(self):
        # `0013`'s decision, enforced. R2, and the whole reason the store exists.
        create_sync(self.service,str(self.repo), "a-problem", "some words")
        self.assertEqual(self._git("status", "--porcelain"), "")
        self.assertFalse((self.repo / ".cos").exists())

    def test_a_unit_takes_no_number_the_host_repository_already_used(self):
        """`0001_product-describes-a-state-it-is-not-in` R10: `0015`, not `0001`."""
        for i in range(1, 15):
            (self.repo / ".cos" / f"{i:04d}_u{i}").mkdir(parents=True)
        before = sorted(p.name for p in (self.repo / ".cos").iterdir())
        made = create_sync(self.service,str(self.repo), "fresh", "some words")
        self.assertEqual(made["unit"], "0015_fresh")
        self.assertEqual(sorted(p.name for p in (self.repo / ".cos").iterdir()), before)

    def test_a_bad_slug_comes_back_as_a_refusal_not_an_exception(self):
        with self.assertRaises(Invalid) as caught:
            create_sync(self.service,str(self.repo), "Bad_Slug")
        self.assertIn("Bad_Slug", str(caught.exception))

    def test_the_gate_applies_to_creating_and_to_branching(self):
        with self.assertRaises(Invalid):
            create_sync(self.service,"/etc", "a-problem")
        with self.assertRaises(Invalid):
            asyncio.run(self.service.start_branch("/etc", "0001_a-problem"))

    def test_the_branch_is_refused_until_the_intent_says_what_type_this_is(self):
        made = create_sync(self.service,str(self.repo), "a-problem", "some words")
        with self.assertRaises(Invalid) as caught:
            asyncio.run(self.service.start_branch(str(self.repo), made["unit"]))
        self.assertIn("intent.md", str(caught.exception))

    def test_the_branch_name_is_the_one_the_intents_type_implies(self):
        made = create_sync(self.service,str(self.repo), "a-problem", "some words")
        directory = Path(made["path"])
        (directory / "intent.md").write_text(
            "# Intent: a problem\nAuthor: t. Type: fix. Status: accepted.\n", encoding="utf-8"
        )
        got = asyncio.run(self.service.start_branch(str(self.repo), made["unit"]))
        self.assertEqual(got["branch"], "fix/a-problem")
        # `0017`: cut in the unit's worktree; the workspace stays on `main` (R2).
        self.assertEqual(asyncio.run(self.service.branch_here(str(self.repo)))["branch"], "main")
        tree = Path(got["worktree"])
        self.assertEqual(asyncio.run(gitops.current_branch(tree)), "fix/a-problem")
        self.assertTrue(got["prepare"]["ok"])

    def test_two_units_each_get_their_own_tree_and_the_workspace_never_moves(self):
        """`0017` R1, R2."""
        a, b = self._typed_unit("a-problem"), self._typed_unit("b-problem")
        got_a = asyncio.run(self.service.start_branch(str(self.repo), a))
        got_b = asyncio.run(self.service.start_branch(str(self.repo), b))
        self.assertNotEqual(got_a["worktree"], got_b["worktree"])
        self.assertEqual(asyncio.run(gitops.current_branch(Path(got_a["worktree"]))), "fix/a-problem")
        self.assertEqual(asyncio.run(gitops.current_branch(Path(got_b["worktree"]))), "fix/b-problem")
        self.assertEqual(self._git("branch", "--show-current").strip(), "main")
        board = asyncio.run(self.service.board(str(self.repo)))
        trees = {u["name"]: u["worktree"] for u in board["units"]}
        self.assertEqual(trees[a]["path"], got_a["worktree"])
        self.assertEqual(trees[b]["branch"], "fix/b-problem")

    def test_units_created_together_take_different_numbers(self):
        """`0017` R8."""
        async def both():
            return await asyncio.gather(
                self.service.create_unit(str(self.repo), "one-problem", "w"),
                self.service.create_unit(str(self.repo), "two-problem", "w"),
            )
        made = asyncio.run(both())
        self.assertEqual(len({m["unit"][:4] for m in made}), 2)

    def test_cutting_the_same_branch_twice_is_refused_rather_than_rejoined(self):
        made = create_sync(self.service,str(self.repo), "a-problem", "some words")
        (Path(made["path"]) / "intent.md").write_text(
            "# Intent: a problem\nAuthor: t. Type: fix. Status: accepted.\n", encoding="utf-8"
        )
        asyncio.run(self.service.start_branch(str(self.repo), made["unit"]))
        self._git("switch", "-q", "main")
        with self.assertRaises(Invalid) as caught:
            asyncio.run(self.service.start_branch(str(self.repo), made["unit"]))
        self.assertIn("already exists", str(caught.exception))

    # --- `0001_product-describes-a-state-it-is-not-in` R6, R7, R8 ----------------

    def test_an_empty_board_counts_the_units_the_host_repository_holds(self):
        """R6: the board is empty, the host's `.cos/` is not, and both are named."""
        for i in range(1, 15):
            (self.repo / ".cos" / f"{i:04d}_u{i}").mkdir(parents=True)
        board = asyncio.run(self.service.board(str(self.repo)))
        self.assertEqual(board["units"], [])
        self.assertEqual(board["empty"]["host_units"], 14)
        self.assertEqual(board["empty"]["host"], str(self.repo.resolve()))
        self.assertEqual(
            board["empty"]["store"], str(units.root(str(self.repo), str(self.root / "data")))
        )

    def test_a_host_with_no_cos_directory_counts_zero(self):
        """R7: nothing to explain, so the old sentence stays."""
        board = asyncio.run(self.service.board(str(self.repo)))
        self.assertEqual(board["empty"]["host_units"], 0)

    def test_the_count_is_taken_again_on_every_read(self):
        """R8: no cache. One more directory, one more unit counted."""
        for i in range(1, 15):
            (self.repo / ".cos" / f"{i:04d}_u{i}").mkdir(parents=True)
        asyncio.run(self.service.board(str(self.repo)))
        (self.repo / ".cos" / "0015_extra").mkdir()
        board = asyncio.run(self.service.board(str(self.repo)))
        self.assertEqual(board["empty"]["host_units"], 15)

    def test_a_board_with_units_carries_no_empty_explanation(self):
        create_sync(self.service,str(self.repo), "a-problem", "some words")
        board = asyncio.run(self.service.board(str(self.repo)))
        self.assertNotIn("empty", board)

    # --- `0001_product-describes-a-state-it-is-not-in` R1, R2, R3 ----------------

    def _typed_unit(self, slug: str = "a-problem") -> str:
        made = create_sync(self.service,str(self.repo), slug, "some words")
        (Path(made["path"]) / "intent.md").write_text(
            f"# Intent: {slug}\nAuthor: t. Type: fix. Status: accepted.\n", encoding="utf-8"
        )
        return made["unit"]

    def _advance_remote(self, name: str = "g.txt", text: str = "from elsewhere\n") -> str:
        """Push one commit to `origin` from a second clone. Returns its SHA."""
        other = self.root / "other"
        if not other.exists():
            subprocess.run(["git", "clone", "-q", str(self.remote), str(other)], check=True)
        run = lambda *a: subprocess.run(  # noqa: E731
            ["git", "-C", str(other), "-c", "user.name=T", "-c", "user.email=t@example.invalid",
             "-c", "commit.gpgsign=false", *a],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        run("pull", "-q", "--ff-only")
        (other / name).write_text(text, encoding="utf-8")
        run("add", "-A")
        run("commit", "-q", "-m", f"elsewhere {name}")
        run("push", "-q", "origin", "main")
        return run("rev-parse", "HEAD")

    def test_the_branch_is_cut_from_the_remote_trunk_not_the_stale_local_one(self):
        """R1: local `main` one commit behind; the branch lands on the remote's commit."""
        ahead = self._advance_remote()
        local = self._git("rev-parse", "main").strip()
        self.assertNotEqual(local, ahead)
        unit = self._typed_unit()
        got = asyncio.run(self.service.start_branch(str(self.repo), unit))
        self.assertEqual(self._git("rev-parse", got["branch"]).strip(), ahead)
        # The local trunk was not moved to get there.
        self.assertEqual(self._git("rev-parse", "main").strip(), local)

    def test_the_result_names_the_ref_and_the_commit_it_was_cut_from(self):
        """R3."""
        self._advance_remote()
        got = asyncio.run(self.service.start_branch(str(self.repo), self._typed_unit()))
        self.assertEqual(got["base"], "origin/main")
        self.assertEqual(got["sha"], self._git("rev-parse", "--short=7", "origin/main").strip())

    def test_a_fetch_that_fails_cuts_nothing_and_says_so(self):
        """R2. And, because there is no remote to reach, spec OQ4 too."""
        unit = self._typed_unit()
        self._git("remote", "set-url", "origin", str(self.root / "gone.git"))
        before = self._git("rev-parse", "HEAD").strip()
        with self.assertRaises(Invalid) as caught:
            asyncio.run(self.service.start_branch(str(self.repo), unit))
        said = str(caught.exception)
        self.assertIn("origin", said)
        self.assertIn("no branch was cut", said)
        self.assertEqual(self._git("rev-parse", "HEAD").strip(), before)
        self.assertEqual(self._git("branch", "--list", "fix/a-problem").strip(), "")
        self.assertEqual(asyncio.run(self.service.branch_here(str(self.repo)))["branch"], "main")

    def test_a_repository_with_no_origin_is_refused_the_same_way(self):
        unit = self._typed_unit()
        self._git("remote", "remove", "origin")
        with self.assertRaises(Invalid) as caught:
            asyncio.run(self.service.start_branch(str(self.repo), unit))
        self.assertIn("no branch was cut", str(caught.exception))
        self.assertEqual(self._git("branch", "--list", "fix/a-problem").strip(), "")

    def test_a_dirty_tree_that_touches_nothing_the_remote_changed_still_cuts(self):
        """Plan Risk 5, first half: the fetch is not stopped by a dirty tree."""
        ahead = self._advance_remote("g.txt")
        (self.repo / "README.md").write_text("edited here\n", encoding="utf-8")
        got = asyncio.run(self.service.start_branch(str(self.repo), self._typed_unit()))
        self.assertEqual(self._git("rev-parse", got["branch"]).strip(), ahead)
        self.assertIn("README.md", self._git("status", "--porcelain"))

    def test_a_dirty_tree_that_touches_a_file_the_remote_changed_cuts_nothing(self):
        """Plan Risk 5, second half: `switch -c` refuses, and creates no branch.

        Since `0017` the tree that matters is the unit's own worktree; the workspace's
        dirt is no longer in the way of anything.
        """
        self._advance_remote("README.md", "changed elsewhere\n")
        unit = self._typed_unit()
        tree = worktrees.path(str(self.repo), unit, str(self.root / "data"))
        (tree / "README.md").write_text("edited here\n", encoding="utf-8")
        with self.assertRaises(Invalid):
            asyncio.run(self.service.start_branch(str(self.repo), unit))
        self.assertEqual(self._git("branch", "--list", "fix/a-problem").strip(), "")
        self.assertEqual((tree / "README.md").read_text(encoding="utf-8"), "edited here\n")


class TheBacklogIsDisplayOnly(unittest.TestCase):
    """`0074`. Three write paths, one paid proposal, one field on `start` — and the board's
    `next` and `blocked` exactly as they were (R15), with no file of the store touched (R16)."""

    GOOD = "```json\n" + json.dumps({"units": [
        {"unit": "0001_idea-only", "value": 4, "effort": "S", "similar": [], "basis": "bớt can thiệp tay: x",
         "relations": [{"type": "liên quan", "other": "0002_has-intent", "reason": "cùng màn"}]},
        {"unit": "0002_has-intent", "value": 2, "effort": "M", "similar": [], "basis": "cảm thấy vậy"},
    ]}) + "\n```"

    class Replies:
        def __init__(self, text, gate=None):
            self.text, self.gate, self.calls = text, gate, 0

        def in_flight(self):
            return []

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            self.calls += 1
            if self.gate is not None:
                await self.gate.wait()
            yield ("chunk", self.text)
            yield ("done", {"session_id": "sess-1", "cost": {"cost_usd": 0.12, "turns": 1}})

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "work" / "proj"
        self.repo.mkdir(parents=True)
        self.sessions = self.Replies(self.GOOD)
        self.service = Service(
            Config(workspaces=(str(self.repo),), working_dir=str(self.root / "work"),
                   data_dir=str(self.root / "data")),
            self.sessions,
        )
        self.cwd = str(self.repo)
        self.a = create_sync(self.service, self.cwd, "idea-only", "words")["unit"]
        made = create_sync(self.service, self.cwd, "has-intent", "words")
        self.b = made["unit"]
        (Path(made["path"]) / "intent.md").write_text(
            "# Intent: b\nAuthor: t. Type: feat. Status: accepted.\n\n## Problem\n\np\n", encoding="utf-8")
        done = Path(create_sync(self.service, self.cwd, "finished", "words")["path"])
        for name in ("intent", "spec", "plan"):
            status = "done" if name == "plan" else "accepted"
            (done / f"{name}.md").write_text(f"# {name}\nAuthor: t. Status: {status}.\n", encoding="utf-8")
        self.journal = self.service._journal()
        self.key = self.service._journal_key(self.cwd)

    def board(self):
        return asyncio.run(self.service.board(self.cwd))

    def store_hash(self):
        units_root = self.service._units_root(self.cwd)
        return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(units_root.rglob("*")) if p.is_file()}

    def test_r1_the_board_carries_the_backlog(self):
        data = self.board()
        self.assertEqual(data["backlog"]["backlog"], [self.a, self.b])
        self.assertEqual(data["backlog"]["shortlist"], [])
        self.assertTrue(data["backlog"]["propose_warning"])

    def test_r15_only_the_new_keys_differ_after_every_kind_of_record(self):
        def strip(data):
            data = json.loads(json.dumps(data))
            data.pop("backlog")
            for u in data["units"]:
                u.pop("backlog")
            return data

        before = self.board()
        asyncio.run(self.service.record_estimate(self.cwd, self.a, 4, "S", "vì", "Leif"))
        asyncio.run(self.service.record_relation(self.cwd, self.a, self.b, "phụ thuộc", "add", "r", "Leif"))
        asyncio.run(self.service.record_shortlist(self.cwd, [self.a], "r", "Leif"))
        after = self.board()
        self.assertEqual(strip(before), strip(after))
        self.assertEqual([(u["next"], u["blocked"]) for u in before["units"]],
                         [(u["next"], u["blocked"]) for u in after["units"]])
        self.assertEqual(after["backlog"]["shortlist"][0]["unit"], self.a)
        self.assertEqual(next(u for u in after["units"] if u["name"] == self.a)["backlog"]["rank"], 1)

    def test_r16_three_writes_touch_no_file_and_refusals_insert_nothing(self):
        before = self.store_hash()
        refusals = [
            lambda: self.service.record_estimate(self.cwd, self.a, 9, "S", "x", "Leif"),
            lambda: self.service.record_estimate(self.cwd, self.a, 3, "S", "x", "agent:me"),
            lambda: self.service.record_estimate(self.cwd, "0003_finished", 3, "S", "x", "Leif"),
            lambda: self.service.record_relation(self.cwd, self.a, self.a, "trùng", "add", "r", "Leif"),
            lambda: self.service.record_shortlist(self.cwd, [self.a], "r", "Leif"),  # no estimate yet
        ]
        for call in refusals:
            with self.assertRaises(Invalid):
                asyncio.run(call())
        self.assertEqual(self.journal.records(self.key, kinds=("estimate-value", "relation", "shortlist")), [])
        asyncio.run(self.service.record_estimate(self.cwd, self.a, "4", "S", "vì", "Leif"))
        asyncio.run(self.service.record_relation(self.cwd, self.a, self.b, "trùng", "add", "r", "Leif"))
        asyncio.run(self.service.record_shortlist(self.cwd, [self.a], "r", "Leif"))
        self.assertEqual(self.store_hash(), before)
        self.assertEqual(self.board()["backlog"]["shortlist"][0]["estimate"]["value"], 4)

    def _start_of_spec(self):
        async def go():
            async for _ in self.service.run_step(self.cwd, self.b, "spec"):
                pass

        try:
            asyncio.run(go())
        except Invalid:
            pass
        return self.journal.records(self.key, self.b, kind="start")[-1]

    def test_r14_the_start_record_says_where_the_unit_stood(self):
        first = self._start_of_spec()
        self.assertEqual(first["shortlist"], {"rank": None, "of": None, "record": None})
        asyncio.run(self.service.record_estimate(self.cwd, self.b, 3, "M", "vì", "Leif"))
        asyncio.run(self.service.record_shortlist(self.cwd, [self.b], "r", "Leif"))
        second = self._start_of_spec()
        self.assertEqual((second["shortlist"]["rank"], second["shortlist"]["of"]), (1, 1))
        self.assertEqual(second["shortlist"]["record"]["n"], 1)

    def test_r14_a_busy_run_log_still_starts_the_step(self):
        from coscc.journal import Busy, Journal

        real = Journal.records

        def busy_on_shortlist(self_, *a, **kw):
            if kw.get("kind") == "shortlist":
                raise Busy("locked")
            return real(self_, *a, **kw)

        with mock.patch.object(Journal, "records", busy_on_shortlist):
            start = self._start_of_spec()
        self.assertIsNone(start["shortlist"]["rank"])
        self.assertIn("locked", start["shortlist"]["error"])

    def _propose(self):
        async def go():
            out = []
            async for item in self.service.propose_estimates(self.cwd):
                out.append(item)
            return out

        return asyncio.run(go())

    def test_r18_a_basis_with_no_goal_drops_only_that_unit(self):
        done = self._propose()[-1][1]["estimate"]
        self.assertEqual(done["outcome"], "done")
        self.assertEqual(done["written"], 2)  # one estimate, one relation
        self.assertEqual([r["unit"] for r in done["rejected"]], [self.b])
        values = self.journal.records(self.key, kind="estimate-value")
        self.assertEqual([(v["unit"], v["by"]) for v in values], [(self.a, "agent:sess-1")])
        ends = [r for r in self.journal.records(self.key, kind="end") if r.get("stage") == "estimate"]
        self.assertEqual((ends[-1]["outcome"], ends[-1]["cost_usd"]), ("done", 0.12))

    def test_r18_a_reply_that_is_not_json_writes_no_estimate(self):
        self.sessions.text = "value 4, I think"
        done = self._propose()[-1][1]["estimate"]
        self.assertEqual(done["outcome"], "failed")
        self.assertIn("not JSON", done["detail"])
        self.assertEqual(self.journal.records(self.key, kind="estimate-value"), [])

    def test_r18_a_second_press_while_one_runs_is_refused_and_spends_nothing(self):
        async def go():
            gate = asyncio.Event()
            self.sessions.gate = gate
            first = self.service.propose_estimates(self.cwd)
            task = asyncio.create_task(first.__anext__())
            for _ in range(200):
                await asyncio.sleep(0.01)
                if self.sessions.calls:
                    break
            jobs = self.service._update_jobs()
            with self.assertRaises(Invalid):
                await self.service.propose_estimates(self.cwd).__anext__()
            gate.set()
            await task
            async for _ in first:
                pass
            return jobs

        jobs = asyncio.run(go())
        self.assertEqual(self.sessions.calls, 1)
        self.assertEqual([(j["kind"], j["stage"]) for j in jobs], [("integration", "estimate")])
        self.assertEqual(self.service._active, {})


class JeraAnswersFromPrecedent(unittest.TestCase):
    """`0044`. `Service.precedent` and the write path it shares with a person's answer."""

    EARLIER = (
        "# Spec: e\nIntent: intent.md. Author: t. Status: accepted.\n\n## Open questions\n\n1. Nhánh?\n\n"
        "## Answers\n\n### Câu 1\nAnswered by: owner. Date: 2026-09-01. Via: product.\n\nTừ Type.\n"
    )
    ASKED = (
        "# Spec: a\nIntent: intent.md. Author: t. Status: draft.\n\n## Open questions\n\n1. Nhánh mới?\n2. Tiền?\n"
    )

    class _Sessions:
        def __init__(self) -> None:
            self.text, self.gate, self.calls = "", None, 0

        def in_flight(self):
            return []

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            self.calls += 1
            self.kw = kw
            if self.gate is not None:
                await self.gate.wait()
            yield ("chunk", self.text)
            yield ("done", {"session_id": "s1", "cost": {"cost_usd": 0.02, "turns": 1}})

    def setUp(self):
        from coscc.journal import Journal

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        (root / "work" / "proj").mkdir(parents=True)
        self.cwd = str(root / "work" / "proj")
        config = Config(workspaces=(self.cwd,), working_dir=str(root / "work"), data_dir=str(root / "data"))
        self.sessions = self._Sessions()
        self.service = Service(config, self.sessions)
        intent = "# Intent: x\nAuthor: t. Type: feat. Status: accepted.\n\n## Problem\n\np\n"
        self.earlier = self._make("earlier", intent=intent, spec=self.EARLIER)
        self.asked = self._make("asked", intent=intent, spec=self.ASKED)
        self.spec = self.service._unit_dir(self.cwd, self.asked) / "spec.md"
        self.cite = f"{self.earlier}/spec.md#Câu 1"
        self.journal = Journal(root / "work", root / "data")
        self.key = self.service._journal_key(self.cwd)

    def _make(self, slug: str, **files: str) -> str:
        made = create_sync(self.service, self.cwd, slug, "x")
        for name, text in files.items():
            (Path(made["path"]) / f"{name}.md").write_text(text, encoding="utf-8")
        return made["unit"]

    def reply(self, *items: dict) -> None:
        self.sessions.text = "```json\n" + json.dumps(list(items), ensure_ascii=False) + "\n```"

    def item(self, n: int, **over) -> dict:
        return {"artifact": "spec.md", "n": n, "verdict": "answer", "category": "other", "text": f"Trả lời {n}.",
                "reason": "", "cites": [self.cite], **over}

    def ask(self) -> dict:
        return asyncio.run(self.service.precedent(self.cwd, self.asked))

    def rows(self, kind: str) -> list[dict]:
        return self.journal.records(self.key, self.asked, kind=kind)

    def board_questions(self) -> list[dict]:
        board = asyncio.run(self.service.board(self.cwd))
        return next(u for u in board["units"] if u["name"] == self.asked)["questions"]

    def test_r1_nothing_but_the_route_and_the_page_calls_it(self):
        """R1. Structural: no read, no timer, no step and no answer starts Jera."""
        callers = []
        for path in sorted(Path(REPO, "coscc").glob("*.py")):
            if path.name.endswith("_test.py"):
                continue
            if ".precedent(" in path.read_text(encoding="utf-8"):
                callers.append(path.name)
        self.assertEqual(callers, ["api.py", "state.py"])

    def test_r2_a_unit_with_no_open_question_is_refused_before_a_session(self):
        self.reply(self.item(1), self.item(2))
        self.ask()
        with self.assertRaises(Invalid):
            self.ask()
        self.assertEqual(self.sessions.calls, 1)
        with self.assertRaises(Invalid):
            asyncio.run(self.service.precedent(self.cwd, self.earlier))
        self.assertEqual(self.sessions.calls, 1)

    def test_r7_r12_answers_are_appended_as_jera_and_recorded(self):
        before = self.spec.read_bytes()
        self.reply(self.item(1), self.item(2, verdict="needs-person", category="significant-spend",
                                           reason="tốn tiền"))
        done = self.ask()
        after = self.spec.read_bytes()
        self.assertTrue(after.startswith(before))
        tail = after[len(before):].decode("utf-8")
        self.assertEqual(tail.count("### Câu"), 1)
        self.assertIn(f"Answered by: Jera. Date: {date.today().isoformat()}. Via: precedent.", tail)
        self.assertIn(f"Tiền lệ: {self.cite}", tail)
        self.assertEqual((done["written"], done["needs_person"]),
                         ([{"artifact": "spec.md", "n": 1}], [{"artifact": "spec.md", "n": 2}]))
        verdicts = {r["n"]: r for r in self.rows("precedent")}
        self.assertEqual((verdicts[1]["verdict"], verdicts[1]["written"]), ("answer", True))
        self.assertEqual((verdicts[2]["verdict"], verdicts[2]["reason"]), ("needs-person", "tốn tiền"))
        self.assertEqual([r["stage"] for r in self.rows("start")], ["precedent"])
        self.assertEqual([(r["stage"], r["outcome"]) for r in self.rows("end")], [("precedent", "done")])
        self.assertEqual(self.sessions.kw["tools"], [])

    def test_r10_the_board_says_which_answer_is_jeras_and_which_needs_a_person(self):
        self.reply(self.item(1), self.item(2, verdict="needs-person", category="business-tradeoff",
                                           reason="đánh đổi", text="Đề xuất: không."))
        self.ask()
        q1, q2 = self.board_questions()
        self.assertEqual((q1["answered"], q1["by_jera"], q1["cites"], q1["said"], q1["needs_person"]),
                         (True, True, [self.cite], "Trả lời 1.", False))
        self.assertEqual((q2["answered"], q2["by_jera"], q2["needs_person"], q2["proposal"], q2["reason"]),
                         (False, False, True, "Đề xuất: không.", "đánh đổi"))
        # A person answering after Jera puts their block in force; nothing is rewritten.
        asyncio.run(self.service.answer(self.cwd, self.asked, "spec.md", 1, "Của tôi.", ""))
        q1, q2 = self.board_questions()
        self.assertEqual((q1["by"], q1["by_jera"], q1["cites"], q1["said"]), ("owner", False, [], ""))

    def test_r5_an_unknown_citation_writes_nothing(self):
        before = self.spec.read_bytes()
        self.reply(self.item(1, cites=["9999_x/spec.md#Câu 1"]), self.item(2, cites=[]))
        self.ask()
        self.assertEqual(self.spec.read_bytes(), before)
        self.assertEqual([r["verdict"] for r in self.rows("precedent")], ["needs-person", "needs-person"])

    def test_r3_review_and_findings_are_refused_by_name_and_shape(self):
        (self.service._unit_dir(self.cwd, self.asked) / "review.md").write_text("# R\nStatus: draft.\n")
        done = asyncio.run(self.service._append_answers(
            self.cwd, self.asked, [("review.md", "F1", "x"), ("spec.md", "F1", "x"), ("review.md", 1, "x")],
            "Jera", "precedent", "agent:Jera", "precedent"))
        self.assertEqual(done["written"], [])
        self.assertEqual(len(done["skipped"]), 3)
        self.assertTrue(all("review.md or a finding" in s["reason"] for s in done["skipped"]))

    def test_r7_one_refused_item_does_not_stop_the_others(self):
        done = asyncio.run(self.service._append_answers(
            self.cwd, self.asked, [("spec.md", 9, "x"), ("spec.md", 1, "x")],
            "Jera", "precedent", "agent:Jera", "precedent"))
        self.assertEqual(done["written"], [{"artifact": "spec.md", "question": 1}])
        self.assertIn("no question 9", done["skipped"][0]["reason"])

    def test_r9_needs_person_changes_no_byte_and_no_answer(self):
        before_q, before = self.board_questions(), self.spec.read_bytes()
        self.reply(self.item(1, verdict="needs-person", category="product-direction"),
                   self.item(2, verdict="needs-person", category="external-action"))
        self.ask()
        self.assertEqual(self.spec.read_bytes(), before)
        self.assertEqual([q["answered"] for q in self.board_questions()], [q["answered"] for q in before_q])

    def test_r11_a_person_cannot_answer_as_jera(self):
        for name in ("Jera", " jera ", "JERA"):
            with self.assertRaises(Invalid):
                asyncio.run(self.service.answer(self.cwd, self.asked, "spec.md", 1, "x", name))
        self.assertNotIn(b"### C", self.spec.read_bytes().split(b"## Open questions")[1])

    def test_r13_a_reply_that_is_not_json_writes_nothing_and_keeps_its_tail(self):
        before = self.spec.read_bytes()
        self.sessions.text = "no json at all, just words"
        done = self.ask()
        self.assertEqual((done["outcome"], self.spec.read_bytes()), ("failed", before))
        [end] = self.rows("end")
        self.assertEqual(end["outcome"], "failed")
        self.assertIn("just words", end["detail"])
        self.assertEqual(self.rows("precedent"), [])

    def test_a_second_press_while_jera_runs_is_refused_with_what_holds_the_unit(self):
        async def go():
            self.sessions.gate = asyncio.Event()
            self.reply(self.item(1), self.item(2))
            task = asyncio.create_task(self.service.precedent(self.cwd, self.asked))
            for _ in range(200):
                await asyncio.sleep(0.01)
                if self.sessions.calls:
                    break
            jobs = self.service._update_jobs()
            running = self.service.running(self.cwd)["running"]
            with self.assertRaises(Invalid) as refused:
                await self.service.precedent(self.cwd, self.asked)
            self.sessions.gate.set()
            await task
            return jobs, running, str(refused.exception)

        jobs, running, said = asyncio.run(go())
        self.assertIn("Jera is answering its questions", said)
        self.assertEqual([(j["stage"], j["unit"]) for j in jobs], [("precedent", self.asked)])
        self.assertEqual(running[self.asked][0]["agent"]["name"], "Jera")
        self.assertEqual((self.sessions.calls, self.service._active), (1, {}))
