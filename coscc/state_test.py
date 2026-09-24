"""Tests for the page's state, and one guard for a failure that is invisible when it hits.

Reflex refuses an assignment to a var that was never declared, and it refuses it **at the
moment the handler runs** — on the server, inside an event, where the only symptom is that
the browser receives nothing. Measured 2026-09-22 while building `0014`'s controls: the
handler created a work unit, then assigned `self.unit` (the var is called `unit_id`), and
the exception killed the event after the side effect. The unit existed and the page looked
as though the button had done nothing, so the next thing a person does is press it again
and get a second unit.

That is why this is an `ast` walk rather than a click test: the class of bug is "a handler
touches a name that is not there", and it costs nothing to find every one of them at once.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parent / "state.py"

# Names assigned on `self` that are not page state. Kept short and explicit: anything added
# here stops being checked, so each entry should be something that is plainly not a var.
NOT_A_VAR = {"membership"}


def _state_class(tree: ast.Module) -> ast.ClassDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "StudioState":
            return node
    raise AssertionError("StudioState is not in state.py any more")


class EveryVarAHandlerSetsIsDeclared(unittest.TestCase):
    def test_no_handler_assigns_a_name_the_state_never_declared(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
        state = _state_class(tree)

        declared = {
            node.target.id
            for node in state.body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        }
        # Computed vars are read-only, but a handler assigning one would be just as wrong,
        # so they count as known names rather than as targets.
        declared |= {
            node.name
            for node in state.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertIn("unit_id", declared, "the fixture for this test has moved")

        unknown = []
        for node in ast.walk(state):
            if not isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for name in _self_names(target):
                    if name not in declared and name not in NOT_A_VAR:
                        unknown.append(f"state.py:{node.lineno}: self.{name}")
        self.assertEqual(
            unknown,
            [],
            "these assign a state var that was never declared, which Reflex refuses at "
            "run time inside the event — the browser just receives nothing:\n  "
            + "\n  ".join(unknown),
        )


class AFreshUnitIsPlannedNotNeedsReview(unittest.TestCase):
    """`0001_product-describes-a-state-it-is-not-in` R4/R5, from this store.

    The dict is the shape `coscc/board.py` hands over for a unit started from the page:
    one accepted `idea.md`, nothing else, `next` pointing at the intent.
    """

    def _fresh(self, phase: str) -> dict:
        stages = ["idea", "intent", "spec", "spike", "plan", "impl", "pr", "review", "ship"]
        return {
            "name": "0015_fresh",
            "next": "write-intent — the unit has no intent.md",
            "blocked": True,
            "problems": [],
            "phase": phase,
            "stages": [
                {"stage": s, "status": "accepted" if s == "idea" else "not started"} for s in stages
            ],
        }

    def test_a_pre_intent_unit_sits_in_the_first_lane(self):
        from coscc.state import _lane

        self.assertEqual(_lane(self._fresh("pre-intent")), "Planned")

    def test_without_the_phase_the_same_unit_would_have_been_in_progress(self):
        # The reason `phase` is read before every other rule: the idea is accepted, so the
        # artifact rules alone would move a ten-second-old unit out of *Planned*.
        from coscc.state import _lane

        self.assertEqual(_lane(self._fresh("started")), "In progress")

    def test_a_unit_with_problems_still_needs_review(self):
        from coscc.state import _lane

        unit = self._fresh("started") | {"problems": ["no intent.md — every unit opens with one"]}
        self.assertEqual(_lane(unit), "Needs review")

    def test_a_review_that_asked_for_changes_needs_review(self):
        """`0015`: the review found something and the unit waits on a fix."""
        from coscc.state import STATUS_COLOR, _lane

        unit = self._fresh("started")
        unit["next"] = "fix the open findings of review round 1 on the branch"
        for row in unit["stages"]:
            row["status"] = "accepted" if row["stage"] not in ("review", "ship") else "not started"
            if row["stage"] == "review":
                row["status"] = "changes-requested"
        self.assertEqual(_lane(unit), "Needs review")
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

    def test_an_open_question_alone_does_not_put_a_unit_in_needs_review(self):
        from coscc.state import _lane

        _, through_board = self._both()
        self.assertGreater(through_board["open"], 0)
        self.assertNotEqual(_lane(through_board), "Needs review")


class PostingARoundLocksTheButtonWhileItRuns(unittest.TestCase):
    """`0021` review F1. Reflex sends state to the browser only at a `yield` or at the end of
    the handler, so a busy flag raised and cleared with no `yield` between never arrives:
    the *Post to PR* button would not lock during the up to 60s `gh` may take."""

    def test_a_yield_follows_raising_posting_round_before_the_service_is_awaited(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
        state = _state_class(tree)
        [handler] = [
            n
            for n in state.body
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "post_review_comment"
        ]
        body = handler.body
        raised = next(
            i
            for i, stmt in enumerate(body)
            if isinstance(stmt, ast.Assign)
            and "posting_round" in [name for t in stmt.targets for name in _self_names(t)]
        )
        after = body[raised + 1]
        self.assertTrue(
            isinstance(after, ast.Expr) and isinstance(after.value, ast.Yield),
            f"state.py:{after.lineno}: the statement after raising posting_round is not "
            "a bare `yield`, so the browser never sees the button locked",
        )

    def test_a_second_press_while_one_is_running_does_nothing(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
        state = _state_class(tree)
        [handler] = [
            n
            for n in state.body
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "post_review_comment"
        ]
        first = next(s for s in handler.body if not isinstance(s, ast.Expr) or not isinstance(s.value, ast.Constant))
        self.assertIsInstance(first, ast.If)
        self.assertIn("posting_round", ast.unparse(first.test))
        self.assertIsInstance(first.body[0], ast.Return)


class ThePageHoldsNoRunningFlagOfItsOwn(unittest.TestCase):
    """`0034` R6, R11, R12. Which units are running is the service's list; whether a unit
    may start, or be stopped, is the service's to refuse."""

    def setUp(self):
        self.tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
        self.state = _state_class(self.tree)
        self.methods = {
            n.name: n for n in self.state.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def test_there_is_no_single_running_attribute(self):
        fields = {
            t.target.id for t in self.state.body
            if isinstance(t, ast.AnnAssign) and isinstance(t.target, ast.Name)
        }
        self.assertNotIn("running", fields)
        self.assertIn("running_steps", fields)
        self.assertNotIn("is_running", self.methods)

    def test_stop_step_asks_the_service(self):
        text = ast.unparse(self.methods["stop_step"])
        self.assertIn("SERVICE.stop_step(", text)

    def test_run_step_has_no_already_running_check_of_its_own(self):
        text = ast.unparse(self.methods["run_step"])
        self.assertNotIn("already running", text)
        self.assertNotIn("self.running ", text)
        self.assertNotIn("self.running:", text)

    def test_the_list_comes_from_the_service(self):
        text = ast.unparse(self.methods["_load_running"])
        self.assertIn("SERVICE.running_steps(", text)


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

    def test_dropped_units_leave_the_lanes_and_paused_ones_stay(self):
        from types import SimpleNamespace

        from coscc.state import StudioState, Unit

        page = SimpleNamespace(
            units=[Unit(id="a", hold_state="dropped"), Unit(id="b", hold_state="paused"), Unit(id="c")],
            query="", focus="All work",
        )
        visible = StudioState.computed_vars["visible_units"].fget(page)
        dropped = StudioState.computed_vars["dropped_units"].fget(page)
        self.assertEqual([u.id for u in visible], ["b", "c"])
        self.assertEqual([u.id for u in dropped], ["a"])

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


class TheRunButtonHoldsNoCopyOfTheLoop(unittest.TestCase):
    """`0024` R1. The stage the button offers is `cos.mjs next`'s, copied; nothing in the
    page works it out from which artifacts exist."""

    def setUp(self):
        self.tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
        self.state = _state_class(self.tree)
        self.methods = {
            n.name: n for n in self.state.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def test_the_old_rule_is_gone_by_name(self):
        names = {n.id for n in ast.walk(self.tree) if isinstance(n, ast.Name)}
        names |= {n.name for n in ast.walk(self.tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertNotIn("_next_required", names)

    def test_next_stage_only_hands_back_run_stage(self):
        body = [s for s in self.methods["next_stage"].body
                if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
        self.assertEqual(len(body), 1)
        self.assertIsInstance(body[0], ast.Return)
        self.assertEqual(ast.unparse(body[0].value), "self.run_stage")

    def test_only_load_next_sets_run_stage_and_it_takes_it_from_run_target(self):
        setters = set()
        for name, fn in self.methods.items():
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign) and any(
                    "run_stage" in _self_names(t) for t in node.targets
                ):
                    setters.add(name)
        self.assertEqual(setters, {"load_next"})
        calls = [n for n in ast.walk(self.methods["load_next"])
                 if isinstance(n, ast.Call) and ast.unparse(n.func) == "_run_target"]
        self.assertEqual(len(calls), 1)
        self.assertIn("SERVICE.next_step", ast.unparse(calls[0]))

    def test_load_next_runs_in_the_background(self):
        decorators = [ast.unparse(d) for d in self.methods["load_next"].decorator_list]
        self.assertIn("rx.event(background=True)", decorators)

    def test_run_step_and_set_mode_run_the_stage_next_stage_names(self):
        for name in ("run_step", "set_mode"):
            with self.subTest(handler=name):
                self.assertIn("self.next_stage", ast.unparse(self.methods[name]))

    def test_opening_a_unit_and_ending_a_step_both_ask_again(self):
        for name in ("open_unit", "run_step"):
            with self.subTest(handler=name):
                self.assertIn("return StudioState.load_next", ast.unparse(self.methods[name]))


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


class ACardShowsWhatServiceRunningSaid(unittest.TestCase):
    """`0051` plan step 6: `_activities` copies, and only `poll_running` feeds it."""

    READ = {
        "running": {
            "0009_x": [{"kind": "step", "stage": "impl", "agent": {"glyph": "ᚢ", "name": "Uruz"},
                        "started": "2026-09-24T01:00:00+00:00", "turns": None, "cost_usd": None}],
            "0010_y": [{"kind": "gebo", "stage": "integrate", "agent": {"glyph": "ᚷ", "name": "Gebo"},
                        "started": "2026-09-24T02:00:00+00:00", "turns": 3, "cost_usd": 0.25}],
            "0011_z": [{"kind": "rebase", "stage": "integrate", "agent": None,
                        "started": "2026-09-24T03:00:00+00:00", "turns": None, "cost_usd": None}],
        },
        "unknown_end": {"0012_w": [{"stage": "plan", "started": "2026-09-24T00:00:00+00:00"}]},
    }

    def test_a_step(self):
        from coscc.state import _activities

        [a] = _activities("0009_x", self.READ)
        self.assertEqual((a.label, a.agent, a.stage, a.started), ("running", "ᚢ Uruz", "impl", "2026-09-24T01:00:00+00:00"))

    def test_unknown_turns_and_cost_are_empty_not_zero(self):
        from coscc.state import _activities

        [a] = _activities("0009_x", self.READ)
        self.assertEqual((a.turns, a.cost), ("", ""))

    def test_gebo_with_turns_and_cost(self):
        from coscc.state import _activities

        [a] = _activities("0010_y", self.READ)
        self.assertEqual((a.label, a.agent, a.turns, a.cost), ("running", "ᚷ Gebo", "3", "$0.25"))

    def test_a_rebase_has_no_agent(self):
        from coscc.state import _activities

        [a] = _activities("0011_z", self.READ)
        self.assertEqual((a.label, a.agent, a.kind), ("rebasing", "", "rebase"))

    def test_ended_unknown(self):
        from coscc.state import _activities

        [a] = _activities("0012_w", self.READ)
        self.assertEqual((a.label, a.agent, a.stage, a.kind), ("ended, unknown", "", "plan", "unknown"))

    def test_a_unit_with_nothing_and_an_empty_read_have_no_lines(self):
        from coscc.state import _activities

        self.assertEqual(_activities("0099_q", self.READ), [])
        self.assertEqual(_activities("0009_x", {}), [])

    def test_building_live_never_reads_the_tab_running_var(self):
        """R8: `running` is this tab's own press; it must not be a second source."""
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
        state = _state_class(tree)
        methods = {n.name: n for n in state.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        for fn in (functions["_activities"], methods["_apply_running"], methods["poll_running"]):
            with self.subTest(fn=fn.name):
                self.assertNotIn("self.running", ast.unparse(fn))
        self.assertIn("SERVICE.running", ast.unparse(methods["poll_running"]))
        decorators = [ast.unparse(d) for d in methods["poll_running"].decorator_list]
        self.assertIn("rx.event(background=True)", decorators)


class OneLoopPerTab(unittest.TestCase):
    """`0051` plan Risk 2: three presses of *Board* leave one loop, and leaving ends it.

    Driven through Reflex's own event processor, as `scripts/verify_0024.py` does.
    """

    def test_three_navigations_one_loop(self):
        import asyncio
        from unittest import mock

        from reflex.event import Event
        from reflex.istate.manager.memory import StateManagerMemory
        from reflex.istate.manager.token import BaseStateToken
        from reflex.state import State
        from reflex_base.event.processor import BaseStateEventProcessor
        from reflex_base.utils.format import format_event_handler

        from coscc import state as page

        token = "state-test-one-loop"
        calls: list[str] = []

        def running(cwd):
            calls.append(cwd)
            return {"running": {}, "unknown_end": {}}

        async def go():
            manager = StateManagerMemory()
            processor = BaseStateEventProcessor().configure(state_manager=manager)
            key = BaseStateToken(ident=token, cls=State)

            async def fire(handler: str, **payload):
                name = format_event_handler(page.StudioState.event_handlers[handler])
                await processor.enqueue(token, Event(name=name, payload=payload))
                await asyncio.sleep(0.05)

            async with processor:
                async with manager.modify_state(key) as root:
                    (await root.get_state(page.StudioState)).cwd = "/somewhere"
                for _ in range(3):
                    await fire("navigate", screen="board")
                await asyncio.sleep(0.3)
                alive = token in page._POLLING
                asked = len(calls)
                await fire("navigate", screen="sessions")
                await asyncio.sleep(0.3)
                return alive, asked, token in page._POLLING

        with mock.patch.object(page, "RUNNING_POLL", 0.1), mock.patch.object(page.SERVICE, "running", running):
            alive, asked, still = asyncio.run(go())
        self.assertTrue(alive)
        # One loop, asking every 0.1s over roughly 0.45s: about five asks. Three loops
        # would have asked about three times as often.
        self.assertLessEqual(asked, 8)
        self.assertGreaterEqual(asked, 2)
        self.assertFalse(still)

    def test_a_socket_drop_does_not_end_the_loop_a_closed_tab_does(self):
        """Review round 1, F2: Reflex unmaps a token on every drop and maps it back on
        reconnect, so one miss must not end the loop; `GONE_AFTER` misses in a row do."""
        import asyncio
        from unittest import mock

        from coscc import state as page

        token = "state-test-drop"
        gone = {"now": False}

        async def go():
            manager, processor, fire = _processor(token)
            async with processor:
                async with manager.modify_state(_key(token)) as root:
                    (await root.get_state(page.StudioState)).cwd = "/somewhere"
                await fire("navigate", screen="board")
                gone["now"] = True
                await asyncio.sleep(0.08)  # one or two misses, under GONE_AFTER
                gone["now"] = False
                await asyncio.sleep(0.3)
                after_drop = token in page._POLLING
                gone["now"] = True
                await asyncio.sleep(0.5)
                return after_drop, token in page._POLLING

        with (
            mock.patch.object(page, "RUNNING_POLL", 0.05),
            mock.patch.object(page, "GONE_AFTER", 4),
            mock.patch.object(page, "_tab_gone", lambda t: gone["now"]),
            mock.patch.object(page.SERVICE, "running", lambda cwd: {"running": {}, "unknown_end": {}}),
        ):
            after_drop, after_close = asyncio.run(go())
        self.assertTrue(after_drop)
        self.assertFalse(after_close)


class ChangingWorkspaceForgetsTheOldRead(unittest.TestCase):
    """Review round 1, F1: a unit named as one running in the workspace just left must not
    show that session in the one chosen, not even until the loop's next ask."""

    def test_same_unit_name_in_another_workspace_shows_nothing(self):
        import asyncio
        from unittest import mock

        from coscc import state as page

        token = "state-test-switch"
        running_in = {"/a": {"running": {"0009_x": [{"kind": "step", "stage": "impl",
                                                     "agent": {"glyph": "ᚢ", "name": "Uruz"},
                                                     "started": "2026-09-24T01:00:00+00:00",
                                                     "turns": None, "cost_usd": None}]},
                             "unknown_end": {}},
                      "/b": {"running": {}, "unknown_end": {}}}
        board = {"stages": [], "recording": True, "units": [
            {"name": "0009_x", "stages": [], "phase": "impl", "next": "", "blocked": True, "problems": []},
        ]}

        async def branch_here(cwd):
            return {"branch": "main"}

        async def read_board(cwd):
            return board

        async def go():
            manager, processor, fire = _processor(token)
            async with processor:
                async with manager.modify_state(_key(token)) as root:
                    studio = await root.get_state(page.StudioState)
                    studio.workspaces = [page.Workspace(id="/a"), page.Workspace(id="/b")]
                    studio.cwd = "/a"
                    studio.screen = "sessions"  # no loop: only `choose_workspace` reads
                    studio._running_read = running_in["/a"]
                await fire("choose_workspace", path="/b")
                async with manager.modify_state(_key(token)) as root:
                    studio = await root.get_state(page.StudioState)
                    return [(u.id, len(u.live)) for u in studio.units]

        with (
            mock.patch.object(page.SERVICE, "running", lambda cwd: running_in[cwd]),
            mock.patch.object(page.SERVICE, "branch_here", branch_here),
            mock.patch.object(page.SERVICE, "board", read_board),
            mock.patch.object(page.SERVICE, "sessions_for", lambda cwd, limit: {"sessions": []}),
            mock.patch.object(page.SERVICE, "activity_and_usage",
                              mock.Mock(side_effect=page.Invalid("not here"))),
        ):
            cards = asyncio.run(go())
        self.assertEqual(cards, [("0009_x", 0)])


def _key(token: str):
    from reflex.istate.manager.token import BaseStateToken
    from reflex.state import State

    return BaseStateToken(ident=token, cls=State)


def _processor(token: str):
    """Reflex's own event processor over a memory state manager, and a `fire` for it."""
    import asyncio

    from reflex.event import Event
    from reflex.istate.manager.memory import StateManagerMemory
    from reflex_base.event.processor import BaseStateEventProcessor
    from reflex_base.utils.format import format_event_handler

    from coscc import state as page

    manager = StateManagerMemory()
    processor = BaseStateEventProcessor().configure(state_manager=manager)

    async def fire(handler: str, **payload):
        name = format_event_handler(page.StudioState.event_handlers[handler])
        await processor.enqueue(token, Event(name=name, payload=payload))
        await asyncio.sleep(0.05)

    return manager, processor, fire


def _self_names(target: ast.expr) -> list[str]:
    """Every `self.X` being assigned by one target, tuple unpacking included."""
    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
        return [target.attr] if target.value.id == "self" else []
    if isinstance(target, (ast.Tuple, ast.List)):
        return [name for item in target.elts for name in _self_names(item)]
    return []


class TheOutcomeIsCopiedFromTheService(unittest.TestCase):
    """`0047` plan step 7. The page shows `Service.board`'s `outcome_label` and decides
    nothing; a refusal from `Service.record_outcome` reaches the page as its words."""

    def test_the_fields_map_from_the_label(self):
        from coscc.state import _outcome_fields

        self.assertEqual(_outcome_fields(None), {})
        got = _outcome_fields({
            "kind": "missed", "text": "trượt", "color": "red", "counted": True,
            "hint": "cân nhắc bỏ hoặc làm lại", "deadline": "2026-10-07", "by": "Linh",
            "date": "2026-10-08", "measured_by": "agent", "source": "board", "reason": None,
            "note": "ghi chú", "invalid": 2, "form": True,
        })
        self.assertEqual(got, {
            "outcome_text": "trượt", "outcome_color": "red", "outcome_detail": "board — ghi chú",
            "outcome_by": "Linh", "outcome_date": "2026-10-08", "outcome_measured_by": "agent",
            "outcome_deadline": "2026-10-07", "outcome_hint": "cân nhắc bỏ hoặc làm lại",
            "outcome_invalid": 2, "outcome_form": True,
        })
        self.assertEqual(_outcome_fields({"text": "không đo được", "reason": "no script"})["outcome_detail"],
                         "no script")

    def test_the_handler_shows_the_services_refusal_verbatim(self):
        import asyncio
        from types import SimpleNamespace
        from unittest import mock

        from coscc import state
        from coscc.service import Invalid

        async def refuse(*args):
            refuse.args = args
            raise Invalid("đạt needs a source: where the figure it rests on came from")

        async def never():
            raise AssertionError("a refused outcome must not reload the board")

        page = SimpleNamespace(
            cwd="/w", unit_id="0001_x", outcome_result="đạt", outcome_measured_by="agent",
            outcome_source="", outcome_reason="", outcome_note="", answer_by="Phong",
            recording_outcome=False, notice="", _load_board=never,
        )
        with mock.patch.object(state, "SERVICE", SimpleNamespace(record_outcome=refuse)):
            asyncio.run(state.StudioState.record_outcome.fn(page))
        self.assertEqual(page.notice, "đạt needs a source: where the figure it rests on came from")
        self.assertFalse(page.recording_outcome)
        self.assertEqual(refuse.args, ("/w", "0001_x", "đạt", "agent", "", "", "", "Phong"))


class AnsweringAlwaysSaysSomething(unittest.TestCase):
    """`0071` R1, R3–R6, R9. Every press of *Send this answer* ends in a block written or a
    reason shown; none ends in nothing. The handler runs in process on a stand-in page, as
    `TheOutcomeIsCopiedFromTheService` does."""

    def page(self, **over):
        from types import SimpleNamespace

        from coscc import state

        async def loaded():
            page.reloaded = True

        page = SimpleNamespace(
            cwd="/w", unit_id="0001_x", answer_target="intent.md#1", answer_text="yes",
            answer_by="Phong", answering_key="", notice="old notice", error="old error",
            reloaded=False, _load_board=loaded, _load_artifact=lambda: None,
        )
        page._fail = lambda e: state.StudioState._fail(page, e)
        for k, v in over.items():
            setattr(page, k, v)
        return page

    def press(self, page, key, answer):
        """Run the handler to its end with `answer` standing in for `Service.answer`.
        Returns what `answering_key` was at each `yield`."""
        import asyncio
        from types import SimpleNamespace
        from unittest import mock

        from coscc import state

        seen = []

        async def drive():
            async for _ in state.StudioState.answer_question.fn(page, key):
                seen.append(page.answering_key)

        with mock.patch.object(state, "SERVICE", SimpleNamespace(answer=answer)):
            asyncio.run(drive())
        return seen

    @staticmethod
    def never():
        async def answer(*args):
            raise AssertionError("a press with nothing to send must not reach the service")
        return answer

    @staticmethod
    def wrote(question="1", artifact="intent.md"):
        async def answer(*args):
            answer.args = args
            return {"question": question, "artifact": artifact, "answered_by": "Phong"}
        return answer

    def test_text_in_another_questions_box_is_named_and_kept(self):
        page = self.page()
        self.press(page, "intent.md#2", self.never())
        self.assertIn("question 2 of intent.md", page.error)
        self.assertIn("question 1 of intent.md", page.error)
        self.assertEqual((page.answer_target, page.answer_text), ("intent.md#1", "yes"))
        self.assertEqual(page.notice, "old notice")
        self.assertEqual(page.answering_key, "")

    def test_an_empty_box_names_the_button_pressed(self):
        page = self.page(answer_target="", answer_text="")
        self.press(page, "review.md#F2", self.never())
        self.assertIn("finding F2 of review.md", page.error)
        self.assertIn("empty", page.error)
        self.assertEqual(page.notice, "old notice")

    def test_a_second_press_queued_behind_the_first_keeps_what_the_first_wrote(self):
        """Review round 1, F1: a double click whose second press reaches the queue before
        the loading state reaches the browser runs after the first, on an emptied box."""
        page = self.page()
        self.press(page, "intent.md#1", self.wrote())
        self.press(page, "intent.md#1", self.never())
        self.assertTrue(page.notice.startswith("Answered question 1 of intent.md as Phong."),
                        page.notice)
        self.assertIn("Nothing was sent", page.error)
        self.assertEqual(page.answering_key, "")

    def test_a_refusal_is_shown_verbatim_and_the_text_is_kept(self):
        from coscc.service import Invalid

        async def refuse(*args):
            raise Invalid("say who is answering, on one line")

        async def never_reload():
            raise AssertionError("a refused answer must not reload the board")

        page = self.page(answer_by="", _load_board=never_reload)
        self.press(page, "intent.md#1", refuse)
        self.assertEqual(page.error, "say who is answering, on one line")
        self.assertEqual((page.answer_target, page.answer_text, page.answer_by),
                         ("intent.md#1", "yes", ""))
        self.assertEqual(page.notice, "")
        self.assertEqual(page.answering_key, "")

    def test_an_unexpected_failure_says_it_is_not_known_whether_it_was_written(self):
        async def broken(*args):
            raise RuntimeError("disk")

        page = self.page()
        self.press(page, "intent.md#1", broken)
        for part in ("RuntimeError", "disk", "intent.md", "not known whether"):
            self.assertIn(part, page.error)
        self.assertNotIn("Nothing was written", page.error)
        self.assertEqual(page.answer_text, "yes")
        self.assertEqual(page.answering_key, "")

    def test_a_written_answer_is_reported_even_when_the_reload_fails(self):
        async def broken_reload():
            raise RuntimeError("board unreadable")

        page = self.page(_load_board=broken_reload)
        self.press(page, "intent.md#1", self.wrote())
        self.assertTrue(page.notice.startswith("Answered question 1 of intent.md as Phong."),
                        page.notice)
        self.assertIn("board unreadable", page.notice + page.error)
        self.assertEqual((page.answer_target, page.answer_text), ("", ""))

    def test_a_finding_is_answered_like_a_question(self):
        answer = self.wrote("F2", "review.md")
        page = self.page(answer_target="review.md#F2")
        self.press(page, "review.md#F2", answer)
        self.assertIn("Answered finding F2 of review.md", page.notice)
        self.assertEqual(page.error, "")
        self.assertEqual(answer.args, ("/w", "0001_x", "review.md", "F2", "yes", "Phong"))
        self.assertEqual((page.answer_target, page.answer_text), ("", ""))
        self.assertTrue(page.reloaded)

    def test_the_sending_key_reaches_the_browser_before_the_service_is_called(self):
        async def answer(*args):
            answer.called_with_key = page.answering_key
            return {"question": "1", "artifact": "intent.md", "answered_by": "Phong"}

        page = self.page()
        seen = self.press(page, "intent.md#1", answer)
        self.assertEqual(seen[:1], ["intent.md#1"])
        self.assertEqual(answer.called_with_key, "intent.md#1")
        self.assertEqual(page.answering_key, "")

    def test_the_first_yield_comes_before_the_service_is_called(self):
        import asyncio
        from types import SimpleNamespace
        from unittest import mock

        from coscc import state

        page = self.page()
        answer = self.wrote()
        answer.args = None

        async def first_step():
            gen = state.StudioState.answer_question.fn(page, "intent.md#1")
            await gen.__anext__()
            at_yield = (page.answering_key, answer.args)
            await gen.aclose()
            return at_yield

        with mock.patch.object(state, "SERVICE", SimpleNamespace(answer=answer)):
            self.assertEqual(asyncio.run(first_step()), ("intent.md#1", None))

    def test_opening_a_unit_clears_the_last_notice(self):
        from types import SimpleNamespace

        from coscc import state

        page = SimpleNamespace(
            unit_id="0001_x", detail_tab="questions", run_log="x", error="e", notice="old",
            _load_timeline=lambda: None, _load_artifact=lambda: None,
        )
        state.StudioState.open_unit.fn(page, "0002_y")
        self.assertEqual((page.unit_id, page.notice, page.error), ("0002_y", "", ""))


class TheDialogDrawsTheMessagesToo(unittest.TestCase):
    """`0071` R2, R7, R8, by structure only. Whether the dialog's copy is really in view is
    `scripts/verify_0071.py`'s to measure; this only keeps both copies from disappearing."""

    def test_the_dialog_carries_its_own_sticky_copy(self):
        from coscc import screens

        drawn = str(screens._detail_dialog())
        for part in ("detail-messages", "detail-notice", "detail-error", "sticky"):
            self.assertIn(part, drawn)

    def test_the_page_keeps_the_ids_older_proofs_read(self):
        from coscc import screens

        drawn = str(screens._banners())
        self.assertIn("page-notice", drawn)
        self.assertIn("page-error", drawn)
        self.assertNotIn("detail-", drawn)


if __name__ == "__main__":
    unittest.main()
