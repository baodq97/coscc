"""Tests for `RerunMixin` in `coscc/state/rerun.py`, split from `tests/state/test_state.py`."""

from __future__ import annotations

import ast
import asyncio
import json
import unittest
from unittest import mock

from coscc import state as page
from coscc.screens.board import _rewrites_panel
from coscc.state.rerun import RerunMixin, Rewrite
from tests.state.test_state import SOURCE, _self_names, _state_class


class TheRerunHoldsNoCopyOfTheRule(unittest.TestCase):
    """The stages offered are `coscc.loop rerun`'s, copied by `load_next` alone, and only `run_rerun`
    asks the service to run one again."""

    def setUp(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
        self.methods = {
            n.name: n
            for n in _state_class(tree).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def setters_of(self, name: str) -> set[str]:
        return {
            method
            for method, fn in self.methods.items()
            for node in ast.walk(fn)
            if isinstance(node, ast.Assign) and any(name in _self_names(t) for t in node.targets)
        }

    def test_only_load_next_sets_the_offers_and_it_takes_them_from_the_service(self):
        self.assertEqual(self.setters_of("rerun_stages"), {"load_next"})
        self.assertEqual(self.setters_of("rerun_later"), {"load_next"})
        self.assertIn("SERVICE.steps.rerun_offers", ast.unparse(self.methods["load_next"]))

    def test_only_the_shared_runner_runs_a_stage_again_as_a_person(self):
        callers = {
            name
            for name, fn in self.methods.items()
            for node in ast.walk(fn)
            if isinstance(node, ast.Call)
            and ast.unparse(node.func) == "app.SERVICE.steps.run_step"
            and any(k.arg == "rerun" for k in node.keywords)
        }
        self.assertEqual(callers, {"_run_again"})
        call = next(
            n
            for n in ast.walk(self.methods["_run_again"])
            if isinstance(n, ast.Call) and ast.unparse(n.func) == "app.SERVICE.steps.run_step"
        )
        sent = {k.arg: ast.unparse(k.value) for k in call.keywords}
        self.assertEqual((sent["rerun"], sent["started_by"]), ("True", "'person'"))

    def test_only_the_two_buttons_reach_the_shared_runner(self):
        reaching = {
            name
            for name, fn in self.methods.items()
            for node in ast.walk(fn)
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "self._run_again"
        }
        self.assertEqual(reaching, {"run_rerun", "rewrite_outdated"})

    def test_only_the_board_read_sets_the_rewrites_and_it_decides_nothing(self):
        # `_load_board` only empties it before the read, as it does every board var.
        self.assertEqual(self.setters_of("rewrites"), {"_show_rewrites", "_load_board"})
        self.assertNotIn("SERVICE", ast.unparse(self.methods["_show_rewrites"]))

    def test_run_rerun_runs_in_the_background_and_asks_again_after(self):
        fn = self.methods["run_rerun"]
        self.assertIn("rx.event(background=True)", [ast.unparse(d) for d in fn.decorator_list])
        self.assertIn("return self.__class__.load_next", ast.unparse(fn))

    def test_the_choosing_handlers_call_no_service(self):
        for name in ("set_rerun_stage", "set_rerun_note", "ask_rerun", "cancel_rerun"):
            with self.subTest(handler=name):
                self.assertNotIn("SERVICE", ast.unparse(self.methods[name]))


class _Stand:
    """What `_run_again` and `rewrite_outdated` touch of the page state, with the `async with
    self` of a background event."""

    load_next = object()
    _run_again = RerunMixin._run_again

    def __init__(self, rewrites=()):
        self.rewrites = list(rewrites)
        self.cwd = "/w"
        self.notice = self.error = self.run_log = self.log_unit = ""
        self.rerun_note = "keep this"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _load_running(self):
        pass

    async def _load_board(self):
        pass

    _load_timeline = _load_artifact = _load_activity = _load_running


class OnePressRewritesTheOutdatedStageWithNoNote(unittest.TestCase):
    ROW = Rewrite(unit="0159_x", stage="spec", label="Rewrite spec: 2 new decisions")

    def press(self, stand: _Stand, unit: str):
        asked: list[tuple] = []

        async def run_step(cwd, unit, stage, **kw):
            asked.append((cwd, unit, stage, kw))
            yield "done", {"outcome": "done", "artifact": "spec.md"}

        async def go():
            return await page.StudioState.rewrite_outdated.fn(stand, unit)

        with mock.patch.object(page.app.SERVICE.steps, "run_step", run_step):
            return asked, asyncio.run(go())

    def test_it_starts_exactly_that_stage_as_a_person_with_an_empty_note(self):
        stand = _Stand([self.ROW])
        asked, after = self.press(stand, "0159_x")
        self.assertEqual(
            asked,
            [("/w", "0159_x", "spec", {"started_by": "person", "rerun": True, "note": ""})],
        )
        self.assertIs(after, _Stand.load_next)
        self.assertEqual(stand.notice, "spec done — wrote spec.md")

    def test_the_note_typed_for_the_open_unit_stays(self):
        stand = _Stand([self.ROW])
        self.press(stand, "0159_x")
        self.assertEqual(stand.rerun_note, "keep this")

    def test_a_unit_the_board_did_not_name_starts_nothing(self):
        stand = _Stand([self.ROW])
        asked, after = self.press(stand, "0001_other")
        self.assertEqual((asked, after), ([], None))
        self.assertEqual(stand.notice, "That unit has nothing to rewrite.")


class TheBoardReadFillsTheRewrites(unittest.TestCase):
    def test_a_row_with_a_rewrite_becomes_one_and_the_others_none(self):
        stand = _Stand()
        RerunMixin._show_rewrites(
            stand,
            {
                "units": [
                    {"name": "0001_a", "rewrite": None},
                    {"name": "0002_b", "rewrite": {"stage": "plan", "label": "Rewrite plan: x"}},
                    {"name": "0003_c"},
                ]
            },
        )
        self.assertEqual(stand.rewrites, [Rewrite("0002_b", "plan", "Rewrite plan: x")])


class TheBoardOffersOneButtonAndNoNoteField(unittest.TestCase):
    PANEL = json.dumps(_rewrites_panel().render(), default=str)

    def test_the_button_runs_the_press_for_the_unit_and_names_the_stage(self):
        self.assertIn("rewrite_outdated", self.PANEL)
        self.assertIn("Rewrite ", self.PANEL)
        self.assertIn("rewrite-button", self.PANEL)

    def test_there_is_no_field_to_write_a_note_in(self):
        for tag in ("textarea", "TextArea", "input", "Input"):
            self.assertNotIn(tag, self.PANEL)

    def test_it_is_drawn_only_when_a_unit_is_outdated(self):
        self.assertIn("rewrites", self.PANEL)
        self.assertIn("length", self.PANEL)
