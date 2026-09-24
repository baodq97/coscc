"""Tests for the one place logic lives.

These exist because `spec.md` R10 is a structural rule with no automated enforcement: if
the page and the API drift apart, it will be because someone put a decision in one of them.
Testing the service directly — with no web framework in the test — is what makes that
drift visible as a missing test rather than as a bug only one entry point has.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from coscc import fetches, gitops, harness, units, worktrees
from coscc.config import Config
from coscc.service import STAGE_FILES, Invalid, Service, describe_base, outcome_label, step_cwd
from coscc.sessions import Live, Sessions

REPO = str(Path(__file__).resolve().parent.parent)


def create_sync(service: Service, *args):
    """`create_unit` is async since `0017`; these tests are not."""
    return asyncio.run(service.create_unit(*args))


def _service(**kw) -> Service:
    config = Config(workspaces=(REPO,), **kw)
    return Service(config, Sessions(config))


class TheGate(unittest.TestCase):
    def test_a_directory_outside_the_list_is_refused_everywhere(self):
        s = _service()
        for call in (
            lambda: s.sessions_for("/etc"),
            lambda: s.history("/etc", "abc"),
            lambda: s.check_send("/etc", "hi"),
        ):
            with self.assertRaises(Invalid):
                call()

    def test_the_reason_names_the_directory_so_a_caller_can_show_it(self):
        with self.assertRaises(Invalid) as e:
            _service().sessions_for("/nope")
        self.assertIn("/nope", str(e.exception))

    def test_a_configured_workspace_passes_the_gate(self):
        self.assertEqual(_service().sessions_for(REPO)["cwd"], REPO)


class WhatCountsAsInvalid(unittest.TestCase):
    def test_history_needs_a_session_id(self):
        with self.assertRaises(Invalid):
            _service().history(REPO, "")

    def test_empty_prompt_is_refused_before_anything_is_spent(self):
        # Quota matters here: this refusal must land before a session is created.
        for text in ("", "   ", "\n"):
            with self.assertRaises(Invalid):
                _service().check_send(REPO, text)

    def test_check_send_accepts_real_text(self):
        self.assertIsNone(_service().check_send(REPO, "hello"))


class TheGateWithAStore(unittest.TestCase):
    """`spec.md` R21 end to end: the gate, not just the store, must hold.

    The store tests prove a bad entry is dropped on read. These prove that dropping it
    actually closes every working path, which is the claim that matters.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _svc(self):
        config = Config(
            workspaces=(), working_dir=str(self.root), data_dir=str(self.root)
        )
        return Service(config, Sessions(config))

    def test_a_stored_workspace_passes_the_gate(self):
        (self.root / "repo").mkdir()
        s = self._svc()
        s.store.add("repo", "My repo")
        self.assertEqual(s.sessions_for(str(self.root / "repo"))["cwd"], str(self.root / "repo"))

    def test_a_hand_edited_entry_pointing_outside_closes_every_path(self):
        s = self._svc()
        # What somebody with `sqlite3` on the command line could type. The name is
        # rejected on read, so the row exists and the workspace does not.
        with s.store.data.write() as conn:
            for name in ("/etc", "../../etc"):
                conn.execute(
                    "INSERT INTO workspaces (root, name, label, added_at) "
                    "VALUES (?, ?, '', '2026-01-01T00:00:00+00:00')",
                    (str(s.store.working_dir), name),
                )
        for call in (
            lambda: s.sessions_for("/etc"),
            lambda: s.history("/etc", "abc"),
            lambda: s.check_send("/etc", "hi"),
        ):
            with self.assertRaises(Invalid):
                call()

    def test_a_sibling_of_the_working_folder_is_refused(self):
        s = self._svc()
        s.store.add("repo")
        with self.assertRaises(Invalid):
            s.check_send(str(self.root.parent), "hi")

    def test_a_subdirectory_nobody_added_is_refused(self):
        (self.root / "stray").mkdir()
        s = self._svc()
        with self.assertRaises(Invalid):
            s.check_send(str(self.root / "stray"), "hi")

    def test_removing_a_workspace_closes_the_gate_again(self):
        (self.root / "repo").mkdir()
        s = self._svc()
        s.store.add("repo")
        s.sessions_for(str(self.root / "repo"))
        s.store.remove("repo")
        with self.assertRaises(Invalid):
            s.check_send(str(self.root / "repo"), "hi")

    def test_no_working_folder_means_no_store_and_0001_behaviour(self):
        config = Config(workspaces=(REPO,))
        s = Service(config, Sessions(config))
        self.assertIsNone(s.store)
        self.assertEqual(s.workspaces()["paths"], [REPO])

    def test_the_count_is_reported_and_tracks_both_sources(self):
        (self.root / "a").mkdir()
        config = Config(
            workspaces=(REPO,), working_dir=str(self.root), data_dir=str(self.root)
        )
        s = Service(config, Sessions(config))
        self.assertEqual(s.workspaces()["count"], 1)
        s.store.add("a")
        self.assertEqual(s.workspaces()["count"], 2)
        s.store.remove("a")
        self.assertEqual(s.workspaces()["count"], 1)

    def test_a_missing_directory_is_flagged_not_hidden(self):
        s = self._svc()
        s.store.add("gone")
        row = next(r for r in s.workspaces()["workspaces"] if r["name"] == "gone")
        self.assertTrue(row["missing"])


class OneMembershipQuestion(unittest.TestCase):
    """`spec.md` R10, at the place it was found broken.

    The session layer keeps its own guard — it is the last thing before a CLI process is
    spawned — but it must answer the same question the service gate answers. Before this,
    a store-backed workspace passed the gate and was refused one layer down, and only a
    real clone-and-send found it.
    """

    def test_the_session_layer_sees_store_workspaces_too(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "repo").mkdir()
            config = Config(workspaces=(), working_dir=str(root), data_dir=str(root))
            s = Service(config, Sessions(config))
            s.store.add("repo")
            self.assertTrue(s.sessions.membership(str(root / "repo")))

    def test_the_session_layer_still_refuses_what_the_gate_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            config = Config(workspaces=(), working_dir=d, data_dir=d)
            s = Service(config, Sessions(config))
            self.assertFalse(s.sessions.membership("/etc"))


class PullStopsAtALiveSession(unittest.TestCase):
    """R6 and R7. The refusal has to happen before `git` runs, not after."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.addCleanup(self.tmp.cleanup)
        config = Config(
            workspaces=(), working_dir=str(self.root), data_dir=str(self.root)
        )
        self.s = Service(config, Sessions(config))
        self.s.store.add("repo")
        self._repo(self.root / "repo")

    @staticmethod
    def _repo(path: Path) -> None:
        """A real repo, because `gitops.pull` returns before spawning anything otherwise.

        With a plain directory the "no git process" assertion would pass for the wrong
        reason — git never runs on a non-repo either way. There is no remote, so the pull
        that does get through fails locally and reaches no network.
        """
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", str(path)], check=True)

    def _open_session_in(self, name: str) -> None:
        target = str(self.s.store.path_of(name))
        self.s.sessions._live["live-1"] = Live(client=object(), session_id="live-1", cwd=target)

    def _pull(self, name: str = "repo"):
        return asyncio.run(self.s.pull_workspace(name))

    def test_no_git_process_is_spawned_while_a_session_is_live(self):
        """R6's testable half. A refusal after the fetch would have already moved files."""
        self._open_session_in("repo")
        boom = mock.Mock(side_effect=AssertionError("git ran despite a live session"))
        with mock.patch.object(subprocess, "Popen", boom), \
                mock.patch.object(asyncio, "create_subprocess_exec", boom):
            with self.assertRaises(Invalid) as e:
                self._pull()
        boom.assert_not_called()
        self.assertIn("repo", str(e.exception))
        self.assertIn("live session", str(e.exception))

    def test_it_gets_as_far_as_git_once_the_session_is_gone(self):
        """R7. A permanent block would be a different bug, not a fix.

        The directory is not a git repo, so reaching `gitops` is itself the signal: the
        message is git's complaint rather than the session refusal.
        """
        self._open_session_in("repo")
        with self.assertRaises(Invalid):
            self._pull()
        self.s.sessions._live.clear()
        with self.assertRaises(Invalid) as e:
            self._pull()
        self.assertNotIn("live session", str(e.exception))

    def test_a_session_in_another_workspace_does_not_block_this_one(self):
        self.s.store.add("other")
        self._repo(self.root / "other")
        self._open_session_in("other")
        with self.assertRaises(Invalid) as e:
            self._pull("repo")
        self.assertNotIn("live session", str(e.exception))


class NoWebFrameworkLeaksIn(unittest.TestCase):
    def test_service_module_imports_no_web_framework(self):
        """`spec.md` R10 in the only form a test can hold it.

        If the service ever imports aiohttp, FastAPI or Reflex, logic has started moving
        back towards one entry point and the two will drift.
        """
        source = (Path(__file__).parent / "service.py").read_text()
        for banned in ("import aiohttp", "import fastapi", "import reflex", "from fastapi", "from aiohttp", "from reflex"):
            self.assertNotIn(banned, source)


class ARefusalFromRunnerStaysARefusal(unittest.TestCase):
    """The boundary `coscc/api.py:157-164` depends on, and the one review caught open.

    `run_step` maps this layer's refusals with one `except RunError`. 0012 introduced a
    second exception type on that path -- `harness.MissingRules`, raised when a stage's
    rules cannot be found -- and for one commit it escaped: measured 2026-09-22, a
    workspace whose harness had `cos.mjs` but no skills produced an unhandled
    `MissingRules` where a 400 was intended, so the page would have shown a 500 for the
    exact failure 0012 was built to report clearly.

    The scenario is not hypothetical: it is a release whose copy step took `scripts/` and
    not `skills/`, which is one of the four things `harness.wheel_complaints` exists to
    refuse.
    """

    def test_a_stage_whose_rules_are_missing_is_invalid_not_an_escaped_exception(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            workspace = root / "work" / "proj"
            workspace.mkdir(parents=True)
            # `0014`: a unit's artifacts live in the product's store, never in the
            # workspace tree, so the fixture has to be built where the product looks.
            unit = units.unit_dir(workspace, "0009_a-test-unit", root / "data")
            unit.mkdir(parents=True)
            (unit / "intent.md").write_text("Status: accepted.\nI", encoding="utf-8")
            (unit / "spec.md").write_text("Status: accepted.\nS", encoding="utf-8")

            # A harness carrying cos.mjs and no skills: the Board reads, every Run refuses.
            half = root / "half"
            (half / "scripts").mkdir(parents=True)
            shutil.copy(Path(REPO) / ".claude" / "scripts" / "cos.mjs", half / "scripts" / "cos.mjs")
            (half / "skills").mkdir()

            config = Config(
                workspaces=(),
                working_dir=str(root / "work"),
                data_dir=str(root / "data"),
            )
            service = Service(config, Sessions(config))
            asyncio.run(service.add_workspace("proj"))

            originals = (harness.PACKAGE_HARNESS, harness.CHECKOUT_HARNESS)
            harness.PACKAGE_HARNESS = Path("/nonexistent/packaged")
            harness.CHECKOUT_HARNESS = half
            try:
                async def go():
                    async for _ in service.run_step(str(workspace), "0009_a-test-unit", "plan"):
                        pass

                with self.assertRaises(Invalid) as caught:
                    asyncio.run(go())
            finally:
                harness.PACKAGE_HARNESS, harness.CHECKOUT_HARNESS = originals

            # And it still says which stage and where it looked -- `spec.md` R4.
            message = str(caught.exception)
            self.assertIn("plan", message)
            self.assertIn("SKILL.md", message)


if __name__ == "__main__":
    unittest.main()


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


class AUnitsBaseIsTheRemoteTrunk(unittest.TestCase):
    """`0030_a-unit-branch-starts-from-a-stale-main` plan step 5.

    `run_step` refreshes a still-detached tree from `origin/main` before the step runs, and
    carries what it found into the `done` record — the reading `describe_base` turns into
    one sentence for the board and the prompt. Same fixture as `StartingAUnitAndItsBranch`
    (a bare remote on disk, no network), driven with a session that replies without talking
    to anything, the way `AStepRecordsTheTransitionItCaused` does below.

    R7 is `intent.md ## Proposed outcome`'s own measurement: `git merge-base --is-ancestor`
    and `git rev-list --count`, run by subprocess against the branch the app or a session
    cut, never against anything this test computed itself.
    """

    class Replies:
        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            yield ("chunk", "# Spec: a problem\nAuthor: t. Status: accepted.\n\n## Body\n")
            yield ("done", {"session_id": "sess-30", "cost": {"output_tokens": 3}})

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
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
        self.config = Config(
            workspaces=(str(self.repo),),
            working_dir=str(self.root / "work"),
            data_dir=str(self.root / "data"),
        )
        self.service = Service(self.config, self.Replies())

    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repo), "-c", "user.name=T",
             "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
            capture_output=True, text=True, check=True,
        ).stdout

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

    def _typed_unit(self, slug: str = "a-problem") -> str:
        made = create_sync(self.service, str(self.repo), slug, "some words")
        (Path(made["path"]) / "intent.md").write_text(
            f"# Intent: {slug}\nAuthor: t. Type: fix. Status: accepted.\n", encoding="utf-8"
        )
        return made["unit"]

    def _tree(self, unit: str) -> Path:
        return worktrees.path(str(self.repo), unit, str(self.root / "data"))

    def _tree_head(self, tree: Path) -> str:
        return subprocess.run(
            ["git", "-C", str(tree), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    def _run_step(self, unit: str, stage: str = "spec") -> dict:
        async def go():
            last = None
            async for item in self.service.run_step(str(self.repo), unit, stage):
                last = item
            return last

        kind, payload = asyncio.run(go())
        self.assertEqual(kind, "done")
        return payload

    def test_r1_the_tree_is_moved_to_the_fetched_tip_and_local_main_stays(self):
        unit = self._typed_unit()
        local_before = self._git("rev-parse", "main").strip()
        ahead = self._advance_remote()
        done = self._run_step(unit)
        self.assertEqual(done["outcome"], "done")
        fetched = done["base"].pop("fetch")
        self.assertEqual(
            done["base"], {"ref": "origin/main", "sha": ahead[:7], "fresh": True, "reason": ""}
        )
        # `0048` R8: a step alone fetches once, exactly as before.
        self.assertEqual((fetched["outcome"], fetched["attempts"]), ("fetched", 1))
        self.assertEqual(self._git("rev-parse", "main").strip(), local_before)
        self.assertEqual(self._tree_head(self._tree(unit)), ahead)

    def test_r2_a_broken_origin_does_not_stop_the_step(self):
        unit = self._typed_unit()
        tree = self._tree(unit)
        before = self._tree_head(tree)
        self._git("remote", "set-url", "origin", str(self.root / "gone.git"))
        done = self._run_step(unit)
        self.assertEqual(done["outcome"], "done")
        self.assertFalse(done["base"]["fresh"])
        self.assertTrue(done["base"]["reason"])
        self.assertEqual(self._tree_head(tree), before)

    def test_r5_a_commit_made_directly_on_the_tree_is_never_left_behind(self):
        unit = self._typed_unit()
        tree = self._tree(unit)
        (tree / "local.txt").write_text("mine\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(tree), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(tree), "-c", "user.name=T", "-c", "user.email=t@example.invalid",
             "-c", "commit.gpgsign=false", "commit", "-q", "-m", "local"], check=True,
        )
        local_head = self._tree_head(tree)
        done = self._run_step(unit)
        self.assertFalse(done["base"]["fresh"])
        self.assertIn("ancestor", done["base"]["reason"])
        self.assertEqual(self._tree_head(tree), local_head)

    def test_r7_the_branch_start_branch_cuts_carries_the_remote_tip(self):
        """`intent.md ## Proposed outcome`, measured through `start_branch`.

        `0030` review round 1, F1: `tip` is the SHA `_advance_remote()` itself pushed and
        returned, never a ref read back from the workspace — `origin/main` there is the very
        ref `start_branch` updates when it fetches, so comparing against it would still pass
        with the fetch removed, which is exactly what this test exists to catch.
        """
        tip = self._advance_remote()
        unit = self._typed_unit()
        got = asyncio.run(self.service.start_branch(str(self.repo), unit))
        tree = got["worktree"]
        branch = got["branch"]
        self.assertEqual(
            subprocess.run(
                ["git", "-C", tree, "merge-base", "--is-ancestor", tip, branch]
            ).returncode,
            0,
        )
        self.assertEqual(
            subprocess.run(
                ["git", "-C", tree, "rev-list", "--count", f"{branch}..{tip}"],
                capture_output=True, text=True, check=True,
            ).stdout.strip(),
            "0",
        )

    def test_r7_a_branch_a_session_cuts_itself_also_carries_the_remote_tip(self):
        """Plan Risk 2's gap, closed at the one place a step runs: `run_step` refreshes the
        still-detached tree, and a session's own `git switch -c` right after starts from
        that refreshed HEAD — the same command a person runs at a terminal
        (`.claude/CLAUDE.md` step 4), not a wrapper this test invented.
        """
        unit = self._typed_unit()
        ahead = self._advance_remote()
        self._run_step(unit)
        tree = self._tree(unit)
        branch = units.branch_name(str(self.repo), unit, str(self.root / "data"))
        subprocess.run(
            ["git", "-C", str(tree), "switch", "--no-track", "-c", branch],
            check=True, capture_output=True,
        )
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(tree), "merge-base", "--is-ancestor", ahead, branch]
            ).returncode,
            0,
        )
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(tree), "rev-list", "--count", f"{branch}..{ahead}"],
                capture_output=True, text=True, check=True,
            ).stdout.strip(),
            "0",
        )

    def test_describe_base_is_empty_when_fresh_and_names_the_sha_when_not(self):
        self.assertEqual(describe_base(None), "")
        self.assertEqual(describe_base({"fresh": True}), "")
        said = describe_base(
            {"fresh": False, "ref": "origin/main", "sha": "abc1234", "reason": "boom"}
        )
        self.assertIn("abc1234", said)
        self.assertIn("boom", said)


class AnImplIsToldWhatMainChangedSinceThePlan(unittest.TestCase):
    """`0042` plan step 5, end to end on real git: `plan` runs, another unit merges, `impl`
    starts. The fixture is `AUnitsBaseIsTheRemoteTrunk`'s, borrowed rather than inherited
    so its tests do not run twice. The expected list is `git diff --name-only` run by
    subprocess — the intent's own check — never something this test worked out.
    """

    HEADING = "# The files main changed since the plan"

    class Replies:
        def __init__(self):
            self.reply = ""
            self.prompts: list[str] = []

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            self.prompts.append(text)
            yield ("chunk", self.reply)
            yield ("done", {"session_id": "sess-42", "cost": {"output_tokens": 3}})

    def setUp(self):
        AUnitsBaseIsTheRemoteTrunk.setUp(self)
        # `0048`: a step under 30s after the last fetch reuses it, so a merge between
        # `plan` and `impl` is only seen when `impl` starts later — `_impl` moves this clock.
        self.now = [1000.0]
        patcher = mock.patch.object(fetches, "shared", fetches.Fetches(clock=lambda: self.now[0]))
        patcher.start()
        self.addCleanup(patcher.stop)

    _git = AUnitsBaseIsTheRemoteTrunk._git
    _advance_remote = AUnitsBaseIsTheRemoteTrunk._advance_remote
    _typed_unit = AUnitsBaseIsTheRemoteTrunk._typed_unit
    _tree = AUnitsBaseIsTheRemoteTrunk._tree
    _run_step = AUnitsBaseIsTheRemoteTrunk._run_step

    PLAN = (
        "# Plan: a problem\nIntent: intent.md. Author: t. Status: accepted.\n\n"
        "## Files that change\n\n| `a.py` | x |\n| `b.py:3-4` | y |\n\n## Order of work\n\n1. x\n"
    )

    def _unit_with_spec(self) -> tuple[str, Path]:
        unit = self._typed_unit()
        directory = units.unit_dir(str(self.repo), unit, str(self.root / "data"))
        (directory / "spec.md").write_text(
            "# Spec: a problem\nAuthor: t. Status: accepted.\n", encoding="utf-8"
        )
        return unit, directory

    def _planned(self) -> str:
        unit, _ = self._unit_with_spec()
        self.service.sessions.reply = self.PLAN
        self.assertEqual(self._run_step(unit, "plan")["outcome"], "done")
        return unit

    def _impl(self, unit: str) -> tuple[str, dict]:
        self.now[0] += fetches.REUSE_SECONDS
        self.service.sessions.reply = "working"
        self._run_step(unit, "impl")
        return self.service.sessions.prompts[-1], self._start(unit, "impl")

    def _start(self, unit: str, stage: str) -> dict:
        records = self.service._journal().records(self.service._journal_key(str(self.repo)), unit, kind="start")
        return [r for r in records if r["stage"] == stage][-1]

    def _tree_git(self, unit: str, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self._tree(unit)), *args], capture_output=True, text=True, check=True
        ).stdout.strip()

    def test_r4a_the_prompt_names_the_changed_files_the_plan_names(self):
        unit = self._planned()
        self._advance_remote("a.py", "changed\n")
        self._advance_remote("lib_a.py", "outside, and a suffix trap\n")
        prompt, start = self._impl(unit)
        drift = start["plan_drift"]
        main = self._tree_git(unit, "rev-parse", "refs/remotes/origin/main")
        diff = self._tree_git(unit, "diff", f"{drift['plan_sha']}..{main}", "--name-only").splitlines()
        self.assertEqual(drift["files"], [d for d in diff if d in ("a.py", "b.py")])
        self.assertEqual(drift["files"], ["a.py"])
        self.assertEqual(drift["main_sha"], main)
        self.assertEqual(drift["plan_sha"], self._start(unit, "plan")["head"])
        self.assertTrue(drift["checked"])
        self.assertIn(self.HEADING, prompt)
        self.assertIn("-- a.py`", prompt)
        self.assertNotIn("lib_a.py", prompt)
        self.assertNotIn("-- b.py", prompt)
        self.assertEqual(start["agents"], 1)

    def test_r4b_nothing_the_plan_names_changed_adds_nothing(self):
        unit = self._planned()
        self._advance_remote("other.txt", "outside\n")
        prompt, start = self._impl(unit)
        self.assertEqual(start["plan_drift"]["files"], [])
        self.assertTrue(start["plan_drift"]["checked"])
        self.assertNotIn(self.HEADING, prompt)

    def test_a_merge_under_thirty_seconds_after_the_plans_fetch_is_not_seen(self):
        # `0048` C1 reaching `0042`: the fetch is reused, so `origin/main` has not moved and
        # the merge is missing from the diff. `main_sha` says which tip was measured.
        unit = self._planned()
        self._advance_remote("a.py", "changed\n")
        self.now[0] -= fetches.REUSE_SECONDS  # `_impl` adds it back: the same instant
        _, start = self._impl(unit)
        self.assertEqual(start["base"]["fetch"]["outcome"], "reused")
        self.assertEqual(start["plan_drift"]["files"], [])
        self.assertTrue(start["plan_drift"]["checked"])
        self.assertEqual(start["plan_drift"]["main_sha"], start["plan_drift"]["plan_sha"])

    def test_r4c_a_plan_no_run_wrote_cannot_be_checked(self):
        unit, directory = self._unit_with_spec()
        (directory / "plan.md").write_text(self.PLAN, encoding="utf-8")
        prompt, start = self._impl(unit)
        self.assertFalse(start["plan_drift"]["checked"])
        self.assertIsNone(start["plan_drift"]["files"])
        self.assertIn(self.HEADING, prompt)
        self.assertIn("could not check", prompt)

    def test_r8_a_computation_that_raises_does_not_stop_the_step(self):
        unit = self._planned()
        with mock.patch("coscc.drift.compute", side_effect=RuntimeError("boom")):
            prompt, start = self._impl(unit)
        self.assertFalse(start["plan_drift"]["checked"])
        self.assertEqual(start["plan_drift"]["reason"], "boom")
        self.assertIn("could not check", prompt)

    def test_another_stage_carries_no_plan_drift(self):
        unit, _ = self._unit_with_spec()
        self.service.sessions.reply = "# Spec: a problem\nAuthor: t. Status: accepted.\n"
        self._run_step(unit, "spec")
        self.assertNotIn("plan_drift", self._start(unit, "spec"))
        self.assertNotIn(self.HEADING, self.service.sessions.prompts[-1])


class AStepRecordsTheTransitionItCaused(unittest.TestCase):
    """`0014` R6. The first writer into `0013`'s log that is not the git import.

    `.cos/0013_.../ship.md` said the loop would come back here: history imported from git
    carries no actor and no session because git knows neither, so the provenance that unit
    built is only true of work done after it. These rows are that work — and the check
    that matters is that `actor` and `session` are **not** `unknown`.

    Driven with a session that replies without talking to anything, because what is under
    test is the bookkeeping around a step, not the step.
    """

    class Replies:
        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            yield ("chunk", "# Spec: a problem\nAuthor: t. Status: accepted.\n\n## Body\n")
            yield ("done", {"session_id": "sess-42", "cost": {"output_tokens": 3}})

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "work" / "proj"
        self.repo.mkdir(parents=True)
        config = Config(
            workspaces=(str(self.repo),),
            working_dir=str(self.root / "work"),
            data_dir=str(self.root / "data"),
        )
        self.config = config
        self.service = Service(config, self.Replies())
        self.made = create_sync(self.service,str(self.repo), "a-problem", "some words")
        (Path(self.made["path"]) / "intent.md").write_text(
            "# Intent: a problem\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8"
        )

    def _run(self, stage: str) -> None:
        async def go():
            async for _ in self.service.run_step(str(self.repo), self.made["unit"], stage):
                pass

        asyncio.run(go())

    def test_the_row_names_the_stage_and_the_real_session(self):
        self._run("spec")
        found = self.service.unit_history(str(self.repo), self.made["unit"])
        [row] = [r for r in found["transitions"] if r["artifact"] == "spec.md"]
        self.assertEqual(row["to_state"], "accepted")
        self.assertEqual(row["actor"], "stage:spec")
        self.assertEqual(row["session"], "sess-42")
        self.assertNotEqual(row["session"], "unknown")

    def test_the_projection_moves_with_it(self):
        self._run("spec")
        found = self.service.unit_history(str(self.repo), self.made["unit"])
        self.assertEqual(found["state"]["spec.md"], "accepted")

    def test_running_the_same_stage_twice_is_two_events_not_one(self):
        # The event `0013` exists to count: an artifact rewritten after it was settled.
        self._run("spec")
        self._run("spec")
        found = self.service.unit_history(str(self.repo), self.made["unit"])
        rows = [r for r in found["transitions"] if r["artifact"] == "spec.md"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(found["settled_edits"], 1)

    def test_a_failed_step_records_nothing(self):
        class Empty:
            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                yield ("done", {"session_id": "sess-0", "cost": {}})

        self.service.sessions = Empty()

        async def go():
            out = []
            async for item in self.service.run_step(str(self.repo), self.made["unit"], "spec"):
                out.append(item)
            return out

        # A step that fails comes back as data, not as an exception: `Runner.run` catches
        # its own RunError so the stream always ends with one `done`. The outcome in it is
        # what says whether anything happened.
        _, payload = asyncio.run(go())[-1]
        self.assertNotEqual(payload["outcome"], "done")
        found = self.service.unit_history(str(self.repo), self.made["unit"])
        self.assertEqual([r for r in found["transitions"] if r["artifact"] == "spec.md"], [])


class AFailedAttemptReachesTheNextRunAndTheBoard(unittest.TestCase):
    """`0019_a-failed-step-destroys-the-work-that-succeeded` plan step 6, `spec.md` R5-R7."""

    class Empty:
        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            yield ("session", "sess-fail")
            yield ("done", {"session_id": "sess-fail", "cost": {"turns": 3, "cost_usd": 0.02}})

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "work" / "proj"
        self.repo.mkdir(parents=True)
        config = Config(
            workspaces=(str(self.repo),),
            working_dir=str(self.root / "work"),
            data_dir=str(self.root / "data"),
        )
        self.config = config
        self.service = Service(config, self.Empty())
        self.made = create_sync(self.service, str(self.repo), "a-problem", "some words")
        (Path(self.made["path"]) / "intent.md").write_text(
            "# Intent: a problem\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8"
        )

    def _run(self, stage: str) -> None:
        async def go():
            async for _ in self.service.run_step(str(self.repo), self.made["unit"], stage):
                pass

        asyncio.run(go())

    def test_the_next_run_of_the_same_stage_sees_the_failed_attempt(self):
        self._run("spec")  # Empty: no Status line -> RunError -> outcome "failed"

        class Probe:
            def __init__(self):
                self.seen = ""

            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                self.seen = text
                yield ("chunk", "# Spec: x\nAuthor: t. Status: accepted.\n")
                yield ("done", {"session_id": "sess-ok", "cost": {}})

        probe = Probe()
        self.service.sessions = probe
        self._run("spec")
        self.assertIn("# The attempt before this one", probe.seen)
        self.assertIn("sess-fail", probe.seen)

    def test_a_run_after_done_carries_no_attempt_section(self):
        class Replies:
            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                yield ("chunk", "# Spec: x\nAuthor: t. Status: accepted.\n")
                yield ("done", {"session_id": "sess-1", "cost": {}})

        self.service.sessions = Replies()
        self._run("spec")

        class Probe:
            def __init__(self):
                self.seen = ""

            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                self.seen = text
                yield ("chunk", "# Spec: x\nAuthor: t. Status: accepted.\n")
                yield ("done", {"session_id": "sess-2", "cost": {}})

        probe = Probe()
        self.service.sessions = probe
        self._run("spec")
        self.assertNotIn("# The attempt before this one", probe.seen)

    def test_board_distinguishes_a_failed_stage_from_a_never_run_one(self):
        self._run("spec")
        board = asyncio.run(self.service.board(str(self.repo)))
        [unit] = [u for u in board["units"] if u["name"] == self.made["unit"]]
        rows = {r["stage"]: r for r in unit["stages"]}
        self.assertIsNotNone(rows["spec"]["last_run"])
        self.assertNotEqual(rows["spec"]["last_run"]["outcome"], "done")
        self.assertIsNone(rows["plan"]["last_run"])

    def test_the_excerpt_never_reaches_a_route(self):
        # R7. The transcript is faked via the runner's own read function, so this does
        # not depend on a real session store.
        with mock.patch(
            "coscc.runner.sessions_mod.transcript_excerpt",
            return_value=("CANARY-0019-EXCERPT", 999),
        ):
            self._run("spec")

        unit = self.made["unit"]
        board = asyncio.run(self.service.board(str(self.repo)))
        timeline = self.service.timeline(str(self.repo), unit)
        activity = self.service.activity(str(self.repo))
        usage = self.service.usage(str(self.repo))
        combo = self.service.activity_and_usage(str(self.repo))
        for payload in (board, timeline, activity, usage, combo):
            self.assertNotIn("CANARY-0019-EXCERPT", json.dumps(payload))
        # And the attempt record itself does carry it — otherwise this test would pass
        # for the wrong reason.
        [attempt] = self.service._journal().records(
            self.service._journal_key(str(self.repo)), unit, kind="attempt"
        )
        self.assertEqual(attempt["excerpt"], "CANARY-0019-EXCERPT")


class AStepTheGateClosesNeverStarts(unittest.TestCase):
    """`.claude/CLAUDE.md` invariant 2, enforced by the app for the first time.

    Until 2026-09-23 `run_step` went from reading the board straight to starting a
    session. The gate existed, `cos.mjs` decided it, every skill opened by telling the
    stage to ask it — and the product asked nobody. The board would run `ship` on a unit
    whose `spec.md` had never been written.

    What these tests actually pin is the *ordering*: the refusal has to land before the
    session is created, because after that the money is already gone.
    """

    class NeverCalled:
        """A session layer that fails the test if a refused step reaches it."""

        def __init__(self):
            self.calls = 0

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            self.calls += 1
            yield ("chunk", "# Ship: no\nAuthor: t. Status: accepted.\n\n## Body\n")
            yield ("done", {"session_id": "s", "cost": {}})

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "work" / "proj"
        self.repo.mkdir(parents=True)
        self.sessions = self.NeverCalled()
        self.service = Service(
            Config(
                workspaces=(str(self.repo),),
                working_dir=str(self.root / "work"),
                data_dir=str(self.root / "data"),
            ),
            self.sessions,
        )
        self.made = create_sync(self.service,str(self.repo), "a-problem", "some words")
        (Path(self.made["path"]) / "intent.md").write_text(
            "# Intent: a problem\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8"
        )

    def _run(self, stage: str):
        async def go():
            out = []
            async for item in self.service.run_step(str(self.repo), self.made["unit"], stage):
                out.append(item)
            return out

        return asyncio.run(go())

    def test_a_stage_whose_earlier_artifacts_are_missing_is_refused(self):
        # `spec.md`, `plan.md`, `impl.md`, `pr.md` and `review.md` do not exist, so `ship`
        # has nothing behind it. The intent alone does not open the last gate.
        with self.assertRaises(Invalid) as caught:
            self._run("ship")
        self.assertTrue(str(caught.exception).strip())

    def test_the_refusal_names_what_is_missing_rather_than_only_saying_no(self):
        with self.assertRaises(Invalid) as caught:
            self._run("ship")
        # The gate's own words. A refusal a person cannot act on is a refusal that sends
        # them to read the source.
        self.assertIn("spec.md", str(caught.exception))

    def test_no_session_is_created_for_a_step_the_gate_refused(self):
        """The whole point of asking in `run_step` and not inside `Runner`."""
        with self.assertRaises(Invalid):
            self._run("ship")
        self.assertEqual(self.sessions.calls, 0)

    def test_a_stage_the_gate_opens_still_runs(self):
        # The guard must not close the ordinary path. `spec` follows an accepted intent.
        self._run("spec")
        self.assertEqual(self.sessions.calls, 1)

    def test_the_gate_is_told_which_repository_the_unit_lives_beside(self):
        """`0015`: the store has no git, so `review` and `ship` read the workspace's."""
        from coscc import board as board_reader

        seen = {}

        async def fake_gate(units_root, unit, stage, repo=None, **kw):
            seen["repo"] = repo
            return False, "blocked: stop here"

        with mock.patch.object(board_reader, "gate", fake_gate):
            with self.assertRaises(Invalid):
                self._run("review")
        self.assertEqual(seen["repo"], str(self.repo))
        self.assertEqual(self.sessions.calls, 0)


class AStageRunsOnTheModelSettingsNames(unittest.TestCase):
    """`0004_no-setting-says-which-model-runs-a-stage`. The setting chooses the model a
    step's session is created with, and nothing else."""

    class Probe:
        def __init__(self):
            self.models = []

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            self.models.append(kw.get("model"))
            yield ("chunk", "# Spec: a problem\nAuthor: t. Status: accepted.\n\n## Body\n")
            yield ("done", {"session_id": "sess-m", "cost": {}})

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "work" / "proj"
        self.repo.mkdir(parents=True)
        self.probe = self.Probe()
        self.service = Service(
            Config(
                workspaces=(str(self.repo),),
                working_dir=str(self.root / "work"),
                data_dir=str(self.root / "data"),
            ),
            self.probe,
        )
        self.made = create_sync(self.service, str(self.repo), "a-problem", "some words")
        (Path(self.made["path"]) / "intent.md").write_text(
            "# Intent: a problem\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8"
        )

    def _run(self, stage: str):
        async def go():
            return [i async for i in self.service.run_step(str(self.repo), self.made["unit"], stage)]

        return asyncio.run(go())

    def _start(self):
        journal = self.service._journal()
        return journal.records(self.service._journal_key(str(self.repo)), kind="start")[-1]

    def test_the_shipped_default_reaches_the_session(self):
        self._run("spec")
        self.assertEqual(self.probe.models, ["claude-opus-5-5[1m]"])
        self.assertEqual(self._start()["model_source"], "default")

    def test_an_override_reaches_the_session_and_the_log_then_goes_away(self):
        asyncio.run(self.service.set_stage_model("spec", "claude-sonnet-5"))
        self._run("spec")
        self.assertEqual(self.probe.models[-1], "claude-sonnet-5")
        self.assertEqual(
            (self._start()["model"], self._start()["model_source"]),
            ("claude-sonnet-5", "override"),
        )
        asyncio.run(self.service.set_stage_model("spec", None))
        self._run("spec")
        self.assertEqual(self._start()["model_source"], "default")

    def test_bad_names_and_empty_models_are_invalid(self):
        for name, model in (("bogus", "m"), ("spec", "  "), ("spec", 3), ("", "m"), (None, "m")):
            with self.assertRaises(Invalid, msg=(name, model)):
                asyncio.run(self.service.set_stage_model(name, model))

    def test_each_change_leaves_one_setting_record(self):
        asyncio.run(self.service.set_stage_model("impl", "a"))
        asyncio.run(self.service.set_stage_model("impl", "b"))
        asyncio.run(self.service.set_stage_model("impl", None))
        records = self.service._journal().records("", kind="setting")
        self.assertEqual(
            [(r["name"], r["old"], r["new"]) for r in records],
            [("model:impl", None, "a"), ("model:impl", "a", "b"), ("model:impl", "b", None)],
        )

    def test_the_gate_is_asked_the_same_question_either_way(self):
        from coscc import board as board_reader

        seen = []
        real = board_reader.gate

        async def spy(*a, **kw):
            seen.append((a, kw))
            return await real(*a, **kw)

        with mock.patch.object(board_reader, "gate", spy):
            self._run("spec")
            asyncio.run(self.service.set_stage_model("spec", "x"))
            self._run("spec")
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0], seen[1])

    def test_a_spec_step_has_no_label_and_the_default_effort(self):
        # `0033` R10: a stage before `plan` has no label; effort comes from R8's table.
        self._run("spec")
        start = self._start()
        self.assertEqual((start["label_declared"], start["label"], start["label_source"]), (None, None, None))
        self.assertEqual((start["effort"], start["effort_source"]), ("high", "default"))
        self.assertNotIn("impl_run", start)

    def test_an_effort_override_of_max_is_taken_and_logged(self):
        # R7: `max` only through an override; R9: every change is a `setting` record.
        asyncio.run(self.service.set_stage_effort("impl:novel", "max"))
        asyncio.run(self.service.set_stage_effort("impl:novel", None))
        records = self.service._journal().records("", kind="setting")
        self.assertEqual(
            [(r["name"], r["old"], r["new"]) for r in records],
            [("effort:impl:novel", None, "max"), ("effort:impl:novel", "max", None)],
        )
        for name, effort in (("chat", "low"), ("plan:novel", "low"), ("impl", "turbo"), ("bogus", "low")):
            with self.assertRaises(Invalid, msg=(name, effort)):
                asyncio.run(self.service.set_stage_effort(name, effort))

    def test_a_novel_row_takes_a_model_override(self):
        asyncio.run(self.service.set_stage_model("review:novel", "m"))
        rows = {r["name"]: r for r in asyncio.run(self.service.stage_models())["rows"]}
        self.assertEqual((rows["review:novel"]["model"], rows["review:novel"]["source"]), ("m", "override"))
        self.assertEqual(rows["review"]["source"], "default")

    def test_model_prefs_are_not_preferences(self):
        asyncio.run(self.service.set_stage_model("impl", "a"))
        self.assertNotIn("model:impl", self.service.preferences())
        with self.assertRaises(Invalid):
            self.service.set_preference("model:impl", "b")

    def test_chat_uses_cos_model_and_is_logged(self):
        service = Service(
            Config(
                workspaces=(str(self.repo),),
                working_dir=str(self.root / "work"),
                data_dir=str(self.root / "data"),
                model="env-model",
            ),
            self.probe,
        )

        async def go():
            return [i async for i in service.stream(str(self.repo), "hi")]

        asyncio.run(go())
        self.assertEqual(self.probe.models[-1], "env-model")
        [rec] = service._journal().records(service._journal_key(str(self.repo)), kind="chat")
        self.assertEqual((rec["model"], rec["model_source"]), ("env-model", "COS_MODEL"))

    def test_settings_names_cos_model_as_the_fallback(self):
        self.assertIn("cos_model", self.service.settings())
        self.assertNotIn("model", self.service.settings())


class AnImplStepRunsUnderThePlansLabel(unittest.TestCase):
    """`0033` R3, R4, R10. The label is read after the gate and picks the configuration;
    the gate is stubbed open, as the review tests below stub it."""

    PLAN = (
        "# Plan: a problem\nIntent: intent.md. Author: t. Status: accepted. Impl: routine.\n\n"
        "## Files that change\n\n- {path}: a change.\n\n## Order of work\n\n1. Do it.\n"
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "work" / "proj"
        self.repo.mkdir(parents=True)
        self.service = Service(
            Config(
                workspaces=(str(self.repo),),
                working_dir=str(self.root / "work"),
                data_dir=str(self.root / "data"),
            ),
            self.Impl(self),
        )
        self.made = create_sync(self.service, str(self.repo), "a-problem", "some words")
        self.dir = Path(self.made["path"])
        self.seen: list[dict] = []
        self.terminal = None

    class Impl:
        def __init__(self, test):
            self.test = test

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            self.test.seen.append(kw)
            (self.test.dir / "impl.md").write_text("# Impl: x\nStatus: accepted.\n", encoding="utf-8")
            yield ("done", {"session_id": "sess-i", "cost": {}, "terminal_reason": self.test.terminal})

    def _run(self):
        from coscc import board as board_reader

        async def open_gate(units_root, unit, stage, repo=None, **kw):
            return True, "open: impl may proceed"

        async def go():
            return [i async for i in self.service.run_step(str(self.repo), self.made["unit"], "impl")]

        with mock.patch.object(board_reader, "gate", open_gate):
            return asyncio.run(go())

    def _starts(self):
        journal = self.service._journal()
        return journal.records(self.service._journal_key(str(self.repo)), kind="start")

    def test_a_plan_naming_the_security_surface_runs_as_novel(self):
        (self.dir / "plan.md").write_text(self.PLAN.format(path="`coscc/policy.py`"), encoding="utf-8")
        self._run()
        start = self._starts()[-1]
        self.assertEqual((start["label_declared"], start["label"], start["label_source"]),
                         ("routine", "novel", "forced"))
        self.assertEqual((start["model"], start["effort"]), ("claude-opus-5-5[1m]", "high"))
        self.assertEqual(self.seen[-1].get("effort"), "high")
        self.assertEqual(start["impl_run"], 1)

    def test_a_routine_run_escalates_after_a_max_turns_stop_and_counts_its_runs(self):
        (self.dir / "plan.md").write_text(self.PLAN.format(path="`coscc/board.py`"), encoding="utf-8")
        self.terminal = "max_turns"
        self._run()
        self.terminal = None
        self._run()
        first, second = self._starts()[-2:]
        self.assertEqual((first["label"], first["label_source"], first["model"], first["effort"]),
                         ("routine", "declared", "claude-sonnet-5[1m]", "medium"))
        self.assertEqual((second["label"], second["label_source"], second["model"]),
                         ("novel", "escalated", "claude-opus-5-5[1m]"))
        self.assertEqual((first["impl_run"], second["impl_run"]), (1, 2))


class TheNextStageComesFromTheScript(unittest.TestCase):
    """`0024`. `Service.next_step` asks `cos.mjs next` and chooses nothing itself."""

    # The fixture of the class above, borrowed rather than inherited so its tests run once.
    NeverCalled = AStepTheGateClosesNeverStarts.NeverCalled
    setUp = AStepTheGateClosesNeverStarts.setUp
    _run = AStepTheGateClosesNeverStarts._run

    def _next(self, unit: str | None = None, cwd: str | None = None):
        return asyncio.run(
            self.service.next_step(
                str(self.repo) if cwd is None else cwd,
                self.made["unit"] if unit is None else unit,
            )
        )

    def test_the_stage_is_the_scripts(self):
        got = self._next()
        self.assertEqual(got["stage"], "spec")
        self.assertIn("write-spec", got["action"])
        self.assertEqual(self.sessions.calls, 0)

    def test_a_missing_workspace_or_unit_is_refused(self):
        for cwd, unit in (("", None), (None, ""), ("/etc", None)):
            with self.subTest(cwd=cwd, unit=unit), self.assertRaises(Invalid):
                self._next(unit=unit, cwd=cwd)

    def test_a_unit_that_is_not_there_or_not_a_name_is_invalid(self):
        for unit in ("0099_not-here", "../escape"):
            with self.subTest(unit=unit), self.assertRaises(Invalid):
                self._next(unit=unit)

    def test_next_reads_the_same_checkout_the_gate_reads(self):
        from coscc import board as board_reader

        seen = {}

        async def fake_next(units_root, unit, repo=None, **kw):
            seen["next"] = (str(units_root), repo)
            return {"unit": unit, "stage": "review", "action": "a", "blocked": True}

        async def fake_gate(units_root, unit, stage, repo=None, **kw):
            seen["gate"] = (str(units_root), repo)
            return False, "blocked: stop here"

        with mock.patch.object(board_reader, "next_step", fake_next), \
                mock.patch.object(board_reader, "gate", fake_gate):
            stage = self._next()["stage"]
            with self.assertRaises(Invalid):
                self._run(stage)
        self.assertEqual(seen["next"], seen["gate"])
        self.assertEqual(seen["next"][1], str(self.repo))

    def test_waiting_is_copied_and_absent_reads_as_none(self):
        """`0028`. The findings a person is awaited on reach the page as `cos.mjs` named them."""
        from coscc import board as board_reader

        answers = [
            {"unit": "u", "stage": "", "action": "needs a person — F3: x", "blocked": True, "waiting": ["F3"]},
            {"unit": "u", "stage": "review", "action": "a", "blocked": True},
        ]

        async def fake_next(units_root, unit, repo=None, **kw):
            return answers.pop(0)

        with mock.patch.object(board_reader, "next_step", fake_next):
            first, second = self._next(), self._next()
        self.assertEqual((first["stage"], first["waiting"]), ("", ["F3"]))
        self.assertEqual(second["waiting"], [])


REVIEW_ONE = (
    "# Review: a problem\nAuthor: t. Status: changes-requested.\n\n"
    "## Round 1\n\nReviewed: abcdef1. Verdict: changes-requested.\n\n"
    "### Findings\n\n- F1 [open] [high] the first thing\n- F2 [open] the second thing\n"
)
ROUND_TWO = (
    "\n## Round 2\n\nReviewed: abcdef2. Verdict: changes-requested.\n\n"
    "### Findings\n\n- F1 [fixed abcdef2] the first thing\n- F2 [open] the second thing\n"
)
PR_URL = "https://github.com/o/r/pull/7"


class FakeGh:
    """Stands in for `prcomment._gh`: records argv, keeps the PR's comments in memory."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[list[str]] = []
        self.comments: list[dict] = []

    async def __call__(self, argv, cwd, stdin):
        import json

        self.calls.append(list(argv))
        if self.fail:
            return 1, "", "HTTP 401: Bad credentials"
        if argv[:2] == ["pr", "view"]:
            return 0, json.dumps({"comments": self.comments}), ""
        if argv[:2] == ["pr", "comment"]:
            url = f"{PR_URL}#issuecomment-{len(self.comments) + 1}"
            self.comments.append({"body": stdin, "url": url})
            return 0, url + "\n", ""
        return 2, "", "unexpected"

    def posts(self):
        return [c for c in self.calls if c[:2] == ["pr", "comment"]]


class ReviewRoundsReachThePullRequest(unittest.TestCase):
    """`0021`. A round the app writes is posted; any round can be posted again, once."""

    class Reviews:
        """A review session that adds round 2 to the round `review.md` held."""

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            yield ("chunk", REVIEW_ONE + ROUND_TWO)
            yield ("done", {"session_id": "sess-r", "cost": {}})

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "work" / "proj"
        self.repo.mkdir(parents=True)
        self.service = Service(
            Config(
                workspaces=(str(self.repo),),
                working_dir=str(self.root / "work"),
                data_dir=str(self.root / "data"),
            ),
            self.Reviews(),
        )
        self.made = create_sync(self.service,str(self.repo), "a-problem", "some words")
        self.dir = Path(self.made["path"])
        (self.dir / "pr.md").write_text(
            f"# PR: a problem\nAuthor: t. Status: accepted.\nPR: {PR_URL}\n", encoding="utf-8"
        )
        (self.dir / "review.md").write_text(REVIEW_ONE, encoding="utf-8")
        self.unit = self.made["unit"]

    def _post(self, gh, n):
        from coscc import prcomment

        with mock.patch.object(prcomment, "_gh", gh):
            return asyncio.run(self.service.post_review_comment(str(self.repo), self.unit, n))

    def _run_review(self, gh):
        from coscc import board as board_reader
        from coscc import prcomment

        async def open_gate(units_root, unit, stage, repo=None, **kw):
            return True, "open: review may proceed"

        async def go():
            out = []
            async for item in self.service.run_step(str(self.repo), self.unit, "review"):
                out.append(item)
            return out

        with mock.patch.object(board_reader, "gate", open_gate), \
                mock.patch.object(prcomment, "_gh", gh):
            return asyncio.run(go())

    def _pr_rows(self):
        from coscc.journal import Journal

        j = Journal(self.service.config.working_dir, self.service.config.data_dir)
        return j.records(str(self.repo.resolve()), kind="pr-comment")

    def _rounds(self):
        [u] = asyncio.run(self.service.board(str(self.repo)))["units"]
        return {r["n"]: r["comment"] for r in u["rounds"]}

    # R2
    def test_a_round_the_step_writes_is_posted_once_with_its_own_text(self):
        gh = FakeGh()
        _, done = self._run_review(gh)[-1]
        self.assertEqual(done["outcome"], "done")
        self.assertEqual(len(gh.posts()), 1)
        body = gh.comments[0]["body"]
        self.assertIn("round 2 of", body.splitlines()[0])
        self.assertIn("- F1 [fixed abcdef2] the first thing", body)
        self.assertEqual([(c["round"], c["state"]) for c in done["comments"]], [(2, "posted")])

    def test_the_end_record_counts_the_findings_of_the_added_round(self):
        # `0033` R10: round 2 has two findings, one of them still open.
        from coscc.journal import Journal

        self._run_review(FakeGh())
        j = Journal(self.service.config.working_dir, self.service.config.data_dir)
        end = j.records(str(self.repo.resolve()), kind="end")[-1]
        self.assertEqual((end["outcome"], end["findings"], end["findings_open"]), ("done", 2, 1))

    # R6
    def test_a_failed_post_leaves_review_md_byte_for_byte_the_same(self):
        self._run_review(FakeGh())
        good = (self.dir / "review.md").read_bytes()
        (self.dir / "review.md").write_text(REVIEW_ONE, encoding="utf-8")
        _, done = self._run_review(FakeGh(fail=True))[-1]
        self.assertEqual((self.dir / "review.md").read_bytes(), good)
        self.assertEqual(done["outcome"], "done")
        self.assertEqual(done["comments"][0]["state"], "failed")
        self.assertIn("Bad credentials", done["comments"][0]["reason"])

    def test_another_stage_never_calls_gh(self):
        from coscc import prcomment

        class Spec:
            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                yield ("chunk", "# Spec: a problem\nAuthor: t. Status: accepted.\n\n## Body\n")
                yield ("done", {"session_id": "s", "cost": {}})

        (self.dir / "intent.md").write_text(
            "# Intent: a problem\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8"
        )
        self.service.sessions = Spec()
        gh = FakeGh()

        async def go():
            async for _ in self.service.run_step(str(self.repo), self.unit, "spec"):
                pass

        with mock.patch.object(prcomment, "_gh", gh):
            asyncio.run(go())
        self.assertEqual(gh.calls, [])

    # R7
    def test_the_board_says_which_round_is_not_on_the_pr(self):
        (self.dir / "review.md").write_text(REVIEW_ONE + ROUND_TWO, encoding="utf-8")
        self._post(FakeGh(), 1)
        rounds = self._rounds()
        self.assertTrue(rounds[1]["posted"])
        self.assertTrue(rounds[1]["url"].startswith(PR_URL))
        self.assertEqual(rounds[2], {"posted": False, "url": "", "reason": None})

    def test_a_failed_attempt_shows_its_reason_until_one_succeeds(self):
        self._post(FakeGh(fail=True), 1)
        self.assertEqual(self._rounds()[1]["reason"], "HTTP 401: Bad credentials")
        self._post(FakeGh(), 1)
        self.assertTrue(self._rounds()[1]["posted"])

    # R8
    def test_posting_again_never_makes_a_second_comment(self):
        gh = FakeGh()
        first = self._post(gh, 1)
        second = self._post(gh, 1)
        self.assertEqual((first["state"], second["state"]), ("posted", "already"))
        self.assertEqual(len(gh.posts()), 1)

    def test_two_presses_at_once_still_make_one_comment(self):
        from coscc import prcomment

        gh = FakeGh()

        async def both():
            return await asyncio.gather(
                self.service.post_review_comment(str(self.repo), self.unit, 1),
                self.service.post_review_comment(str(self.repo), self.unit, 1),
            )

        with mock.patch.object(prcomment, "_gh", gh):
            got = asyncio.run(both())
        self.assertEqual(sorted(r["state"] for r in got), ["already", "posted"])
        self.assertEqual(len(gh.posts()), 1)

    def test_a_round_that_is_not_there_is_refused(self):
        with self.assertRaises(Invalid):
            self._post(FakeGh(), 5)
        with self.assertRaises(Invalid):
            self._post(FakeGh(), "one")

    def test_no_pr_line_is_a_failure_with_a_reason_and_no_gh(self):
        (self.dir / "pr.md").write_text("# PR: a problem\nAuthor: t. Status: accepted.\n")
        gh = FakeGh()
        r = self._post(gh, 1)
        self.assertEqual((r["state"], r["reason"]), ("failed", "pr.md names no pull request"))
        self.assertEqual(gh.calls, [])

    # R13
    def test_every_attempt_is_one_run_log_row(self):
        self._post(FakeGh(), 1)
        self._post(FakeGh(fail=True), 1)
        rows = self._pr_rows()
        self.assertEqual(len(rows), 2)
        ok, bad = rows
        self.assertEqual(
            (ok["unit"], ok["round"], ok["pr"], ok["outcome"], ok["stage"]),
            (self.unit, 1, PR_URL, "posted", "review"),
        )
        self.assertTrue(ok["comment_url"].startswith(PR_URL))
        self.assertEqual((bad["outcome"], bad["detail"]), ("failed", "HTTP 401: Bad credentials"))

    def test_a_comment_row_does_not_disturb_the_cost_timeline(self):
        self._post(FakeGh(), 1)
        tl = self.service.timeline(str(self.repo), self.unit)
        self.assertEqual(tl.get("runs") or [], [])


class ShipRunsOutsideTheWorktree(unittest.TestCase):
    """`0017` review F3: inside a worktree, `gh pr merge --delete-branch` merged and then
    exited 1 on `'main' is already used by worktree`. Only `ship`'s session moves."""

    def test_ship_runs_in_the_units_directory(self):
        self.assertEqual(step_cwd("ship", "/w/tree", Path("/store/0017_x")), "/store/0017_x")

    def test_every_other_stage_keeps_the_worktree(self):
        for stage in ("idea", "intent", "spec", "plan", "impl", "pr", "review"):
            self.assertEqual(step_cwd(stage, "/w/tree", Path("/store/0017_x")), "/w/tree")


class TheOutcomeLabel(unittest.TestCase):
    """`0047` R8. One pure decision, every branch, with a fixed `today`."""

    TODAY = date(2026, 10, 8)

    def label(self, deadline="2026-10-07", result=None, finished=False, **over):
        outcome = {"deadline": deadline, "result": result, "by": None, "date": None,
                   "measured_by": None, "source": None, "reason": None, "note": None,
                   "invalid": 0, **over}
        return outcome_label(outcome, self.TODAY, finished=finished)

    def test_met_missed_and_unmeasurable_whatever_the_deadline(self):
        for deadline in ("2026-10-07", "2026-10-31", None):
            met, missed, unmeasurable = (
                self.label(deadline, r) for r in ("met", "missed", "unmeasurable")
            )
            self.assertEqual((met["text"], met["color"], met["counted"], met["hint"]),
                             ("đạt", "grass", True, ""))
            self.assertEqual((missed["text"], missed["color"], missed["counted"], missed["hint"]),
                             ("trượt", "red", True, "cân nhắc bỏ hoặc làm lại"))
            self.assertEqual((unmeasurable["text"], unmeasurable["color"], unmeasurable["counted"]),
                             ("không đo được", "amber", False))
            self.assertEqual(met["deadline"], deadline)

    def test_no_result_is_due_on_and_after_the_deadline_and_pending_before(self):
        self.assertEqual(self.label("2026-10-07")["text"], "tới hạn — chưa đo")
        self.assertEqual(self.label("2026-10-08")["text"], "tới hạn — chưa đo")
        self.assertEqual(self.label("2026-10-08")["color"], "amber")
        self.assertFalse(self.label("2026-10-08")["counted"])
        pending = self.label("2026-10-31")
        self.assertEqual((pending["text"], pending["color"], pending["counted"]),
                         ("chưa tới hạn", "gray", False))

    def test_no_deadline_and_no_result_is_no_label(self):
        self.assertIsNone(self.label(None))
        self.assertIsNone(outcome_label(None, self.TODAY))

    def test_the_form_follows_finished_and_the_block_fields_are_carried(self):
        got = self.label(result="met", finished=True, by="Linh", measured_by="agent",
                         source="npm test", invalid=2)
        self.assertTrue(got["form"])
        self.assertFalse(self.label()["form"])
        self.assertEqual((got["by"], got["measured_by"], got["source"], got["invalid"]),
                         ("Linh", "agent", "npm test", 2))


OUTCOME_INTENT = (
    "# Intent: q\n"
    "Author: t. Type: feat. Status: accepted.\n\n"
    # A deadline already past on any day these tests run, so the board reads it as due.
    "## Proposed outcome\n\nBy 2026-09-01, three of three.\n\n"
    "## Open questions\n\n1. One?\n\n"
    "## Answers\n\n### Câu 1\nAnswered by: Phong. Date: 2026-09-24. Via: product.\n\nCó.\n"
)


class RecordingAnOutcome(unittest.TestCase):
    """`0047` R1–R4, R7, R9 through the one place logic lives."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.cwd = str(root / "work" / "proj")
        Path(self.cwd).mkdir(parents=True)
        self.data_dir = root / "data"
        config = Config(workspaces=(self.cwd,), working_dir=str(root / "work"),
                        data_dir=str(self.data_dir))
        self.service = Service(config, Sessions(config))
        made = create_sync(self.service, self.cwd, "a-problem", "x")
        self.unit = made["unit"]
        self.dir = Path(made["path"])
        for stage in ("spec", "impl", "pr", "review", "ship"):
            (self.dir / f"{stage}.md").write_text(f"# {stage}\nStatus: accepted.\n", encoding="utf-8")
        (self.dir / "plan.md").write_text("# plan\nStatus: done.\n", encoding="utf-8")
        self.intent = self.dir / "intent.md"
        self.intent.write_text(OUTCOME_INTENT, encoding="utf-8")

    def record(self, **over):
        kw = {"result": "đạt", "measured_by": "agent", "source": "npm test, 12 pass",
              "reason": "", "note": "", "recorded_by": "Phong", **over}
        return asyncio.run(self.service.record_outcome(self.cwd, self.unit, **kw))

    def board_unit(self):
        [u] = asyncio.run(self.service.board(self.cwd))["units"]
        return u

    def refused(self, **over) -> str:
        before = hashlib.sha256(self.intent.read_bytes()).hexdigest()
        with self.assertRaises(Invalid) as e:
            self.record(**over)
        self.assertEqual(hashlib.sha256(self.intent.read_bytes()).hexdigest(), before)
        self.assertTrue(str(e.exception))
        return str(e.exception)

    def test_a_block_is_appended_and_every_byte_before_it_stays(self):
        before = self.intent.read_bytes()
        got = self.record(note="Ghi chú.")
        after = self.intent.read_bytes()
        self.assertEqual(after[:len(before)], before)
        tail = after[len(before):].decode("utf-8")
        self.assertIn("\n### Outcome\nAnswered by: Phong. Date: ", tail)
        self.assertIn("Result: đạt\nMeasured by: agent\nSource: npm test, 12 pass\n\nGhi chú.\n", tail)
        self.assertNotIn("## Answers", tail, "the existing heading is reused")
        self.assertEqual((got["result"], got["measured_by"], got["recorded_by"]), ("đạt", "agent", "Phong"))
        self.assertLessEqual({p.name for p in self.dir.iterdir()}, {f"{s}.md" for s in STAGE_FILES})

    def test_the_board_then_reads_it_and_the_last_block_is_in_force(self):
        self.record()
        u = self.board_unit()
        self.assertEqual(u["outcome"]["result"], "met")
        self.assertEqual(u["outcome_label"]["text"], "đạt")
        self.assertTrue(u["outcome_label"]["form"])
        self.record(result="trượt", measured_by="Linh", source="board, 2026-10-08")
        u = self.board_unit()
        self.assertEqual((u["outcome"]["result"], u["outcome"]["measured_by"]), ("missed", "Linh"))
        self.assertEqual(u["outcome_label"]["hint"], "cân nhắc bỏ hoặc làm lại")
        self.assertEqual(u["next"], "finished")
        self.assertEqual((u["questions"][0]["answered"], u["open"]), (True, 0))

    def test_unmeasurable_carries_its_reason_and_no_source_line(self):
        self.record(result="không đo được", source="", reason="không có script")
        text = self.intent.read_text(encoding="utf-8")
        self.assertIn("Result: không đo được\nMeasured by: agent\nReason: không có script\n", text)
        self.assertEqual(self.board_unit()["outcome"]["result"], "unmeasurable")

    def test_every_refusal_writes_nothing(self):
        self.assertIn("unknown", self.refused(result="unknown"))
        self.assertIn("source", self.refused(source=""))
        self.assertIn("source", self.refused(result="trượt", source=" "))
        self.assertIn("reason", self.refused(result="không đo được", source="", reason=""))
        self.refused(recorded_by="  ")
        self.refused(recorded_by="A\nStatus: rejected")
        self.refused(measured_by="")
        self.refused(measured_by="agent\nResult: đạt")
        self.refused(source="a\nb")
        self.refused(reason="a\nb", result="không đo được")
        self.refused(note="ok\n## Status: rejected")
        self.refused(note="### Outcome")
        self.refused(source="# x")
        self.assertIn("no such work unit", asyncio.run(self._missing()))

    async def _missing(self) -> str:
        try:
            await self.service.record_outcome(self.cwd, "0099_nothing", "đạt", "agent", "x", "", "", "P")
        except Invalid as e:
            return str(e)
        return ""

    def test_an_unfinished_unit_is_refused(self):
        (self.dir / "plan.md").write_text("# plan\nStatus: accepted.\n", encoding="utf-8")
        (self.dir / "ship.md").unlink()
        self.assertIn("only on a finished unit", self.refused())
        self.assertFalse(self.board_unit()["outcome_label"]["form"])

    def test_a_section_after_answers_is_refused(self):
        self.intent.write_text(OUTCOME_INTENT + "\n## Notes\n\nx\n", encoding="utf-8")
        self.assertIn("section after its ## Answers", self.refused())

    def test_an_intent_with_no_answers_gets_the_heading_once(self):
        self.intent.write_text(OUTCOME_INTENT.split("## Answers")[0].rstrip("\n"), encoding="utf-8")
        self.record()
        self.record()
        text = self.intent.read_text(encoding="utf-8")
        self.assertEqual(text.count("## Answers"), 1)
        self.assertEqual(text.count("### Outcome"), 2)
        self.assertEqual(self.board_unit()["outcome"]["invalid"], 0)

    def test_the_block_is_recorded_as_a_person_in_the_history(self):
        from coscc.history import History

        self.record()
        rows = History(str(Path(self.cwd).parent), self.data_dir).outputs(
            str(Path(self.cwd).resolve()), self.unit
        )
        mine = [r for r in rows if r["source"] == "outcome"]
        self.assertEqual(len(mine), 1)
        self.assertEqual((mine[0]["actor"], mine[0]["path"]), ("human:Phong", "intent.md"))

    def test_r9_reading_an_overdue_board_writes_no_row_and_starts_nothing(self):
        journal = self.service._journal()
        key = self.service._journal_key(self.cwd)
        before = len(journal.records(key))
        self.assertEqual(self.board_unit()["outcome_label"]["kind"], "due")
        self.board_unit()
        self.assertEqual(len(journal.records(key)), before)
        self.assertEqual(self.service.sessions_for(self.cwd)["sessions"], [])
