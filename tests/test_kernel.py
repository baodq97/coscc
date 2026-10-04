"""Tests for what a feature hands the agent's steps: names, the stages a tool is meant for, and
what is on for a workspace."""

from __future__ import annotations

import unittest
from pathlib import Path

from coscc import kernel
from coscc.kernel import Hooks, Parts, Tool


def tool(server="fake", names=("ping",), stages=("impl",)) -> Tool:
    return Tool(server=server, names=names, stages=stages, make=lambda f: {"name": server})


class AToolNamesItsServerAndItsNamesInTheKernelsSpelling(unittest.TestCase):
    def test_the_server_cos_is_refused(self):
        with self.assertRaises(ValueError):
            tool(server="cos")

    def test_a_server_that_is_not_lowercase_dashes_and_digits_is_refused(self):
        for server in ("Fake", "fa_ke", "1fake", "", "fake ", "fa ke", "fake\n"):
            with self.subTest(server=server), self.assertRaises(ValueError):
                tool(server=server)
        tool(server="fa-ke2")

    def test_a_local_name_with_double_underscore_or_capitals_is_refused(self):
        for name in ("a__b", "Ping", "pIng", "", "1a", "a-b", "a b", "a\n"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                tool(names=(name,))
        tool(names=("ping_2",))

    def test_granted_derives_the_full_names_in_order(self):
        got = kernel.granted((tool(names=("a", "b_c")), tool(server="other-x", names=("z",))))
        self.assertEqual(got, ("mcp__fake__a", "mcp__fake__b_c", "mcp__other-x__z"))
        self.assertEqual(kernel.granted(()), ())


class HooksKeepOnlyWhatIsOnAndMeantForTheStage(unittest.TestCase):
    def test_by_stage(self):
        impl, plan = tool(stages=("impl",)), tool(server="p", stages=("plan", "impl"))
        h = Hooks(parts=(("a", Parts(tools=(impl, plan))),))
        self.assertEqual(h.for_step("impl", "/w").tools, (impl, plan))
        self.assertEqual(h.for_step("plan", "/w").tools, (plan,))
        self.assertEqual(h.for_step("spec", "/w").tools, ())

    def test_by_feature_and_workspace(self):
        a, b = tool(server="a"), tool(server="b")
        h = Hooks(
            parts=(("a", Parts(tools=(a,))), ("b", Parts(tools=(b,)))),
            enabled=lambda feature, workspace: (feature, workspace) != ("b", "/w"),
        )
        self.assertEqual(h.for_step("impl", "/w").tools, (a,))
        self.assertEqual(h.for_step("impl", "/other").tools, (a, b))

    def test_the_default_is_no_parts_and_always_on(self):
        self.assertEqual(Hooks().for_step("impl", "/w"), Parts())
        self.assertTrue(Hooks().enabled("x", "/w"))


class AToolIsGrantedOnlyToARunItsWhenLetsThrough(unittest.TestCase):
    def test_the_even_unit_of_a_pilot_gets_the_tool_and_the_odd_one_does_not(self):
        armed = Tool("armed", ("ping",), ("impl",), lambda f: {}, when=_even)
        plain = tool(server="plain")
        h = Hooks(parts=(("a", Parts(tools=(armed, plain))),))
        even = FactsFollowTheRunsTree._facts(None, None, unit="0002_u", stage="impl")
        odd = FactsFollowTheRunsTree._facts(None, None, unit="0003_u", stage="impl")
        self.assertEqual(h.tools_for(even), (armed, plain))
        self.assertEqual(h.tools_for(odd), (plain,))
        self.assertEqual(kernel.granted(h.tools_for(odd)), ("mcp__plain__ping",))

    def test_a_tool_off_for_the_workspace_is_not_asked(self):
        asked = []
        t = Tool("t", ("ping",), ("impl",), lambda f: {}, when=lambda f: asked.append(f) or True)
        h = Hooks(parts=(("a", Parts(tools=(t,))),), enabled=lambda _f, _w: False)
        self.assertEqual(h.tools_for(FactsFollowTheRunsTree._facts(None, None, stage="impl")), ())
        self.assertEqual(asked, [])


def _even(facts) -> bool:
    return int(facts.unit[:4]) % 2 == 0


class FactsFollowTheRunsTree(unittest.TestCase):
    def _facts(self, watch, unit="0001_u", stage="spike"):
        return kernel.facts(
            workspace="/w",
            workspace_key="k",
            unit=unit,
            stage=stage,
            run="r",
            cwd="/scratch",
            watch=watch,
            directory=Path("/w/.cos/0001_u"),
            commands=("git",),
            resumed=False,
        )

    def test_a_spike_watches_a_tree_and_owns_a_scratch(self):
        f = self._facts("/tree")
        self.assertEqual((f.tree, f.scratch), ("/tree", "/scratch"))

    def test_any_other_run_works_in_its_cwd_and_has_no_scratch(self):
        f = self._facts(None)
        self.assertEqual((f.tree, f.scratch), ("/scratch", None))
        self.assertEqual((f.workspace_key, f.commands, f.resumed), ("k", ("git",), False))
