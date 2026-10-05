"""Tests for the board's cost on a large workspace: a held answer does not grow with its finished
units or their review rounds."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.config import Config
from coscc.github import integrate
from coscc.service import Service
from tests.service.test_steps_integrate import StandIn, git

SMALL, LARGE, ROUNDS, FINDINGS = 8, 300, 5, 4


def review(unit: str) -> str:
    """A `review.md` of `ROUNDS` rounds, each asking for changes on `FINDINGS` findings."""
    out = f"# Review: {unit}\nAuthor: t. Status: changes-requested.\n"
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


class TheBoardOfALargeWorkspaceIsHeld(unittest.IsolatedAsyncioTestCase):
    """A held board answers at once on 300 finished units, and is no bigger for their rounds."""

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.services: list[Service] = []
        patch = mock.patch.object(integrate, "_gh", self._gh)
        patch.start()
        self.addCleanup(patch.stop)

    async def asyncTearDown(self):
        for service in self.services:
            await service.shutdown()

    async def _gh(self, argv, cwd):
        return 0, "[]", ""

    async def store(self, name: str, units: int) -> tuple[Service, str]:
        """A workspace of `units` units the loop reads as `finished`, each with a long review."""
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
        service = Service(config, StandIn(None))
        self.services.append(service)
        first = Path((await service.answers.create_unit(cwd, "scale-0", "fixture"))["path"])
        for n in range(units):
            unit = f"{n + 1:04d}_scale-{n}"
            directory = first.parent / (first.name if n == 0 else unit)
            directory.mkdir(exist_ok=True)
            for stage in ("intent", "spec", "plan", "impl"):
                extra = " Type: feat." if stage == "intent" else ""
                status = "done" if stage == "plan" else "accepted"
                (directory / f"{stage}.md").write_text(
                    f"# X: {unit}\nAuthor: t.{extra} Status: {status}.\n", encoding="utf-8"
                )
            (directory / "review.md").write_text(review(unit), encoding="utf-8")
        return service, cwd

    async def ended(self, service: Service) -> None:
        """Every read running now ended, and the reads an answer starts."""
        while running := [*service.boards.reads.values(), *service.steps.ci_asks.values()]:
            await asyncio.gather(*running)
            for _ in range(5):
                await asyncio.sleep(0)

    async def warm(self, name: str, units: int) -> tuple[dict, int]:
        """The board after one read, and the reads a held answer made."""
        service, cwd = await self.store(name, units)
        board = await service.board(cwd)
        await self.ended(service)
        reads = service.boards.read
        calls = []

        async def counted(*args, **kw):
            calls.append(args)
            return await reads(*args, **kw)

        with mock.patch.object(service.boards, "read", counted):
            held = await service.board(cwd, "held")
        await self.ended(service)
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
