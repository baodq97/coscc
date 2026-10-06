"""`coscc/features/codegraph/__init__.py`: the record of each run, the map block, the tools'
condition, the Settings sentence, the bus handler and the report, with a fake index."""

from __future__ import annotations

import dataclasses
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc import kernel
from coscc.store.db import Data, now
from coscc.features import codegraph
from coscc.features.codegraph import Ready, Status
from coscc.http.plugin import create_tables
from coscc.kernel import Settings, Units, arm_of
from tests.features.ctx import ctx_for

SHA = "a" * 40
KEY = "/w/proj"


class FakeIndexes:
    def __init__(self, home: Path):
        self.home = home
        self.got: Ready | str = Ready("/data/_main", SHA, 12)
        self.shown = Status("ready", SHA, now(), "")
        self._binary: Path | None = Path("/bin/node")
        self._installing = False
        self.locked = ""
        self.retried = 0
        self.scheduled: list[str] = []

    async def ensure(self, workspace: str) -> Ready | str:
        return self.got

    async def _engine(self) -> Path | str:
        return self._binary or "not installed"

    def status(self, key: str) -> Status:
        return self.shown

    def lock(self) -> str:
        return self.locked

    def retry(self) -> None:
        self.retried += 1

    def schedule(self, key: str) -> None:
        self.scheduled.append(key)


class Setup(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = "pilot"
        data = Data(self.root / "data")
        self.ctx = ctx_for(
            units=Units(lambda cwd: KEY, None, None, None, None, None),
            store=data,
            settings=Settings(
                lambda w: self.state,
                lambda w: self.state != "off",
                lambda w, unit: arm_of(self.state, unit),
                None,
                None,
            ),
        )
        create_tables(data, codegraph.FEATURE.tables)
        self.idx = FakeIndexes(self.root / "data" / "codegraph")
        patch = mock.patch.object(codegraph, "_indexes", return_value=self.idx)
        patch.start()
        self.addCleanup(patch.stop)

    def facts(self, unit: str, stage: str = "impl", run: str = "r1") -> kernel.Facts:
        """A run of `stage`: an impl's grant writes its tree, a review's reads only."""
        writes = (str(self.root),) if stage == "impl" else ()
        return kernel.facts(
            workspace="/w/proj",
            workspace_key=KEY,
            unit=unit,
            agent=stage,
            run=run,
            cwd=str(self.root),
            watch=None,
            directory=self.root,
            resumed=False,
            grant=kernel.Grant(cwd=str(self.root), write=writes),
        )

    def rows(self) -> list[tuple]:
        with self.ctx.store.connect() as conn:
            return [
                tuple(r)
                for r in conn.execute(
                    "SELECT run, unit, stage, arm, sha, map_chars, wait_ms, error "
                    "FROM codegraph_runs ORDER BY run"
                ).fetchall()
            ]

    def ready_row(self) -> None:
        with self.ctx.store.write() as conn:
            conn.execute(
                "INSERT INTO codegraph_index (workspace, path, state, sha, at, root) "
                "VALUES (?, '/w/proj', 'ready', ?, ?, '/data/_main')",
                (KEY, SHA, now()),
            )


class TheImplMapAsksForThePlanRecordsFiles(Setup):
    async def test_the_files_are_the_records_not_plan_md(self):
        (self.root / "plan.md").write_text("## Files that change\n- not/this.py\n")
        facts = self.facts("0002_a")
        asked: list = []
        with (
            mock.patch.object(codegraph, "changed_files", return_value=[]),
            mock.patch.object(codegraph, "impl_map", lambda *a: asked.append(a[3]) or "map"),
        ):
            plan = {
                "impl": "routine",
                "files": ["b.py", "a.py", "b.py"],
                "steps": [],
                "rests_on": [],
            }
            with_plan = dataclasses.replace(facts, plan=plan)
            codegraph._map(self.ctx, with_plan, Path("/bin/node"), Ready("/r", SHA, 0))
            codegraph._map(self.ctx, facts, Path("/bin/node"), Ready("/r", SHA, 0))
        self.assertEqual(asked, [["a.py", "b.py"], []])


class TheToolsGoOnlyToAnOnArmRunWithAReadyIndex(Setup):
    async def test_a_tool_answers_from_the_index_and_marks_the_changed_files(self):
        self.ready_row()
        tools = {t.name: t for t in codegraph.build_tools(self.ctx, self.facts("0002_a"))}
        with (
            mock.patch.object(codegraph, "call", return_value=[]),
            mock.patch.object(codegraph, "changed_files", return_value=set()),
        ):
            got = await tools["callers"].handler({"symbol": "ensure"})
        self.assertFalse(got["is_error"])
        self.assertEqual(got["content"], [{"type": "text", "text": mock.ANY}])
        self.assertIn(SHA[:12], json.dumps(got["content"]))


if __name__ == "__main__":
    unittest.main()
