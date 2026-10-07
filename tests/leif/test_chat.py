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
from coscc.store.db import Data
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
        # A read leaves the next board read running in the background; it ends before the
        # folder it writes in is removed (cleanups run last in, first out).
        self.addAsyncCleanup(self.core.shutdown)
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

    async def test_proposals_lists_newest_first_one_agents_and_decided_only_when_asked(self):
        from coscc.units import proposals

        data = Data(str(self.root / "data"))
        key = self.core.ws.key(self.cwd)
        first, second = proposals.add(
            data,
            key,
            "telemetry-audit",
            "",
            [
                {"type": "fix", "slug": "a-one", "title": "First", "problem": "p" * 900},
                {"type": "feat", "slug": "b-two", "title": "Second", "problem": "short"},
            ],
        )
        await proposals.dismiss(data, key, first, "not now")
        said = await chat.read_proposals(self.core, self.cwd, {})
        lines = said.splitlines()
        self.assertEqual(lines[0], "workspace proj: 1 pending proposals, newest first")
        self.assertEqual(len(lines), 2)
        self.assertIn(f"#{second} · pending · telemetry-audit · feat · Second", lines[1])
        full = await chat.read_proposals(self.core, self.cwd, {"include_decided": True})
        last = full.splitlines()[-1]
        self.assertIn(f"#{first} · dismissed", last)
        self.assertIn("by owner: not now", last)
        self.assertNotIn("p" * 401, last)
        for asked, n in (("telemetry", 1), ("telemetry-audit", 1), ("scan", 0)):
            said = await chat.read_proposals(self.core, self.cwd, {"agent": asked})
            self.assertEqual(len(said.splitlines()) - 1, n, asked)

    async def test_runs_names_an_agents_last_runs_with_what_they_proposed_and_said(self):
        from coscc.units import proposals

        key = self.core.ws.key(self.cwd)
        journal = self.core.ws.journal()
        for run, by in (("t-old", "schedule"), ("t-new", "leif")):
            journal.started(key, "", "scan", "manual", run=run, started_by=by)
            journal.finished(key, "", "scan", "done", agent="scan", run=run, cost_usd=0.08)
        data = Data(str(self.root / "data"))
        item = {"type": "fix", "slug": "a-one", "title": "Reruns cost", "problem": "p" * 250}
        (pid,) = proposals.add(data, key, "scan", "", [item], run="t-new")
        with mock.patch.object(chat.ask, "last_words", lambda d, run: f"found it in {run}"):
            said = (await self.call("runs", agent="Sowilo"))["content"][0]["text"]
        lines = said.splitlines()
        self.assertIn("run t-new", lines[1])
        self.assertIn("started by leif: done, $0.08", lines[1])
        self.assertIn(f"proposed #{pid} (pending): Reruns cost", lines[2])
        self.assertIn("its last words: found it in t-new", lines[3])
        self.assertIn("run t-old", lines[4])
        # A question asked of a run is no run of its own: it is counted on the run.
        journal.finished(key, "", "ask", "done", agent="scan", run="q1", parent_run="t-new")
        with mock.patch.object(chat.ask, "last_words", lambda d, run: ""):
            again = await chat.read_runs(self.core, self.cwd, {"agent": "scan"})
        self.assertIn("run t-new", again.splitlines()[1])
        self.assertIn("asked 1 question(s) since", again.splitlines()[1])
        none = await chat.read_runs(self.core, self.cwd, {"agent": "dagaz"})
        self.assertEqual(none.splitlines()[1], "- none")

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
        for name in ("board", "spend", "agents", "proposals"):
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
            ("proposals", {"include_decided": True}),
        ):
            got = await self.call(name, **args)
            self.assertFalse(got.get("is_error"), got)
        self.assertEqual(self.snapshot(), before)


class TheChatIsGrantedTheReads(_App):
    async def test_the_server_holds_them_and_the_start_names_them(self):
        server = triggers.leif_server(self.core, self.cwd, chat.read_tools(self.core, self.cwd), {})
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
            ["run_agent", "board", "unit", "needs_you", "spend", "agents", "proposals", "runs"],
        )


class TalkListsConversationsNotRuns(_App):
    """Which sessions Talk shows and may write to comes from the run log, so a restart keeps it,
    and an agent's run is never continued as a conversation."""

    def ended(self, stage: str, session: str) -> None:
        self.core.ws.journal().finished(
            self.core.ws.key(self.cwd), "", stage, "done", agent=stage, session_id=session
        )

    def listed(self, core) -> dict[str, bool]:
        rows = [
            {"session_id": s, "summary": s, "cwd": self.cwd, "last_modified": 1}
            for s in ("c1", "a1", "t1")
        ]
        rows = [{**r, "created_at": None, "git_branch": None} for r in rows]
        with mock.patch.object(chat.reader, "list_for_directory", return_value=rows):
            got = core.chat.sessions_for(self.cwd)["sessions"]
        return {r["session_id"]: r["resumable"] for r in got}

    async def test_a_chat_is_resumable_a_terminals_read_only_and_an_agents_run_absent(self):
        self.ended("chat", "c1")
        self.ended("scan", "a1")
        self.assertEqual(self.listed(self.core), {"c1": True, "t1": False})
        self.assertTrue(self.core.sessions.known("c1"))
        self.assertFalse(self.core.sessions.known("t1"))

    async def test_after_a_restart_the_same(self):
        self.ended("chat", "c1")
        again = build(self.core.config)
        self.addAsyncCleanup(again.state.core.shutdown)
        self.assertEqual(self.listed(again.state.core)["c1"], True)
        self.assertTrue(again.state.core.sessions.known("c1"))

    async def test_an_agents_run_is_refused_as_a_conversation_before_anything_opens(self):
        self.ended("scan", "a1")
        with self.assertRaises(Invalid) as e:
            self.core.chat.check_send(self.cwd, "hi", "a1")
        self.assertEqual(e.exception.reasons, ("no-run",))
        self.ended("chat", "c1")
        self.core.chat.check_send(self.cwd, "hi", "c1")
        # Another workspace's run or chat, and a session both a chat and a run, are refused too.
        journal = self.core.ws.journal()
        journal.finished("/elsewhere", "", "scan", "done", agent="scan", session_id="x1")
        journal.finished("/elsewhere", "", "chat", "done", agent="leif", session_id="x2")
        journal.finished("/elsewhere", "", "ask", "done", agent="scan", session_id="c1")
        for sid in ("x1", "x2", "c1"):
            with self.assertRaises(Invalid) as e:
                self.core.chat.check_send(self.cwd, "hi", sid)
            self.assertEqual(e.exception.reasons, ("no-run",), sid)


class LeifHearsOfTheRunsItStarted(_App):
    """A run Leif started shows in its conversation once it ends, and Leif is told on its next
    turn, from the run log; the note never shows as the person's words."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        key, j = self.core.ws.key(self.cwd), self.core.ws.journal()
        j.started(key, "", "chat", "manual", agent="leif", run="c1", said="run the scan")
        j.finished(key, "", "chat", "done", agent="leif", run="c1", session_id="S")
        j.started(
            key, "", "scan", "manual", run="a1", started_by="leif", agent="scan", chat_run="c1"
        )
        j.finished(
            key,
            "",
            "scan",
            "done",
            agent="scan",
            run="a1",
            agent_name="Sowilo",
            proposals=2,
            cost_usd=0.08,
        )

    async def test_the_conversation_lists_it_and_the_next_turn_is_told(self):
        said = [{"role": "user", "text": "hi", "uuid": "u"}]
        with mock.patch.object(chat.reader, "history", return_value=said):
            got = self.core.chat.history(self.cwd, "S")
        (run,) = got["runs"]
        self.assertEqual(
            (run["run"], run["name"], run["outcome"], run["proposals"]), ("a1", "Sowilo", "done", 2)
        )
        # Found by the words of the turn that started it: shown under that turn's answer.
        self.assertEqual(run["said"], "run the scan")
        prompts, starts = [], []

        async def run_(agent, inp, ctx):
            prompts.append(inp.prompt)
            starts.append(dict(inp.start))
            yield ("done", run_mod.Run("done", None, session="S"))

        with (
            mock.patch.object(run_mod, "run", run_),
            mock.patch.object(self.core.sessions, "known", lambda s: True),
        ):
            async for _ in self.core.chat.stream(self.cwd, "what did it find?", "S"):
                pass
        self.assertTrue(prompts[0].startswith(chat.NOTE_HEAD))
        self.assertIn("Sowilo (scan) run a1: done, 2 proposals, $0.08", prompts[0])
        self.assertTrue(prompts[0].endswith(f"{chat.NOTE_END}\nwhat did it find?"))
        # Once a turn has heard it, the next one is not told again.
        self.assertEqual(starts[0], {"said": "what did it find?", "told": ["a1"]})
        key, j = self.core.ws.key(self.cwd), self.core.ws.journal()
        j.started(key, "", "chat", "manual", run="c2", agent="leif", **starts[0])
        j.finished(key, "", "chat", "done", agent="leif", run="c2", session_id="S")
        self.assertEqual(self.core.chat._note("S"), ("", []))
        with mock.patch.object(
            chat.reader, "history", return_value=[{"role": "user", "text": prompts[0], "uuid": "v"}]
        ):
            self.assertEqual(
                self.core.chat.history(self.cwd, "S")["messages"][0]["text"], "what did it find?"
            )


class LeifsYesComesFromALaterTurn(_App):
    """Through the chat itself: the turn Leif asked in and the turn the person said yes in are
    two turns of one conversation, each with its own run, so the second call starts the run. A
    kept client keeps the server its first turn was given, so that server must hear each turn."""

    async def test_ask_then_yes_in_the_next_turn_starts_it(self):
        turns, said = [], []

        def server(core, cwd, reads=(), turn=None):
            turns.append(turn)
            return {"type": "sdk"}

        async def run_(agent, inp, ctx):
            ctx.journal.started(inp.workspace, "", "chat", "manual", run=inp.run, agent="leif")
            args = {"key": "scan", "reason": "asked", **({"confirmed": True} if said else {})}
            # What `leif_call` starts with: no run is spawned to outlive the test's folder.
            with mock.patch.object(triggers, "_hold", return_value="r9"):
                # The server the client was built with: the first turn's.
                said.append(await triggers.leif_call(self.core, self.cwd, args, turns[0]))
            ctx.journal.finished(
                inp.workspace, "", "chat", "done", agent="leif", run=inp.run, session_id="S"
            )
            yield ("done", run_mod.Run("done", None, session="S", run=inp.run))

        self.addCleanup(triggers._ASKED.clear)
        with (
            mock.patch.object(triggers, "leif_server", server),
            mock.patch.object(run_mod, "run", run_),
            mock.patch.object(self.core.sessions, "known", lambda s: True),
        ):
            async for _ in self.core.chat.stream(self.cwd, "run scan"):
                pass
            async for _ in self.core.chat.stream(self.cwd, "yes", "S"):
                pass
        self.assertIn("needs-confirm", said[0]["content"][0]["text"])
        self.assertFalse(said[1].get("is_error"), said[1])
        self.assertIn("run r9", said[1]["content"][0]["text"])
        self.assertEqual(triggers._TASKS, set())
