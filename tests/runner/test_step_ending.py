"""Tests for `Runner` in `coscc/runner/step.py`: how a step ends, split from
`tests/runner/test_step.py`.

An answer that comes in pieces is written whole. A step that fails, is stopped, dies, runs
out of turns or touches what it may not still leaves a record that says so, and writes no
artifact it should not."""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.agent import harness
from coscc.git import gitops
from coscc.store.journal import Journal
from coscc.runner.reply import RunError
from coscc.runner.step import Runner
from coscc.runner.prompt import compose_prompt, skill_for
from coscc.runner.attempt import snapshot
from tests.runner.test_step import (
    REVIEW_R1,
    SPIKE_REPLY,
    UNIT,
    _git_repo,
    asks,
    incomplete_reply,
    make_unit,
)
from coscc.agent import policy
from tests.units.test_submit import submits as _submits


class AFailedStepIsRecordedAsFailed(unittest.TestCase):
    def test_a_session_that_returns_nothing_writes_no_artifact_and_says_why(self):
        class Silent:
            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                await _submits(kw)
                yield ("done", {"session_id": "s-9", "cost": {"input_tokens": 5}})

        with tempfile.TemporaryDirectory() as d:
            directory = make_unit(Path(d), intent_md="Status: accepted.\nI")
            journal = Journal(d, d)
            r = Runner(sessions=Silent(), journal=journal)

            async def go():
                out = []
                async for item in r.run(
                    workspace=d,
                    directory=Path(d) / ".cos" / UNIT,
                    journal_key=d,
                    unit=UNIT,
                    stage="spec",
                    artifact="spec.md",
                    mode="manual",
                ):
                    out.append(item)
                return out

            [(kind, payload)] = asyncio.run(go())
            self.assertEqual(kind, "done")
            self.assertEqual(payload["outcome"], "failed")
            self.assertIn("returned nothing", payload["error"])
            self.assertFalse((directory / "spec.md").exists())

            # and the failure is in the journal, with what it cost anyway
            [row] = journal.timeline(d, UNIT)
            self.assertEqual(row["outcome"], "failed")
            self.assertEqual(row["cost"]["input_tokens"], 5)

    def test_a_good_reply_becomes_the_artifact_and_is_recorded_done(self):
        class Replies:
            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                yield ("chunk", "# Spec: x\n")
                yield ("chunk", "Status: accepted.\n")
                await _submits(kw)
                yield ("done", {"session_id": "s-1", "cost": {"output_tokens": 7}})

        with tempfile.TemporaryDirectory() as d:
            directory = make_unit(Path(d), intent_md="Status: accepted.\nI")
            journal = Journal(d, d)
            r = Runner(sessions=Replies(), journal=journal)

            async def go():
                out = []
                async for item in r.run(
                    workspace=d,
                    directory=Path(d) / ".cos" / UNIT,
                    journal_key=d,
                    unit=UNIT,
                    stage="spec",
                    artifact="spec.md",
                    mode="manual",
                ):
                    out.append(item)
                return out

            items = asyncio.run(go())
            kind, payload = items[-1]
            self.assertEqual(payload["outcome"], "done")
            self.assertEqual(payload["artifact"], "spec.md")
            self.assertIn("Status: accepted.", (directory / "spec.md").read_text())

            [row] = journal.timeline(d, UNIT)
            self.assertEqual(row["outcome"], "done")
            self.assertEqual(row["session_id"], "s-1")
            self.assertEqual(row["cost"]["output_tokens"], 7)

    def test_a_step_stopped_by_its_ceiling_is_exhausted_not_done(self):
        """A bound doing its job must not read as a bug, or as success."""

        class RanOut:
            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                yield ("chunk", "# Spec: x\nStatus: draft.\n")
                await _submits(kw)
                yield ("done", {"session_id": "s-3", "cost": {}, "terminal_reason": "max_turns"})

        with tempfile.TemporaryDirectory() as d:
            make_unit(Path(d), intent_md="Status: accepted.\nI")
            journal = Journal(d, d)
            r = Runner(sessions=RanOut(), journal=journal)

            async def go():
                out = []
                async for item in r.run(
                    workspace=d,
                    directory=Path(d) / ".cos" / UNIT,
                    journal_key=d,
                    unit=UNIT,
                    stage="spec",
                    artifact="spec.md",
                    mode="manual",
                ):
                    out.append(item)
                return out

            _, payload = asyncio.run(go())[-1]
            self.assertEqual(payload["outcome"], "exhausted")
            self.assertIn("ceiling", payload["error"])
            self.assertEqual(journal.timeline(d, UNIT)[0]["outcome"], "exhausted")

    def test_an_ordinary_finish_is_not_mistaken_for_a_ceiling(self):
        class Normal:
            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                yield ("chunk", "# Spec: x\nStatus: accepted.\n")
                await _submits(kw)
                yield ("done", {"session_id": "s-4", "cost": {}, "terminal_reason": "completed"})

        with tempfile.TemporaryDirectory() as d:
            make_unit(Path(d), intent_md="Status: accepted.\nI")
            r = Runner(sessions=Normal(), journal=Journal(d, d))

            async def go():
                out = []
                async for item in r.run(
                    workspace=d,
                    directory=Path(d) / ".cos" / UNIT,
                    journal_key=d,
                    unit=UNIT,
                    stage="spec",
                    artifact="spec.md",
                    mode="manual",
                ):
                    out.append(item)
                return out

            _, payload = asyncio.run(go())[-1]
            self.assertEqual(payload["outcome"], "done")

    def test_a_missing_unit_refuses_before_a_session_exists(self):
        with tempfile.TemporaryDirectory() as d:
            r = Runner(sessions=None, journal=None)

            async def go():
                async for _ in r.run(
                    workspace=d,
                    directory=Path(d) / ".cos" / UNIT,
                    journal_key=d,
                    unit=UNIT,
                    stage="spec",
                    artifact="spec.md",
                    mode="manual",
                ):
                    pass

            with self.assertRaises(RunError):
                asyncio.run(go())


class AStepWithNoRulesDoesNotRun(unittest.TestCase):
    """Until 0012 every assertion in this class was false by design.

    The failure it stands against is not "an error was raised" but "no error was raised":
    a step that cannot find its rules used to run to completion, bill an account, and
    leave a record identical to a step that had them."""

    def test_a_stage_with_no_skill_raises_rather_than_dropping_the_section(self):
        # `RunError`, not `MissingRules`: `coscc/http/routes.py` maps this module's refusals
        # with one `except RunError`, and anything else reaches the route as a 500.
        with self.assertRaises(RunError) as caught:
            skill_for("no-such-stage")
        self.assertIn("no-such-stage", str(caught.exception))
        self.assertIn("SKILL.md", str(caught.exception))

    def test_the_prompt_always_carries_the_rules_section(self):
        with tempfile.TemporaryDirectory() as d:
            make_unit(Path(d), intent_md="Status: accepted.\nINTENT")
            prompt, _ = compose_prompt(d, Path(d) / ".cos" / UNIT, UNIT, "spec", "spec.md")
            self.assertIn("# The rules for this stage", prompt)

    def test_it_refuses_before_the_journal_is_touched_or_a_session_is_made(self):
        # The two things a step costs: a row saying it started, and a request that bills.
        # Both come after `compose_prompt` in `Runner.run`, and this is what holds them there.
        class Counting:
            def __init__(self):
                self.streams = 0

            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                self.streams += 1
                await _submits(kw)
                yield ("done", {})

        with tempfile.TemporaryDirectory() as d:
            make_unit(Path(d), intent_md="Status: accepted.\nINTENT")
            # Two arguments, like every other Journal in this file. With one, `data`
            # defaults to `Data(None)` and this test writes into the real `~/.cos` --
            # the hazard `coscc/store/journal.py:108-109` names, found live 2026-09-22 after
            # `SCHEMA_VERSION` went to 2: running `npm test` upgraded the developer's own
            # database, and the installed v0.2.3 then answered 500 on every route that
            # reads it while `/api/health` still said ok.
            journal = Journal(d, d)
            sessions = Counting()
            runner = Runner(sessions=sessions, journal=journal)

            originals = (harness.PACKAGE_HARNESS, harness.CHECKOUT_HARNESS)
            harness.PACKAGE_HARNESS = Path("/nonexistent/packaged")
            harness.CHECKOUT_HARNESS = Path("/nonexistent/checkout")
            try:

                async def go():
                    async for _ in runner.run(
                        workspace=d,
                        directory=Path(d) / ".cos" / UNIT,
                        journal_key=d,
                        unit=UNIT,
                        stage="spec",
                        artifact="spec.md",
                        mode="manual",
                    ):
                        pass

                with self.assertRaises(RunError):
                    asyncio.run(go())
            finally:
                harness.PACKAGE_HARNESS, harness.CHECKOUT_HARNESS = originals

            self.assertEqual(sessions.streams, 0)
            self.assertEqual(journal.timelines(d).get(UNIT, []), [])


class AnUnusableReplyIsKeptBesideTheReason(unittest.TestCase):
    """A paid step that produced nothing usable must not throw the reply away.

    Measured 2026-09-22 inside a proof run that spends real money: a `spec` step failed
    with *"the reply carries no title"* and the reply went with the run's
    temporary data root. Nothing was left to say whether the artifact had been there
    behind a preamble, and the only way to find out was to pay again."""

    class NoTitle:
        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            yield ("chunk", "Here is the spec you asked for:\n\n")
            yield ("chunk", "## Requirements\n\nR1 — something.\n")
            await _submits(kw)
            yield ("done", {"session_id": "s-9", "cost": {}})

    def test_the_reply_comes_back_with_the_refusal(self):
        with tempfile.TemporaryDirectory() as d:
            make_unit(Path(d), intent_md="Status: accepted.\nI")
            journal = Journal(d, d)
            r = Runner(sessions=self.NoTitle(), journal=journal)

            async def go():
                out = []
                async for item in r.run(
                    workspace=d,
                    directory=Path(d) / ".cos" / UNIT,
                    journal_key=d,
                    unit=UNIT,
                    stage="spec",
                    artifact="spec.md",
                    mode="manual",
                ):
                    out.append(item)
                return out

            _, payload = asyncio.run(go())[-1]
            self.assertNotEqual(payload["outcome"], "done")
            self.assertIn("no `# Spec:` title", payload["error"])
            self.assertIn("R1 — something.", payload["error"])
            # And it is in the run log too, so it survives the page being closed.
            [row] = journal.timeline(d, UNIT)
            self.assertIn("R1 — something.", row.get("detail") or "")


class AnAnswerInPiecesIsWrittenWhole(unittest.TestCase):
    """A `plan.md` came back as its last piece alone: the session wrote the title and the head,
    called a tool, wrote more, called another, and only what followed the last call reached
    the file."""

    HEAD = (
        "# Plan: 0085 again\nIntent: intent.md. Spec: spec.md. Author: t. Status: accepted. "
        "Impl: routine.\n\n## Order of work\n\nPHẦN-ĐẦU\n"
    )
    UNTITLED = "## Order of work\n\nPHẦN-ĐẦU\n"
    TAIL = "Lượt chốt (closing turn) không chạy… PHẦN-ĐUÔI\n"

    class Pieces:
        def __init__(self, first, blank=False, terminal="success"):
            self.first, self.blank, self.terminal = first, blank, terminal

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            yield ("chunk", self.first)
            if self.blank:
                yield ("chunk", "  ")
            yield ("tool", "Read")
            yield ("chunk", "PHẦN-GIỮA")
            yield ("tool", "Grep")
            yield ("chunk", AnAnswerInPiecesIsWrittenWhole.TAIL)
            await _submits(kw)
            yield ("done", {"session_id": "s-85", "terminal_reason": self.terminal, "cost": {}})

    def go(self, sessions, existing=None):
        with tempfile.TemporaryDirectory() as d:
            directory = make_unit(
                Path(d), intent_md="Status: accepted.\nI", spec_md="Status: accepted.\nS"
            )
            if existing is not None:
                (directory / "plan.md").write_bytes(existing)
            journal = Journal(d, d)
            runner = Runner(sessions, journal)

            async def run():
                return [
                    item
                    async for item in runner.run(
                        workspace=d,
                        directory=directory,
                        journal_key=d,
                        unit=UNIT,
                        stage="plan",
                        artifact="plan.md",
                        mode="autonomous",
                    )
                ]

            out = asyncio.run(run())
            target = directory / "plan.md"
            [end] = [r for r in journal.records() if r["kind"] == "end"]
            return out[-1][1], end, target.read_bytes() if target.exists() else None

    def test_every_piece_after_the_title_is_written_in_order(self):
        final, _, written = self.go(self.Pieces(self.HEAD))
        self.assertEqual(final["outcome"], "done")
        text = written.decode("utf-8")
        self.assertTrue(text.startswith("# Plan:"), text[:80])
        self.assertLess(text.index("PHẦN-ĐẦU"), text.index("PHẦN-GIỮA"))
        self.assertLess(text.index("PHẦN-GIỮA"), text.index("PHẦN-ĐUÔI"))
        self.assertIn("PHẦN-GIỮA\nLượt chốt", text, "a piece that ends mid-line gets its own line")

    def test_an_answer_without_its_head_fails_and_says_why(self):
        final, end, written = self.go(self.Pieces(self.UNTITLED, blank=True))
        self.assertEqual(final["outcome"], "failed")
        first = end["detail"].splitlines()[0]
        for part in ("plan.md", "title", "3 blocks"):
            self.assertIn(part, first)
        self.assertIsNone(written)

    def test_an_answer_without_its_head_leaves_the_file_byte_for_byte(self):
        existing = (
            "# Plan: x\nIntent: i. Status: draft.\n\nCŨ\n\n## Answers\n\n"
            "### Câu 1\nAnswered by: Lan. Date: 2026-09-26. Via: product.\n\ncó\n"
        ).encode("utf-8")
        final, _, written = self.go(self.Pieces(self.UNTITLED, blank=True), existing=existing)
        self.assertEqual(final["outcome"], "failed")
        self.assertEqual(written, existing)

    def test_at_the_ceiling_it_is_exhausted_never_done(self):
        final, end, written = self.go(self.Pieces(self.UNTITLED, blank=True, terminal="max_turns"))
        self.assertEqual((final["outcome"], end["outcome"]), ("exhausted", "exhausted"))
        self.assertIn("plan.md lacks its opening", end["detail"])
        self.assertIsNone(written)


class AFencedAnswerAfterNarrationIsUnwrapped(unittest.TestCase):
    """Narration, a tool call, then the artifact in a fence: it is written whole, not as only the
    fenced piece that `unfence` would take out."""

    BODY = "# Plan: x\nIntent: i. Status: accepted.\n\n## Body\n\n```\n# Plan: quoted\n```\n"

    class Fenced:
        def __init__(self, last):
            self.last = last

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            yield ("chunk", "Để tôi đọc lại plan.")
            yield ("tool", "Read")
            yield ("chunk", self.last)
            await _submits(kw)
            yield ("done", {"session_id": "s-1", "cost": {}})

    def go(self, last):
        return AnAnswerInPiecesIsWrittenWhole.go(self, self.Fenced(last))

    def test_the_fenced_artifact_is_written_out_of_its_fence(self):
        final, _, written = self.go("```markdown\n" + self.BODY + "```\n")
        self.assertEqual(final["outcome"], "done")
        self.assertEqual(written.decode("utf-8"), self.BODY)

    def test_a_piece_that_is_only_a_code_block_keeps_its_fence(self):
        # A fence whose top is not the title is the body's, not a wrapper.
        from coscc.runner.reply import _joined

        pieces = ["# Plan: x\nStatus: accepted.\n\n## Body\n", "```\ncode\n```"]
        self.assertEqual(
            _joined(pieces, "plan.md"), "# Plan: x\nStatus: accepted.\n\n## Body\n```\ncode\n```"
        )


class AFailedStepLeavesASnapshot(unittest.TestCase):
    def test_max_turns_leaves_an_attempt_before_end_with_matching_cost(self):
        class HitCeiling:
            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                yield ("session", "s-ceiling")
                yield ("chunk", "still working")
                yield (
                    "done",
                    {
                        "session_id": "s-ceiling",
                        "cost": {"turns": 121, "cost_usd": 6.88},
                        "terminal_reason": "max_turns",
                    },
                )

        with tempfile.TemporaryDirectory() as d:
            repo = _git_repo(Path(d))
            make_unit(Path(d), intent_md="Status: accepted.\nI")
            journal = Journal(d, d)
            r = Runner(sessions=HitCeiling(), journal=journal)

            async def go():
                return [
                    ev
                    async for ev in r.run(
                        workspace=d,
                        directory=Path(d) / ".cos" / UNIT,
                        journal_key=d,
                        unit=UNIT,
                        stage="impl",
                        artifact="impl.md",
                        mode="manual",
                        cwd=str(repo),
                    )
                ]

            items = asyncio.run(go())
            self.assertEqual(items[-1][1]["outcome"], "exhausted")

            kinds = [r["kind"] for r in journal.records(d, UNIT)]
            self.assertEqual(kinds, ["start", "attempt", "end"])
            [attempt] = journal.records(d, UNIT, kind="attempt")
            [end] = journal.records(d, UNIT, kind="end")
            self.assertEqual(attempt["turns"], end["turns"])
            self.assertEqual(attempt["cost_usd"], end["cost_usd"])
            self.assertEqual(attempt["turns"], 121)
            self.assertEqual(attempt["session_id"], "s-ceiling")
            self.assertEqual(attempt["branch"], "main")
            self.assertIsNone(attempt.get("snapshot_errors"))

    def test_an_exception_after_the_session_event_keeps_the_session_id_with_null_cost(self):
        class DiesMidStream:
            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                yield ("session", "s-dead")
                raise RuntimeError("verify_0019: the stream broke")

        with tempfile.TemporaryDirectory() as d:
            repo = _git_repo(Path(d))
            make_unit(Path(d), intent_md="Status: accepted.\nI")
            journal = Journal(d, d)
            r = Runner(sessions=DiesMidStream(), journal=journal)

            async def go():
                return [
                    ev
                    async for ev in r.run(
                        workspace=d,
                        directory=Path(d) / ".cos" / UNIT,
                        journal_key=d,
                        unit=UNIT,
                        stage="impl",
                        artifact="impl.md",
                        mode="manual",
                        cwd=str(repo),
                    )
                ]

            items = asyncio.run(go())
            self.assertEqual(items[-1][1]["outcome"], "failed")
            [attempt] = journal.records(d, UNIT, kind="attempt")
            self.assertEqual(attempt["session_id"], "s-dead")
            self.assertIsNone(attempt["turns"])
            self.assertIsNone(attempt["cost_usd"])
            self.assertEqual(attempt["error"]["type"], "RuntimeError")

    def test_a_git_failure_leaves_the_outcome_and_end_record_unchanged(self):

        class HitCeiling:
            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                await _submits(kw)
                yield (
                    "done",
                    {
                        "session_id": "s-r3",
                        "cost": {"turns": 5, "cost_usd": 0.1},
                        "terminal_reason": "max_turns",
                    },
                )

        with tempfile.TemporaryDirectory() as d:
            repo = _git_repo(Path(d))
            make_unit(Path(d), intent_md="Status: accepted.\nI")
            journal = Journal(d, d)
            r = Runner(sessions=HitCeiling(), journal=journal)

            async def go():
                return [
                    ev
                    async for ev in r.run(
                        workspace=d,
                        directory=Path(d) / ".cos" / UNIT,
                        journal_key=d,
                        unit=UNIT,
                        stage="impl",
                        artifact="impl.md",
                        mode="manual",
                        cwd=str(repo),
                    )
                ]

            with mock.patch.object(gitops, "log_range", side_effect=gitops.GitError("boom")):
                items = asyncio.run(go())
            self.assertEqual(items[-1][1]["outcome"], "exhausted")
            [end] = journal.records(d, UNIT, kind="end")
            self.assertEqual(end["outcome"], "exhausted")
            self.assertEqual(end["turns"], 5)
            [attempt] = journal.records(d, UNIT, kind="attempt")
            self.assertIsNone(attempt["commits"])
            self.assertIn("commits: boom", attempt["snapshot_errors"][0])

    def test_a_done_step_writes_no_attempt_record(self):
        class Replies:
            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                yield ("chunk", "# Spec: x\nStatus: accepted.\n")
                await _submits(kw)
                yield ("done", {"session_id": "s-ok", "cost": {"turns": 1, "cost_usd": 0.01}})

        with tempfile.TemporaryDirectory() as d:
            make_unit(Path(d), intent_md="Status: accepted.\nI")
            journal = Journal(d, d)
            r = Runner(sessions=Replies(), journal=journal)

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
                    )
                ]

            asyncio.run(go())
            self.assertEqual(journal.records(d, UNIT, kind="attempt"), [])

    def test_last_attempt_is_recorded_in_the_start_record_when_given(self):
        class Replies:
            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                self.seen_prompt = text
                yield ("chunk", "# Spec: x\nStatus: accepted.\n")
                await _submits(kw)
                yield ("done", {"session_id": "s-ok", "cost": {}})

        probe = Replies()
        with tempfile.TemporaryDirectory() as d:
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
                        last_attempt="PREVIOUS-ATTEMPT-TEXT",
                    )
                ]

            asyncio.run(go())
            self.assertIn("PREVIOUS-ATTEMPT-TEXT", probe.seen_prompt)
            [start] = journal.records(d, UNIT, kind="start")
            self.assertIn("last-attempt", start["envelope"])

    def test_snapshot_on_a_non_git_directory_names_the_reason_and_still_tries_the_excerpt(self):
        with tempfile.TemporaryDirectory() as d:
            fields, pending = asyncio.run(snapshot(d, ""))
        self.assertIsNone(pending)
        self.assertIsNone(fields["head"])
        self.assertIn(f"git: {d} is not a git checkout", fields["snapshot_errors"])
        self.assertIn("excerpt: no session id", fields["snapshot_errors"][-1])


class ASpikeThatTouchesTheWorktreeFails(unittest.TestCase):
    """The spike writes its scratch; a change to the worktree fails it."""

    def run_spike(self, touch):
        class Fake:
            def __init__(self):
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
                for name, target in (
                    ("scratch", f"{cwd}/p.py"),
                    ("tree", f"{tree}/p.py"),
                    ("unit", f"{directory}/spec.md"),
                ):
                    said = await asks(gate, "Write", {"file_path": target})
                    self.answers[name] = "deny" if said else "allow"
                read = await asks(gate, "Read", {"file_path": f"{tree}/a.txt"})
                self.answers["read-tree"] = "deny" if read else "allow"
                touch(tree)
                yield ("chunk", SPIKE_REPLY)
                await _submits(kw)
                yield ("done", {"session_id": "s-spike", "cost": {}})

        with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as scratch:
            tree = _git_repo(Path(ws))
            directory = make_unit(
                Path(ws) / "store", intent_md="Status: accepted.\nI", spec_md="Status: accepted.\nS"
            )
            fake = Fake()
            r = Runner(sessions=fake, journal=None)

            async def go():
                return [
                    ev
                    async for ev in r.run(
                        workspace=ws,
                        directory=directory,
                        journal_key=ws,
                        unit=UNIT,
                        stage="spike",
                        artifact="spike.md",
                        mode="autonomous",
                        cwd=scratch,
                        watch=str(tree),
                    )
                ]

            _, final = asyncio.run(go())[-1]
            return final, fake.answers, (directory / "spike.md").exists()

    def test_an_untouched_worktree_gets_its_spike_md(self):
        final, answers, written = self.run_spike(lambda tree: None)
        self.assertEqual(final["outcome"], "done", final)
        self.assertTrue(written)
        self.assertEqual(
            answers,
            {
                "scratch": "allow",
                "tree": "deny",
                "unit": "deny",
                "read-tree": "allow",
            },
        )

    def test_a_file_left_in_the_worktree_fails_the_step_and_writes_nothing(self):
        final, _, written = self.run_spike(lambda tree: (tree / "probe.py").write_text("x"))
        self.assertEqual(final["outcome"], "failed")
        self.assertFalse(written)
        self.assertIn("the worktree changed during spike", final["error"])
        self.assertIn("?? probe.py", final["error"])

    def test_a_commit_in_the_worktree_fails_the_step(self):
        def commit(tree):
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
                    "x",
                ],
                cwd=tree,
                check=True,
            )

        final, _, written = self.run_spike(commit)
        self.assertEqual(final["outcome"], "failed")
        self.assertFalse(written)
        self.assertIn("HEAD ", final["error"])


PROGRESS = (
    "# Spike: x\nSpec: spec.md. Author: ᛈ Perthro. Round: 1. Status: accepted.\n\n"
    "## U1\n\nVerdict: holds.\n\n```\n$ python -c 'print(1)'\n1\n```\n\n"
    "## U2\n\nĐã chạy probe đầu; còn thiếu phép đo thứ hai.\n"
)


class ASpikeLeavesWhatItMeasured(unittest.TestCase):
    """A spike whose reply is not an artifact gets `spike.md` from the progress file it kept in
    `cwd`, unless a Stop or a changed worktree withholds it, and its `end` row says which source
    wrote it."""

    def run_spike(
        self,
        progress=PROGRESS,
        reply="Tôi hết lượt ở U2.",
        terminal="max_turns",
        touch=None,
        raise_after=None,
        answers=None,
        running=None,
        tree_fails_from=None,
    ):
        # `tree_fails_from`: the 1-based reading of the worktree from which git fails.
        from coscc.runner import step as runner

        real_tree_state = runner._tree_state
        readings = []

        async def tree_state(path):
            readings.append(path)
            if tree_fails_from is not None and len(readings) >= tree_fails_from:
                raise RunError("could not read the worktree's state: git broke")
            return await real_tree_state(path)

        class Fake:
            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                if progress is not None:
                    (Path(cwd) / "spike.md").write_text(progress, encoding="utf-8")
                if touch is not None:
                    touch(tree)
                if raise_after is not None:
                    raise raise_after
                if running is not None:
                    # A Stop that came before the seal, with no cancel behind it.
                    running.stop_requested, running.stopped_by = True, "Lan"
                yield ("chunk", reply)
                await _submits(kw)
                yield (
                    "done",
                    {
                        "session_id": "s-spike",
                        "terminal_reason": terminal,
                        "cost": {"turns": 81, "cost_usd": 4.5},
                    },
                )

        with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as scratch:
            tree = _git_repo(Path(ws))
            directory = make_unit(
                Path(ws) / "store", intent_md="Status: accepted.\nI", spec_md="Status: accepted.\nS"
            )
            if answers is not None:
                (directory / "spike.md").write_text(answers, encoding="utf-8")
            journal = Journal(ws, ws)
            r = Runner(sessions=Fake(), journal=journal)

            async def go():
                return [
                    ev
                    async for ev in r.run(
                        workspace=ws,
                        directory=directory,
                        journal_key=ws,
                        unit=UNIT,
                        stage="spike",
                        artifact="spike.md",
                        mode="autonomous",
                        cwd=scratch,
                        watch=str(tree),
                        **({"running": running} if running is not None else {}),
                    )
                ]

            with mock.patch("coscc.runner.step._tree_state", tree_state):
                _, final = asyncio.run(go())[-1]
            target = directory / "spike.md"
            written = target.read_bytes() if target.exists() else None
            [end] = [x for x in journal.records() if x["kind"] == "end"]
            attempts = [x for x in journal.records() if x["kind"] == "attempt"]
            return final, written, end, attempts

    def test_a_the_ceiling_writes_spike_md_from_the_progress_file(self):
        final, written, end, attempts = self.run_spike()
        self.assertEqual(written, PROGRESS.encode("utf-8"))
        self.assertEqual(final["outcome"], "exhausted")
        self.assertEqual((end["outcome"], end["spike_md"]), ("exhausted", "progress"))
        self.assertIsNone(end["artifact"])
        self.assertIn("spike.md written from the progress file", end["detail"])
        self.assertEqual(len(attempts), 1)

    def test_b_a_changed_worktree_withholds_it(self):
        final, written, end, _ = self.run_spike(
            touch=lambda tree: (tree / "probe.py").write_text("x")
        )
        self.assertIsNone(written)
        self.assertEqual(end["spike_md"], "withheld")
        self.assertIn("the worktree changed during spike", final["error"])

    def test_c_a_stop_before_the_seal_withholds_it(self):
        from coscc.agent import steps

        running = steps.Running(workspace="w", unit=UNIT, stage="spike", started_at="t")
        final, written, end, _ = self.run_spike(running=running)
        self.assertIsNone(written)
        self.assertEqual((final["outcome"], end["outcome"]), ("stopped", "stopped"))
        self.assertEqual(end["spike_md"], "withheld")

    def test_c_a_stop_during_the_session_withholds_it(self):
        from coscc.agent import steps

        release = asyncio.Event()

        class Waits:
            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                (Path(cwd) / "spike.md").write_text(PROGRESS, encoding="utf-8")
                yield ("chunk", "đang đo ")
                await release.wait()
                await _submits(kw)
                yield ("done", {"session_id": "s", "terminal_reason": "max_turns", "cost": {}})

        with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as scratch:
            tree = _git_repo(Path(ws))
            directory = make_unit(
                Path(ws) / "store", intent_md="Status: accepted.\nI", spec_md="Status: accepted.\nS"
            )
            running = steps.Running(ws, UNIT, "spike", "")
            journal = Journal(ws, ws)
            r = Runner(sessions=Waits(), journal=journal)

            async def go():
                out = []

                async def drive():
                    async for item in r.run(
                        workspace=ws,
                        directory=directory,
                        journal_key=ws,
                        unit=UNIT,
                        stage="spike",
                        artifact="spike.md",
                        mode="manual",
                        cwd=scratch,
                        watch=str(tree),
                        running=running,
                    ):
                        out.append(item)

                running.task = asyncio.create_task(drive())
                while not out:
                    await asyncio.sleep(0)
                await AStoppedStepEndsStopped.stop(running, None)
                await running.task
                return out

            out = asyncio.run(go())
            self.assertEqual(out[-1][1]["outcome"], "stopped")
            self.assertFalse((directory / "spike.md").exists())
            [end] = [x for x in journal.records() if x["kind"] == "end"]
            self.assertEqual((end["outcome"], end["spike_md"]), ("stopped", "withheld"))

    def test_d_no_progress_file_is_none(self):
        final, written, end, _ = self.run_spike(progress=None)
        self.assertIsNone(written)
        self.assertEqual((final["outcome"], end["spike_md"]), ("exhausted", "none"))

    def test_e_a_progress_file_with_no_title_is_unusable(self):
        final, written, end, _ = self.run_spike(progress="## U1\n\nChưa đo.\n")
        self.assertIsNone(written)
        self.assertEqual((final["outcome"], end["spike_md"]), ("exhausted", "unusable"))
        self.assertIn("the progress file was not an artifact", end["detail"])

    def test_f_a_usable_reply_wins_over_the_progress_file(self):
        final, written, end, attempts = self.run_spike(reply=SPIKE_REPLY, terminal="success")
        self.assertEqual(written, SPIKE_REPLY.encode("utf-8"))
        self.assertEqual((final["outcome"], end["spike_md"]), ("done", "reply"))
        self.assertEqual(attempts, [])

    def test_a_worktree_not_read_before_the_step_is_unchecked(self):
        # No reading to compare with, so nothing is written -- and it is the app's failure, not a
        # Stop's or the spike's.
        final, written, end, _ = self.run_spike(tree_fails_from=1)
        self.assertIsNone(written)
        self.assertEqual((final["outcome"], end["spike_md"]), ("failed", "unchecked"))
        self.assertIn("the worktree's state was not read before the step", end["detail"])

    def test_a_worktree_not_read_again_after_the_step_is_unchecked(self):
        # The first two readings (before the session, and on the reply's road) agree; the
        # one `_from_progress` makes fails.
        final, written, end, _ = self.run_spike(tree_fails_from=3)
        self.assertIsNone(written)
        self.assertEqual((final["outcome"], end["spike_md"]), ("exhausted", "unchecked"))
        self.assertIn(
            "spike.md not written from the progress file: could not read the worktree's state: git broke",
            end["detail"],
        )

    def test_a_session_that_broke_after_writing_it_is_progress(self):
        final, written, end, _ = self.run_spike(
            raise_after=RuntimeError("the stream broke"), terminal=""
        )
        self.assertEqual(written, PROGRESS.encode("utf-8"))
        self.assertEqual((final["outcome"], end["spike_md"]), ("failed", "progress"))

    def test_another_stages_end_carries_no_spike_md(self):
        class Fake:
            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                yield ("chunk", "# Plan: x\nStatus: accepted.\n")
                await _submits(kw)
                yield ("done", {"session_id": "s", "cost": {}})

        with tempfile.TemporaryDirectory() as d:
            make_unit(Path(d), intent_md="Status: accepted.\nI", spec_md="Status: accepted.\nS")
            journal = Journal(d, d)
            r = Runner(sessions=Fake(), journal=journal)

            async def go():
                return [
                    ev
                    async for ev in r.run(
                        workspace=d,
                        directory=Path(d) / ".cos" / UNIT,
                        journal_key=d,
                        unit=UNIT,
                        stage="plan",
                        artifact="plan.md",
                        mode="autonomous",
                    )
                ]

            asyncio.run(go())
            [end] = [x for x in journal.records() if x["kind"] == "end"]
            self.assertEqual(end["outcome"], "done")
            self.assertNotIn("spike_md", end)

    def test_a_spike_without_its_opening_gets_no_repair_turn(self):
        # A spike has its progress file instead.
        final, written, end, _ = self.run_spike(terminal="success")
        self.assertEqual(written, PROGRESS.encode("utf-8"))
        self.assertEqual((final["outcome"], end["spike_md"]), ("failed", "progress"))
        self.assertNotIn("opening", end)
        self.assertNotIn("closing", end)


class AStoppedStepEndsStopped(unittest.TestCase):
    """A Stop before the seal ends the step `stopped`, with the name, no cost it never saw, and no
    artifact. After the seal it is refused. A cancel nobody asked for is the app going down, and
    writes no `end`."""

    class Waits:
        """Sends one chunk, then waits for a release that a Stop never gives it."""

        def __init__(self, reply="# Spec: x\nStatus: accepted.\n"):
            self.release = asyncio.Event()
            self.reply = reply
            self.steps = []

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            self.steps.append(kw.get("step"))
            yield ("chunk", "thinking\n")
            await self.release.wait()
            yield ("chunk", self.reply)
            await _submits(kw)
            yield (
                "done",
                {
                    "session_id": "s-1",
                    "terminal_reason": "success",
                    "cost": {"turns": 2, "cost_usd": 0.25},
                },
            )

    def _run(self, d, sessions, stage, artifact, act):
        from coscc.agent import steps

        running = steps.Running(d, UNIT, stage, "")
        journal = Journal(d, d)
        r = Runner(sessions=sessions, journal=journal)

        async def go():
            out = []

            async def drive():
                async for item in r.run(
                    workspace=d,
                    directory=Path(d) / ".cos" / UNIT,
                    journal_key=d,
                    unit=UNIT,
                    stage=stage,
                    artifact=artifact,
                    mode="manual",
                    running=running,
                ):
                    out.append(item)

            running.task = asyncio.create_task(drive())
            while not out:
                await asyncio.sleep(0)
            await act(running, sessions)
            try:
                await running.task
            except asyncio.CancelledError:
                out.append(("cancelled", None))
            return out

        return asyncio.run(go()), journal, running

    @staticmethod
    async def stop(running, sessions):
        # What `Steps.stop_running` sets, with the attempt's `stop_asked_at`, on a step not yet
        # `ending`.
        running.stop_requested, running.stopped_by = True, "Lan"
        await running.handle.close()
        running.task.cancel()

    def _ends(self, journal):
        return [r for r in journal.records() if r["kind"] == "end"]

    def test_a_prose_step_stopped_midway_writes_nothing_and_says_who(self):
        with tempfile.TemporaryDirectory() as d:
            directory = make_unit(Path(d), intent_md="Status: accepted.\nI")
            sessions = self.Waits()
            out, journal, running = self._run(d, sessions, "spec", "spec.md", self.stop)
            self.assertIs(sessions.steps[0], running.handle)
            kind, payload = out[-1]
            self.assertEqual((kind, payload["outcome"]), ("done", "stopped"))
            self.assertEqual(payload["stopped_by"], "Lan")
            self.assertFalse((directory / "spec.md").exists())
            [end] = self._ends(journal)
            self.assertEqual(end["outcome"], "stopped")
            self.assertEqual(end["stopped_by"], "Lan")
            self.assertEqual(end["detail"], "stopped by Lan")
            self.assertIsNone(end["artifact"])
            self.assertTrue(end["cost_unknown"])
            self.assertNotIn("cost_usd", end)
            self.assertNotIn("turns", end)

    def test_a_self_writing_step_stopped_midway_leaves_its_file_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            before = b"# Impl: x\nStatus: draft.\nhalf of it\n"
            directory = make_unit(
                Path(d),
                intent_md="Status: accepted.\nI",
                plan_md="Status: accepted.\nP",
            )
            (directory / "impl.md").write_bytes(before)
            out, journal, _ = self._run(d, self.Waits(), "impl", "impl.md", self.stop)
            self.assertEqual(out[-1][1]["outcome"], "stopped")
            self.assertEqual((directory / "impl.md").read_bytes(), before)
            [end] = self._ends(journal)
            self.assertEqual(end["outcome"], "stopped")

    def test_a_stop_after_the_seal_is_not_honoured_and_the_step_is_done(self):
        # a Stop that reaches a sealed (`ending`) step is no longer refused; the
        # attempt records it (`stop_late`, tested in tests/runner/test_steps.py) and
        # `Steps.stop_running` does not set `stop_requested`, so the runner finishes the step.
        async def release_then_stop(running, sessions):
            sessions.release.set()
            while not running.sealed:
                await asyncio.sleep(0)

        with tempfile.TemporaryDirectory() as d:
            directory = make_unit(Path(d), intent_md="Status: accepted.\nI")
            out, journal, running = self._run(d, self.Waits(), "spec", "spec.md", release_then_stop)
            self.assertTrue(running.sealed)
            self.assertFalse(running.stop_requested)
            self.assertEqual(out[-1][1]["outcome"], "done")
            self.assertTrue((directory / "spec.md").exists())
            [end] = self._ends(journal)
            self.assertEqual((end["outcome"], end["cost_usd"]), ("done", 0.25))

    def test_a_stop_after_the_outcome_is_decided_is_not_honoured_and_the_end_says_failed(self):
        """A Stop that lands while a failed step captures its attempt used to be told "stopped"
        while the `end` said `failed`. The door was closed by then: the step is sealed, so the
        Stop is only recorded on its attempt and never reaches the runner."""
        sealed = []

        class Fails(self.Waits):
            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                yield ("chunk", "thinking ")
                await self.release.wait()
                raise RuntimeError("the CLI died")

        box = []

        async def capture(cwd, session_id):
            sealed.append(box[0].sealed)
            return {}, None

        async def release(running, sessions):
            box.append(running)
            sessions.release.set()

        with tempfile.TemporaryDirectory() as d, mock.patch("coscc.runner.step.snapshot", capture):
            make_unit(Path(d), intent_md="Status: accepted.\nI")
            out, journal, running = self._run(d, Fails(), "spec", "spec.md", release)
            self.assertEqual(sealed, [True])
            self.assertFalse(running.stop_requested)
            self.assertEqual(out[-1][1]["outcome"], "failed")
            self.assertNotIn("stopped_by", out[-1][1])
            [end] = self._ends(journal)
            self.assertEqual(end["outcome"], "failed")
            self.assertNotIn("stopped_by", end)

    def test_a_cancel_with_no_stop_behind_it_writes_no_end(self):
        async def cancel(running, sessions):
            running.task.cancel()

        with tempfile.TemporaryDirectory() as d:
            make_unit(Path(d), intent_md="Status: accepted.\nI")
            out, journal, _ = self._run(d, self.Waits(), "spec", "spec.md", cancel)
            self.assertEqual(out[-1], ("cancelled", None))
            self.assertEqual(self._ends(journal), [])
            self.assertEqual([r for r in journal.records() if r["kind"] == "attempt"], [])


class ADeadStepKeepsItsTurns(unittest.TestCase):
    """A step that dies after three turns ends `failed` or `exhausted` as before, with the turns its
    recorder stored, and no `cost_usd` unless the CLI sent one."""

    class Dies:
        def __init__(self, then):
            self.then = then

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            from claude_agent_sdk import AssistantMessage, TextBlock

            for mid in ("m1", "m2", "m3"):
                kw["step"].recorder.message(
                    AssistantMessage(content=[TextBlock(mid)], model="m", message_id=mid)
                )
            yield ("session", "s-dead")
            if isinstance(self.then, BaseException):
                raise self.then
            await _submits(kw)
            yield ("done", self.then)

    def _run(self, then):
        from coscc.agent import steps
        from coscc.runlog import events
        from coscc.store.db import Data

        with tempfile.TemporaryDirectory() as d:
            repo = _git_repo(Path(d))
            make_unit(Path(d), intent_md="Status: accepted.\nI")
            data = Data(d)
            journal = Journal(d, data)
            running = steps.Running(d, UNIT, "impl", "")
            running.handle.recorder = events.Recorder("r-dead", data, d, d, UNIT, "impl")

            async def go():
                return [
                    ev
                    async for ev in Runner(sessions=self.Dies(then), journal=journal).run(
                        workspace=d,
                        directory=Path(d) / ".cos" / UNIT,
                        journal_key=d,
                        unit=UNIT,
                        stage="impl",
                        artifact="impl.md",
                        mode="manual",
                        cwd=str(repo),
                        running=running,
                    )
                ]

            asyncio.run(go())
            [start] = journal.records(d, UNIT, kind="start")
            [attempt] = journal.records(d, UNIT, kind="attempt")
            [end] = journal.records(d, UNIT, kind="end")
            return start, attempt, end, data.step_turns("r-dead")

    def _no_cost(self, end, attempt, stored):
        self.assertEqual(end["outcome"], "failed")
        self.assertEqual((end["turns"], stored), (3, 3))
        self.assertIs(end["cost_unknown"], True)
        self.assertNotIn("cost_usd", end)
        self.assertNotIn("cli_turns", end)
        self.assertNotIn("turns_from", end)
        self.assertEqual(attempt["turns"], end["turns"])

    def test_an_sdk_error_keeps_the_turns_and_claims_no_cost(self):
        start, attempt, end, stored = self._run(RuntimeError("the stream broke"))
        self._no_cost(end, attempt, stored)
        self.assertEqual(start["pid"], os.getpid())
        self.assertEqual(end["run"], "r-dead")
        self.assertIn("the stream broke", end["detail"])

    def test_a_killed_cli_is_the_same(self):
        from claude_agent_sdk._errors import ProcessError

        start, attempt, end, stored = self._run(
            ProcessError("Command failed with exit code -9", exit_code=-9)
        )
        self._no_cost(end, attempt, stored)
        self.assertIn("exit code -9", end["detail"])

    def test_a_ceiling_keeps_the_clis_count_apart(self):
        start, attempt, end, stored = self._run(
            {
                "session_id": "s-dead",
                "terminal_reason": "error_max_turns",
                "cost": {"turns": 7, "cost_usd": 0.4},
            }
        )
        self.assertEqual(end["outcome"], "exhausted")
        self.assertEqual((end["turns"], end["cli_turns"], end["cost_usd"]), (3, 7, 0.4))
        self.assertNotIn("cost_unknown", end)
        self.assertEqual(attempt["turns"], 3)
        self.assertEqual(attempt["cost_usd"], 0.4)


class AReviewThatRunsOutGetsAClosingTurn(unittest.TestCase):
    """One more turn on the same session, with no tools, when a review's reply could not be written
    because it hit its ceiling."""

    class Closes:
        def __init__(
            self,
            first="Tôi hết lượt.",
            terminal="max_turns",
            closing=None,
            closing_terminal="completed",
            closing_cost=1.40,
            raises=None,
            waits=False,
            stop=None,
        ):
            self.calls = []
            self.first, self.terminal = first, terminal
            self.closing, self.closing_terminal, self.closing_cost = (
                closing,
                closing_terminal,
                closing_cost,
            )
            self.raises, self.waits, self.stop = raises, waits, stop

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            import re

            self.calls.append(
                {"text": text, "session_id": session_id, "max_turns": max_turns, **kw}
            )
            if len(self.calls) == 1:
                if self.stop is not None:
                    self.stop.stop_requested, self.stop.stopped_by = True, "Lan"
                yield ("chunk", self.first)
                await _submits(kw)
                yield (
                    "done",
                    {
                        "session_id": "s1",
                        "terminal_reason": self.terminal,
                        "cost": {"turns": 41, "cost_usd": 1.00},
                    },
                )
                return
            if self.raises is not None:
                raise self.raises
            if self.waits:
                await asyncio.Event().wait()
            head = re.search(r"Reviewed: ([0-9a-f]{40})\. Verdict: incomplete", text).group(1)
            yield ("chunk", self.closing(head) if self.closing else incomplete_reply(head))
            await _submits(kw)
            yield (
                "done",
                {
                    "session_id": "s1",
                    "terminal_reason": self.closing_terminal,
                    "cost": {"turns": 1, "cost_usd": self.closing_cost},
                }
                if self.closing_terminal
                else {"session_id": "s1", "cost": {}},
            )

    def run_review(self, sessions, stage="review", git=True, act=None, stop=False, resume=None):
        from coscc.agent import steps

        with tempfile.TemporaryDirectory() as ws:
            tree = _git_repo(Path(ws)) if git else Path(ws)
            head = (
                subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=tree, capture_output=True, text=True
                ).stdout.strip()
                if git
                else ""
            )
            directory = make_unit(
                Path(ws) / "store",
                intent_md="Status: accepted.\nI",
                plan_md="Status: accepted.\nP",
                impl_md="Status: accepted.\nI",
                pr_md="Status: accepted.\nP",
                review_md=REVIEW_R1,
            )
            running = steps.Running(ws, UNIT, stage, "")
            if stop:
                sessions.stop = running
            journal = Journal(ws, ws)
            r = Runner(sessions=sessions, journal=journal)
            artifact = f"{stage}.md"

            async def go():
                out = []

                async def drive():
                    async for item in r.run(
                        workspace=ws,
                        directory=directory,
                        journal_key=ws,
                        unit=UNIT,
                        stage=stage,
                        artifact=artifact,
                        mode="manual",
                        cwd=str(tree),
                        running=running,
                        **({"resume": resume(head)} if resume is not None else {}),
                    ):
                        out.append(item)

                running.task = asyncio.create_task(drive())
                if act is not None:
                    await act(running, sessions)
                try:
                    await running.task
                except asyncio.CancelledError:
                    out.append(("cancelled", None))
                return out

            out = asyncio.run(go())
            ends = [x for x in journal.records() if x["kind"] == "end"]
            return out, ends, (directory / "review.md").read_text(encoding="utf-8"), head, running

    def test_a_turn_ceiling_reopens_the_session_once_with_no_tools(self):
        sessions = self.Closes()
        out, [end], review, head, running = self.run_review(sessions)
        self.assertEqual(len(sessions.calls), 2)
        first, closing = sessions.calls
        self.assertIsNone(first["session_id"])
        self.assertIs(first["step"], running.handle)
        self.assertEqual(closing["session_id"], "s1")
        self.assertEqual(closing["tools"], [])
        self.assertEqual(closing["max_turns"], 1)
        self.assertEqual(closing["gate"].grant, policy.Grant())
        self.assertIsNot(closing["step"], running.handle)
        self.assertIsNone(closing["step"].recorder)
        self.assertIn(f"Reviewed: {head}. Verdict: incomplete.", closing["text"])
        self.assertIn("## Round 2", closing["text"])
        # The round is appended, and everything above it is byte for byte what it was.
        self.assertTrue(review.startswith("# Review: x\nSpec: spec.md. Author: t.\n"))
        self.assertIn(REVIEW_R1.split("\n\n", 1)[1].rstrip(), review)
        self.assertIn(f"## Round 2\n\nReviewed: {head}. Verdict: incomplete.", review)
        self.assertEqual(out[-1][1]["outcome"], "exhausted")
        self.assertEqual((end["outcome"], end["review_md"]), ("exhausted", "incomplete"))
        self.assertEqual(end["cost_usd"], 1.4)
        self.assertEqual(end["closing"], {"terminal": "completed", "turns": 1, "cost_usd": 0.4})
        self.assertIn("review.md: Round 2 incomplete, written by the closing turn", end["detail"])

    def next_of(self, store: Path, meta, key: str) -> dict:
        import json

        from tests.units.test_meta import loop

        snap = meta.snapshot(key, {"proj": key})
        done = loop(
            "--root", str(store), "--state", "-", "next", UNIT, input=json.dumps(snap), check=False
        )
        return json.loads(done.stdout)

    def test_the_incomplete_round_sends_the_unit_to_write_review_again(self):
        # From the closing turn to `next`: the round the closing turn wrote is recorded as the
        # review going back to draft, so the loop asks for the review again and not for impl.
        from coscc.agent.sessions import Sessions
        from coscc.config import Config
        from coscc.http.app import Core
        from tests.units.test_meta import seed

        out, [end], review, head, running = self.run_review(self.Closes())
        self.assertEqual(end["review_md"], "incomplete")
        done = out[-1][1]
        self.assertEqual((done["outcome"], done["incomplete_round"]), ("exhausted", 2))
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "store"
            make_unit(
                store,
                intent_md="# Intent: x\n",
                spec_md="# Spec: x\n",
                plan_md="# Plan: x\n",
                impl_md="# Impl: x\n",
                pr_md="# PR: fix(0009): x\nPR: https://github.com/o/r/pull/9\n",
                review_md=review,
            )
            config = Config(
                workspaces=(), working_dir=str(Path(d) / "work"), data_dir=str(Path(d) / "data")
            )
            core = Core(config, Sessions(config))
            meta, key = core.ws.unit_meta(), str(Path(d) / "work" / "proj")
            seed(
                meta,
                key,
                UNIT,
                {
                    "intent.md": "accepted",
                    "spec.md": "accepted",
                    "plan.md": "accepted",
                    "impl.md": "accepted",
                    "pr.md": "accepted",
                    "review.md": "changes-requested",
                },
                type="fix",
            )
            self.assertNotIn("review-incomplete", self.next_of(store, meta, key)["reasons"])
            self.assertEqual(asyncio.run(core.answers.ingest(key, UNIT, done, "review.md")), {})
            [row] = meta.history.transitions(key, UNIT, "review.md")[-1:]
            self.assertEqual(
                (row["to_state"], row["guard"], row["authority"], row["source"]),
                ("draft", "incomplete-round", "code", "run:review"),
            )
            after = self.next_of(store, meta, key)
            self.assertEqual(after["reasons"], ["review-incomplete"])
            self.assertIn("review round 2 is incomplete — write-review again", after["action"])

    def test_a_review_that_wrote_no_incomplete_round_is_not_moved(self):
        from coscc.agent.sessions import Sessions
        from coscc.config import Config
        from coscc.http.app import Core

        with tempfile.TemporaryDirectory() as d:
            config = Config(
                workspaces=(), working_dir=str(Path(d) / "work"), data_dir=str(Path(d) / "data")
            )
            core = Core(config, Sessions(config))
            key = str(Path(d) / "work" / "proj")
            done = {"outcome": "exhausted", "stage": "review", "unit": UNIT}
            self.assertEqual(asyncio.run(core.answers.ingest(key, UNIT, done, "review.md")), {})
            self.assertEqual(core.ws.unit_meta().history.transitions(key, UNIT, "review.md"), [])

    def test_a_resumed_closing_turn_resumes_that_turn_not_the_step(self):
        # An update paused the closing turn. The main reply is not asked again; the turn goes on
        # from its safe point with the message, and the step ends once.
        sessions = self.Closes()
        sessions.calls.append({"text": "the main reply, before the update"})

        # A closing turn comes only after a ceiling, so its row has used both of them up, and the
        # turn is taken up all the same, with no spent budget passed.
        def resume(head):
            return {
                "suspend_id": "c1",
                "session_id": "s1",
                "safe_uuid": "u7",
                "message": f"MSG Reviewed: {head}. Verdict: incomplete",
                "pieces": ["Tôi hết lượt."],
                "api_calls": 500,
                "spent_usd": 50.0,
                "owner": {
                    "kind": "closing",
                    "start_at": "t0",
                    "head": head,
                    "main_terminal": "max_turns",
                    "main_cost": {"turns": 41, "cost_usd": 1.0},
                },
            }

        out, [end], review, head, _ = self.run_review(sessions, resume=resume)
        [closing] = sessions.calls[1:]
        self.assertEqual((closing["session_id"], closing["resume_at"]), ("s1", "u7"))
        self.assertEqual(closing["max_turns"], 1)
        self.assertIsNone(closing["max_budget_usd"])
        self.assertTrue(closing["text"].startswith("MSG "))
        self.assertIn(f"## Round 2\n\nReviewed: {head}. Verdict: incomplete.", review)
        self.assertEqual((end["outcome"], end["review_md"]), ("exhausted", "incomplete"))
        self.assertNotIn("was not reached again", end["detail"])
        self.assertNotIn("not resumed", end["detail"])
        self.assertIn("review.md: Round 2 incomplete, written by the closing turn", end["detail"])

    def test_the_closing_turns_gate_grants_nothing(self):
        sessions = self.Closes()
        self.run_review(sessions)
        gate = sessions.calls[1]["gate"]
        self.assertEqual(gate.grant, policy.Grant())
        self.assertIn(policy.HELD, asyncio.run(asks(gate, "mcp__cos__submit", {})))

    def test_a_budget_ceiling_on_the_closing_turn_still_writes_a_round(self):
        _, [end], review, _, _ = self.run_review(
            self.Closes(terminal="budget_exhausted", closing_terminal="budget_exhausted")
        )
        self.assertEqual(end["review_md"], "incomplete")
        self.assertEqual(end["closing"]["terminal"], "budget_exhausted")
        self.assertIn("Verdict: incomplete.", review)

    def test_a_review_that_finished_gets_no_closing_turn(self):
        sessions = self.Closes(first=incomplete_reply("a" * 40, verdict="pass"), terminal="success")
        _, [end], _, _, _ = self.run_review(sessions)
        self.assertEqual(len(sessions.calls), 1)
        self.assertEqual((end["outcome"], end["review_md"]), ("done", "round"))
        self.assertNotIn("closing", end)

    def test_a_stop_gets_no_closing_turn(self):
        sessions = self.Closes()
        _, [end], review, _, _ = self.run_review(sessions, stop=True)
        self.assertEqual(len(sessions.calls), 1)
        self.assertEqual((end["outcome"], end["review_md"]), ("stopped", "withheld"))
        self.assertEqual(review, REVIEW_R1)

    def test_another_stage_gets_no_closing_turn(self):
        sessions = self.Closes()
        _, [end], _, _, _ = self.run_review(sessions, stage="plan")
        self.assertEqual(len(sessions.calls), 1)
        self.assertEqual(end["outcome"], "exhausted")
        self.assertNotIn("review_md", end)
        self.assertNotIn("closing", end)

    def test_no_head_gets_no_closing_turn(self):
        sessions = self.Closes()
        _, [end], review, _, _ = self.run_review(sessions, git=False)
        self.assertEqual(len(sessions.calls), 1)
        self.assertEqual(end["review_md"], "none")
        self.assertEqual(review, REVIEW_R1)

    def test_a_full_round_from_the_closing_turn_is_not_written(self):
        sessions = self.Closes(closing=lambda head: incomplete_reply(head, verdict="pass"))
        _, [end], review, _, _ = self.run_review(sessions)
        self.assertEqual(review, REVIEW_R1)
        self.assertEqual(end["review_md"], "none")
        self.assertIn("closing", end)
        self.assertIn("does not open with `Reviewed: ", end["detail"])

    def test_a_closing_turn_that_breaks_still_leaves_an_end(self):
        sessions = self.Closes(raises=RuntimeError("the CLI died"))
        _, [end], review, _, _ = self.run_review(sessions)
        self.assertEqual(review, REVIEW_R1)
        self.assertEqual((end["outcome"], end["review_md"]), ("exhausted", "none"))
        self.assertTrue(end["closing"]["cost_unknown"])
        self.assertEqual(end["cost_usd"], 1.0)

    def test_a_closing_turn_with_no_result_keeps_the_main_cost(self):
        _, [end], _, _, _ = self.run_review(self.Closes(closing_terminal=""))
        self.assertEqual(end["review_md"], "incomplete")
        self.assertEqual(end["closing"], {"cost_unknown": True})
        self.assertEqual(end["cost_usd"], 1.0)

    def test_a_closing_turn_that_hangs_is_cut_at_the_timeout(self):
        with mock.patch("coscc.runner.step.CLOSING_TIMEOUT", 0.05):
            _, [end], review, _, _ = self.run_review(self.Closes(waits=True))
        self.assertEqual(end["review_md"], "none")
        self.assertTrue(end["closing"]["cost_unknown"])
        self.assertEqual(review, REVIEW_R1)

    def test_the_app_going_down_during_the_closing_turn_writes_no_end(self):
        async def cancel_in_closing(running, sessions):
            while len(sessions.calls) < 2:
                await asyncio.sleep(0)
            await asyncio.sleep(0)
            running.task.cancel()

        out, ends, review, _, _ = self.run_review(self.Closes(waits=True), act=cancel_in_closing)
        self.assertEqual(out[-1], ("cancelled", None))
        self.assertEqual(ends, [])
        self.assertEqual(review, REVIEW_R1)


REPAIRED_PLAN = (
    "# Plan: x\nIntent: intent.md. Spec: spec.md. Author: t. Status: accepted. Impl: routine.\n\n"
    "## Order of work\n\nPHẦN-SỬA\n"
)
UNOPENED_PLAN = "## Order of work\n\nPHẦN-ĐẦU\n"


class AReplyWithoutItsOpeningGetsOneRepairTurn(unittest.TestCase):
    """One more turn on the same session, with no tools, when a prose step's reply was refused for
    its opening alone; written by the road every reply takes."""

    class Repairs:
        def __init__(
            self,
            first=UNOPENED_PLAN,
            repair=REPAIRED_PLAN,
            terminal="success",
            repair_terminal="completed",
            raises=None,
            waits=False,
            session="s1",
            tries_tool=False,
        ):
            self.calls = []
            self.first, self.repair, self.terminal = first, repair, terminal
            self.repair_terminal, self.raises, self.waits = repair_terminal, raises, waits
            self.session, self.tries_tool = session, tries_tool

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            self.calls.append(
                {"text": text, "session_id": session_id, "max_turns": max_turns, **kw}
            )
            if len(self.calls) == 1:
                yield ("chunk", self.first)
                await _submits(kw, **getattr(self, "obj", {}))
                yield (
                    "done",
                    {
                        "session_id": self.session,
                        "terminal_reason": self.terminal,
                        "cost": {"turns": 12, "cost_usd": 2.0},
                    },
                )
                return
            if self.raises is not None:
                raise self.raises
            if self.waits:
                await asyncio.Event().wait()
            if self.tries_tool:
                await asks(kw["gate"], "mcp__x__y", {})
                yield ("tool", "mcp__x__y")
            yield ("chunk", self.repair)
            await _submits(kw)
            yield (
                "done",
                {
                    "session_id": self.session,
                    "terminal_reason": self.repair_terminal,
                    "cost": {"turns": 1, "cost_usd": 3.1},
                },
            )

    def go(self, sessions, stage="plan", existing=None, act=None, running=True, resume=None):
        from coscc.agent import steps

        with tempfile.TemporaryDirectory() as d:
            directory = make_unit(
                Path(d),
                intent_md="Status: accepted.\nI",
                spec_md="Status: accepted.\nS",
                **({"review_md": REVIEW_R1} if stage == "review" else {}),
            )
            if existing is not None:
                (directory / f"{stage}.md").write_bytes(existing)
            handle = steps.Running(d, UNIT, stage, "") if running else None
            journal = Journal(d, d)
            r = Runner(sessions=sessions, journal=journal)

            async def main():
                out = []

                async def drive():
                    async for item in r.run(
                        workspace=d,
                        directory=directory,
                        journal_key=d,
                        unit=UNIT,
                        stage=stage,
                        artifact=f"{stage}.md",
                        mode="autonomous",
                        **({"running": handle} if handle is not None else {}),
                        **({"resume": resume} if resume is not None else {}),
                    ):
                        out.append(item)

                task = asyncio.create_task(drive())
                if handle is not None:
                    handle.task = task
                if act is not None:
                    await act(task, sessions)
                try:
                    await task
                except asyncio.CancelledError:
                    out.append(("cancelled", None))
                return out

            out = asyncio.run(main())
            records = journal.records()
            target = directory / f"{stage}.md"
            written = target.read_bytes() if target.exists() else None
            return out, [x for x in records if x["kind"] == "end"], records, written, handle

    def test_a_reply_without_its_opening_gets_one_repair_turn_on_its_session(self):
        sessions = self.Repairs()
        _, _, _, _, handle = self.go(sessions)
        self.assertEqual(len(sessions.calls), 2)
        first, repair = sessions.calls
        self.assertIsNone(first["session_id"])
        self.assertIs(first["step"], handle.handle)
        self.assertEqual(repair["session_id"], "s1")
        self.assertEqual(repair["tools"], [])
        self.assertEqual(repair["max_turns"], 1)
        self.assertIsNot(repair["step"], handle.handle)
        self.assertIsNone(repair["step"].recorder)
        self.assertIn("no `# Plan:` title", repair["text"])
        self.assertIn("the whole of `plan.md`", repair["text"])

    def test_the_repair_turn_grants_nothing_and_counts_what_it_refused(self):
        sessions = self.Repairs(tries_tool=True)
        _, [end], _, _, _ = self.go(sessions)
        self.assertEqual(end["denials"], 1)
        self.assertEqual(end["denied"], [f"mcp__x__y: {policy.HELD}: mcp__x__y is not one"])
        self.assertEqual(sessions.calls[1]["gate"].grant, policy.Grant())

    def _paused_repair(self, spent_usd):
        # An update paused the repair turn; its main reply is the pieces before it.
        return {
            "suspend_id": "o1",
            "session_id": "s1",
            "safe_uuid": "u3",
            "message": "MSG go on",
            "pieces": [UNOPENED_PLAN],
            "api_calls": 500,
            "spent_usd": spent_usd,
            "owner": {
                "kind": "opening",
                "start_at": "t0",
                "main_terminal": "success",
                "main_cost": {"turns": 12, "cost_usd": 2.0},
            },
        }

    def test_a_resumed_repair_turn_resumes_that_turn_past_the_turns_used(self):
        # The main reply's turns are no ceiling on the one repair turn.
        sessions = self.Repairs()
        sessions.calls.append({"text": "the main reply, before the update"})
        _, [end], _, written, _ = self.go(sessions, resume=self._paused_repair(1.0))
        # The object the first process's channel held went with it, so the step asks for it again on
        # its own session after the repair.
        [repair, again] = sessions.calls[1:]
        self.assertIn("without handing back its object", again["text"])
        self.assertEqual(
            (repair["session_id"], repair["resume_at"], repair["text"]), ("s1", "u3", "MSG go on")
        )
        self.assertEqual(repair["max_turns"], 1)
        self.assertGreater(repair["max_budget_usd"], 0)
        self.assertTrue(written.decode("utf-8").startswith(REPAIRED_PLAN))
        self.assertEqual((end["outcome"], end["opening"]), ("done", "repaired"))

    def test_a_repaired_reply_is_written_and_the_step_ends_done(self):
        section = (
            "## Answers\n\n### Câu 1\nAnswered by: Lan. Date: 2026-09-26. Via: product.\n\ncó\n"
        )
        existing = ("# Plan: x\nIntent: i. Status: draft.\n\nCŨ\n\n" + section).encode("utf-8")
        out, [end], records, written, _ = self.go(self.Repairs(), existing=existing)
        text = written.decode("utf-8")
        self.assertTrue(text.startswith(REPAIRED_PLAN), text[:120])
        self.assertNotIn("PHẦN-ĐẦU", text)
        # The reply is the file: what the old one held under `## Answers` is a row, not carried.
        self.assertNotIn("CŨ", text)
        self.assertEqual(
            (end["outcome"], end["opening"], end["artifact"]), ("done", "repaired", "plan.md")
        )
        self.assertTrue(
            end["opening_reason"].startswith("plan.md lacks its opening:"), end["opening_reason"]
        )
        self.assertEqual(end["closing"], {"terminal": "completed", "turns": 1, "cost_usd": 1.1})
        self.assertEqual(end["cost_usd"], 3.1)
        self.assertNotIn("detail", {k for k, v in end.items() if v})
        self.assertEqual([x for x in records if x["kind"] == "attempt"], [])
        self.assertEqual(out[-1][1]["outcome"], "done")

    def test_the_app_going_down_during_the_repair_turn_writes_no_end(self):
        async def cancel_in_repair(task, sessions):
            while len(sessions.calls) < 2:
                await asyncio.sleep(0)
            await asyncio.sleep(0)
            task.cancel()

        out, ends, _, written, _ = self.go(self.Repairs(waits=True), act=cancel_in_repair)
        self.assertEqual(out[-1], ("cancelled", None))
        self.assertEqual(ends, [])
        self.assertIsNone(written)


class TheOtherTwoWritesAreCheckedTheSame(unittest.TestCase):
    """A spike's progress file and a review's closing turn are held to the opening a reply is, and
    an opening that fails writes nothing."""

    def test_a_progress_file_with_no_title_is_unusable(self):
        untitled = PROGRESS.split("\n", 1)[1]
        final, written, end, _ = ASpikeLeavesWhatItMeasured.run_spike(self, progress=untitled)
        self.assertIsNone(written)
        self.assertEqual((final["outcome"], end["spike_md"]), ("exhausted", "unusable"))
        self.assertIn("spike.md lacks its opening: no `# Spike:` title", end["detail"])

    def test_a_closing_round_with_no_title_is_not_written(self):
        def untitled(head):
            return incomplete_reply(head).split("\n", 1)[1]

        closes = AReviewThatRunsOutGetsAClosingTurn.Closes(closing=untitled)
        _, [end], review, _, _ = AReviewThatRunsOutGetsAClosingTurn.run_review(self, closes)
        self.assertEqual(review, REVIEW_R1)
        self.assertEqual((end["outcome"], end["review_md"]), ("exhausted", "none"))
        self.assertIn("review.md lacks its opening: no `# Review:` title", end["detail"])

    def test_a_refused_write_leaves_the_file_and_its_answers_byte_for_byte(self):
        from coscc.runner.attempt import _write_artifact

        with tempfile.TemporaryDirectory() as d:
            directory = make_unit(Path(d))
            before = (
                "# Plan: x\nIntent: i. Status: draft.\n\nBODY\n\n## Answers\n\n"
                "### Câu 1\nAnswered by: Lan. Date: 2026-09-26. Via: product.\n\ncó\n"
            ).encode("utf-8")
            (directory / "plan.md").write_bytes(before)
            with self.assertRaises(RunError) as caught:
                _write_artifact(
                    directory, "plan.md", "## Order of work\n\nStatus: accepted.\n", blocks=2
                )
            self.assertIn("(the session replied in 2 blocks)", str(caught.exception))
            self.assertEqual((directory / "plan.md").read_bytes(), before)


class AnAnswerCutAtItsCeilingIsNotWritten(unittest.TestCase):
    """A session that wrote a title and a header, called a tool and ran out of turns left a draft,
    or the first of its pieces."""

    class Cut:
        def __init__(self, *said, terminal="max_turns"):
            self.said, self.terminal = said, terminal

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            for kind, payload in self.said:
                yield (kind, payload)
            await _submits(kw)
            yield ("done", {"session_id": "s-1", "terminal_reason": self.terminal, "cost": {}})

    class DraftsThenRunsOut(AReviewThatRunsOutGetsAClosingTurn.Closes):
        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            async for kind, payload in super().stream(cwd, text, session_id, max_turns, **kw):
                yield (kind, payload)
                if kind == "chunk" and len(self.calls) == 1:
                    yield ("tool", "Read")

    def test_a_titled_piece_before_the_last_tool_call_is_not_written(self):
        cut = self.Cut(("chunk", AnAnswerInPiecesIsWrittenWhole.HEAD), ("tool", "Read"))
        final, end, written = AnAnswerInPiecesIsWrittenWhole.go(self, cut)
        self.assertEqual((final["outcome"], end["outcome"]), ("exhausted", "exhausted"))
        # Nothing followed the last call, so the reason is the one `main` gave.
        self.assertIn("the session returned nothing", end["detail"].splitlines()[0])
        self.assertIsNone(written)

    def test_the_same_pieces_below_the_ceiling_are_written(self):
        cut = self.Cut(
            ("chunk", AnAnswerInPiecesIsWrittenWhole.HEAD),
            ("tool", "Read"),
            ("chunk", "PHẦN-ĐUÔI\n"),
            terminal="success",
        )
        final, _, written = AnAnswerInPiecesIsWrittenWhole.go(self, cut)
        self.assertEqual(final["outcome"], "done")
        self.assertIn("PHẦN-ĐẦU\nPHẦN-ĐUÔI\n", written.decode("utf-8"))

    def test_a_whole_answer_after_the_last_tool_call_is_still_written(self):
        cut = self.Cut(
            ("chunk", "Đọc thêm."), ("tool", "Read"), ("chunk", AnAnswerInPiecesIsWrittenWhole.HEAD)
        )
        final, _, written = AnAnswerInPiecesIsWrittenWhole.go(self, cut)
        self.assertEqual(final["outcome"], "exhausted")
        self.assertEqual(written.decode("utf-8"), AnAnswerInPiecesIsWrittenWhole.HEAD)

    def test_a_drafted_round_leaves_the_review_its_closing_turn(self):
        draft = incomplete_reply("c" * 40, verdict="pass")
        sessions = self.DraftsThenRunsOut(first=draft)
        _, [end], review, head, _ = AReviewThatRunsOutGetsAClosingTurn.run_review(self, sessions)
        self.assertEqual(len(sessions.calls), 2)
        self.assertEqual((end["outcome"], end["review_md"]), ("exhausted", "incomplete"))
        self.assertNotIn("Verdict: pass", review)
        self.assertIn(f"## Round 2\n\nReviewed: {head}. Verdict: incomplete.", review)


class ABackgroundRunIsRefusedAndCounted(unittest.TestCase):
    """A step refused its background runs still ends the way it always did, and its `end` row says
    how many there were."""

    class Fake:
        def __init__(self, calls=(), writes=None):
            self.calls, self.writes, self.answers = calls, writes, []

        async def stream(self, cwd, text, session_id=None, max_turns=1, gate=None, **kw):
            for tool_input in self.calls:
                self.answers.append("deny" if await asks(gate, "Bash", tool_input) else "allow")
            if self.writes is not None:
                self.writes()
            yield ("chunk", "# Plan: x\nStatus: accepted.\n")
            await _submits(kw)
            yield ("done", {"session_id": "s-bg", "cost": {}})

    def run_step(self, stage, artifact, fake):
        with tempfile.TemporaryDirectory() as d:
            repo = _git_repo(Path(d))
            directory = make_unit(Path(d), intent_md="Status: accepted.\nI")
            journal = Journal(d, d)
            r = Runner(sessions=fake(directory), journal=journal)

            async def go():
                return [
                    ev
                    async for ev in r.run(
                        workspace=d,
                        directory=directory,
                        journal_key=d,
                        unit=UNIT,
                        stage=stage,
                        artifact=artifact,
                        mode="manual",
                        cwd=str(repo),
                    )
                ]

            asyncio.run(go())
            [end] = journal.records(d, UNIT, kind="end")
            return end

    def test_a_step_refused_two_background_runs_ends_failed_and_counts_them(self):
        calls = (
            {"command": "npm run e2e", "run_in_background": True},
            {"command": "npm run e2e &"},
        )
        fake = self.Fake(calls)
        end = self.run_step("impl", "impl.md", lambda directory: fake)
        self.assertEqual(fake.answers, ["deny", "deny"])
        self.assertEqual(end["outcome"], "failed")
        self.assertEqual((end["background"], end["denials"]), (2, 2))
        self.assertTrue(end["detail"].startswith("the step did not write impl.md"), end["detail"])
