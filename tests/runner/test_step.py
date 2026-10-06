"""Tests for the thing that runs a step and writes what comes back.

Two properties carry the weight here.

Nothing here creates a session. What the guards do before a process is spawned is exactly what is
worth testing cheaply. The prompt's own tests are in `tests/runner/test_prompt.py`, and how a step
ends is in `tests/runner/test_step_ending.py`."""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.agent import policy
from coscc.kernel import Facts, Hooks, Parts, Tool
from coscc.store.journal import Journal
from coscc.agent.policy import grant_for
from coscc.runner.prompt import compose_prompt
from coscc.runner.reply import RunError
from coscc.runner.step import Runner
from coscc.runner.prompt import skill_for
from tests.units.test_submit import a_head, submits as _submits

STAGES = ["idea", "intent", "spec", "spike", "plan", "impl", "pr", "review", "ship"]
SESSION_STAGES = [s for s in STAGES if s not in ("pr", "ship")]
UNIT = "0009_a-test-unit"


async def asks(gate, tool: str, tool_input: dict, agent_id: str | None = None) -> str:
    """What a session's gate says of one call through its hook: "" or why it is refused."""
    said = await gate.pre_tool_use(
        {
            "tool_name": tool,
            "tool_input": tool_input,
            **({"agent_id": agent_id} if agent_id else {}),
        },
        None,
        None,
    )
    return ((said or {}).get("hookSpecificOutput") or {}).get("permissionDecisionReason", "")


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
            self.assertEqual(policy.beyond_reading(grant), (), f"{stage} carries more than reading")

    def test_intent_spec_plan_and_review_read_and_idea_does_not(self):
        # All four only read, in any mode.
        readers = ("intent", "spec", "plan", "review")
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
                    mode="autonomous",
                ):
                    out.append(ev)
                return out

            _, final = asyncio.run(go())[-1]

        self.assertEqual(final["outcome"], "done", final)
        self.assertEqual(sessions.granted, policy.READ_TOOLS)

    def test_the_guard_lets_the_intent_stage_through_with_its_read_tools(self):
        """A real `intent` run reaches the session holding `READ_TOOLS`: it checks the idea's
        problem against the worktree before it writes `## Problem`."""

        class Replies:
            def __init__(self):
                self.granted = None

            async def stream(self, cwd, text, session_id=None, max_turns=1, tools=None, **kw):
                self.granted = tuple(tools or ())
                yield ("chunk", "# Intent: x\nStatus: accepted.\n")
                await _submits(kw)
                yield ("done", {"session_id": "s-intent", "cost": {}})

        sessions = Replies()
        with tempfile.TemporaryDirectory() as d:
            make_unit(Path(d), idea_md="Status: accepted.\nI")
            r = Runner(sessions=sessions, journal=None)

            async def go():
                out = []
                async for ev in r.run(
                    workspace=d,
                    directory=Path(d) / ".cos" / UNIT,
                    journal_key=d,
                    unit=UNIT,
                    stage="intent",
                    artifact="intent.md",
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
            gate = replies.kw["gate"]
            options = sessions_mod._options(
                Config(tools=("Read", "Bash")),
                d,
                None,
                tools=replies.kw["tools"],
                data_dir=d,
                gate=gate,
            )
        self.assertEqual(replies.kw["tools"], [])
        # The gate an `idea` gets lets `submit` through and no other MCP tool.
        self.assertEqual(list(replies.kw["mcp_servers"]), ["cos"])
        self.assertEqual(asyncio.run(asks(gate, "mcp__cos__submit", {})), "")
        self.assertIn(policy.HELD, asyncio.run(asks(gate, "mcp__cos__other", {})))
        self.assertEqual(gate.grant.tools, ())
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
            again, _ = compose_prompt(*args, **kwargs)
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
            prompt, _ = compose_prompt(d, Path(d) / ".cos" / UNIT, UNIT, "review", "review.md")
            self.assertIn("# The commit you are reviewing", prompt)
            self.assertIn("could not read the head", prompt)
            self.assertIn("Do not guess one", prompt)

    def test_only_review_is_handed_the_head(self):
        with tempfile.TemporaryDirectory() as d:
            make_unit(Path(d), intent_md="Status: accepted.\nI")
            prompt, _ = compose_prompt(
                d, Path(d) / ".cos" / UNIT, UNIT, "spec", "spec.md", head="a" * 40
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
        """`coscc/http/routes.py` reads every kind that is not `chunk` as the terminal `done`."""
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

    def run_review(self, d, reply, meta=None):
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
                mode="manual",
                meta=meta,
            ):
                last = item
            return last[1]

        return session, unit / "review.md", go

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
            rounds = [{"n": 1, "verdict": "changes-requested", "findings": []}]
            session, _, go = self.run_review(
                d,
                "# Review: x\nStatus: accepted.\n\n## Round 2\n\nok\n",
                meta={"artifacts": {"review.md": {"rounds": rounds}}},
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
                gate=None,
                workspace=None,
                **kw,
            ):
                self.cwd, self.workspace = cwd, workspace
                for name, target in (
                    ("inside", f"{cwd}/x.txt"),
                    ("workspace", f"{workspace}/x.txt"),
                ):
                    said = await asks(gate, "Write", {"file_path": target, "content": "x"})
                    self.answers[name] = "deny" if said else "allow"
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
                        mode="autonomous",
                        cwd=wt,
                    )
                ]

            _, final = asyncio.run(go())[-1]
            self.assertEqual(final["outcome"], "done", final)
            self.assertEqual((probe.cwd, probe.workspace), (wt, ws))
            self.assertEqual(probe.answers, {"inside": "allow", "workspace": "deny"})


class TheStepHandsItsPlacesToTheGate(unittest.TestCase):
    """The gate a step's session gets: its grant, its worktree and unit to write, the branch the
    worktree stands on to push, the run's denials and helpers. A spike writes only its `cwd` and
    pushes nothing."""

    def _run(self, tree: Path, ws: str, stage: str, **kw):
        seen = {}

        class Probe:
            async def stream(self, cwd, text, session_id=None, max_turns=1, **given):
                seen.update(given, prompt=text)
                yield ("chunk", "# Spike: x\nSpec: spec.md. Status: accepted.\n\n## U1\n")
                await _submits(given)
                yield ("done", {"session_id": "s", "cost": {}})

        directory = make_unit(
            Path(ws),
            intent_md="Status: accepted.\nI",
            spec_md="Status: accepted.\nS",
            plan_md="Status: accepted.\nP",
            impl_md="# Impl\nStatus: accepted.\n",
        )

        async def go():
            async for _ in Runner(sessions=Probe(), journal=None).run(
                workspace=ws,
                directory=directory,
                journal_key=ws,
                unit=UNIT,
                stage=stage,
                artifact=f"{stage}.md",
                mode="autonomous",
                **kw,
            ):
                pass

        asyncio.run(go())
        self.prompt = seen["prompt"]
        return seen["gate"], directory

    def _tree(self, root: str) -> Path:
        tree = Path(root) / "tree"
        git = ["git", "-C", str(tree), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(["git", "init", "-q", "-b", "main", str(tree)], check=True)
        subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "a"], check=True)
        subprocess.run(git + ["switch", "-q", "-c", "feat/x"], check=True)
        return tree

    def test_an_impl_writes_its_worktree_and_unit_and_pushes_its_branch(self):
        with tempfile.TemporaryDirectory() as d:
            tree = self._tree(d)
            gate, directory = self._run(tree, d, "impl", cwd=str(tree))
        self.assertEqual(gate.places.roots, (str(tree), str(directory)))
        self.assertEqual(gate.places.branch, "feat/x")
        self.assertEqual(gate.places.lease, "")
        self.assertEqual(gate.grant.tools, grant_for("impl").tools)
        self.assertIsNotNone(gate.helpers)
        # The prompt names the one push this gate lets through.
        self.assertIn("`git push origin feat/x`", self.prompt)

    def test_a_spike_writes_its_cwd_and_pushes_nothing(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as scratch:
            tree = self._tree(d)
            gate, _ = self._run(tree, d, "spike", cwd=scratch, watch=str(tree))
        self.assertEqual(gate.places.roots, (scratch,))
        self.assertEqual(gate.places.branch, "")
        self.assertIsNone(gate.helpers)

    def test_a_worktree_on_the_trunk_pushes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            tree = self._tree(d)
            subprocess.run(["git", "-C", str(tree), "switch", "-q", "main"], check=True)
            gate, _ = self._run(tree, d, "impl", cwd=str(tree))
        self.assertEqual(gate.places.branch, "")


class AnImplReadsItsSiblings(unittest.TestCase):
    """The note reaches the prompt; a sibling is read and not written."""

    def _run(self, **kw):
        class Probe:
            def __init__(self):
                self.answers, self.prompt = {}, ""

            async def stream(self, cwd, text, session_id=None, max_turns=1, gate=None, **_):
                self.prompt = text
                for name, (tool, inp) in self.calls.items():
                    self.answers[name] = "deny" if await asks(gate, tool, inp) else "allow"
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
                        mode="autonomous",
                        **{k: v.format(sib=sib) for k, v in kw.items()},
                    )
                ]

            asyncio.run(go())
        return probe

    def test_a_sibling_is_read_and_not_written(self):
        probe = self._run(siblings_note="- api: {sib} at abc1234")
        self.assertEqual(probe.answers, {"read": "allow", "write": "deny", "git": "allow"})
        self.assertIn("# The sibling repositories this step may read", probe.prompt)
        self.assertIn("at abc1234", probe.prompt)

    def test_a_unit_with_no_idea_names_no_sibling(self):
        probe = self._run()
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
        runner = Runner(sessions=object(), journal=None)
        grant, ceilings = runner._configured(grant_for("spec"), "spec", None, {})
        self.assertEqual(
            (grant.max_turns, grant.max_budget_usd),
            (grant_for("spec").max_turns, grant_for("spec").max_budget_usd),
        )
        self.assertEqual(
            (ceilings["max_turns"], ceilings["max_budget_usd"]),
            (grant.max_turns, grant.max_budget_usd),
        )


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
                    mode="manual",
                )
            ]

        _, final = asyncio.run(go())[-1]
        return probe, final

    def test_every_stage_with_tools_gets_the_preset(self):
        with_tools = [s for s in STAGES if grant_for(s).opens_anything]
        # Pinned, so a change to the grant table turns this red rather than quietly
        # leaving a stage out of what it checks.
        self.assertEqual(with_tools, ["intent", "spec", "spike", "plan", "impl", "review"])
        for stage in with_tools:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as d:
                probe, _ = self.run_stage(d, stage)
                # Asserted on what the session was handed, before the runner looks at
                # the artifact, so the outcome is not what this depends on.
                self.assertEqual(probe.kw.get("system_prompt"), self.PRESET)
                self.assertNotIn("append", probe.kw["system_prompt"])

    def test_a_stage_without_tools_gets_none(self):
        with tempfile.TemporaryDirectory() as d:
            probe, _ = self.run_stage(d, "idea")
            self.assertIsNotNone(probe.kw)
            self.assertNotIn("system_prompt", probe.kw)

    def test_the_read_only_stage_still_holds_only_its_read_tools(self):
        with tempfile.TemporaryDirectory() as d:
            probe, _ = self.run_stage(d, "spec")
            self.assertEqual(probe.kw.get("system_prompt"), self.PRESET)
            self.assertEqual(probe.kw["tools"], list(policy.READ_TOOLS))
            self.assertEqual(probe.kw["gate"].grant.tools, policy.READ_TOOLS)

    def test_the_start_record_says_which_prompt_ran(self):
        with tempfile.TemporaryDirectory() as d:
            journal = Journal(d, d)
            self.run_stage(d, "impl", journal)
            self.run_stage(d, "idea", journal)
            starts = journal.records(d, kind="start")
            by_stage = {s["stage"]: s for s in starts}
            self.assertEqual(by_stage["impl"]["system_prompt"], "claude_code")
            self.assertEqual(by_stage["idea"]["system_prompt"], "")


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

    def test_the_spike_prompt_names_its_progress_file_and_its_ceilings(self):
        # The numbers are the grant's, not a second copy.
        g = grant_for("spike")
        with tempfile.TemporaryDirectory() as d:
            directory = self.unit(d)
            prompt, _ = compose_prompt(
                d,
                directory,
                UNIT,
                "spike",
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
                    compose_prompt(d, directory, UNIT, stage, artifact, ceilings=(80, 8.0)),
                    compose_prompt(d, directory, UNIT, stage, artifact),
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
    sections=("Reviewed so far", "Findings", "What was not reviewed"),
) -> str:
    body = "".join(f"### {s}\n\n- {s.lower()}\n\n" for s in sections)
    return (
        f"# Review: x\nSpec: spec.md. Author: t.\n\n"
        f"## Round {number}\n\nReviewed: {head}. Verdict: {verdict}.\n\n{body}"
    )


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
        self.assertIn("review-unfinished", start["envelope"])
        self.assertNotIn("review-unfinished", plain_start["envelope"])
        self.assertEqual(set(start), set(plain_start))


class TheScreenshotsTakenAgain(unittest.TestCase):
    """`service.steps.run_step` builds the section after a retake; this module places it for `review`
    only, after the integration's, and every other prompt is what it was."""

    NOTE = "# The screenshots, taken again\n\nSCREENS-MARKER"

    def test_review_carries_it_after_the_integration_and_says_so(self):
        with tempfile.TemporaryDirectory() as d:
            directory = _golden_unit(Path(d))
            prompt, included = compose_prompt(
                d,
                directory,
                UNIT,
                "review",
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
                    args = (d, directory, UNIT, stage, f"{stage}.md")
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
                    mode="manual",
                    screens_note=self.NOTE,
                ):
                    pass

            asyncio.run(go())
        self.assertIn("SCREENS-MARKER", seen[0])


class AStepMakesItsScratch(unittest.TestCase):
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
                    f"{stage}.md",
                    runs_commands=True,
                )[0]
                self.assertEqual(prompt.count(HARNESS_HEADING), 1)
                self.assertIn(
                    f"`{sys.executable} -P -m coscc.loop <command> --root {root}`", prompt
                )

    def test_a_prose_stage_is_not_told(self):
        from coscc.runner.prompt import HARNESS_HEADING

        prompt = compose_prompt("/w", "/store/.cos/" + UNIT, UNIT, "spec", "spec.md")[0]
        self.assertNotIn(HARNESS_HEADING, prompt)


class AStepAnUpdatePaused(unittest.TestCase):
    """At `Runner.run`: a step `suspend_all` paused writes no `end`, and one taken up again goes
    on in its own session and ends once."""

    PLAN = "# Plan: a problem\nIntent: intent.md. Status: accepted.\n\n## Order of work\n\n- a.py\n"

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
            gate = kw["gate"]
            for tool in ("mcp__fake__ping", "mcp__fake__other", "mcp__cos__submit", "Bash"):
                self.answers[tool] = not await asks(gate, tool, {"command": "ls"})
            yield ("chunk", "# Plan: x\nStatus: accepted.\n")
            self.submitted = await _submits(kw)
            yield ("done", {"session_id": "s", "cost": {}})

    def _run(self, d, hooks, stage, unit=UNIT, journal=None, cwd=None, plan=None):
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
                    mode="autonomous",
                    plan=plan,
                    **({"cwd": cwd} if cwd else {}),
                )
            ]

        _, final = asyncio.run(go())[-1]
        return probe, final

    def test_the_features_get_the_plan_record_the_step_was_handed(self):
        record = {"impl": "routine", "files": ["a.py"], "steps": [], "rests_on": []}
        with tempfile.TemporaryDirectory() as d:
            self._run(d, self._hooks(), "impl", plan=record)
        self.assertEqual(self.made[0].plan, record)

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


class AReviewsEndCountsTheRoundItHandedBack(unittest.TestCase):
    def test_the_findings_open_ones_and_verdict_come_from_the_submitted_round(self):
        from types import SimpleNamespace

        from coscc.runner.step import _round_counts

        obj = {"verdict": "changes-requested", "findings": [{"state": "open"}, {"state": "fixed"}]}
        self.assertEqual(
            _round_counts(SimpleNamespace(received={"object": obj})),
            {"findings": 2, "findings_open": 1, "verdicts": ["changes-requested"]},
        )
        self.assertEqual(
            _round_counts(SimpleNamespace(received={"object": {"judgement": "ready"}})), {}
        )
        self.assertEqual(_round_counts(None), {})
