"""Tests for `StepsMixin` in `coscc/service_steps.py`, split from `coscc/service_test.py` (`0095`).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc import fetches, units, worktrees
from coscc.config import Config
from coscc.service_common import Invalid, describe_base, step_cwd
from coscc.service import Service
from coscc.sessions import Sessions
from coscc.service_test import create_sync


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
                mock.patch("coscc.service_steps.Runner", StandIn), \
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


class RunStepHandsOnThePlanMap(RunStepHandsOnTheKnowledgeStore):
    """`0096` plan step 4. `run_step` hands an `impl` step the files its plan names as they
    stand in the step's tree, and every other stage no key. The same stand-ins as the
    knowledge store's; the tests of that class run here too."""

    PLAN = "# Plan: x\nStatus: accepted.\n\n## Files that change\n\n- `pkg/m.py`.\n- `pkg/later.py`.\n"

    def plan(self, text: str | bytes) -> None:
        path = units.unit_dir(str(self.repo), self.unit, str(self.data)) / "plan.md"
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")

    def setUp(self):
        super().setUp()
        (self.repo / "pkg").mkdir()
        (self.repo / "pkg" / "m.py").write_text("import os\n\n\ndef top():\n    pass\n", encoding="utf-8")

    def test_impl_gets_the_files_of_its_plan_from_the_tree(self):
        service = self.service(False)
        self.plan(self.PLAN)
        kw = self.kwargs_of(service, "impl")
        self.assertEqual(kw["plan_map"], "- `pkg/m.py` — 5 lines\n  - 4 def top\n- `pkg/later.py` — new")
        self.assertEqual((kw["plan_map_record"]["files"], kw["plan_map_record"]["new"]), (2, 1))
        self.assertNotIn("error", kw["plan_map_record"])

    def test_a_plan_without_the_section_is_an_empty_section_and_zero_bytes(self):
        service = self.service(False)
        self.plan("# Plan: x\nStatus: accepted.\n")
        kw = self.kwargs_of(service, "impl")
        self.assertEqual(kw["plan_map"], "")
        self.assertEqual(kw["plan_map_record"]["bytes"], 0)

    def test_no_other_stage_gets_a_key(self):
        service = self.service(False)
        self.plan(self.PLAN)
        for stage in ("plan", "pr", "review", "ship"):
            with self.subTest(stage=stage):
                kw = self.kwargs_of(service, stage)
                self.assertNotIn("plan_map", kw)
                self.assertNotIn("plan_map_record", kw)

    def test_a_plan_that_cannot_be_read_still_runs_the_step(self):
        service = self.service(False)
        self.plan(b"# Plan: x\nStatus: accepted.\n\n## Files that change\n\n- `pkg/m.py` \xff\n")
        kw = self.kwargs_of(service, "impl")
        self.assertEqual(kw["plan_map"], "")
        self.assertIn("UnicodeDecodeError", kw["plan_map_record"]["error"])


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
                mock.patch("coscc.service_steps.Runner", StandIn), \
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
