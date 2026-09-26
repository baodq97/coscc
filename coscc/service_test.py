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

from coscc import events as events_mod
from coscc import fetches, gitops, harness, units, worktrees
from coscc.config import Config
from coscc.service import (
    STAGE_FILES, STATE_COLOR, STATE_LABEL, Invalid, Service, attention_reason, describe_base, outcome_label,
    reason_beside, shown_state, step_cwd, unit_state,
)
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


class UsageCountsWhatItCouldNotAdd(unittest.TestCase):
    """`0092` R7. The workspace's cost adds what is known and counts, per unit and in all, the
    `end` rows that carried no `cost_usd`."""

    def test_unknown_is_counted_per_unit_and_in_the_total(self):
        rows = [
            {"kind": "start", "unit": "0002_a"},
            {"kind": "end", "unit": "0002_a", "turns": 4, "cost_usd": 0.52},
            {"kind": "end", "unit": "0002_a", "turns": 109, "cost_unknown": True},
            {"kind": "end", "unit": "0004_b", "cost_unknown": True},
            {"kind": "end", "unit": "0005_c", "cost_usd": 1.0},
            {"kind": "attempt", "unit": "0005_c"},
        ]
        got = _service()._usage_of("w", rows)
        self.assertEqual((got["total"]["cost_usd"], got["total"]["unknown"]), (1.52, 2))
        self.assertEqual(got["total"]["turns"], 113)
        self.assertEqual({u: b["unknown"] for u, b in got["per_unit"].items()},
                         {"0002_a": 1, "0004_b": 1, "0005_c": 0})


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


class AStepOutlivesItsReaderAndCanBeStopped(unittest.TestCase):
    """`0034`. A step is its own task: a reader leaving does not end it (R3), a second
    step on one unit is refused before it spends anything (R11), two units run at once
    (R12), the page can list them (R13), and a Stop ends one `stopped` with nothing
    recorded after it (R5, R9)."""

    class Waits:
        def __init__(self):
            self.release: dict[str, asyncio.Event] = {}
            self.calls = 0

        def gate(self, unit):
            return self.release.setdefault(unit, asyncio.Event())

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            self.calls += 1
            step = kw.get("step")
            # The prompt names its unit; `cwd` is the workspace for a prose stage.
            [unit] = [u for u in self.units if u in text]
            try:
                yield ("chunk", "# Spec: a problem\n")
                await asyncio.wait_for(self.gate(unit).wait(), 10)
                yield ("chunk", "Author: t. Status: accepted.\n")
                yield ("done", {"session_id": "sess-7", "terminal_reason": "success",
                                "cost": {"output_tokens": 3, "cost_usd": 0.01}})
            finally:
                if step is not None:
                    await step.close()

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
        self.sessions = self.Waits()
        self.service = Service(config, self.sessions)
        self.units = []
        for slug in ("a-problem", "b-problem"):
            made = create_sync(self.service, str(self.repo), slug, "some words")
            (Path(made["path"]) / "intent.md").write_text(
                "# Intent: x\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8"
            )
            self.units.append(made)
        self.sessions.units = [u["unit"] for u in self.units]
        self.ws = str(self.repo)

    def _release(self, made):
        self.sessions.gate(made["unit"]).set()

    async def _first_chunk(self, made):
        agen = self.service.run_step(self.ws, made["unit"], "spec")
        first = await agen.__anext__()
        self.assertEqual(first[0], "chunk")
        return agen

    def _ends(self, unit):
        journal = self.service._journal()
        return [r for r in journal.records() if r["kind"] == "end" and r["unit"] == unit]

    def test_a_reader_that_leaves_does_not_take_the_step_with_it(self):
        a = self.units[0]

        async def go():
            agen = await self._first_chunk(a)
            await agen.aclose()  # the NDJSON client went away
            running = self.service.steps.get(self.service._journal_key(self.ws), a["unit"])
            self.assertIsNotNone(running)
            self._release(a)
            await running.task

        asyncio.run(go())
        self.assertIn("Status: accepted.", (Path(a["path"]) / "spec.md").read_text())
        [end] = self._ends(a["unit"])
        self.assertEqual(end["outcome"], "done")

    def test_a_second_step_on_one_unit_is_refused_and_two_units_run_at_once(self):
        a, b = self.units

        async def go():
            first = await self._first_chunk(a)
            with self.assertRaises(Invalid):
                await self.service.run_step(self.ws, a["unit"], "spec").__anext__()
            self.assertEqual(self.sessions.calls, 1)  # refused before a session
            second = await self._first_chunk(b)
            listed = self.service.running_steps(self.ws)
            self.assertEqual(sorted(r["unit"] for r in listed), sorted([a["unit"], b["unit"]]))
            self._release(a)
            self._release(b)
            outs = [[i async for i in g] for g in (first, second)]
            self.assertEqual([o[-1][1]["outcome"] for o in outs], ["done", "done"])
            self.assertEqual(self.service.running_steps(self.ws), [])

        asyncio.run(go())

    def test_a_stop_ends_the_step_stopped_and_records_nothing_after_it(self):
        a = self.units[0]

        async def go():
            agen = await self._first_chunk(a)
            said = await self.service.stop_step(self.ws, a["unit"], "Lan")
            self.assertEqual(said, {"unit": a["unit"], "stage": "spec", "stopped_by": "Lan"})
            # A second press is the same stop.
            running = self.service.steps.get(self.service._journal_key(self.ws), a["unit"])
            if running is not None:
                await self.service.stop_step(self.ws, a["unit"], "Minh")
            rest = [i async for i in agen]
            return rest

        rest = asyncio.run(go())
        self.assertEqual(rest[-1][1]["outcome"], "stopped")
        self.assertEqual(rest[-1][1]["stopped_by"], "Lan")
        self.assertFalse((Path(a["path"]) / "spec.md").exists())
        [end] = self._ends(a["unit"])
        self.assertEqual((end["outcome"], end["stopped_by"]), ("stopped", "Lan"))
        found = self.service.unit_history(self.ws, a["unit"])
        self.assertEqual([r for r in found["transitions"] if r["artifact"] == "spec.md"], [])
        self.assertIn("Status: accepted.", (Path(a["path"]) / "intent.md").read_text())
        self.assertEqual(self.service.running_steps(self.ws), [])

    def test_stopping_nothing_is_refused_and_no_name_stops_as_owner(self):
        """`0082` R3: a Stop with no name used to be refused; it now records `owner`."""
        a = self.units[0]

        async def go():
            with self.assertRaises(Invalid):
                await self.service.stop_step(self.ws, a["unit"], "Lan")
            agen = await self._first_chunk(a)
            done = await self.service.stop_step(self.ws, a["unit"], "  ")
            self.assertEqual(done["stopped_by"], "owner")
            self._release(a)
            try:
                [i async for i in agen]
            except (Invalid, asyncio.CancelledError):
                pass

        asyncio.run(go())

    def test_shutdown_cancels_a_running_step_and_writes_no_end(self):
        a = self.units[0]

        async def go():
            agen = await self._first_chunk(a)
            await self.service.shutdown()
            with self.assertRaises(Invalid):
                [i async for i in agen]

        asyncio.run(go())
        self.assertEqual(self._ends(a["unit"]), [])
        self.assertEqual(self.service.running_steps(self.ws), [])


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

    def test_the_start_record_names_the_build_that_ran_it(self):
        # `0094` R13, R16: three fields more, and none of the ones already there is lost.
        from unittest import mock

        with mock.patch.object(self.service.updater, "me",
                               lambda: {"version": "9.8.7", "commit": "d" * 40, "shape": "x"}):
            self._run("spec")
        start = self._start()
        self.assertEqual((start["app_version"], start["app_commit"]), ("9.8.7", "d" * 40))
        self.assertEqual(start["pointed"], [])
        for kept in ("included", "prompt_chars", "granted", "max_turns", "head", "model",
                     "model_source", "base", "system_prompt", "instructions"):
            self.assertIn(kept, start)

    def test_a_build_that_cannot_be_read_is_two_empty_strings_and_the_step_runs(self):
        from unittest import mock

        def broken():
            raise OSError("no identity")

        with mock.patch.object(self.service.updater, "me", broken):
            self._run("spec")
        start = self._start()
        self.assertEqual((start["app_version"], start["app_commit"]), ("", ""))
        self.assertEqual(self.probe.models, ["claude-opus-5-5[1m]"])

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

    def test_settings_shows_the_novel_impl_ceilings_after_impl(self):
        """`0062` R9."""
        rows = self.service.settings()["grants"]
        stages = [r["stage"] for r in rows]
        impl, novel = rows[stages.index("impl")], rows[stages.index("impl:novel")]
        self.assertEqual(stages.index("impl:novel"), stages.index("impl") + 1)
        self.assertEqual((novel["max_turns"], novel["max_budget_usd"]), (250, 16.0))
        self.assertEqual((impl["max_turns"], impl["max_budget_usd"]), (120, 8.0))
        for field in ("tools", "commands", "warning"):
            self.assertEqual(novel[field], impl[field], field)
        for stage in ("pr:novel", "review:novel", "ship:novel"):
            self.assertNotIn(stage, stages)

    def test_a_grants_tools_and_commands_are_also_lists(self):
        """`0082` F2: the page lists them; the joined strings stay in the API as they were."""
        for row in self.service.settings()["grants"]:
            self.assertEqual(", ".join(row["tool_list"]) or "none", row["tools"], row["stage"])
            self.assertEqual(", ".join(row["command_list"]) or "none", row["commands"], row["stage"])


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
            self.test.seen.append({**kw, "max_turns": max_turns})
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

    def test_a_novel_impl_gets_the_novel_ceilings(self):
        """`0062` R1 and R7, down the board's whole road."""
        (self.dir / "plan.md").write_text(self.PLAN.format(path="`coscc/policy.py`"), encoding="utf-8")
        self._run()
        start = self._starts()[-1]
        self.assertEqual((start["label"], start["label_source"]), ("novel", "forced"))
        self.assertEqual((self.seen[-1]["max_turns"], self.seen[-1].get("max_budget_usd")),
                         (250, 16.0))
        self.assertEqual(start["max_turns"], 250)

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
        # `0062`: the rerun also gets the ceilings it was escalated for.
        ceilings = [(kw["max_turns"], kw.get("max_budget_usd")) for kw in self.seen[-2:]]
        self.assertEqual(ceilings, [(120, 8.0), (250, 16.0)])


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
            # `0045`: `next_step` first asks with no `--repo` whether the unit is held.
            if repo is None:
                return {"unit": "u", "stage": "", "action": "", "blocked": True, "hold": None}
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

    def test_0093_the_end_record_carries_the_added_rounds_verdicts(self):
        # R9: the one round this step added, round 2, asks for changes.
        from coscc.journal import Journal

        self._run_review(FakeGh())
        j = Journal(self.service.config.working_dir, self.service.config.data_dir)
        end = j.records(str(self.repo.resolve()), kind="end")[-1]
        self.assertEqual(end["verdicts"], ["changes-requested"])

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

    def test_spike_runs_in_its_scratch(self):
        self.assertEqual(step_cwd("spike", "/w/tree", Path("/store/x"), "/data/spikes/s/x"), "/data/spikes/s/x")


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

    def test_no_recorded_by_is_recorded_as_owner(self):
        """`0082` R3: what `recorded_by="  "` was refused for until then."""
        self.record(recorded_by="  ")
        block = self.intent.read_text(encoding="utf-8").split("### Outcome", 1)[1]
        self.assertIn("Answered by: owner", block)

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


class ASpikeRunsInAScratchTheAppRemoves(unittest.TestCase):
    """`0039` R11, R12: the scratch is emptied before the step and gone after it."""

    class Probe:
        def __init__(self, fail: bool = False):
            self.fail = fail
            self.seen: list[tuple[str, list[str], str | None]] = []

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            self.seen.append((cwd, sorted(p.name for p in Path(cwd).iterdir()), kw.get("workspace")))
            (Path(cwd) / "probe.py").write_text("print(1)\n", encoding="utf-8")
            if self.fail:
                raise RuntimeError("the session broke")
            yield ("chunk", "# Spike: x\nSpec: spec.md. Round: 1. Status: accepted.\n\n"
                            "## U1\n\nVerdict: holds.\n\n```\n$ x\n1\n```\n")
            yield ("done", {"session_id": "sess-spike", "cost": {}})

    class RunsOut:
        """`0080` R3: keeps its progress file in `cwd`, then stops at the turn ceiling."""

        PROGRESS = ("# Spike: x\nSpec: spec.md. Author: ᛈ Perthro. Round: 1. Status: accepted.\n\n"
                    "## U1\n\nVerdict: holds.\n\n```\n$ x\n1\n```\n")

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            (Path(cwd) / "spike.md").write_text(self.PROGRESS, encoding="utf-8")
            yield ("chunk", "Tôi hết lượt.")
            yield ("done", {"session_id": "sess-spike", "terminal_reason": "max_turns", "cost": {}})

    def test_the_progress_file_is_read_before_the_scratch_is_removed(self):
        service = self._service(self.RunsOut())
        out = self._run(service)
        self.assertEqual(out[-1][1]["outcome"], "exhausted", out[-1])
        written = Path(service._unit_dir(str(self.repo), self.unit)) / "spike.md"
        self.assertEqual(written.read_text(encoding="utf-8"), self.RunsOut.PROGRESS)
        self.assertFalse(self.scratch.exists())

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "work" / "proj"
        self.repo.mkdir(parents=True)
        self.tree = self.root / "tree"
        self.tree.mkdir()
        for where in (self.repo, self.tree):
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=where, check=True)
            subprocess.run(
                ["git", "-c", "user.name=T", "-c", "user.email=t@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "first"],
                cwd=where, check=True,
            )

    def _service(self, probe) -> Service:
        service = Service(
            Config(workspaces=(str(self.repo),), working_dir=str(self.root / "work"),
                   data_dir=str(self.root / "data")),
            probe,
        )
        made = create_sync(service, str(self.repo), "a-problem", "some words")
        d = Path(made["path"])
        (d / "intent.md").write_text("# Intent: x\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8")
        (d / "spec.md").write_text(
            "# Spec: x\nIntent: intent.md. Author: t. Status: accepted.\n\n## Concerns\n\n"
            "- [unmeasured] U1. does it exit?\n", encoding="utf-8",
        )
        self.unit = made["unit"]
        self.scratch = units.spike_dir(str(self.repo), self.unit, str(self.root / "data"))
        return service

    def _run(self, service):
        tree = {"path": str(self.tree), "branch": "feat/a-problem", "base": None}

        async def go():
            with mock.patch.object(Service, "_worktree", mock.AsyncMock(return_value=tree)):
                return [i async for i in service.run_step(str(self.repo), self.unit, "spike")]

        return asyncio.run(go())

    def test_the_scratch_is_used_and_then_gone(self):
        probe = self.Probe()
        service = self._service(probe)
        out = self._run(service)
        self.assertEqual(out[-1][1]["outcome"], "done", out[-1])
        self.assertEqual(probe.seen, [(str(self.scratch), [], str(self.repo))])
        self.assertFalse(self.scratch.exists())
        self.assertTrue((Path(service._unit_dir(str(self.repo), self.unit)) / "spike.md").exists())

    def test_the_scratch_is_gone_when_the_session_fails(self):
        probe = self.Probe(fail=True)
        service = self._service(probe)
        out = self._run(service)
        self.assertEqual(out[-1][1]["outcome"], "failed")
        self.assertFalse(self.scratch.exists())

    def test_a_scratch_left_behind_is_emptied_before_the_step(self):
        probe = self.Probe()
        service = self._service(probe)
        self.scratch.mkdir(parents=True)
        (self.scratch / "stale.txt").write_text("old", encoding="utf-8")
        self._run(service)
        self.assertEqual(probe.seen[0][1], [])
        self.assertFalse(self.scratch.exists())

    def test_a_workspace_with_no_git_is_refused(self):
        probe = self.Probe()
        service = self._service(probe)
        shutil.rmtree(self.repo / ".git")

        async def go():
            return [i async for i in service.run_step(str(self.repo), self.unit, "spike")]

        with self.assertRaises(Invalid) as caught:
            asyncio.run(go())
        self.assertIn("spike needs a git worktree", str(caught.exception))
        self.assertEqual(probe.seen, [])


class WhatIsRunningIsKeptWhileItRuns(unittest.TestCase):
    """`0051` plan step 3: one `_running` entry per step, gone however the step ends."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "work" / "proj"
        self.repo.mkdir(parents=True)
        self.seen: list[list[dict]] = []
        test = self

        class Looks:
            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                test.seen.append(list(test.service._running.values()))
                yield ("chunk", "# Spec: a problem\nAuthor: t. Status: accepted.\n\n## Body\n")
                yield ("done", {"session_id": "sess-51", "cost": {}})

        self.service = Service(
            Config(
                workspaces=(str(self.repo),),
                working_dir=str(self.root / "work"),
                data_dir=str(self.root / "data"),
            ),
            Looks(),
        )
        self.made = create_sync(self.service, str(self.repo), "a-problem", "some words")
        self.unit = self.made["unit"]
        (Path(self.made["path"]) / "intent.md").write_text(
            "# Intent: a problem\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8"
        )

    def _run(self, stage: str, stop_after: int | None = None):
        async def go():
            out = []
            agen = self.service.run_step(str(self.repo), self.unit, stage)
            try:
                async for item in agen:
                    out.append(item)
                    if stop_after is not None and len(out) >= stop_after:
                        break
            finally:
                await agen.aclose()
            return out

        return asyncio.run(go())

    def test_one_entry_while_the_step_runs_and_none_after_done(self):
        self._run("spec")
        [[entry]] = self.seen
        self.assertEqual((entry["unit"], entry["stage"], entry["kind"]), (self.unit, "spec", "step"))
        self.assertIsNone(entry["turns"])
        self.assertIsNone(entry["cost_usd"])
        self.assertEqual(self.service._running, {})

    def test_none_after_a_run_error(self):
        from coscc.runner import RunError

        async def fails(*a, **kw):
            self.seen.append(list(self.service._running.values()))
            raise RunError("stand-in")
            yield  # pragma: no cover

        with mock.patch("coscc.service.Runner.run", fails):
            with self.assertRaises(Invalid):
                self._run("spec")
        self.assertEqual(len(self.seen[0]), 1)
        self.assertEqual(self.service._running, {})

    def test_none_after_the_caller_goes_away_mid_step(self):
        self._run("spec", stop_after=1)
        self.assertEqual(len(self.seen[0]), 1)
        self.assertEqual(self.service._running, {})

    def test_a_step_the_gate_refuses_leaves_none(self):
        with self.assertRaises(Invalid):
            self._run("ship")
        self.assertEqual(self.service._running, {})

    def test_a_step_refused_as_busy_leaves_none_and_keeps_the_other(self):
        key = self.service._journal_key(str(self.repo))
        self.service._take(key, self.unit, "step", "spec")
        self.service._running["other"] = {"workspace": key, "unit": self.unit}
        with self.assertRaises(Invalid) as caught:
            self._run("spec")
        self.assertIn("a spec step is being prepared", str(caught.exception))
        self.assertEqual(list(self.service._running), ["other"])


class AStepCanBeWatched(unittest.TestCase):
    """`0073` step 5: `events_page` and `follow_events`, while a step runs and after."""

    N = 800  # refusals the stand-in session reports before it waits

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "work" / "proj"
        self.repo.mkdir(parents=True)
        self.release = None
        test = self

        class Reports:
            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                recorder = kw["step"].recorder
                for i in range(test.N):
                    recorder.denied("Bash", {"command": f"c{i}"}, "not granted")
                await test.release.wait()
                yield ("chunk", "# Spec: a problem\nAuthor: t. Status: accepted.\n\n## Body\n")
                yield ("done", {"session_id": "sess-73", "cost": {}})

        self.service = Service(
            Config(
                workspaces=(str(self.repo),),
                working_dir=str(self.root / "work"),
                data_dir=str(self.root / "data"),
            ),
            Reports(),
        )
        self.made = create_sync(self.service, str(self.repo), "a-problem", "some words")
        self.unit = self.made["unit"]
        self.other = create_sync(self.service, str(self.repo), "another", "words")["unit"]
        for made in (self.made["path"],):
            (Path(made) / "intent.md").write_text(
                "# Intent: a problem\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8"
            )

    def test_pages_and_following_while_running_and_after(self):
        ws = str(self.repo)

        async def go():
            self.release = asyncio.Event()
            reader = asyncio.create_task(self._drain())
            while not self.service._recorders or next(iter(self.service._recorders.values())).seq < self.N:
                await asyncio.sleep(0.01)
            [run] = list(self.service._recorders)
            listed = self.service.running(ws)["running"][self.unit][0]["run"]
            steps_run = self.service.running_steps(ws)[0]["run"]
            live = self.service.events_page(ws, self.unit, run)
            older = self.service.events_page(ws, self.unit, run, before=live["first_seq"], limit=9999)
            one = self.service.events_page(ws, self.unit, run, seq=7)
            with self.assertRaises(Invalid):
                self.service.events_page(ws, self.other, run)
            followed: list[int] = []

            async def follow():
                async for kind, batch in self.service.follow_events(ws, self.unit, run, after=100):
                    self.assertEqual(kind, "events")
                    followed.extend(e["seq"] for e in batch)

            follower = asyncio.create_task(follow())
            await asyncio.sleep(0.05)
            self.release.set()
            await reader
            await asyncio.wait_for(follower, 10)
            after = self.service.events_page(ws, self.unit, run)
            after_older = self.service.events_page(ws, self.unit, run, before=live["first_seq"], limit=9999)
            ended = [i async for i in self.service.follow_events(ws, self.unit, run, after=0)]
            return run, listed, steps_run, live, older, one, followed, after, after_older, ended

        run, listed, steps_run, live, older, one, followed, after, after_older, ended = asyncio.run(go())
        self.assertEqual((listed, steps_run), (run, run))
        self.assertEqual(live["status"], "running")
        self.assertEqual([e["seq"] for e in live["events"]], list(range(self.N - 199, self.N + 1)))
        self.assertTrue(live["has_older"])
        self.assertEqual(len(older["events"]), events_mod.PAGE_MAX)
        self.assertEqual(older["events"][-1]["seq"], live["first_seq"] - 1)
        self.assertEqual([e["seq"] for e in one["events"]], [7])
        # Following from 100: every later event once, in order, ending with `end`.
        self.assertEqual(followed, list(range(101, self.N + 2)))
        self.assertEqual(after["status"], "ended")
        self.assertEqual(after["events"][-1]["kind"], "end")
        # R7: the same pages from the table as from memory.
        self.assertEqual(after["events"][:-1], live["events"][1:])
        self.assertEqual(after_older["events"], older["events"])
        self.assertEqual([k for k, _ in ended], ["status"])
        self.assertEqual(ended[0][1]["status"], "ended")
        self.assertEqual(self.service._recorders, {})
        [start] = [r for r in self.service._journal().records(kind="start")]
        [end] = [r for r in self.service._journal().records(kind="end")]
        self.assertEqual((start["run"], end["run"], end["events_lost"]), (run, run, 0))

    async def _drain(self):
        async for _ in self.service.run_step(str(self.repo), self.unit, "spec"):
            pass

    def test_a_run_nobody_knows_is_refused_and_a_step_the_gate_closes_leaves_no_recorder(self):
        with self.assertRaises(Invalid):
            self.service.events_page(str(self.repo), self.unit, "nope")
        with self.assertRaises(Invalid):
            self.service.events_page(str(self.repo), self.unit, "")

        async def refused():
            async for _ in self.service.run_step(str(self.repo), self.unit, "ship"):
                pass

        with self.assertRaises(Invalid):
            asyncio.run(refused())
        self.assertEqual(self.service._recorders, {})

    def test_a_run_the_run_log_names_with_no_index_row_is_none(self):
        journal = self.service._journal()
        key = self.service._journal_key(str(self.repo))
        journal.started(key, self.unit, "spec", "manual", run="r-lost")
        page = self.service.events_page(str(self.repo), self.unit, "r-lost")
        self.assertEqual((page["status"], page["events"]), ("none", []))


class RunningAnswersFromMemoryAndTheRunLog(unittest.TestCase):
    """`0051` plan step 4: `running` and `unknown_end`, and what keeps them apart."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.repo = root / "work" / "proj"
        self.other = root / "work" / "other"
        self.repo.mkdir(parents=True)
        self.other.mkdir(parents=True)
        config = Config(
            workspaces=(str(self.repo), str(self.other)),
            working_dir=str(root / "work"),
            data_dir=str(root / "data"),
        )
        self.service = Service(config, Sessions(config))
        self.cwd = str(self.repo)
        self.key = self.service._journal_key(self.cwd)
        self.journal = self.service._journal()

    def test_an_entry_and_its_own_start_show_only_as_running(self):
        self.service._mark_running(self.key, "0009_x", "impl", "step")
        self.journal.started(self.key, "0009_x", "impl", "manual")
        got = self.service.running(self.cwd)
        [row] = got["running"]["0009_x"]
        self.assertEqual(row["stage"], "impl")
        self.assertEqual(row["agent"], {"glyph": "ᚢ", "name": "Uruz"})
        self.assertEqual(row["kind"], "step")
        self.assertIsNone(row["turns"])
        self.assertIsNone(row["cost_usd"])
        self.assertEqual(got["unknown_end"], {})

    def test_an_orphan_start_shows_as_ended_unknown(self):
        rec = self.journal.started(self.key, "0009_x", "plan", "manual")
        got = self.service.running(self.cwd)
        self.assertEqual(got["running"], {})
        self.assertEqual(got["unknown_end"], {"0009_x": [{"stage": "plan", "started": rec["at"]}]})

    def test_a_later_start_retires_the_orphan(self):
        self.journal.append({
            "kind": "start", "workspace": self.key, "unit": "0009_x", "stage": "plan",
            "mode": "manual", "at": "2026-09-24T01:00:00+00:00",
        })
        self.journal.started(self.key, "0009_x", "impl", "manual")
        self.journal.finished(self.key, "0009_x", "impl", "done")
        self.assertEqual(self.service.running(self.cwd)["unknown_end"], {})

    def test_an_orphan_older_than_a_day_is_not_shown(self):
        self.journal.append({
            "kind": "start", "workspace": self.key, "unit": "0009_x", "stage": "plan",
            "mode": "manual", "at": "2020-01-01T00:00:00+00:00",
        })
        self.assertEqual(self.service.running(self.cwd)["unknown_end"], {})

    def test_two_workspaces_do_not_mix(self):
        other_key = self.service._journal_key(str(self.other))
        self.service._mark_running(other_key, "0009_x", "spec", "step")
        self.journal.started(other_key, "0010_y", "spec", "manual")
        self.assertEqual(self.service.running(self.cwd), {"running": {}, "unknown_end": {}})
        got = self.service.running(str(self.other))
        self.assertEqual(list(got["running"]), ["0009_x"])
        self.assertEqual(list(got["unknown_end"]), ["0010_y"])

    def test_gebo_is_named_and_a_rebase_is_not(self):
        self.service._mark_running(self.key, "0009_x", "integrate", "gebo")
        self.service._mark_running(self.key, "0010_y", "integrate", "rebase")
        got = self.service.running(self.cwd)["running"]
        self.assertEqual(got["0009_x"][0]["agent"], {"glyph": "ᚷ", "name": "Gebo"})
        self.assertIsNone(got["0010_y"][0]["agent"])
        self.assertEqual(got["0010_y"][0]["kind"], "rebase")

    def test_a_workspace_outside_the_list_is_refused(self):
        with self.assertRaises(Invalid):
            self.service.running("/nonexistent/elsewhere")

    def test_a_busy_run_log_is_a_note_not_a_refusal(self):
        from coscc.journal import Busy, Journal

        self.service._mark_running(self.key, "0009_x", "impl", "step")
        with mock.patch.object(Journal, "open_starts", side_effect=Busy("locked")):
            got = self.service.running(self.cwd)
        self.assertEqual(got["note"], "locked")
        self.assertEqual(list(got["running"]), ["0009_x"])
        self.assertEqual(got["unknown_end"], {})

    def test_no_working_folder_means_no_unknown_end(self):
        config = Config(workspaces=(self.cwd,))
        service = Service(config, Sessions(config))
        self.assertEqual(service.running(self.cwd), {"running": {}, "unknown_end": {}})


class APrStepIsHandedItsPullRequest(unittest.TestCase):
    """`0041` R2, through `run_step`: one `gh pr list` in the unit's tree, before the
    session starts, and its answer in both the prompt and the `start` record. The fixture
    is `AUnitsBaseIsTheRemoteTrunk`'s, with a `gh` first on `PATH`."""

    setUp = AUnitsBaseIsTheRemoteTrunk.setUp
    _git = AUnitsBaseIsTheRemoteTrunk._git
    _typed_unit = AUnitsBaseIsTheRemoteTrunk._typed_unit

    class Replies:
        """A `pr` session: keeps the prompt, writes `pr.md` itself."""

        def __init__(self):
            self.prompt = ""
            self.directory: Path | None = None

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            self.prompt = text
            (self.directory / "pr.md").write_text(
                "# PR: a problem\nAuthor: t. Status: accepted.\nPR: https://x/pull/7\n",
                encoding="utf-8",
            )
            yield ("chunk", "done")
            yield ("done", {"session_id": "sess-41", "cost": {}})

    def _run_pr(self, stdout: str, code: int = 0) -> tuple[str, dict, str]:
        from coscc import board as board_reader
        from coscc.integrate_test import fake_gh, on_path
        from coscc.journal import Journal

        unit = self._typed_unit()
        self._git("branch", "fix/a-problem")
        replies = self.Replies()
        replies.directory = self.service._unit_dir(str(self.repo), unit)
        self.service.sessions = replies

        async def open_gate(units_root, unit, stage, repo=None, **kw):
            return True, "open: pr may proceed"

        async def go():
            last = None
            async for item in self.service.run_step(str(self.repo), unit, "pr"):
                last = item
            return last

        bindir = self.root / "bin"
        log = fake_gh(bindir, stdout, code)
        with on_path(bindir), mock.patch.object(board_reader, "gate", open_gate):
            _, done = asyncio.run(go())
        j = Journal(self.config.working_dir, self.config.data_dir)
        start = j.records(str(self.repo.resolve()), kind="start")[-1]
        self.assertEqual(done["outcome"], "done", done)
        return replies.prompt, start, log.read_text(encoding="utf-8")

    def test_found(self):
        rows = json.dumps([{"url": "https://github.com/o/r/pull/7", "number": 7,
                            "mergeable": "CONFLICTING", "headRefOid": "a" * 40}])
        prompt, start, argv = self._run_pr(rows)
        self.assertIn("pr list --head fix/a-problem --state open", argv)
        self.assertIn("# The pull request, already looked up", prompt)
        self.assertIn("https://github.com/o/r/pull/7", prompt)
        self.assertIn("*Integrate*", prompt)
        self.assertEqual(start["pr_before"], "https://github.com/o/r/pull/7")

    def test_none(self):
        prompt, start, _ = self._run_pr("[]")
        self.assertIn("no open pull request for the branch `fix/a-problem`", prompt)
        self.assertEqual(start["pr_before"], "")

    def test_unknown_still_runs_the_step(self):
        prompt, start, _ = self._run_pr("", code=1)
        self.assertIn("could not ask `gh`", prompt)
        self.assertIn("no auth", prompt)
        self.assertEqual(start["pr_before"], "")


class APrStepPutsPrMdOntoItsPullRequest(unittest.TestCase):
    """`0055` R3–R5, through `run_step`: after a `pr` step that was not stopped, the title
    and body `cos.mjs pr-text` cut from `pr.md` are on the pull request, and one `pr-sync`
    row says how. `APrStepIsHandedItsPullRequest`'s fixture, with `gh` in memory."""

    setUp = AUnitsBaseIsTheRemoteTrunk.setUp
    _git = AUnitsBaseIsTheRemoteTrunk._git
    _typed_unit = AUnitsBaseIsTheRemoteTrunk._typed_unit

    TITLE = "a problem, fixed"
    BODY = "## Where\n\nhttps://github.com/o/r/pull/7, checks pending.\n"
    ACCEPTED = f"# PR: {TITLE}\nIntent: intent.md. PR: https://github.com/o/r/pull/7. Author: t. Status: accepted.\n\n{BODY}"

    class Replies:
        """A session that writes `pr.md` itself; with `hold`, it then waits to be stopped."""

        def __init__(self, text: str | None = None, hold: bool = False):
            self.text, self.hold = text, hold
            self.directory: Path | None = None
            self.reply = "working"

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            step = kw.get("step")
            try:
                if self.text is not None:
                    (self.directory / "pr.md").write_text(self.text, encoding="utf-8")
                yield ("chunk", self.reply)
                if self.hold:
                    await asyncio.sleep(10)
                yield ("done", {"session_id": "sess-55", "cost": {}})
            finally:
                if step is not None:
                    await step.close()

    class Gh:
        """Both `integrate._gh` and `prcomment._gh`: one pull request, in memory."""

        def __init__(self, listed: bool, title: str = "temporary", body: str = "temporary", fail=None, raise_=None):
            self.listed, self.title, self.body = listed, title, body
            self.fail, self.raise_ = fail, raise_
            self.calls: list[tuple[list[str], str | None]] = []

        async def __call__(self, argv, cwd, stdin=None):
            self.calls.append((list(argv), stdin))
            if argv[:2] == ["pr", "list"] and self.fail == "list":
                return 1, "", "error connecting to api.github.com"
            if argv[:2] == ["pr", "list"]:
                rows = [{"url": "https://github.com/o/r/pull/7", "number": 7,
                         "mergeable": "MERGEABLE", "headRefOid": "a" * 40}] if self.listed else []
                return 0, json.dumps(rows), ""
            if self.raise_ is not None:
                raise self.raise_
            if self.fail and argv[:2] == ["pr", self.fail]:
                return 1, "", "HTTP 422: Validation Failed"
            if argv[:2] == ["pr", "view"] and argv[-1] == "comments":
                return 0, json.dumps({"comments": []}), ""
            if argv[:2] == ["pr", "view"]:
                return 0, json.dumps({"title": self.title, "body": self.body}), ""
            if argv[:2] == ["pr", "edit"]:
                self.title = next((a[len("--title="):] for a in argv if a.startswith("--title=")), self.title)
                self.body = stdin
                return 0, "https://github.com/o/r/pull/7\n", ""
            return 0, "", ""

        def of(self, sub: str, json_: str | None = None):
            return [c for c in self.calls if c[0][:2] == ["pr", sub] and (json_ is None or c[0][-1] == json_)]

    def _run(self, text, gh, stage="pr", hold=False, prepare=None):
        from coscc import board as board_reader
        from coscc import integrate, prcomment

        unit = self._typed_unit()
        self._git("branch", "fix/a-problem")
        directory = self.service._unit_dir(str(self.repo), unit)
        if prepare:
            prepare(directory)
        replies = self.Replies(text, hold)
        replies.directory = directory
        self.service.sessions = replies

        async def open_gate(units_root, unit, stage, repo=None, **kw):
            return True, f"open: {stage} may proceed"

        async def go():
            agen = self.service.run_step(str(self.repo), unit, stage)
            out = [await agen.__anext__()]
            if hold:
                await self.service.stop_step(str(self.repo), unit, "Lan")
            out += [i async for i in agen]
            return out

        with mock.patch.object(board_reader, "gate", open_gate), \
                mock.patch.object(integrate, "_gh", gh), mock.patch.object(prcomment, "_gh", gh):
            out = asyncio.run(go())
        rows = self.service._journal().records(self.service._journal_key(str(self.repo)), unit, kind="pr-sync")
        return out[-1][1], rows, directory

    def test_a_pull_request_that_existed_gets_the_title_and_body(self):
        gh = self.Gh(listed=True)
        done, [row], _ = self._run(self.ACCEPTED, gh)
        [(argv, stdin)] = gh.of("edit")
        self.assertEqual(argv, ["pr", "edit", "https://github.com/o/r/pull/7", f"--title={self.TITLE}", "--body-file", "-"])
        self.assertEqual(stdin, self.BODY)
        self.assertEqual((gh.title, gh.body), (self.TITLE, self.BODY))
        self.assertEqual((row["outcome"], row["existed"], row["pr"]), ("updated", True, "https://github.com/o/r/pull/7"))
        self.assertNotIn("detail", row)
        self.assertEqual(done["pr_sync"]["outcome"], "updated")

    def test_a_pull_request_opened_by_the_step_gets_them_too(self):
        # What `gh pr create --fill-first --body-file` left: the draft pr.md, whole.
        draft = f"# PR: {self.TITLE}\nIntent: intent.md. Author: t. Status: draft.\n\n{self.BODY}"
        gh = self.Gh(listed=False, title="first commit subject", body=draft)
        done, [row], _ = self._run(self.ACCEPTED, gh)
        self.assertEqual(len(gh.of("edit")), 1)
        self.assertEqual((gh.title, gh.body), (self.TITLE, self.BODY))
        self.assertEqual((row["outcome"], row["existed"]), ("updated", False))

    def test_a_lookup_that_could_not_answer_is_existed_none_not_false(self):
        # Review F2: a lookup that failed is not "no pull request".
        gh = self.Gh(listed=True, fail="list")
        done, [row], _ = self._run(self.ACCEPTED, gh)
        self.assertEqual((row["outcome"], row["existed"]), ("updated", None))
        self.assertIsNone(done["pr_sync"]["existed"])
        [start] = [r for r in self.service._journal().records(self.service._journal_key(str(self.repo)), kind="start")
                   if r.get("stage") == "pr"]
        self.assertEqual(start["pr_before"], "")

    def test_already_there_is_not_written_again(self):
        gh = self.Gh(listed=True, title=self.TITLE, body=self.BODY)
        done, [row], _ = self._run(self.ACCEPTED, gh)
        self.assertEqual(gh.of("edit"), [])
        self.assertEqual(row["outcome"], "already")

    def test_a_stopped_step_calls_nothing_and_writes_no_row(self):
        gh = self.Gh(listed=True)
        done, rows, _ = self._run(self.ACCEPTED, gh, hold=True)
        self.assertEqual(done["outcome"], "stopped")
        self.assertEqual((gh.of("view"), gh.of("edit"), rows), ([], [], []))
        self.assertNotIn("pr_sync", done)

    def test_a_draft_or_a_missing_url_is_skipped_without_gh(self):
        for text in (self.ACCEPTED.replace("Status: accepted", "Status: draft"),
                     self.ACCEPTED.replace("PR: https://github.com/o/r/pull/7. ", "")):
            with self.subTest(text=text.splitlines()[1]):
                self.setUp()
                gh = self.Gh(listed=True)
                done, [row], _ = self._run(text, gh)
                self.assertEqual((gh.of("view"), gh.of("edit")), ([], []))
                self.assertEqual(row["outcome"], "skipped")
                self.assertTrue(row["detail"])

    def test_r4_impl_and_review_do_not_sync(self):
        for stage in ("impl", "review"):
            with self.subTest(stage=stage):
                self.setUp()
                gh = self.Gh(listed=True)
                _, rows, _ = self._run(
                    None, gh, stage=stage,
                    prepare=lambda d: (d / "pr.md").write_text(self.ACCEPTED, encoding="utf-8"),
                )
                self.assertEqual((rows, gh.of("edit"), gh.of("view", "title,body")), ([], [], []))

    def test_r5_a_refusal_leaves_the_step_done_and_pr_md_as_it_was(self):
        gh = self.Gh(listed=True, fail="edit")
        done, [row], directory = self._run(self.ACCEPTED, gh)
        self.assertEqual(done["outcome"], "done")
        self.assertEqual((row["outcome"], row["detail"]), ("failed", "HTTP 422: Validation Failed"))
        self.assertEqual((directory / "pr.md").read_text(encoding="utf-8"), self.ACCEPTED)

    def test_r5_a_timeout_is_failed_and_says_so(self):
        gh = self.Gh(listed=True, raise_=asyncio.TimeoutError())
        done, [row], _ = self._run(self.ACCEPTED, gh)
        self.assertEqual(done["outcome"], "done")
        self.assertEqual(row["outcome"], "failed")
        self.assertIn("timed out", row["detail"])


class TheUpdateWindow(unittest.IsolatedAsyncioTestCase):
    """`0068` R8, R10 and R11, at the `Service` seam."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config = Config(workspaces=(self.tmp.name,), data_dir=str(Path(self.tmp.name) / "data"))
        self.s = Service(config, Sessions(config))

    def tearDown(self):
        self.tmp.cleanup()

    async def test_r11_run_integrate_and_send_refuse_with_updating(self):
        from coscc.service import Updating

        self.s.updater.window = True
        with self.assertRaises(Updating):
            await self.s.run_step(self.tmp.name, "0001_a", "impl").__anext__()
        with self.assertRaises(Updating):
            await self.s.integrate(self.tmp.name, "0001_a").__anext__()
        with self.assertRaises(Updating):
            self.s.check_send(self.tmp.name, "hi")
        self.s.updater.window = False
        self.s.check_send(self.tmp.name, "hi")

    async def test_r8_steps_integrations_and_chat_turns_are_jobs(self):
        self.s.steps.claim("/w", "0001_a", "impl")
        self.s._mark_running("/w", "0002_b", "integrate", "rebase")
        self.s._mark_running("/w", "0003_c", "impl", "step")  # a step: already in `steps`
        self.s.sessions._begin_turn("/w", "sid")
        kinds = sorted(j["kind"] for j in self.s._update_jobs())
        self.assertEqual(kinds, ["chat", "integration", "step"])

    async def test_r10_a_cut_step_goes_through_stop(self):
        running = self.s.steps.claim("/w", "0001_a", "impl")
        job = next(j for j in self.s._update_jobs() if j["kind"] == "step")
        self.assertTrue(await self.s._update_cut(job, "an"))
        self.assertTrue(running.stop_requested)
        self.assertEqual(running.stopped_by, "an")
        integration = {"kind": "integration", "workspace": "/w", "unit": "0002_b"}
        self.assertFalse(await self.s._update_cut(integration, "an"))

    def test_the_routes_seam_refuses_where_updates_are_not_available(self):
        from coscc.service import NotUpdatable

        self.assertEqual(self.s.update_status()["shape"], "unavailable")
        with self.assertRaises(NotUpdatable):
            self.s.update_cut_list()
        with self.assertRaises(NotUpdatable):
            self.s.update_build_local("an")


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


class RunStepHandsOnTheKnowledgeStore(unittest.TestCase):
    """`0090` plan step 4. `run_step` reads the store once, only with `COS_KNOWLEDGE` on and
    only for `spec`, `spike` and `plan`, and hands `Runner.run` what applies. The gate, the
    worktree and the runner are stand-ins: what is checked is the kwargs `Runner.run` gets."""

    ALL = ("idea", "intent", "spec", "spike", "plan", "impl", "pr", "review", "ship")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "work" / "proj"
        (self.repo / ".git").mkdir(parents=True)
        self.data = self.root / "data"
        self.seen: list[dict] = []

    def service(self, on: bool) -> Service:
        config = Config(workspaces=(str(self.repo),), working_dir=str(self.root / "work"),
                        data_dir=str(self.data), knowledge=on)
        service = Service(config, Sessions(config))
        self.unit = create_sync(service, str(self.repo), "a-problem", "words")["unit"]
        return service

    def write_store(self, *scopes: str) -> None:
        from coscc import knowledge

        blocks = [
            f"## K{n}\nScope: {scope}\nSource: proj-000000000000/0001_a/spike.md ## U1\nMeasured: 2026-09-25\nFact {n}."
            for n, scope in enumerate(scopes, 1)
        ]
        text = f"# Knowledge\nVersion: 1. Gathered: 2026-09-27T00:00:00Z. Max id: K{len(scopes)}.\n\n" + "\n\n".join(blocks) + "\n"
        knowledge.save(knowledge.path_of(str(self.data)) / knowledge.STORE, text)

    def kwargs_of(self, service: Service, stage: str) -> dict:
        from coscc import board as board_reader
        from coscc import integrate
        from coscc import service as service_mod
        from coscc.runner import RunError

        seen = self.seen

        class StandIn:
            def __init__(self, *a, **kw):
                pass

            async def run(self, **kw):
                seen.append(kw)
                raise RunError("a stand-in runner")
                yield  # pragma: no cover

        async def open_gate(units_root, unit, stage, repo=None, **kw):
            return True, f"open: {stage} may proceed"

        async def tree(*a, **kw):
            return {"path": str(self.repo), "branch": "feat/a-problem", "base": None}

        async def no_pr(*a, **kw):
            return {"state": "none", "url": ""}

        async def go():
            async for _ in service.run_step(str(self.repo), self.unit, stage):
                pass

        before = len(self.seen)
        with mock.patch.object(board_reader, "gate", open_gate), \
                mock.patch.object(service_mod, "Runner", StandIn), \
                mock.patch.object(service, "_worktree", tree), \
                mock.patch.object(worktrees, "read_prepare", lambda *a: {"ok": True}), \
                mock.patch.object(integrate, "pr_for_branch", no_pr):
            with self.assertRaises(Invalid) as refused:
                asyncio.run(go())
        # The step reached `Runner.run`, rather than being refused before it.
        self.assertEqual(len(self.seen), before + 1, str(refused.exception))
        return self.seen[-1]

    def test_off_nothing_is_read_and_no_key_is_handed_on(self):
        from coscc import knowledge

        service = self.service(False)
        self.write_store("tool:x")
        with mock.patch.object(knowledge, "load", side_effect=AssertionError("read with the flag off")):
            for stage in self.ALL:
                with self.subTest(stage=stage):
                    kw = self.kwargs_of(service, stage)
                    self.assertNotIn("knowledge", kw)
                    self.assertNotIn("knowledge_record", kw)

    def test_on_spec_spike_and_plan_get_the_entries_of_this_workspace(self):
        from coscc import knowledge

        service = self.service(True)
        self.write_store("tool:x", "workspace:elsewhere-0123456789ab")
        for stage in knowledge.STAGES:
            with self.subTest(stage=stage):
                kw = self.kwargs_of(service, stage)
                self.assertIn("Fact 1.", kw["knowledge"])
                self.assertNotIn("Fact 2.", kw["knowledge"])
                self.assertEqual(kw["knowledge_record"]["entries"], 1)
                self.assertEqual(kw["knowledge_record"]["version"], knowledge.version_of(kw["knowledge"]))

    def test_on_no_other_stage_gets_a_key(self):
        service = self.service(True)
        self.write_store("tool:x")
        for stage in ("idea", "intent", "impl", "pr", "review", "ship"):
            with self.subTest(stage=stage):
                kw = self.kwargs_of(service, stage)
                self.assertNotIn("knowledge", kw)
                self.assertNotIn("knowledge_record", kw)

    def test_on_with_nothing_applicable_is_an_empty_section_and_zero_entries(self):
        service = self.service(True)
        self.write_store("workspace:elsewhere-0123456789ab")
        kw = self.kwargs_of(service, "spec")
        self.assertEqual(kw["knowledge"], "")
        self.assertEqual(kw["knowledge_record"]["entries"], 0)
        self.assertNotIn("error", kw["knowledge_record"])

    def test_settings_does_not_list_the_gathering_grant(self):
        """Spec *Design*: no screen changes, so `/settings` lists the grants it listed before."""
        from coscc import policy

        stages = [r["stage"] for r in self.service(False).settings()["grants"]]
        self.assertNotIn("knowledge", stages)
        self.assertEqual(
            [s for s in stages if ":" not in s],
            sorted(set(policy.GRANTS) - policy.TERMINAL_ONLY),
        )

    def test_on_a_store_that_cannot_be_read_still_runs_the_step(self):
        service = self.service(True)
        kw = self.kwargs_of(service, "plan")  # no store written at all
        self.assertEqual(kw["knowledge"], "")
        self.assertEqual((kw["knowledge_record"]["entries"], kw["knowledge_record"]["bytes"]), (0, 0))
        self.assertIn("FileNotFoundError", kw["knowledge_record"]["error"])


class RunStepHandsOnWhatEarlierReviewsSaid(RunStepHandsOnTheKnowledgeStore):
    """`0110` plan step 4. `run_step` hands an `impl` step the finding lines of the units the
    board it read reports finished, for the files its plan names, and every other stage no
    key. The same stand-ins as the knowledge store's; the tests of that class run here too."""

    PLAN = "# Plan: x\nStatus: accepted.\n\n## Files that change\n\n- `coscc/service.py`.\n"

    def shipped(self, service: Service, review: str) -> str:
        """A second unit the board reads as finished, whose `review.md` is `review`."""
        other = create_sync(service, str(self.repo), "an-earlier-problem", "words")["unit"]
        d = units.unit_dir(str(self.repo), other, str(self.data))
        for name in ("intent", "spec"):
            (d / f"{name}.md").write_text(f"# {name}\nStatus: accepted.\n", encoding="utf-8")
        (d / "plan.md").write_text("# Plan\nStatus: done.\n", encoding="utf-8")
        (d / "review.md").write_text(review, encoding="utf-8")
        return other

    def plan(self, text: str) -> None:
        (units.unit_dir(str(self.repo), self.unit, str(self.data)) / "plan.md").write_text(text, encoding="utf-8")

    def test_impl_gets_the_lines_of_a_finished_units_review_naming_a_file_of_the_plan(self):
        service = self.service(False)
        other = self.shipped(service, "# Review\nStatus: accepted.\n\n## Round 1\n\n"
                                      "- F1 [fixed abc] coscc/service.py:9 — high — PRIOR-MARKER\n")
        self.plan(self.PLAN)
        kw = self.kwargs_of(service, "impl")
        self.assertEqual(kw["prior_findings"],
                         f"- {other[:4]} Round 1 F1 [fixed abc] coscc/service.py:9 — high — PRIOR-MARKER")
        self.assertGreaterEqual(kw["prior_findings_record"]["lines"], 1)
        self.assertNotIn("error", kw["prior_findings_record"])

    def test_a_plan_without_the_section_is_an_empty_section_and_zero_bytes(self):
        service = self.service(False)
        self.shipped(service, "## Round 1\n\n- F1 [open] coscc/service.py:9 — high — x\n")
        self.plan("# Plan: x\nStatus: accepted.\n")
        kw = self.kwargs_of(service, "impl")
        self.assertEqual(kw["prior_findings"], "")
        self.assertEqual(kw["prior_findings_record"], {"bytes": 0, "lines": 0, "units": 0, "dropped": 0})

    def test_no_other_stage_gets_a_key(self):
        service = self.service(False)
        self.shipped(service, "## Round 1\n\n- F1 [open] coscc/service.py:9 — high — x\n")
        self.plan(self.PLAN)
        for stage in ("plan", "pr", "review", "ship"):
            with self.subTest(stage=stage):
                kw = self.kwargs_of(service, stage)
                self.assertNotIn("prior_findings", kw)
                self.assertNotIn("prior_findings_record", kw)

    def test_a_review_that_cannot_be_read_still_runs_the_step(self):
        service = self.service(False)
        other = self.shipped(service, "")
        # Not UTF-8: `cos.mjs` still reads the board, `priorfindings` cannot read the file.
        (units.unit_dir(str(self.repo), other, str(self.data)) / "review.md").write_bytes(
            b"## Round 1\n\n- F1 [open] coscc/service.py:9 \xff\n"
        )
        self.plan(self.PLAN)
        kw = self.kwargs_of(service, "impl")
        self.assertEqual(kw["prior_findings"], "")
        self.assertEqual(kw["prior_findings_record"],
                         {"bytes": 0, "lines": 0, "units": 0, "dropped": 0, "unreadable": 1})


class TheStateOfAUnit(unittest.TestCase):
    """`0100` R3, R5, R13. One state per unit, the first rule that matches deciding it."""

    @staticmethod
    def _unit(**kw) -> dict:
        base = {
            "name": "0001_x", "why": "missing", "at": "plan", "open": 0, "problems": [],
            "hold": None, "between_pr_and_ship": False, "integration": None,
            "stages": [{"stage": "intent", "status": "accepted"}],
        }
        return {**base, **kw}

    def _is(self, unit: dict, last_end=None, ci=None) -> str:
        return unit_state(unit, last_end, ci)["state"]

    def test_every_state_has_a_case_and_its_own_label_and_colour(self):
        cases = {
            "done": self._unit(why="finished"),
            "dropped": self._unit(why="dropped", hold={"state": "dropped"}),
            "paused": self._unit(why="paused", hold={"state": "paused"}),
            "needs-you": self._unit(open=2),
            "error": self._unit(problems=["plan.md: no Status line"]),
            "awaiting": self._unit(at="review", between_pr_and_ship=True),
            "ready": self._unit(),
        }
        for want, unit in cases.items():
            got = unit_state(unit, None, None)
            self.assertEqual(got["state"], want, want)
            self.assertEqual((got["label"], got["color"]), (STATE_LABEL[want], STATE_COLOR[want]))
        running = shown_state(unit_state(self._unit(), None, None), [{"stage": "plan"}])
        self.assertEqual((running["state"], running["label"]), ("running", "Running"))
        self.assertEqual(sorted(STATE_LABEL), sorted(STATE_COLOR))
        self.assertEqual(len(set(STATE_COLOR.values())), len(STATE_COLOR), "no two states share a colour")

    def test_no_state_reads_as_approval(self):
        for label in STATE_LABEL.values():
            self.assertNotIn("Approved", label)
            self.assertNotIn("Accepted", label)

    def test_a_rejected_unit_is_dropped_and_names_the_stage(self):
        unit = self._unit(why="rejected", stages=[
            {"stage": "intent", "status": "accepted"}, {"stage": "spec", "status": "rejected"},
        ])
        got = unit_state(unit, None, None)
        self.assertEqual((got["state"], got["label"]), ("dropped", "Dropped — spec rejected"))

    def test_each_pair_of_neighbouring_rules_goes_to_the_earlier_one(self):
        # 1/2: finished and dropped at once.
        self.assertEqual(self._is(self._unit(why="finished", hold={"state": "dropped"})), "done")
        # 2/3: rejected with a pause still on file.
        self.assertEqual(self._is(self._unit(why="rejected", hold={"state": "paused"})), "dropped")
        # 3/4: a paused unit with a session listed is still paused.
        paused = unit_state(self._unit(why="paused", hold={"state": "paused"}), None, None)
        self.assertEqual(shown_state(paused, [{"stage": "plan"}])["state"], "paused")
        # 4/5: a running unit with an open question is running.
        asking = unit_state(self._unit(open=1), None, None)
        self.assertEqual(shown_state(asking, [{"stage": "plan"}])["state"], "running")
        self.assertEqual(shown_state(asking, [])["state"], "needs-you")
        # 5/6: an open question beats an error (C1).
        self.assertEqual(self._is(self._unit(open=1, problems=["x"])), "needs-you")
        self.assertEqual(self._is(self._unit(why="awaits-person", problems=["x"])), "needs-you")
        # 6/7: an error in the pr→ship window.
        self.assertEqual(
            self._is(self._unit(at="review", between_pr_and_ship=True, problems=["x"])), "error"
        )
        # 7/8: in the window and missing is awaiting, not ready.
        self.assertEqual(self._is(self._unit(at="review", between_pr_and_ship=True)), "awaiting")

    def test_each_cause_of_error(self):
        # (a)
        self.assertEqual(self._is(self._unit(problems=["x"])), "error")
        self.assertEqual(self._is(self._unit(why="unreadable")), "error")
        # (b)
        self.assertEqual(self._is(self._unit(), {"stage": "plan", "outcome": "failed"}), "error")
        self.assertEqual(self._is(self._unit(), {"stage": "plan", "outcome": "exhausted"}), "error")
        # (c)
        window = self._unit(at="review", between_pr_and_ship=True)
        self.assertEqual(self._is({**window, "integration": {"state": "red-after-integration"}}), "error")
        # (d)
        ci = {"head": "a", "checks": [{"name": "tests", "bucket": "fail"}], "at": "2026-09-26T00:00:00+00:00"}
        got = unit_state(window, None, ci)
        self.assertEqual(got["state"], "error")
        self.assertEqual(got["ci"], {"read": True, "red": ["tests"], "at": "2026-09-26T00:00:00+00:00"})
        cancelled = {**ci, "checks": [{"name": "lint", "bucket": "cancel"}]}
        self.assertEqual(self._is(window, None, cancelled), "error")

    def test_a_failure_at_another_stage_or_a_stop_is_not_an_error(self):
        self.assertEqual(self._is(self._unit(at="plan"), {"stage": "spec", "outcome": "failed"}), "ready")
        self.assertEqual(self._is(self._unit(at="plan"), {"stage": "plan", "outcome": "stopped"}), "ready")

    def test_a_stale_review_or_ship_in_the_window_is_awaiting_with_its_ci_line(self):
        """`0054` review F3. A rerun of `pr` leaves `review.md` stale; `next` sends it down
        the missing review's wait on CI, so the card and the dialog say so too."""
        for at in ("review", "ship"):
            got = unit_state(self._unit(at=at, why="stale", between_pr_and_ship=True), None, None)
            self.assertEqual((got["state"], got["ci"]), ("awaiting", {"read": False, "red": [], "at": ""}), at)
        # A stale `pr.md` is `pr` to run again, not a wait.
        self.assertEqual(self._is(self._unit(at="pr", why="stale", between_pr_and_ship=True)), "ready")

    def test_changes_requested_in_the_window_is_ready(self):
        """C6. It waits on an `impl`, not on CI."""
        self.assertEqual(self._is(self._unit(at="review", why="changes-requested", between_pr_and_ship=True)), "ready")

    def test_the_ci_line_says_read_not_red_or_not_read(self):
        window = self._unit(at="review", between_pr_and_ship=True)
        self.assertEqual(unit_state(window, None, None)["ci"], {"read": False, "red": [], "at": ""})
        green = {"head": "a", "checks": [{"name": "tests", "bucket": "pass"}], "at": "t"}
        self.assertEqual(unit_state(window, None, green)["ci"], {"read": True, "red": [], "at": "t"})
        failed = {"head": "a", "error": "gh: not logged in", "at": "t"}
        self.assertEqual(unit_state(window, None, failed)["ci"], {"read": False, "red": [], "at": "t"})
        self.assertIsNone(unit_state(self._unit(), None, None)["ci"], "no line outside the window")

    def test_the_dialog_shows_no_reason_its_state_contradicts(self):
        """Review F1. A unit with `problems` is `Error` (C3), yet `attention_reason` still
        reads "Needs a person"; a dropped unit with a draft still reads "Accept <stage>.md"."""
        broken = self._unit(problems=["plan.md: no Status line"])
        self.assertEqual(attention_reason(broken), "Needs a person")
        state = unit_state(broken, None, None)["state"]
        self.assertEqual(state, "error")
        self.assertEqual(reason_beside(attention_reason(broken), state), "")
        self.assertEqual(reason_beside("Needs a person", "running"), "")

        dropped = self._unit(why="dropped", hold={"state": "dropped"},
                             stages=[{"stage": "intent", "status": "accepted"}, {"stage": "spec", "status": "draft"}])
        self.assertEqual(attention_reason(dropped), "Accept spec.md")
        self.assertEqual(reason_beside(attention_reason(dropped), unit_state(dropped, None, None)["state"]), "")
        for state in ("done", "paused"):
            self.assertEqual(reason_beside("Changes requested", state), "")

        # Where they agree, the words are `0082`'s, unchanged (R12).
        self.assertEqual(reason_beside("Needs a person", "needs-you"), "Needs a person")
        self.assertEqual(reason_beside("Accept plan.md", "ready"), "Accept plan.md")
        self.assertEqual(reason_beside("Changes requested", "ready"), "Changes requested")

    def test_0112_a_ship_md_a_refused_merge_left_is_not_offered_for_acceptance(self):
        """`0112` review F1. `next` works that draft; one with no `Round` reads as before."""
        rows = [{"stage": "review", "status": "accepted"}, {"stage": "ship", "status": "draft"}]
        refused = self._unit(why="ship-refused", at="ship", between_pr_and_ship=True, stages=rows,
                             next="ship after review round 1 did not merge — next, with --repo, says what runs now")
        self.assertEqual(attention_reason(refused), "")
        self.assertEqual(unit_state(refused, None, None)["state"], "ready")
        old = self._unit(why="draft", at="ship", between_pr_and_ship=True, stages=rows, next="finish and accept ship.md")
        self.assertEqual(attention_reason(old), "Accept ship.md")


class ReviewTakesTheScreenshotsAgainAfterARewrite(unittest.TestCase):
    """`0111` plan step 6. `cos.mjs screens` and the capture are stand-ins, and so is the
    runner: what is checked is whether `Runner.run` is reached, with which section, and what
    the run log holds."""

    OLD = {"head": "a" * 40, "dirty": False, "addresses": ["/board"], "hits": []}
    NEW = {"head": "b" * 40, "dirty": False, "addresses": ["/board"], "hits": []}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "work" / "proj"
        (self.repo / ".git").mkdir(parents=True)
        config = Config(workspaces=(str(self.repo),), working_dir=str(self.root / "work"),
                        data_dir=str(self.root / "data"))
        self.service = Service(config, Sessions(config))
        made = create_sync(self.service, str(self.repo), "a-problem", "words")
        self.unit, self.dir = made["unit"], Path(made["path"])
        self.seen: list[dict] = []
        self.taken: list[list[str]] = []

    def screens_records(self) -> list[dict]:
        journal = self.service._journal()
        return [r for r in journal.records(self.service._journal_key(str(self.repo))) if r.get("kind") == "screens"]

    def step(self, answer: dict, result: dict | None):
        from coscc import board as board_reader
        from coscc import retake
        from coscc import service as service_mod
        from coscc.runner import RunError

        seen, taken = self.seen, self.taken

        class StandIn:
            def __init__(self, *a, **kw):
                pass

            async def run(self, **kw):
                seen.append(kw)
                raise RunError("a stand-in runner")
                yield  # pragma: no cover

        async def open_gate(units_root, unit, stage, repo=None, **kw):
            return True, f"open: {stage} may proceed"

        async def tree(*a, **kw):
            return {"path": str(self.repo), "branch": "fix/a-problem", "base": None}

        async def asked(units_root, unit, repo, **kw):
            return answer

        async def take(tree, addresses, **kw):
            taken.append(list(addresses))
            return result

        async def go():
            async for _ in self.service.run_step(str(self.repo), self.unit, "review"):
                pass

        with mock.patch.object(board_reader, "gate", open_gate), \
                mock.patch.object(board_reader, "screens", asked), \
                mock.patch.object(retake, "take", take), \
                mock.patch.object(service_mod, "Runner", StandIn), \
                mock.patch.object(self.service, "_worktree", tree):
            with self.assertRaises(Invalid) as refused:
                asyncio.run(go())
        return str(refused.exception)

    def test_no_retake_records_nothing_and_the_step_runs(self):
        self.step({"retake": False, "why": "the manifest's head is still an ancestor of HEAD"}, None)
        self.assertEqual(len(self.seen), 1)
        self.assertEqual(self.seen[0]["screens_note"], "")
        self.assertEqual((self.taken, self.screens_records()), ([], []))

    def test_a_retake_taken_is_recorded_and_the_prompt_carries_it(self):
        result = {"code": 0, "seconds": 21.0, "tail": "", "head_before_run": "b" * 40,
                  "manifest_after": self.NEW, "status_before": "", "status_after": ""}
        self.step({"retake": True, "manifest": self.OLD}, result)
        self.assertEqual(self.taken, [["/board"]])
        [rec] = self.screens_records()
        self.assertEqual((rec["outcome"], rec["head_before"], rec["head_after"], rec["started_by"], rec["stage"]),
                         ("taken", "a" * 40, "b" * 40, "person", "review"))
        self.assertEqual(len(self.seen), 1)
        self.assertIn("# The screenshots, taken again", self.seen[0]["screens_note"])
        self.assertIn("b" * 40, self.seen[0]["screens_note"])

    def test_a_retake_that_fails_refuses_review_before_the_session(self):
        from coscc.service import RETAKE_REFUSED

        files = {p.name: p.read_bytes() for p in self.dir.iterdir()}
        result = {"code": 2, "seconds": 0.3, "tail": "127.0.0.1:18783 is already in use", "head_before_run": "b" * 40,
                  "manifest_after": self.OLD, "status_before": "", "status_after": ""}
        said = self.step({"retake": True, "manifest": self.OLD}, result)
        self.assertEqual(said, RETAKE_REFUSED)
        self.assertEqual(self.seen, [])
        [rec] = self.screens_records()
        self.assertEqual((rec["outcome"], rec["head_after"], rec["code"]), ("failed", "", 2))
        self.assertIn("exited 2", rec["detail"])
        self.assertIn("already in use", rec["detail"])
        # The mark is given back, and nothing was appended to the unit.
        self.assertEqual(self.service._active, {})
        self.assertEqual({p.name: p.read_bytes() for p in self.dir.iterdir()}, files)
        # One sentence, and no commit, path or log line in it (S1, S3).
        self.assertNotIn("/", said)
        self.assertNotRegex(said, r"[0-9a-f]{7,}")

    def test_a_board_that_cannot_answer_refuses_the_step(self):
        from coscc import board as board_reader

        async def unavailable(*a, **kw):
            raise board_reader.Unavailable("node is missing")

        with mock.patch.object(board_reader, "screens", unavailable):
            with self.assertRaises(Invalid):
                asyncio.run(self.service._retake_screens(
                    str(self.repo), "k", self.service._journal(), self.unit, str(self.repo), "person"))

    def _unrecorded(self, result: dict) -> str:
        from coscc import board as board_reader
        from coscc import retake
        from coscc.journal import Busy, Journal

        async def asked(*a, **kw):
            return {"retake": True, "manifest": self.OLD}

        async def take(tree, addresses, **kw):
            return result

        with mock.patch.object(board_reader, "screens", asked), mock.patch.object(retake, "take", take), \
                mock.patch.object(Journal, "append", side_effect=Busy("locked")):
            with self.assertRaises(Invalid) as refused:
                asyncio.run(self.service._retake_screens(
                    str(self.repo), "k", self.service._journal(), self.unit, str(self.repo), "person"))
        return str(refused.exception)

    def test_a_failed_retake_the_run_log_could_not_record_says_it_failed(self):
        # Review round 1, F2: it must not say the screenshots were taken again.
        from coscc.service import RETAKE_REFUSED

        said = self._unrecorded({"code": 2, "seconds": 0.3, "tail": "", "head_before_run": "b" * 40,
                                 "manifest_after": self.OLD, "status_before": "", "status_after": ""})
        self.assertEqual(said, RETAKE_REFUSED)

    def test_a_taken_retake_the_run_log_could_not_record_says_so(self):
        said = self._unrecorded({"code": 0, "seconds": 21.0, "tail": "", "head_before_run": "b" * 40,
                                 "manifest_after": self.NEW, "status_before": "", "status_after": ""})
        self.assertIn("taken again, but the run log could not record it", said)

    def test_a_pending_update_waits_for_a_retake(self):
        # Review round 1, F3: listed as a job while it runs, never cut, gone once it ends.
        from coscc import board as board_reader
        from coscc import retake

        during: list[list[dict]] = []

        async def asked(*a, **kw):
            return {"retake": True, "manifest": self.OLD}

        async def take(tree, addresses, **kw):
            during.append(self.service._update_jobs())
            return {"code": 0, "seconds": 0.1, "tail": "", "head_before_run": "b" * 40,
                    "manifest_after": self.NEW, "status_before": "", "status_after": ""}

        with mock.patch.object(board_reader, "screens", asked), mock.patch.object(retake, "take", take), \
                mock.patch.object(self.service.updater, "job_ended") as ended:
            asyncio.run(self.service._retake_screens(
                str(self.repo), "k", self.service._journal(), self.unit, str(self.repo), "person"))
        [[job]] = during
        self.assertEqual((job["kind"], job["unit"], job["stage"]), ("integration", self.unit, "screens"))
        self.assertEqual(self.service._update_jobs(), [])
        ended.assert_called_once()

    def test_no_retake_begins_while_an_update_is_applied(self):
        from coscc import board as board_reader
        from coscc import retake

        taken: list[str] = []

        async def asked(*a, **kw):
            return {"retake": True, "manifest": self.OLD}

        async def take(tree, addresses, **kw):
            taken.append(tree)
            return {}

        self.service.updater.window = True
        with mock.patch.object(board_reader, "screens", asked), mock.patch.object(retake, "take", take):
            with self.assertRaises(Invalid):
                asyncio.run(self.service._retake_screens(
                    str(self.repo), "k", self.service._journal(), self.unit, str(self.repo), "person"))
        self.assertEqual((taken, self.service._update_jobs()), ([], []))

    def test_two_retakes_never_run_at_once(self):
        from coscc import board as board_reader
        from coscc import retake

        running, most = [0], [0]

        async def asked(*a, **kw):
            return {"retake": True, "manifest": self.OLD}

        async def take(tree, addresses, **kw):
            running[0] += 1
            most[0] = max(most[0], running[0])
            await asyncio.sleep(0.05)
            running[0] -= 1
            return {"code": 0, "seconds": 0.05, "tail": "", "head_before_run": "b" * 40,
                    "manifest_after": self.NEW, "status_before": "", "status_after": ""}

        async def go():
            journal = self.service._journal()
            await asyncio.gather(*(
                self.service._retake_screens(str(self.repo), "k", journal, u, str(self.repo), "person")
                for u in ("0001_a", "0002_b", "0003_c")
            ))

        with mock.patch.object(board_reader, "screens", asked), mock.patch.object(retake, "take", take):
            asyncio.run(go())
        self.assertEqual(most[0], 1)

    def test_activity_shows_no_screens_record(self):
        journal = self.service._journal()
        key = self.service._journal_key(str(self.repo))
        journal.append({"kind": "screens", "workspace": key, "unit": self.unit, "stage": "review", "outcome": "failed"})
        journal.append({"kind": "start", "workspace": key, "unit": self.unit, "stage": "plan", "mode": "manual"})
        kinds = [e["kind"] for e in self.service.activity(str(self.repo))["events"]]
        self.assertEqual(kinds, ["start"])
        both = self.service.activity_and_usage(str(self.repo))
        self.assertEqual([e["kind"] for e in both["events"]], ["start"])
