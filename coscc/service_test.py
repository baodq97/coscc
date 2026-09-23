"""Tests for the one place logic lives.

These exist because `spec.md` R10 is a structural rule with no automated enforcement: if
the page and the API drift apart, it will be because someone put a decision in one of them.
Testing the service directly — with no web framework in the test — is what makes that
drift visible as a missing test rather than as a bug only one entry point has.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc import harness, units
from coscc.config import Config
from coscc.service import Invalid, Service
from coscc.sessions import Live, Sessions

REPO = str(Path(__file__).resolve().parent.parent)


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
        made = self.service.create_unit(str(self.repo), "a-first-problem", "some words")
        board = asyncio.run(self.service.board(str(self.repo)))
        self.assertEqual([u["name"] for u in board["units"]], [made["unit"]])

    def test_nothing_of_it_lands_in_the_repository(self):
        # `0013`'s decision, enforced. R2, and the whole reason the store exists.
        self.service.create_unit(str(self.repo), "a-problem", "some words")
        self.assertEqual(self._git("status", "--porcelain"), "")
        self.assertFalse((self.repo / ".cos").exists())

    def test_a_unit_takes_no_number_the_host_repository_already_used(self):
        """`0001_product-describes-a-state-it-is-not-in` R10: `0015`, not `0001`."""
        for i in range(1, 15):
            (self.repo / ".cos" / f"{i:04d}_u{i}").mkdir(parents=True)
        before = sorted(p.name for p in (self.repo / ".cos").iterdir())
        made = self.service.create_unit(str(self.repo), "fresh", "some words")
        self.assertEqual(made["unit"], "0015_fresh")
        self.assertEqual(sorted(p.name for p in (self.repo / ".cos").iterdir()), before)

    def test_a_bad_slug_comes_back_as_a_refusal_not_an_exception(self):
        with self.assertRaises(Invalid) as caught:
            self.service.create_unit(str(self.repo), "Bad_Slug")
        self.assertIn("Bad_Slug", str(caught.exception))

    def test_the_gate_applies_to_creating_and_to_branching(self):
        with self.assertRaises(Invalid):
            self.service.create_unit("/etc", "a-problem")
        with self.assertRaises(Invalid):
            asyncio.run(self.service.start_branch("/etc", "0001_a-problem"))

    def test_the_branch_is_refused_until_the_intent_says_what_type_this_is(self):
        made = self.service.create_unit(str(self.repo), "a-problem", "some words")
        with self.assertRaises(Invalid) as caught:
            asyncio.run(self.service.start_branch(str(self.repo), made["unit"]))
        self.assertIn("intent.md", str(caught.exception))

    def test_the_branch_name_is_the_one_the_intents_type_implies(self):
        made = self.service.create_unit(str(self.repo), "a-problem", "some words")
        directory = Path(made["path"])
        (directory / "intent.md").write_text(
            "# Intent: a problem\nAuthor: t. Type: fix. Status: accepted.\n", encoding="utf-8"
        )
        got = asyncio.run(self.service.start_branch(str(self.repo), made["unit"]))
        self.assertEqual(got["branch"], "fix/a-problem")
        self.assertEqual(
            asyncio.run(self.service.branch_here(str(self.repo)))["branch"], "fix/a-problem"
        )

    def test_cutting_the_same_branch_twice_is_refused_rather_than_rejoined(self):
        made = self.service.create_unit(str(self.repo), "a-problem", "some words")
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
        self.service.create_unit(str(self.repo), "a-problem", "some words")
        board = asyncio.run(self.service.board(str(self.repo)))
        self.assertNotIn("empty", board)

    # --- `0001_product-describes-a-state-it-is-not-in` R1, R2, R3 ----------------

    def _typed_unit(self, slug: str = "a-problem") -> str:
        made = self.service.create_unit(str(self.repo), slug, "some words")
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
        """Plan Risk 5, second half: `switch -c` refuses, and creates no branch."""
        self._advance_remote("README.md", "changed elsewhere\n")
        (self.repo / "README.md").write_text("edited here\n", encoding="utf-8")
        unit = self._typed_unit()
        with self.assertRaises(Invalid):
            asyncio.run(self.service.start_branch(str(self.repo), unit))
        self.assertEqual(self._git("branch", "--list", "fix/a-problem").strip(), "")
        self.assertEqual((self.repo / "README.md").read_text(encoding="utf-8"), "edited here\n")


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
        self.made = self.service.create_unit(str(self.repo), "a-problem", "some words")
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
        self.made = self.service.create_unit(str(self.repo), "a-problem", "some words")
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
        self.made = self.service.create_unit(str(self.repo), "a-problem", "some words")
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
