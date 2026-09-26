"""Tests for the thing that runs a step and writes what comes back.

Two properties carry the weight here. The prompt has to contain the stage before it
(`spec.md` R4), and a prose stage has to run with nothing — no tools in either mode, which
is `spec.md` R9 and the reason the zero-tool default can still be checked.

Nothing here creates a session. What the guards do before a process is spawned is exactly
what is worth testing cheaply. The prompt's own tests are in `coscc/runner_prompt_test.py`,
and how a step ends is in `coscc/runner_ending_test.py` (`0095`).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc import gitops, harness, policy
from coscc.journal import Journal
from coscc.policy import decide, grant_for
from coscc.runner import (
    ATTEMPT_EXCERPT,
    RunError,
    Runner,
    answers_section,
    build_prompt,
    check_reply,
    compose_prompt,
    describe_attempt,
    merge_review,
    skill_for,
    snapshot,
    strip_answers,
    with_answers,
)

STAGES = ["idea", "intent", "spec", "spike", "plan", "impl", "pr", "review", "ship"]
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
        """Was `..._in_either_mode`. `0020` `spec.md` `## Answers`, answer 1: the grant no
        longer depends on the mode, so there is one grant per stage to check."""
        for stage in policy.PROSE_STAGES:
            grant = policy.grant_for(stage)
            self.assertEqual(grant.commands, (), f"{stage} carries commands")
            self.assertEqual(
                policy.beyond_reading(grant), (), f"{stage} carries more than reading"
            )

    def test_spec_plan_and_review_read_and_idea_and_intent_do_not(self):
        # `review` joined `plan` in `0015`: the separate session that sits before the merge
        # has to open the files it judges. `spec` joined in `0020`, because `write-spec`
        # requires citations with line ranges. All three only read, in any mode.
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

            async def stream(self, cwd, text, session_id=None, max_turns=1,
                             tools=None, **kw):
                self.granted = tuple(tools or ())
                yield ("chunk", "# Plan: x\nStatus: accepted.\n")
                yield ("done", {"session_id": "s-plan", "cost": {}})

        sessions = Replies()
        with tempfile.TemporaryDirectory() as d:
            make_unit(Path(d), intent_md="Status: accepted.\nI",
                      spec_md="Status: accepted.\nS")
            r = Runner(sessions=sessions, journal=None)

            async def go():
                out = []
                async for ev in r.run(
                    workspace=d, directory=Path(d) / '.cos' / UNIT, journal_key=d,
                    unit=UNIT, stage="plan", artifact="plan.md", stages=STAGES,
                    mode="autonomous",
                ):
                    out.append(ev)
                return out

            _, final = asyncio.run(go())[-1]

        self.assertEqual(final["outcome"], "done", final)
        self.assertEqual(sessions.granted, policy.READ_TOOLS)

    def test_the_guard_lets_the_spec_stage_through_with_its_read_tools(self):
        """`0020`: a real `spec` run reaches the session holding `READ_TOOLS`, in `manual`."""
        class Replies:
            def __init__(self):
                self.granted = None

            async def stream(self, cwd, text, session_id=None, max_turns=1,
                             tools=None, **kw):
                self.granted = tuple(tools or ())
                yield ("chunk", "# Spec: x\nStatus: accepted.\n")
                yield ("done", {"session_id": "s-spec", "cost": {}})

        sessions = Replies()
        with tempfile.TemporaryDirectory() as d:
            make_unit(Path(d), intent_md="Status: accepted.\nI")
            r = Runner(sessions=sessions, journal=None)

            async def go():
                out = []
                async for ev in r.run(
                    workspace=d, directory=Path(d) / '.cos' / UNIT, journal_key=d,
                    unit=UNIT, stage="spec", artifact="spec.md", stages=STAGES,
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
                        workspace=d, directory=Path(d) / '.cos' / UNIT, journal_key=d, unit=UNIT, stage="spec",
                        artifact="spec.md", stages=STAGES, mode="autonomous",
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
    """`0020` R5: the outcome checks a spec's citations at the commit the stage read."""

    class Replies:
        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            yield ("chunk", "# Spec: x\nStatus: accepted.\n")
            yield ("done", {"session_id": "s-spec", "cost": {}})

    def _start_record(self, d: str, **extra) -> dict:
        make_unit(Path(d), intent_md="Status: accepted.\nI")
        journal = Journal(d, d)
        r = Runner(sessions=self.Replies(), journal=journal)

        async def go():
            async for _ in r.run(
                workspace=d, directory=Path(d) / '.cos' / UNIT, journal_key=d, unit=UNIT,
                stage="spec", artifact="spec.md", stages=STAGES, mode="manual", **extra,
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
        """`0074` R14. The runner carries it; `service.run_step` works it out."""
        stamp = {"rank": 2, "of": 5, "record": {"at": "t", "n": 3}}
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self._start_record(d, shortlist=stamp)["shortlist"], stamp)
        with tempfile.TemporaryDirectory() as d:
            self.assertNotIn("shortlist", self._start_record(d))


class AStepCarriesItsGrantAndNothingOfTheMachine(unittest.TestCase):
    """`0088` R4, R6, R13."""

    class Replies:
        def __init__(self):
            self.kw: dict = {}
            self.prompt = ""

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            self.prompt = text
            self.kw = kw
            yield ("chunk", "# Idea: x\nStatus: accepted.\n")
            yield ("done", {"session_id": "s-idea", "cost": {}})

    def _run(self, d: str, sessions, journal=None, stage="idea"):
        r = Runner(sessions=sessions, journal=journal)

        async def go():
            async for _ in r.run(
                workspace=d, directory=Path(d) / ".cos" / UNIT, journal_key=d, unit=UNIT,
                stage=stage, artifact=f"{stage}.md", stages=STAGES, mode="manual",
            ):
                pass

        asyncio.run(go())

    def test_an_empty_grant_is_an_empty_list_whatever_cos_tools_says(self):
        # R4. `None` fell back to `COS_TOOLS`, so an `idea` held `Read` with no gate.
        from coscc import sessions as sessions_mod
        from coscc.config import Config

        replies = self.Replies()
        with tempfile.TemporaryDirectory() as d:
            make_unit(Path(d))
            self._run(d, replies)
            options = sessions_mod._options(
                Config(tools=("Read", "Bash")), d, None,
                tools=replies.kw["tools"], data_dir=d,
            )
        self.assertEqual(replies.kw["tools"], [])
        self.assertNotIn("can_use_tool", {k for k, v in replies.kw.items() if v is not None})
        self.assertEqual(options.tools, [])

    def test_the_start_row_names_the_instructions_the_session_was_given(self):
        # R13: verbatim first, scoped after, relative to the step's `cwd`.
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
        # R6. The runner hands on exactly what `build_prompt` made from the arguments it was
        # given, and the project's block goes to the system prompt, not here.
        from coscc import runner as runner_mod

        replies = self.Replies()
        built: list[str] = []

        def spy(*args, **kwargs):
            # Called again with the same arguments, before the step writes its artifact.
            # `compose_prompt` since `0094`: `build_prompt` is its wrapper.
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
    """`0030_a-unit-branch-starts-from-a-stale-main` plan.md step 4.

    `base` is `service.py`'s to compute; this module only carries it from `Runner.run`'s
    caller into the `start` record, the same way it already carries `head`.
    """

    class Replies:
        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            yield ("chunk", "# Impl: x\nStatus: accepted.\n")
            yield ("done", {"session_id": "s-impl", "cost": {}})

    def _start_record(self, d: str, **run_kw) -> dict:
        make_unit(Path(d), intent_md="Status: accepted.\nI")
        journal = Journal(d, d)
        r = Runner(sessions=self.Replies(), journal=journal)

        async def go():
            async for _ in r.run(
                workspace=d, directory=Path(d) / ".cos" / UNIT, journal_key=d, unit=UNIT,
                stage="impl", artifact="impl.md", stages=STAGES, mode="manual", **run_kw,
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
    """`0054` R7. A stage a person ran again from the board is told so, with their note, and
    a stage nobody ran again gets the prompt and the `start` record it got before."""

    HEADING = "# Why this stage runs again"
    PLAN = "# Plan: x\nStatus: accepted.\n\n1. PLAN-BODY-0054\n"
    ANSWERS = "\n## Answers\n\n### Câu 1\nAnswered by: A. Date: 2026-09-26. Via: product.\n\nANSWER-0054\n"

    def _unit(self, d: str) -> Path:
        return make_unit(
            Path(d), intent_md="Status: accepted.\nI", spec_md="Status: accepted.\nS",
            plan_md=self.PLAN + self.ANSWERS, pr_md="# PR: x\nStatus: accepted.\n\nPR-BODY-0054\n",
        )

    def _prompt(self, d: str, stage: str, **kw) -> str:
        return compose_prompt(d, self._unit(d), UNIT, stage, STAGES, f"{stage}.md",
                              writes_own=stage == "pr", **kw)[0]

    def test_without_rerun_not_one_byte_changes(self):
        with tempfile.TemporaryDirectory() as d:
            for stage in ("intent", "spec", "plan", "pr"):
                self.assertEqual(self._prompt(d, stage, rerun=False, rerun_note="x"), self._prompt(d, stage))
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
            section = prompt[prompt.index(self.HEADING):prompt.index("# Your task")]
            self.assertIn("PLAN-BODY-0054", section)
            self.assertNotIn("ANSWER-0054", section)
            self.assertNotIn("\n## Answers\n", section)

    def test_pr_does_not_repeat_pr_md(self):
        with tempfile.TemporaryDirectory() as d:
            prompt = self._prompt(d, "pr", rerun=True, rerun_note="n")
            self.assertIn(self.HEADING, prompt)
            self.assertNotIn("PR-BODY-0054", prompt)

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
    """`0042` plan step 4. `plan_drift` is `service.py`'s; the record only carries it."""

    def test_the_drift_handed_in_is_the_drift_recorded(self):
        drift = {"plan_sha": "a" * 40, "main_sha": "b" * 40, "files": ["x.py"], "checked": True, "reason": ""}
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self._start_record(d, plan_drift=drift)["plan_drift"], drift)

    def test_no_drift_handed_in_leaves_no_field(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertNotIn("plan_drift", self._start_record(d))


class AReviewIsHandedTheCommitItReviews(unittest.TestCase):
    """`0020` review round 1, F1.

    Every step runs in the unit's git worktree, whose `.git` is a file naming a directory
    under the main repository's `.git/worktrees/`. Since `0020` a `Read` there is refused,
    so `write-review` step 2 can no longer read the head itself. The app reads it and puts
    it in the prompt instead.
    """

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
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        return tree, head

    def test_the_worktree_git_directory_is_outside_what_review_may_read(self):
        # The shape F1 named: this is why the head has to come from the app.
        with tempfile.TemporaryDirectory() as d:
            tree, _ = self._worktree(d)
            self.assertTrue((tree / ".git").is_file())
            gitdir = (tree / ".git").read_text(encoding="utf-8").split(":", 1)[1].strip()
            unit = Path(d) / "store" / UNIT
            reason = decide(grant_for("review"), "Read", {"file_path": gitdir + "/HEAD"},
                            str(tree), str(unit))
            self.assertIn("reading outside the workspace", reason)

    def test_a_review_run_in_a_worktree_is_handed_its_head(self):
        seen = {}

        class Replies:
            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                seen["prompt"] = text
                yield ("chunk", "# Review: x\nStatus: accepted.\n\n## Round 1\n")
                yield ("done", {"session_id": "s-r", "cost": {}})

        with tempfile.TemporaryDirectory() as d:
            tree, head = self._worktree(d)
            store = Path(d) / "store"
            make_unit(store, intent_md="Status: accepted.\nI")
            journal = Journal(str(tree), str(tree))
            r = Runner(sessions=Replies(), journal=journal)

            async def go():
                async for _ in r.run(
                    workspace=str(tree), directory=store / ".cos" / UNIT,
                    journal_key=str(tree), unit=UNIT, stage="review", artifact="review.md",
                    stages=STAGES, mode="manual", cwd=str(tree),
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
            prompt, _ = build_prompt(d, Path(d) / ".cos" / UNIT, UNIT, "review", STAGES, "review.md")
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
    """Measured on `0016_no-human-in-the-loop`, 2026-09-23.

    Its `plan.md` opened with *"Tôi đang đọc code để viết plan — xong `cos.mjs`..."* run
    into the title with no newline between them. The file no longer began with `# Plan:`
    and `Status:` was no longer its second line. `cos.mjs` read it anyway — it looks for
    `Status:` anywhere in the file — so this corrupted every plan the board produced
    without ever failing a gate.

    It began the hour `plan` was given `Read`, `Glob` and `Grep` (#22). Before that no
    prose stage had tools, so no prose stage ever spoke twice, and concatenating every
    chunk was indistinguishable from taking the reply.
    """

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
    """`0015`'s second review erased its first, 2026-09-23.

    The app writes `review.md` from the reply, and the reply carried only what that run
    had to say. Round 1 and its five findings were gone, and so was the count `cos.mjs`
    reads to stop after N rounds and ask for a person -- a limit that resets every run is
    one that never arrives.
    """

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
            yield ("done", {"session_id": "s", "cost": {}})

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
                workspace=d, directory=unit, journal_key=d, unit=UNIT, stage="review",
                artifact="review.md", stages=STAGES, mode="manual",
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
        """`0017`, 2026-09-23: copying round 2 back is where two reviews were stopped."""
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
            session, _, go = self.run_review(d, "# Review: x\nStatus: accepted.\n\n## Round 2\n\nok\n")
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
    """`0025`. Re-running a prose stage used to overwrite its artifact whole, so a
    `## Answers` block the answer route had appended was gone with no trace but an
    `outputs` row (`.claude/rules/coscc-app.md`, the hazard this unit rewrites). `spec.md`
    R1-R6: whatever a reply says, the section already on disk survives a re-run byte for
    byte, unless there was none there to keep.
    """

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
        """`ReviewRoundsAccumulate.Replies`, plus `mid_write`: called after the reply has
        been handed over and before `done` is yielded, so a test can simulate a person's
        answer landing on disk while the step is still running (R6)."""

        def __init__(self, text, mid_write=None):
            self.text = text
            self.mid_write = mid_write

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            self.prompt = text
            yield ("chunk", self.text)
            if self.mid_write:
                self.mid_write()
            yield ("done", {"session_id": "s", "cost": {}})

    def unit_dir(self, d, **files):
        return make_unit(Path(d), **files)

    def run_once(self, d, stage, artifact, reply, mid_write=None):
        directory = Path(d) / ".cos" / UNIT
        session = self.Replies(reply, mid_write)

        async def go():
            last = None
            async for item in Runner(session, None).run(
                workspace=d, directory=directory, journal_key=d, unit=UNIT, stage=stage,
                artifact=artifact, stages=STAGES, mode="manual",
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
                + existing[existing.index("## Answers"):]
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
                d, spec_md="Author: t. Status: accepted.\n\n## Requirements\n\n"
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
        # `0025` review round 1, F1: `check_reply` saw the whole reply, so a `Status:`
        # living only under the reply's `## Answers` passed it, and `strip_answers` then
        # cut the one line the gate reads.
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
                d, spec_md="Author: t. Status: accepted.\n\n## Requirements\n\nnothing yet.\n",
            )
            path = Path(d) / ".cos" / UNIT / "spec.md"

            def mid_write():
                with path.open("a", encoding="utf-8") as f:
                    f.write(
                        "\n## Answers\n\n### Câu 1\nAnswered by: Phong. "
                        "Date: 2026-09-24. Via: product.\n\nMID-RUN-MARKER-0025\n"
                    )

            done = self.run_once(
                d, "spec", "spec.md", "# Spec: x\nStatus: accepted.\n\nno answers here.\n",
                mid_write=mid_write,
            )
            after = path.read_text(encoding="utf-8")
            self.assertEqual(done["outcome"], "done", done)
            self.assertIn("MID-RUN-MARKER-0025", after)

    def test_g_review_with_answers_and_a_new_round_keeps_both(self):
        with tempfile.TemporaryDirectory() as d:
            existing = (
                "# Review: x\nPR: pr.md. Status: changes-requested.\n\n" + self.ROUND1 +
                "\n## Answers\n\n### Câu 1\nAnswered by: Phong. Date: 2026-09-24. "
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
                "# Review: x\nPR: pr.md. Status: changes-requested.\n\n" + self.ROUND1 +
                "\n## Answers\n\n### Câu 1\nAnswered by: Phong. Date: 2026-09-24. "
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
    """`0017` plan step 5. `cwd` is the session's directory and the write boundary."""

    def test_writing_outside_the_worktree_is_refused_and_inside_is_allowed(self):
        class Probe:
            def __init__(self):
                self.cwd = None
                self.workspace = None
                self.answers = {}

            async def stream(self, cwd, text, session_id=None, max_turns=1,
                             can_use_tool=None, workspace=None, **kw):
                self.cwd, self.workspace = cwd, workspace
                for name, target in (
                    ("inside", f"{cwd}/x.txt"),
                    ("workspace", f"{workspace}/x.txt"),
                ):
                    got = await can_use_tool("Write", {"file_path": target, "content": "x"}, None)
                    self.answers[name] = type(got).__name__
                yield ("done", {"session_id": "s-impl", "cost": {}})

        probe = Probe()
        with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as wt:
            directory = make_unit(Path(ws), intent_md="Status: accepted.\nI",
                                  plan_md="Status: accepted.\nP")
            (directory / "impl.md").write_text("# Impl\nStatus: accepted.\n", encoding="utf-8")
            r = Runner(sessions=probe, journal=None)

            async def go():
                return [ev async for ev in r.run(
                    workspace=ws, directory=directory, journal_key=ws, unit=UNIT,
                    stage="impl", artifact="impl.md", stages=STAGES, mode="autonomous",
                    cwd=wt,
                )]

            _, final = asyncio.run(go())[-1]
            self.assertEqual(final["outcome"], "done", final)
            self.assertEqual((probe.cwd, probe.workspace), (wt, ws))
            self.assertEqual(probe.answers["inside"], "PermissionResultAllow")
            self.assertEqual(probe.answers["workspace"], "PermissionResultDeny")


class PrHasItsOwnTask(unittest.TestCase):
    """`0041` R1 and R2: `pr` is told where its file goes, where its shape is, the order
    that writes it before anything waits, and when to stop."""

    NOTE = "# The pull request, already looked up\n\nTHE-PR-NOTE https://x/pull/7"

    def prompt(self, d, **kw):
        directory = make_unit(Path(d), intent_md="Status: accepted.\nI", impl_md="Status: accepted.\nM")
        prompt, included = build_prompt(d, directory, UNIT, "pr", STAGES, "pr.md", writes_own=True, **kw)
        return prompt, included, directory

    def test_a_to_d(self):
        with tempfile.TemporaryDirectory() as d:
            prompt, _, directory = self.prompt(d)
            task = prompt.split("\n\n---\n\n")[-1]
            self.assertIn(f"`{directory / 'pr.md'}`", task)                       # (a)
            self.assertIn("## Output", task)                                       # (b)
            self.assertIn("read boundary refuses", task)
            self.assertLess(task.index("gh pr create"), task.index("`Status: accepted`"))  # (c)
            self.assertLess(task.index("`Status: accepted`"), task.index("gh pr checks"))
            self.assertIn("never `--watch`", task)
            self.assertIn("Do not rebase", task)                                  # (d)
            self.assertIn("*Integrate*", task)
            self.assertNotIn("Do the work this unit's plan authorises", prompt)

    def test_the_lookup_is_placed_only_for_pr(self):
        with tempfile.TemporaryDirectory() as d:
            prompt, included, _ = self.prompt(d, pr_note=self.NOTE)
            self.assertIn("THE-PR-NOTE", prompt)
            self.assertIn("pull-request", included)
            self.assertLess(prompt.index("THE-PR-NOTE"), prompt.index("# Your task"))
        with tempfile.TemporaryDirectory() as d:
            directory = make_unit(Path(d), intent_md="Status: accepted.\nI")
            prompt, _ = build_prompt(d, directory, UNIT, "impl", STAGES, "impl.md",
                                     writes_own=True, pr_note=self.NOTE)
            self.assertNotIn("THE-PR-NOTE", prompt)

    def run_stage(self, d, stage, journal, **kw):
        class Probe:
            async def stream(self, cwd, text, session_id=None, max_turns=1, **_):
                (Path(d) / ".cos" / UNIT / f"{stage}.md").write_text(
                    f"# {stage}: x\nStatus: accepted.\n", encoding="utf-8")
                yield ("chunk", "done")
                yield ("done", {"session_id": "s", "cost": {}})

        directory = make_unit(Path(d), intent_md="Status: accepted.\nI")
        r = Runner(sessions=Probe(), journal=journal)

        async def go():
            return [ev async for ev in r.run(
                workspace=d, directory=directory, journal_key=d, unit=UNIT, stage=stage,
                artifact=f"{stage}.md", stages=STAGES, mode="manual", **kw,
            )]

        return asyncio.run(go())[-1][1]

    def test_the_start_record_carries_pr_before_for_pr_alone(self):
        with tempfile.TemporaryDirectory() as d:
            journal = Journal(d, d)
            self.run_stage(d, "pr", journal, pr_note=self.NOTE, pr_before="https://x/pull/7")
            self.run_stage(d, "impl", journal, pr_before="https://x/pull/7")
            by_stage = {s["stage"]: s for s in journal.records(d, kind="start")}
            self.assertEqual(by_stage["pr"]["pr_before"], "https://x/pull/7")
            self.assertNotIn("pr_before", by_stage["impl"])

    def test_no_pull_request_before_is_the_empty_string(self):
        with tempfile.TemporaryDirectory() as d:
            journal = Journal(d, d)
            self.run_stage(d, "pr", journal)
            self.assertEqual(journal.records(d, kind="start")[-1]["pr_before"], "")


class TheStepRunsOnTheModelItWasGiven(unittest.TestCase):
    """`0004_no-setting-says-which-model-runs-a-stage`. The runner does not choose a model;
    it passes on the one it was given and writes it into the run log."""

    def run_spec(self, d, **kw):
        class Probe:
            def __init__(self):
                self.kw = None

            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                self.kw = kw
                yield ("chunk", "# Spec: x\nStatus: accepted.\n")
                yield ("done", {"session_id": "s-m", "cost": {}})

        probe = Probe()
        make_unit(Path(d), intent_md="Status: accepted.\nI")
        journal = Journal(d, d)
        r = Runner(sessions=probe, journal=journal)

        async def go():
            return [ev async for ev in r.run(
                workspace=d, directory=Path(d) / ".cos" / UNIT, journal_key=d,
                unit=UNIT, stage="spec", artifact="spec.md", stages=STAGES,
                mode="manual", **kw,
            )]

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
        """`0043` R3: `person` unless the caller names `autopilot`; nothing else is taken."""
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
    """`0033` spec R10. The runner chooses none of it; it passes effort on and writes what
    it was given into `start`, the SDK's `terminal_reason` into `end`."""

    def run_spec(self, d, terminal=None, **kw):
        class Probe:
            def __init__(self):
                self.kw = None

            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                self.kw = kw
                yield ("chunk", "# Spec: x\nStatus: accepted.\n")
                yield ("done", {"session_id": "s-e", "cost": {}, "terminal_reason": terminal})

        probe = Probe()
        make_unit(Path(d), intent_md="Status: accepted.\nI")
        journal = Journal(d, d)
        r = Runner(sessions=probe, journal=journal)

        async def go():
            return [ev async for ev in r.run(
                workspace=d, directory=Path(d) / ".cos" / UNIT, journal_key=d,
                unit=UNIT, stage="spec", artifact="spec.md", stages=STAGES,
                mode="manual", **kw,
            )]

        _, final = asyncio.run(go())[-1]
        return probe, journal, final

    def test_start_carries_every_field(self):
        with tempfile.TemporaryDirectory() as d:
            probe, journal, final = self.run_spec(
                d, effort="high", effort_source="default", label_declared="routine",
                label="novel", label_source="escalated", impl_run=2,
            )
            self.assertEqual(final["outcome"], "done", final)
            self.assertEqual(probe.kw.get("effort"), "high")
            start = journal.records(d, kind="start")[-1]
            got = {k: start.get(k) for k in
                   ("effort", "effort_source", "label_declared", "label", "label_source", "impl_run")}
            self.assertEqual(got, {"effort": "high", "effort_source": "default", "label_declared": "routine",
                                   "label": "novel", "label_source": "escalated", "impl_run": 2})

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
    """`0062` R1 and R7: `Runner.run` asks for the grant with the label it was given, and
    the ceilings the session receives are the ones the `start` record names."""

    def run_impl(self, d, **kw):
        class Probe:
            def __init__(self):
                self.max_turns = self.budget = None

            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                self.max_turns, self.budget = max_turns, kw.get("max_budget_usd")
                (directory / "impl.md").write_text("# Impl\nStatus: accepted.\n",
                                                   encoding="utf-8")
                yield ("done", {"session_id": "s-impl", "cost": {}})

        probe = Probe()
        directory = make_unit(Path(d), intent_md="Status: accepted.\nI",
                              plan_md="Status: accepted.\nP")
        journal = Journal(d, d)
        r = Runner(sessions=probe, journal=journal)

        async def go():
            return [ev async for ev in r.run(
                workspace=d, directory=directory, journal_key=d, unit=UNIT,
                stage="impl", artifact="impl.md", stages=STAGES, mode="autonomous", **kw,
            )]

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


class ABoardStepWithToolsRunsOnClaudeCodesPrompt(unittest.TestCase):
    """`0037_board-sessions-run-without-claude-codes-system-prompt`.

    A step holding any tool is handed Claude Code's preset system prompt; a step holding
    none is handed nothing, exactly as before. The preset must not widen the grant: the
    callback a preset step receives still refuses what its stage may not do.
    """

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
            yield ("done", {"session_id": f"s-{stage}", "cost": {}})

    def run_stage(self, d, stage, journal=None):
        probe = self.Probe()
        directory = make_unit(Path(d), intent_md="Status: accepted.\nI")
        probe.stage, probe.directory = stage, directory
        r = Runner(sessions=probe, journal=journal)

        async def go():
            return [ev async for ev in r.run(
                workspace=d, directory=directory, journal_key=d,
                unit=UNIT, stage=stage, artifact=f"{stage}.md", stages=STAGES,
                mode="manual",
            )]

        _, final = asyncio.run(go())[-1]
        return probe, final

    def test_every_stage_with_tools_gets_the_preset(self):
        with_tools = [s for s in STAGES if grant_for(s).opens_anything]
        # Pinned, so a change to the grant table turns this red rather than quietly
        # leaving a stage out of what it checks.
        self.assertEqual(with_tools, ["spec", "spike", "plan", "impl", "pr", "review", "ship"])
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
            for tool, data in (("Write", {"file_path": inside, "content": "x"}),
                               ("Bash", {"command": "ls"})):
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


def _git_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "a.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", "add", "-A"], cwd=repo, check=True,
    )
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", "commit", "-q", "-m", "first"], cwd=repo, check=True,
    )
    return repo


SPIKE_REPLY = (
    "# Spike: x\nSpec: spec.md. Author: ᛈ Perthro. Round: 1. Status: accepted.\n\n"
    "## U1\n\nVerdict: holds.\n\n```\n$ python -c 'print(1)'\n1\n```\n"
)


class ThePlanAndTheSpecReadTheSpike(unittest.TestCase):
    """`0039` R14: the prompts that need a second artifact get it, and `included` says so."""

    def unit(self, d: str, **files: str) -> Path:
        return make_unit(Path(d), intent_md="Status: accepted.\nI", spec_md="Status: accepted.\nSPEC", **files)

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
        # `0080` R2: the numbers are the grant's, not a second copy.
        g = grant_for("spike")
        with tempfile.TemporaryDirectory() as d:
            directory = self.unit(d)
            prompt, _ = build_prompt(
                d, directory, UNIT, "spike", STAGES, "spike.md", worktree="/the/tree",
                ceilings=(g.max_turns, g.max_budget_usd),
            )
            self.assertIn(f"`{Path(d).resolve() / 'spike.md'}`", prompt)
            self.assertIn(f"{g.max_turns} turns", prompt)
            self.assertIn(f"${g.max_budget_usd:.2f}", prompt)
            self.assertIn("the app writes `spike.md` from this file", prompt)

    def test_no_other_stage_prompt_changes_by_a_byte(self):
        with tempfile.TemporaryDirectory() as d:
            directory = self.unit(d, spike_md="Status: accepted.\nR")
            for stage in STAGES:
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
                yield ("done", {"session_id": "s", "cost": {}})

        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as scratch:
            directory = self.unit(d)
            r = Runner(sessions=Fake(), journal=None)

            async def go():
                return [ev async for ev in r.run(
                    workspace=d, directory=directory, journal_key=d, unit=UNIT,
                    stage="spike", artifact="spike.md", stages=STAGES, mode="autonomous",
                    cwd=scratch,
                )]

            asyncio.run(go())
        [(text, turns, budget)] = seen
        self.assertEqual((turns, budget), (g.max_turns, g.max_budget_usd))
        self.assertIn(f"This step has {g.max_turns} turns and ${g.max_budget_usd:.2f}.", text)

    def test_the_spike_skill_carries_the_progress_file(self):
        # `0080` R1. Red too when a stale `coscc/_harness/` hides `.claude/`.
        self.assertIn("## The progress file", skill_for("spike"))


class RecordingChangesNothing(unittest.TestCase):
    """`0073` R5. The same step with a recorder and without one: the same outcome, the same
    artifact, the same `start` and `end` but for `run` and `events_lost`. A recorder that
    raises everywhere changes none of that, and the `end` says events were lost."""

    class Replies:
        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            yield ("chunk", "# Spec: x\nStatus: accepted.\n")
            yield ("done", {"session_id": "s-1", "terminal_reason": "success",
                            "cost": {"turns": 2, "cost_usd": 0.25}})

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
        from coscc import steps
        from coscc.data import Data

        with tempfile.TemporaryDirectory() as d:
            directory = make_unit(Path(d), intent_md="Status: accepted.\nI")
            journal = Journal(d, d)
            running = None
            if make_recorder is not None:
                running = steps.Registry().claim(d, UNIT, "spec")
                running.handle.recorder = make_recorder(Data(d))

            async def go():
                return [i async for i in Runner(sessions=self.Replies(), journal=journal).run(
                    workspace=d, directory=directory, journal_key=d, unit=UNIT,
                    stage="spec", artifact="spec.md", stages=STAGES, mode="manual", running=running,
                )]

            out = asyncio.run(go())
            raw = [r for r in journal.records() if r["kind"] in ("start", "end")]
            # Each run has its own temporary directory, and the records name it.
            same = [
                {k: (v.replace(d, "<d>") if isinstance(v, str) else v)
                 for k, v in r.items() if k not in self.IGNORED}
                for r in raw
            ]
            return out[-1][1]["outcome"], (directory / "spec.md").read_bytes(), same, raw

    def test_with_and_without_a_recorder_the_step_is_the_same(self):
        from coscc import events

        plain = self._once(None)
        recorded = self._once(lambda data: events.Recorder("r-1", data, "/w", "/w/ws", UNIT, "spec"))
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
        from coscc.runner import Denials

        heard = []
        denials = Denials()
        denials.listener = lambda tool, tool_input, reason: heard.append((tool, tool_input, reason))
        for i in range(7):
            denials.record("Bash", "no", {"command": str(i)})
        self.assertEqual(len(heard), 7)
        self.assertEqual(heard[6], ("Bash", {"command": "6"}, "no"))
        self.assertEqual((denials.count, len(denials.reasons)), (7, Denials.KEEP))


# `0094` plan step 4. One unit holding every artifact, each prose one carrying `## Answers`,
# and a review sent back with two rounds: the fixture the prompts below are built from.
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
    files = {f"{s}_md": _ANSWERED.format(name=s.upper()) for s in ("idea", "intent", "spec", "plan")}
    files.update(
        spike_md="# Spike: x\nSpec: spec.md. Status: accepted. Round: 1.\n\nSPIKE-BODY\n",
        impl_md="# Impl: x\nStatus: accepted.\n\nIMPL-BODY\n",
        pr_md="# PR: x\nPR: https://github.com/o/r/pull/9. Status: accepted.\n\nPR-BODY\n",
        review_md=_REVIEW_TWO_ROUNDS,
        ship_md="# Ship: x\nStatus: draft.\n\nSHIP-BODY\n",
    )
    return make_unit(root, **files)


class TheStartRecordSaysWhatRanAndWhatWasNamed(unittest.TestCase):
    """`0094` R13, R16."""

    class Probe:
        async def stream(self, cwd, text, session_id=None, max_turns=1, **_):
            yield ("chunk", "x")
            yield ("done", {"session_id": "s", "cost": {}})

    def start(self, d, stage, app):
        directory = _golden_unit(Path(d))
        journal = Journal(d, d)
        r = Runner(self.Probe(), journal, app=app)

        async def go():
            async for _ in r.run(
                workspace=d, directory=directory, journal_key=d, unit=UNIT, stage=stage,
                artifact=f"{stage}.md", stages=STAGES, mode="manual",
            ):
                pass

        asyncio.run(go())
        return journal.records(d, kind="start")[-1]

    def test_the_build_and_the_pointed_files(self):
        with tempfile.TemporaryDirectory() as d:
            start = self.start(d, "review", {"version": "0.12.0", "commit": "c" * 40})
            self.assertEqual((start["app_version"], start["app_commit"]), ("0.12.0", "c" * 40))
            self.assertIn("impl.md", start["pointed"])
            self.assertIn("review-findings", start["included"])
            self.assertNotIn("review.md", start["included"])

    def test_a_runner_given_no_build_writes_neither_field(self):
        with tempfile.TemporaryDirectory() as d:
            start = self.start(d, "spec", None)
            self.assertNotIn("app_version", start)
            self.assertNotIn("app_commit", start)
            self.assertEqual(start["pointed"], [])


# --- `0085`: a review that runs out of turns --------------------------------------------

REVIEW_R1 = (
    "# Review: x\nSpec: spec.md. Author: t. Status: changes-requested.\n\n"
    "## Round 1\n\nReviewed: " + "a" * 40 + ". Verdict: changes-requested.\n\n"
    "### Findings\n\n- F1 [open] a.py:3 — high — x\n"
)


def incomplete_reply(head: str, number: int = 2, verdict: str = "incomplete",
                     status: str = "draft", sections=("Reviewed so far", "Findings", "What was not reviewed")) -> str:
    body = "".join(f"### {s}\n\n- {s.lower()}\n\n" for s in sections)
    return (f"# Review: x\nSpec: spec.md. Author: t. Status: {status}.\n\n"
            f"## Round {number}\n\nReviewed: {head}. Verdict: {verdict}.\n\n{body}")


class AStepRecordsTheKnowledgeItCarried(AStepRecordsTheBaseItRanOn):
    """`0090` R2, R4: the record handed in is the record written, and none is no field."""

    def test_no_record_handed_in_leaves_no_field(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertNotIn("knowledge", self._start_record(d))

    def test_the_record_handed_in_is_the_record_written(self):
        record = {"version": "abc", "entries": 0, "bytes": 0}
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self._start_record(d, knowledge_record=record)["knowledge"], record)


class AStepRecordsThePriorFindingsItCarried(AStepRecordsTheBaseItRanOn):
    """`0110` R7: the record handed in is the record written, and none is no field."""

    def test_no_record_handed_in_leaves_no_field(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertNotIn("prior_findings", self._start_record(d))

    def test_the_record_handed_in_is_the_record_written(self):
        record = {"bytes": 0, "lines": 0, "units": 0, "dropped": 0}
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self._start_record(d, prior_findings_record=record)["prior_findings"], record)


class TheScreenshotsTakenAgain(unittest.TestCase):
    """`0111` R7. `service.run_step` builds the section after a retake; this module places it
    for `review` only, after the integration's, and every other prompt is what it was."""

    NOTE = "# The screenshots, taken again\n\nSCREENS-MARKER"

    def test_review_carries_it_after_the_integration_and_says_so(self):
        with tempfile.TemporaryDirectory() as d:
            directory = _golden_unit(Path(d))
            prompt, included, _ = compose_prompt(
                d, directory, UNIT, "review", STAGES, "review.md",
                integration_note="# INTEGRATION\n\nINTEGRATION-NOTE", screens_note=self.NOTE,
            )
        self.assertEqual(prompt.count("SCREENS-MARKER"), 1)
        self.assertLess(prompt.index("INTEGRATION-NOTE"), prompt.index("SCREENS-MARKER"))
        self.assertLess(prompt.index("SCREENS-MARKER"), prompt.index("# Your task"))
        self.assertIn("screens", included)

    def test_no_other_stage_carries_it_and_none_handed_is_no_byte(self):
        with tempfile.TemporaryDirectory() as d:
            directory = _golden_unit(Path(d))
            for stage in STAGES:
                with self.subTest(stage=stage):
                    args = (d, directory, UNIT, stage, STAGES, f"{stage}.md")
                    handed = compose_prompt(*args, screens_note=self.NOTE)
                    if stage == "review":
                        self.assertEqual(compose_prompt(*args, screens_note=""), compose_prompt(*args))
                    else:
                        self.assertEqual(handed, compose_prompt(*args))

    def test_runner_run_hands_it_to_the_prompt(self):
        seen = []

        class Replies:
            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                seen.append(text)
                yield ("chunk", "# Review: x\nStatus: changes-requested.\n\n## Round 1\n\nReviewed: abc1234. Verdict: changes-requested.\n")
                yield ("done", {"session_id": "s-review", "cost": {}})

        with tempfile.TemporaryDirectory() as d:
            make_unit(Path(d), intent_md="Status: accepted.\nI")
            journal = Journal(d, d)

            async def go():
                async for _ in Runner(sessions=Replies(), journal=journal).run(
                    workspace=d, directory=Path(d) / ".cos" / UNIT, journal_key=d, unit=UNIT,
                    stage="review", artifact="review.md", stages=STAGES, mode="manual", screens_note=self.NOTE,
                ):
                    pass

            asyncio.run(go())
        self.assertIn("SCREENS-MARKER", seen[0])


class TheCommandsAStepMayRun(unittest.TestCase):
    """`0096` Risk 4. `COMMANDS_ADVICE` is prose about `policy.check_command`: each thing it
    says is refused is refused, and what it says may run does, on `impl`'s own grant."""

    def test_what_the_advice_says_is_refused_is_refused(self):
        grant = policy.grant_for_step("impl", None)
        for line in ("cd x", "timeout 5 npm test", "git status; cd x", "echo a > f.txt", "echo $(pwd)",
                     "echo `pwd`", "diff <(ls) f"):
            with self.subTest(line=line):
                self.assertNotEqual(policy.check_command(grant, line, unit=UNIT), "")

    def test_what_it_says_may_run_runs(self):
        grant = policy.grant_for_step("impl", None)
        for line in ("echo a > /dev/null", "git status && npm test", "ls | wc -l"):
            with self.subTest(line=line):
                self.assertEqual(policy.check_command(grant, line, unit=UNIT), "")

    def test_the_words_an_impl_step_gets_are_its_grant(self):
        from coscc.runner import COMMANDS_HEADING

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


class AStepRecordsThePlanMapItCarried(AStepRecordsTheBaseItRanOn):
    """`0096` R11: the record handed in is the record written, and none is no field."""

    def test_no_record_handed_in_leaves_no_field(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertNotIn("plan_map", self._start_record(d))

    def test_the_record_handed_in_is_the_record_written(self):
        record = {"bytes": 0, "files": 0, "full": 0, "short": 0, "new": 0, "outside": 0}
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self._start_record(d, plan_map_record=record)["plan_map"], record)
