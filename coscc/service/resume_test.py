"""Tests for `coscc/service/resume.py`: taking up at start-up what an update paused.

`transcript.projects_root` is a temporary directory throughout, so nothing here reads or
writes `~/.claude`. No session opens: an owner is a stand-in, or `Sessions.stream` is."""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.agent import transcript
from coscc.agent.sessions import Refused, Sessions, Suspended
from coscc.config import Config
from coscc.service import Service
from coscc.service import resume as resume_mod
from coscc.service.service_test import create_sync
from coscc.agent.submit_test import submits as _submits

SID = "5f1c2d3e-0000-4000-8000-000000000001"
DROPPED = [{"name": "Bash", "input": "sleep 60"}]


def _line(**entry) -> str:
    return json.dumps(entry) + "\n"


def _transcript() -> str:
    """One Bash call answered, then one cut: the safe point is `u2`."""
    return (
        _line(type="user", uuid="u1", message={"role": "user", "content": "do it"})
        + _line(
            type="assistant",
            uuid="a1",
            message={
                "id": "m1",
                "content": [
                    {"type": "text", "text": "Running."},
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Bash",
                        "input": {"command": "echo one"},
                    },
                ],
            },
        )
        + _line(
            type="user",
            uuid="u2",
            message={"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "one"}]},
        )
        + _line(
            type="assistant",
            uuid="a2",
            message={
                "id": "m2",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t2",
                        "name": "Bash",
                        "input": {"command": "sleep 60"},
                    }
                ],
            },
        )
    )


class _Base(unittest.TestCase):
    STAGE = {
        "step": "plan",
        "opening": "plan",
        "closing": "review",
        "integrate": "integrate",
        "estimate": "estimate",
        "precedent": "precedent",
        "chat": "",
    }

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        repo = self.root / "work" / "proj"
        repo.mkdir(parents=True)
        self.cwd = str(repo)
        self.projects = self.root / "projects"
        patcher = mock.patch.object(transcript, "projects_root", lambda: self.projects)
        patcher.start()
        self.addCleanup(patcher.stop)
        config = Config(
            workspaces=(self.cwd,),
            working_dir=str(self.root / "work"),
            data_dir=str(self.root / "data"),
        )
        self.service = Service(config, Sessions(config))
        self.journal = self.service._journal()
        self.key = self.service._journal_key(self.cwd)
        self.unit = create_sync(self.service, self.cwd, "a-problem", "words")["unit"]
        self.tree = self.root / "tree"
        self.tree.mkdir()

    def paused(
        self,
        kind: str = "step",
        cwd: Path | None = None,
        write: bool = True,
        api_calls: int | None = None,
        **extra,
    ) -> dict:
        """One `suspend` row, as `suspend_sessions` writes it, and its transcript unless not. A
        chat turn's has used none of its one turn unless the test says so."""
        if api_calls is None:
            api_calls = 0 if kind == "chat" else 2
        cwd = str(cwd or self.tree)
        if write:
            path = transcript.path_for(cwd, SID)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_transcript(), encoding="utf-8")
        unit = "" if kind in ("estimate", "chat") else self.unit
        owner = {
            "kind": kind,
            "workspace": self.key,
            "workspace_dir": self.cwd,
            "unit": unit,
            "stage": self.STAGE[kind],
            "start_at": "t0",
            "artifact": "plan.md",
            "head": "",
        }
        return self.journal.suspended(
            self.key,
            unit,
            owner["stage"],
            by="an",
            owner=owner,
            cwd=cwd,
            session_id=SID,
            model="m",
            start_at="t0",
            boundary=4,
            safe_uuid="u2",
            dropped=DROPPED,
            api_calls=api_calls,
            spent_usd=0.3,
            **extra,
        )

    def up(self) -> list[dict]:
        async def go():
            said = await self.service.resume_after_update()
            for _ in range(20):
                await asyncio.sleep(0)
            return said

        return asyncio.run(go())

    def taken(self) -> list[dict]:
        """`resume_step` replaced: what it was handed, and nothing run."""
        calls: list[dict] = []
        patcher = mock.patch.object(self.service, "resume_step", calls.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def ends(self) -> list[dict]:
        return self.journal.records(self.key, kind="end")


class WhatASessionIsTold(unittest.TestCase):
    def test_the_resume_message_names_the_dropped_commands_and_says_the_user_did_not_refuse_them(
        self,
    ):
        said = resume_mod.resume_message(
            [
                {"name": "Bash", "input": "npm test"},
                {"name": "Edit", "input": '{"file_path": "a.py"}'},
            ]
        )
        self.assertIn("- Bash: npm test", said)
        self.assertIn('- Edit: {"file_path": "a.py"}', said)
        self.assertIn("The user did not refuse them", said)
        self.assertIn("restarted to install an update", said)
        self.assertIn("Check the state of the worktree first", said)

    def test_a_session_with_nothing_dropped_is_told_only_to_continue(self):
        for dropped in (None, []):
            said = resume_mod.resume_message(dropped)
            self.assertIn("Carry on with the work you were doing", said)
            self.assertNotIn("refuse", said)
            self.assertNotIn("worktree", said)


class TakingUpAfterAnUpdate(_Base):
    def test_each_kind_goes_to_its_owner(self):
        got: dict[str, list] = {k: [] for k in resume_mod.KINDS}
        steps_seen = self.taken()

        async def integration(record):
            got["integrate"].append(record)

        async def estimates(cwd, resume=None):
            got["estimate"].append(resume)
            return
            yield

        async def chat(cwd, record):
            got["chat"].append(record)

        with (
            mock.patch.object(self.service, "resume_integration", lambda r: integration(r)),
            mock.patch.object(self.service, "propose_estimates", estimates),
            mock.patch.object(self.service, "_resume_chat", chat),
        ):
            for kind in resume_mod.KINDS:
                self.paused(kind)
            said = self.up()
        self.assertEqual(sorted(s["kind"] for s in said), sorted(resume_mod.KINDS))
        self.assertEqual({s["result"] for s in said}, {"resumed"})
        self.assertEqual([r["owner"]["kind"] for r in steps_seen], list(resume_mod.STEP_KINDS))
        for kind in ("integrate", "estimate", "chat"):
            [record] = got[kind]
            self.assertEqual(record["owner"]["kind"], kind)
        record = steps_seen[0]
        self.assertEqual(record["pieces"], ["Running.", ""])
        self.assertIn("- Bash: sleep 60", record["message"])
        self.assertEqual(self.ends(), [])

    def test_each_suspended_session_is_resumed_exactly_once_across_two_starts(self):
        steps_seen = self.taken()
        self.paused()
        self.up()
        self.up()
        self.assertEqual(len(steps_seen), 1)
        [row] = self.journal.records(self.key, kind="resume")
        self.assertEqual((row["by"], row["result"]), ("app", "resumed"))
        self.assertEqual(self.journal.unresumed(), [])

    def test_a_resumed_row_records_snapshot(self):
        self.taken()
        self.paused()
        self.up()
        [row] = self.journal.records(self.key, kind="resume")
        self.assertEqual((row["result"], row["system_prompt"]), ("resumed", "snapshot"))

    def assert_failed(self, why: str) -> None:
        [end] = self.ends()
        self.assertEqual((end["unit"], end["stage"], end["outcome"]), (self.unit, "plan", "failed"))
        self.assertIn("not resumed after an update", end["detail"])
        self.assertIn(why, end["detail"])
        [row] = self.journal.records(self.key, kind="resume")
        self.assertEqual(row["result"], "failed")

    def test_a_missing_transcript_ends_failed_and_waits_for_rerun(self):
        steps_seen = self.taken()
        self.paused(write=False)
        self.up()
        self.assertEqual(steps_seen, [])
        self.assert_failed("transcript")

    def test_a_transcript_only_in_another_project_is_not_resumed(self):
        # The row's own directory decides.
        steps_seen = self.taken()
        other = transcript.path_for(str(self.root / "other_place.x"), SID)
        other.parent.mkdir(parents=True)
        other.write_text(_transcript(), encoding="utf-8")
        self.paused(write=False)
        self.up()
        self.assertEqual(steps_seen, [])
        self.assert_failed("transcript")
        self.assertEqual(other.read_text(encoding="utf-8"), _transcript())

    def test_a_gone_cwd_ends_failed(self):
        steps_seen = self.taken()
        self.paused(cwd=self.root / "gone", write=False)
        self.up()
        self.assertEqual(steps_seen, [])
        self.assert_failed("is gone")

    def test_an_unresumable_row_ends_failed(self):
        steps_seen = self.taken()
        self.paused(unresumable="no session id yet")
        self.up()
        self.assertEqual(steps_seen, [])
        self.assert_failed("no session id yet")

    def test_a_unit_that_moved_on_after_the_pause_is_not_taken_up(self):
        # A rerun while the row waited for a start. Taking the old session up would write over newer
        # work.
        steps_seen = self.taken()
        self.paused()
        self.journal.append(
            {"kind": "start", "workspace": self.key, "unit": self.unit, "stage": "plan"}
        )
        self.up()
        self.assertEqual(steps_seen, [])
        self.assert_failed("moved on after the update paused it")

    def test_a_unit_already_held_writes_a_failed_resume_row(self):
        # The `resume` row says what happened, not what was about to.
        steps_seen = self.taken()
        self.paused()
        self.service._take(self.key, self.unit, "integrate")
        [said] = self.up()
        self.assertEqual(steps_seen, [])
        self.assertEqual(said["result"], "failed")
        [row] = self.journal.records(self.key, kind="resume")
        self.assertEqual(row["result"], "failed")
        self.assertEqual(row["detail"], said["detail"])
        self.assertTrue(row["detail"])
        [end] = self.ends()
        self.assertEqual(end["outcome"], "failed")

    def test_what_an_estimate_or_chat_refuses_is_asked_before_the_resume_row(self):
        # None of the owners is reached, and the `resume` row says `failed` with the owner's own reason.
        reached: list[str] = []

        async def estimates(cwd, resume=None):
            reached.append("estimate")
            return
            yield

        async def chat(cwd, record):
            reached.append("chat")

        cases = {
            "the workspace was taken off the list": lambda: mock.patch.object(
                self.service, "_is_member", lambda cwd: False
            ),
            "an update is being applied": lambda: mock.patch.object(
                self.service.updater, "window", True
            ),
        }
        with (
            mock.patch.object(self.service, "propose_estimates", estimates),
            mock.patch.object(self.service, "_resume_chat", chat),
        ):
            for why, refusal in cases.items():
                with self.subTest(why), refusal():
                    for kind in ("estimate", "chat"):
                        self.paused(kind)
                    said = self.up()
                    self.assertEqual(
                        [(s["kind"], s["result"]) for s in said],
                        [("estimate", "failed"), ("chat", "failed")],
                    )
            self.assertEqual(reached, [])
            rows = self.journal.records(self.key, kind="resume")
            self.assertEqual({r["result"] for r in rows}, {"failed"})
            self.assertTrue(all(r["detail"] for r in rows))
            # An estimate had a `start`; each ends `failed`. A chat turn had none.
            self.assertEqual(sorted(e["stage"] for e in self.ends()), ["estimate", "estimate"])

    def test_a_session_of_a_kind_no_owner_takes_up_ends_failed(self):
        self.paused("precedent")
        [said] = self.up()
        self.assertEqual((said["kind"], said["result"]), ("precedent", "failed"))
        self.assertIn("no owner takes up", said["detail"])
        [end] = self.ends()
        self.assertEqual((end["stage"], end["outcome"]), ("precedent", "failed"))

    def test_taking_up_again_lets_sessions_open_once_more(self):
        # `suspend_all` closed `Sessions` to new streams; after a failed hand-off this same process
        # takes its rows up, and must open them again.
        self.taken()
        self.service.sessions.paused = True
        self.paused()
        self.up()
        self.assertFalse(self.service.sessions.paused)

    def test_a_row_failing_in_the_same_pass_is_not_the_unit_moving_on(self):
        steps_seen = self.taken()
        got: list[dict] = []

        async def integration(record):
            got.append(record)

        self.paused(cwd=self.root / "gone", write=False)
        self.paused("integrate")
        with mock.patch.object(self.service, "resume_integration", lambda r: integration(r)):
            said = self.up()
        self.assertEqual(
            [(s["kind"], s["result"]) for s in said], [("step", "failed"), ("integrate", "resumed")]
        )
        self.assertEqual((steps_seen, len(got)), ([], 1))

    def test_the_chat_ceiling_is_what_sessions_gives_a_turn_that_names_none(self):
        import inspect

        from coscc.service.sessions import CHAT_TURNS

        self.assertEqual(
            CHAT_TURNS, inspect.signature(Sessions.stream).parameters["max_turns"].default
        )
        self.assertIsNone(inspect.signature(Sessions.stream).parameters["max_budget_usd"].default)

    def test_a_chat_turn_goes_on_with_what_is_left_of_its_ceiling(self):
        streamed: list[dict] = []

        async def stream(cwd, text, session_id=None, **kw):
            streamed.append({"session_id": session_id, **kw})
            await _submits(kw)
            yield ("done", {"session_id": SID})

        self.service.sessions.stream = stream  # type: ignore[method-assign]
        self.paused("chat", api_calls=0)
        [said] = self.up()
        self.assertEqual(said["result"], "resumed")
        [call] = streamed
        self.assertEqual((call["session_id"], call["resume_at"], call["max_turns"]), (SID, "u2", 1))

    def test_a_chat_turn_with_its_one_turn_used_is_not_taken_up(self):
        streamed: list[dict] = []

        async def stream(cwd, text, session_id=None, **kw):
            streamed.append(kw)
            await _submits(kw)
            yield ("done", {"session_id": SID})

        self.service.sessions.stream = stream  # type: ignore[method-assign]
        self.paused("chat", api_calls=1)
        [said] = self.up()
        self.assertEqual(streamed, [])
        self.assertEqual(said["result"], "failed")
        self.assertIn("error_max_turns", said["detail"])
        [row] = self.journal.records(self.key, kind="resume")
        self.assertEqual(row["result"], "failed")
        self.assertEqual(self.ends(), [])

    def test_a_refused_resume_ends_failed_without_a_new_session(self):
        # The CLI refusing the id, or `Sessions` finding another in `init`, is the end.
        from coscc.runner import Runner

        class Refuses:
            calls: list[dict] = []

            async def stream(self, cwd, text, session_id=None, **kw):
                self.calls.append({"session_id": session_id, **kw})
                raise Refused(f"session {session_id}: init named another id")
                yield

        record = {**self.paused(), "pieces": [], "message": "MSG"}
        sessions = Refuses()

        async def go():
            async for _ in Runner(sessions, self.journal).run(
                workspace=self.cwd,
                directory=self.service._unit_dir(self.cwd, self.unit),
                journal_key=self.key,
                unit=self.unit,
                stage="plan",
                artifact="plan.md",
                stages=[],
                mode="manual",
                cwd=str(self.tree),
                resume=record,
            ):
                pass

        asyncio.run(go())
        self.assertEqual([c["session_id"] for c in sessions.calls], [SID])
        [end] = self.ends()
        self.assertEqual(end["outcome"], "failed")
        self.assertIn("init named another id", end["detail"])

    def test_suspended_units_are_claimed_before_the_autopilot_resumes(self):
        gate = asyncio.Event()
        held: list[list[str]] = []

        class Waits:
            def __init__(self, *a, **kw):
                pass

            async def run(self, **kw):
                await gate.wait()
                raise Suspended("paused again")
                yield

        def autopilot_resume():
            held.append([r.unit for r in self.service.steps.all()])
            gate.set()
            return []

        self.paused()
        with (
            mock.patch("coscc.runner.Runner", Waits),
            mock.patch.object(self.service, "autopilot_resume", autopilot_resume),
        ):
            self.up()
        self.assertEqual(held, [[self.unit]])
        self.assertEqual(self.service.steps.all(), [])

    def test_resume_runs_no_git_command_on_the_worktree(self):
        # Nothing reads or cleans the worktree before the session goes on.
        ran: list[tuple] = []
        real_exec, real_run = asyncio.create_subprocess_exec, subprocess.run

        async def exec_(*args, cwd=None, **kw):
            ran.append((args, str(cwd or "")))
            return await real_exec(*args, cwd=cwd, **kw)

        def run(args, *a, cwd=None, **kw):
            ran.append(
                (tuple(args) if isinstance(args, (list, tuple)) else (args,), str(cwd or ""))
            )
            return real_run(args, *a, cwd=cwd, **kw)

        streamed: list[dict] = []

        async def stream(cwd, text, session_id=None, **kw):
            streamed.append({"cwd": cwd, "text": text, "session_id": session_id, **kw})
            raise Suspended("paused again")
            yield

        self.paused()
        self.service.sessions.stream = stream  # type: ignore[method-assign]
        with (
            mock.patch.object(asyncio, "create_subprocess_exec", exec_),
            mock.patch.object(subprocess, "run", run),
        ):
            self.up()
        [call] = streamed
        self.assertEqual(
            (call["cwd"], call["session_id"], call["resume_at"]), (str(self.tree), SID, "u2")
        )
        on_tree = [a for a, where in ran if "git" in str(a[0]) and where.startswith(str(self.tree))]
        self.assertEqual(on_tree, [])


class APausedOwnerEndsNothing(_Base):
    """Gebo and an estimate re-raise `Suspended` before any branch that writes their `end`."""

    ASKED = "# Spec: a\nIntent: intent.md. Author: t. Status: draft.\n\n## Open questions\n\n1. Nhánh mới?\n"

    def test_a_suspended_gebo_or_estimate_writes_no_end(self):
        async def stream(cwd, text, session_id=None, **kw):
            yield ("chunk", "working")
            raise Suspended("paused for an update")

        self.service.sessions.stream = stream  # type: ignore[method-assign]
        unit_dir = self.service._unit_dir(self.cwd, self.unit)
        (unit_dir / "intent.md").write_text(
            "# Intent: x\nAuthor: t. Type: feat. Status: accepted.\n\n## Problem\n\np\n",
            encoding="utf-8",
        )
        (unit_dir / "spec.md").write_text(self.ASKED, encoding="utf-8")
        row = {**self.paused("integrate"), "message": "MSG"}

        async def gebo():
            async for _ in self.service._integrate_gebo(
                self.cwd,
                self.key,
                self.unit,
                None,
                None,
                None,
                None,
                7,
                self.tree,
                "feat/a-problem",
                "a" * 40,
                "b" * 40,
                self.journal,
                self.journal.append,
                {"started_by": "person"},
                resume=row,
            ):
                pass

        async def estimate():
            async for _ in self.service.propose_estimates(self.cwd):
                pass

        for name, run in (("integrate", gebo), ("estimate", estimate)):
            with self.subTest(owner=name):
                with self.assertRaises(Suspended):
                    asyncio.run(run())
                self.assertEqual([e for e in self.ends() if e.get("stage") == name], [])
                self.assertEqual(self.service._active, {})


if __name__ == "__main__":
    unittest.main()
