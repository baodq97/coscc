"""Tests for what a feature hands the agent's runs: the catalog, names, and what is on for a
workspace and held by a run's row and grant."""

from __future__ import annotations

import unittest
from pathlib import Path

from coscc import kernel
from coscc.agent import policy
from coscc.kernel import Block, Grant, Hooks, Parts, Row, Tool


def tool(server="fake", names=("ping",), name="", when=None) -> Tool:
    return Tool(
        name or server,
        "read",
        "low",
        server,
        names,
        lambda f: {"name": server},
        **({"when": when} if when else {}),
    )


class AToolNamesItsServerAndItsNamesInTheKernelsSpelling(unittest.TestCase):
    def test_the_server_cos_is_refused(self):
        with self.assertRaises(ValueError):
            tool(server="cos")

    def test_a_server_that_is_not_lowercase_dashes_and_digits_is_refused(self):
        for server in ("Fake", "fa_ke", "1fake", "fake ", "fa ke", "fake\n"):
            with self.subTest(server=server), self.assertRaises(ValueError):
                tool(server=server, name="x")
        tool(server="fa-ke2")

    def test_a_local_name_with_double_underscore_or_capitals_is_refused(self):
        for name in ("a__b", "Ping", "pIng", "", "1a", "a-b", "a b", "a\n"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                tool(names=(name,))
        tool(names=("ping_2",))

    def test_a_server_needs_its_names_and_its_maker(self):
        with self.assertRaises(ValueError):
            Tool("x", "read", "low", "x")
        with self.assertRaises(ValueError):
            Tool("x", "read", "low", "x", ("ping",))

    def test_granted_derives_the_full_names_in_order(self):
        got = kernel.granted((tool(names=("a", "b_c")), tool(server="other-x", names=("z",))))
        self.assertEqual(got, ("mcp__fake__a", "mcp__fake__b_c", "mcp__other-x__z"))
        self.assertEqual(kernel.granted(()), ())


class TheCatalogHoldsTheBuiltinsAndEveryFeaturesTools(unittest.TestCase):
    def test_every_tool_a_row_may_name_is_in_it(self):
        catalog = Hooks().catalog()
        for name in (*policy.READ_TOOLS, *policy.WRITE_TOOLS, "Bash", "Agent", "SendMessage"):
            self.assertIn(name, catalog)
        self.assertEqual(catalog["Read"].effect, "read")
        self.assertEqual(catalog["Write"].effect, "write-worktree")
        self.assertNotIn("vault", catalog)

    def test_a_features_tool_joins_on_or_off(self):
        a = tool(server="a")
        h = Hooks(parts=(("a", Parts(tools=(a,))),), enabled=lambda _f, _w: False)
        self.assertIs(h.catalog()["a"], a)
        self.assertIn("a", h.reads())
        self.assertNotIn("Write", h.reads())

    def test_the_app_registers_vault_and_codegraph(self):
        from coscc.features.codegraph import agent as codegraph_agent
        from coscc.features.vault import agent as vault_agent
        from unittest import mock

        ctx = mock.MagicMock()
        h = Hooks(
            parts=(
                ("vault", vault_agent(ctx, store_of=lambda _c: mock.MagicMock())),
                ("codegraph", codegraph_agent(ctx)),
            )
        )
        catalog = h.catalog()
        self.assertEqual(catalog["vault"].names, ("vault_list", "vault_exec", "vault_generate"))
        self.assertEqual(catalog["vault"].effect, "external")
        self.assertEqual(catalog["codegraph"].names, ("find", "callers", "impact"))
        self.assertEqual(catalog["codegraph"].effect, "read")
        for key, row in policy.ROWS.items():
            self.assertEqual([t for t in row.tools if t not in catalog], [], key)


class HooksKeepOnlyWhatIsOnAndHeldByTheRow(unittest.TestCase):
    def test_by_the_rows_names(self):
        a, b = tool(server="a"), tool(server="b")
        h = Hooks(parts=(("f", Parts(tools=(a, b))),))
        self.assertEqual(h.held(Row(tools=("Read", "a")), "/w"), (a,))
        self.assertEqual(h.held(Row(tools=("Read",)), "/w"), ())

    def test_by_feature_and_workspace(self):
        a, b = tool(server="a"), tool(server="b")
        h = Hooks(
            parts=(("a", Parts(tools=(a,))), ("b", Parts(tools=(b,)))),
            enabled=lambda feature, workspace: (feature, workspace) != ("b", "/w"),
        )
        row = Row(tools=("a", "b"))
        self.assertEqual(h.held(row, "/w"), (a,))
        self.assertEqual(h.held(row, "/other"), (a, b))

    def test_a_block_shows_only_to_a_run_holding_its_tool(self):
        taught, plain = Block("t", lambda f: "x", tool="a"), Block("p", lambda f: "y")
        h = Hooks(parts=(("f", Parts(blocks=(taught, plain))),))
        held = _facts(None, grant=Grant(held=("a",)))
        builtin = _facts(None, grant=Grant(tools=("a",)))
        self.assertEqual(h.blocks_for(held), (taught, plain))
        self.assertEqual(h.blocks_for(builtin), (taught, plain))
        self.assertEqual(h.blocks_for(_facts(None)), (plain,))


class AToolIsGrantedOnlyToARunItsWhenLetsThrough(unittest.TestCase):
    ROW = Row(tools=("armed", "plain", "t"))

    def test_the_even_unit_of_a_pilot_gets_the_tool_and_the_odd_one_does_not(self):
        armed = tool(server="armed", when=_even)
        plain = tool(server="plain")
        h = Hooks(parts=(("a", Parts(tools=(armed, plain))),))
        even = _facts(None, unit="0002_u")
        odd = _facts(None, unit="0003_u")
        self.assertEqual(h.tools_for(even, self.ROW), (armed, plain))
        self.assertEqual(h.tools_for(odd, self.ROW), (plain,))
        self.assertEqual(kernel.granted(h.tools_for(odd, self.ROW)), ("mcp__plain__ping",))

    def test_a_tool_off_for_the_workspace_is_not_asked(self):
        asked = []
        t = tool(server="t", when=lambda f: asked.append(f) or True)
        h = Hooks(parts=(("a", Parts(tools=(t,))),), enabled=lambda _f, _w: False)
        self.assertEqual(h.tools_for(_facts(None), self.ROW), ())
        self.assertEqual(asked, [])


def _even(facts) -> bool:
    return int(facts.unit[:4]) % 2 == 0


def _facts(watch, unit="0001_u", agent="spike", grant=None):
    return kernel.facts(
        workspace="/w",
        workspace_key="k",
        unit=unit,
        agent=agent,
        run="r",
        cwd="/scratch",
        watch=watch,
        directory=Path("/w/.cos/0001_u"),
        resumed=False,
        grant=grant,
    )


class FactsFollowTheRunsTree(unittest.TestCase):
    def test_a_spike_watches_a_tree_and_owns_a_scratch(self):
        f = _facts("/tree")
        self.assertEqual((f.tree, f.scratch), ("/tree", "/scratch"))

    def test_any_other_run_works_in_its_cwd_and_has_no_scratch(self):
        f = _facts(None)
        self.assertEqual((f.tree, f.scratch), ("/scratch", None))
        self.assertEqual((f.workspace_key, f.resumed), ("k", False))
        self.assertFalse(hasattr(f, "commands"))
        self.assertFalse(hasattr(f, "stage"))
        self.assertEqual(f.grant, Grant())

    def test_a_feature_gets_the_critical_check_and_no_command_list(self):
        self.assertTrue(callable(kernel.bash_refused))
        self.assertFalse(hasattr(kernel, "Places"))
        self.assertFalse(hasattr(kernel, "check_command"))
