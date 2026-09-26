"""Tests for `RerunMixin` in `coscc/state_rerun.py`, split from `coscc/state_test.py` (`0095`).
"""

from __future__ import annotations

import ast
import unittest

from coscc.state_test import SOURCE, _self_names, _state_class


class TheRerunHoldsNoCopyOfTheRule(unittest.TestCase):
    """`0054` R1, R9. The stages offered are `cos.mjs rerun`'s, copied by `load_next` alone,
    and only `run_rerun` asks the service to run one again."""

    def setUp(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
        self.methods = {
            n.name: n for n in _state_class(tree).body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def setters_of(self, name: str) -> set[str]:
        return {
            method for method, fn in self.methods.items() for node in ast.walk(fn)
            if isinstance(node, ast.Assign) and any(name in _self_names(t) for t in node.targets)
        }

    def test_only_load_next_sets_the_offers_and_it_takes_them_from_the_service(self):
        self.assertEqual(self.setters_of("rerun_stages"), {"load_next"})
        self.assertEqual(self.setters_of("rerun_later"), {"load_next"})
        self.assertIn("SERVICE.rerun_offers", ast.unparse(self.methods["load_next"]))

    def test_only_run_rerun_runs_a_stage_again(self):
        callers = {
            name for name, fn in self.methods.items() for node in ast.walk(fn)
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "SERVICE.run_step"
            and any(k.arg == "rerun" for k in node.keywords)
        }
        self.assertEqual(callers, {"run_rerun"})
        call = next(n for n in ast.walk(self.methods["run_rerun"])
                    if isinstance(n, ast.Call) and ast.unparse(n.func) == "SERVICE.run_step")
        self.assertEqual(ast.unparse(next(k.value for k in call.keywords if k.arg == "rerun")), "True")

    def test_run_rerun_runs_in_the_background_and_asks_again_after(self):
        fn = self.methods["run_rerun"]
        self.assertIn("rx.event(background=True)", [ast.unparse(d) for d in fn.decorator_list])
        self.assertIn("return StudioState.load_next", ast.unparse(fn))

    def test_the_choosing_handlers_call_no_service(self):
        for name in ("set_rerun_stage", "set_rerun_note", "ask_rerun", "cancel_rerun"):
            with self.subTest(handler=name):
                self.assertNotIn("SERVICE", ast.unparse(self.methods[name]))
