"""Tests for the thing that runs a step and writes what comes back.

Two properties carry the weight here.

Nothing here creates a session. What the guards do before a process is spawned is exactly what is
worth testing cheaply. The prompt's own tests are in `tests/runner/test_prompt.py`, and how a step
ends is in `tests/runner/test_step_ending.py`."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.agent import modeltrial, policy
from coscc.kernel import Facts, Hooks, Parts, Tool
from coscc.store.journal import Journal
from coscc.agent.policy import decide, grant_for
from coscc.runner.prompt import compose_prompt
from coscc.runner.reply import RunError
from coscc.runner.step import Runner
from coscc.runner.prompt import answers_section
from coscc.runner.prompt import build_prompt
from coscc.runner.prompt import skill_for
from tests.units.test_submit import a_head, submits as _submits

STAGES = ["idea", "intent", "spec", "spike", "plan", "impl", "pr", "review", "ship"]
SESSION_STAGES = [s for s in STAGES if s not in ("pr", "ship")]
UNIT = "0009_a-test-unit"


def make_unit(root: Path, **files: str) -> Path:
    d = root / ".cos" / UNIT
    d.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (d / name.replace("_", ".")).write_text(body, encoding="utf-8")
    return d


class ProseStagesCarryNothingThatWrites(unittest.TestCase):
    """The rule narrowed on 2026-09-23 and this class records both halves.

    It used to read: a prose stage carries nothing at all. `plan` broke that, on purpose
    -- `write-plan/SKILL.md` requires it to open every file it names, and an empty grant
    made that impossible, so every plan the board produced named paths it had never seen.

    What has not moved: the app writes a prose stage's artifact from the reply, so no
    prose stage may write or run anything, in any mode.
    """

    def test_no_prose_stage_can_write_or_run(self):
        """Was `..._in_either_mode`. The grant no longer depends on the mode, so there is one
        grant per stage to check."""
        for stage in policy.PROSE_STAGES:
            grant = policy.grant_for(stage)
            self.assertEqual(grant.commands, (), f"{stage} carries commands")
            self.assertEqual(policy.beyond_reading(grant), (), f"{stage} carries more than reading")

    def test_spec_plan_and_review_read_and_idea_and_intent_do_not(self):
        # All three only read, in any mode.
        readers = ("spec", "plan", "review")
        for reader in readers:
            self.assertEqual(policy.grant_for(reader).tools, policy.READ_TOOLS)
        for stage in policy.PROSE_STAGES:
            if stage not in readers:
                self.assertEqual(policy.grant_for(stage).tools, (), f"{stage} carries tools")

    def test_the_guard_lets_the_plan_stage_through_with_its_read_tools(self):
        """The half a grant-table test cannot cover.

        `plan` holding `Read` is only useful if the runner's own guard agrees, and that
        guard is the reason it could not hold anything for so long. This drives a real
        run and asserts it reaches the session rather than being refused on the way.
        """

        class Replies:
            def __init__(self):
                self.granted = None

            async def stream(self, cwd, text, session_id=None, max_turns=1, tools=None, **kw):
                self.granted = tuple(tools or ())
                yield ("chunk", "# Plan: x\nStatus: accepted.\n")
                await _submits(kw)
                yield ("done", {"session_id": "s-plan", "cost": {}})

        sessions = Replies()
        with tempfile.TemporaryDirectory() as d:
            make_unit(Path(d), intent_md="Status: accepted.\nI", spec_md="Status: accepted.\nS")
            r = Runner(sessions=sessions, journal=None)

            async def go():
                out = []
                async for ev in r.run(
                    workspace=d,
                    directory=Path(d) / ".cos" / UNIT,
                    journal_key=d,
                    unit=UNIT,
                    stage="plan",
                    artifact="plan.md",
                    stages=STAGES,
                    mode="autonomous",
                ):
                    out.append(ev)
                return out

            _, final = asyncio.run(go())[-1]

        self.assertEqual(final["outcome"], "done", final)
        self.assertEqual(sessions.granted, policy.READ_TOOLS)

    def test_the_guard_lets_the_spec_stage_through_with_its_read_tools(self):
        """A real `spec` run reaches the session holding `READ_TOOLS`, in `manual`."""

        class Replies:
            def __init__(self):
                self.granted = None

            async def stream(self, cwd, text, session_id=None, max_turns=1, tools=None, **kw):
                self.granted = tuple(tools or ())
                yield ("chunk", "# Spec: x\nStatus: accepted.\n")
                await _submits(kw)
                yield ("done", {"session_id": "s-spec", "cost": {}})

        sessions = Replies()
        with tempfile.TemporaryDirectory() as d:
            make_unit(Path(d), intent_md="Status: accepted.\nI")
            r = Runner(sessions=sessions, journal=None)

            async def go():
                out = []
                async for ev in r.run(
                    workspace=d,
                    directory=Path(d) / ".cos" / UNIT,
                    journal_key=d,
                    unit=UNIT,
                    stage="spec",
                    artifact="spec.md",
                    stages=STAGES,
                    mode="manual",
                ):
                    out.append(ev)
                return out

            _, final = asyncio.run(go())[-1]

        self.assertEqual(final["outcome"], "done", final)
        self.assertEqual(sessions.granted, policy.READ_TOOLS)

    def test_the_app_still_writes_the_plan_artifact(self):
        """The reason `plan` gets no write tools. If the session wrote `plan.md` itself,
        an unaccepted plan could author the thing that authorizes it."""
        self.assertTrue(policy.grant_for("plan").app_writes_artifact)

    def test_an_unknown_stage_is_locked_rather_than_open(self):
        grant = policy.grant_for("a-stage-invented-tomorrow")
        self.assertFalse(grant.opens_anything)
        self.assertEqual(grant.max_turns, 1)
        self.assertEqual(grant.max_budget_usd, 0.0)

    def test_a_prose_stage_that_somehow_gained_tools_refuses_to_run(self):
        """Belt and braces against a future edit to the grant table.

        A prose stage with tools would stop being covered without anything
        failing, so the runner checks rather than trusting the table it just read.
        """
        original = dict(policy.GRANTS)
        policy.GRANTS["spec"] = policy.Grant(tools=("Write",))
        try:
            with tempfile.TemporaryDirectory() as d:
                make_unit(Path(d), intent_md="Status: accepted.\nI")
                r = Runner(sessions=None, journal=None)

                async def go():
                    async for _ in r.run(
                        workspace=d,
                        directory=Path(d) / ".cos" / UNIT,
                        journal_key=d,
                        unit=UNIT,
                        stage="spec",
                        artifact="spec.md",
                        stages=STAGES,
                        mode="autonomous",
                    ):
                        pass

                with self.assertRaises(RunError) as caught:
                    asyncio.run(go())
                # The refusal names what it refused, which the older message did not:
                # "must not carry tools" said a prose stage may hold none, and since
                # 2026-09-23 `plan` holds three.
                self.assertIn("must not carry Write", str(caught.exception))
        finally:
            policy.GRANTS.clear()
            policy.GRANTS.update(original)


class AStepRecordsTheCommitItRanOn(unittest.TestCase):
    """The outcome checks a spec's citations at the commit the stage read."""

    class Replies:
        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            yield ("chunk", "# Spec: x\nStatus: accepted.\n")
            await _submits(kw)
            yield ("done", {"session_id": "s-spec", "cost": {}})

    def _start_record(self, d: str, **extra) -> dict:
        make_unit(Path(d), intent_md="Status: accepted.\nI")
        journal = Journal(d, d)
        r = Runner(sessions=self.Replies(), journal=journal)

        async def go():
            async for _ in r.run(
                workspace=d,
                directory=Path(d) / ".cos" / UNIT,
                journal_key=d,
                unit=UNIT,
                stage="spec",
                artifact="spec.md",
                stages=STAGES,
                mode="manual",
                **extra,
            ):
                pass

        asyncio.run(go())
        [start] = journal.records(d, kind="start")
        return start

    def test_a_step_records_the_commit_it_ran_on(self):
        import subprocess

        with tempfile.TemporaryDirectory() as d:
            git = ["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t"]
            subprocess.run(["git", "init", "-q", d], check=True)
            (Path(d) / "a.txt").write_text("a\n", encoding="utf-8")
            subprocess.run(git + ["add", "a.txt"], check=True)
            subprocess.run(git + ["commit", "-qm", "a"], check=True)
            head = subprocess.run(
                ["git", "-C", d, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
            ).stdout.strip()

            self.assertEqual(self._start_record(d)["head"], head)

    def test_a_step_outside_git_records_no_commit(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self._start_record(d)["head"], "")

    def test_the_shortlist_stamp_is_carried_into_start_only_when_given(self):
        """The runner carries it; `service.steps.run_step` works it out."""
        stamp = {"rank": 2, "of": 5, "record": {"at": "t", "n": 3}}
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self._start_record(d, shortlist=stamp)["shortlist"], stamp)
        with tempfile.TemporaryDirectory() as d:
            self.assertNotIn("shortlist", self._start_record(d))


class AStepCarriesItsGrantAndNothingOfTheMachine(unittest.TestCase):
    class Replies:
        def __init__(self):
            self.kw: dict = {}
            self.prompt = ""

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            if session_id is not None:
                # Another stage's step reads `# Idea:` as no title and gets a repair turn on this
                # session; what is asked of here is the step's own call.
                return
            self.prompt = text
            self.kw = kw
            yield ("chunk", "# Idea: x\nStatus: accepted.\n")
            await _submits(kw)
            yield ("done", {"session_id": "s-idea", "cost": {}})

    def _run(self, d: str, sessions, journal=None, stage="idea"):
        r = Runner(sessions=sessions, journal=journal)

        async def go():
            async for _ in r.run(
                workspace=d,
                directory=Path(d) / ".cos" / UNIT,
                journal_key=d,
                unit=UNIT,
                stage=stage,
                artifact=f"{stage}.md",
                stages=STAGES,
                mode="manual",
            ):
                pass

        asyncio.run(go())

    def test_an_empty_grant_is_an_empty_list_whatever_cos_tools_says(self):
        # `None` fell back to `COS_TOOLS`, so an `idea` held `Read` with no gate.
        from coscc.agent import sessions as sessions_mod
        from coscc.config import Config

        replies = self.Replies()
        with tempfile.TemporaryDirectory() as d:
            make_unit(Path(d))
            self._run(d, replies)
            options = sessions_mod._options(
                Config(tools=("Read", "Bash")),
                d,
                None,
                tools=replies.kw["tools"],
                data_dir=d,
            )
        self.assertEqual(replies.kw["tools"], [])
        # The gate an `idea` now gets lets `submit` through and nothing else.
        gate = replies.kw["can_use_tool"]
        self.assertEqual(list(replies.kw["mcp_servers"]), ["cos"])
        for tool, allowed in (("mcp__cos__submit", True), ("Read", False), ("Bash", False)):
            verdict = asyncio.run(gate(tool, {"file_path": d, "command": "ls"}, None))
            self.assertEqual(type(verdict).__name__ == "PermissionResultAllow", allowed, tool)
        self.assertEqual(options.tools, [])

    def test_the_start_row_names_the_instructions_the_session_was_given(self):
        # Verbatim first, scoped after, relative to the step's `cwd`.
        with tempfile.TemporaryDirectory() as d:
            make_unit(Path(d))
            rules = Path(d) / ".claude" / "rules"
            rules.mkdir(parents=True)
            (Path(d) / ".claude" / "CLAUDE.md").write_text("PROJECT\n", encoding="utf-8")
            (rules / "app.md").write_text('---\npaths: ["x/**"]\n---\nAPP\n', encoding="utf-8")
            journal = Journal(d, d)
            self._run(d, self.Replies(), journal)
            [start] = journal.records(d, kind="start")
        self.assertEqual(
            start["instructions"],
            {"verbatim": [".claude/CLAUDE.md"], "scoped": [".claude/rules/app.md"]},
        )

    def test_the_prompt_is_build_prompts_own_byte_for_byte(self):
        # The runner hands on exactly what `build_prompt` made from the arguments it was given, and
        # the project's block goes to the system prompt, not here.
        from coscc.runner import step as runner_mod

        replies = self.Replies()
        built: list[str] = []

        def spy(*args, **kwargs):
            # Called again with the same arguments, before the step writes its artifact.
            again, _, _ = compose_prompt(*args, **kwargs)
            built.append(again)
            return compose_prompt(*args, **kwargs)

        with tempfile.TemporaryDirectory() as d:
            make_unit(Path(d), idea_md="Status: accepted.\nI")
            (Path(d) / "CLAUDE.md").write_text("PROJECT\n", encoding="utf-8")
            with mock.patch.object(runner_mod, "compose_prompt", spy):
                self._run(d, replies, stage="intent")
        self.assertEqual(len(built), 1)
        self.assertEqual(replies.prompt.encode("utf-8"), built[0].encode("utf-8"))
        self.assertNotIn("# Project instructions", replies.prompt)


class AStepRecordsTheBaseItRanOn(unittest.TestCase):
    """`base` is `service.py`'s to compute; this module only carries it from `Runner.run`'s
    caller into the `start` record, the same way it already carries `head`."""

    class Replies:
        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            yield ("chunk", "# Impl: x\nStatus: accepted.\n")
            await _submits(kw)
            yield ("done", {"session_id": "s-impl", "cost": {}})

    def _start_record(self, d: str, **run_kw) -> dict:
        make_unit(Path(d), intent_md="Status: accepted.\nI")
        journal = Journal(d, d)
        r = Runner(sessions=self.Replies(), journal=journal)

        async def go():
            async for _ in r.run(
                workspace=d,
                directory=Path(d) / ".cos" / UNIT,
                journal_key=d,
                unit=UNIT,
                stage="impl",
                artifact="impl.md",
                stages=STAGES,
                mode="manual",
                **run_kw,
            ):
                pass

        asyncio.run(go())
        [start] = journal.records(d, kind="start")
        return start

    def test_the_base_handed_in_is_the_base_recorded(self):
        base = {"ref": "origin/main", "sha": "abc1234", "fresh": False, "reason": "R"}
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self._start_record(d, base=base)["base"], base)

    def test_no_base_handed_in_records_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(self._start_record(d)["base"])


class AStageRunAgainIsToldWhy(unittest.TestCase):
    """A stage a person ran again from the board is told so, with their note, and a stage nobody ran
    again gets the prompt and the `start` record it got before."""

    HEADING = "# Why this stage runs again"
    PLAN = "# Plan: x\nStatus: accepted.\n\n1. PLAN-BODY-0054\n"
    ANSWERS = "\n## Answers\n\n### Câu 1\nAnswered by: A. Date: 2026-09-26. Via: product.\n\nANSWER-0054\n"

    def _unit(self, d: str) -> Path:
        return make_unit(
            Path(d),
            intent_md="Status: accepted.\nI",
            spec_md="Status: accepted.\nS",
            plan_md=self.PLAN + self.ANSWERS,
            pr_md="# PR: x\nStatus: accepted.\n\nPR-BODY-0054\n",
        )

    def _prompt(self, d: str, stage: str, **kw) -> str:
        return compose_prompt(
            d, self._unit(d), UNIT, stage, STAGES, f"{stage}.md", writes_own=stage == "pr", **kw
        )[0]

    def test_without_rerun_not_one_byte_changes(self):
        with tempfile.TemporaryDirectory() as d:
            for stage in ("intent", "spec", "plan"):
                self.assertEqual(
                    self._prompt(d, stage, rerun=False, rerun_note="x"), self._prompt(d, stage)
                )
                self.assertNotIn(self.HEADING, self._prompt(d, stage))

    def test_the_note_reaches_the_prompt_verbatim_before_the_task(self):
        note = "NOTE-0054: tách bước 3\n  giữ nguyên thụt lề"
        with tempfile.TemporaryDirectory() as d:
            prompt = self._prompt(d, "plan", rerun=True, rerun_note=note)
            self.assertIn(note, prompt)
            self.assertIn("recorded as `owner`", prompt)
            self.assertIn("not a decision anyone approved", prompt)
            self.assertLess(prompt.index(self.HEADING), prompt.index("# Your task"))

    def test_no_note_says_so(self):
        with tempfile.TemporaryDirectory() as d:
            prompt = self._prompt(d, "spec", rerun=True, rerun_note="  ")
            self.assertIn("No note was given: rewrite it on what changed since", prompt)

    def test_plan_carries_its_text_above_answers(self):
        with tempfile.TemporaryDirectory() as d:
            prompt = self._prompt(d, "plan", rerun=True)
            section = prompt[prompt.index(self.HEADING) : prompt.index("# Your task")]
            self.assertIn("PLAN-BODY-0054", section)
            self.assertNotIn("ANSWER-0054", section)
            self.assertNotIn("\n## Answers\n", section)

    def test_the_start_record_carries_the_rerun_and_nothing_otherwise(self):
        records = AStepRecordsTheBaseItRanOn()
        with tempfile.TemporaryDirectory() as d:
            plain = records._start_record(d)
        with tempfile.TemporaryDirectory() as d:
            unasked = records._start_record(d, rerun=False, rerun_note="x")
        with tempfile.TemporaryDirectory() as d:
            rerun = records._start_record(d, rerun=True, rerun_note="NOTE")
        self.assertNotIn("rerun", plain)
        self.assertEqual(set(unasked), set(plain))
        self.assertEqual(unasked["prompt_chars"], plain["prompt_chars"])
        self.assertEqual((rerun["rerun"], rerun["rerun_note"]), (True, "NOTE"))


class AStepRecordsThePlanDrift(AStepRecordsTheBaseItRanOn):
    """`plan_drift` is `service.py`'s; the record only carries it."""

    def test_the_drift_handed_in_is_the_drift_recorded(self):
        drift = {
            "plan_sha": "a" * 40,
            "main_sha": "b" * 40,
            "files": ["x.py"],
            "checked": True,
            "reason": "",
        }
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self._start_record(d, plan_drift=drift)["plan_drift"], drift)

    def test_no_drift_handed_in_leaves_no_field(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertNotIn("plan_drift", self._start_record(d))


class AReviewIsHandedTheCommitItReviews(unittest.TestCase):
    """Every step runs in the unit's git worktree, whose `.git` is a file naming a directory under
    the main repository's `.git/worktrees/`. The app reads it and puts it in the prompt instead."""

    def _worktree(self, d: str) -> tuple[Path, str]:
        import subprocess

        main = Path(d) / "main"
        tree = Path(d) / "tree"
        git = ["git", "-C", str(main), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(["git", "init", "-q", str(main)], check=True)
        (main / "a.txt").write_text("a\n", encoding="utf-8")
        subprocess.run(git + ["add", "a.txt"], check=True)
        subprocess.run(git + ["commit", "-qm", "a"], check=True)
        subprocess.run(git + ["worktree", "add", "-q", "-b", "fix/x", str(tree)], check=True)
        head = subprocess.run(
            ["git", "-C", str(tree), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return tree, head

    def test_the_worktree_git_directory_is_outside_what_review_may_read(self):
        with tempfile.TemporaryDirectory() as d:
            tree, _ = self._worktree(d)
            self.assertTrue((tree / ".git").is_file())
            gitdir = (tree / ".git").read_text(encoding="utf-8").split(":", 1)[1].strip()
            unit = Path(d) / "store" / UNIT
            reason = decide(
                grant_for("review"), "Read", {"file_path": gitdir + "/HEAD"}, str(tree), str(unit)
            )
            self.assertIn("reading outside the workspace", reason)

    def test_a_review_run_in_a_worktree_is_handed_its_head(self):
        seen = {}

        class Replies:
            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                seen["prompt"] = text
                yield ("chunk", "# Review: x\nStatus: accepted.\n\n## Round 1\n")
                await _submits(kw)
                yield ("done", {"session_id": "s-r", "cost": {}})

        with tempfile.TemporaryDirectory() as d:
            tree, head = self._worktree(d)
            store = Path(d) / "store"
            make_unit(store, intent_md="Status: accepted.\nI")
            journal = Journal(str(tree), str(tree))
            r = Runner(sessions=Replies(), journal=journal)

            async def go():
                async for _ in r.run(
                    workspace=str(tree),
                    directory=store / ".cos" / UNIT,
                    journal_key=str(tree),
                    unit=UNIT,
                    stage="review",
                    artifact="review.md",
                    stages=STAGES,
                    mode="manual",
                    cwd=str(tree),
                ):
                    pass

            asyncio.run(go())
            self.assertIn("# The commit you are reviewing", seen["prompt"])
            self.assertIn(f"    {head}\n", seen["prompt"])
            [start] = journal.records(str(tree), kind="start")
            self.assertEqual(start["head"], head)

    def test_no_head_is_said_rather_than_left_to_a_guess(self):
        with tempfile.TemporaryDirectory() as d:
            make_unit(Path(d), intent_md="Status: accepted.\nI")
            prompt, _ = build_prompt(
                d, Path(d) / ".cos" / UNIT, UNIT, "review", STAGES, "review.md"
            )
            self.assertIn("# The commit you are reviewing", prompt)
            self.assertIn("could not read the head", prompt)
            self.assertIn("Do not guess one", prompt)

    def test_only_review_is_handed_the_head(self):
        with tempfile.TemporaryDirectory() as d:
            make_unit(Path(d), intent_md="Status: accepted.\nI")
            prompt, _ = build_prompt(
                d, Path(d) / ".cos" / UNIT, UNIT, "spec", STAGES, "spec.md", head="a" * 40
            )
            self.assertNotIn("# The commit you are reviewing", prompt)
            self.assertNotIn("a" * 40, prompt)


if __name__ == "__main__":
    unittest.main()


class NarrationBeforeAToolCallIsNotTheArtifact(unittest.TestCase):
    """Its `plan.md` opened with *"Tôi đang đọc code để viết plan — xong the loop..."* run
    into the title with no newline between them. The file no longer began with `# Plan:`
    and `Status:` was no longer its second line. the loop read it anyway — it looks for
    `Status:` anywhere in the file — so this corrupted every plan the board produced
    without ever failing a gate.

    It began the hour `plan` was given `Read`, `Glob` and `Grep` (#22). Before that no
    prose stage had tools, so no prose stage ever spoke twice, and concatenating every
    chunk was indistinguishable from taking the reply."""

    class Narrates:
        """A session that thinks out loud, reads two files, then answers."""

        def __init__(self, journal=None):
            self.journal = journal

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            yield ("chunk", "Reading the board reader and the page handlers.")
            yield ("tool", "Read")
            yield ("chunk", "Now checking the route the proof will use.")
            yield ("tool", "Grep")
            yield ("chunk", "# Plan: a problem\nIntent: intent.md. Status: accepted.\n\n## Body\n")
            await _submits(kw)
            yield ("done", {"session_id": "s-1", "cost": {"output_tokens": 9}})

    def go(self, d):
        make_unit(
            Path(d),
            intent_md="Status: accepted.\nTHE-INTENT",
            spec_md="Status: accepted.\nTHE-SPEC",
        )
        runner = Runner(self.Narrates(), None)

        async def run():
            out = []
            async for item in runner.run(
                workspace=d,
                directory=Path(d) / ".cos" / UNIT,
                journal_key=d,
                unit=UNIT,
                stage="plan",
                artifact="plan.md",
                stages=STAGES,
                mode="autonomous",
            ):
                out.append(item)
            return out

        return asyncio.run(run()), Path(d) / ".cos" / UNIT / "plan.md"

    def test_the_artifact_starts_at_its_own_heading(self):
        with tempfile.TemporaryDirectory() as d:
            _, written = self.go(d)
            self.assertTrue(
                written.read_text(encoding="utf-8").startswith("# Plan:"),
                written.read_text(encoding="utf-8")[:120],
            )

    def test_no_narration_survives_into_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            _, written = self.go(d)
            body = written.read_text(encoding="utf-8")
            self.assertNotIn("Reading the board reader", body)
            self.assertNotIn("Now checking the route", body)

    def test_the_status_line_is_the_second_line_again(self):
        """What `write-plan`'s template asks for, and what a reader looks at first."""
        with tempfile.TemporaryDirectory() as d:
            _, written = self.go(d)
            self.assertIn("Status: accepted", written.read_text(encoding="utf-8").splitlines()[1])

    def test_the_narration_still_reaches_the_page_while_it_happens(self):
        """Dropping it from the file must not drop it from the stream a person watches."""
        with tempfile.TemporaryDirectory() as d:
            items, _ = self.go(d)
            chunks = [p for k, p in items if k == "chunk"]
            self.assertIn("Reading the board reader and the page handlers.", chunks)

    def test_the_tool_signal_is_not_forwarded_as_a_row_of_its_own(self):
        """`coscc/api.py` reads every kind that is not `chunk` as the terminal `done`."""
        with tempfile.TemporaryDirectory() as d:
            items, _ = self.go(d)
            self.assertEqual([k for k, _ in items if k not in ("chunk", "done")], [])


class ReviewRoundsAccumulate(unittest.TestCase):
    """The app writes `review.md` from the reply, and the reply carried only what that run
    had to say. Round 1 and its five findings were gone, and so was the count the loop
    reads to stop after N rounds and ask for a person -- a limit that resets every run is
    one that never arrives."""

    ROUND1 = (
        "## Round 1\n\nReviewed: abc1234. Verdict: changes-requested.\n\n"
        "### Findings\n\n- F1 [open] a.py:3 — high — ROUND-ONE-MARKER\n"
    )

    class Replies:
        def __init__(self, text):
            self.text = text

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            self.prompt = text
            yield ("chunk", self.text)
            await _submits(kw)
            yield ("done", {"session_id": "s", "cost": {}})

    def setUp(self):
        a_head(self)

    def run_review(self, d, reply):
        unit = make_unit(
            Path(d),
            intent_md="Status: accepted.\nI",
            pr_md="Status: accepted.\nP",
            review_md="# Review: x\nPR: pr.md. Status: changes-requested.\n\n" + self.ROUND1,
        )
        session = self.Replies(reply)

        async def go():
            last = None
            async for item in Runner(session, None).run(
                workspace=d,
                directory=unit,
                journal_key=d,
                unit=UNIT,
                stage="review",
                artifact="review.md",
                stages=STAGES,
                mode="manual",
            ):
                last = item
            return last[1]

        return session, unit / "review.md", go

    def test_the_prompt_carries_the_earlier_rounds(self):
        with tempfile.TemporaryDirectory() as d:
            session, _, go = self.run_review(
                d, "# Review: x\nStatus: accepted.\n\n" + self.ROUND1 + "\n## Round 2\n\nok\n"
            )
            asyncio.run(go())
            self.assertIn("ROUND-ONE-MARKER", session.prompt)

    def test_a_reply_that_keeps_round_one_is_written(self):
        with tempfile.TemporaryDirectory() as d:
            _, written, go = self.run_review(
                d, "# Review: x\nStatus: accepted.\n\n" + self.ROUND1 + "\n## Round 2\n\nok\n"
            )
            asyncio.run(go())
            body = written.read_text(encoding="utf-8")
            self.assertIn("ROUND-ONE-MARKER", body)
            self.assertIn("## Round 2", body)

    def test_a_reply_with_only_the_new_round_keeps_round_one(self):
        """2026-09-23: copying round 2 back is where two reviews were stopped."""
        with tempfile.TemporaryDirectory() as d:
            _, written, go = self.run_review(
                d, "# Review: x\nStatus: accepted.\n\n## Round 2\n\nall fine\n"
            )
            done = asyncio.run(go())
            self.assertEqual(done["outcome"], "done", done["error"])
            body = written.read_text(encoding="utf-8")
            self.assertTrue(body.startswith("# Review: x\nStatus: accepted.\n\n## Round 1"))
            self.assertLess(body.index("ROUND-ONE-MARKER"), body.index("## Round 2"))

    def test_the_prompt_no_longer_asks_for_a_copy(self):
        with tempfile.TemporaryDirectory() as d:
            session, _, go = self.run_review(
                d, "# Review: x\nStatus: accepted.\n\n## Round 2\n\nok\n"
            )
            asyncio.run(go())
            self.assertIn("Do not copy them", session.prompt)
            self.assertNotIn("byte for byte", session.prompt)

    def test_a_reply_that_changes_round_one_leaves_the_file_as_it_was(self):
        with tempfile.TemporaryDirectory() as d:
            _, written, go = self.run_review(
                d,
                "# Review: x\nStatus: accepted.\n\n## Round 1\n\nnothing was wrong\n\n"
                "## Round 2\n\nall fine\n",
            )
            # The runner reports a refusal as the step's outcome rather than raising.
            done = asyncio.run(go())
            self.assertNotEqual(done["outcome"], "done")
            self.assertIn("changes an earlier review round", done["error"])
            self.assertEqual(
                written.read_text(encoding="utf-8"),
                "# Review: x\nPR: pr.md. Status: changes-requested.\n\n" + self.ROUND1,
            )

    def test_a_reply_that_adds_no_round_leaves_the_file_as_it_was(self):
        with tempfile.TemporaryDirectory() as d:
            _, written, go = self.run_review(d, "# Review: x\nStatus: accepted.\n\nlooks fine\n")
            done = asyncio.run(go())
            self.assertNotEqual(done["outcome"], "done")
            self.assertIn("adds no review round", done["error"])
            self.assertIn("ROUND-ONE-MARKER", written.read_text(encoding="utf-8"))


class RerunningKeepsTheAnswers(unittest.TestCase):
    """Re-running a prose stage used to overwrite its artifact whole, so a `## Answers` block the
    answer route had appended was gone with no trace but an `outputs` row
    (`.claude/rules/coscc-app.md`, the hazard this unit rewrites)."""

    ANSWERED = (
        "Author: t. Status: accepted.\n\n## Open questions\n\n1. Placeholder?\n\n"
        "## Answers\n\n### Câu 1\nAnswered by: Phong. Date: 2026-09-24. Via: product.\n\n"
        "{mark}\n"
    )

    ROUND1 = (
        "## Round 1\n\nReviewed: abc1234. Verdict: changes-requested.\n\n"
        "### Findings\n\n- F1 [open] a.py:3 — high — ROUND-ONE-MARKER-0025\n"
    )

    class Replies:
        """`ReviewRoundsAccumulate.Replies`, plus `mid_write`: called after the reply has been
        handed over and before `done` is yielded, so a test can simulate a person's answer landing
        on disk while the step is still running."""

        def __init__(self, text, mid_write=None):
            self.text = text
            self.mid_write = mid_write

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            self.prompt = text
            yield ("chunk", self.text)
            if self.mid_write:
                self.mid_write()
            await _submits(kw)
            yield ("done", {"session_id": "s", "cost": {}})

    def unit_dir(self, d, **files):
        return make_unit(Path(d), **files)

    def run_once(self, d, stage, artifact, reply, mid_write=None):
        directory = Path(d) / ".cos" / UNIT
        session = self.Replies(reply, mid_write)

        async def go():
            last = None
            async for item in Runner(session, None).run(
                workspace=d,
                directory=directory,
                journal_key=d,
                unit=UNIT,
                stage=stage,
                artifact=artifact,
                stages=STAGES,
                mode="manual",
            ):
                last = item
            return last[1]

        return asyncio.run(go())

    def test_a_reply_without_answers_keeps_the_section_on_every_non_review_stage(self):
        for stage in ("idea", "intent", "spec", "plan"):
            with self.subTest(stage=stage):
                with tempfile.TemporaryDirectory() as d:
                    artifact = f"{stage}.md"
                    existing = self.ANSWERED.format(mark=f"KEEP-{stage.upper()}-0025")
                    self.unit_dir(d, **{artifact.replace(".", "_"): existing})
                    path = Path(d) / ".cos" / UNIT / artifact
                    before_section = answers_section(path.read_bytes())
                    reply = f"# {stage.title()}: x\nStatus: accepted.\n\nno answers here.\n"
                    done = self.run_once(d, stage, artifact, reply)
                    after = path.read_bytes()
                    self.assertEqual(done["outcome"], "done", done)
                    self.assertTrue(after.endswith(before_section))

    def test_b_a_reply_whose_answers_match_disk_still_yields_exactly_one_section(self):
        with tempfile.TemporaryDirectory() as d:
            existing = self.ANSWERED.format(mark="MATCHING-MARK-0025")
            self.unit_dir(d, spec_md=existing)
            reply = (
                "# Spec: x\nStatus: accepted.\n\n## Requirements\n\nbody\n\n"
                + existing[existing.index("## Answers") :]
            )
            done = self.run_once(d, "spec", "spec.md", reply)
            after = (Path(d) / ".cos" / UNIT / "spec.md").read_text(encoding="utf-8")
            self.assertEqual(done["outcome"], "done", done)
            self.assertEqual(after.count("## Answers"), 1)

    def test_c_a_reply_whose_answers_differ_from_disk_is_overruled_by_disk(self):
        with tempfile.TemporaryDirectory() as d:
            self.unit_dir(d, spec_md=self.ANSWERED.format(mark="DISK-MARK-0025"))
            reply = (
                "# Spec: x\nStatus: accepted.\n\n## Requirements\n\nbody\n\n"
                "## Answers\n\n### Câu 1\nAnswered by: a model. Date: 2099-01-01. "
                "Via: product.\n\nREPLY-MARK-SHOULD-NOT-LAND-0025\n"
            )
            done = self.run_once(d, "spec", "spec.md", reply)
            after = (Path(d) / ".cos" / UNIT / "spec.md").read_text(encoding="utf-8")
            self.assertEqual(done["outcome"], "done", done)
            self.assertIn("DISK-MARK-0025", after)
            self.assertNotIn("REPLY-MARK-SHOULD-NOT-LAND-0025", after)

    def test_d_a_reply_with_answers_but_none_on_disk_writes_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.unit_dir(
                d,
                spec_md="Author: t. Status: accepted.\n\n## Requirements\n\n"
                "nothing answered yet.\n",
            )
            reply = (
                "# Spec: x\nStatus: accepted.\n\n## Requirements\n\nbody\n\n"
                "## Answers\n\n### Câu 1\nAnswered by: a model. Date: 2099-01-01. "
                "Via: product.\n\nSHOULD-NOT-LAND-0025\n"
            )
            done = self.run_once(d, "spec", "spec.md", reply)
            after = (Path(d) / ".cos" / UNIT / "spec.md").read_text(encoding="utf-8")
            self.assertEqual(done["outcome"], "done", done)
            self.assertNotIn("## Answers", after)

    def test_e_a_reply_with_no_status_line_leaves_the_file_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            self.unit_dir(d, spec_md=self.ANSWERED.format(mark="UNTOUCHED-MARK-0025"))
            path = Path(d) / ".cos" / UNIT / "spec.md"
            before = path.read_bytes()
            done = self.run_once(d, "spec", "spec.md", "# Spec: x\n\nNo Status line at all.\n")
            self.assertNotEqual(done["outcome"], "done")
            self.assertEqual(path.read_bytes(), before)

    def test_e2_a_status_line_only_under_the_replys_own_answers_is_refused(self):
        # `check_reply` saw the whole reply, so a `Status:` living only under the reply's `##
        # Answers` passed it, and `strip_answers` then cut the one line the gate reads.
        with tempfile.TemporaryDirectory() as d:
            self.unit_dir(d, spec_md=self.ANSWERED.format(mark="F1-MARK-0025"))
            path = Path(d) / ".cos" / UNIT / "spec.md"
            before = path.read_bytes()
            reply = (
                "# Spec: x\n\n## Requirements\n\nbody\n\n"
                "## Answers\n\n### Câu 1\nStatus: accepted.\n"
            )
            done = self.run_once(d, "spec", "spec.md", reply)
            self.assertNotEqual(done["outcome"], "done")
            self.assertEqual(path.read_bytes(), before)

    def test_f_a_block_appended_while_the_step_runs_is_still_on_disk_after(self):
        with tempfile.TemporaryDirectory() as d:
            self.unit_dir(
                d,
                spec_md="Author: t. Status: accepted.\n\n## Requirements\n\nnothing yet.\n",
            )
            path = Path(d) / ".cos" / UNIT / "spec.md"

            def mid_write():
                with path.open("a", encoding="utf-8") as f:
                    f.write(
                        "\n## Answers\n\n### Câu 1\nAnswered by: Phong. "
                        "Date: 2026-09-24. Via: product.\n\nMID-RUN-MARKER-0025\n"
                    )

            done = self.run_once(
                d,
                "spec",
                "spec.md",
                "# Spec: x\nStatus: accepted.\n\nno answers here.\n",
                mid_write=mid_write,
            )
            after = path.read_text(encoding="utf-8")
            self.assertEqual(done["outcome"], "done", done)
            self.assertIn("MID-RUN-MARKER-0025", after)

    def test_g_review_with_answers_and_a_new_round_keeps_both(self):
        a_head(self)
        with tempfile.TemporaryDirectory() as d:
            existing = (
                "# Review: x\nPR: pr.md. Status: changes-requested.\n\n"
                + self.ROUND1
                + "\n## Answers\n\n### Câu 1\nAnswered by: Phong. Date: 2026-09-24. "
                "Via: product.\n\nREVIEW-ANSWER-MARK-0025\n"
            )
            self.unit_dir(d, review_md=existing)
            path = Path(d) / ".cos" / UNIT / "review.md"
            before_section = answers_section(path.read_bytes())
            reply = (
                "# Review: x\nStatus: accepted.\n\n## Round 2\n\n"
                "Reviewed: def5678. Verdict: pass.\n\n### Findings\n\nnone\n"
            )
            done = self.run_once(d, "review", "review.md", reply)
            after = path.read_bytes()
            self.assertEqual(done["outcome"], "done", done)
            self.assertTrue(after.endswith(before_section))
            i1 = after.find(b"ROUND-ONE-MARKER-0025")
            i2 = after.find(b"## Round 2")
            i3 = after.find(b"## Answers")
            self.assertNotIn(-1, (i1, i2, i3))
            self.assertLess(i1, i2)
            self.assertLess(i2, i3)
            self.assertEqual(after.decode("utf-8").count("\n## Round "), 2)

    def test_h_a_reply_that_rewrites_round_one_on_a_review_with_answers_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            existing = (
                "# Review: x\nPR: pr.md. Status: changes-requested.\n\n"
                + self.ROUND1
                + "\n## Answers\n\n### Câu 1\nAnswered by: Phong. Date: 2026-09-24. "
                "Via: product.\n\nREVIEW-ANSWER-MARK-0025\n"
            )
            self.unit_dir(d, review_md=existing)
            path = Path(d) / ".cos" / UNIT / "review.md"
            before = path.read_bytes()
            reply = (
                "# Review: x\nStatus: accepted.\n\n## Round 1\n\nnothing was wrong\n\n"
                "## Round 2\n\nall fine\n"
            )
            done = self.run_once(d, "review", "review.md", reply)
            self.assertNotEqual(done["outcome"], "done")
            self.assertIn("changes an earlier review round", done["error"])
            self.assertEqual(path.read_bytes(), before)


class AStepWorksInItsUnitsWorktree(unittest.TestCase):
    """`cwd` is the session's directory and the write boundary."""

    def test_writing_outside_the_worktree_is_refused_and_inside_is_allowed(self):
        class Probe:
            def __init__(self):
                self.cwd = None
                self.workspace = None
                self.answers = {}

            async def stream(
                self,
                cwd,
                text,
                session_id=None,
                max_turns=1,
                can_use_tool=None,
                workspace=None,
                **kw,
            ):
                self.cwd, self.workspace = cwd, workspace
                for name, target in (
                    ("inside", f"{cwd}/x.txt"),
                    ("workspace", f"{workspace}/x.txt"),
                ):
                    got = await can_use_tool("Write", {"file_path": target, "content": "x"}, None)
                    self.answers[name] = type(got).__name__
                await _submits(kw)
                yield ("done", {"session_id": "s-impl", "cost": {}})

        probe = Probe()
        with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as wt:
            directory = make_unit(
                Path(ws), intent_md="Status: accepted.\nI", plan_md="Status: accepted.\nP"
            )
            (directory / "impl.md").write_text("# Impl\nStatus: accepted.\n", encoding="utf-8")
            r = Runner(sessions=probe, journal=None)

            async def go():
                return [
                    ev
                    async for ev in r.run(
                        workspace=ws,
                        directory=directory,
                        journal_key=ws,
                        unit=UNIT,
                        stage="impl",
                        artifact="impl.md",
                        stages=STAGES,
                        mode="autonomous",
                        cwd=wt,
                    )
                ]

            _, final = asyncio.run(go())[-1]
            self.assertEqual(final["outcome"], "done", final)
            self.assertEqual((probe.cwd, probe.workspace), (wt, ws))
            self.assertEqual(probe.answers["inside"], "PermissionResultAllow")
            self.assertEqual(probe.answers["workspace"], "PermissionResultDeny")


class AnImplReadsItsSiblings(unittest.TestCase):
    """`read_also` reaches the gate; the note reaches the prompt."""

    def _run(self, **kw):
        class Probe:
            def __init__(self):
                self.answers, self.prompt = {}, ""

            async def stream(self, cwd, text, session_id=None, max_turns=1, can_use_tool=None, **_):
                self.prompt = text
                for name, (tool, inp) in self.calls.items():
                    self.answers[name] = type(await can_use_tool(tool, inp, None)).__name__
                await _submits(_)
                yield ("done", {"session_id": "s-impl", "cost": {}})

        probe = Probe()
        with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as sib:
            (Path(sib) / "api.py").write_text("x = 1\n", encoding="utf-8")
            probe.calls = {
                "read": ("Read", {"file_path": f"{sib}/api.py"}),
                "write": ("Write", {"file_path": f"{sib}/api.py", "content": "y"}),
                "git": ("Bash", {"command": f"git -C {sib} status"}),
            }
            directory = make_unit(
                Path(ws), intent_md="Status: accepted.\nI", plan_md="Status: accepted.\nP"
            )
            r = Runner(sessions=probe, journal=None)

            async def go():
                return [
                    ev
                    async for ev in r.run(
                        workspace=ws,
                        directory=directory,
                        journal_key=ws,
                        unit=UNIT,
                        stage="impl",
                        artifact="impl.md",
                        stages=STAGES,
                        mode="autonomous",
                        **{
                            k: (
                                v.format(sib=sib)
                                if isinstance(v, str)
                                else tuple(x.format(sib=sib) for x in v)
                            )
                            for k, v in kw.items()
                        },
                    )
                ]

            asyncio.run(go())
        return probe

    def test_a_sibling_is_read_and_neither_written_nor_pointed_at_by_git(self):
        probe = self._run(read_also=("{sib}",), siblings_note="- api: {sib} at abc1234")
        self.assertEqual(
            probe.answers,
            {
                "read": "PermissionResultAllow",
                "write": "PermissionResultDeny",
                "git": "PermissionResultDeny",
            },
        )
        self.assertIn("# The sibling repositories this step may read", probe.prompt)
        self.assertIn("at abc1234", probe.prompt)

    def test_a_unit_with_no_idea_runs_impl_with_the_read_also_it_had(self):
        probe = self._run()
        self.assertEqual(probe.answers["read"], "PermissionResultDeny")
        self.assertNotIn("sibling repositories", probe.prompt)


class TheStepRunsOnTheModelItWasGiven(unittest.TestCase):
    """The runner does not choose a model; it passes on the one it was given and writes it into the
    run log."""

    def run_spec(self, d, **kw):
        class Probe:
            def __init__(self):
                self.kw = None

            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                self.kw = kw
                yield ("chunk", "# Spec: x\nStatus: accepted.\n")
                await _submits(kw)
                yield ("done", {"session_id": "s-m", "cost": {}})

        probe = Probe()
        make_unit(Path(d), intent_md="Status: accepted.\nI")
        journal = Journal(d, d)
        r = Runner(sessions=probe, journal=journal)

        async def go():
            return [
                ev
                async for ev in r.run(
                    workspace=d,
                    directory=Path(d) / ".cos" / UNIT,
                    journal_key=d,
                    unit=UNIT,
                    stage="spec",
                    artifact="spec.md",
                    stages=STAGES,
                    mode="manual",
                    **kw,
                )
            ]

        _, final = asyncio.run(go())[-1]
        return probe, journal, final

    def test_the_model_reaches_the_session_and_the_start_record(self):
        with tempfile.TemporaryDirectory() as d:
            probe, journal, final = self.run_spec(d, model="m", model_source="override")
            self.assertEqual(final["outcome"], "done", final)
            self.assertEqual(probe.kw.get("model"), "m")
            start = journal.records(d, kind="start")[-1]
            self.assertEqual((start["model"], start["model_source"]), ("m", "override"))
            self.assertEqual(start["agents"], 1)
            row = journal.timeline(d, UNIT)[-1]
            self.assertEqual((row["model"], row["model_source"]), ("m", "override"))
            self.assertEqual((final["model"], final["model_source"]), ("m", "override"))

    def test_the_start_record_says_who_started_it(self):
        """`person` unless the caller names `autopilot`; nothing else is taken."""
        with tempfile.TemporaryDirectory() as d:
            _, journal, _ = self.run_spec(d)
            self.assertEqual(journal.records(d, kind="start")[-1]["started_by"], "person")
        with tempfile.TemporaryDirectory() as d:
            _, journal, _ = self.run_spec(d, started_by="autopilot")
            self.assertEqual(journal.records(d, kind="start")[-1]["started_by"], "autopilot")
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                self.run_spec(d, started_by="someone")
            self.assertEqual(Journal(d, d).records(d, kind="start"), [])

    def test_no_model_is_not_passed_at_all(self):
        # A stand-in `stream` without a `model` parameter must keep working.
        with tempfile.TemporaryDirectory() as d:
            probe, _, final = self.run_spec(d)
            self.assertEqual(final["outcome"], "done", final)
            self.assertNotIn("model", probe.kw)
            self.assertNotIn("effort", probe.kw)


class TheRunLogCarriesEffortLabelAndTerminal(unittest.TestCase):
    """The runner chooses none of it; it passes effort on and writes what it was given into `start`,
    the SDK's `terminal_reason` into `end`."""

    def run_spec(self, d, terminal=None, **kw):
        class Probe:
            def __init__(self):
                self.kw = None

            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                self.kw = kw
                yield ("chunk", "# Spec: x\nStatus: accepted.\n")
                await _submits(kw)
                yield ("done", {"session_id": "s-e", "cost": {}, "terminal_reason": terminal})

        probe = Probe()
        make_unit(Path(d), intent_md="Status: accepted.\nI")
        journal = Journal(d, d)
        r = Runner(sessions=probe, journal=journal)

        async def go():
            return [
                ev
                async for ev in r.run(
                    workspace=d,
                    directory=Path(d) / ".cos" / UNIT,
                    journal_key=d,
                    unit=UNIT,
                    stage="spec",
                    artifact="spec.md",
                    stages=STAGES,
                    mode="manual",
                    **kw,
                )
            ]

        _, final = asyncio.run(go())[-1]
        return probe, journal, final

    def test_start_carries_every_field(self):
        with tempfile.TemporaryDirectory() as d:
            probe, journal, final = self.run_spec(
                d,
                effort="high",
                effort_source="default",
                label_declared="routine",
                label="novel",
                label_source="escalated",
                impl_run=2,
            )
            self.assertEqual(final["outcome"], "done", final)
            self.assertEqual(probe.kw.get("effort"), "high")
            start = journal.records(d, kind="start")[-1]
            got = {
                k: start.get(k)
                for k in (
                    "effort",
                    "effort_source",
                    "label_declared",
                    "label",
                    "label_source",
                    "impl_run",
                )
            }
            self.assertEqual(
                got,
                {
                    "effort": "high",
                    "effort_source": "default",
                    "label_declared": "routine",
                    "label": "novel",
                    "label_source": "escalated",
                    "impl_run": 2,
                },
            )

    def test_no_impl_run_is_not_written(self):
        with tempfile.TemporaryDirectory() as d:
            _, journal, _ = self.run_spec(d)
            start = journal.records(d, kind="start")[-1]
            self.assertNotIn("impl_run", start)
            self.assertIsNone(start["label"])

    def test_end_carries_the_terminal_reason(self):
        with tempfile.TemporaryDirectory() as d:
            _, journal, final = self.run_spec(d, terminal="max_turns")
            self.assertEqual(final["outcome"], "exhausted", final)
            self.assertEqual(journal.records(d, kind="end")[-1]["terminal"], "max_turns")

    def test_end_fields_are_added_to_a_done_end(self):
        async def fields():
            return {"findings": 3, "findings_open": 1}

        with tempfile.TemporaryDirectory() as d:
            _, journal, _ = self.run_spec(d, end_fields=fields)
            end = journal.records(d, kind="end")[-1]
            self.assertEqual((end["findings"], end["findings_open"]), (3, 1))

    def test_a_failing_callback_leaves_an_ordinary_end(self):
        async def fields():
            raise RuntimeError("the board could not be read")

        with tempfile.TemporaryDirectory() as d:
            _, journal, final = self.run_spec(d, end_fields=fields)
            self.assertEqual(final["outcome"], "done", final)
            end = journal.records(d, kind="end")[-1]
            self.assertEqual(end["outcome"], "done")
            self.assertNotIn("findings", end)


class AnImplRunsUnderTheCeilingsOfItsLabel(unittest.TestCase):
    """`Runner.run` asks for the grant with the label it was given, and the ceilings the session
    receives are the ones the `start` record names."""

    def run_impl(self, d, **kw):
        class Probe:
            def __init__(self):
                self.max_turns = self.budget = None

            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                self.max_turns, self.budget = max_turns, kw.get("max_budget_usd")
                (directory / "impl.md").write_text("# Impl\nStatus: accepted.\n", encoding="utf-8")
                await _submits(kw)
                yield ("done", {"session_id": "s-impl", "cost": {}})

        probe = Probe()
        directory = make_unit(
            Path(d), intent_md="Status: accepted.\nI", plan_md="Status: accepted.\nP"
        )
        journal = Journal(d, d)
        r = Runner(sessions=probe, journal=journal)

        async def go():
            return [
                ev
                async for ev in r.run(
                    workspace=d,
                    directory=directory,
                    journal_key=d,
                    unit=UNIT,
                    stage="impl",
                    artifact="impl.md",
                    stages=STAGES,
                    mode="autonomous",
                    **kw,
                )
            ]

        asyncio.run(go())
        return probe, journal.records(d, kind="start")[-1]

    def test_novel_gets_the_novel_ceilings_whatever_made_it_novel(self):
        for source in ("declared", "forced", "missing", "escalated"):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as d:
                probe, start = self.run_impl(d, label="novel", label_source=source)
                self.assertEqual((probe.max_turns, probe.budget), (250, 16.0))
                self.assertEqual(start["max_turns"], probe.max_turns)

    def test_routine_and_no_label_keep_impls_ceilings(self):
        for label in ("routine", None):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as d:
                probe, start = self.run_impl(d, label=label)
                self.assertEqual((probe.max_turns, probe.budget), (120, 8.0))
                self.assertEqual(start["max_turns"], probe.max_turns)


class AStepRunsUnderTheCeilingsResolvedForIt(unittest.TestCase):
    """A ceiling stored in `cos.db` reaches the session of any stage, and the step's first event
    says what it was handed and where each value came from."""

    CONFIG_FIELDS = {
        "model",
        "model_source",
        "effort",
        "effort_source",
        "max_turns",
        "max_turns_source",
        "max_budget_usd",
        "max_budget_source",
    }

    def run_spec(self, d, prefs=None, break_store=False, **kw):
        from coscc.agent import steps
        from coscc.config import Config
        from coscc.store.db import Data
        from coscc.runlog import events

        config = Config(data_dir=str(Path(d) / "data"), config_home=str(Path(d) / "cfg"))
        for key, value in (prefs or {}).items():
            Data(config.data_dir).set_pref(key, value)

        class Probe:
            def __init__(self):
                self.max_turns = self.budget = None

            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                self.max_turns, self.budget = max_turns, kw.get("max_budget_usd")
                yield ("chunk", "# Spec: x\nStatus: accepted.\n")
                await _submits(kw)
                yield ("done", {"session_id": "s-c", "cost": {}})

        Probe.config = config
        probe = Probe()
        directory = make_unit(Path(d), intent_md="Status: accepted.\nI")
        running = steps.Running(d, UNIT, "spec", "")
        running.handle.recorder = events.Recorder("r-1", Data(config.data_dir), d, d, UNIT, "spec")

        async def go():
            return [
                ev
                async for ev in Runner(sessions=probe, journal=Journal(d, d)).run(
                    workspace=d,
                    directory=directory,
                    journal_key=d,
                    unit=UNIT,
                    stage="spec",
                    artifact="spec.md",
                    stages=STAGES,
                    mode="manual",
                    running=running,
                    **kw,
                )
            ]

        unreadable = (
            mock.patch.object(Data, "pref_rows", side_effect=sqlite3.OperationalError("locked"))
            if break_store
            else contextlib.nullcontext()
        )
        with unreadable:
            final = asyncio.run(go())[-1][1]
        stored, _ = Data(config.data_dir).step_events_page("r-1", None, 100)
        return probe, final, stored

    def test_an_overridden_turn_ceiling_is_what_the_session_gets_and_what_config_says(self):
        with tempfile.TemporaryDirectory() as d:
            probe, final, stored = self.run_spec(
                d,
                {"turns:spec": 30},
                model="m",
                model_source="override",
                effort="high",
                effort_source="default",
            )
        self.assertEqual(final["outcome"], "done", final)
        self.assertEqual(probe.max_turns, 30)
        config = next(e for e in stored if e["kind"] == "config")
        self.assertEqual(config["max_turns"], 30)
        self.assertEqual(config["max_turns_source"], "override")
        # The first event of the run, before anything the session said.
        self.assertEqual((stored[0]["kind"], stored[0]["seq"]), ("config", 1))
        self.assertEqual(set(config) - {"run", "seq", "at", "kind"}, self.CONFIG_FIELDS)
        self.assertEqual(
            {k: config[k] for k in self.CONFIG_FIELDS},
            {
                "model": "m",
                "model_source": "override",
                "effort": "high",
                "effort_source": "default",
                "max_turns": 30,
                "max_turns_source": "override",
                "max_budget_usd": grant_for("spec").max_budget_usd,
                "max_budget_source": "default",
            },
        )

    def test_an_overridden_budget_reaches_the_session(self):
        with tempfile.TemporaryDirectory() as d:
            probe, _, stored = self.run_spec(d, {"budget:spec": 2.5})
        self.assertEqual(probe.budget, 2.5)
        self.assertEqual(
            (stored[0]["max_budget_usd"], stored[0]["max_budget_source"]), (2.5, "override")
        )
        self.assertEqual(stored[0]["max_turns_source"], "default")

    def test_the_floor_of_a_step_that_submits_still_applies_to_an_override(self):
        with tempfile.TemporaryDirectory() as d:
            probe, _, stored = self.run_spec(d, {"turns:spec": 1})
        self.assertEqual(probe.max_turns, policy.SUBMIT_TURNS)
        self.assertEqual(
            (stored[0]["max_turns"], stored[0]["max_turns_source"]),
            (policy.SUBMIT_TURNS, "override"),
        )

    def test_the_other_rows_overrides_change_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            probe, _, stored = self.run_spec(d, {"turns:impl": 30, "turns:impl:novel": 40})
        self.assertEqual(probe.max_turns, grant_for("spec").max_turns)
        self.assertEqual(stored[0]["max_turns_source"], "default")

    def test_a_bad_value_is_skipped_and_the_step_starts_on_the_default(self):
        for bad in ("many", 100000, 0, 2.5, None):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as d:
                probe, final, stored = self.run_spec(d, {"turns:spec": bad})
                self.assertEqual(final["outcome"], "done", final)
                self.assertEqual(probe.max_turns, grant_for("spec").max_turns)
                self.assertEqual(stored[0]["max_turns_source"], "default")

    def test_a_store_that_cannot_be_read_is_no_override(self):
        with tempfile.TemporaryDirectory() as d:
            probe, final, stored = self.run_spec(d, {"turns:spec": 30}, break_store=True)
        self.assertEqual(final["outcome"], "done", final)
        self.assertEqual(probe.max_turns, grant_for("spec").max_turns)
        self.assertEqual(stored[0]["max_turns_source"], "default")

    def test_with_no_config_the_ceilings_are_the_grants(self):
        with tempfile.TemporaryDirectory() as d:
            runner = Runner(sessions=object(), journal=None)
            grant, ceilings = runner._configured(grant_for("spec"), "spec", None, d, {})
        self.assertEqual(
            (grant.max_turns, grant.max_budget_usd),
            (grant_for("spec").max_turns, grant_for("spec").max_budget_usd),
        )
        self.assertEqual(
            (ceilings["max_turns"], ceilings["max_budget_usd"]),
            (grant.max_turns, grant.max_budget_usd),
        )


class AStepTakenUpAgainWritesItsConfig(unittest.TestCase):
    """The new segment's `config` is what its owner kept, less what the first segment used."""

    def run_plan(self, d, owner):
        from coscc.agent import steps
        from coscc.store.db import Data
        from coscc.runlog import events

        directory = make_unit(
            Path(d), intent_md="Status: accepted.\nI", spec_md="Status: accepted.\nS"
        )
        sessions = AStepAnUpdatePaused.GoesOn(rest=AStepAnUpdatePaused.PLAN)
        running = steps.Running(d, UNIT, "plan", "")
        running.handle.recorder = events.Recorder("r-2", Data(d), d, d, UNIT, "plan")
        resume = {
            "suspend_id": "p1",
            "session_id": "s-1",
            "safe_uuid": "u9",
            "message": "MSG",
            "pieces": [],
            "api_calls": 3,
            "spent_usd": 0.5,
            "owner": {"kind": "step", "start_at": "t0", "head": "", **owner},
        }

        async def go():
            async for _ in Runner(sessions, Journal(d, d)).run(
                workspace=d,
                directory=directory,
                journal_key=d,
                unit=UNIT,
                stage="plan",
                artifact="plan.md",
                stages=STAGES,
                mode="manual",
                running=running,
                model="m",
                effort="high",
                resume=resume,
            ):
                pass

        asyncio.run(go())
        return sessions.calls[0], Data(d).step_events_page("r-2", None, 100)[0]

    def test_it_says_what_the_owner_kept_and_what_was_left_of_the_ceilings(self):
        owner = {
            "max_turns": 50,
            "max_budget_usd": 3.0,
            "max_turns_source": "override",
            "max_budget_source": "default",
            "model_source": "override",
            "effort_source": "default",
        }
        with tempfile.TemporaryDirectory() as d:
            call, stored = self.run_plan(d, owner)
        self.assertEqual((call["max_turns"], call["max_budget_usd"]), (47, 2.5))
        self.assertEqual(stored[0]["kind"], "config")
        self.assertEqual(
            {k: v for k, v in stored[0].items() if k not in ("run", "seq", "at", "kind")},
            {
                "model": "m",
                "model_source": "override",
                "effort": "high",
                "effort_source": "default",
                "max_turns": 47,
                "max_turns_source": "override",
                "max_budget_usd": 2.5,
                "max_budget_source": "default",
            },
        )
        # And it keeps them for the next pause.
        self.assertEqual(
            (call["owner"]["max_turns"], call["owner"]["max_turns_source"]), (50, "override")
        )

    def test_an_owner_with_no_ceilings_leaves_them_as_resolved_now(self):
        with tempfile.TemporaryDirectory() as d:
            call, stored = self.run_plan(d, {})
        self.assertEqual(call["max_turns"], grant_for("plan").max_turns - 3)
        self.assertEqual(
            (stored[0]["kind"], stored[0]["max_turns_source"], stored[0]["model_source"]),
            ("config", "default", ""),
        )


class AWorkspacesListsReachTheImplGrant(unittest.TestCase):
    """`allow` and `block` from `cos.db` are in `Facts.commands` and the gate of an `impl`; the
    protected paths are refused at every stage that runs commands."""

    def run_stage(self, d, stage, stored):
        from coscc.config import Config
        from coscc.store.db import Data
        from coscc.kernel import Block

        seen = {}

        class Probe:
            config = Config(data_dir=str(Path(d) / "data"), config_home=str(Path(d) / "cfg"))

            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                gate = kw["can_use_tool"]
                for line in ("curl -s x", "rm x", f"cat {Path(d) / 'data' / 'vault'}/a.age"):
                    said = await gate("Bash", {"command": line}, None)
                    seen[line] = getattr(said, "message", "")
                (directory / f"{stage}.md").write_text("# X\nStatus: accepted.\n", encoding="utf-8")
                await _submits(kw)
                yield ("done", {"session_id": "s", "cost": {}})

        def render(facts: Facts) -> str:
            seen["commands"] = facts.commands
            return ""

        Data(Probe.config.data_dir).set_pref(policy.GRANTS_PREF, stored)
        directory = make_unit(
            Path(d), intent_md="Status: accepted.\nI", plan_md="Status: accepted.\nP"
        )
        hooks = Hooks(parts=(("probe", Parts(blocks=(Block("probe", render),))),))
        r = Runner(sessions=Probe(), journal=None, hooks=hooks)

        async def go():
            async for _ in r.run(
                workspace=d,
                directory=directory,
                journal_key=d,
                unit=UNIT,
                stage=stage,
                artifact=f"{stage}.md",
                stages=STAGES,
                mode="autonomous",
            ):
                pass

        asyncio.run(go())
        return seen

    def test_impl_runs_with_the_workspaces_lists_and_block_wins(self):
        with tempfile.TemporaryDirectory() as d:
            seen = self.run_stage(d, "impl", {d: {"allow": ["curl", "rm"], "block": ["rm", "git"]}})
        self.assertIn("curl", seen["commands"])
        self.assertNotIn("rm", seen["commands"])
        self.assertNotIn("git", seen["commands"])
        self.assertEqual(seen["curl -s x"], "")
        self.assertIn("may not run 'rm'", seen["rm x"])

    def test_the_vault_is_refused_to_a_command_even_when_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            seen = self.run_stage(d, "impl", {d: {"allow": ["cat"], "block": []}})
        refused = [v for k, v in seen.items() if k.startswith("cat ")]
        self.assertIn("the app's secrets", refused[0])

    def test_another_workspaces_lists_change_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            seen = self.run_stage(d, "impl", {"/elsewhere": {"allow": ["curl"], "block": []}})
        self.assertEqual(seen["commands"], grant_for("impl").commands)


class ABoardStepWithToolsRunsOnClaudeCodesPrompt(unittest.TestCase):
    """A step holding any tool is handed Claude Code's preset system prompt; a step holding
    none is handed nothing, exactly as before. The preset must not widen the grant: the
    callback a preset step receives still refuses what its stage may not do."""

    PRESET = {"type": "preset", "preset": "claude_code"}

    class Probe:
        """Records what `stream` was given, then answers the way each stage needs."""

        def __init__(self):
            self.kw = None

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            self.kw = kw
            stage, directory = self.stage, self.directory
            if grant_for(stage).app_writes_artifact:
                title = stage.capitalize()
                body = f"# {title}: x\nStatus: accepted.\n"
                if stage == "review":
                    body = "# Review: x\nStatus: accepted.\n\n## Round 1\n"
                yield ("chunk", body)
            else:
                (directory / f"{stage}.md").write_text(
                    f"# {stage}: x\nStatus: accepted.\n", encoding="utf-8"
                )
                yield ("chunk", "done")
            await _submits(kw)
            yield ("done", {"session_id": f"s-{stage}", "cost": {}})

    def run_stage(self, d, stage, journal=None):
        probe = self.Probe()
        directory = make_unit(Path(d), intent_md="Status: accepted.\nI")
        probe.stage, probe.directory = stage, directory
        r = Runner(sessions=probe, journal=journal)

        async def go():
            return [
                ev
                async for ev in r.run(
                    workspace=d,
                    directory=directory,
                    journal_key=d,
                    unit=UNIT,
                    stage=stage,
                    artifact=f"{stage}.md",
                    stages=STAGES,
                    mode="manual",
                )
            ]

        _, final = asyncio.run(go())[-1]
        return probe, final

    def test_every_stage_with_tools_gets_the_preset(self):
        with_tools = [s for s in STAGES if grant_for(s).opens_anything]
        # Pinned, so a change to the grant table turns this red rather than quietly
        # leaving a stage out of what it checks.
        self.assertEqual(with_tools, ["spec", "spike", "plan", "impl", "review"])
        for stage in with_tools:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as d:
                probe, _ = self.run_stage(d, stage)
                # Asserted on what the session was handed, before the runner looks at
                # the artifact, so the outcome is not what this depends on.
                self.assertEqual(probe.kw.get("system_prompt"), self.PRESET)
                self.assertNotIn("append", probe.kw["system_prompt"])

    def test_a_stage_without_tools_gets_none(self):
        for stage in ("idea", "intent"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as d:
                probe, _ = self.run_stage(d, stage)
                self.assertIsNotNone(probe.kw)
                self.assertNotIn("system_prompt", probe.kw)

    def test_the_read_only_stage_is_still_refused_writes_and_commands(self):
        import claude_agent_sdk as sdk

        with tempfile.TemporaryDirectory() as d:
            probe, _ = self.run_stage(d, "spec")
            self.assertEqual(probe.kw.get("system_prompt"), self.PRESET)
            # The very callback the preset session was given, not one rebuilt from `decide`.
            gate = probe.kw["can_use_tool"]
            inside = str(Path(d) / "a.txt")
            for tool, data in (
                ("Write", {"file_path": inside, "content": "x"}),
                ("Bash", {"command": "ls"}),
            ):
                verdict = asyncio.run(gate(tool, data, None))
                self.assertIsInstance(verdict, sdk.PermissionResultDeny, tool)

    def test_the_start_record_says_which_prompt_ran(self):
        with tempfile.TemporaryDirectory() as d:
            journal = Journal(d, d)
            self.run_stage(d, "impl", journal)
            self.run_stage(d, "idea", journal)
            starts = journal.records(d, kind="start")
            by_stage = {s["stage"]: s for s in starts}
            self.assertEqual(by_stage["impl"]["system_prompt"], "claude_code")
            self.assertEqual(by_stage["idea"]["system_prompt"], "")


class TheStepKnowsWhichAgentItIs(unittest.TestCase):
    """At the runner: the prompt, `start`, `end` and `settings` all say which agent the step is."""

    class Probe(ABoardStepWithToolsRunsOnClaudeCodesPrompt.Probe):
        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            self.text = text
            self.kw = kw
            stage, directory = self.stage, self.directory
            body = f"# {stage.capitalize()}: x\nIntent: intent.md. Author: {self.author}. Status: accepted.\n"
            if stage == "review":
                body = f"# Review: x\nPR: pr.md. Author: {self.author}. Status: accepted.\n\n## Round 1\n"
            if grant_for(stage).app_writes_artifact:
                yield ("chunk", body)
            else:
                (directory / f"{stage}.md").write_text(body, encoding="utf-8")
                yield ("chunk", "done")
            await _submits(kw)
            yield ("done", {"session_id": f"s-{stage}", "cost": {}})

    def run_stage(self, d, stage, journal, agent, author="Someone"):
        probe = self.Probe()
        directory = make_unit(Path(d), intent_md="Status: accepted.\nI")
        probe.stage, probe.directory, probe.author = stage, directory, author
        r = Runner(sessions=probe, journal=journal)

        async def go():
            return [
                ev
                async for ev in r.run(
                    workspace=d,
                    directory=directory,
                    journal_key=d,
                    unit=UNIT,
                    stage=stage,
                    artifact=f"{stage}.md",
                    stages=STAGES,
                    mode="manual",
                    agent=agent,
                )
            ]

        asyncio.run(go())
        return probe

    def test_start_carries_the_agent_and_end_the_author(self):
        from coscc.agent import agents

        with tempfile.TemporaryDirectory() as d:
            journal = Journal(d, d)
            probe = self.run_stage(
                d, "impl", journal, agents.agent_for("impl"), author="Uruz (agent, impl)"
            )
            self.assertTrue(probe.text.startswith("# Who you are\n"))
            [start] = journal.records(d, kind="start")
            [end] = journal.records(d, kind="end")
            self.assertEqual(start["agent"], "Uruz")
            self.assertEqual((end["outcome"], end["author"]), ("done", "Uruz (agent, impl)"))
            # A name off the table is written as it is, and the step is not refused.
            self.run_stage(d, "plan", journal, agents.agent_for("plan"), author="Someone Else")
            end = journal.records(d, kind="end")[-1]
            self.assertEqual((end["outcome"], end["author"]), ("done", "Someone Else"))
            # With no row: no `agent`, and not one byte more in the prompt.
            probe = self.run_stage(d, "spec", journal, None)
            self.assertNotIn("agent", journal.records(d, kind="start")[-1])
            self.assertTrue(probe.text.startswith("# The rules for this stage"))

    def test_a_preset_step_gets_the_attribution_settings_and_a_toolless_one_does_not(self):
        from coscc.agent import agents

        with tempfile.TemporaryDirectory() as d:
            probe = self.run_stage(d, "impl", None, agents.agent_for("impl"))
            self.assertEqual(
                json.loads(probe.kw["settings"]),
                {
                    "attribution": {
                        "commit": "Co-authored-by: Uruz (agent, impl) <uruz@agents.coscc.invalid>",
                        "pr": "Uruz (agent, impl)",
                    }
                },
            )
        with tempfile.TemporaryDirectory() as d:
            probe = self.run_stage(d, "intent", None, agents.agent_for("intent"))
            self.assertNotIn("settings", probe.kw)
            self.assertNotIn("system_prompt", probe.kw)
            self.assertTrue(probe.text.startswith("# Who you are\n"))
        with tempfile.TemporaryDirectory() as d:
            self.assertNotIn("settings", self.run_stage(d, "impl", None, None).kw)


def _git_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "a.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=T",
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "add",
            "-A",
        ],
        cwd=repo,
        check=True,
    )
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
            "-m",
            "first",
        ],
        cwd=repo,
        check=True,
    )
    return repo


SPIKE_REPLY = (
    "# Spike: x\nSpec: spec.md. Author: ᛈ Perthro. Round: 1. Status: accepted.\n\n"
    "## U1\n\nVerdict: holds.\n\n```\n$ python -c 'print(1)'\n1\n```\n"
)


class ThePlanAndTheSpecReadTheSpike(unittest.TestCase):
    """The prompts that need a second artifact get it, and `included` says so."""

    def unit(self, d: str, **files: str) -> Path:
        return make_unit(
            Path(d), intent_md="Status: accepted.\nI", spec_md="Status: accepted.\nSPEC", **files
        )

    def test_plan_reads_the_spike_and_the_spec(self):
        with tempfile.TemporaryDirectory() as d:
            directory = self.unit(d, spike_md="Status: accepted.\nSPIKE")
            prompt, included = build_prompt(d, directory, UNIT, "plan", STAGES, "plan.md")
            self.assertEqual(included, ["intent.md", "spike.md", "spec.md"])
            self.assertIn("SPIKE", prompt)
            self.assertIn("SPEC", prompt)

    def test_plan_without_a_spike_is_what_it_was(self):
        with tempfile.TemporaryDirectory() as d:
            directory = self.unit(d)
            _, included = build_prompt(d, directory, UNIT, "plan", STAGES, "plan.md")
            self.assertEqual(included, ["intent.md", "spec.md"])

    def test_a_spec_rerun_reads_the_spike(self):
        with tempfile.TemporaryDirectory() as d:
            directory = self.unit(d, spike_md="Status: accepted.\nU2 FAILS")
            prompt, included = build_prompt(d, directory, UNIT, "spec", STAGES, "spec.md")
            self.assertIn("spike.md", included)
            self.assertIn("U2 FAILS", prompt)
            self.assertIn("# What the spike measured", prompt)

    def test_the_spike_prompt_builds_names_its_directories_and_the_last_spike(self):
        with tempfile.TemporaryDirectory() as d:
            directory = self.unit(d, spike_md="Status: accepted.\nROUND-ONE")
            prompt, included = build_prompt(
                d, directory, UNIT, "spike", STAGES, "spike.md", worktree="/the/tree"
            )
            self.assertEqual(included, ["intent.md", "spec.md", "spike.md"])
            self.assertIn("# The previous spike\n\nStatus: accepted.\nROUND-ONE", prompt)
            self.assertIn("# Where you work", prompt)
            self.assertIn("`/the/tree`", prompt)
            self.assertIn("Reply with the file's complete contents", prompt)

    def test_the_spike_prompt_names_its_progress_file_and_its_ceilings(self):
        # The numbers are the grant's, not a second copy.
        g = grant_for("spike")
        with tempfile.TemporaryDirectory() as d:
            directory = self.unit(d)
            prompt, _ = build_prompt(
                d,
                directory,
                UNIT,
                "spike",
                STAGES,
                "spike.md",
                worktree="/the/tree",
                ceilings=(g.max_turns, g.max_budget_usd),
            )
            self.assertIn(f"`{Path(d).resolve() / 'spike.md'}`", prompt)
            self.assertIn(f"{g.max_turns} turns", prompt)
            self.assertIn(f"${g.max_budget_usd:.2f}", prompt)
            self.assertIn("the app writes `spike.md` from this file", prompt)

    def test_no_other_stage_prompt_changes_by_a_byte(self):
        with tempfile.TemporaryDirectory() as d:
            directory = self.unit(d, spike_md="Status: accepted.\nR")
            for stage in SESSION_STAGES:
                if stage == "spike":
                    continue
                artifact = f"{stage}.md"
                self.assertEqual(
                    build_prompt(d, directory, UNIT, stage, STAGES, artifact, ceilings=(80, 8.0)),
                    build_prompt(d, directory, UNIT, stage, STAGES, artifact),
                    stage,
                )

    def test_the_step_hands_the_grants_ceilings_to_the_prompt(self):
        g = grant_for("spike")
        seen = []

        class Fake:
            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                seen.append((text, max_turns, kw.get("max_budget_usd")))
                yield ("chunk", SPIKE_REPLY)
                await _submits(kw)
                yield ("done", {"session_id": "s", "cost": {}})

        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as scratch:
            directory = self.unit(d)
            r = Runner(sessions=Fake(), journal=None)

            async def go():
                return [
                    ev
                    async for ev in r.run(
                        workspace=d,
                        directory=directory,
                        journal_key=d,
                        unit=UNIT,
                        stage="spike",
                        artifact="spike.md",
                        stages=STAGES,
                        mode="autonomous",
                        cwd=scratch,
                    )
                ]

            asyncio.run(go())
        [(text, turns, budget)] = seen
        self.assertEqual((turns, budget), (g.max_turns, g.max_budget_usd))
        self.assertIn(f"This step has {g.max_turns} turns and ${g.max_budget_usd:.2f}.", text)

    def test_the_spike_skill_carries_the_progress_file(self):
        # Red too when a stale `coscc/_harness/` hides `.claude/`.
        self.assertIn("## The progress file", skill_for("spike"))


class RecordingChangesNothing(unittest.TestCase):
    """The same step with a recorder and without one: the same outcome, the same artifact, the same
    `start` and `end` but for `run` and `events_lost`. A recorder that raises everywhere changes
    none of that, and the `end` says events were lost."""

    class Replies:
        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            yield ("chunk", "# Spec: x\nStatus: accepted.\n")
            await _submits(kw)
            yield (
                "done",
                {
                    "session_id": "s-1",
                    "terminal_reason": "success",
                    "cost": {"turns": 2, "cost_usd": 0.25},
                },
            )

    class Raises:
        run = "r-broken"
        seq = 4

        def __getattr__(self, name):
            def boom(*a, **k):
                raise RuntimeError(name)

            return boom

    IGNORED = ("at", "run", "events_lost", "session_id", "pid")

    def _once(self, make_recorder):
        """`make_recorder(data)` gives the step's recorder; `None` runs it with no row at all."""
        from coscc.agent import steps
        from coscc.store.db import Data

        with tempfile.TemporaryDirectory() as d:
            directory = make_unit(Path(d), intent_md="Status: accepted.\nI")
            journal = Journal(d, d)
            running = None
            if make_recorder is not None:
                running = steps.Running(d, UNIT, "spec", "")
                running.handle.recorder = make_recorder(Data(d))

            async def go():
                return [
                    i
                    async for i in Runner(sessions=self.Replies(), journal=journal).run(
                        workspace=d,
                        directory=directory,
                        journal_key=d,
                        unit=UNIT,
                        stage="spec",
                        artifact="spec.md",
                        stages=STAGES,
                        mode="manual",
                        running=running,
                    )
                ]

            out = asyncio.run(go())
            raw = [r for r in journal.records() if r["kind"] in ("start", "end")]
            # Each run has its own temporary directory, and the records name it.
            same = [
                {
                    k: (v.replace(d, "<d>") if isinstance(v, str) else v)
                    for k, v in r.items()
                    if k not in self.IGNORED
                }
                for r in raw
            ]
            return out[-1][1]["outcome"], (directory / "spec.md").read_bytes(), same, raw

    def test_with_and_without_a_recorder_the_step_is_the_same(self):
        from coscc.runlog import events

        plain = self._once(None)
        recorded = self._once(
            lambda data: events.Recorder("r-1", data, "/w", "/w/ws", UNIT, "spec")
        )
        self.assertEqual(plain[:3], recorded[:3])
        start, end = recorded[3]
        self.assertEqual((start["run"], end["run"], end["events_lost"]), ("r-1", "r-1", 0))
        self.assertNotIn("run", plain[3][0])

    def test_a_recorder_that_raises_everywhere_changes_nothing_but_events_lost(self):
        plain = self._once(None)
        broken = self._once(lambda data: self.Raises())
        self.assertEqual(plain[:3], broken[:3])
        self.assertGreater(broken[3][1]["events_lost"], 0)

    def test_every_refusal_reaches_the_listener_and_the_record_keeps_five(self):
        from coscc.runner.attempt import Denials

        heard = []
        denials = Denials()
        denials.listener = lambda tool, tool_input, reason: heard.append((tool, tool_input, reason))
        for i in range(7):
            denials.record("Bash", "no", {"command": str(i)})
        self.assertEqual(len(heard), 7)
        self.assertEqual(heard[6], ("Bash", {"command": "6"}, "no"))
        self.assertEqual((denials.count, len(denials.reasons)), (7, Denials.KEEP))


# One unit holding every artifact, each prose one carrying `## Answers`, and a review sent back with
# two rounds: the fixture the prompts below are built from.
_ANSWERED = (
    "Author: t. Status: accepted.\n\n## Open questions\n\n1. Q-{name}?\n\n"
    "## Answers\n\n### Câu 1\nAnswered by: P. Date: 2026-09-25. Via: product.\n\nA-{name}\n"
)
_REVIEW_TWO_ROUNDS = (
    "# Review: x\nPR: pr.md. Concluded by: agent. Status: changes-requested.\n\n"
    "## Round 1\n\nReviewed: abc1234. Verdict: changes-requested.\n\n### Findings\n\n"
    "- F1 [open] a.py:1 — high — ROUND-ONE-F1\n"
    "- F2 [open] b.py:2 — low — ROUND-ONE-F2\n\n"
    "## Round 2\n\nReviewed: def5678. Verdict: changes-requested.\n\n### Findings\n\n"
    "- F1 [fixed 1111111] a.py:1 — high — ROUND-TWO-F1-FIXED\n"
    "- F2 [answered] b.py:2 — low — ROUND-TWO-F2-ANSWERED\n"
    "- F3 [open] c.py:3 — high — ROUND-TWO-F3-OPEN\n"
    "  CONTINUATION-OF-F3\n"
    "- F4 [needs-person] d.py:4 — medium — ROUND-TWO-F4-PERSON\n"
)


def _golden_unit(root: Path) -> Path:
    files = {
        f"{s}_md": _ANSWERED.format(name=s.upper()) for s in ("idea", "intent", "spec", "plan")
    }
    files.update(
        spike_md="# Spike: x\nSpec: spec.md. Status: accepted. Round: 1.\n\nSPIKE-BODY\n",
        impl_md="# Impl: x\nStatus: accepted.\n\nIMPL-BODY\n",
        pr_md="# PR: x\nPR: https://github.com/o/r/pull/9. Status: accepted.\n\nPR-BODY\n",
        review_md=_REVIEW_TWO_ROUNDS,
        ship_md="# Ship: x\nStatus: draft.\n\nSHIP-BODY\n",
    )
    return make_unit(root, **files)


class TheStartRecordSaysWhatRanAndWhatWasNamed(unittest.TestCase):
    class Probe:
        async def stream(self, cwd, text, session_id=None, max_turns=1, **_):
            yield ("chunk", "x")
            await _submits(_)
            yield ("done", {"session_id": "s", "cost": {}})

    def start(self, d, stage, app):
        directory = _golden_unit(Path(d))
        journal = Journal(d, d)
        r = Runner(self.Probe(), journal, app=app)

        async def go():
            async for _ in r.run(
                workspace=d,
                directory=directory,
                journal_key=d,
                unit=UNIT,
                stage=stage,
                artifact=f"{stage}.md",
                stages=STAGES,
                mode="manual",
            ):
                pass

        asyncio.run(go())
        return journal.records(d, kind="start")[-1]

    def test_the_build_and_the_pointed_files(self):
        with tempfile.TemporaryDirectory() as d:
            start = self.start(d, "review", {"version": "0.12.0", "commit": "c" * 40})
            self.assertEqual((start["app_version"], start["app_commit"]), ("0.12.0", "c" * 40))
            self.assertIn("plan.md", start["pointed"])
            self.assertIn("impl.md", start["included"])
            self.assertIn("review-findings", start["included"])
            self.assertNotIn("review.md", start["included"])

    def test_a_runner_given_no_build_writes_neither_field(self):
        with tempfile.TemporaryDirectory() as d:
            start = self.start(d, "spec", None)
            self.assertNotIn("app_version", start)
            self.assertNotIn("app_commit", start)
            self.assertEqual(start["pointed"], [])


# --- a review that runs out of turns --------------------------------------------

REVIEW_R1 = (
    "# Review: x\nSpec: spec.md. Author: t. Status: changes-requested.\n\n"
    "## Round 1\n\nReviewed: " + "a" * 40 + ". Verdict: changes-requested.\n\n"
    "### Findings\n\n- F1 [open] a.py:3 — high — x\n"
)


def incomplete_reply(
    head: str,
    number: int = 2,
    verdict: str = "incomplete",
    status: str = "draft",
    sections=("Reviewed so far", "Findings", "What was not reviewed"),
) -> str:
    body = "".join(f"### {s}\n\n- {s.lower()}\n\n" for s in sections)
    return (
        f"# Review: x\nSpec: spec.md. Author: t. Status: {status}.\n\n"
        f"## Round {number}\n\nReviewed: {head}. Verdict: {verdict}.\n\n{body}"
    )


class AStepRecordsTheModelTrial(AStepRecordsTheBaseItRanOn):
    """The record handed in lands in `start`, none is no field, and its `model` is what the
    session's `init` named, or `never-started`."""

    TRIAL = {modeltrial.FIELD: {"arm": modeltrial.OPUS_ARM, "requested": "claude-opus-5-5[1m]"}}

    class Initialised:
        async def stream(self, cwd, text, session_id=None, max_turns=1, step=None, **kw):
            step.init_model = "claude-opus-5-5[1m]"
            yield ("session", "s-init")
            yield ("chunk", "# Spec: x\nStatus: accepted.\n")
            await _submits(kw)
            yield ("done", {"session_id": "s-init", "cost": {}})

    def test_no_trial_record_leaves_the_start_record_as_it_was(self):
        with tempfile.TemporaryDirectory() as d:
            unnamed = self._start_record(d)
        with tempfile.TemporaryDirectory() as d:
            none = self._start_record(d, trial_record=None)
        self.assertEqual(set(unnamed), set(none))
        for field in (modeltrial.FIELD, modeltrial.CI_RED):
            self.assertNotIn(field, unnamed)

    def test_a_session_that_never_named_its_model_is_recorded_never_started(self):
        record = {**self.TRIAL, modeltrial.CI_RED: None}
        with tempfile.TemporaryDirectory() as d:
            start = self._start_record(d, trial_record=record)
        self.assertEqual(
            start[modeltrial.FIELD], {**self.TRIAL[modeltrial.FIELD], "model": "never-started"}
        )
        self.assertIn(modeltrial.CI_RED, start)
        self.assertIsNone(start[modeltrial.CI_RED])

    def test_the_model_init_named_reaches_the_start(self):
        from coscc.agent import steps as steps_mod

        with tempfile.TemporaryDirectory() as d:
            make_unit(Path(d), intent_md="Status: accepted.\nI")
            journal = Journal(d, d)
            r = Runner(sessions=self.Initialised(), journal=journal)
            running = steps_mod.Running(d, UNIT, "spec", "")

            async def go():
                async for _ in r.run(
                    workspace=d,
                    directory=Path(d) / ".cos" / UNIT,
                    journal_key=d,
                    unit=UNIT,
                    stage="spec",
                    artifact="spec.md",
                    stages=STAGES,
                    mode="manual",
                    trial_record=self.TRIAL,
                    running=running,
                ):
                    pass

            asyncio.run(go())
            [start] = journal.records(d, kind="start")
        self.assertEqual(start[modeltrial.FIELD]["model"], "claude-opus-5-5[1m]")


class AReviewAfterAnUnfinishedRoundIsHandedIt(unittest.TestCase):
    """`Runner.run` passes `unfinished_round` to the prompt and nowhere else."""

    class Replies:
        def __init__(self):
            self.prompts: list[str] = []

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            self.prompts.append(text)
            yield ("chunk", "# Review: x\nStatus: changes-requested.\n")
            await _submits(kw)
            yield ("done", {"session_id": "s-review", "cost": {}})

    def _run(self, d: str, **run_kw) -> tuple[str, dict]:
        make_unit(Path(d), intent_md="Status: accepted.\nI", review_md=REVIEW_R1)
        journal = Journal(d, d)
        replies = self.Replies()
        r = Runner(sessions=replies, journal=journal)

        async def go():
            async for _ in r.run(
                workspace=d,
                directory=Path(d) / ".cos" / UNIT,
                journal_key=d,
                unit=UNIT,
                stage="review",
                artifact="review.md",
                stages=STAGES,
                mode="manual",
                **run_kw,
            ):
                pass

        asyncio.run(go())
        [start] = journal.records(d, kind="start")
        return replies.prompts[0], start

    def test_the_round_handed_in_reaches_the_prompt(self):
        with tempfile.TemporaryDirectory() as d:
            plain, plain_start = self._run(d)
        with tempfile.TemporaryDirectory() as d:
            told, start = self._run(d, unfinished_round={"n": 1, "dropped": ["F9"]})
        self.assertNotIn("# The round that did not count", plain)
        self.assertIn("Round 1 asked for changes but does not list `F9`", told)
        self.assertIn("review-unfinished", start["included"])
        self.assertNotIn("review-unfinished", plain_start["included"])
        self.assertEqual(set(start), set(plain_start))


class TheScreenshotsTakenAgain(unittest.TestCase):
    """`service.steps.run_step` builds the section after a retake; this module places it for `review`
    only, after the integration's, and every other prompt is what it was."""

    NOTE = "# The screenshots, taken again\n\nSCREENS-MARKER"

    def test_review_carries_it_after_the_integration_and_says_so(self):
        with tempfile.TemporaryDirectory() as d:
            directory = _golden_unit(Path(d))
            prompt, included, _ = compose_prompt(
                d,
                directory,
                UNIT,
                "review",
                STAGES,
                "review.md",
                integration_note="# INTEGRATION\n\nINTEGRATION-NOTE",
                screens_note=self.NOTE,
            )
        self.assertEqual(prompt.count("SCREENS-MARKER"), 1)
        self.assertLess(prompt.index("INTEGRATION-NOTE"), prompt.index("SCREENS-MARKER"))
        self.assertLess(prompt.index("SCREENS-MARKER"), prompt.index("# Your task"))
        self.assertIn("screens", included)

    def test_no_other_stage_carries_it_and_none_handed_is_no_byte(self):
        with tempfile.TemporaryDirectory() as d:
            directory = _golden_unit(Path(d))
            for stage in SESSION_STAGES:
                with self.subTest(stage=stage):
                    args = (d, directory, UNIT, stage, STAGES, f"{stage}.md")
                    handed = compose_prompt(*args, screens_note=self.NOTE)
                    if stage == "review":
                        self.assertEqual(
                            compose_prompt(*args, screens_note=""), compose_prompt(*args)
                        )
                    else:
                        self.assertEqual(handed, compose_prompt(*args))

    def test_runner_run_hands_it_to_the_prompt(self):
        seen = []

        class Replies:
            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                seen.append(text)
                yield (
                    "chunk",
                    "# Review: x\nStatus: changes-requested.\n\n## Round 1\n\nReviewed: abc1234. Verdict: changes-requested.\n",
                )
                await _submits(kw)
                yield ("done", {"session_id": "s-review", "cost": {}})

        with tempfile.TemporaryDirectory() as d:
            make_unit(Path(d), intent_md="Status: accepted.\nI")
            journal = Journal(d, d)

            async def go():
                async for _ in Runner(sessions=Replies(), journal=journal).run(
                    workspace=d,
                    directory=Path(d) / ".cos" / UNIT,
                    journal_key=d,
                    unit=UNIT,
                    stage="review",
                    artifact="review.md",
                    stages=STAGES,
                    mode="manual",
                    screens_note=self.NOTE,
                ):
                    pass

            asyncio.run(go())
        self.assertIn("SCREENS-MARKER", seen[0])


class TheCommandsAStepMayRun(unittest.TestCase):
    """`COMMANDS_ADVICE` is prose about `policy.check_command`: each thing it says is refused is
    refused, and what it says may run does, on `impl`'s own grant."""

    def test_what_the_advice_says_is_refused_is_refused(self):
        grant = policy.grant_for_step("impl", None)
        for line in (
            "cd x",
            "timeout 5 npm test",
            "git status; cd x",
            "echo a > f.txt",
            "echo $(pwd)",
            "echo `pwd`",
            "diff <(ls) f",
        ):
            with self.subTest(line=line):
                self.assertNotEqual(policy.check_command(grant, line), "")

    def test_what_it_says_may_run_runs(self):
        grant = policy.grant_for_step("impl", None)
        for line in ("echo a > /dev/null", "git status && npm test", "ls | wc -l"):
            with self.subTest(line=line):
                self.assertEqual(policy.check_command(grant, line), "")

    def test_a_step_makes_its_scratch_and_stops_on_one_it_may_not_use(self):
        import os
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as d:
            base = Path(d).resolve()
            (base / "tmp").mkdir()
            fake = SimpleNamespace(sessions=SimpleNamespace(config=SimpleNamespace(data_dir=base)))
            with mock.patch.object(tempfile, "tempdir", str(base / "tmp")):
                ram, disk = Runner._scratch(fake, str(base / "repo"), UNIT)  # type: ignore[arg-type]
                self.assertTrue(ram.is_dir() and disk.is_dir())
                os.chmod(ram.parent.parent, 0o755)
                with self.assertRaisesRegex(RunError, "did not start.*0755"):
                    Runner._scratch(fake, str(base / "repo"), UNIT)  # type: ignore[arg-type]

    def test_what_it_says_may_go_to_scratch_does(self):
        grant = policy.grant_for_step("impl", None)
        with tempfile.TemporaryDirectory() as ram, tempfile.TemporaryDirectory() as disk:
            for line in ("echo a > $COS_SCRATCH_RAM/f", "npm test > $COS_SCRATCH_DISK/log 2>&1"):
                with self.subTest(line=line):
                    said = policy.check_command(grant, line, None, (ram, disk), 2**20)
                    self.assertEqual(said, "")

    def test_the_words_an_impl_step_gets_are_its_grant(self):
        from coscc.runner.prompt import COMMANDS_HEADING

        steps = AStepCarriesItsGrantAndNothingOfTheMachine()
        words = ", ".join(f"`{c}`" for c in policy.grant_for_step("impl", None).commands)
        for stage in ("impl", "plan"):
            replies = steps.Replies()
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as d:
                make_unit(Path(d), intent_md="Status: accepted.\nI", plan_md="Status: accepted.\nP")
                steps._run(d, replies, stage=stage)
                if stage == "impl":
                    self.assertIn(f"{COMMANDS_HEADING}\n\n{words}\n\n", replies.prompt)
                else:
                    self.assertNotIn(COMMANDS_HEADING, replies.prompt)

    def test_write_impl_tells_a_step_to_batch_its_reads(self):
        prompt = compose_prompt("/w", "/nowhere", UNIT, "impl", STAGES, "impl.md")[0]
        self.assertIn("## Reading the tree", prompt)
        self.assertIn("in one turn", prompt)


class TheLastTurnEndsTheSession(unittest.TestCase):
    """Every step whose grant holds `Bash` is told its session ends with its turn."""

    def prompt_of(self, stage):
        from coscc.runner.prompt import SESSION_ENDS_HEADING

        steps = AStepCarriesItsGrantAndNothingOfTheMachine()
        replies = steps.Replies()
        with tempfile.TemporaryDirectory() as d:
            make_unit(Path(d), intent_md="Status: accepted.\nI", plan_md="Status: accepted.\nP")
            steps._run(d, replies, stage=stage)
        self.assertIn("# The rules for this stage", replies.prompt)
        return replies.prompt.count(SESSION_ENDS_HEADING)

    def test_every_stage_that_runs_commands_is_told_once(self):
        for stage in ("impl", "spike"):
            with self.subTest(stage=stage):
                self.assertIn("Bash", policy.grant_for(stage).tools)
                self.assertEqual(self.prompt_of(stage), 1)

    def test_no_prose_stage_is_told(self):
        for stage in ("idea", "intent", "spec", "plan", "review"):
            with self.subTest(stage=stage):
                self.assertEqual(self.prompt_of(stage), 0)


class AStepThatRunsCommandsIsToldWhereTheHarnessIs(unittest.TestCase):
    """A step with `Bash` is handed the command that runs this app's loop and its unit's `--root`,
    so it never goes looking for them (a `find /` once held a step seven minutes)."""

    def test_the_path_and_root_are_in_the_prompt_of_every_stage_with_bash(self):
        from coscc.runner.prompt import HARNESS_HEADING

        root = Path("/store")
        for stage in ("impl", "spike"):
            with self.subTest(stage=stage):
                prompt = compose_prompt(
                    "/w",
                    root / ".cos" / UNIT,
                    UNIT,
                    stage,
                    STAGES,
                    f"{stage}.md",
                    runs_commands=True,
                )[0]
                self.assertEqual(prompt.count(HARNESS_HEADING), 1)
                self.assertIn(
                    f"`{sys.executable} -P -m coscc.loop <command> --root {root}`", prompt
                )

    def test_the_snapshot_file_is_named_for_the_deciding_commands(self):
        """`gate`, `pr-text` and the rest exit 2 without `--state`; the step is told its file."""
        prompt = compose_prompt(
            "/w",
            Path("/store") / ".cos" / UNIT,
            UNIT,
            "impl",
            STAGES,
            "impl.md",
            runs_commands=True,
            state_file="/data/state/w/x.json",
        )[0]
        self.assertIn("also take `--state /data/state/w/x.json`", prompt)
        without = compose_prompt(
            "/w",
            Path("/store") / ".cos" / UNIT,
            UNIT,
            "impl",
            STAGES,
            "impl.md",
            runs_commands=True,
        )[0]
        self.assertNotIn("--state", without)

    def test_a_prose_stage_is_not_told(self):
        from coscc.runner.prompt import HARNESS_HEADING

        prompt = compose_prompt("/w", "/store/.cos/" + UNIT, UNIT, "spec", STAGES, "spec.md")[0]
        self.assertNotIn(HARNESS_HEADING, prompt)


class AStepRecordsThePlanMapItCarried(AStepRecordsTheBaseItRanOn):
    """The record handed in is the record written, and none is no field."""

    def test_no_record_handed_in_leaves_no_field(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertNotIn("plan_map", self._start_record(d))

    def test_the_record_handed_in_is_the_record_written(self):
        record = {"bytes": 0, "files": 0, "full": 0, "short": 0, "new": 0, "outside": 0}
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self._start_record(d, plan_map_record=record)["plan_map"], record)


class AStepAnUpdatePaused(unittest.TestCase):
    """At `Runner.run`: a step `suspend_all` paused writes no `end`, and one taken up again goes
    on in its own session and ends once."""

    PLAN = "# Plan: a problem\nIntent: intent.md. Status: accepted.\n\n## Files that change\n\n- a.py\n"

    class Paused:
        """A session that says something and is then paused by an update."""

        def __init__(self):
            self.calls = []

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            from coscc.agent.sessions import Suspended

            self.calls.append(
                {"text": text, "session_id": session_id, "max_turns": max_turns, **kw}
            )
            yield ("chunk", "Reading the plan.")
            raise Suspended("session s-1 was paused for an update")

    class GoesOn:
        """The same session taken up again: it says the rest and ends."""

        def __init__(self, rest="## Order of work\n\n1. a\n", cost=1.2):
            self.calls, self.rest, self.cost = [], rest, cost

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            self.calls.append(
                {"text": text, "session_id": session_id, "max_turns": max_turns, **kw}
            )
            yield ("chunk", self.rest)
            await _submits(kw)
            yield (
                "done",
                {
                    "session_id": session_id,
                    "terminal_reason": "completed",
                    "cost": {"turns": 4, "cost_usd": self.cost},
                    "first_call": {
                        "input_tokens": 3,
                        "cache_creation_tokens": 900,
                        "cache_read_tokens": 0,
                    },
                },
            )

    def run_plan(self, d, sessions, resume=None, hooks=None):
        from coscc.agent import steps

        directory = make_unit(
            Path(d), intent_md="Status: accepted.\nI", spec_md="Status: accepted.\nS"
        )
        journal = Journal(d, d)
        running = steps.Running(d, UNIT, "plan", "")

        async def go():
            out = []
            async for item in Runner(sessions, journal, **({"hooks": hooks} if hooks else {})).run(
                workspace=d,
                directory=directory,
                journal_key=d,
                unit=UNIT,
                stage="plan",
                artifact="plan.md",
                stages=STAGES,
                mode="manual",
                running=running,
                **({"resume": resume} if resume is not None else {}),
            ):
                out.append(item)
            return out

        return asyncio.run(go()), journal, directory

    def resume(self, **extra):
        return {
            "suspend_id": "p1",
            "session_id": "s-1",
            "safe_uuid": "u9",
            "message": "MSG",
            "pieces": [],
            "api_calls": 3,
            "spent_usd": 0.5,
            "owner": {"kind": "step", "start_at": "t0", "head": ""},
            **extra,
        }

    def kinds(self, journal):
        return [r["kind"] for r in journal.records() if r["kind"] in ("start", "attempt", "end")]

    def test_a_resumed_step_gets_a_server_made_from_resumed_facts(self):
        made = []
        tool = Tool(
            server="fake",
            names=("ping",),
            stages=("plan",),
            make=lambda facts: made.append(facts) or {"type": "sdk", "name": "fake"},
        )
        hooks = Hooks(parts=(("fake", Parts(tools=(tool,))),))
        with tempfile.TemporaryDirectory() as d:
            sessions = self.GoesOn(rest=self.PLAN)
            self.run_plan(d, sessions, self.resume(), hooks=hooks)
            [call] = sessions.calls
            self.assertEqual(set(call["mcp_servers"]), {"cos", "fake"})
        [facts] = made
        self.assertTrue(facts.resumed)
        self.assertEqual((facts.unit, facts.stage), (UNIT, "plan"))

    def test_a_suspended_step_writes_no_end_and_no_attempt(self):
        from coscc.agent.sessions import Suspended

        with tempfile.TemporaryDirectory() as d:
            sessions = self.Paused()
            with self.assertRaises(Suspended):
                self.run_plan(d, sessions)
            journal = Journal(d, d)
            self.assertEqual(self.kinds(journal), ["start"])
            self.assertFalse((Path(d) / ".cos" / UNIT / "plan.md").exists())
            # What `suspend_all` needs to write the row, handed to the session.
            owner = sessions.calls[0]["owner"]
            self.assertEqual((owner["kind"], owner["unit"], owner["stage"]), ("step", UNIT, "plan"))
            self.assertEqual(owner["start_at"], journal.records(kind="start")[0]["at"])

    def test_a_step_resumes_without_a_second_start_row_and_ends_once(self):
        with tempfile.TemporaryDirectory() as d:
            sessions = self.GoesOn(rest=self.PLAN)
            out, journal, _ = self.run_plan(d, sessions, self.resume())
            self.assertEqual(self.kinds(journal), ["end"])
            self.assertEqual(out[-1][1]["outcome"], "done")
            [call] = sessions.calls
            self.assertEqual(
                (call["text"], call["session_id"], call["resume_at"]), ("MSG", "s-1", "u9")
            )

    def test_remaining_ceilings_are_the_grant_less_what_was_used(self):
        grant = grant_for("plan")
        with tempfile.TemporaryDirectory() as d:
            sessions = self.GoesOn(rest=self.PLAN)
            self.run_plan(d, sessions, self.resume())
            [call] = sessions.calls
            self.assertEqual(call["max_turns"], grant.max_turns - 3)
            self.assertAlmostEqual(call["max_budget_usd"], grant.max_budget_usd - 0.5)
        with tempfile.TemporaryDirectory() as d:
            sessions = self.GoesOn(rest=self.PLAN)
            self.run_plan(d, sessions, self.resume(cost_unknown=True, spent_usd=None))
            # With the cost unknown, the whole budget.
            self.assertEqual(sessions.calls[0]["max_budget_usd"], grant.max_budget_usd)

    def test_a_used_up_ceiling_ends_the_step_exhausted_without_a_session(self):
        grant = grant_for("plan")
        for used, terminal in (
            ({"api_calls": grant.max_turns}, "error_max_turns"),
            ({"spent_usd": grant.max_budget_usd}, "error_max_budget_usd"),
        ):
            with self.subTest(terminal=terminal), tempfile.TemporaryDirectory() as d:
                sessions = self.GoesOn()
                _, journal, _ = self.run_plan(d, sessions, self.resume(**used))
                self.assertEqual(sessions.calls, [])
                [end] = journal.records(kind="end")
                self.assertEqual(end["outcome"], "exhausted")
                self.assertIn(terminal, end["detail"])

    def test_a_resumed_prose_step_writes_its_artifact_from_the_pieces_before_and_after(self):
        with tempfile.TemporaryDirectory() as d:
            sessions = self.GoesOn()
            _, journal, directory = self.run_plan(
                d, sessions, self.resume(pieces=["Reading.", self.PLAN])
            )
            written = (directory / "plan.md").read_text(encoding="utf-8")
            self.assertTrue(written.startswith("# Plan: a problem\n"), written)
            self.assertIn("- a.py", written)
            self.assertIn("## Order of work\n\n1. a", written)
            self.assertNotIn("Reading.", written)

    def test_the_end_row_carries_the_whole_session_cost_and_each_segment(self):
        # The CLI's total carries over a resume, so `cost_usd` is the session's.
        with tempfile.TemporaryDirectory() as d:
            _, journal, _ = self.run_plan(d, self.GoesOn(rest=self.PLAN), self.resume())
            [end] = journal.records(kind="end")
            self.assertEqual(end["cost_usd"], 1.2)
            self.assertEqual(
                end["segments"],
                [
                    {
                        "suspend_id": "p1",
                        "cost_usd": 0.7,
                        "first_call": {
                            "input_tokens": 3,
                            "cache_creation_tokens": 900,
                            "cache_read_tokens": 0,
                        },
                    }
                ],
            )
            self.assertNotIn("cost_partial", end)

    def test_a_segment_without_cost_state_marks_the_end_cost_partial(self):
        with tempfile.TemporaryDirectory() as d:
            _, journal, _ = self.run_plan(
                d, self.GoesOn(rest=self.PLAN), self.resume(spent_usd=None, cost_unknown=True)
            )
            [end] = journal.records(kind="end")
            self.assertTrue(end["cost_partial"])
            # A CLI killed before its `cost-state` leaves the next counting from zero.
            self.assertEqual(end["segments"][0]["cost_usd"], 1.2)
            self.assertTrue(end["segments"][0]["cost_unknown"])


class AFeatureHandsAStepItsOwnTools(unittest.TestCase):
    """A tool a feature declares reaches the session only on the stages it names and only while the
    feature is on, and it never takes `submit`'s place."""

    made: list[Facts]

    def _hooks(self, on=True, stages=("impl",)):
        self.made = []

        def make(facts):
            self.made.append(facts)
            return {"type": "sdk", "name": f"fake-{facts.unit}"}

        tool = Tool(server="fake", names=("ping",), stages=stages, make=make)
        return Hooks(
            parts=(("fake", Parts(tools=(tool,))),),
            enabled=lambda feature, _workspace: on and feature == "fake",
        )

    class Probe:
        def __init__(self):
            self.kw: dict = {}
            self.answers: dict = {}
            self.submitted = None

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            if session_id is not None:
                return
            self.kw = kw
            gate = kw.get("can_use_tool")
            if gate is not None:
                for tool in ("mcp__fake__ping", "mcp__fake__other", "mcp__cos__submit", "Bash"):
                    got = await gate(tool, {"command": "ls"}, None)
                    self.answers[tool] = type(got).__name__ == "PermissionResultAllow"
            yield ("chunk", "# Plan: x\nStatus: accepted.\n")
            self.submitted = await _submits(kw)
            yield ("done", {"session_id": "s", "cost": {}})

    def _run(self, d, hooks, stage, unit=UNIT, journal=None, cwd=None):
        probe = self.Probe()
        directory = make_unit(
            Path(d),
            intent_md="Status: accepted.\nI",
            spec_md="Status: accepted.\nS",
            plan_md="Status: accepted.\nP",
        )
        if stage == "impl":
            (directory / "impl.md").write_text("# Impl\nStatus: accepted.\n", encoding="utf-8")
        r = Runner(sessions=probe, journal=journal, hooks=hooks)

        async def go():
            return [
                ev
                async for ev in r.run(
                    workspace=d,
                    directory=directory,
                    journal_key=d,
                    unit=unit,
                    stage=stage,
                    artifact=f"{stage}.md",
                    stages=STAGES,
                    mode="autonomous",
                    **({"cwd": cwd} if cwd else {}),
                )
            ]

        _, final = asyncio.run(go())[-1]
        return probe, final

    def test_an_enabled_impl_step_gets_cos_and_the_feature_server(self):
        with tempfile.TemporaryDirectory() as d:
            journal = Journal(d, d)
            probe, final = self._run(d, self._hooks(), "impl", journal=journal)
            self.assertEqual(final["outcome"], "done", final)
            self.assertEqual(set(probe.kw["mcp_servers"]), {"cos", "fake"})
            # Only the derived name is allowed, beside `submit`; a sibling name and a built-in stay
            # denied. `Bash` is denied here because the stand-in's command has no allowed first word.
            self.assertTrue(probe.answers["mcp__fake__ping"])
            self.assertTrue(probe.answers["mcp__cos__submit"])
            self.assertFalse(probe.answers["mcp__fake__other"])
            self.assertEqual(probe.kw["tools"], list(policy.grant_for("impl").tools))
            self.assertNotIn("mcp__fake__ping", probe.kw["tools"])
            [start] = journal.records(d, kind="start")
            self.assertEqual(start["mcp"], ["mcp__fake__ping"])
            self.assertEqual(start["granted"], list(policy.grant_for("impl").tools))

    def test_another_stage_and_a_feature_that_is_off_get_cos_only(self):
        for name, hooks, stage in (
            ("other stage", self._hooks(), "plan"),
            ("feature off", self._hooks(on=False), "impl"),
        ):
            with self.subTest(name), tempfile.TemporaryDirectory() as d:
                journal = Journal(d, d)
                probe, final = self._run(d, hooks, stage, journal=journal)
                self.assertEqual(final["outcome"], "done", final)
                self.assertEqual(list(probe.kw["mcp_servers"]), ["cos"])
                self.assertFalse(probe.answers.get("mcp__fake__ping", False))
                self.assertFalse(probe.answers.get("mcp__fake__other", False))
                # `submit` still ends the step.
                self.assertIsNotNone(probe.submitted)
                self.assertEqual(journal.records(d, kind="start")[0]["mcp"], [])

    def test_a_step_with_tools_and_no_submit_channel_still_gets_them(self):
        hooks = self._hooks(stages=("idea",))
        with tempfile.TemporaryDirectory() as d:
            probe, _ = self._run(d, hooks, "idea")
        self.assertIn("fake", probe.kw["mcp_servers"])

    def test_two_concurrent_runs_each_get_a_server_made_from_their_own_facts(self):
        hooks = self._hooks()
        first, second = "0009_a-test-unit", "0010_another-unit"

        async def both(d, trees):
            probes = {first: self.Probe(), second: self.Probe()}
            directories = {}
            for unit in (first, second):
                directories[unit] = Path(d) / ".cos" / unit
                directories[unit].mkdir(parents=True)
                for name, body in (("intent.md", "I"), ("plan.md", "P"), ("impl.md", "# Impl\n")):
                    (directories[unit] / name).write_text(f"Status: accepted.\n{body}\n")

            async def one(unit):
                r = Runner(sessions=probes[unit], journal=None, hooks=hooks)
                async for _ in r.run(
                    workspace=d,
                    directory=directories[unit],
                    journal_key=d,
                    unit=unit,
                    stage="impl",
                    artifact="impl.md",
                    stages=STAGES,
                    mode="autonomous",
                    cwd=trees[unit],
                ):
                    pass

            await asyncio.gather(one(first), one(second))
            return probes

        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as a:
            with tempfile.TemporaryDirectory() as b:
                probes = asyncio.run(both(d, {first: a, second: b}))
                by_unit = {f.unit: f for f in self.made}
                self.assertEqual(set(by_unit), {first, second})
                self.assertEqual((by_unit[first].tree, by_unit[second].tree), (a, b))
                self.assertNotEqual(by_unit[first].run, by_unit[second].run)
                for unit in (first, second):
                    self.assertEqual(probes[unit].kw["mcp_servers"]["fake"]["name"], f"fake-{unit}")
