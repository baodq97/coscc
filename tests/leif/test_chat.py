"""Leif's reads in `coscc/leif/chat.py`: the chat's read-only tools on the `cos` server."""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from coscc.agent import policy
from coscc.config import Config
from coscc.http.app import build
from coscc.kernel import Invalid
from coscc.leif import chat
from coscc.runner import run as run_mod
from coscc.runner import triggers
from tests.http.test_app import seed_unit


QUESTIONS = "# Intent: q\nAuthor: t. Type: feat.\n\n## Problem\n\nx\n\n## Open questions\n\n1. One?\n2. Two?\n"


class _App(unittest.IsolatedAsyncioTestCase):
    """One workspace with two units: one asking two questions, one shipped; three runs ended."""

    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "work" / "proj").mkdir(parents=True)
        (root / "work" / "other").mkdir(parents=True)
        self.cwd = str(root / "work" / "proj")
        self.other = str(root / "work" / "other")
        self.root = root
        self.app = build(
            Config(
                workspaces=(self.cwd, self.other),
                working_dir=str(root / "work"),
                data_dir=str(root / "data"),
            )
        )
        self.core = self.app.state.core
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://t")
        self.addAsyncCleanup(client.aclose)
        made = []
        for slug in ("asks-twice", "shipped-one"):
            r = await client.post("/api/units", json={"cwd": self.cwd, "slug": slug, "brief": slug})
            made.append(r.json())
        (self.asking, self.shipped) = (m["unit"] for m in made)
        Path(made[0]["path"], "intent.md").write_text(QUESTIONS, encoding="utf-8")
        seed_unit(self.core, self.cwd, self.asking, statuses={"intent.md": "accepted"}, type="feat")
        seed_unit(self.core, self.cwd, self.asking, questions={"intent.md": ["One?", "Two?"]})
        seed_unit(self.core, self.cwd, self.shipped, shipped=True)
        key = self.core.ws.key(self.cwd)
        for unit, stage, usd in (
            (self.asking, "intent", 0.5),
            (self.shipped, "impl", 4.0),
            (self.shipped, "review", 1.25),
        ):
            self.core.ws.journal().append(
                {
                    "kind": "end",
                    "workspace": key,
                    "unit": unit,
                    "stage": stage,
                    "agent": stage,
                    "run": f"r-{stage}",
                    "outcome": "done",
                    "cost_usd": usd,
                }
            )

    async def call(self, tool_name: str, **args) -> dict:
        (tool,) = [t for t in chat.read_tools(self.core, self.cwd) if t.name == tool_name]
        return await tool.handler(args)


class EachReadAnswersFromTheAppsOwnState(_App):
    async def test_board_counts_and_lists_open_units_first(self):
        said = await chat.read_board(self.core, self.cwd, {})
        lines = said.splitlines()
        self.assertIn("workspace proj: 2 units", lines[0])
        self.assertIn("$5.75 in all", lines[0])
        self.assertTrue(lines[3].startswith(self.asking), said)
        self.assertIn("waits on you: 2 open questions", lines[3])
        self.assertTrue(lines[4].startswith(self.shipped), said)

    async def test_unit_by_number_names_its_questions_and_runs(self):
        number = self.shipped.split("_")[0].lstrip("0")
        said = await chat.read_unit(self.core, self.cwd, {"name": number})
        self.assertTrue(said.startswith(f"proj/{self.shipped}"), said)
        self.assertIn("runs: 2, $5.25", said)
        asked = await chat.read_unit(self.core, self.cwd, {"name": self.asking})
        self.assertIn("open questions: 2", asked)
        self.assertIn("intent.md Q1: One?", asked)
        self.assertIn("brief: asks-twice", asked)

    async def test_needs_you_is_the_open_units_waiting_in_every_workspace(self):
        said = await chat.read_needs_you(self.core, self.cwd, {})
        self.assertEqual(
            said.splitlines(),
            [
                "waiting on you: 1 unit, open units only",
                f"proj/{self.asking} · 2 open questions · at spec",
            ],
        )

    async def test_spend_holds_today_against_the_cap_and_the_costliest_units(self):
        said = await chat.read_spend(self.core, self.cwd, {})
        self.assertRegex(
            said.splitlines()[0], r"^today, every workspace: \$5\.75 of the \$\d+\.\d\d daily cap"
        )
        self.assertIn("the last 30 days: $5.75 over 3 runs", said)
        self.assertIn(f"- {self.shipped}: $5.25 over 2 runs (impl $4.00 x1, review $1.25 x1)", said)
        self.assertIn("- impl: $4.00, 1 runs", said)

    async def test_agents_lists_each_row_with_its_model_and_ceilings(self):
        said = await chat.read_agents(self.core, self.cwd, {})
        leif = next(line for line in said.splitlines() if line.startswith("leif "))
        self.assertIn("≤10 turns, $1.00", leif)
        self.assertRegex(said.splitlines()[0], r"^\d+ agents:")

    async def test_a_named_workspace_is_read_and_a_long_list_is_cut_with_a_count(self):
        self.assertIn(
            "workspace other: 0 units",
            (await self.call("board", workspace="other"))["content"][0]["text"],
        )
        with mock.patch.object(chat, "READ_BUDGET", 10):
            said = chat._fit(["head"], ["a" * 4, "b" * 4, "c"])
        self.assertEqual(said, "head\naaaa\n... 2 more not shown")


class AFolderThatIsNoWorkspaceIsRefused(_App):
    async def test_every_read_that_takes_one_refuses_it(self):
        for name in ("board", "spend", "agents"):
            got = await self.call(name, workspace=str(self.root))
            self.assertTrue(got.get("is_error"), name)
            self.assertIn("not a configured workspace", got["content"][0]["text"])
        got = await self.call("unit", name="1", workspace="/etc")
        self.assertTrue(got.get("is_error"))
        with self.assertRaises(Invalid):
            chat.where(self.core, self.cwd, "../proj")


class NoReadWritesAnything(_App):
    def snapshot(self) -> dict:
        out = {}
        for f in sorted(self.root.rglob("*")):
            if f.is_file() and f.suffix == ".db":
                with sqlite3.connect(f"file:{f}?mode=ro", uri=True) as conn:
                    tables = [
                        r[0]
                        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    ]
                    out[str(f)] = {
                        t: conn.execute(f'SELECT * FROM "{t}"').fetchall() for t in tables
                    }
            elif f.is_file() and not f.name.endswith(("-wal", "-shm")):
                out[str(f)] = hashlib.sha256(f.read_bytes()).hexdigest()
        return out

    async def test_the_data_and_the_workspace_are_the_same_after_every_read(self):
        await self.core.board(self.cwd, "held")
        before = self.snapshot()
        for name, args in (
            ("board", {}),
            ("unit", {"name": self.asking}),
            ("needs_you", {}),
            ("spend", {}),
            ("agents", {}),
        ):
            got = await self.call(name, **args)
            self.assertFalse(got.get("is_error"), got)
        self.assertEqual(self.snapshot(), before)


class TheChatIsGrantedTheReads(_App):
    async def test_the_server_holds_them_and_the_start_names_them(self):
        server = triggers.leif_server(self.core, self.cwd, chat.read_tools(self.core, self.cwd))
        self.assertEqual(server["type"], "sdk")
        given = []

        async def run(agent, inp, ctx):
            given.append(inp)
            yield ("done", run_mod.Run("done", None))

        with mock.patch.object(run_mod, "run", run):
            async for _ in self.core.chat.stream(self.cwd, "hi"):
                pass
        self.assertEqual(given[0].mcp, policy.LEIF_TOOLS)
        grant = run_mod.issue(
            policy.row_for("leif"), self.core.sessions, cwd=self.cwd, mcp=given[0].mcp
        )
        self.assertEqual(
            [g for g in policy.granted(grant) if g in ("run_agent", *policy.LEIF_READS)],
            ["run_agent", "board", "unit", "needs_you", "spend", "agents"],
        )
