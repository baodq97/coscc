"""Tests for `Backlog` in `coscc/service/backlog.py`, split from
`tests/service/test_service.py`."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.bus import Bus
from coscc import units
from coscc.git import gitops
from coscc.units import worktrees
from coscc.config import Config
from coscc.service.common import Invalid
from coscc.service import Service
from coscc.agent.sessions import Sessions
from tests.service.test_service import REPO, _service, create_sync
from tests.units.test_submit import submits as _submits


class TheUnitHistoryReadPath(unittest.TestCase):
    """The log read through the one place logic lives.

    Written against a temporary working folder and data root rather than this repository's
    real `~/.cos` — `coscc/runlog/journal.py:108-109` names that hazard and the two new tables
    inherit it unchanged."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.work = self.root / "work"
        self.work.mkdir()
        self.service = _service(working_dir=str(self.work), data_dir=str(self.root / "data"))

    def _log(self):
        from coscc.units.history import History

        return History(self.work, self.root / "data")

    def test_a_unit_with_no_history_answers_with_empty_rather_than_refusing(self):
        found = self.service.backlog.unit_history(REPO, "0001_a-problem")
        self.assertTrue(found["recording"])
        self.assertEqual(found["transitions"], [])
        self.assertEqual(found["settled_edits"], 0)

    def test_the_state_it_returns_is_a_projection_of_the_rows_it_returns(self):
        log = self._log()
        log.record(REPO, "0001_a-problem", "intent.md", "draft")
        log.record(REPO, "0001_a-problem", "intent.md", "accepted")
        found = self.service.backlog.unit_history(REPO, "0001_a-problem")
        # Not two sources: the last transition's destination *is* the state.
        self.assertEqual(found["state"]["intent.md"], found["transitions"][-1]["to_state"])
        self.assertEqual(found["state"]["intent.md"], "accepted")

    def test_the_timeline_carries_each_transition_with_its_guards_label(self):
        log = self._log()
        log.record(REPO, "0001_a-problem", "intent.md", "draft")
        log.record(
            REPO,
            "0001_a-problem",
            "ship.md",
            "accepted",
            guard="merge-read",
            authority="code",
            run="r-1",
            inputs={"merge_commit": "f" * 40, "number": 7},
        )
        old, merged = self.service.backlog.timeline(REPO, "0001_a-problem")["transitions"]
        self.assertEqual((old["guard"], old["guard_label"], old["head"]), ("unknown", "", ""))
        self.assertEqual(
            (
                merged["guard_label"],
                merged["authority"],
                merged["run"],
                merged["head"],
                merged["inputs"]["number"],
            ),
            (
                "A merge is recorded only from a read that names its merge commit.",
                "code",
                "r-1",
                "f" * 40,
                7,
            ),
        )

    def test_it_counts_the_edits_after_settling_that_exists_to_count(self):
        log = self._log()
        log.record(REPO, "0001_a-problem", "intent.md", "draft")
        log.record(REPO, "0001_a-problem", "intent.md", "accepted")
        log.record(REPO, "0001_a-problem", "intent.md", "accepted", source="commit:abc")
        self.assertEqual(
            self.service.backlog.unit_history(REPO, "0001_a-problem")["settled_edits"], 1
        )

    def test_a_unit_retired_from_the_working_tree_still_has_a_history(self):
        log = self._log()
        log.record(REPO, "0099_retired", "intent.md", "accepted")
        self.assertEqual(self.service.backlog.units_with_history(REPO)["units"], ["0099_retired"])

    def test_cost_and_unit_cost_read_the_run_log(self):
        j = self.service.ws.journal()
        key = self.service.ws.key(REPO)
        j.finished(key, "0001_a-problem", "spec", "done", cost_usd=2.0)
        j.finished(key, "0001_a-problem", "spec", "failed")
        j.finished(key, "0002_other", "plan", "done", cost_usd=20.0)
        found = self.service.activity.cost(REPO, {"0001_a-problem": ["changes-requested"]})
        self.assertTrue(found["recording"])
        self.assertEqual(
            [(r["key"], r["usd"], r["over"]) for r in found["by_unit"]],
            [("0002_other", 20.0, True), ("0001_a-problem", 2.0, False)],
        )
        self.assertEqual(found["waste"][-1]["count"], 1)
        mine = self.service.activity.unit_cost(REPO, "0001_a-problem")
        self.assertEqual(
            [(r["key"], r["steps"], r["unknown"]) for r in mine["by_stage"]], [("spec", 2, 1)]
        )
        self.assertEqual([a["kind"] for a in mine["anomalies"]], ["failed"])

    def test_cost_with_no_working_folder_says_it_is_not_recording(self):
        service = _service()
        self.assertFalse(service.activity.cost(REPO)["recording"])
        self.assertEqual(
            service.activity.unit_cost(REPO, "0001_a-problem"),
            {"by_stage": [], "anomalies": [], "recording": False},
        )

    def test_the_gate_applies_to_both_reads(self):
        for call in (
            lambda: self.service.backlog.unit_history("/etc", "0001_a-problem"),
            lambda: self.service.backlog.units_with_history("/etc"),
        ):
            with self.assertRaises(Invalid):
                call()

    def test_with_no_working_folder_it_says_it_is_not_recording(self):
        service = _service()
        found = service.backlog.unit_history(REPO, "0001_a-problem")
        self.assertFalse(found["recording"])
        self.assertEqual(found["transitions"], [])
        self.assertFalse(service.backlog.units_with_history(REPO)["recording"])

    def test_a_unit_holding_rows_from_two_state_sets_says_so(self):
        """Said out loud rather than refused, because refusing a read would hide the only evidence
        that the two sets were ever mixed."""
        import json

        from coscc.units import states
        from coscc.units.history import History

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

        found = self.service.backlog.unit_history(REPO, "0001_a-problem")
        self.assertEqual(found["written_under"], ["coscc-default", "two-step"])
        self.assertIn("two-step", found["mixed_state_sets"])

    def test_one_state_set_reports_no_mixture(self):
        self._log().record(REPO, "0001_a-problem", "intent.md", "draft")
        found = self.service.backlog.unit_history(REPO, "0001_a-problem")
        self.assertEqual(found["written_under"], ["coscc-default"])
        self.assertIsNone(found["mixed_state_sets"])


class StartingAUnitAndItsBranch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        # A bare directory: no network.
        self.remote = self.root / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
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

    def test_a_new_unit_appears_on_the_board_it_was_created_for(self):
        made = create_sync(self.service, str(self.repo), "a-first-problem", "some words")
        board = asyncio.run(self.service.board(str(self.repo)))
        self.assertEqual([u["name"] for u in board["units"]], [made["unit"]])

    def test_nothing_of_it_lands_in_the_repository(self):
        create_sync(self.service, str(self.repo), "a-problem", "some words")
        self.assertEqual(self._git("status", "--porcelain"), "")
        self.assertFalse((self.repo / ".cos").exists())

    def test_a_unit_takes_no_number_the_host_repository_already_used(self):
        for i in range(1, 15):
            (self.repo / ".cos" / f"{i:04d}_u{i}").mkdir(parents=True)
        before = sorted(p.name for p in (self.repo / ".cos").iterdir())
        made = create_sync(self.service, str(self.repo), "fresh", "some words")
        self.assertEqual(made["unit"], "0015_fresh")
        self.assertEqual(sorted(p.name for p in (self.repo / ".cos").iterdir()), before)

    def test_a_bad_slug_comes_back_as_a_refusal_not_an_exception(self):
        with self.assertRaises(Invalid) as caught:
            create_sync(self.service, str(self.repo), "Bad_Slug")
        self.assertIn("Bad_Slug", str(caught.exception))

    def test_the_gate_applies_to_creating_and_to_branching(self):
        with self.assertRaises(Invalid):
            create_sync(self.service, "/etc", "a-problem")
        with self.assertRaises(Invalid):
            asyncio.run(self.service.backlog.start_branch("/etc", "0001_a-problem"))

    def test_the_branch_is_refused_until_the_intent_says_what_type_this_is(self):
        made = create_sync(self.service, str(self.repo), "a-problem", "some words")
        with self.assertRaises(Invalid) as caught:
            asyncio.run(self.service.backlog.start_branch(str(self.repo), made["unit"]))
        self.assertIn("intent.md", str(caught.exception))

    def test_the_branch_name_is_the_one_the_intents_type_implies(self):
        made = create_sync(self.service, str(self.repo), "a-problem", "some words")
        directory = Path(made["path"])
        (directory / "intent.md").write_text(
            "# Intent: a problem\nAuthor: t. Type: fix. Status: accepted.\n", encoding="utf-8"
        )
        got = asyncio.run(self.service.backlog.start_branch(str(self.repo), made["unit"]))
        self.assertEqual(got["branch"], "fix/a-problem")
        # Cut in the unit's worktree; the workspace stays on `main`.
        self.assertEqual(
            asyncio.run(self.service.backlog.branch_here(str(self.repo)))["branch"], "main"
        )
        tree = Path(got["worktree"])
        self.assertEqual(asyncio.run(gitops.current_branch(tree)), "fix/a-problem")
        self.assertTrue(got["prepare"]["ok"])

    def test_two_units_each_get_their_own_tree_and_the_workspace_never_moves(self):
        a, b = self._typed_unit("a-problem"), self._typed_unit("b-problem")
        got_a = asyncio.run(self.service.backlog.start_branch(str(self.repo), a))
        got_b = asyncio.run(self.service.backlog.start_branch(str(self.repo), b))
        self.assertNotEqual(got_a["worktree"], got_b["worktree"])
        self.assertEqual(
            asyncio.run(gitops.current_branch(Path(got_a["worktree"]))), "fix/a-problem"
        )
        self.assertEqual(
            asyncio.run(gitops.current_branch(Path(got_b["worktree"]))), "fix/b-problem"
        )
        self.assertEqual(self._git("branch", "--show-current").strip(), "main")
        board = asyncio.run(self.service.board(str(self.repo)))
        trees = {u["name"]: u["worktree"] for u in board["units"]}
        self.assertEqual(trees[a]["path"], got_a["worktree"])
        self.assertEqual(trees[b]["branch"], "fix/b-problem")

    def test_units_created_together_take_different_numbers(self):

        async def both():
            return await asyncio.gather(
                self.service.answers.create_unit(str(self.repo), "one-problem", "w"),
                self.service.answers.create_unit(str(self.repo), "two-problem", "w"),
            )

        made = asyncio.run(both())
        self.assertEqual(len({m["unit"][:4] for m in made}), 2)

    def test_cutting_the_same_branch_twice_is_refused_rather_than_rejoined(self):
        made = create_sync(self.service, str(self.repo), "a-problem", "some words")
        (Path(made["path"]) / "intent.md").write_text(
            "# Intent: a problem\nAuthor: t. Type: fix. Status: accepted.\n", encoding="utf-8"
        )
        asyncio.run(self.service.backlog.start_branch(str(self.repo), made["unit"]))
        self._git("switch", "-q", "main")
        with self.assertRaises(Invalid) as caught:
            asyncio.run(self.service.backlog.start_branch(str(self.repo), made["unit"]))
        self.assertIn("already exists", str(caught.exception))

    def test_an_empty_board_counts_the_units_the_host_repository_holds(self):
        """The board is empty, the host's `.cos/` is not, and both are named."""
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
        """Nothing to explain, so the old sentence stays."""
        board = asyncio.run(self.service.board(str(self.repo)))
        self.assertEqual(board["empty"]["host_units"], 0)

    def test_the_count_is_taken_again_on_every_read(self):
        """No cache. One more directory, one more unit counted."""
        for i in range(1, 15):
            (self.repo / ".cos" / f"{i:04d}_u{i}").mkdir(parents=True)
        asyncio.run(self.service.board(str(self.repo)))
        (self.repo / ".cos" / "0015_extra").mkdir()
        board = asyncio.run(self.service.board(str(self.repo)))
        self.assertEqual(board["empty"]["host_units"], 15)

    def test_a_board_with_units_carries_no_empty_explanation(self):
        create_sync(self.service, str(self.repo), "a-problem", "some words")
        board = asyncio.run(self.service.board(str(self.repo)))
        self.assertNotIn("empty", board)

    def _typed_unit(self, slug: str = "a-problem") -> str:
        made = create_sync(self.service, str(self.repo), slug, "some words")
        (Path(made["path"]) / "intent.md").write_text(
            f"# Intent: {slug}\nAuthor: t. Type: fix. Status: accepted.\n", encoding="utf-8"
        )
        return made["unit"]

    def _advance_remote(self, name: str = "g.txt", text: str = "from elsewhere\n") -> str:
        """Push one commit to `origin` from a second clone. Returns its SHA."""
        other = self.root / "other"
        if not other.exists():
            subprocess.run(["git", "clone", "-q", str(self.remote), str(other)], check=True)
        run = lambda *a: subprocess.run(
            [
                "git",
                "-C",
                str(other),
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@example.invalid",
                "-c",
                "commit.gpgsign=false",
                *a,
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        run("pull", "-q", "--ff-only")
        (other / name).write_text(text, encoding="utf-8")
        run("add", "-A")
        run("commit", "-q", "-m", f"elsewhere {name}")
        run("push", "-q", "origin", "main")
        return run("rev-parse", "HEAD")

    def test_the_branch_is_cut_from_the_remote_trunk_not_the_stale_local_one(self):
        """Local `main` one commit behind; the branch lands on the remote's commit."""
        ahead = self._advance_remote()
        local = self._git("rev-parse", "main").strip()
        self.assertNotEqual(local, ahead)
        unit = self._typed_unit()
        got = asyncio.run(self.service.backlog.start_branch(str(self.repo), unit))
        self.assertEqual(self._git("rev-parse", got["branch"]).strip(), ahead)
        # The local trunk was not moved to get there.
        self.assertEqual(self._git("rev-parse", "main").strip(), local)

    def test_the_result_names_the_ref_and_the_commit_it_was_cut_from(self):
        self._advance_remote()
        got = asyncio.run(self.service.backlog.start_branch(str(self.repo), self._typed_unit()))
        self.assertEqual(got["base"], "origin/main")
        self.assertEqual(got["sha"], self._git("rev-parse", "--short=7", "origin/main").strip())

    def test_a_fetch_that_fails_cuts_nothing_and_says_so(self):
        """And, because there is no remote to reach, spec OQ4 too."""
        unit = self._typed_unit()
        self._git("remote", "set-url", "origin", str(self.root / "gone.git"))
        before = self._git("rev-parse", "HEAD").strip()
        with self.assertRaises(Invalid) as caught:
            asyncio.run(self.service.backlog.start_branch(str(self.repo), unit))
        said = str(caught.exception)
        self.assertIn("origin", said)
        self.assertIn("no branch was cut", said)
        self.assertEqual(self._git("rev-parse", "HEAD").strip(), before)
        self.assertEqual(self._git("branch", "--list", "fix/a-problem").strip(), "")
        self.assertEqual(
            asyncio.run(self.service.backlog.branch_here(str(self.repo)))["branch"], "main"
        )

    def test_a_repository_with_no_origin_is_refused_the_same_way(self):
        unit = self._typed_unit()
        self._git("remote", "remove", "origin")
        with self.assertRaises(Invalid) as caught:
            asyncio.run(self.service.backlog.start_branch(str(self.repo), unit))
        self.assertIn("no branch was cut", str(caught.exception))
        self.assertEqual(self._git("branch", "--list", "fix/a-problem").strip(), "")

    def test_a_dirty_tree_that_touches_nothing_the_remote_changed_still_cuts(self):
        """Plan Risk 5, first half: the fetch is not stopped by a dirty tree."""
        ahead = self._advance_remote("g.txt")
        (self.repo / "README.md").write_text("edited here\n", encoding="utf-8")
        got = asyncio.run(self.service.backlog.start_branch(str(self.repo), self._typed_unit()))
        self.assertEqual(self._git("rev-parse", got["branch"]).strip(), ahead)
        self.assertIn("README.md", self._git("status", "--porcelain"))

    def test_a_dirty_tree_that_touches_a_file_the_remote_changed_cuts_nothing(self):
        """`switch -c` refuses, and creates no branch.

        The tree that matters is the unit's own worktree; the workspace's dirt is in the way of
        nothing."""
        self._advance_remote("README.md", "changed elsewhere\n")
        unit = self._typed_unit()
        tree = worktrees.path(str(self.repo), unit, str(self.root / "data"))
        (tree / "README.md").write_text("edited here\n", encoding="utf-8")
        with self.assertRaises(Invalid):
            asyncio.run(self.service.backlog.start_branch(str(self.repo), unit))
        self.assertEqual(self._git("branch", "--list", "fix/a-problem").strip(), "")
        self.assertEqual((tree / "README.md").read_text(encoding="utf-8"), "edited here\n")


class TheBacklogIsDisplayOnly(unittest.TestCase):
    """Three write paths, one paid proposal, one field on `start` — and the board's `next` and
    `blocked` exactly as they were, with no file of the store touched."""

    # The object the session hands back through `submit`.
    GOOD = {
        "units": [
            {
                "unit": "0001_idea-only",
                "value": 4,
                "effort": "S",
                "similar": [],
                "basis": "bớt can thiệp tay: x",
                "relations": [
                    {"type": "liên quan", "other": "0002_has-intent", "reason": "cùng màn"}
                ],
            },
            {
                "unit": "0002_has-intent",
                "value": 2,
                "effort": "M",
                "similar": [],
                "basis": "cảm thấy vậy",
                "relations": [],
            },
        ]
    }

    class Replies:
        bus = Bus()

        def __init__(self, obj, gate=None):
            self.obj, self.gate, self.calls = obj, gate, 0
            self.text = "Here is my estimate."

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            self.calls += 1
            if self.gate is not None:
                await self.gate.wait()
            yield ("chunk", self.text)
            if self.obj is not None:
                await _submits(kw, **self.obj)
            yield ("done", {"session_id": "sess-1", "cost": {"cost_usd": 0.12, "turns": 1}})

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "work" / "proj"
        self.repo.mkdir(parents=True)
        self.sessions = self.Replies(self.GOOD)
        self.service = Service(
            Config(
                workspaces=(str(self.repo),),
                working_dir=str(self.root / "work"),
                data_dir=str(self.root / "data"),
            ),
            self.sessions,
        )
        self.cwd = str(self.repo)
        self.a = create_sync(self.service, self.cwd, "idea-only", "words")["unit"]
        made = create_sync(self.service, self.cwd, "has-intent", "words")
        self.b = made["unit"]
        (Path(made["path"]) / "intent.md").write_text(
            "# Intent: b\nAuthor: t. Type: feat. Status: accepted.\n\n## Problem\n\np\n",
            encoding="utf-8",
        )
        done = Path(create_sync(self.service, self.cwd, "finished", "words")["path"])
        for name in ("intent", "spec", "plan"):
            status = "done" if name == "plan" else "accepted"
            (done / f"{name}.md").write_text(
                f"# {name}\nAuthor: t. Status: {status}.\n", encoding="utf-8"
            )
        self.journal = self.service.ws.journal()
        self.key = self.service.ws.key(self.cwd)

    def board(self):
        return asyncio.run(self.service.board(self.cwd))

    def store_hash(self):
        units_root = self.service.ws.units_root(self.cwd)
        return {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(units_root.rglob("*"))
            if p.is_file()
        }

    def test_the_board_carries_the_backlog(self):
        data = self.board()
        self.assertEqual(data["backlog"]["backlog"], [self.a, self.b])
        self.assertEqual(data["backlog"]["shortlist"], [])
        self.assertTrue(data["backlog"]["propose_warning"])

    def test_only_the_new_keys_differ_after_every_kind_of_record(self):
        def strip(data):
            data = json.loads(json.dumps(data))
            data.pop("backlog")
            # When the read was made, not what it found.
            data.pop("read_at")
            for u in data["units"]:
                u.pop("backlog")
            return data

        before = self.board()
        asyncio.run(self.service.backlog.record_estimate(self.cwd, self.a, 4, "S", "vì", "Leif"))
        asyncio.run(
            self.service.backlog.record_relation(
                self.cwd, self.a, self.b, "phụ thuộc", "add", "r", "Leif"
            )
        )
        asyncio.run(self.service.backlog.record_shortlist(self.cwd, [self.a], "r", "Leif"))
        after = self.board()
        self.assertEqual(strip(before), strip(after))
        self.assertEqual(
            [(u["next"], u["blocked"]) for u in before["units"]],
            [(u["next"], u["blocked"]) for u in after["units"]],
        )
        self.assertEqual(after["backlog"]["shortlist"][0]["unit"], self.a)
        self.assertEqual(
            next(u for u in after["units"] if u["name"] == self.a)["backlog"]["rank"], 1
        )

    def test_three_writes_touch_no_file_and_refusals_insert_nothing(self):
        before = self.store_hash()
        refusals = [
            lambda: self.service.backlog.record_estimate(self.cwd, self.a, 9, "S", "x", "Leif"),
            lambda: self.service.backlog.record_estimate(self.cwd, self.a, 3, "S", "x", "agent:me"),
            lambda: self.service.backlog.record_estimate(
                self.cwd, "0003_finished", 3, "S", "x", "Leif"
            ),
            lambda: self.service.backlog.record_relation(
                self.cwd, self.a, self.a, "trùng", "add", "r", "Leif"
            ),
            lambda: self.service.backlog.record_shortlist(
                self.cwd, [self.a], "r", "Leif"
            ),  # no estimate yet
        ]
        for call in refusals:
            with self.assertRaises(Invalid):
                asyncio.run(call())
        self.assertEqual(
            self.journal.records(self.key, kinds=("estimate-value", "relation", "shortlist")), []
        )
        asyncio.run(self.service.backlog.record_estimate(self.cwd, self.a, "4", "S", "vì", "Leif"))
        asyncio.run(
            self.service.backlog.record_relation(
                self.cwd, self.a, self.b, "trùng", "add", "r", "Leif"
            )
        )
        asyncio.run(self.service.backlog.record_shortlist(self.cwd, [self.a], "r", "Leif"))
        self.assertEqual(self.store_hash(), before)
        self.assertEqual(self.board()["backlog"]["shortlist"][0]["estimate"]["value"], 4)

    def _start_of_spec(self):
        async def go():
            async for _ in self.service.steps.run_step(self.cwd, self.b, "spec"):
                pass

        try:
            asyncio.run(go())
        except Invalid:
            pass
        return self.journal.records(self.key, self.b, kind="start")[-1]

    def test_the_start_record_says_where_the_unit_stood(self):
        first = self._start_of_spec()
        self.assertEqual(first["shortlist"], {"rank": None, "of": None, "record": None})
        asyncio.run(self.service.backlog.record_estimate(self.cwd, self.b, 3, "M", "vì", "Leif"))
        asyncio.run(self.service.backlog.record_shortlist(self.cwd, [self.b], "r", "Leif"))
        second = self._start_of_spec()
        self.assertEqual((second["shortlist"]["rank"], second["shortlist"]["of"]), (1, 1))
        self.assertEqual(second["shortlist"]["record"]["n"], 1)

    def test_a_busy_run_log_still_starts_the_step(self):
        from coscc.runlog.journal import Journal
        from coscc.data import Busy

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
            async for item in self.service.backlog.propose_estimates(self.cwd):
                out.append(item)
            return out

        return asyncio.run(go())

    def test_a_basis_with_no_goal_drops_only_that_unit(self):
        done = self._propose()[-1][1]["estimate"]
        self.assertEqual(done["outcome"], "done")
        self.assertEqual(done["written"], 2)  # one estimate, one relation
        self.assertEqual([r["unit"] for r in done["rejected"]], [self.b])
        values = self.journal.records(self.key, kind="estimate-value")
        self.assertEqual(
            [(v["unit"], v["by"], v["authority"]) for v in values],
            [(self.a, "agent:sess-1", "agent")],
        )
        ends = [
            r for r in self.journal.records(self.key, kind="end") if r.get("stage") == "estimate"
        ]
        self.assertEqual((ends[-1]["outcome"], ends[-1]["cost_usd"]), ("done", 0.12))

    def test_a_session_that_hands_back_no_object_writes_no_estimate(self):
        """Even when its reply holds the estimate as a JSON block."""
        import json

        self.sessions.text = "```json\n" + json.dumps(self.GOOD) + "\n```"
        self.sessions.obj = None
        done = self._propose()[-1][1]["estimate"]
        self.assertEqual(done["outcome"], "failed")
        self.assertIn("no-submission", done["detail"])
        self.assertEqual(self.journal.records(self.key, kind="estimate-value"), [])

    def test_a_second_press_while_one_runs_is_refused_and_spends_nothing(self):
        async def go():
            gate = asyncio.Event()
            self.sessions.gate = gate
            first = self.service.backlog.propose_estimates(self.cwd)
            task = asyncio.create_task(first.__anext__())
            for _ in range(200):
                await asyncio.sleep(0.01)
                if self.sessions.calls:
                    break
            jobs = self.service._update_waited()
            with self.assertRaises(Invalid):
                await self.service.backlog.propose_estimates(self.cwd).__anext__()
            gate.set()
            await task
            async for _ in first:
                pass
            return jobs

        jobs = asyncio.run(go())
        self.assertEqual(self.sessions.calls, 1)
        # An update pauses a running estimate instead of waiting for it.
        self.assertEqual(jobs, [])
        self.assertEqual(self.service.holds.marks, {})
