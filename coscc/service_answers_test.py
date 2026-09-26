"""Tests for `AnswersMixin` in `coscc/service_answers.py`, split from `coscc/service_test.py` (`0095`).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.config import Config
from coscc.service_common import STAGE_FILES, Invalid
from coscc.service import Service
from coscc.sessions import Sessions
from coscc.service_test import create_sync


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
