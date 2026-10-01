"""Tests for `the rows, cards and pure functions` in `coscc/state/views.py`, split from
`tests/state/test_state.py`."""

from __future__ import annotations

import json

import ast
import unittest
from unittest import mock
from pathlib import Path

from tests.units.test_meta import snapshot_of
from tests.state.test_state import SOURCE, _self_names, _source_text, _state_class
from coscc.state import views


class TheAutopilotBlockIsCopied(unittest.TestCase):
    """The board's `autopilot` block, shown as it came: a row per stop, one cap line."""

    def test_stops_and_cap(self):
        from types import SimpleNamespace

        from coscc.state import AutopilotStop, StudioState

        page = SimpleNamespace()
        StudioState._show_autopilot_block(
            page,
            {
                "on": True,
                "refused_because": "",
                "stops": [
                    {"unit": "0010_a", "kind": "a", "reason": "open questions: spec.md question 2"},
                    {"unit": "", "kind": "cap", "reason": "over"},
                ],
                "cap": {"limit": 50.0, "spent": 3.5, "running": 4.0, "day": "2026-10-01"},
            },
        )
        self.assertTrue(page.autopilot_on)
        self.assertEqual(
            page.autopilot_stops,
            [
                AutopilotStop("0010_a", "Open question", "open questions: spec.md question 2"),
                AutopilotStop("the workspace", "Daily cap", "over"),
            ],
        )
        self.assertEqual(page.autopilot_cap, "Today: 3.50 spent, 4.00 running, cap 50.00 USD")
        StudioState._show_autopilot_block(
            page,
            {
                "on": True,
                "cap": {
                    "limit": 80.0,
                    "spent": 68.0,
                    "known": 20.0,
                    "estimated": 48.0,
                    "estimated_count": 5,
                    "running": 0.0,
                },
            },
        )
        self.assertEqual(
            page.autopilot_cap,
            "Today: 68.00 spent, 48.00 of it estimated, 0.00 running, cap 80.00 USD",
        )
        StudioState._show_autopilot_block(page, {"on": False})
        self.assertEqual(
            (page.autopilot_on, page.autopilot_stops, page.autopilot_cap), (False, [], "")
        )

    def test_no_shortlist_has_its_own_label(self):
        from types import SimpleNamespace

        from coscc.units import autopilot
        from coscc.state import AutopilotStop, StudioState

        page = SimpleNamespace()
        StudioState._show_autopilot_block(
            page,
            {
                "on": True,
                "stops": [
                    {"unit": "", "kind": "shortlist", "reason": autopilot.NO_SHORTLIST},
                ],
            },
        )
        self.assertEqual(
            page.autopilot_stops,
            [
                AutopilotStop(
                    unit="the workspace", kind="No shortlist", reason=autopilot.NO_SHORTLIST
                ),
            ],
        )


class AFreshUnitIsPlannedNotNeedsReview(unittest.TestCase):
    """The dict is the shape `coscc/units/board.py` hands over for a unit started from the page:
    one accepted `idea.md`, nothing else, `next` pointing at the intent."""

    def _fresh(self, phase: str) -> dict:
        stages = ["idea", "intent", "spec", "spike", "plan", "impl", "pr", "review", "ship"]
        return {
            "name": "0015_fresh",
            "next": "write-intent — the unit has no intent.md",
            "why": "missing",
            "at": "intent",
            "blocked": True,
            "problems": [],
            "phase": phase,
            "stages": [
                {"stage": s, "status": "accepted" if s == "idea" else "not started"} for s in stages
            ],
        }

    def test_a_pre_intent_unit_sits_in_the_intent_column_and_is_ready(self):
        import asyncio
        import tempfile

        from coscc.units import board as _board
        from tests.units.test_meta import WithSnapshot

        board = WithSnapshot(_board)
        from coscc.service.common import unit_state

        with tempfile.TemporaryDirectory() as d:
            unit = Path(d) / ".cos" / "0015_fresh"
            unit.mkdir(parents=True)
            (unit / "idea.md").write_text("# Idea: x\nStatus: accepted.\n", encoding="utf-8")
            [u] = asyncio.run(board.read(d))["units"]
        self.assertEqual((u["phase"], u["at"]), ("pre-intent", "intent"))
        self.assertEqual(unit_state(u | {"integration": None}, None, None)["state"], "ready")

    def test_a_unit_with_problems_is_an_error(self):
        """An artifact that cannot be read is something a re-run fixes."""
        from coscc.service.common import unit_state

        unit = self._fresh("started") | {"problems": ["no intent.md — every unit opens with one"]}
        self.assertEqual(unit_state(unit, None, None)["state"], "error")

    def test_the_dialog_of_a_unit_with_problems_does_not_say_it_needs_a_person(self):
        """The dialog's header draws `state_reason`, never the raw `attention_reason`, which calls
        this unit "Needs a person" beside `Error`."""
        from coscc.service.common import attention_reason
        from coscc.service.common import unit_state
        from coscc.state import Unit, _shown

        unit = self._fresh("started") | {"problems": ["no intent.md — every unit opens with one"]}
        decided = unit_state(unit, None, None)
        page = Unit(
            id=unit["name"],
            attention_reason=attention_reason(unit),
            decided_state=decided["state"],
            decided_label=decided["label"],
            decided_color=decided["color"],
        )
        self.assertEqual(page.attention_reason, "Needs a person")
        self.assertEqual(_shown(page, {})["state"], "error")
        self.assertEqual(_shown(page, {})["state_reason"], "")
        screens = _source_text("screens")
        self.assertNotIn("current_unit.attention_reason", screens)
        self.assertIn("current_unit.state_reason", screens)

    def test_a_review_that_asked_for_changes_is_ready(self):
        """The unit waits on a fix a step makes, not on a person."""
        from coscc.service.common import unit_state
        from coscc.state.views import STATUS_COLOR

        unit = self._fresh("started") | {
            "why": "changes-requested",
            "at": "review",
            "between_pr_and_ship": True,
        }
        for row in unit["stages"]:
            row["status"] = "accepted" if row["stage"] not in ("review", "ship") else "not started"
            if row["stage"] == "review":
                row["status"] = "changes-requested"
        self.assertEqual(unit_state(unit, None, None)["state"], "ready")
        self.assertIn("changes-requested", STATUS_COLOR)


class OpenQuestionsAreCopiedNotRecounted(unittest.TestCase):
    """Open questions are copied from `status --json`, not recounted, on one temporary `.cos/` read
    the way the page reads it."""

    TEXT = (
        "# Intent: q\nAuthor: t. Type: feat. Status: accepted.\n\n"
        "## Open questions\n\n1. One?\n2. Two?\n3. Three?\n\n"
        "## Answers\n\n### Câu 1\nAnswered by: A. Date: 2026-09-23. Via: product.\n\nCó.\n"
    )

    def _both(self) -> tuple[dict, dict]:
        """The unit as `status --json` printed it, and as `coscc/units/board.py` handed it on."""
        import asyncio
        import subprocess
        import tempfile

        from coscc.units import board as _board
        from tests.units.test_meta import WithSnapshot

        board = WithSnapshot(_board)
        from coscc.agent import harness

        with tempfile.TemporaryDirectory() as d:
            unit = Path(d) / ".cos" / "0001_q"
            unit.mkdir(parents=True)
            (unit / "intent.md").write_text(self.TEXT, encoding="utf-8")
            raw = subprocess.run(
                ["node", str(harness.script()), "--root", d, "--state", "-", "status", "--json"],
                input=json.dumps(snapshot_of(d)),
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            [from_script] = json.loads(raw)["units"]
            [through_board] = asyncio.run(board.read(d))["units"]
        return from_script, through_board

    def test_the_page_count_is_the_status_json_count(self):
        from coscc.state import _questions

        from_script, through_board = self._both()
        waiting, asked = _questions(through_board)
        self.assertEqual(from_script["open"], 2)
        self.assertEqual(waiting, from_script["open"])
        self.assertEqual(
            [(q.artifact, q.number, q.answered) for q in asked],
            [(q["artifact"], q["n"], q["answered"]) for q in from_script["questions"]],
        )

    def test_an_open_question_alone_puts_a_unit_in_needs_you(self):
        """An open question alone puts the unit in `needs-you`; it is not just a number on the card
        with the unit in no lane."""
        from coscc.service.common import unit_state

        _, through_board = self._both()
        self.assertGreater(through_board["open"], 0)
        self.assertEqual(
            unit_state(through_board | {"integration": None}, None, None)["state"], "needs-you"
        )


class AHoldIsCopiedAndStartsNothing(unittest.TestCase):
    """The card's hold is `cos.mjs`'s; the handler calls `SERVICE.answers.hold` and never a step."""

    TEXT = (
        "# Intent: q\nAuthor: t. Type: feat. Status: accepted.\n\n## Answers\n\n"
        "### Dropped\nDecided by: Leif. Date: 2026-09-24. Via: product.\n\nkhông đáng\n"
    )

    def test_the_card_fields_are_the_boards(self):
        import asyncio
        import tempfile

        from coscc.units import board as _board
        from tests.units.test_meta import WithSnapshot

        board = WithSnapshot(_board)
        from coscc.state import _hold_fields

        with tempfile.TemporaryDirectory() as d:
            unit = Path(d) / ".cos" / "0001_q"
            unit.mkdir(parents=True)
            (unit / "intent.md").write_text(self.TEXT, encoding="utf-8")
            [u] = asyncio.run(board.read(d))["units"]
        got = _hold_fields(u)
        self.assertEqual(
            (
                got["hold_state"],
                got["hold_reason"],
                got["hold_by"],
                got["hold_date"],
                got["hold_moves"],
            ),
            ("dropped", "không đáng", "Leif", "2026-09-24", ["paused"]),
        )
        self.assertEqual(_hold_fields({})["hold_state"], "")

    def test_the_activity_row_says_which_pull_requests_were_already_closed(self):
        from coscc.state import _hold_detail

        row = {
            "reason": "không đáng",
            "by": "Leif",
            "effects": [
                {
                    "effect": "close-pr",
                    "result": "failed",
                    "detail": "closed #7; #9: HTTP 502: Bad Gateway",
                },
                {"effect": "remove-worktree", "result": "done", "detail": "removed /t"},
            ],
        }
        self.assertEqual(
            _hold_detail(row),
            " / không đáng / by Leif / close-pr: failed (closed #7; #9: HTTP 502: Bad Gateway)",
        )

    def test_a_paused_unit_stays_in_its_stage_lane_and_a_dropped_one_leaves_for_its_group(self):
        """A paused unit is not folded into a group: it keeps its stage lane, and only a dropped one
        leaves for its group."""
        from types import SimpleNamespace

        from coscc.state import Card, StudioState

        # The lanes draw the cards `board_ids` names; each folded group draws the cards of its state
        # and counts them with `group_counts`.
        page = SimpleNamespace(
            cards=[
                Card(id="a", hold_state="dropped", state="dropped"),
                Card(id="b", hold_state="paused", state="paused"),
                Card(id="c", state="ready"),
            ],
            query="",
            focus="All work",
        )
        cv = StudioState.computed_vars
        page.shown_ids = cv["shown_ids"].fget(page)
        self.assertEqual(cv["board_ids"].fget(page), ["b", "c"])
        self.assertEqual(cv["group_counts"].fget(page), {"done": 0, "dropped": 1})
        # A group holds only what the search and the filter leave.
        page.query = "b"
        page.shown_ids = cv["shown_ids"].fget(page)
        self.assertEqual(cv["group_counts"].fget(page), {"done": 0, "dropped": 0})
        page.query, page.focus = "", "Needs you"
        page.shown_ids = cv["shown_ids"].fget(page)
        self.assertEqual(cv["group_counts"].fget(page), {"done": 0, "dropped": 0})

    def test_the_handler_calls_hold_and_no_step(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
        [handler] = [
            n
            for n in _state_class(tree).body
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "set_hold"
        ]
        text = "\n".join(ast.unparse(s) for s in handler.body[1:])  # past the docstring
        self.assertIn("SERVICE.answers.hold(", text)
        for forbidden in ("run_step", "run_next", "SERVICE.steps.integrate"):
            self.assertNotIn(forbidden, text)
        raised = next(
            i
            for i, stmt in enumerate(handler.body)
            if isinstance(stmt, ast.Assign)
            and "holding" in [x for t in stmt.targets for x in _self_names(t)]
        )
        after = handler.body[raised + 1]
        self.assertTrue(isinstance(after, ast.Expr) and isinstance(after.value, ast.Yield))


class MoreRoundsIsCopiedAndStartsNothing(unittest.TestCase):
    """The unit's `more_rounds` is `cos.mjs`'s; the handler calls `SERVICE.answers.more_rounds` and never a
    step."""

    def test_the_more_rounds_handler_calls_the_service_and_no_step(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
        [handler] = [
            n
            for n in _state_class(tree).body
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "allow_more_rounds"
        ]
        text = "\n".join(ast.unparse(s) for s in handler.body[1:])  # past the docstring
        self.assertIn("SERVICE.answers.more_rounds(", text)
        for forbidden in ("run_step", "run_next", "SERVICE.steps.integrate"):
            self.assertNotIn(forbidden, text)
        raised = next(
            i
            for i, stmt in enumerate(handler.body)
            if isinstance(stmt, ast.Assign)
            and "granting_round" in [x for t in stmt.targets for x in _self_names(t)]
        )
        after = handler.body[raised + 1]
        self.assertTrue(isinstance(after, ast.Expr) and isinstance(after.value, ast.Yield))

    def test_more_rounds_is_copied_onto_the_unit(self):
        """The expression `_load_board` gives `Unit(more_rounds=…)`, run on a real board read:
        a unit `cos.mjs` calls out of rounds, and one it does not."""
        import asyncio
        import os
        import tempfile
        from unittest import mock

        from coscc.units import board as _board
        from tests.units.test_meta import WithSnapshot

        board = WithSnapshot(_board)

        [kw] = [
            k
            for node in ast.walk(ast.parse(SOURCE.read_text(encoding="utf-8")))
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Unit"
            for k in node.keywords
            if k.arg == "more_rounds"
        ]
        code = compile(ast.Expression(kw.value), "state.py", "eval")

        def copy(u: dict) -> object:
            return eval(code, {"bool": bool, "u": u})

        review = "# Review: q\nAuthor: t. Status: changes-requested.\n" + "".join(
            f"\n## Round {n}\n\nReviewed: aaaaaaa. Verdict: changes-requested.\n\n### Findings\n\n- F1 [open] a\n"
            for n in (1, 2, 3)
        )
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ):
            os.environ.pop("COS_REVIEW_ROUNDS", None)
            for name, text in (
                ("0001_stuck", review),
                ("0002_fine", review.split("\n## Round 2")[0]),
            ):
                unit = Path(d) / ".cos" / name
                unit.mkdir(parents=True)
                (unit / "intent.md").write_text(
                    "# I\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8"
                )
                for f in ("spec.md", "plan.md", "impl.md"):
                    (unit / f).write_text("Status: accepted.\n", encoding="utf-8")
                (unit / "pr.md").write_text(
                    "PR: https://github.com/o/r/pull/3. Status: accepted.\n", encoding="utf-8"
                )
                (unit / "review.md").write_text(text, encoding="utf-8")
            got = {u["name"]: u for u in asyncio.run(board.read(d))["units"]}
        self.assertIs(copy(got["0001_stuck"]), True)
        self.assertIs(copy(got["0002_fine"]), False)
        self.assertIs(copy({}), False)
        from coscc.state.views import Unit

        self.assertIs(Unit().more_rounds, False)


class AnAskOutlivesItsWaiter(unittest.TestCase):
    """A navigation cancels the `load_next` an arrival chained; the `cos.mjs next` it was waiting on
    must not be cancelled with it — its `node` and `gh` would run on unread — and the next waiter at
    that unit takes its answer."""

    def test_a_cancelled_waiter_leaves_the_ask_to_finish_and_be_joined(self):
        import asyncio

        from coscc import state as page

        calls, ended = [], []

        async def go():
            gate = asyncio.Event()

            async def ask(cwd, unit):
                calls.append(unit)
                try:
                    await gate.wait()
                except asyncio.CancelledError:
                    ended.append("cancelled")
                    raise
                ended.append("answered")
                return {"stage": "review"}

            first = asyncio.ensure_future(page._asking(ask, "/a", "0009_x", join=True))
            await asyncio.sleep(0)
            first.cancel()
            await asyncio.sleep(0)
            second = page._asking(ask, "/a", "0009_x", join=True)
            gate.set()
            answer = await second
            await asyncio.sleep(0)
            return first.cancelled(), answer, dict(views._ASKING)

        cancelled, answer, left = asyncio.run(go())
        self.assertTrue(cancelled)
        self.assertEqual((calls, ended), (["0009_x"], ["answered"]))
        self.assertEqual((answer, left), ({"stage": "review"}, {}))

    def test_asking_afresh_does_not_join(self):
        import asyncio

        from coscc import state as page

        calls = []

        async def go():
            gate = asyncio.Event()

            async def ask(cwd, unit):
                calls.append(unit)
                await gate.wait()
                return {}

            one = page._asking(ask, "/a", "0009_x", join=True)
            two = page._asking(ask, "/a", "0009_x", join=False)
            gate.set()
            await asyncio.gather(one, two)

        asyncio.run(go())
        self.assertEqual(calls, ["0009_x", "0009_x"])


class RunTargetCopies(unittest.TestCase):
    def test_it_copies_stage_and_action_and_nothing_else(self):
        from coscc.state import _run_target

        self.assertEqual(
            _run_target({"stage": "impl", "action": "fix it", "blocked": True}), ("impl", "fix it")
        )
        self.assertEqual(
            _run_target({"stage": "", "action": "needs a person"}), ("", "needs a person")
        )
        self.assertEqual(_run_target({}), ("", ""))
        # An action naming a stage is still not a stage.
        self.assertEqual(_run_target({"action": "then write-review again"})[0], "")


class FindingsAwaitingAPersonAreCopied(unittest.TestCase):
    """The Questions tab lists `cos.mjs`'s `personFindings`, and the run frame shows `cos.mjs
    next`'s `waiting`; the page derives neither."""

    UNIT = {
        "open": 1,
        "questions": [
            {"artifact": "intent.md", "n": 2, "text": "Two?", "answered": False, "counted": True}
        ],
        "person_findings": [
            {"id": "F2", "reason": "no budget", "answered": True},
            {"id": "F3", "reason": "no gh", "answered": False},
        ],
    }

    def test_questions_carry_findings_after_the_numbered_ones(self):
        from coscc.state import _questions

        count, asked = _questions(self.UNIT)
        self.assertEqual(count, 1)
        self.assertEqual(
            [(q.key, q.artifact, q.label, q.number, q.text, q.answered, q.counted) for q in asked],
            [
                ("intent.md#2", "intent.md", "2", 2, "Two?", False, True),
                ("review.md#F2", "review.md", "F2", 0, "no budget", True, False),
                ("review.md#F3", "review.md", "F3", 0, "no gh", False, False),
            ],
        )

    def test_run_waiting_copies_and_absent_reads_as_none(self):
        from coscc.state import _run_waiting

        self.assertEqual(_run_waiting({"stage": "", "waiting": ["F2", "F3"]}), ["F2", "F3"])
        self.assertEqual(_run_waiting({"stage": "impl"}), [])

    def test_only_load_next_sets_run_waiting(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
        setters = set()
        for fn in _state_class(tree).body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign) and any(
                    "run_waiting" in _self_names(t) for t in node.targets
                ):
                    setters.add(fn.name)
        self.assertEqual(setters, {"load_next"})

    def test_run_dropped_copies_and_absent_reads_as_none(self):
        from coscc.state import _run_dropped

        self.assertEqual(_run_dropped({"stage": "review", "dropped": ["F2", "F3"]}), ["F2", "F3"])
        self.assertEqual(_run_dropped({"stage": "impl"}), [])

    def test_only_load_next_sets_run_dropped(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
        setters = set()
        for fn in _state_class(tree).body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign) and any(
                    "run_dropped" in _self_names(t) for t in node.targets
                ):
                    setters.add(fn.name)
        self.assertEqual(setters, {"load_next"})


class TheQuestionsTabListsWhatWaits(unittest.TestCase):
    """What `Service.board` sent is copied onto each row, and the tab lists only the questions
    still waiting, the counted artifact's first."""

    UNIT = {
        "open": 2,
        "questions": [
            {"artifact": "spec.md", "n": 1, "text": "a", "answered": True, "counted": True},
            {"artifact": "intent.md", "n": 1, "text": "d", "answered": False, "counted": False},
            {"artifact": "spec.md", "n": 2, "text": "b", "answered": False, "counted": True},
        ],
    }

    def test_only_the_unanswered_are_listed_and_the_counted_artifact_comes_first(self):
        from types import SimpleNamespace

        from coscc.state import StudioState, _questions

        _, asked = _questions(self.UNIT)
        shown = StudioState.computed_vars["open_questions_here"].fget(
            SimpleNamespace(current_unit=SimpleNamespace(questions=asked))
        )
        self.assertEqual([q.key for q in shown], ["spec.md#2", "intent.md#1"])

    def test_answering_comes_first_only_while_a_question_is_open_and_answerable(self):
        from types import SimpleNamespace

        from coscc.state import StudioState, _questions

        _, asked = _questions(self.UNIT)
        first = StudioState.computed_vars["answer_first"].fget

        def page(answerable=True, hold="", questions=asked):
            unit = SimpleNamespace(answerable=answerable, hold_state=hold, questions=questions)
            open_here = [q for q in questions if not q.answered]
            return SimpleNamespace(current_unit=unit, open_questions_here=open_here)

        self.assertTrue(first(page()))
        self.assertTrue(first(page(hold="paused")))
        self.assertFalse(first(page(hold="dropped")))
        self.assertFalse(first(page(answerable=False)))
        self.assertFalse(first(page(questions=[q for q in asked if q.answered])))


class TheBoardShowsTheGuardAndWhoseDecision(unittest.TestCase):
    """Copied from what the service sent; nothing decided here."""

    def test_a_transition_shows_its_guards_label_and_authority_and_keeps_the_rest_in_details(self):
        from coscc.state import _moves

        old, new = _moves(
            [
                {
                    "id": 9,
                    "artifact": "ship.md",
                    "from_state": "draft",
                    "to_state": "accepted",
                    "at": "2026-09-29T10:00:00",
                    "guard": "merge-read",
                    "authority": "code",
                    "run": "r-1",
                    "guard_label": "A merge is recorded only from a read that names its merge commit.",
                    "head": "f" * 40,
                },
                {
                    "artifact": "intent.md",
                    "from_state": "absent",
                    "to_state": "draft",
                    "guard": "unknown",
                    "authority": "unknown",
                    "run": "unknown",
                    "guard_label": "",
                    "head": "",
                },
            ]
        )
        self.assertEqual(
            (new.change, new.guard_label, new.authority, new.guard, new.head, new.run, new.key),
            (
                "draft → accepted",
                "A merge is recorded only from a read that names its merge commit.",
                "By the app",
                "merge-read",
                "f" * 40,
                "r-1",
                "move-9",
            ),
        )
        self.assertEqual(
            (old.guard_label, old.authority, old.key),
            ("No guard was recorded for this change.", "Author not recorded", "move-1"),
        )

    def test_the_card_carries_the_code_the_autopilot_held_it_back_with(self):
        from coscc.state import Unit, _card

        self.assertEqual(_card(Unit(id="0002_b", held="overlap-pr #7")).held, "overlap-pr #7")
        self.assertEqual(_card(Unit(id="0002_b")).held, "")


class CellLabelNamesAFailureTheArtifactCannot(unittest.TestCase):
    def test_a_failed_run_with_cost_is_named_and_coloured_amber(self):
        from coscc.state import _cell_label

        row = {
            "status": "not started",
            "last_run": {"outcome": "exhausted", "turns": 121, "cost_usd": 6.88},
        }
        label, color = _cell_label(row)
        self.assertEqual(label, "not started · exhausted · 121 turns · $6.88")
        self.assertEqual(color, "amber")

    def test_a_failed_run_with_no_cost_says_unknown_rather_than_zero(self):
        from coscc.state import _cell_label

        row = {
            "status": "not started",
            "last_run": {"outcome": "failed", "turns": None, "cost_usd": None},
        }
        label, color = _cell_label(row)
        self.assertEqual(label, "not started · failed · turns and cost unknown")
        self.assertEqual(color, "amber")

    def test_a_stage_never_run_keeps_the_bare_status(self):
        from coscc.state import _cell_label

        label, color = _cell_label({"status": "not started", "last_run": None})
        self.assertEqual(label, "not started")
        self.assertEqual(color, "gray")

    def test_a_stage_with_an_artifact_ignores_last_run_entirely(self):
        from coscc.state import _cell_label

        row = {
            "status": "accepted",
            "last_run": {"outcome": "exhausted", "turns": 5, "cost_usd": 0.1},
        }
        label, color = _cell_label(row)
        self.assertEqual(label, "accepted")
        self.assertEqual(color, "grass")

    def test_turns_known_and_cost_not_says_each(self):
        from coscc.state import _cell_label

        row = {
            "status": "not started",
            "last_run": {"outcome": "failed", "turns": 109, "cost_usd": None},
        }
        self.assertEqual(
            _cell_label(row), ("not started · failed · 109 turns · cost unknown", "amber")
        )

    def test_cost_known_and_turns_not_says_each(self):
        from coscc.state import _cell_label

        row = {
            "status": "not started",
            "last_run": {"outcome": "failed", "turns": None, "cost_usd": 0.4},
        }
        self.assertEqual(
            _cell_label(row), ("not started · failed · turns unknown · $0.40", "amber")
        )


class ACostNobodyKnowsIsNeverShownAsNothing(unittest.TestCase):
    """`_usd` and the Cost caption say `unknown`, never `—` or `$0.00`."""

    def test_usd(self):
        from coscc.state import _usd

        self.assertEqual(_usd({"cost_usd": 3.2}), "$3.20")
        self.assertEqual(_usd({"cost_usd": 3.2, "unknown": 0}), "$3.20")
        self.assertEqual(_usd({"cost_usd": 3.2, "unknown": 2}), "$3.20 + unknown")
        self.assertEqual(_usd({"cost_usd": 0.004, "unknown": 1}), "$0.0040 + unknown")
        self.assertEqual(_usd({"cost_usd": 0.0, "unknown": 1}), "unknown")
        self.assertEqual(_usd({}), "—")

    def test_the_cost_caption_counts_the_runs_it_could_not_add(self):
        from coscc.state import COST_NOTE, cost_note

        self.assertEqual(cost_note({"unknown": 0}), COST_NOTE)
        self.assertEqual(cost_note({}), COST_NOTE)
        self.assertEqual(cost_note({"unknown": 3}), "Every finished run; 3 without a cost")


class TheLinksOfAUnitOpenedFromAnIdea(unittest.TestCase):
    """Copied from `Service.board`, with the addresses they link to."""

    def test_a_card_whose_next_why_is_dependency_shows_waits_for_ref(self):
        from coscc.state.views import Unit, _card, link_fields

        u = {"idea": "proj/ideas/0001_f.md", "repo": "proj", "waits_for": ["api/0001_b"]}
        fields = link_fields(u, "proj")
        self.assertEqual(fields["idea_href"], "/idea?ws=proj&id=0001_f")
        self.assertEqual(fields["waits_for_href"], "/unit?ws=api&id=0001_b")
        self.assertEqual(_card(Unit(id="0006_x", **fields)).waits_for, "api/0001_b")

    def test_a_childs_overview_links_to_its_idea_and_to_the_unit_it_waits_for(self):
        from coscc.state.views import child_rows, link_fields

        self.assertEqual(
            link_fields({"idea": "ideas/0001_f.md"}, "api")["idea_href"], "/idea?ws=api&id=0001_f"
        )
        self.assertEqual(
            link_fields({}, "api"),
            {"idea_ref": "", "idea_href": "", "repo": "", "waits_for": "", "waits_for_href": ""},
        )
        [row, gone] = child_rows(
            {
                "units": [
                    {
                        "ref": "api/0001_b",
                        "unit": "0001_b",
                        "repo": "api",
                        "stage": "impl",
                        "state": "Ready",
                        "waits_for": [],
                    },
                    {
                        "ref": "old/0002_c",
                        "unit": "0002_c",
                        "repo": "old",
                        "missing": True,
                        "state": "missing",
                    },
                ]
            }
        )
        self.assertEqual((row.href, gone.href, gone.missing), ("/unit?ws=api&id=0001_b", "", True))


class AKnobIsNamedForAReader(unittest.TestCase):
    """Settings shows what a knob does; its variable waits behind Details (S3)."""

    def test_label_and_variable(self):
        k = views.knob({"name": "allow_write_and_exec", "value": "off", "detail": "d", "on": False})
        self.assertEqual(k.label, "Write files and run commands")
        self.assertEqual(k.variable, "COS_ALLOW_WRITE_AND_EXEC")

    def test_every_knob_the_service_names_has_a_label(self):
        from coscc.config import from_env
        from coscc.service.activity import Activity

        found = Activity.settings(mock.Mock(config=from_env({})))
        self.assertEqual({k["name"] for k in found["knobs"]}, set(views.KNOB_LABELS))


class TheCostOfAMergedUnitIsTheMeanOfTheMergedOnes(unittest.TestCase):
    def test_only_finished_units_with_a_known_cost_count(self):
        rows = [
            {"key": "0001_a", "usd": 10.0},
            {"key": "0002_b", "usd": 20.0},
            {"key": "0003_c", "usd": None},
            {"key": "0004_open", "usd": 99.0},
        ]
        done = {"0001_a", "0002_b", "0003_c"}
        self.assertEqual(views.per_merged_unit(rows, done), ("$15.00", 2))
        self.assertEqual(views.per_merged_unit(rows, set()), ("—", 0))
