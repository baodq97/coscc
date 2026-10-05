"""Tests for `Steps` in `coscc/runner/steps.py`."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.bus import Bus
from coscc import units
from coscc.agent import modeltrial
from coscc.github import prmachine, prscope
from coscc.git import fetches
from coscc.units import worktrees
from coscc.config import Config
from coscc.runner.queue import Refused
from coscc.runner.steps import step_cwd
from coscc.units.worktrees import describe_base
from coscc.kernel import Invalid
from coscc.http.app import Core
from coscc.agent.sessions import Sessions
from tests.http.test_app import create_sync, timeline, unit_history
from tests.units.test_submit import submits as _submits
from tests.http.test_app import use_sessions


def live(core, unit: str):
    """The `Running` this process holds for the unit's unfinished attempt, or `None`."""
    return next((r for r in core.steps.tasks.values() if r.unit == unit), None)


class AUnitsBaseIsTheRemoteTrunk(unittest.TestCase):
    """`run_step` refreshes a still-detached tree from `origin/main` before the step runs, and
    carries what it found into the `done` record — the reading `describe_base` turns into
    one sentence for the board and the prompt. Same fixture as `StartingAUnitAndItsBranch`
    (a bare remote on disk, no network), driven with a session that replies without talking
    to anything, the way `AStepRecordsTheTransitionItCaused` does below."""

    class Replies:
        bus = Bus()

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            yield ("chunk", "# Spec: a problem\nAuthor: t. Status: accepted.\n\n## Body\n")
            await _submits(kw)
            yield ("done", {"session_id": "sess-30", "cost": {"output_tokens": 3}})

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
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
        self.config = Config(
            workspaces=(str(self.repo),),
            working_dir=str(self.root / "work"),
            data_dir=str(self.root / "data"),
        )
        self.core = Core(self.config, self.Replies())

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

    def _typed_unit(self, slug: str = "a-problem") -> str:
        made = create_sync(self.core, str(self.repo), slug, "some words")
        (Path(made["path"]) / "intent.md").write_text(
            f"# Intent: {slug}\nAuthor: t. Type: fix. Status: accepted.\n", encoding="utf-8"
        )
        return made["unit"]

    def _tree(self, unit: str) -> Path:
        return worktrees.path(str(self.repo), unit, str(self.root / "data"))

    def _tree_head(self, tree: Path) -> str:
        return subprocess.run(
            ["git", "-C", str(tree), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def _run_step(self, unit: str, stage: str = "spec") -> dict:
        async def go():
            last = None
            async for item in self.core.steps.run_step(str(self.repo), unit, stage):
                last = item
            return last

        kind, payload = asyncio.run(go())
        self.assertEqual(kind, "done")
        return payload

    def test_the_tree_is_moved_to_the_fetched_tip_and_local_main_stays(self):
        unit = self._typed_unit()
        local_before = self._git("rev-parse", "main").strip()
        ahead = self._advance_remote()
        done = self._run_step(unit)
        self.assertEqual(done["outcome"], "done")
        fetched = done["base"].pop("fetch")
        self.assertEqual(
            done["base"], {"ref": "origin/main", "sha": ahead[:7], "fresh": True, "reason": ""}
        )
        # A step alone fetches once, exactly as before.
        self.assertEqual((fetched["outcome"], fetched["attempts"]), ("fetched", 1))
        self.assertEqual(self._git("rev-parse", "main").strip(), local_before)
        self.assertEqual(self._tree_head(self._tree(unit)), ahead)

    def test_a_broken_origin_does_not_stop_the_step(self):
        unit = self._typed_unit()
        tree = self._tree(unit)
        before = self._tree_head(tree)
        self._git("remote", "set-url", "origin", str(self.root / "gone.git"))
        done = self._run_step(unit)
        self.assertEqual(done["outcome"], "done")
        self.assertFalse(done["base"]["fresh"])
        self.assertTrue(done["base"]["reason"])
        self.assertEqual(self._tree_head(tree), before)

    def test_a_commit_made_directly_on_the_tree_is_never_left_behind(self):
        unit = self._typed_unit()
        tree = self._tree(unit)
        (tree / "local.txt").write_text("mine\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(tree), "add", "-A"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(tree),
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-q",
                "-m",
                "local",
            ],
            check=True,
        )
        local_head = self._tree_head(tree)
        done = self._run_step(unit)
        self.assertFalse(done["base"]["fresh"])
        self.assertIn("ancestor", done["base"]["reason"])
        self.assertEqual(self._tree_head(tree), local_head)

    def test_the_branch_start_branch_cuts_carries_the_remote_tip(self):
        """`intent.md ## Proposed outcome`, measured through `start_branch`.

        `tip` is the SHA `_advance_remote()` itself pushed and returned, never a ref read back from
        the workspace — `origin/main` there is the very ref `start_branch` updates when it fetches,
        so comparing against it would still pass with the fetch removed, which is exactly what this
        test exists to catch."""
        tip = self._advance_remote()
        unit = self._typed_unit()
        got = asyncio.run(self.core.backlog.start_branch(str(self.repo), unit))
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
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip(),
            "0",
        )

    def test_a_branch_a_session_cuts_itself_also_carries_the_remote_tip(self):
        """Plan Risk 2's gap, closed at the one place a step runs: `run_step` refreshes the
        still-detached tree, and a session's own `git switch -c` right after starts from
        that refreshed HEAD — the same command a person runs at a terminal
        (`.claude/CLAUDE.md` step 4), not a wrapper this test invented.
        """
        unit = self._typed_unit()
        ahead = self._advance_remote()
        self._run_step(unit)
        tree = self._tree(unit)
        branch = units.branch_name(
            str(self.repo),
            unit,
            str(self.root / "data"),
            self.core.ws.snapshot(str(self.repo), [unit]),
        )
        subprocess.run(
            ["git", "-C", str(tree), "switch", "--no-track", "-c", branch],
            check=True,
            capture_output=True,
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
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip(),
            "0",
        )

    def _impl(self, unit: str) -> None:
        """Start `impl` with the gate open and a runner that stops at once: what is looked at is
        the tree it was handed, not a session."""
        from coscc.units import board as board_reader
        from coscc.runner.reply import RunError

        class StandIn:
            bus = Bus()

            def __init__(self, *a, **kw):
                pass

            async def run(self, **kw):
                raise RunError("a stand-in runner")
                yield  # pragma: no cover

        async def open_gate(units_root, unit, stage, repo=None, **kw):
            return True, f"open: {stage} may proceed"

        async def go():
            async for _ in self.core.steps.run_step(str(self.repo), unit, "impl"):
                pass

        with (
            mock.patch.object(board_reader, "gate", open_gate),
            mock.patch("coscc.runner.steps.Runner", StandIn),
            mock.patch.object(worktrees, "read_prepare", lambda *a: {"ok": True}),
        ):
            asyncio.run(go())

    def _commits_past_main(self, tree: Path) -> str:
        return subprocess.run(
            ["git", "-C", str(tree), "rev-list", "--count", "origin/main..HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def test_impl_on_a_detached_tree_cuts_the_branch_from_the_remote_tip_first(self):
        unit = self._typed_unit()
        tip = self._advance_remote()
        tree = self._tree(unit)
        with self.assertRaises(Invalid):
            self._impl(unit)  # the stand-in runner fails after the tree was readied
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(tree), "branch", "--show-current"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip(),
            "fix/a-problem",
        )
        self.assertEqual(self._tree_head(tree), tip)
        self.assertEqual(self._commits_past_main(tree), "0")

    def test_impl_leaves_a_tree_already_on_a_branch_alone(self):
        unit = self._typed_unit()
        asyncio.run(self.core.backlog.start_branch(str(self.repo), unit))
        tree = self._tree(unit)
        subprocess.run(
            ["git", "-C", str(tree), "branch", "-m", "fix/mine"], check=True, capture_output=True
        )
        with self.assertRaises(Invalid):
            self._impl(unit)
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(tree), "branch", "--show-current"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip(),
            "fix/mine",
        )

    def test_impl_is_refused_with_no_branch_when_the_fetch_fails(self):
        unit = self._typed_unit()
        tree = self._tree(unit)
        before = self._tree_head(tree)
        self._git("remote", "set-url", "origin", str(self.root / "gone.git"))
        with self.assertRaises(Refused) as refused:
            self._impl(unit)
        self.assertEqual(refused.exception.reasons, ("no-branch",))
        self.assertEqual(self._tree_head(tree), before)
        self.assertEqual(self._commits_past_main(tree), "0")

    def test_describe_base_is_empty_when_fresh_and_names_the_sha_when_not(self):
        self.assertEqual(describe_base(None), "")
        self.assertEqual(describe_base({"fresh": True}), "")
        said = describe_base(
            {"fresh": False, "ref": "origin/main", "sha": "abc1234", "reason": "boom"}
        )
        self.assertIn("abc1234", said)
        self.assertIn("boom", said)


class AnImplIsToldWhatMainChangedSinceThePlan(unittest.TestCase):
    """The fixture is `AUnitsBaseIsTheRemoteTrunk`'s, borrowed rather than inherited so its tests do
    not run twice. The expected list is `git diff --name-only` run by subprocess — the intent's own
    check — never something this test worked out."""

    HEADING = "# The files main changed since the plan"

    class Replies:
        bus = Bus()

        def __init__(self):
            self.reply = ""
            self.prompts: list[str] = []

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            self.prompts.append(text)
            yield ("chunk", self.reply)
            await _submits(kw)
            yield ("done", {"session_id": "sess-42", "cost": {"output_tokens": 3}})

    def setUp(self):
        AUnitsBaseIsTheRemoteTrunk.setUp(self)
        # A step under 30s after the last fetch reuses it, so a merge between `plan` and `impl` is
        # only seen when `impl` starts later — `_impl` moves this clock.
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
        self.core.sessions.reply = self.PLAN
        self.assertEqual(self._run_step(unit, "plan")["outcome"], "done")
        return unit

    def _impl(self, unit: str) -> tuple[str, dict]:
        self.now[0] += fetches.REUSE_SECONDS
        self.core.sessions.reply = "working"
        self._run_step(unit, "impl")
        return self.core.sessions.prompts[-1], self._start(unit, "impl")

    def _start(self, unit: str, stage: str) -> dict:
        records = self.core.ws.journal().records(
            self.core.ws.key(str(self.repo)), unit, kind="start"
        )
        return [r for r in records if r["stage"] == stage][-1]

    def _tree_git(self, unit: str, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self._tree(unit)), *args], capture_output=True, text=True, check=True
        ).stdout.strip()

    def test_the_prompt_names_the_changed_files_the_plan_names(self):
        unit = self._planned()
        self._advance_remote("a.py", "changed\n")
        self._advance_remote("lib_a.py", "outside, and a suffix trap\n")
        prompt, start = self._impl(unit)
        drift = start["plan_drift"]
        main = self._tree_git(unit, "rev-parse", "refs/remotes/origin/main")
        diff = self._tree_git(
            unit, "diff", f"{drift['plan_sha']}..{main}", "--name-only"
        ).splitlines()
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

    def test_nothing_the_plan_names_changed_adds_nothing(self):
        unit = self._planned()
        self._advance_remote("other.txt", "outside\n")
        prompt, start = self._impl(unit)
        self.assertEqual(start["plan_drift"]["files"], [])
        self.assertTrue(start["plan_drift"]["checked"])
        self.assertNotIn(self.HEADING, prompt)

    def test_a_merge_under_thirty_seconds_after_the_plans_fetch_is_not_seen(self):
        # `main_sha` says which tip was measured.
        unit = self._planned()
        self._advance_remote("a.py", "changed\n")
        self.now[0] -= fetches.REUSE_SECONDS  # `_impl` adds it back: the same instant
        _, start = self._impl(unit)
        self.assertEqual(start["base"]["fetch"]["outcome"], "reused")
        self.assertEqual(start["plan_drift"]["files"], [])
        self.assertTrue(start["plan_drift"]["checked"])
        self.assertEqual(start["plan_drift"]["main_sha"], start["plan_drift"]["plan_sha"])

    def test_a_plan_no_run_wrote_cannot_be_checked(self):
        unit, directory = self._unit_with_spec()
        (directory / "plan.md").write_text(self.PLAN, encoding="utf-8")
        prompt, start = self._impl(unit)
        self.assertFalse(start["plan_drift"]["checked"])
        self.assertIsNone(start["plan_drift"]["files"])
        self.assertIn(self.HEADING, prompt)
        self.assertIn("could not check", prompt)

    def test_a_computation_that_raises_does_not_stop_the_step(self):
        unit = self._planned()
        with mock.patch("coscc.git.drift.compute", side_effect=RuntimeError("boom")):
            prompt, start = self._impl(unit)
        self.assertFalse(start["plan_drift"]["checked"])
        self.assertEqual(start["plan_drift"]["reason"], "boom")
        self.assertIn("could not check", prompt)

    def test_another_stage_carries_no_plan_drift(self):
        unit, _ = self._unit_with_spec()
        self.core.sessions.reply = "# Spec: a problem\nAuthor: t. Status: accepted.\n"
        self._run_step(unit, "spec")
        self.assertNotIn("plan_drift", self._start(unit, "spec"))
        self.assertNotIn(self.HEADING, self.core.sessions.prompts[-1])


class AStepRecordsTheTransitionItCaused(unittest.TestCase):
    """`.cos/0013_.../ship.md` said the loop would come back here: history imported from git
    carries no actor and no session because git knows neither, so the provenance that unit
    built is only true of work done after it. These rows are that work — and the check
    that matters is that `actor` and `session` are **not** `unknown`.

    Driven with a session that replies without talking to anything, because what is under
    test is the bookkeeping around a step, not the step."""

    class Replies:
        bus = Bus()

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            yield ("chunk", "# Spec: a problem\nAuthor: t. Status: accepted.\n\n## Body\n")
            await _submits(kw)
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
        self.core = Core(config, self.Replies())
        self.made = create_sync(self.core, str(self.repo), "a-problem", "some words")
        (Path(self.made["path"]) / "intent.md").write_text(
            "# Intent: a problem\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8"
        )

    def _run(self, stage: str) -> None:
        async def go():
            async for _ in self.core.steps.run_step(str(self.repo), self.made["unit"], stage):
                pass

        asyncio.run(go())

    def test_the_row_names_the_stage_and_the_real_session(self):
        self._run("spec")
        found = unit_history(self.core, str(self.repo), self.made["unit"])
        [row] = [r for r in found["transitions"] if r["artifact"] == "spec.md"]
        self.assertEqual(row["to_state"], "accepted")
        self.assertEqual(row["actor"], "stage:spec")
        self.assertEqual(row["session"], "sess-42")
        self.assertNotEqual(row["session"], "unknown")

    def test_the_projection_moves_with_it(self):
        self._run("spec")
        found = unit_history(self.core, str(self.repo), self.made["unit"])
        self.assertEqual(found["state"]["spec.md"], "accepted")

    def test_running_the_same_stage_twice_is_two_events_not_one(self):
        self._run("spec")
        self._run("spec")
        found = unit_history(self.core, str(self.repo), self.made["unit"])
        rows = [r for r in found["transitions"] if r["artifact"] == "spec.md"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(found["settled_edits"], 1)

    def test_a_failed_ingest_is_on_the_step_and_in_the_database(self):
        """The step still ends `done`, and the failure is kept, not dropped. What is kept is one
        fixed sentence; the error, which may carry a path, goes to the log."""
        from unittest import mock

        from coscc.units import meta

        async def go():
            return [
                item
                async for item in self.core.steps.run_step(
                    str(self.repo), self.made["unit"], "spec"
                )
            ]

        # The store is imported on its first read, before `meta` is broken: only the ingest fails.
        asyncio.run(self.core.board(str(self.repo)))
        error = meta.MetaError("coscc.loop meta did not run: no interpreter at /home/x/units")
        with (
            mock.patch.object(meta, "read", side_effect=error),
            self.assertLogs("coscc", "WARNING") as log,
        ):
            _, payload = asyncio.run(go())[-1]
        self.assertEqual(payload["outcome"], "done")
        self.assertEqual(payload["ingest_error"], "its files could not be read")
        with self.core.ws.unit_meta().data.connect() as conn:
            [row] = conn.execute(
                "SELECT unit, field, reason FROM unit_unknowns WHERE field = 'ingest'"
            ).fetchall()
        self.assertEqual(tuple(row), (self.made["unit"], "ingest", "its files could not be read"))
        self.assertIn("/home/x/units", log.output[-1])
        self.assertIn(self.made["unit"], log.output[-1])

    def test_a_failed_ingest_on_the_database_names_no_path(self):
        from unittest import mock

        from coscc.store.db import Busy

        asyncio.run(self.core.board(str(self.repo)))
        busy = Busy(self.core.config.data_dir + "/cos.db")
        with (
            mock.patch("coscc.units.meta.UnitMeta.ingest", side_effect=busy),
            self.assertLogs("coscc", "WARNING") as log,
        ):
            said = asyncio.run(
                self.core.answers.ingest(
                    str(self.repo), self.made["unit"], {"outcome": "done", "stage": "spec"}
                )
            )
        self.assertEqual(said, {"ingest_error": "the database could not be written"})
        self.assertIn("cos.db", log.output[-1])

    def test_a_failed_step_records_nothing(self):
        class Empty:
            bus = Bus()

            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                await _submits(kw)
                yield ("done", {"session_id": "sess-0", "cost": {}})

        use_sessions(self.core, Empty())

        async def go():
            out = []
            async for item in self.core.steps.run_step(str(self.repo), self.made["unit"], "spec"):
                out.append(item)
            return out

        # A step that fails comes back as data, not as an exception: `Runner.run` catches
        # its own RunError so the stream always ends with one `done`. The outcome in it is
        # what says whether anything happened.
        _, payload = asyncio.run(go())[-1]
        self.assertNotEqual(payload["outcome"], "done")
        found = unit_history(self.core, str(self.repo), self.made["unit"])
        self.assertEqual([r for r in found["transitions"] if r["artifact"] == "spec.md"], [])


class AStepThatEndsRecordsWhatANoticeSays(unittest.TestCase):
    """After a `done` step's `end`: the questions it left open, and after `ship` whether the unit
    merged. Driven like `AStepRecordsTheTransitionItCaused`."""

    ASKS = "# Spec: a problem\nAuthor: t. Status: draft.\n\n## Body\n\n## Open questions\n\n1. Which one?\n"
    # What the session hands back beside `ASKS`; the questions are the object's.
    ASKED = {"judgement": "not-ready", "questions": [{"n": 1, "text": "Which one?"}]}

    def replies(self, text: str, outcome: dict | None = None, **fields):
        class Replies:
            bus = Bus()

            async def stream(self, cwd, prompt, session_id=None, max_turns=1, **kw):
                yield ("chunk", text)
                await _submits(kw, **fields)
                yield ("done", outcome or {"session_id": "sess-1", "cost": {"output_tokens": 3}})

        return Replies()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "work" / "proj"
        self.repo.mkdir(parents=True)
        self.config = Config(
            workspaces=(str(self.repo),),
            working_dir=str(self.root / "work"),
            data_dir=str(self.root / "data"),
        )
        self.core = Core(self.config, self.replies(self.ASKS, **self.ASKED))
        self.made = create_sync(self.core, str(self.repo), "a-problem", "some words")
        self.unit = self.made["unit"]
        (Path(self.made["path"]) / "intent.md").write_text(
            "# Intent: a problem\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8"
        )
        self.key = self.core.ws.key(str(self.repo))

    def _run(self, stage: str = "spec") -> list:
        async def go():
            stream = self.core.steps.run_step(str(self.repo), self.unit, stage)
            items = [await stream.__anext__()]
            running = live(self.core, self.unit)
            items += [item async for item in stream]
            # The reader has its `done` before `after_end` runs, last in the step's task.
            if running is not None:
                await running.task
            return items

        return asyncio.run(go())

    def records(self) -> list[dict]:
        from coscc.store.journal import Journal

        return Journal(self.config.working_dir, self.config.data_dir).records(self.key, self.unit)

    def test_a_done_step_that_leaves_open_questions_records_them_after_its_end(self):
        _, payload = self._run()[-1]
        self.assertEqual(payload["outcome"], "done")
        kinds = [r["kind"] for r in self.records()]
        # The status the object set, and its event, between the two.
        self.assertEqual(kinds[-3:], ["end", "transition", "questions"])
        [asked] = [r for r in self.records() if r["kind"] == "questions"]
        self.assertEqual((asked["workspace"], asked["stage"]), (self.key, "spec"))
        self.assertEqual(asked["questions"], [{"artifact": "spec.md", "n": 1}])

    def test_a_done_step_with_every_question_answered_records_none(self):
        use_sessions(
            self.core,
            self.replies("# Spec: a problem\nAuthor: t. Status: accepted.\n\n## Body\n"),
        )
        self._run()
        self.assertNotIn("questions", [r["kind"] for r in self.records()])

    def test_a_failed_or_stopped_step_records_no_questions(self):
        use_sessions(
            self.core,
            self.replies(self.ASKS, {"session_id": "s", "terminal_reason": "error_max_turns"}),
        )
        _, payload = self._run()[-1]
        self.assertNotEqual(payload["outcome"], "done")
        self.assertNotIn("questions", [r["kind"] for r in self.records()])

    def _after_end_with(self, why: str, stage: str = "ship") -> list[dict]:
        from coscc.units import board as board_reader

        async def read(root, timeout=None, state=None):
            return {"units": [{"name": self.unit, "why": why, "questions": []}]}

        with mock.patch.object(board_reader, "read", read):
            asyncio.run(self.core.steps.after_end(str(self.repo), self.unit, stage, self.key))
        return [r for r in self.records() if r["kind"] == "ship"]

    def test_a_done_ship_records_shipped_when_the_unit_is_finished(self):
        [row] = self._after_end_with("finished")
        self.assertEqual(
            (row["result"], row["stage"], row["workspace"]), ("shipped", "ship", self.key)
        )

    def test_a_done_ship_records_refused_on_ship_refused(self):
        [row] = self._after_end_with("ship-refused")
        self.assertEqual(row["result"], "refused")

    def test_a_ship_still_merging_records_no_ship(self):
        self.assertEqual(self._after_end_with("ship-merging"), [])

    def test_any_other_why_or_stage_records_no_ship(self):
        self.assertEqual(self._after_end_with("ci"), [])
        self.assertEqual(self._after_end_with("finished", stage="review"), [])

    def test_a_board_read_that_fails_changes_nothing_about_the_step(self):
        from coscc.units import board as board_reader

        real = board_reader.read

        async def broken(root, timeout=board_reader.TIMEOUT, state=None):
            # Only once the step's `end` is written: the gate before it reads the board too.
            if "end" in [r["kind"] for r in self.records()]:
                raise board_reader.Unavailable("the loop could not start")
            return await real(root, timeout, state)

        with mock.patch.object(board_reader, "read", broken):
            _, payload = self._run()[-1]
        self.assertEqual(payload["outcome"], "done")
        self.assertEqual([r["kind"] for r in self.records()][-2:], ["end", "transition"])
        self.assertEqual(self.core.attempts.unfinished(), [])
        self.assertEqual(self.core.steps.tasks, {})

    async def _ended(self, after_end) -> asyncio.Task:
        """A step run to its `done` with `after_end` as its `after_end`, returned once its
        attempt has ended and its task is in `holds.finishing`."""
        self.core.steps.after_end = after_end
        stream = self.core.steps.run_step(str(self.repo), self.unit, "spec")
        await stream.__anext__()
        task = live(self.core, self.unit).task
        [_ async for _ in stream]
        for _ in range(500):
            if self.core.holds.finishing:
                break
            await asyncio.sleep(0.01)
        # R: the attempt is ended before `after_end` runs, so nothing holds the unit.
        self.assertEqual(self.core.attempts.unfinished(), [])
        self.assertEqual(self.core.steps.tasks, {})
        return task

    def test_an_apply_waits_for_the_after_end_of_a_step_that_just_ended(self):
        # `drive` ends the unit's attempt before `after_end`; the settle still waits, and the
        # `questions` row is written.
        real = self.core.steps.after_end

        async def go():
            gate = asyncio.Event()

            async def slow(*args):
                await gate.wait()
                await real(*args)

            task = await self._ended(slow)
            settle = asyncio.create_task(self.core.resume.settle_after_suspend(5))
            # Its first look found the `after_end` still running.
            for _ in range(3):
                await asyncio.sleep(0)
            self.assertFalse(settle.done())
            gate.set()
            left = await settle
            await task
            return left

        self.assertEqual(asyncio.run(go()), [])
        self.assertEqual(
            [r["kind"] for r in self.records()][-3:], ["end", "transition", "questions"]
        )
        self.assertEqual(self.core.holds.finishing, {})

    def test_an_after_end_that_outlives_the_settle_is_named_and_cancelled(self):
        async def go():
            async def hangs(*args):
                await asyncio.sleep(60)

            task = await self._ended(hangs)
            left = await self.core.resume.settle_after_suspend(0.05)
            await self.core.shutdown()
            return left, task

        left, task = asyncio.run(go())
        self.assertEqual(
            [(j["kind"], j["unit"], j["stage"]) for j in left], [("after-end", self.unit, "spec")]
        )
        self.assertTrue(task.cancelled())
        self.assertEqual(self.core.holds.finishing, {})

    def test_a_step_the_autopilot_queued_runs_to_its_end_with_no_reader(self):
        """`enqueue_step` opens no reader; the step's own task still ends the attempt,
        publishes `step.ended` and runs `after_end`."""
        ended: list[str] = []
        self.core.bus.subscribe("step.ended", lambda e: ended.append(e.unit))
        real = self.core.steps.after_end
        after: list[str] = []

        async def counted(*args):
            after.append(args[2])
            await real(*args)

        async def go():
            self.core.steps.after_end = counted
            attempt = self.core.steps.enqueue_step(str(self.repo), self.unit, "spec")
            for _ in range(1000):
                if after and not self.core.holds.finishing:
                    break
                await asyncio.sleep(0.01)
            return attempt

        row = self.core.attempts.get(asyncio.run(go()))
        self.assertEqual(
            (row["state"], row["outcome"], row["started_by"]), ("ended", "done", "autopilot")
        )
        self.assertEqual((ended, after), ([self.unit], ["spec"]))
        self.assertEqual(
            [r["kind"] for r in self.records()][-3:], ["end", "transition", "questions"]
        )
        self.assertEqual(self.core.steps.tasks, {})

    def test_an_enqueued_step_carries_the_apps_note_and_a_second_is_unit_busy(self):
        steps = self.core.steps
        first = steps.enqueue_step(str(self.repo), self.unit, "spec", note=" red: tests ")
        row = self.core.attempts.get(first)
        self.assertEqual(
            (row["state"], row["note"], row["note_by"]), ("queued", "red: tests", "app")
        )
        with self.assertRaises(Refused) as caught:
            steps.enqueue_step(str(self.repo), self.unit, "spec")
        self.assertEqual(caught.exception.reasons, ("unit-busy",))

    def test_run_step_refuses_a_note_that_says_it_is_the_autopilots(self):
        async def go():
            stream = self.core.steps.run_step(
                str(self.repo), self.unit, "spec", started_by="autopilot", note="mine"
            )
            await stream.__anext__()

        with self.assertRaises(Invalid):
            asyncio.run(go())
        self.assertEqual(self.core.attempts.unfinished(), [])


class AStepOutlivesItsReaderAndCanBeStopped(unittest.TestCase):
    """A step is its own task: a reader leaving does not end it, a second step on one unit is
    refused before it spends anything, two units run at once, the page can list them, and a Stop
    ends one `stopped` with nothing recorded after it."""

    class Waits:
        bus = Bus()

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
                await _submits(kw)
                yield (
                    "done",
                    {
                        "session_id": "sess-7",
                        "terminal_reason": "success",
                        "cost": {"output_tokens": 3, "cost_usd": 0.01},
                    },
                )
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
        self.core = Core(config, self.sessions)
        self.units = []
        for slug in ("a-problem", "b-problem"):
            made = create_sync(self.core, str(self.repo), slug, "some words")
            (Path(made["path"]) / "intent.md").write_text(
                "# Intent: x\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8"
            )
            self.units.append(made)
        self.sessions.units = [u["unit"] for u in self.units]
        self.ws = str(self.repo)

    def _release(self, made):
        self.sessions.gate(made["unit"]).set()

    async def _first_chunk(self, made):
        agen = self.core.steps.run_step(self.ws, made["unit"], "spec")
        first = await agen.__anext__()
        self.assertEqual(first[0], "chunk")
        return agen

    def _ends(self, unit):
        journal = self.core.ws.journal()
        return [r for r in journal.records() if r["kind"] == "end" and r["unit"] == unit]

    def test_a_reader_that_leaves_does_not_take_the_step_with_it(self):
        a = self.units[0]

        async def go():
            agen = await self._first_chunk(a)
            await agen.aclose()  # the NDJSON client went away
            running = live(self.core, a["unit"])
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
                await self.core.steps.run_step(self.ws, a["unit"], "spec").__anext__()
            self.assertEqual(self.sessions.calls, 1)  # refused before a session
            second = await self._first_chunk(b)
            listed = self.core.steps.running_steps(self.ws)
            self.assertEqual(sorted(r["unit"] for r in listed), sorted([a["unit"], b["unit"]]))
            self._release(a)
            self._release(b)
            outs = [[i async for i in g] for g in (first, second)]
            self.assertEqual([o[-1][1]["outcome"] for o in outs], ["done", "done"])
            self.assertEqual(self.core.steps.running_steps(self.ws), [])

        asyncio.run(go())

    def test_a_stop_ends_the_step_stopped_and_records_nothing_after_it(self):
        a = self.units[0]

        async def go():
            agen = await self._first_chunk(a)
            said = await self.core.steps.stop_step(self.ws, a["unit"], "Lan")
            self.assertEqual(said, {"unit": a["unit"], "stage": "spec", "stopped_by": "Lan"})
            # A second press is the same stop.
            running = live(self.core, a["unit"])
            if running is not None:
                await self.core.steps.stop_step(self.ws, a["unit"], "Minh")
            rest = [i async for i in agen]
            return rest

        rest = asyncio.run(go())
        self.assertEqual(rest[-1][1]["outcome"], "stopped")
        self.assertEqual(rest[-1][1]["stopped_by"], "Lan")
        self.assertFalse((Path(a["path"]) / "spec.md").exists())
        [end] = self._ends(a["unit"])
        self.assertEqual((end["outcome"], end["stopped_by"]), ("stopped", "Lan"))
        found = unit_history(self.core, self.ws, a["unit"])
        self.assertEqual([r for r in found["transitions"] if r["artifact"] == "spec.md"], [])
        self.assertIn("Status: accepted.", (Path(a["path"]) / "intent.md").read_text())
        self.assertEqual(self.core.steps.running_steps(self.ws), [])

    def test_a_stop_at_ending_is_recorded_and_the_step_runs_on_and_ends_stop_late(self):
        # a Stop that reaches a sealed step is not refused and not honoured halfway.
        from coscc.agent import steps as steps_mod

        a = self.units[0]

        async def go():
            agen = await self._first_chunk(a)
            running = live(self.core, a["unit"])
            steps_mod.seal(running)  # what the runner does as it begins the artifact
            [row] = self.core.attempts.unfinished()
            self.assertEqual(row["state"], "ending")
            said = await self.core.steps.stop_step(self.ws, a["unit"], "Lan")
            self.assertEqual(said["stopped_by"], "Lan")
            self.assertFalse(running.stop_requested)
            [asked] = self.core.steps.running_steps(self.ws)
            self.assertTrue(asked["stopping"])
            self._release(a)
            return row["id"], [i async for i in agen]

        attempt, rest = asyncio.run(go())
        self.assertEqual(rest[-1][1]["outcome"], "done")
        self.assertIn("Status: accepted.", (Path(a["path"]) / "spec.md").read_text())
        [end] = self._ends(a["unit"])
        self.assertEqual(end["outcome"], "done")
        ended = self.core.attempts.get(attempt)
        self.assertEqual((ended["state"], ended["outcome"]), ("ended", "stop_late"))
        self.assertEqual(ended["stop_asked_by"], "Lan")

    def test_a_stop_at_queued_ends_stopped_at_once_and_touches_no_session(self):
        # a click with no free slot is `queued`; its Stop ends it `stopped` with nothing
        # run, and its reader is told.
        a, b = self.units
        self.core.attempts.capacity = lambda workspace, slot: 1

        async def go():
            first = await self._first_chunk(a)
            second = self.core.steps.run_step(self.ws, b["unit"], "spec")
            waiting = asyncio.create_task(second.__anext__())
            for _ in range(200):
                if self.core.attempts.holding(self.core.ws.key(self.ws), b["unit"]):
                    break
                await asyncio.sleep(0.01)
            held = self.core.attempts.holding(self.core.ws.key(self.ws), b["unit"])
            self.assertEqual(held["state"], "queued")
            [listed] = [r for r in self.core.steps.running_steps(self.ws) if r["unit"] == b["unit"]]
            self.assertEqual(listed["state"], "queued")
            await self.core.steps.stop_step(self.ws, b["unit"], "Lan")
            with self.assertRaises(Invalid):
                await waiting
            self._release(a)
            await first.aclose()
            return held["id"]

        attempt = asyncio.run(go())
        ended = self.core.attempts.get(attempt)
        self.assertEqual((ended["state"], ended["outcome"]), ("ended", "stopped"))
        self.assertEqual(self.sessions.calls, 1)
        self.assertEqual(self._ends(b["unit"]), [])

    def test_stopping_nothing_is_refused_and_no_name_stops_as_owner(self):
        """A Stop with no name used to be refused; it now records `owner`."""
        a = self.units[0]

        async def go():
            with self.assertRaises(Invalid):
                await self.core.steps.stop_step(self.ws, a["unit"], "Lan")
            agen = await self._first_chunk(a)
            done = await self.core.steps.stop_step(self.ws, a["unit"], "  ")
            self.assertEqual(done["stopped_by"], "owner")
            self._release(a)
            try:
                [i async for i in agen]
            except Invalid, asyncio.CancelledError:
                pass

        asyncio.run(go())

    def test_shutdown_cancels_a_running_step_and_writes_no_end(self):
        a = self.units[0]

        async def go():
            agen = await self._first_chunk(a)
            await self.core.shutdown()
            with self.assertRaises(Invalid):
                [i async for i in agen]

        asyncio.run(go())
        self.assertEqual(self._ends(a["unit"]), [])
        # going down leaves the attempt as it was, for the next start to take up or end;
        # nothing live is kept for it here.
        self.assertEqual([r["unit"] for r in self.core.steps.running_steps(self.ws)], [a["unit"]])
        self.assertEqual(self.core.steps.tasks, {})


class AFailedAttemptReachesTheNextRunAndTheBoard(unittest.TestCase):
    class Empty:
        bus = Bus()

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            yield ("session", "sess-fail")
            await _submits(kw)
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
        self.core = Core(config, self.Empty())
        self.made = create_sync(self.core, str(self.repo), "a-problem", "some words")
        (Path(self.made["path"]) / "intent.md").write_text(
            "# Intent: a problem\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8"
        )

    def _run(self, stage: str) -> None:
        async def go():
            async for _ in self.core.steps.run_step(str(self.repo), self.made["unit"], stage):
                pass

        asyncio.run(go())

    def test_the_next_run_of_the_same_stage_sees_the_failed_attempt(self):
        self._run("spec")  # Empty: no Status line -> RunError -> outcome "failed"

        class Probe:
            bus = Bus()

            def __init__(self):
                self.seen = ""

            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                self.seen = text
                yield ("chunk", "# Spec: x\nAuthor: t. Status: accepted.\n")
                await _submits(kw)
                yield ("done", {"session_id": "sess-ok", "cost": {}})

        probe = Probe()
        use_sessions(self.core, probe)
        self._run("spec")
        self.assertIn("# The attempt before this one", probe.seen)
        self.assertIn("sess-fail", probe.seen)

    def test_a_run_after_done_carries_no_attempt_section(self):
        class Replies:
            bus = Bus()

            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                yield ("chunk", "# Spec: x\nAuthor: t. Status: accepted.\n")
                await _submits(kw)
                yield ("done", {"session_id": "sess-1", "cost": {}})

        use_sessions(self.core, Replies())
        self._run("spec")

        class Probe:
            bus = Bus()

            def __init__(self):
                self.seen = ""

            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                self.seen = text
                yield ("chunk", "# Spec: x\nAuthor: t. Status: accepted.\n")
                await _submits(kw)
                yield ("done", {"session_id": "sess-2", "cost": {}})

        probe = Probe()
        use_sessions(self.core, probe)
        self._run("spec")
        self.assertNotIn("# The attempt before this one", probe.seen)

    def test_board_distinguishes_a_failed_stage_from_a_never_run_one(self):
        self._run("spec")
        board = asyncio.run(self.core.board(str(self.repo)))
        [unit] = [u for u in board["units"] if u["name"] == self.made["unit"]]
        rows = {r["stage"]: r for r in unit["stages"]}
        self.assertIsNotNone(rows["spec"]["last_run"])
        self.assertNotEqual(rows["spec"]["last_run"]["outcome"], "done")
        self.assertIsNone(rows["plan"]["last_run"])

    def test_the_excerpt_never_reaches_a_route(self):
        # The transcript is faked via the runner's own read function, so this does not depend on a
        # real session store.
        with mock.patch(
            "coscc.runner.step.sessions_mod.transcript_excerpt",
            return_value=("CANARY-0019-EXCERPT", 999),
        ):
            self._run("spec")

        unit = self.made["unit"]
        board = asyncio.run(self.core.board(str(self.repo)))
        runs = timeline(self.core, str(self.repo), unit)
        for payload in (board, runs):
            self.assertNotIn("CANARY-0019-EXCERPT", json.dumps(payload))
        # And the attempt record itself does carry it — otherwise this test would pass
        # for the wrong reason.
        [attempt] = self.core.ws.journal().records(
            self.core.ws.key(str(self.repo)), unit, kind="attempt"
        )
        self.assertEqual(attempt["excerpt"], "CANARY-0019-EXCERPT")


class AStepTheGateClosesNeverStarts(unittest.TestCase):
    """`.claude/CLAUDE.md` invariant 2, enforced by the app for the first time.

    Until 2026-09-23 `run_step` went from reading the board straight to starting a
    session. The gate existed, the loop decided it, every skill opened by telling the
    stage to ask it — and the product asked nobody. The board would run `ship` on a unit
    whose `spec.md` had never been written.

    What these tests actually pin is the *ordering*: the refusal has to land before the
    session is created, because after that the money is already gone.
    """

    class NeverCalled:
        """A session layer that fails the test if a refused step reaches it."""

        bus = Bus()

        def __init__(self):
            self.calls = 0

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            # A prose stage reads `# Ship:` as no title and reopens this session once to repair it.
            # That is the same step, so only a new session is counted.
            self.calls += session_id is None
            yield ("chunk", "# Ship: no\nAuthor: t. Status: accepted.\n\n## Body\n")
            await _submits(kw)
            yield ("done", {"session_id": "s", "cost": {}})

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "work" / "proj"
        self.repo.mkdir(parents=True)
        self.sessions = self.NeverCalled()
        self.core = Core(
            Config(
                workspaces=(str(self.repo),),
                working_dir=str(self.root / "work"),
                data_dir=str(self.root / "data"),
            ),
            self.sessions,
        )
        self.made = create_sync(self.core, str(self.repo), "a-problem", "some words")
        (Path(self.made["path"]) / "intent.md").write_text(
            "# Intent: a problem\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8"
        )

    def _run(self, stage: str):
        async def go():
            out = []
            async for item in self.core.steps.run_step(str(self.repo), self.made["unit"], stage):
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
        """The store has no git, so `review` and `ship` read the workspace's."""
        from coscc.units import board as board_reader

        seen = {}

        async def fake_gate(units_root, unit, stage, repo=None, **kw):
            seen["repo"] = repo
            return False, "blocked: stop here"

        with mock.patch.object(board_reader, "gate", fake_gate):
            with self.assertRaises(Invalid):
                self._run("review")
        self.assertEqual(seen["repo"], str(self.repo))
        self.assertEqual(self.sessions.calls, 0)


class AnImplStepRunsUnderThePlansLabel(unittest.TestCase):
    """The label is read after the gate and picks the configuration; the gate is stubbed open, as
    the review tests below stub it."""

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
        self.core = Core(
            Config(
                workspaces=(str(self.repo),),
                working_dir=str(self.root / "work"),
                data_dir=str(self.root / "data"),
            ),
            self.Impl(self),
        )
        self.made = create_sync(self.core, str(self.repo), "a-problem", "some words")
        self.dir = Path(self.made["path"])
        self.seen: list[dict] = []
        self.terminal = None

    class Impl:
        bus = Bus()

        def __init__(self, test):
            self.test = test

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            self.test.seen.append({**kw, "max_turns": max_turns})
            (self.test.dir / "impl.md").write_text(
                "# Impl: x\nStatus: accepted.\n", encoding="utf-8"
            )
            await _submits(kw)
            yield (
                "done",
                {"session_id": "sess-i", "cost": {}, "terminal_reason": self.test.terminal},
            )

    def _run(self):
        from coscc.units import board as board_reader

        async def open_gate(units_root, unit, stage, repo=None, **kw):
            return True, "open: impl may proceed"

        async def go():
            return [
                i async for i in self.core.steps.run_step(str(self.repo), self.made["unit"], "impl")
            ]

        # A routine run asks for its arm's model; the Sonnet arm is `models.json`'s.
        with (
            mock.patch.object(board_reader, "gate", open_gate),
            mock.patch.object(modeltrial, "arm", lambda unit: modeltrial.SONNET_ARM),
        ):
            return asyncio.run(go())

    def _starts(self):
        journal = self.core.ws.journal()
        return journal.records(self.core.ws.key(str(self.repo)), kind="start")

    def test_a_plan_naming_the_security_surface_runs_as_novel(self):
        (self.dir / "plan.md").write_text(
            self.PLAN.format(path="`coscc/agent/policy.py`"), encoding="utf-8"
        )
        self._run()
        start = self._starts()[-1]
        self.assertEqual(
            (start["label_declared"], start["label"], start["label_source"]),
            ("routine", "novel", "forced"),
        )
        self.assertEqual((start["model"], start["effort"]), ("claude-opus-5-5[1m]", "high"))
        self.assertEqual(self.seen[-1].get("effort"), "high")
        self.assertEqual(start["impl_run"], 1)

    def test_a_novel_impl_gets_the_novel_ceilings(self):
        (self.dir / "plan.md").write_text(
            self.PLAN.format(path="`coscc/agent/policy.py`"), encoding="utf-8"
        )
        self._run()
        start = self._starts()[-1]
        self.assertEqual((start["label"], start["label_source"]), ("novel", "forced"))
        self.assertEqual(
            (self.seen[-1]["max_turns"], self.seen[-1].get("max_budget_usd")), (250, 16.0)
        )
        self.assertEqual(start["max_turns"], 250)

    def test_a_routine_run_escalates_after_a_max_turns_stop_and_counts_its_runs(self):
        (self.dir / "plan.md").write_text(
            self.PLAN.format(path="`coscc/units/board.py`"), encoding="utf-8"
        )
        self.terminal = "max_turns"
        self._run()
        self.terminal = None
        self._run()
        first, second = self._starts()[-2:]
        self.assertEqual(
            (first["label"], first["label_source"], first["model"], first["effort"]),
            ("routine", "declared", "claude-sonnet-5-5[1m]", "medium"),
        )
        self.assertEqual(
            (second["label"], second["label_source"], second["model"]),
            ("novel", "escalated", "claude-opus-5-5[1m]"),
        )
        self.assertEqual((first["impl_run"], second["impl_run"]), (1, 2))
        # The rerun also gets the ceilings it was escalated for.
        ceilings = [(kw["max_turns"], kw.get("max_budget_usd")) for kw in self.seen[-2:]]
        self.assertEqual(ceilings, [(120, 8.0), (250, 16.0)])


class AnImplStepUnderTheModelTrial(unittest.TestCase):
    """The fixture of `AnImplStepRunsUnderThePlansLabel`, with the arm forced by patching
    `modeltrial.arm`; the session stand-in names in `init` the model it was asked for, unless
    `self.init` says otherwise. `coscc.loop next` is a stub that counts its calls and answers
    `self.action`, or raises `self.next_fails`."""

    PLAN = AnImplStepRunsUnderThePlansLabel.PLAN
    ROUTINE = "`coscc/units/board.py`"
    SECURITY = "`coscc/agent/policy.py`"
    OPUS, SONNET = "claude-opus-5-5[1m]", "claude-sonnet-5-5[1m]"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.seen: list[dict] = []
        self.terminal = None
        self.asked: list[dict] = []
        self.action = "review: run a review round"
        self.reasons: list[str] = []
        self.next_fails: Exception | None = None
        self.made_count = 0
        # What the stand-in's `init` names: the model asked for, `None` for no `init` at all.
        self.init: str | None = ""

    class Impl(AnImplStepRunsUnderThePlansLabel.Impl):
        async def stream(self, cwd, text, session_id=None, max_turns=1, step=None, **kw):
            said = self.test.init
            if step is not None and said is not None:
                step.init_model = said or str(kw.get("model") or "")
                yield ("session", "sess-i")
            async for item in super().stream(cwd, text, session_id, max_turns, step=step, **kw):
                yield item

    def _unit(self, plan: str | None = ROUTINE, model: str | None = None):
        """A fresh workspace, service and unit, so two runs of one test do not share a log."""
        self.made_count += 1
        root = Path(self._tmp.name) / str(self.made_count)
        self.repo = root / "work" / "proj"
        self.repo.mkdir(parents=True)
        self.core = Core(
            Config(
                workspaces=(str(self.repo),),
                working_dir=str(root / "work"),
                data_dir=str(root / "data"),
                model=model,
            ),
            self.Impl(self),
        )
        self.made = create_sync(self.core, str(self.repo), "a-problem", "some words")
        self.dir = Path(self.made["path"])
        if plan is not None:
            (self.dir / "plan.md").write_text(self.PLAN.format(path=plan), encoding="utf-8")

    def _run(self, stage: str = "impl", arm: str = "opus-5-5"):
        from coscc.units import board as board_reader
        from coscc.agent import modeltrial

        async def open_gate(units_root, unit, stage, repo=None, **kw):
            return True, f"open: {stage} may proceed"

        async def next_step(units_root, unit, repo=None, **kw):
            self.asked.append({"unit": unit, "repo": repo})
            if self.next_fails is not None:
                raise self.next_fails
            return {
                "unit": unit,
                "stage": "impl",
                "action": self.action,
                "blocked": False,
                "waiting": [],
                "reasons": self.reasons,
            }

        async def go():
            return [
                i async for i in self.core.steps.run_step(str(self.repo), self.made["unit"], stage)
            ]

        with (
            mock.patch.object(board_reader, "gate", open_gate),
            mock.patch.object(board_reader, "next_step", next_step),
            mock.patch.object(modeltrial, "arm", lambda unit: arm),
        ):
            return asyncio.run(go())

    def _starts(self):
        journal = self.core.ws.journal()
        return journal.records(self.core.ws.key(str(self.repo)), kind="start")

    def _prefs(self):
        from coscc.agent import models
        from coscc.store.db import Data

        data = Data(self.core.config.data_dir)
        return {**data.pref_rows(models.PREFIX), **data.pref_rows(models.EFFORT_PREFIX)}

    def test_every_routine_impl_start_carries_arm_and_model(self):
        for arm, model in (("opus-5-5", self.OPUS), ("sonnet-5-5", self.SONNET)):
            with self.subTest(arm=arm):
                self._unit()
                self._run(arm=arm)
                start = self._starts()[0]
                self.assertEqual(
                    start["model_trial"], {"arm": arm, "requested": model, "model": model}
                )
                self.assertEqual((start["model"], start["model_source"]), (model, "trial"))
                self.assertEqual(self.seen[-1].get("model"), model)
                # Both arms run the default's effort.
                self.assertEqual((start["effort"], start["effort_source"]), ("medium", "default"))
                self.assertNotIn("effort_trial", start)

    def test_an_override_keeps_the_arm_and_records_the_real_model(self):
        self._unit()
        self.core.agents.set_agent_field("impl", "model", "claude-other")
        before = self._prefs()
        self._run(arm="opus-5-5")
        start = self._starts()[0]
        self.assertEqual((start["model"], start["model_source"]), ("claude-other", "override"))
        self.assertEqual(
            start["model_trial"],
            {"arm": "opus-5-5", "requested": "claude-other", "model": "claude-other"},
        )
        self.assertEqual(self._prefs(), before)
        self._unit(model="from-env")
        self._run(arm="opus-5-5")
        start = self._starts()[0]
        self.assertEqual((start["model"], start["model_source"]), ("from-env", "COS_MODEL"))
        self.assertEqual(start["model_trial"]["arm"], "opus-5-5")

    def test_the_model_init_names_is_the_model_recorded(self):
        # `init` names what the CLI resolved, which is what is kept.
        self._unit()
        self.init = "claude-opus-5-5"
        self._run(arm="opus-5-5")
        self.assertEqual(self._starts()[0]["model_trial"]["model"], "claude-opus-5-5")

    def test_a_session_dead_before_init_is_recorded_never_started(self):
        self._unit()
        self.init = None
        self._run(arm="sonnet-5-5")
        self.assertEqual(
            self._starts()[0]["model_trial"],
            {"arm": "sonnet-5-5", "requested": self.SONNET, "model": "never-started"},
        )

    def test_a_novel_impl_is_not_in_the_trial(self):
        self._unit(plan=self.SECURITY)
        self._run(arm="sonnet-5-5")
        start = self._starts()[0]
        self.assertEqual((start["label"], start["label_source"]), ("novel", "forced"))
        self.assertEqual((start["model"], start["model_source"]), (self.OPUS, "default"))
        self.assertNotIn("model_trial", start)

    def test_an_impl_with_no_declared_label_is_not_in_the_trial(self):
        self._unit(plan=None)
        (self.dir / "plan.md").write_text(
            self.PLAN.format(path=self.ROUTINE).replace(" Impl: routine.", ""), encoding="utf-8"
        )
        self._run(arm="sonnet-5-5")
        start = self._starts()[0]
        self.assertEqual((start["label"], start["label_source"]), ("novel", "missing"))
        self.assertNotIn("model_trial", start)

    def test_an_escalated_impl_leaves_the_trial(self):
        self._unit()
        self.terminal = "max_turns"
        self._run(arm="sonnet-5-5")
        self.terminal = None
        self._run(arm="sonnet-5-5")
        first, second = self._starts()
        self.assertEqual(first["model_trial"]["arm"], "sonnet-5-5")
        self.assertEqual((second["label"], second["label_source"]), ("novel", "escalated"))
        self.assertNotIn("model_trial", second)

    def test_other_stages_carry_no_trial(self):
        self._unit()
        self._run(stage="spec", arm="sonnet-5-5")
        on = self._starts()[0]
        self.assertEqual((on["model"], on["model_source"]), (self.OPUS, "default"))
        self.assertNotIn("model_trial", on)
        self.assertEqual(self.asked, [])

    # -- the CI question, now asked of every routine impl ---------------------

    def test_a_second_impl_asks_next_once_and_records_ci_red(self):
        self._unit()
        self._run()
        self.action = "CI is red on #7: tests — back to impl: fix on the branch and push"
        self.reasons = ["ci-red"]
        self._run()
        self.assertEqual(self.asked, [{"unit": self.made["unit"], "repo": str(self.repo)}])
        self.assertIs(self._starts()[1]["ci_red"], True)

    def test_a_second_impl_after_a_review_records_no_red(self):
        self._unit()
        self._run()
        self.action = "impl: review round 1 asked for changes"
        self._run()
        self.assertEqual(len(self.asked), 1)
        self.assertIs(self._starts()[1]["ci_red"], False)

    def test_next_failing_never_refuses_the_step(self):
        from coscc.units.board import Unavailable

        self._unit()
        self._run()
        self.next_fails = Unavailable("gh is not logged in")
        self.seen.clear()
        self._run()
        start = self._starts()[1]
        self.assertIn("ci_red", start)
        self.assertIsNone(start["ci_red"])
        self.assertEqual(len(self.seen), 1)

    def test_a_first_impl_asks_nothing(self):
        self._unit()
        self._run()
        self.assertEqual(self._starts()[0]["impl_run"], 1)
        self.assertNotIn("ci_red", self._starts()[0])
        self.assertEqual(self.asked, [])


class TheNextStageComesFromTheScript(unittest.TestCase):
    """`Steps.next_step` asks `coscc.loop next` and chooses nothing itself."""

    # The fixture of the class above, borrowed rather than inherited so its tests run once.
    NeverCalled = AStepTheGateClosesNeverStarts.NeverCalled
    setUp = AStepTheGateClosesNeverStarts.setUp
    _run = AStepTheGateClosesNeverStarts._run

    def _next(self, unit: str | None = None, cwd: str | None = None):
        return asyncio.run(
            self.core.steps.next_step(
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
        from coscc.units import board as board_reader

        seen = {}

        async def fake_next(units_root, unit, repo=None, **kw):
            seen["next"] = (str(units_root), repo)
            return {"unit": unit, "stage": "review", "action": "a", "blocked": True}

        async def fake_gate(units_root, unit, stage, repo=None, **kw):
            seen["gate"] = (str(units_root), repo)
            return False, "blocked: stop here"

        with (
            mock.patch.object(board_reader, "next_step", fake_next),
            mock.patch.object(board_reader, "gate", fake_gate),
        ):
            stage = self._next()["stage"]
            with self.assertRaises(Invalid):
                self._run(stage)
        self.assertEqual(seen["next"], seen["gate"])
        self.assertEqual(seen["next"][1], str(self.repo))

    def test_waiting_is_copied_and_absent_reads_as_none(self):
        """The findings a person is awaited on reach the page as the loop named them."""
        from coscc.units import board as board_reader

        answers = [
            {
                "unit": "u",
                "stage": "",
                "action": "needs a person — F3: x",
                "blocked": True,
                "waiting": ["F3"],
            },
            {"unit": "u", "stage": "review", "action": "a", "blocked": True},
        ]

        async def fake_next(units_root, unit, repo=None, **kw):
            # `next_step` first asks with no `--repo` whether the unit is held.
            if repo is None:
                return {"unit": "u", "stage": "", "action": "", "blocked": True, "hold": None}
            return answers.pop(0)

        with mock.patch.object(board_reader, "next_step", fake_next):
            first, second = self._next(), self._next()
        self.assertEqual((first["stage"], first["waiting"]), ("", ["F3"]))
        self.assertEqual(second["waiting"], [])

    def test_continue_is_copied_and_absent_reads_as_empty(self):
        """An `impl` left a draft reaches the autopilot as the loop named it."""
        from coscc.units import board as board_reader

        answers = [
            {"unit": "u", "stage": "", "action": "a", "blocked": True, "continue": "impl"},
            {"unit": "u", "stage": "review", "action": "a", "blocked": True},
        ]

        async def fake_next(units_root, unit, repo=None, **kw):
            if repo is None:
                return {"unit": "u", "stage": "", "action": "", "blocked": True, "hold": None}
            return answers.pop(0)

        with mock.patch.object(board_reader, "next_step", fake_next):
            first, second = self._next(), self._next()
        self.assertEqual((first["continue"], second["continue"]), ("impl", ""))

    def test_dropped_is_copied_and_absent_reads_as_none(self):
        """The ids the last round left out reach the page as the loop listed them, not inside
        `action`."""
        from coscc.units import board as board_reader

        answers = [
            {
                "unit": "u",
                "stage": "review",
                "action": "a",
                "blocked": True,
                "dropped": ["F2", "F3"],
            },
            {"unit": "u", "stage": "review", "action": "a", "blocked": True},
        ]

        async def fake_next(units_root, unit, repo=None, **kw):
            if repo is None:
                return {"unit": "u", "stage": "", "action": "", "blocked": True, "hold": None}
            return answers.pop(0)

        with mock.patch.object(board_reader, "next_step", fake_next):
            first, second = self._next(), self._next()
        self.assertEqual(first["dropped"], ["F2", "F3"])
        self.assertEqual(second["dropped"], [])


class ShipRunsOutsideTheWorktree(unittest.TestCase):
    """Inside a worktree, `gh pr merge --delete-branch` merged and then exited 1 on `'main' is
    already used by worktree`. Only `ship`'s session moves."""

    def test_ship_runs_in_the_units_directory(self):
        self.assertEqual(step_cwd("ship", "/w/tree", Path("/store/0017_x")), "/store/0017_x")

    def test_every_other_stage_keeps_the_worktree(self):
        for stage in ("idea", "intent", "spec", "plan", "impl", "pr", "review"):
            self.assertEqual(step_cwd(stage, "/w/tree", Path("/store/0017_x")), "/w/tree")

    def test_spike_runs_in_its_scratch(self):
        self.assertEqual(
            step_cwd("spike", "/w/tree", Path("/store/x"), "/data/spikes/s/x"), "/data/spikes/s/x"
        )


class ASpikeRunsInAScratchTheAppRemoves(unittest.TestCase):
    """The scratch is emptied before the step and gone after it."""

    class Probe:
        bus = Bus()

        def __init__(self, fail: bool = False):
            self.fail = fail
            self.seen: list[tuple[str, list[str], str | None]] = []

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            self.seen.append(
                (cwd, sorted(p.name for p in Path(cwd).iterdir()), kw.get("workspace"))
            )
            (Path(cwd) / "probe.py").write_text("print(1)\n", encoding="utf-8")
            if self.fail:
                raise RuntimeError("the session broke")
            yield (
                "chunk",
                "# Spike: x\nSpec: spec.md. Round: 1. Status: accepted.\n\n"
                "## U1\n\nVerdict: holds.\n\n```\n$ x\n1\n```\n",
            )
            await _submits(kw)
            yield ("done", {"session_id": "sess-spike", "cost": {}})

    class RunsOut:
        """Keeps its progress file in `cwd`, then stops at the turn ceiling."""

        bus = Bus()

        PROGRESS = (
            "# Spike: x\nSpec: spec.md. Author: ᛈ Perthro. Round: 1. Status: accepted.\n\n"
            "## U1\n\nVerdict: holds.\n\n```\n$ x\n1\n```\n"
        )

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            (Path(cwd) / "spike.md").write_text(self.PROGRESS, encoding="utf-8")
            yield ("chunk", "Tôi hết lượt.")
            await _submits(kw)
            yield ("done", {"session_id": "sess-spike", "terminal_reason": "max_turns", "cost": {}})

    def test_the_progress_file_is_read_before_the_scratch_is_removed(self):
        core = self._core(self.RunsOut())
        out = self._run(core)
        self.assertEqual(out[-1][1]["outcome"], "exhausted", out[-1])
        written = Path(core.ws.unit_dir(str(self.repo), self.unit)) / "spike.md"
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
                [
                    "git",
                    "-c",
                    "user.name=T",
                    "-c",
                    "user.email=t@example.invalid",
                    "-c",
                    "commit.gpgsign=false",
                    "commit",
                    "-q",
                    "--allow-empty",
                    "-m",
                    "first",
                ],
                cwd=where,
                check=True,
            )

    def _core(self, probe) -> Core:
        core = Core(
            Config(
                workspaces=(str(self.repo),),
                working_dir=str(self.root / "work"),
                data_dir=str(self.root / "data"),
            ),
            probe,
        )
        made = create_sync(core, str(self.repo), "a-problem", "some words")
        d = Path(made["path"])
        (d / "intent.md").write_text(
            "# Intent: x\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8"
        )
        (d / "spec.md").write_text(
            "# Spec: x\nIntent: intent.md. Author: t. Status: accepted.\n\n## Concerns\n\n"
            "- [unmeasured] U1. does it exit?\n",
            encoding="utf-8",
        )
        self.unit = made["unit"]
        self.scratch = units.spike_dir(str(self.repo), self.unit, str(self.root / "data"))
        return core

    def _run(self, core):
        tree = {"path": str(self.tree), "branch": "feat/a-problem", "base": None}

        async def go():
            with mock.patch.object(core.steps, "worktree", mock.AsyncMock(return_value=tree)):
                return [i async for i in core.steps.run_step(str(self.repo), self.unit, "spike")]

        return asyncio.run(go())

    def test_the_scratch_is_used_and_then_gone(self):
        probe = self.Probe()
        core = self._core(probe)
        out = self._run(core)
        self.assertEqual(out[-1][1]["outcome"], "done", out[-1])
        self.assertEqual(probe.seen, [(str(self.scratch), [], str(self.repo))])
        self.assertFalse(self.scratch.exists())
        self.assertTrue((Path(core.ws.unit_dir(str(self.repo), self.unit)) / "spike.md").exists())

    def test_the_scratch_is_gone_when_the_session_fails(self):
        probe = self.Probe(fail=True)
        core = self._core(probe)
        out = self._run(core)
        self.assertEqual(out[-1][1]["outcome"], "failed")
        self.assertFalse(self.scratch.exists())

    def test_a_scratch_left_behind_is_emptied_before_the_step(self):
        probe = self.Probe()
        core = self._core(probe)
        self.scratch.mkdir(parents=True)
        (self.scratch / "stale.txt").write_text("old", encoding="utf-8")
        self._run(core)
        self.assertEqual(probe.seen[0][1], [])
        self.assertFalse(self.scratch.exists())

    def test_a_workspace_with_no_git_is_refused(self):
        probe = self.Probe()
        core = self._core(probe)
        shutil.rmtree(self.repo / ".git")

        async def go():
            return [i async for i in core.steps.run_step(str(self.repo), self.unit, "spike")]

        with self.assertRaises(Invalid) as caught:
            asyncio.run(go())
        self.assertIn("spike needs a git worktree", str(caught.exception))
        self.assertEqual(probe.seen, [])


class APrStepIsMechanical(unittest.TestCase):
    """The same three answers of `gh pr list` now decide what the step does -- take the pull request
    the branch has, open one, or fail with what `gh` said. The fixture is
    `AUnitsBaseIsTheRemoteTrunk`'s, whose bare remote takes the push."""

    setUp = AUnitsBaseIsTheRemoteTrunk.setUp
    _git = AUnitsBaseIsTheRemoteTrunk._git
    _typed_unit = AUnitsBaseIsTheRemoteTrunk._typed_unit

    class NoSession:
        bus = Bus()

        async def stream(self, *a, **kw):
            raise AssertionError("a pr step opened a session")
            yield  # pragma: no cover

    Replies = NoSession

    class Gh:
        """`integrate._gh` and `gh.run`, in memory."""

        def __init__(self, rows: list[dict] | None):
            self.rows = rows
            self.calls: list[list[str]] = []

        async def __call__(self, argv, cwd, stdin=None):
            self.calls.append(list(argv))
            if argv[:2] == ["pr", "list"]:
                return (
                    (1, "", "error connecting to api.github.com")
                    if self.rows is None
                    else (0, json.dumps(self.rows), "")
                )
            if argv[:2] == ["pr", "create"]:
                return 0, "https://github.com/o/r/pull/8\n", ""
            if argv[:2] == ["pr", "view"]:
                return 0, json.dumps({"title": "", "body": ""}), ""
            return 0, "", ""

        def of(self, verb: str) -> list[list[str]]:
            return [c for c in self.calls if c[:2] == ["pr", verb]]

    def _run_pr(self, gh: Gh) -> tuple[dict, str]:
        from coscc.units import board as board_reader
        from coscc.github import integrate
        from coscc.store.journal import Journal

        unit = self._typed_unit()
        self._git("branch", "fix/a-problem")
        use_sessions(self.core, self.NoSession())

        async def open_gate(units_root, unit, stage, repo=None, **kw):
            return True, "open: pr may proceed"

        async def go():
            return [item async for item in self.core.steps.run_step(str(self.repo), unit, "pr")]

        with (
            mock.patch.object(board_reader, "gate", open_gate),
            mock.patch.object(integrate, "_gh", gh),
            mock.patch("coscc.git.gh.run", gh),
        ):
            items = asyncio.run(go())
        self.assertEqual([k for k, _ in items], ["done"])
        j = Journal(self.config.working_dir, self.config.data_dir)
        self.assertEqual(
            [
                r
                for r in j.records(str(self.repo.resolve()), kind="start")
                if r.get("stage") == "pr"
            ],
            [],
        )
        return items[-1][1], unit

    def _pushed(self) -> str:
        return subprocess.run(
            [
                "git",
                "-C",
                str(self.remote),
                "rev-parse",
                "--verify",
                "--quiet",
                "refs/heads/fix/a-problem",
            ],
            capture_output=True,
            text=True,
        ).stdout.strip()

    def test_found(self):
        gh = self.Gh(
            [{"url": "https://github.com/o/r/pull/7", "number": 7, "headRefOid": "a" * 40}]
        )
        done, unit = self._run_pr(gh)
        self.assertEqual(
            (done["outcome"], done["mechanical"]["result"], done["mechanical"]["number"]),
            ("done", "found", 7),
        )
        self.assertEqual(gh.of("create"), [])
        self.assertTrue(self._pushed())
        text = (self.core.ws.unit_dir(str(self.repo), unit) / "pr.md").read_text(encoding="utf-8")
        self.assertIn("# PR: fix(0001): a problem", text)
        self.assertEqual(done["pr_sync"]["existed"], True)

    def test_none(self):
        gh = self.Gh([])
        done, _ = self._run_pr(gh)
        self.assertEqual(
            (done["mechanical"]["result"], done["mechanical"]["number"]), ("opened", 8)
        )
        [create] = gh.of("create")
        self.assertEqual(create[create.index("--title") + 1], "fix(0001): a problem")
        self.assertEqual(done["pr_sync"]["existed"], False)

    def test_unknown_fails_and_records_nothing(self):
        gh = self.Gh(None)
        done, unit = self._run_pr(gh)
        self.assertEqual(done["outcome"], "failed")
        self.assertIn("error connecting", done["error"])
        self.assertEqual(gh.of("create"), [])
        history = self.core.ws.unit_meta().history
        self.assertEqual(history.transitions(self.core.ws.key(str(self.repo)), unit, "pr.md"), [])


class APrStepPutsPrMdOntoItsPullRequest(unittest.TestCase):
    """Through `run_step`: after a `pr` step that was not stopped, the title and body
    `coscc.loop pr-text` cut from `pr.md` are on the pull request, and one `pr-sync` row says how.
    `APrStepIsHandedItsPullRequest`'s fixture, with `gh` in memory."""

    setUp = AUnitsBaseIsTheRemoteTrunk.setUp
    _git = AUnitsBaseIsTheRemoteTrunk._git
    _typed_unit = AUnitsBaseIsTheRemoteTrunk._typed_unit

    TITLE = "a problem, fixed"
    BODY = "## Where\n\nhttps://github.com/o/r/pull/7, checks pending.\n"
    ACCEPTED = f"# PR: {TITLE}\nIntent: intent.md. PR: https://github.com/o/r/pull/7. Author: t. Status: accepted.\n\n{BODY}"

    class Replies:
        """A session that writes `pr.md` itself; with `hold`, it then waits to be stopped."""

        bus = Bus()

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
                await _submits(kw)
                yield ("done", {"session_id": "sess-55", "cost": {}})
            finally:
                if step is not None:
                    await step.close()

    class Gh:
        """Both `integrate._gh` and `gh.run`: one pull request, in memory."""

        def __init__(
            self,
            listed: bool,
            title: str = "temporary",
            body: str = "temporary",
            fail=None,
            raise_=None,
            scope=None,
        ):
            self.listed, self.title, self.body = listed, title, body
            self.fail, self.raise_, self.scope = fail, raise_, scope
            self.calls: list[tuple[list[str], str | None]] = []

        async def __call__(self, argv, cwd, stdin=None):
            self.calls.append((list(argv), stdin))
            if argv[:2] == ["pr", "list"] and self.fail == "list":
                return 1, "", "error connecting to api.github.com"
            if argv[:2] == ["pr", "list"]:
                rows = (
                    [
                        {
                            "url": "https://github.com/o/r/pull/7",
                            "number": 7,
                            "mergeable": "MERGEABLE",
                            "headRefOid": "a" * 40,
                        }
                    ]
                    if self.listed
                    else []
                )
                return 0, json.dumps(rows), ""
            if self.raise_ is not None:
                raise self.raise_
            if self.fail and argv[:2] == ["pr", self.fail]:
                return 1, "", "HTTP 422: Validation Failed"
            if argv[:2] == ["pr", "view"] and argv[-1] == "comments":
                return 0, json.dumps({"comments": []}), ""
            if argv[:2] == ["pr", "view"] and argv[-1] == prscope.FIELDS:
                return 0, json.dumps(self.scope or {}), ""
            if argv[:2] == ["pr", "view"]:
                return 0, json.dumps({"title": self.title, "body": self.body}), ""
            if argv[:2] == ["pr", "edit"]:
                self.title = next(
                    (a[len("--title=") :] for a in argv if a.startswith("--title=")), self.title
                )
                self.body = stdin
                return 0, "https://github.com/o/r/pull/7\n", ""
            return 0, "", ""

        def of(self, sub: str, json_: str | None = None):
            return [
                c
                for c in self.calls
                if c[0][:2] == ["pr", sub] and (json_ is None or c[0][-1] == json_)
            ]

    def _run(self, text, gh, stage="pr", hold=False, prepare=None, gate=None, via_step=False):
        """A `pr` step writes `pr.md` itself and runs no session, so a `pr.md` of any other words --
        `text` -- reaches `sync_pr` only by calling it as the step does, with what the PR machine
        found. `via_step` runs the step itself."""
        from coscc.units import board as board_reader
        from coscc.github import integrate

        unit = self._typed_unit()
        self._git("branch", "fix/a-problem")
        directory = self.core.ws.unit_dir(str(self.repo), unit)
        if prepare:
            prepare(directory)
        replies = self.Replies(text, hold)
        replies.directory = directory
        use_sessions(self.core, replies)

        async def open_gate(units_root, unit, stage, repo=None, **kw):
            return True, f"open: {stage} may proceed"

        open_gate = gate or open_gate

        async def go():
            if stage == "pr" and not via_step:
                if text is not None:
                    (directory / "pr.md").write_text(text, encoding="utf-8")
                await self.core.answers.ingest(
                    str(self.repo), unit, {"outcome": "done", "stage": "pr"}, "pr.md"
                )
                # What the PR machine hands on: no answer, no pull request, or its URL.
                url = "https://github.com/o/r/pull/7" if gh.listed else ""
                before = None if gh.fail == "list" else url
                return [
                    (
                        "done",
                        {
                            "outcome": "done",
                            "pr_sync": await self.core.answers.sync_pr(
                                str(self.repo), unit, before
                            ),
                        },
                    )
                ]
            agen = self.core.steps.run_step(str(self.repo), unit, stage)
            return [i async for i in agen]

        with (
            mock.patch.object(board_reader, "gate", open_gate),
            mock.patch.object(integrate, "_gh", gh),
            mock.patch("coscc.git.gh.run", gh),
        ):
            out = asyncio.run(go())
        rows = self.core.ws.journal().records(
            self.core.ws.key(str(self.repo)), unit, kind="pr-sync"
        )
        return out[-1][1], rows, directory

    def test_a_pull_request_that_existed_gets_the_title_and_body(self):
        gh = self.Gh(listed=True)
        done, [row], _ = self._run(self.ACCEPTED, gh)
        [(argv, stdin)] = gh.of("edit")
        self.assertEqual(
            argv,
            [
                "pr",
                "edit",
                "https://github.com/o/r/pull/7",
                f"--title={self.TITLE}",
                "--body-file",
                "-",
            ],
        )
        self.assertEqual(stdin, self.BODY)
        self.assertEqual((gh.title, gh.body), (self.TITLE, self.BODY))
        self.assertEqual(
            (row["outcome"], row["existed"], row["pr"]),
            ("updated", True, "https://github.com/o/r/pull/7"),
        )
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
        # A lookup that failed is not "no pull request".
        gh = self.Gh(listed=True, fail="list")
        done, [row], _ = self._run(self.ACCEPTED, gh)
        self.assertEqual((row["outcome"], row["existed"]), ("updated", None))
        self.assertIsNone(done["pr_sync"]["existed"])

    def test_already_there_is_not_written_again(self):
        gh = self.Gh(listed=True, title=self.TITLE, body=self.BODY)
        done, [row], _ = self._run(self.ACCEPTED, gh)
        self.assertEqual(gh.of("edit"), [])
        self.assertEqual(row["outcome"], "already")

    def test_a_pr_step_that_fails_calls_nothing_and_writes_no_row(self):
        gh = self.Gh(listed=True, fail="list")
        done, rows, _ = self._run(None, gh, via_step=True)
        self.assertEqual(done["outcome"], "failed")
        self.assertEqual((gh.of("view"), gh.of("edit"), rows), ([], [], []))
        self.assertNotIn("pr_sync", done)

    def test_a_draft_or_a_missing_url_is_skipped_without_gh(self):
        for text in (
            self.ACCEPTED.replace("Status: accepted", "Status: draft"),
            self.ACCEPTED.replace("PR: https://github.com/o/r/pull/7. ", ""),
        ):
            with self.subTest(text=text.splitlines()[1]):
                self.setUp()
                gh = self.Gh(listed=True)
                done, [row], _ = self._run(text, gh)
                self.assertEqual((gh.of("view"), gh.of("edit")), ([], []))
                self.assertEqual(row["outcome"], "skipped")
                self.assertTrue(row["detail"])

    def test_impl_and_review_do_not_sync(self):
        for stage in ("impl", "review"):
            with self.subTest(stage=stage):
                self.setUp()
                gh = self.Gh(listed=True)
                _, rows, _ = self._run(
                    None,
                    gh,
                    stage=stage,
                    prepare=lambda d: (d / "pr.md").write_text(self.ACCEPTED, encoding="utf-8"),
                )
                self.assertEqual((rows, gh.of("edit"), gh.of("view", "title,body")), ([], [], []))

    def test_a_refusal_leaves_the_step_done_and_pr_md_as_it_was(self):
        gh = self.Gh(listed=True, fail="edit")
        done, [row], directory = self._run(self.ACCEPTED, gh)
        self.assertEqual(done["outcome"], "done")
        self.assertEqual((row["outcome"], row["detail"]), ("failed", "HTTP 422: Validation Failed"))
        self.assertEqual((directory / "pr.md").read_text(encoding="utf-8"), self.ACCEPTED)

    def test_a_timeout_is_failed_and_says_so(self):
        gh = self.Gh(listed=True, raise_=asyncio.TimeoutError())
        done, [row], _ = self._run(self.ACCEPTED, gh)
        self.assertEqual(done["outcome"], "done")
        self.assertEqual(row["outcome"], "failed")
        self.assertIn("timed out", row["detail"])

    SCOPED = f"{ACCEPTED}\n## Scope of the diff\n\n2 files, +10/-3\n- `a.py`\n- `b.py`\n"
    GH_SCOPE = {
        "changedFiles": 2,
        "additions": 10,
        "deletions": 3,
        "files": [{"path": "a.py"}, {"path": "b.py"}],
    }

    def test_the_pr_sync_row_carries_githubs_counts_and_a_verdict(self):
        gh = self.Gh(listed=True, scope=self.GH_SCOPE)
        done, [row], _ = self._run(self.SCOPED, gh)
        self.assertEqual(row["outcome"], "updated")
        self.assertEqual(
            row["scope"],
            {"github": {"files": 2, "additions": 10, "deletions": 3}, "verdict": "match"},
        )
        self.assertEqual(done["pr_sync"]["scope"]["verdict"], "match")
        [(argv, stdin)] = gh.of("view", prscope.FIELDS)
        self.assertEqual(
            (argv, stdin),
            (["pr", "view", "https://github.com/o/r/pull/7", "--json", prscope.FIELDS], None),
        )

    def test_a_mismatch_leaves_the_step_done_and_pr_md_as_it_was(self):
        gh = self.Gh(
            listed=True,
            scope={
                **self.GH_SCOPE,
                "changedFiles": 3,
                "files": [{"path": "a.py"}, {"path": "c.py"}],
            },
        )
        done, [row], directory = self._run(self.SCOPED, gh)
        self.assertEqual(done["outcome"], "done")
        self.assertEqual(row["outcome"], "updated")
        self.assertEqual(
            {k: row["scope"][k] for k in ("verdict", "differ", "only_in_pr_md", "only_on_github")},
            {
                "verdict": "mismatch",
                "differ": ["files"],
                "only_in_pr_md": ["b.py"],
                "only_on_github": ["c.py"],
            },
        )
        self.assertEqual((directory / "pr.md").read_text(encoding="utf-8"), self.SCOPED)

    def _pr_md(self, text):
        return lambda d: (d / "pr.md").write_text(text, encoding="utf-8")

    def test_a_ship_step_puts_pr_md_up_before_the_gate_is_asked(self):
        gh = self.Gh(listed=True, title="changed on GitHub")
        seen: list[str] = []

        async def gate(units_root, unit, stage, repo=None, **kw):
            seen.append(gh.title)
            return True, f"open: {stage} may proceed"

        _, rows, _ = self._run(
            None, gh, stage="ship", prepare=self._pr_md(self.ACCEPTED), gate=gate
        )
        self.assertEqual(seen, [self.TITLE])
        self.assertEqual((gh.title, gh.body), (self.TITLE, self.BODY))
        [row] = rows
        self.assertEqual(
            (row["stage"], row["outcome"], row["pr"]),
            ("ship", "updated", "https://github.com/o/r/pull/7"),
        )
        self.assertNotIn("existed", row)

    def test_ship_reads_no_scope(self):
        gh = self.Gh(listed=True, title=self.TITLE, body=self.BODY, scope=self.GH_SCOPE)
        _, [row], _ = self._run(None, gh, stage="ship", prepare=self._pr_md(self.SCOPED))
        self.assertEqual(gh.of("view", prscope.FIELDS), [])
        self.assertNotIn("scope", row)
        self.assertEqual(row["stage"], "ship")

    def test_a_failed_sync_still_asks_the_gate_and_a_closed_gate_refuses(self):
        gh = self.Gh(listed=True, fail="edit")
        asked: list[str] = []

        async def closed(units_root, unit, stage, repo=None, **kw):
            asked.append(stage)
            return False, 'blocked: ship cannot proceed\n  - #7 carries the title "temporary"'

        with self.assertRaises(Invalid) as refused:
            self._run(None, gh, stage="ship", prepare=self._pr_md(self.ACCEPTED), gate=closed)
        self.assertIn("carries the title", str(refused.exception))
        self.assertEqual(asked, ["ship"])
        [row] = self.core.ws.journal().records(self.core.ws.key(str(self.repo)), kind="pr-sync")
        self.assertEqual(
            (row["stage"], row["outcome"], row["detail"]),
            ("ship", "failed", "HTTP 422: Validation Failed"),
        )
        self.assertEqual(len(gh.of("edit")), 1, "the scope read writes nothing of its own")

    def test_a_skipped_sync_has_no_scope_and_no_read(self):
        gh = self.Gh(listed=True, scope=self.GH_SCOPE)
        done, [row], _ = self._run(self.SCOPED.replace("Status: accepted", "Status: draft"), gh)
        self.assertEqual(row["outcome"], "skipped")
        self.assertNotIn("scope", row)
        self.assertEqual(gh.of("view", prscope.FIELDS), [])


class RunStepHandsOnThePlanMap(unittest.TestCase):
    """`run_step` hands an `impl` step the files its plan names as they stand in the step's tree,
    and every other stage no key. The gate, the worktree and the runner are stand-ins: what is
    checked is the kwargs `Runner.run` gets."""

    PLAN = (
        "# Plan: x\nStatus: accepted.\n\n## Files that change\n\n- `pkg/m.py`.\n- `pkg/later.py`.\n"
    )

    def plan(self, text: str | bytes) -> None:
        path = units.unit_dir(str(self.repo), self.unit, str(self.data)) / "plan.md"
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")

    def test_pr_and_ship_reach_no_runner(self):
        from coscc.units import board as board_reader

        core = self.core()

        async def open_gate(units_root, unit, stage, repo=None, **kw):
            return True, f"open: {stage} may proceed"

        async def tree(*a, **kw):
            return {"path": str(self.repo), "branch": "feat/a-problem", "base": None}

        async def mechanical(cwd, key, unit, stage, tree, started_by, again=False, rebased=None):
            return {"unit": unit, "stage": stage, "outcome": "done"}

        async def go(stage):
            return [i async for i in core.steps.run_step(str(self.repo), self.unit, stage)]

        for stage in ("pr", "ship"):
            with (
                self.subTest(stage=stage),
                mock.patch.object(board_reader, "gate", open_gate),
                mock.patch(
                    "coscc.runner.steps.Runner", side_effect=AssertionError("a runner was made")
                ),
                mock.patch.object(core.steps, "worktree", tree),
                mock.patch.object(core.steps, "sync_pr", mock.AsyncMock()),
                mock.patch.object(core.steps, "mechanical", mechanical),
            ):
                self.assertEqual(
                    asyncio.run(go(stage)),
                    [("done", {"unit": self.unit, "stage": stage, "outcome": "done"})],
                )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "work" / "proj"
        (self.repo / ".git").mkdir(parents=True)
        self.data = self.root / "data"
        self.seen: list[dict] = []
        (self.repo / "pkg").mkdir()
        (self.repo / "pkg" / "m.py").write_text(
            "import os\n\n\ndef top():\n    pass\n", encoding="utf-8"
        )

    def core(self) -> Core:
        config = Config(
            workspaces=(str(self.repo),),
            working_dir=str(self.root / "work"),
            data_dir=str(self.data),
        )
        core = Core(config, Sessions(config))
        self.unit = create_sync(core, str(self.repo), "a-problem", "words")["unit"]
        return core

    def kwargs_of(self, core: Core, stage: str) -> dict:
        from coscc.units import board as board_reader
        from coscc.runner.reply import RunError

        seen = self.seen

        class StandIn:
            bus = Bus()

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

        async def go():
            async for _ in core.steps.run_step(str(self.repo), self.unit, stage):
                pass

        before = len(self.seen)
        with (
            mock.patch.object(board_reader, "gate", open_gate),
            mock.patch("coscc.runner.steps.Runner", StandIn),
            mock.patch.object(core.steps, "worktree", tree),
            mock.patch.object(worktrees, "read_prepare", lambda *a: {"ok": True}),
        ):
            with self.assertRaises(Invalid) as refused:
                asyncio.run(go())
        # The step reached `Runner.run`, rather than being refused before it.
        self.assertEqual(len(self.seen), before + 1, str(refused.exception))
        return self.seen[-1]

    def test_impl_gets_the_files_of_its_plan_from_the_tree(self):
        core = self.core()
        self.plan(self.PLAN)
        kw = self.kwargs_of(core, "impl")
        self.assertEqual(
            kw["plan_map"], "- `pkg/m.py` — 5 lines\n  - 4 def top\n- `pkg/later.py` — new"
        )
        self.assertEqual((kw["plan_map_record"]["files"], kw["plan_map_record"]["new"]), (2, 1))
        self.assertNotIn("error", kw["plan_map_record"])

    def test_a_plan_without_the_section_is_an_empty_section_and_zero_bytes(self):
        core = self.core()
        self.plan("# Plan: x\nStatus: accepted.\n")
        kw = self.kwargs_of(core, "impl")
        self.assertEqual(kw["plan_map"], "")
        self.assertEqual(kw["plan_map_record"]["bytes"], 0)

    def test_no_other_stage_gets_a_key(self):
        core = self.core()
        self.plan(self.PLAN)
        for stage in ("plan", "review"):
            with self.subTest(stage=stage):
                kw = self.kwargs_of(core, stage)
                self.assertNotIn("plan_map", kw)
                self.assertNotIn("plan_map_record", kw)

    def test_a_plan_that_cannot_be_read_still_runs_the_step(self):
        core = self.core()
        self.plan(b"# Plan: x\nStatus: accepted.\n\n## Files that change\n\n- `pkg/m.py` \xff\n")
        kw = self.kwargs_of(core, "impl")
        self.assertEqual(kw["plan_map"], "")
        self.assertIn("UnicodeDecodeError", kw["plan_map_record"]["error"])


class AStepAnUpdatePausedIsLeftAsItWas(unittest.TestCase):
    """`drive` takes `Suspended` as the app going down. No session opens: the runner is a stand-in
    that raises it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "work" / "proj"
        (self.repo / ".git").mkdir(parents=True)
        config = Config(
            workspaces=(str(self.repo),),
            working_dir=str(self.root / "work"),
            data_dir=str(self.root / "data"),
        )
        self.core = Core(config, Sessions(config))
        self.unit = create_sync(self.core, str(self.repo), "a-problem", "words")["unit"]

    def test_a_suspended_drive_abandons_its_recorder_and_nudges_nothing(self):
        from coscc.agent.sessions import Suspended
        from coscc.runlog import events
        from coscc.units import board as board_reader

        core = self.core

        class StandIn:
            bus = Bus()

            def __init__(self, *a, **kw):
                pass

            async def run(self, **kw):
                yield ("chunk", "measuring")
                raise Suspended("paused for an update")

        async def open_gate(units_root, unit, stage, repo=None, **kw):
            return True, f"open: {stage} may proceed"

        async def tree(*a, **kw):
            return {"path": str(self.repo), "branch": "feat/a-problem", "base": None}

        async def nothing(*a, **kw):
            return {}

        abandoned: list[str] = []
        real_abandon = events.Recorder.abandon

        async def abandon(recorder):
            abandoned.append(recorder.run)
            await real_abandon(recorder)

        nudged: list[str] = []
        after: list[tuple] = []

        async def after_end(*a):
            after.append(a)

        async def go():
            with self.assertRaises(Invalid):
                async for _ in core.steps.run_step(str(self.repo), self.unit, "spike"):
                    pass
            others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            await asyncio.gather(*others, return_exceptions=True)

        with (
            mock.patch.object(board_reader, "gate", open_gate),
            mock.patch("coscc.runner.steps.Runner", StandIn),
            mock.patch.object(core.steps, "worktree", tree),
            mock.patch.object(worktrees, "read_prepare", lambda *a: {"ok": True}),
            mock.patch.object(core.steps, "after_end", after_end),
            mock.patch.object(core.autopilot, "nudge", nudged.append),
            mock.patch.object(events.Recorder, "abandon", abandon),
        ):
            asyncio.run(go())
        self.assertEqual(len(abandoned), 1)
        self.assertEqual((nudged, after), ([], []))
        # the paused step's attempt is left as it was, for the next start; nothing live
        # is kept for it.
        self.assertEqual(core.steps.tasks, {})
        self.assertEqual([r["unit"] for r in core.attempts.unfinished()], [self.unit])
        # The spike's directory stays for the step taken up again.
        self.assertTrue(units.spike_dir(str(self.repo), self.unit, core.config.data_dir).is_dir())


class AFeatureGuardRefusesAStepBeforeSpend(unittest.TestCase):
    """The guards of the features are asked once the gate is open, and a denial hands the unit
    back before anything starts."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "work" / "proj"
        self.repo.mkdir(parents=True)
        self.sessions = AStepTheGateClosesNeverStarts.NeverCalled()
        self.core = Core(
            Config(
                workspaces=(str(self.repo),),
                working_dir=str(self.root / "work"),
                data_dir=str(self.root / "data"),
            ),
            self.sessions,
        )
        self.made = create_sync(self.core, str(self.repo), "a-problem", "some words")
        (Path(self.made["path"]) / "intent.md").write_text(
            "# Intent: a problem\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8"
        )
        self.key = self.core.ws.key(str(self.repo))

    def guarded(self, check):
        from coscc.kernel import Guard, Hooks, Parts

        self.core.steps.hooks = Hooks(parts=(("f", Parts(guards=(Guard("g", check),))),))

    def run_stage(self, stage: str):
        async def go():
            return [
                i async for i in self.core.steps.run_step(str(self.repo), self.made["unit"], stage)
            ]

        return asyncio.run(go())

    def starts(self):
        return self.core.ws.journal().records(self.key, kind="start")

    def refused(self, stage: str = "spec"):
        from coscc.runner.queue import Refused

        with self.assertRaises(Refused) as caught:
            self.run_stage(stage)
        self.assertEqual(caught.exception.reasons, ("feature-refused",))
        self.assertFalse(self.core.holds.busy(self.key, self.made["unit"]))
        self.assertEqual(self.starts(), [])
        self.assertEqual(self.sessions.calls, 0)
        return str(caught.exception)

    def test_a_guard_that_denies_refuses_the_step_with_its_words(self):
        self.guarded(lambda facts: "not today")
        self.assertEqual(self.refused(), "g: not today")

    def test_a_guard_that_raises_refuses_the_step(self):
        def boom(facts):
            raise RuntimeError("nope")

        self.guarded(boom)
        with self.assertLogs("coscc.runner.steps", "ERROR"):
            self.assertEqual(self.refused(), "g: failed (RuntimeError)")

    def test_the_guard_is_told_the_run_it_would_refuse(self):
        seen = []
        self.guarded(lambda facts: seen.append(facts) or "no")
        self.refused()
        [facts] = seen
        self.assertEqual(
            (facts.unit, facts.stage, facts.resumed), (self.made["unit"], "spec", False)
        )

    def test_a_pr_step_is_refused_before_the_mechanical_path(self):
        from coscc.units import board as board_reader

        async def open_gate(*a, **kw):
            return board_reader.Gate(True, "open: pr may proceed", (), None)

        async def tree(*a, **kw):
            return str(self.repo), str(self.repo)

        async def none(*a, **kw):
            return None

        mechanical = mock.AsyncMock()
        self.guarded(lambda facts: "no")
        steps = self.core.steps
        with (
            mock.patch.object(steps, "_ask_gate", open_gate),
            mock.patch.object(steps, "_open_tree", tree),
            mock.patch.object(steps, "_tree_base", none),
            mock.patch.object(steps, "_run_mechanical", mechanical),
        ):
            self.refused("pr")
        mechanical.assert_not_called()

    def test_a_guard_that_abstains_changes_nothing(self):
        self.guarded(lambda facts: None)
        self.run_stage("spec")
        self.assertEqual(self.sessions.calls, 1)
        self.assertEqual(
            self.core.steps.feature_refusal(mock.Mock(stage="spec", workspace="w")), ""
        )


class APrOrShipEndsThroughTheMachine(unittest.TestCase):
    """`drive` ends a `pr` or a `ship` through the PR machine and leaves the unit's last word.
    No session opens: the runner and the machine are stand-ins throughout."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "work" / "proj"
        (self.repo / ".git").mkdir(parents=True)

    def core(self) -> Core:
        config = Config(
            workspaces=(str(self.repo),),
            working_dir=str(self.root / "work"),
            data_dir=str(self.root / "data"),
        )
        core = Core(config, Sessions(config))
        self.unit = create_sync(core, str(self.repo), "a-problem", "words")["unit"]
        return core

    def drive(self, core: Core, stage: str, outcome: str, rebased: dict | None = None) -> None:
        """One step whose runner ends `outcome`. What the stand-in machine's `ship` was handed
        is kept in `self.shipped_with`."""
        from coscc.units import board as board_reader

        class StandIn:
            bus = Bus()

            def __init__(self, *a, **kw):
                pass

            async def run(self, **kw):
                yield ("done", {"outcome": outcome})

        async def open_gate(units_root, unit, stage, repo=None, **kw):
            return board_reader.Gate(True, f"open: {stage} may proceed", (), rebased)

        self.shipped_with: list[dict | None] = []
        shipped_with = self.shipped_with

        async def tree(*a, **kw):
            return {"path": str(self.repo), "branch": "feat/a-problem", "base": None}

        async def nothing(*a, **kw):
            return {}

        from coscc.git import gitops
        from coscc.github import prmachine

        class Machine:
            """`pr` and `ship` end through the PR machine, not a runner."""

            history = core.ws.unit_meta().history

            async def open_pr(self, u, again=False):
                return prmachine.Outcome(
                    "opened" if outcome == "done" else "failed",
                    detail="" if outcome == "done" else "gh down",
                )

            async def ship(self, u, authority="person", rebased=None):
                shipped_with.append(rebased)
                return prmachine.Outcome(
                    "merged" if outcome == "done" else "failed",
                    detail="" if outcome == "done" else "gh down",
                )

        async def on_branch(*a, **kw):
            return "feat/a-problem"

        async def go():
            async for _ in core.steps.run_step(str(self.repo), self.unit, stage):
                pass
            # `drive` runs as its own task; wait for it, and anything it left, to end.
            others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            await asyncio.gather(*others, return_exceptions=True)

        with (
            mock.patch.object(core.integration, "pr_machine", Machine),
            mock.patch.object(units, "branch_name", lambda *a, **kw: "feat/a-problem"),
            mock.patch.object(gitops, "current_branch", on_branch),
            mock.patch.object(board_reader, "gate", open_gate),
            mock.patch("coscc.runner.steps.Runner", StandIn),
            mock.patch.object(core.steps, "worktree", tree),
            mock.patch.object(worktrees, "read_prepare", lambda *a: {"ok": True}),
            mock.patch.object(core.steps, "ingest", nothing),
            mock.patch.object(core.integration, "cleanup", nothing),
            mock.patch.object(core.steps, "after_end", nothing),
            mock.patch.object(core.steps, "sync_pr", nothing),
        ):
            asyncio.run(go())

    def test_the_gates_clean_rebase_reaches_the_machines_guard(self):
        core = self.core()
        rebased = {"reviewed": "a" * 40, "head": "b" * 40}
        self.drive(core, "ship", "done", rebased=rebased)
        self.assertEqual(self.shipped_with, [rebased])
        self.drive(self.core(), "ship", "done")
        self.assertEqual(self.shipped_with, [None])

    def test_every_pr_and_ship_leaves_the_record_the_autopilots_stop_reads(self):
        """There is no `end`, so this is the unit's last word."""
        for stage, outcome in (
            ("ship", "failed"),
            ("ship", "done"),
            ("pr", "failed"),
            ("pr", "done"),
        ):
            with self.subTest(stage=stage, outcome=outcome):
                core = self.core()
                # Each subtest makes a new unit over the same data root, so only its own rows count.
                before = len(core.ws.journal().records(kind=prmachine.RECORD_KIND))
                self.drive(core, stage, outcome)
                [rec] = core.ws.journal().records(kind=prmachine.RECORD_KIND)[before:]
                self.assertEqual(
                    (rec["unit"], rec["stage"], rec["outcome"], rec["started_by"]),
                    (self.unit, stage, outcome, "person"),
                )
                self.assertEqual(rec["detail"], "" if outcome == "done" else "gh down")
                self.assertFalse(rec["merge_refused"])
                self.assertEqual(core.ws.journal().records(kind="end"), [])

    def test_a_merge_github_refused_after_the_machine_requested_it_says_so(self):
        """The autopilot stops `f` on it, not `e`."""
        from coscc.github import prmachine

        core = self.core()
        with mock.patch.object(prmachine, "state", lambda *a: {"state": "merge-requested"}):
            self.drive(core, "ship", "failed")
        [rec] = core.ws.journal().records(kind=prmachine.RECORD_KIND)
        self.assertEqual(
            (rec["outcome"], rec["merge_refused"], rec["detail"]), ("failed", True, "gh down")
        )
        [ship] = core.ws.journal().records(kind="ship")
        self.assertEqual(ship["result"], "refused")


class _AReviewStep:
    """A `review` step run against stand-ins for the gate, `coscc.loop screens`, the capture and
    the runner; the board is read by the real the loop. `self.seen` holds the kwargs each
    `Runner.run` was handed."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "work" / "proj"
        (self.repo / ".git").mkdir(parents=True)
        config = Config(
            workspaces=(str(self.repo),),
            working_dir=str(self.root / "work"),
            data_dir=str(self.root / "data"),
        )
        self.core = Core(config, Sessions(config))
        made = create_sync(self.core, str(self.repo), "a-problem", "words")
        self.unit, self.dir = made["unit"], Path(made["path"])
        self.seen: list[dict] = []
        self.taken: list[list[str]] = []

    def screens_records(self) -> list[dict]:
        journal = self.core.ws.journal()
        return [
            r
            for r in journal.records(self.core.ws.key(str(self.repo)))
            if r.get("kind") == "screens"
        ]

    def step(self, answer: dict, result: dict | None):
        from coscc.units import board as board_reader
        from coscc.units import retake
        from coscc.runner.reply import RunError

        seen, taken = self.seen, self.taken

        class StandIn:
            bus = Bus()

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
            async for _ in self.core.steps.run_step(str(self.repo), self.unit, "review"):
                pass

        with (
            mock.patch.object(board_reader, "gate", open_gate),
            mock.patch.object(board_reader, "screens", asked),
            mock.patch.object(retake, "take", take),
            mock.patch("coscc.runner.steps.Runner", StandIn),
            mock.patch.object(self.core.steps, "worktree", tree),
        ):
            with self.assertRaises(Invalid) as refused:
                asyncio.run(go())
        return str(refused.exception)


class ReviewTakesTheScreenshotsAgainAfterARewrite(_AReviewStep, unittest.TestCase):
    """`coscc.loop screens` and the capture are stand-ins, and so is the runner: what is checked is
    whether `Runner.run` is reached, with which section, and what the run log holds."""

    OLD = {"head": "a" * 40, "dirty": False, "addresses": ["/board"], "hits": []}
    NEW = {"head": "b" * 40, "dirty": False, "addresses": ["/board"], "hits": []}

    def test_the_step_is_handed_a_snapshot_file_the_loop_decides_on(self):
        """A step that runs `coscc.loop gate` or `pr-text` itself needs `--state`."""
        import json
        from coscc.loop import run as loop

        self.step(
            {"retake": False, "why": "the manifest's head is still an ancestor of HEAD"}, None
        )
        path = Path(self.seen[0]["state_file"])
        snap = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn(f"{snap['workspace']}/{self.unit}", snap["units"])
        self.assertFalse(path.is_relative_to(self.dir.parent))
        done = loop.ask_sync(
            ["--root", str(self.dir.parent.parent), "gate", self.unit, "spec", "--state", str(path)]
        )
        self.assertIn(done.code, (0, 1), done.err)

    def test_no_retake_records_nothing_and_the_step_runs(self):
        self.step(
            {"retake": False, "why": "the manifest's head is still an ancestor of HEAD"}, None
        )
        self.assertEqual(len(self.seen), 1)
        self.assertEqual(self.seen[0]["screens_note"], "")
        self.assertEqual((self.taken, self.screens_records()), ([], []))

    def test_a_retake_taken_is_recorded_and_the_prompt_carries_it(self):
        result = {
            "code": 0,
            "seconds": 21.0,
            "tail": "",
            "head_before_run": "b" * 40,
            "manifest_after": self.NEW,
            "status_before": "",
            "status_after": "",
        }
        self.step({"retake": True, "manifest": self.OLD}, result)
        self.assertEqual(self.taken, [["/board"]])
        [rec] = self.screens_records()
        self.assertEqual(
            (
                rec["outcome"],
                rec["head_before"],
                rec["head_after"],
                rec["started_by"],
                rec["stage"],
            ),
            ("taken", "a" * 40, "b" * 40, "person", "review"),
        )
        self.assertEqual(len(self.seen), 1)
        self.assertIn("# The screenshots, taken again", self.seen[0]["screens_note"])
        self.assertIn("b" * 40, self.seen[0]["screens_note"])

    def test_a_retake_that_fails_refuses_review_before_the_session(self):
        from coscc.runner.steps import RETAKE_REFUSED

        files = {p.name: p.read_bytes() for p in self.dir.iterdir()}
        result = {
            "code": 2,
            "seconds": 0.3,
            "tail": "127.0.0.1:18783 is already in use",
            "head_before_run": "b" * 40,
            "manifest_after": self.OLD,
            "status_before": "",
            "status_after": "",
        }
        said = self.step({"retake": True, "manifest": self.OLD}, result)
        self.assertEqual(said, RETAKE_REFUSED)
        self.assertEqual(self.seen, [])
        [rec] = self.screens_records()
        self.assertEqual((rec["outcome"], rec["head_after"], rec["code"]), ("failed", "", 2))
        self.assertIn("exited 2", rec["detail"])
        self.assertIn("already in use", rec["detail"])
        # The unit is given back, and nothing was appended to it.
        self.assertEqual(self.core.attempts.unfinished(), [])
        self.assertEqual({p.name: p.read_bytes() for p in self.dir.iterdir()}, files)
        # One sentence, and no commit, path or log line in it (S1, S3).
        self.assertNotIn("/", said)
        self.assertNotRegex(said, r"[0-9a-f]{7,}")

    def test_a_board_that_cannot_answer_refuses_the_step(self):
        from coscc.units import board as board_reader

        async def unavailable(*a, **kw):
            raise board_reader.Unavailable("the loop could not start")

        with mock.patch.object(board_reader, "screens", unavailable):
            with self.assertRaises(Invalid):
                asyncio.run(
                    self.core.steps.retake_screens(
                        str(self.repo),
                        "k",
                        self.core.ws.journal(),
                        self.unit,
                        str(self.repo),
                        "person",
                    )
                )

    def _unrecorded(self, result: dict) -> str:
        from coscc.units import board as board_reader
        from coscc.units import retake
        from coscc.store.journal import Journal
        from coscc.store.db import Busy

        async def asked(*a, **kw):
            return {"retake": True, "manifest": self.OLD}

        async def take(tree, addresses, **kw):
            return result

        with (
            mock.patch.object(board_reader, "screens", asked),
            mock.patch.object(retake, "take", take),
            mock.patch.object(Journal, "append", side_effect=Busy("locked")),
        ):
            with self.assertRaises(Invalid) as refused:
                asyncio.run(
                    self.core.steps.retake_screens(
                        str(self.repo),
                        "k",
                        self.core.ws.journal(),
                        self.unit,
                        str(self.repo),
                        "person",
                    )
                )
        return str(refused.exception)

    def test_a_failed_retake_the_run_log_could_not_record_says_it_failed(self):
        # It must not say the screenshots were taken again.
        from coscc.runner.steps import RETAKE_REFUSED

        said = self._unrecorded(
            {
                "code": 2,
                "seconds": 0.3,
                "tail": "",
                "head_before_run": "b" * 40,
                "manifest_after": self.OLD,
                "status_before": "",
                "status_after": "",
            }
        )
        self.assertEqual(said, RETAKE_REFUSED)

    def test_a_taken_retake_the_run_log_could_not_record_says_so(self):
        said = self._unrecorded(
            {
                "code": 0,
                "seconds": 21.0,
                "tail": "",
                "head_before_run": "b" * 40,
                "manifest_after": self.NEW,
                "status_before": "",
                "status_after": "",
            }
        )
        self.assertIn("taken again, but the run log could not record it", said)

    def test_a_pending_update_waits_for_a_retake(self):
        # Listed as a job while it runs, never cut, gone once it ends.
        from coscc.units import board as board_reader
        from coscc.units import retake

        during: list[list[dict]] = []

        async def asked(*a, **kw):
            return {"retake": True, "manifest": self.OLD}

        async def take(tree, addresses, **kw):
            during.append(self.core.resume.update_waited())
            return {
                "code": 0,
                "seconds": 0.1,
                "tail": "",
                "head_before_run": "b" * 40,
                "manifest_after": self.NEW,
                "status_before": "",
                "status_after": "",
            }

        with (
            mock.patch.object(board_reader, "screens", asked),
            mock.patch.object(retake, "take", take),
            mock.patch.object(self.core.updater, "job_ended") as ended,
        ):
            asyncio.run(
                self.core.steps.retake_screens(
                    str(self.repo),
                    "k",
                    self.core.ws.journal(),
                    self.unit,
                    str(self.repo),
                    "person",
                )
            )
        [[job]] = during
        self.assertEqual(
            (job["kind"], job["unit"], job["stage"]), ("integration", self.unit, "screens")
        )
        self.assertEqual(self.core.resume.update_waited(), [])
        ended.assert_called_once()

    def test_no_retake_begins_while_an_update_is_applied(self):
        from coscc.units import board as board_reader
        from coscc.units import retake

        taken: list[str] = []

        async def asked(*a, **kw):
            return {"retake": True, "manifest": self.OLD}

        async def take(tree, addresses, **kw):
            taken.append(tree)
            return {}

        # Nor while one waits, so the wait cannot grow.
        for window, state in ((True, "idle"), (False, "pending")):
            self.core.updater.window, self.core.updater.state = window, state
            with (
                mock.patch.object(board_reader, "screens", asked),
                mock.patch.object(retake, "take", take),
            ):
                with self.assertRaises(Invalid):
                    asyncio.run(
                        self.core.steps.retake_screens(
                            str(self.repo),
                            "k",
                            self.core.ws.journal(),
                            self.unit,
                            str(self.repo),
                            "person",
                        )
                    )
        self.assertEqual((taken, self.core.resume.update_waited()), ([], []))

    def test_two_retakes_never_run_at_once(self):
        from coscc.units import board as board_reader
        from coscc.units import retake

        # Each take ran holding the one app-wide lock.
        held: list[bool] = []

        async def asked(*a, **kw):
            return {"retake": True, "manifest": self.OLD}

        async def take(tree, addresses, **kw):
            held.append(self.core.steps._screens_lock.locked())
            return {
                "code": 0,
                "seconds": 0.05,
                "tail": "",
                "head_before_run": "b" * 40,
                "manifest_after": self.NEW,
                "status_before": "",
                "status_after": "",
            }

        async def go():
            journal = self.core.ws.journal()
            await asyncio.gather(
                *(
                    self.core.steps.retake_screens(
                        str(self.repo), "k", journal, u, str(self.repo), "person"
                    )
                    for u in ("0001_a", "0002_b", "0003_c")
                )
            )

        with (
            mock.patch.object(board_reader, "screens", asked),
            mock.patch.object(retake, "take", take),
        ):
            asyncio.run(go())
        self.assertEqual(held, [True, True, True])


class AReviewAfterAnUnfinishedRoundIsToldWhy(_AReviewStep, unittest.TestCase):
    """Which round the loop read as unfinished reaches `Runner.run` as it was read; `service_steps`
    compares no ids itself."""

    NO_RETAKE = {"retake": False, "why": "the manifest's head is still an ancestor of HEAD"}
    ROUND1 = (
        "## Round 1\n\nReviewed: abcdef1. Verdict: changes-requested.\n\n"
        "### Findings\n\n- F1 [open] a.py:1 — high — x\n- F2 [open] b.py:2 — high — y\n"
    )

    def review(self, round2_findings: str) -> None:
        (self.dir / "review.md").write_text(
            "# Review: x\nStatus: changes-requested.\n\n"
            + self.ROUND1
            + "\n## Round 2\n\nReviewed: abcdef2. Verdict: changes-requested.\n\n"
            f"### Findings\n\n{round2_findings}",
            encoding="utf-8",
        )

    def test_review_step_is_handed_the_unfinished_round(self):
        self.review("- F1 [open] a.py:1 — high — x\n")
        self.step(self.NO_RETAKE, None)
        [kw] = self.seen
        self.assertEqual(kw["unfinished_round"], {"n": 2, "dropped": ["F2"]})
        # A round that carries every id forward hands over nothing.
        self.seen.clear()
        self.review("- F1 [open] a.py:1 — high — x\n- F2 [open] b.py:2 — high — y\n")
        self.step(self.NO_RETAKE, None)
        [kw] = self.seen
        self.assertNotIn("unfinished_round", kw)
