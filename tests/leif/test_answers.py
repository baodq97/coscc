"""Tests for `Answers` in `coscc/leif/answers.py`, split from
`tests/http/test_app.py`."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.bus import Bus
from coscc.config import Config
from coscc.units import states
from coscc.kernel import Invalid
from coscc.http.app import Core
from coscc.agent.sessions import Sessions
from coscc.units import scratch
from tests.http.test_app import create_sync, unit_history
from tests.units.test_submit import a_head, finding, submits as _submits
from tests.http.test_app import use_sessions
from tests.units.test_meta import seed


def state_of(core, cwd, unit, **kw):
    """The unit's state as rows, where the app keeps it."""
    seed(core.ws.unit_meta(), core.ws.key(cwd), unit, **kw)


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
    """Stands in for `gh.run`: records argv, keeps the PR's comments in memory."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[list[str]] = []
        self.comments: list[dict] = []

    async def __call__(self, argv, cwd, stdin):

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
    """A round the app writes is posted; any round can be posted again, once."""

    class Reviews:
        """A review session that adds round 2 to the round `review.md` held."""

        bus = Bus()

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            yield ("chunk", REVIEW_ONE + ROUND_TWO)
            # The round's object, which `review.md` is written from.
            await _submits(
                kw,
                verdict="changes-requested",
                findings=[
                    finding(
                        "F1", "fixed", "high", fixed_in="abcdef2", path="", text="the first thing"
                    ),
                    finding("F2", "open", "low", path="", text="the second thing"),
                ],
            )
            yield ("done", {"session_id": "sess-r", "cost": {}})

    def setUp(self):
        a_head(self, "abcdef2")
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
            self.Reviews(),
        )
        self.made = create_sync(self.core, str(self.repo), "a-problem", "some words")
        self.dir = Path(self.made["path"])
        (self.dir / "pr.md").write_text(
            f"# PR: a problem\nAuthor: t. Status: accepted.\nPR: {PR_URL}\n", encoding="utf-8"
        )
        (self.dir / "review.md").write_text(REVIEW_ONE, encoding="utf-8")
        # What review declares it needs.
        for name in ("intent.md", "impl.md"):
            (self.dir / name).write_text("# x\n", encoding="utf-8")
        self.unit = self.made["unit"]

    def _post(self, gh, n):

        with mock.patch("coscc.git.gh.run", gh):
            return asyncio.run(self.core.answers.post_review_comment(str(self.repo), self.unit, n))

    def _run_review(self, gh):
        from coscc.units import board as board_reader

        async def open_gate(units_root, unit, stage, repo=None, **kw):
            return True, "open: review may proceed"

        async def go():
            out = []
            async for item in self.core.steps.run_step(str(self.repo), self.unit, "review"):
                out.append(item)
            return out

        with (
            mock.patch.object(board_reader, "gate", open_gate),
            mock.patch("coscc.git.gh.run", gh),
        ):
            return asyncio.run(go())

    def _pr_rows(self):
        from coscc.store.journal import Journal

        j = Journal(self.core.config.working_dir, self.core.config.data_dir)
        return j.records(str(self.repo.resolve()), kind="pr-comment")

    def test_a_round_the_step_writes_is_posted_once_with_its_own_text(self):
        gh = FakeGh()
        _, done = self._run_review(gh)[-1]
        self.assertEqual(done["outcome"], "done")
        self.assertEqual(len(gh.posts()), 1)
        body = gh.comments[0]["body"]
        self.assertIn("round 2 of", body.splitlines()[0])
        self.assertIn("- F1 [fixed abcdef2] (none) — high — the first thing", body)
        self.assertEqual([(c["round"], c["state"]) for c in done["comments"]], [(2, "posted")])

    def test_changes_requested_reaches_the_history(self):
        """The round's verdict reaches the history whole, through guard `review-round`, and no
        failure is swallowed on the way."""
        _, done = self._run_review(FakeGh())[-1]
        self.assertNotIn("ingest_error", done)
        rows = [
            r
            for r in unit_history(self.core, str(self.repo), self.unit)["transitions"]
            if r["artifact"] == "review.md"
        ]
        self.assertEqual(
            (rows[-1]["to_state"], rows[-1]["guard"], rows[-1]["authority"]),
            ("changes-requested", "review-round", "agent"),
        )

    def test_the_end_record_counts_the_findings_of_the_added_round(self):
        # Round 2 has two findings, one of them still open.
        from coscc.store.journal import Journal

        self._run_review(FakeGh())
        j = Journal(self.core.config.working_dir, self.core.config.data_dir)
        end = j.records(str(self.repo.resolve()), kind="end")[-1]
        self.assertEqual((end["outcome"], end["findings"], end["findings_open"]), ("done", 2, 1))

    def test_a_failed_post_leaves_review_md_byte_for_byte_the_same(self):
        self._run_review(FakeGh())
        good = (self.dir / "review.md").read_bytes()
        (self.dir / "review.md").write_text(REVIEW_ONE, encoding="utf-8")
        _, done = self._run_review(FakeGh(fail=True))[-1]
        # The app has a row for round 2 now, so the round it writes is round 3.
        self.assertEqual(
            (self.dir / "review.md").read_bytes(), good.replace(b"## Round 2", b"## Round 3")
        )
        self.assertEqual(done["outcome"], "done")
        self.assertEqual(done["comments"][0]["state"], "failed")
        self.assertIn("Bad credentials", done["comments"][0]["reason"])

    def test_another_stage_never_calls_gh(self):

        class Spec:
            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                yield ("chunk", "# Spec: a problem\nAuthor: t. Status: accepted.\n\n## Body\n")
                await _submits(kw)
                yield ("done", {"session_id": "s", "cost": {}})

        (self.dir / "intent.md").write_text(
            "# Intent: a problem\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8"
        )
        state_of(
            self.core, str(self.repo), self.unit, statuses={"intent.md": "accepted"}, type="feat"
        )
        use_sessions(self.core, Spec())
        gh = FakeGh()

        async def go():
            async for _ in self.core.steps.run_step(str(self.repo), self.unit, "spec"):
                pass

        with mock.patch("coscc.git.gh.run", gh):
            asyncio.run(go())
        self.assertEqual(gh.calls, [])

    def test_posting_again_never_makes_a_second_comment(self):
        gh = FakeGh()
        first = self._post(gh, 1)
        second = self._post(gh, 1)
        self.assertEqual((first["state"], second["state"]), ("posted", "already"))
        self.assertEqual(len(gh.posts()), 1)

    def test_two_presses_at_once_still_make_one_comment(self):

        gh = FakeGh()

        async def both():
            return await asyncio.gather(
                self.core.answers.post_review_comment(str(self.repo), self.unit, 1),
                self.core.answers.post_review_comment(str(self.repo), self.unit, 1),
            )

        with mock.patch("coscc.git.gh.run", gh):
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


OUTCOME_INTENT = (
    "# Intent: q\n"
    "Author: t. Type: feat. Status: accepted.\n\n"
    # A deadline already past on any day these tests run, so the board reads it as due.
    "## Proposed outcome\n\nBy 2026-09-01, three of three.\n\n"
    "## Open questions\n\n1. One?\n\n"
    "## Answers\n\n### Câu 1\nAnswered by: Phong. Date: 2026-09-24. Via: product.\n\nCó.\n"
)


class HowAnAnswerNamesItsQuestion(unittest.TestCase):
    """`artifact` and `question` in any accepted form write the same row; any other form is
    refused with what to send."""

    LONG = "A question long enough to be cut at sixty characters, and this tail is not shown?"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.cwd = str(root / "work" / "proj")
        Path(self.cwd).mkdir(parents=True)
        config = Config(
            workspaces=(self.cwd,), working_dir=str(root / "work"), data_dir=str(root / "data")
        )
        self.core = Core(config, Sessions(config))
        made = create_sync(self.core, self.cwd, "a-problem", "x")
        self.unit = made["unit"]
        (Path(made["path"]) / "intent.md").write_text(
            "# Intent: q\nAuthor: t. Type: feat. Status: accepted.\n\n"
            f"## Open questions\n\n1. One?\n2. {self.LONG}\n",
            encoding="utf-8",
        )
        state_of(
            self.core,
            self.cwd,
            self.unit,
            statuses={"intent.md": "accepted"},
            type="feat",
            questions={"intent.md": ["One?", self.LONG]},
        )

    def answer(self, artifact, question, text="Có."):
        return asyncio.run(
            self.core.answers.answer(self.cwd, self.unit, artifact, question, text, "Phong")
        )

    def answers(self):
        [u] = asyncio.run(self.core.board(self.cwd))["units"]
        return [(a["artifact"], a["n"]) for a in u["answers"]]

    def refused(self, artifact, question) -> str:
        with self.assertRaises(Invalid) as e:
            self.answer(artifact, question)
        self.assertEqual(self.answers(), [], "nothing was written")
        return str(e.exception)

    def assertSays(self, message, *parts):
        for part in parts:
            self.assertIn(part, message)

    def assertListsTheOpenQuestions(self, message):
        self.assertSays(message, "intent.md 1 (One?)", f"intent.md 2 ({self.LONG[:60]})")
        self.assertNotIn("this tail", message.split("Open questions:")[1])
        self.assertSays(message, 'Send question as its number, 1 or "1", or F<n>')

    def test_a_stage_name_and_a_file_name_write_the_same_row(self):
        for artifact in ("intent", "intent.md", " INTENT.md "):
            got = self.answer(artifact, 1)
            self.assertEqual((got["artifact"], got["question"]), ("intent.md", 1))
            self.assertEqual(self.answers(), [("intent.md", 1)])

    def test_a_number_and_a_digit_string_write_the_same_row(self):
        for question in (1, "1", " 1\n"):
            got = self.answer("intent.md", question)
            self.assertEqual((got["artifact"], got["question"]), ("intent.md", 1))
            self.assertEqual(self.answers(), [("intent.md", 1)])

    def test_a_bad_artifact_lists_the_artifacts_of_the_unit(self):
        message = self.refused("intnet", 1)
        self.assertSays(message, "'intnet'", self.unit, "intent.md", "spec.md", "review.md")

    def test_a_question_that_is_not_a_number_lists_the_open_questions(self):
        for question in ("Câu 1", "One?", self.LONG, "1.0", 1.0, True, None, "", "-1", "١"):
            self.assertListsTheOpenQuestions(self.refused("intent.md", question))

    def test_a_number_the_artifact_does_not_have_lists_the_open_questions(self):
        message = self.refused("intent.md", 5)
        self.assertSays(message, "has no question 5")
        self.assertListsTheOpenQuestions(message)

    def test_an_artifact_with_no_questions_lists_the_open_questions(self):
        message = self.refused("spec", 1)
        self.assertSays(message, "has no numbered item")
        self.assertListsTheOpenQuestions(message)

    def test_a_finding_in_another_artifact_lists_the_open_questions(self):
        message = self.refused("intent.md", "F1")
        self.assertSays(message, "a finding is answered in review.md")
        self.assertListsTheOpenQuestions(message)

    def test_a_finding_nobody_awaits_lists_the_open_questions(self):
        message = self.refused("review", " F9 ")
        self.assertSays(message, "F9 is not a finding")
        self.assertListsTheOpenQuestions(message)


class RecordingAnOutcome(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.cwd = str(root / "work" / "proj")
        Path(self.cwd).mkdir(parents=True)
        self.data_dir = root / "data"
        config = Config(
            workspaces=(self.cwd,), working_dir=str(root / "work"), data_dir=str(self.data_dir)
        )
        self.core = Core(config, Sessions(config))
        made = create_sync(self.core, self.cwd, "a-problem", "x")
        self.unit = made["unit"]
        self.dir = Path(made["path"])
        for stage in ("spec", "impl", "pr", "review", "ship"):
            (self.dir / f"{stage}.md").write_text(
                f"# {stage}\nStatus: accepted.\n", encoding="utf-8"
            )
        (self.dir / "plan.md").write_text("# plan\nStatus: accepted.\n", encoding="utf-8")
        self.intent = self.dir / "intent.md"
        self.intent.write_text(OUTCOME_INTENT, encoding="utf-8")
        self.finished(self.cwd, self.unit, shipped=True)

    def finished(self, cwd, unit, shipped):
        state_of(
            self.core,
            cwd,
            unit,
            statuses={
                "intent.md": "accepted",
                "spec.md": "accepted",
                "plan.md": "accepted",
                "impl.md": "accepted",
                "pr.md": "accepted",
                "review.md": "accepted",
                "ship.md": "accepted",
            },
            type="feat",
            shipped=shipped,
            questions={"intent.md": ["One?"]},
        )
        self.core.ws.unit_meta().add_answer(
            self.core.ws.key(cwd), unit, "intent.md", 1, "Có.", "Phong", "2026-09-24", "product"
        )

    def record(self, **over):
        kw = {
            "result": "đạt",
            "measured_by": "agent",
            "source": "npm test, 12 pass",
            "reason": "",
            "note": "",
            "recorded_by": "Phong",
            **over,
        }
        return asyncio.run(self.core.answers.record_outcome(self.cwd, self.unit, **kw))

    def board_unit(self):
        [u] = asyncio.run(self.core.board(self.cwd))["units"]
        return u

    def refused(self, **over) -> str:
        before = hashlib.sha256(self.intent.read_bytes()).hexdigest()
        with self.assertRaises(Invalid) as e:
            self.record(**over)
        self.assertEqual(hashlib.sha256(self.intent.read_bytes()).hexdigest(), before)
        self.assertTrue(str(e.exception))
        return str(e.exception)

    def test_no_recorded_by_is_recorded_as_owner(self):
        """What `recorded_by="  "` was refused for until then."""
        self.record(recorded_by="  ")
        block = self.intent.read_text(encoding="utf-8").split("### Outcome", 1)[1]
        self.assertIn("Answered by: owner", block)

    def test_a_block_is_appended_and_every_byte_before_it_stays(self):
        before = self.intent.read_bytes()
        got = self.record(note="Ghi chú.")
        after = self.intent.read_bytes()
        self.assertEqual(after[: len(before)], before)
        tail = after[len(before) :].decode("utf-8")
        self.assertIn("\n### Outcome\nAnswered by: Phong. Date: ", tail)
        self.assertIn(
            "Result: đạt\nMeasured by: agent\nSource: npm test, 12 pass\n\nGhi chú.\n", tail
        )
        self.assertNotIn("## Answers", tail, "the existing heading is reused")
        self.assertEqual(
            (got["result"], got["measured_by"], got["recorded_by"]), ("đạt", "agent", "Phong")
        )
        self.assertLessEqual(
            {p.name for p in self.dir.iterdir()}, {f"{s}.md" for s in states.default().stage_names}
        )

    def test_the_board_then_reads_it_and_the_last_block_is_in_force(self):
        self.record()
        u = self.board_unit()
        self.assertEqual(u["outcome"]["result"], "met")
        self.record(result="trượt", measured_by="Linh", source="board, 2026-10-08")
        u = self.board_unit()
        self.assertEqual((u["outcome"]["result"], u["outcome"]["measured_by"]), ("missed", "Linh"))
        self.assertEqual(u["next"], "finished")
        self.assertEqual((u["questions"][0]["answered"], u["open"]), (True, 0))

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
            await self.core.answers.record_outcome(
                self.cwd, "0099_nothing", "đạt", "agent", "x", "", "", "P"
            )
        except Invalid as e:
            return str(e)
        return ""

    def test_an_unfinished_unit_is_refused(self):
        other = create_sync(self.core, self.cwd, "b-problem", "x")["unit"]
        self.finished(self.cwd, other, shipped=False)
        with self.assertRaises(Invalid) as e:
            asyncio.run(
                self.core.answers.record_outcome(
                    self.cwd, other, "đạt", "agent", "npm test", "", "", "Phong"
                )
            )
        self.assertIn("only on a finished unit", str(e.exception))

    def test_a_section_after_answers_is_refused(self):
        self.intent.write_text(OUTCOME_INTENT + "\n## Notes\n\nx\n", encoding="utf-8")
        self.assertIn("section after its ## Answers", self.refused())

    def test_reading_an_overdue_board_writes_no_row_and_starts_nothing(self):
        journal = self.core.ws.journal()
        key = self.core.ws.key(self.cwd)
        # The first read of a store imports it, and says what it could not read.
        self.assertIsNone(self.board_unit()["outcome"]["result"])
        before = len(journal.records(key))
        self.board_unit()
        self.assertEqual(len(journal.records(key)), before)
        self.assertEqual(self.core.chat.sessions_for(self.cwd)["sessions"], [])


class DroppingAUnitRemovesItsScratch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name).resolve()
        (root / "tmp").mkdir()
        patch = mock.patch.object(tempfile, "tempdir", str(root / "tmp"))
        patch.start()
        self.addCleanup(patch.stop)
        self.repo = root / "work" / "proj"
        self.repo.mkdir(parents=True)
        self.data = str(root / "data")
        config = Config(
            workspaces=(str(self.repo),), working_dir=str(root / "work"), data_dir=self.data
        )
        self.core = Core(config, Sessions(config))
        made = create_sync(self.core, str(self.repo), "a-problem", "some words")
        self.unit = made["unit"]
        (Path(made["path"]) / "intent.md").write_text(
            "# Intent: a problem\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8"
        )

    def test_both_directories_are_gone_after_a_drop(self):
        ram, disk = scratch.ensure(self.repo, self.unit, self.data)
        (disk / "big").write_text("x")
        done = {"effect": "worktree", "result": "absent", "detail": ""}
        with (
            mock.patch("coscc.units.hold.close_pr", mock.AsyncMock(return_value=done)),
            mock.patch("coscc.units.hold.remove_tree", mock.AsyncMock(return_value=done)),
        ):
            asyncio.run(
                self.core.answers.hold(str(self.repo), self.unit, "dropped", "no use", "Leif")
            )
        self.assertFalse(ram.exists())
        self.assertFalse(disk.exists())


class OpeningAUnitWritesItsRowAndItsIdea(unittest.TestCase):
    """A press that opens a unit records two things in one transaction: the unit's row, and, when
    it carried a brief, `idea.md` accepted through the `unit-created` guard. No file is read."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.cwd = str(root / "work" / "proj")
        Path(self.cwd).mkdir(parents=True)
        config = Config(
            workspaces=(self.cwd,), working_dir=str(root / "work"), data_dir=str(root / "data")
        )
        self.core = Core(config, Sessions(config))

    def rows(self, unit):
        meta = self.core.ws.unit_meta()
        with meta.data.connect() as conn:
            return conn.execute("SELECT unit FROM unit_meta WHERE unit = ?", (unit,)).fetchall()

    def test_a_brief_gives_the_row_and_the_accepted_idea(self):
        with mock.patch("coscc.leif.answers.Answers.ingest") as ingest:
            made = create_sync(self.core, self.cwd, "a-problem", "some words")
        ingest.assert_not_called()
        self.assertEqual(len(self.rows(made["unit"])), 1)
        [row] = unit_history(self.core, self.cwd, made["unit"])["transitions"]
        self.assertEqual(
            (row["artifact"], row["from_state"], row["to_state"]),
            ("idea.md", "not started", "accepted"),
        )
        self.assertEqual(
            (row["guard"], row["authority"], row["source"], row["actor"]),
            ("unit-created", "code", "app:create", "app:create"),
        )
        self.assertNotIn("error", made)
        self.assertNotIn("ingest_error", made)

    def test_no_brief_gives_the_row_only(self):
        made = create_sync(self.core, self.cwd, "a-problem", "")
        self.assertEqual(len(self.rows(made["unit"])), 1)
        self.assertEqual(unit_history(self.core, self.cwd, made["unit"])["transitions"], [])
        self.assertFalse((Path(made["path"]) / "idea.md").exists())

    def test_a_failed_write_is_told_as_an_ingest_error_and_a_card_problem(self):
        with mock.patch(
            "coscc.units.transitions.apply", side_effect=sqlite3.OperationalError("disk")
        ):
            made = create_sync(self.core, self.cwd, "a-problem", "some words")
        self.assertIn("ingest_error", made)
        meta = self.core.ws.unit_meta()
        with meta.data.connect() as conn:
            found = conn.execute(
                "SELECT field FROM unit_unknowns WHERE unit = ?", (made["unit"],)
            ).fetchall()
        self.assertEqual([r["field"] for r in found], ["ingest"])
