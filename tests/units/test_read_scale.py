"""Tests for the board's cost on a large workspace: a held answer does not grow with its finished
units or their review rounds."""

from __future__ import annotations

import asyncio
import json
import statistics
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from coscc.config import Config
from coscc.github import integrate
from coscc.http.app import Core
from tests.github.test_integration import StandIn, git

SMALL, LARGE, ROUNDS, FINDINGS = 8, 300, 5, 4


def review(unit: str) -> str:
    """A `review.md` of `ROUNDS` rounds, each with `FINDINGS` findings, as prose: the text the
    board must not carry."""
    out = f"# Review: {unit}\n"
    for n in range(1, ROUNDS + 1):
        found = "\n".join(
            f"- F{i} [fixed {n:07x}] a long sentence about what the reviewer found, number {i}"
            for i in range(1, FINDINGS + 1)
        )
        out += (
            f"\n## Round {n}\n\nReviewed: {n:07x}. Verdict: changes-requested.\n\n"
            f"### Findings\n\n{found}\n\n### What was not reviewed\n\nNothing.\n"
        )
    return out


def rounds(unit: str) -> list[dict]:
    """`ROUNDS` review rounds, each asking for changes on `FINDINGS` findings, as the review stage submits them."""
    return [
        {
            "n": n,
            "run": f"r{n}",
            "head": f"{n:07x}",
            "object": {
                "verdict": "changes-requested",
                "findings": [
                    {
                        "id": f"F{i}",
                        "state": "fixed",
                        "fixed_in": f"{n:07x}",
                        "severity": "low",
                        "criterion": "R1",
                        "path": "",
                        "lines": "",
                        "text": f"a long sentence about what the reviewer found, number {i}",
                    }
                    for i in range(1, FINDINGS + 1)
                ],
            },
        }
        for n in range(1, ROUNDS + 1)
    ]


class TheBoardOfALargeWorkspaceIsHeld(unittest.IsolatedAsyncioTestCase):
    """A held board answers at once on 300 finished units, and is no bigger for their rounds."""

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.cores: list[Core] = []
        patch = mock.patch.object(integrate, "_gh", self._gh)
        patch.start()
        self.addCleanup(patch.stop)

    async def asyncTearDown(self):
        for core in self.cores:
            await core.shutdown()

    async def _gh(self, argv, cwd):
        return 0, "[]", ""

    async def store(self, name: str, units: int) -> tuple[Core, str]:
        """A workspace of `units` units the loop reads as `finished`, each with a long review and two
        runs in the run log."""
        workspace = self.root / name / "proj"
        workspace.mkdir(parents=True)
        git(workspace, "init", "-q", "-b", "main")
        git(workspace, "commit", "-q", "--allow-empty", "-m", "seed")
        cwd = str(workspace)
        config = Config(
            workspaces=(cwd,),
            working_dir=str(self.root / name),
            data_dir=str(self.root / name / "data"),
        )
        core = Core(config, StandIn(None))
        self.cores.append(core)
        first = Path((await core.answers.create_unit(cwd, "scale-0", "fixture"))["path"])
        meta = core.ws.unit_meta()
        key = core.ws.key(cwd)
        # One transaction for every unit: `seed` commits one by one, which 300 units cannot afford.
        names = [first.name if n == 0 else f"{n + 1:04d}_scale-{n}" for n in range(units)]
        for unit in names:
            (first.parent / unit).mkdir(exist_ok=True)
            (first.parent / unit / "review.md").write_text(review(unit), encoding="utf-8")
        stated = dict.fromkeys(("intent.md", "spec.md", "plan.md", "impl.md"), "accepted")
        stated["review.md"] = "changes-requested"
        with meta.data.write() as conn:
            for unit in names:
                meta.add_unit(conn, key, unit)
                for submitted in rounds(unit):
                    meta.record_round(conn, key, unit, submitted)
            meta.history.record_in(
                conn,
                [
                    {
                        "workspace": key,
                        "unit": unit,
                        "artifact": artifact,
                        "to_state": state,
                        "actor": "test",
                        "session": "test",
                        "source": "test",
                    }
                    for unit in names
                    for artifact, state in stated.items()
                ]
                + [
                    {
                        "workspace": key,
                        "unit": unit,
                        "artifact": "ship.md",
                        "to_state": "accepted",
                        "source": "prmachine:merged",
                        "guard": "merge-read",
                        "authority": "code",
                    }
                    for unit in names
                ],
            )
        log = core.ws.journal()
        assert log is not None
        for unit in names:
            for stage in ("impl", "review"):
                log.started(key, unit, stage, "autonomous")
                log.finished(key, unit, stage, "done")
        return core, cwd

    async def ended(self, core: Core) -> None:
        """Every read running now ended, and the reads an answer starts."""
        while running := [*core.boards.reads.values(), *core.integration.ci_asks.values()]:
            await asyncio.gather(*running)
            for _ in range(5):
                await asyncio.sleep(0)

    async def warm(self, name: str, units: int) -> tuple[dict, int]:
        """The board after one read, and the reads a held answer made."""
        core, cwd = await self.store(name, units)
        board = await core.board(cwd)
        await self.ended(core)
        reads = core.boards.read
        calls = []

        async def counted(*args, **kw):
            calls.append(args)
            return await reads(*args, **kw)

        with mock.patch.object(core.boards, "read", counted):
            held = await core.board(cwd, "held")
        await self.ended(core)
        self.assertEqual(held["read_at"], board["read_at"])
        return held, len(calls)

    async def test_a_held_answer_on_300_finished_units_is_as_quick_as_on_8(self):
        small, small_reads = await self.warm("small", SMALL)
        large, large_reads = await self.warm("large", LARGE)
        self.assertEqual({u["why"] for u in small["units"]}, {"finished"})
        self.assertEqual({u["why"] for u in large["units"]}, {"finished"})
        self.assertEqual((len(small["units"]), len(large["units"])), (SMALL, LARGE))
        # It reads nothing, so no size of workspace can make it slower.
        self.assertEqual((small_reads, large_reads), (0, 0))

    async def median_read(self, name: str, units: int) -> float:
        """Seconds the median of 10 standalone `Board.read`s takes on `units` finished units, after
        one read to warm the files and the caches."""
        core, cwd = await self.store(name, units)
        await core.boards.read(cwd)
        await self.ended(core)
        took = []
        for _ in range(10):
            began = time.perf_counter()
            await core.boards.read(cwd)
            took.append(time.perf_counter() - began)
            await self.ended(core)
        return statistics.median(took)

    async def test_a_standalone_read_of_300_finished_units_takes_at_most_half_a_second(self):
        took = await self.median_read("many", LARGE)
        print(f"median read: {LARGE} units {took:.4f}s", flush=True)
        self.assertLessEqual(took, 0.5)

    async def test_the_rounds_of_300_units_carry_no_text_and_stay_small(self):
        large, _ = await self.warm("large", LARGE)
        rounds = [u["rounds"] for u in large["units"]]
        self.assertEqual({len(r) for r in rounds}, {ROUNDS})
        for unit in rounds:
            for rnd in unit:
                self.assertNotIn("text", rnd)
        # A round is a dozen counters; its text alone (the file's lines, one per finding) would
        # take it past 1 KB.
        size = len(json.dumps(rounds))
        self.assertLessEqual(size, LARGE * ROUNDS * 512)
