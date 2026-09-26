"""Tests for `the rows, cards and pure functions` in `coscc/state_views.py`, split from `coscc/state_test.py` (`0095`).
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from coscc.state_test import SOURCE, _self_names, _source_text, _state_class


class TheAutopilotBlockIsCopied(unittest.TestCase):
    """`0043` R9. The board's `autopilot` block, shown as it came: a row per stop, one cap line."""

    def test_stops_and_cap(self):
        from types import SimpleNamespace

        from coscc.state import AutopilotStop, StudioState

        page = SimpleNamespace()
        StudioState._show_autopilot_block(page, {
            "on": True, "refused_because": "",
            "stops": [{"unit": "0010_a", "kind": "a", "reason": "open questions: spec.md question 2"},
                      {"unit": "", "kind": "cap", "reason": "over"}],
            "cap": {"limit": 50.0, "spent": 3.5, "running": 4.0, "day": "2026-10-01"},
        })
        self.assertTrue(page.autopilot_on)
        self.assertEqual(page.autopilot_stops, [
            AutopilotStop("0010_a", "Open question", "open questions: spec.md question 2"),
            AutopilotStop("the workspace", "Daily cap", "over"),
        ])
        self.assertEqual(page.autopilot_cap, "Today: 3.50 spent, 4.00 running, cap 50.00 USD")
        StudioState._show_autopilot_block(page, {"on": True, "cap": {
            "limit": 80.0, "spent": 68.0, "known": 20.0, "estimated": 48.0, "estimated_count": 5, "running": 0.0,
        }})
        self.assertEqual(page.autopilot_cap, "Today: 68.00 spent, 48.00 of it estimated, 0.00 running, cap 80.00 USD")
        StudioState._show_autopilot_block(page, {"on": False})
        self.assertEqual((page.autopilot_on, page.autopilot_stops, page.autopilot_cap), (False, [], ""))

    def test_no_shortlist_has_its_own_label(self):
        """`0104` R9."""
        from types import SimpleNamespace

        from coscc import autopilot
        from coscc.state import AutopilotStop, StudioState

        page = SimpleNamespace()
        StudioState._show_autopilot_block(page, {"on": True, "stops": [
            {"unit": "", "kind": "shortlist", "reason": autopilot.NO_SHORTLIST},
        ]})
        self.assertEqual(page.autopilot_stops, [
            AutopilotStop(unit="the workspace", kind="No shortlist", reason=autopilot.NO_SHORTLIST),
        ])


class AFreshUnitIsPlannedNotNeedsReview(unittest.TestCase):
    """`0001_product-describes-a-state-it-is-not-in` R4/R5, from this store, asked of the
    stage columns and states of `0100` rather than the lanes they replaced.

    The dict is the shape `coscc/board.py` hands over for a unit started from the page:
    one accepted `idea.md`, nothing else, `next` pointing at the intent.
    """

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

        from coscc import board
        from coscc.service import unit_state

        with tempfile.TemporaryDirectory() as d:
            unit = Path(d) / ".cos" / "0015_fresh"
            unit.mkdir(parents=True)
            (unit / "idea.md").write_text("# Idea: x\nStatus: accepted.\n", encoding="utf-8")
            [u] = asyncio.run(board.read(d))["units"]
        self.assertEqual((u["phase"], u["at"]), ("pre-intent", "intent"))
        self.assertEqual(unit_state(u | {"integration": None}, None, None)["state"], "ready")

    def test_a_unit_with_problems_is_an_error(self):
        """`0100` C3: an artifact that cannot be read is something a re-run fixes."""
        from coscc.service import unit_state

        unit = self._fresh("started") | {"problems": ["no intent.md — every unit opens with one"]}
        self.assertEqual(unit_state(unit, None, None)["state"], "error")

    def test_the_dialog_of_a_unit_with_problems_does_not_say_it_needs_a_person(self):
        """`0100` review F1. The dialog's header draws `state_reason`, never the raw
        `attention_reason`, which calls this unit "Needs a person" beside `Error`."""
        from coscc.service import attention_reason, unit_state
        from coscc.state import Unit, _shown

        unit = self._fresh("started") | {"problems": ["no intent.md — every unit opens with one"]}
        decided = unit_state(unit, None, None)
        page = Unit(id=unit["name"], attention_reason=attention_reason(unit), decided_state=decided["state"],
                    decided_label=decided["label"], decided_color=decided["color"])
        self.assertEqual(page.attention_reason, "Needs a person")
        self.assertEqual(_shown(page, {})["state"], "error")
        self.assertEqual(_shown(page, {})["state_reason"], "")
        screens = _source_text("screens")
        self.assertNotIn("current_unit.attention_reason", screens)
        self.assertIn("current_unit.state_reason", screens)

    def test_a_review_that_asked_for_changes_is_ready(self):
        """`0015`, `0100` C6: the unit waits on a fix a step makes, not on a person."""
        from coscc.service import unit_state
        from coscc.state import STATUS_COLOR

        unit = self._fresh("started") | {"why": "changes-requested", "at": "review", "between_pr_and_ship": True}
        for row in unit["stages"]:
            row["status"] = "accepted" if row["stage"] not in ("review", "ship") else "not started"
            if row["stage"] == "review":
                row["status"] = "changes-requested"
        self.assertEqual(unit_state(unit, None, None)["state"], "ready")
        self.assertIn("changes-requested", STATUS_COLOR)


class OpenQuestionsAreCopiedNotRecounted(unittest.TestCase):
    """`0016` R7 and R8, on one temporary `.cos/` read the way the page reads it."""

    TEXT = (
        "# Intent: q\nAuthor: t. Type: feat. Status: accepted.\n\n"
        "## Open questions\n\n1. One?\n2. Two?\n3. Three?\n\n"
        "## Answers\n\n### Câu 1\nAnswered by: A. Date: 2026-09-23. Via: product.\n\nCó.\n"
    )

    def _both(self) -> tuple[dict, dict]:
        """The unit as `status --json` printed it, and as `coscc/board.py` handed it on."""
        import asyncio
        import json
        import subprocess
        import tempfile

        from coscc import board, harness

        with tempfile.TemporaryDirectory() as d:
            unit = Path(d) / ".cos" / "0001_q"
            unit.mkdir(parents=True)
            (unit / "intent.md").write_text(self.TEXT, encoding="utf-8")
            raw = subprocess.run(
                ["node", str(harness.script()), "--root", d, "status", "--json"],
                capture_output=True, text=True, check=True,
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
        """Reversed by `0100` on purpose (`intent.md ## Answers, câu 3`): until then an open
        question was a number on the card and moved the unit into no lane (`0016` R8)."""
        from coscc.service import unit_state

        _, through_board = self._both()
        self.assertGreater(through_board["open"], 0)
        self.assertEqual(unit_state(through_board | {"integration": None}, None, None)["state"], "needs-you")


class AHoldIsCopiedAndStartsNothing(unittest.TestCase):
    """`0045` R14, R16. The card's hold is `cos.mjs`'s; the handler calls `SERVICE.hold` and
    never a step."""

    TEXT = (
        "# Intent: q\nAuthor: t. Type: feat. Status: accepted.\n\n## Answers\n\n"
        "### Dropped\nDecided by: Leif. Date: 2026-09-24. Via: product.\n\nkhông đáng\n"
    )

    def test_the_card_fields_are_the_boards(self):
        import asyncio
        import tempfile

        from coscc import board
        from coscc.state import _hold_fields

        with tempfile.TemporaryDirectory() as d:
            unit = Path(d) / ".cos" / "0001_q"
            unit.mkdir(parents=True)
            (unit / "intent.md").write_text(self.TEXT, encoding="utf-8")
            [u] = asyncio.run(board.read(d))["units"]
        got = _hold_fields(u)
        self.assertEqual(
            (got["hold_state"], got["hold_reason"], got["hold_by"], got["hold_date"], got["hold_moves"]),
            ("dropped", "không đáng", "Leif", "2026-09-24", ["paused"]),
        )
        self.assertEqual(_hold_fields({})["hold_state"], "")

    def test_the_activity_row_says_which_pull_requests_were_already_closed(self):
        from coscc.state import _hold_detail

        row = {"reason": "không đáng", "by": "Leif", "effects": [
            {"effect": "close-pr", "result": "failed", "detail": "closed #7; #9: HTTP 502: Bad Gateway"},
            {"effect": "remove-worktree", "result": "done", "detail": "removed /t"},
        ]}
        self.assertEqual(
            _hold_detail(row),
            " / không đáng / by Leif / close-pr: failed (closed #7; #9: HTTP 502: Bad Gateway)",
        )

    def test_dropped_and_paused_units_leave_the_columns_for_their_groups(self):
        """`0100` R8, Design 6. Until then a paused unit stayed in its lane (`0045`)."""
        from types import SimpleNamespace

        from coscc.state import Card, StudioState

        # `0053`: the columns draw the cards `board_ids` names; each collapsed group draws
        # the cards of its state and counts them with `group_counts`.
        page = SimpleNamespace(
            cards=[Card(id="a", hold_state="dropped", state="dropped"),
                   Card(id="b", hold_state="paused", state="paused"), Card(id="c", state="ready")],
            query="", focus="All work",
        )
        cv = StudioState.computed_vars
        page.shown_ids = cv["shown_ids"].fget(page)
        self.assertEqual(cv["board_ids"].fget(page), ["c"])
        self.assertEqual(cv["group_counts"].fget(page), {"done": 0, "paused": 1, "dropped": 1})
        # Review F2: a group holds only what the search and the filter leave.
        page.query = "b"
        page.shown_ids = cv["shown_ids"].fget(page)
        self.assertEqual(cv["group_counts"].fget(page), {"done": 0, "paused": 1, "dropped": 0})
        page.query, page.focus = "", "Needs you"
        page.shown_ids = cv["shown_ids"].fget(page)
        self.assertEqual(cv["group_counts"].fget(page), {"done": 0, "paused": 0, "dropped": 0})

    def test_the_handler_calls_hold_and_no_step(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
        [handler] = [
            n for n in _state_class(tree).body
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "set_hold"
        ]
        text = "\n".join(ast.unparse(s) for s in handler.body[1:])  # past the docstring
        self.assertIn("SERVICE.hold(", text)
        for forbidden in ("run_step", "run_next", "SERVICE.integrate"):
            self.assertNotIn(forbidden, text)
        raised = next(
            i for i, stmt in enumerate(handler.body)
            if isinstance(stmt, ast.Assign) and "holding" in [x for t in stmt.targets for x in _self_names(t)]
        )
        after = handler.body[raised + 1]
        self.assertTrue(isinstance(after, ast.Expr) and isinstance(after.value, ast.Yield))


class AnAskOutlivesItsWaiter(unittest.TestCase):
    """`0056` review round 1, F2. A navigation cancels the `load_next` an arrival chained; the
    `cos.mjs next` it was waiting on must not be cancelled with it — its `node` and `gh` would
    run on unread — and the next waiter at that unit takes its answer."""

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
            return first.cancelled(), answer, dict(page._ASKING)

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

        self.assertEqual(_run_target({"stage": "impl", "action": "fix it", "blocked": True}), ("impl", "fix it"))
        self.assertEqual(_run_target({"stage": "", "action": "needs a person"}), ("", "needs a person"))
        self.assertEqual(_run_target({}), ("", ""))
        # An action naming a stage is still not a stage.
        self.assertEqual(_run_target({"action": "then write-review again"})[0], "")


class FindingsAwaitingAPersonAreCopied(unittest.TestCase):
    """`0028` plan step 9. The Questions tab lists `cos.mjs`'s `personFindings`, and the run
    frame shows `cos.mjs next`'s `waiting`; the page derives neither."""

    UNIT = {
        "open": 1,
        "questions": [{"artifact": "intent.md", "n": 2, "text": "Two?", "answered": False, "counted": True}],
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
                if isinstance(node, ast.Assign) and any("run_waiting" in _self_names(t) for t in node.targets):
                    setters.add(fn.name)
        self.assertEqual(setters, {"load_next"})


class JerasAnswersAreShownAndAnswerable(unittest.TestCase):
    """`0044` R10. What `Service.board` decided about Jera is copied onto each row, and the
    tab keeps Jera's answers in view under the ones still waiting."""

    UNIT = {
        "open": 1,
        "questions": [
            {"artifact": "spec.md", "n": 1, "text": "a", "answered": True, "counted": True, "by_jera": True,
             "cites": ["0001_a/spec.md#Câu 1"], "said": "Có."},
            {"artifact": "spec.md", "n": 2, "text": "b", "answered": False, "counted": True,
             "needs_person": True, "proposal": "Đề xuất.", "reason": "tiền"},
            {"artifact": "spec.md", "n": 3, "text": "c", "answered": True, "counted": True},
            {"artifact": "intent.md", "n": 1, "text": "d", "answered": False, "counted": False},
        ],
    }

    def page(self, unit=None, answerable=True):
        from types import SimpleNamespace

        from coscc.state import _questions

        _, asked = _questions(unit or self.UNIT)
        return SimpleNamespace(current_unit=SimpleNamespace(questions=asked, answerable=answerable))

    def test_the_fields_are_copied(self):
        from coscc.state import _questions

        _, (q1, q2, _q3, q4) = _questions(self.UNIT)
        self.assertEqual((q1.by_jera, q1.cites, q1.said, q1.needs_person), (True, ["0001_a/spec.md#Câu 1"], "Có.", False))
        self.assertEqual((q2.needs_person, q2.proposal, q2.reason), (True, "Đề xuất.", "tiền"))
        self.assertEqual((q4.by_jera, q4.cites, q4.said, q4.needs_person, q4.proposal), (False, [], "", False, ""))

    def test_waiting_first_then_jeras_and_never_a_persons(self):
        from coscc.state import StudioState

        shown = StudioState.computed_vars["open_questions_here"].fget(self.page())
        self.assertEqual([q.key for q in shown], ["spec.md#2", "intent.md#1", "spec.md#1"])

    def test_ask_jera_is_offered_only_with_a_question_it_may_answer(self):
        from coscc.state import StudioState

        can = StudioState.computed_vars["jera_can_ask"].fget
        self.assertTrue(can(self.page()))
        self.assertFalse(can(self.page(answerable=False)))
        only_review = {"questions": [{"artifact": "review.md", "n": 1, "text": "x", "answered": False}]}
        self.assertFalse(can(self.page(only_review)))
        answered = {"questions": [dict(q, answered=True) for q in self.UNIT["questions"]]}
        self.assertFalse(can(self.page(answered)))

    def test_the_handler_only_calls_the_service(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
        fn = next(f for f in _state_class(tree).body if getattr(f, "name", "") == "ask_jera")
        calls = {n.func.attr for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and isinstance(n.func.value, ast.Name) and n.func.value.id == "SERVICE"}
        self.assertEqual(calls, {"precedent"})


class CellLabelNamesAFailureTheArtifactCannot(unittest.TestCase):
    """`0019_a-failed-step-destroys-the-work-that-succeeded` plan step 7, `spec.md` R5."""

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

        row = {"status": "not started", "last_run": {"outcome": "failed", "turns": None, "cost_usd": None}}
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

    def test_0092_turns_known_and_cost_not_says_each(self):
        from coscc.state import _cell_label

        row = {"status": "not started", "last_run": {"outcome": "failed", "turns": 109, "cost_usd": None}}
        self.assertEqual(_cell_label(row), ("not started · failed · 109 turns · cost unknown", "amber"))

    def test_0092_cost_known_and_turns_not_says_each(self):
        from coscc.state import _cell_label

        row = {"status": "not started", "last_run": {"outcome": "failed", "turns": None, "cost_usd": 0.4}}
        self.assertEqual(_cell_label(row), ("not started · failed · turns unknown · $0.40", "amber"))


class ACostNobodyKnowsIsNeverShownAsNothing(unittest.TestCase):
    """`0092` R8 b, c: `_usd` and the Cost caption say `unknown`, never `—` or `$0.00`."""

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
        self.assertEqual(cost_note({"unknown": 3}), "Added up from each finished run; 3 run(s) with unknown cost")
