"""`0136` R17. The eight places `docs/architecture/fsm.md` §6 names where an agent's free text
became a machine decision, one class each, `Place1` to `Place8`, carrying the words of its
item. Each asserts two things: the transition went through the guard named for it, and where
the prose of an artifact says the opposite of the structured state, the structured state wins.

The file grows with `plan.md ## Order of work`: each step that moves a place adds its class.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from coscc.agent.submit_test import submits
from coscc.config import Config
from coscc.service import Service
from coscc.service.service_test import create_sync
from coscc.units import board as board_reader


class _Nobody:
    """Sessions until a test hands in its own."""

    async def stream(self, *a, **kw):
        raise AssertionError("no session was expected")
        yield


class Place1(unittest.TestCase):
    """§6, 1: "Every `Status:` an agent writes opens or closes a gate." Now guard
    `stage-result` sets the artifact's status from the object its run submitted (R4)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.repo = root / "work" / "proj"
        self.repo.mkdir(parents=True)
        self.config = Config(
            workspaces=(str(self.repo),), working_dir=str(root / "work"), data_dir=str(root / "data"),
        )
        self.service = Service(self.config, _Nobody())
        made = create_sync(self.service, str(self.repo), "a-problem", "some words")
        self.unit = made["unit"]
        (Path(made["path"]) / "intent.md").write_text(
            "# Intent: a problem\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8"
        )

    def _spec(self, says: str, judgement: str) -> dict:
        class Replies:
            async def stream(self, cwd, prompt, session_id=None, max_turns=1, **kw):
                yield ("chunk", f"# Spec: a problem\nAuthor: t. Status: {says}.\n\n## Body\n")
                await submits(kw, judgement=judgement)
                yield ("done", {"session_id": "sess-1", "cost": {}})

        self.service.sessions = Replies()

        async def go():
            return [item async for item in self.service.run_step(str(self.repo), self.unit, "spec")]

        return asyncio.run(go())[-1][1]

    def _plan_gate(self) -> bool:
        repo = str(self.repo)
        opened, _ = asyncio.run(board_reader.gate(
            self.service._units_root(repo), self.unit, "plan",
            state=self.service._snapshot(repo, [self.unit]),
        ))
        return opened

    def _spec_row(self) -> dict:
        rows = self.service.unit_history(str(self.repo), self.unit)["transitions"]
        return [r for r in rows if r["artifact"] == "spec.md"][-1]

    def test_prose_saying_accepted_does_not_open_the_gate_an_object_saying_not_ready_closes(self):
        self.assertEqual(self._spec("accepted", "not-ready")["outcome"], "done")
        row = self._spec_row()
        self.assertEqual((row["to_state"], row["guard"], row["authority"]), ("draft", "stage-result", "agent"))
        self.assertFalse(self._plan_gate())

    def test_prose_saying_draft_does_not_close_the_gate_an_object_saying_ready_opens(self):
        self.assertEqual(self._spec("draft", "ready")["outcome"], "done")
        row = self._spec_row()
        self.assertEqual((row["to_state"], row["guard"], row["authority"]), ("accepted", "stage-result", "agent"))
        self.assertTrue(self._plan_gate())

    def test_a_run_that_submits_nothing_moves_nothing_whatever_its_file_says(self):
        class Silent:
            prompts: list[str] = []

            async def stream(self, cwd, prompt, session_id=None, max_turns=1, **kw):
                self.prompts.append(prompt)
                yield ("chunk", "# Spec: a problem\nAuthor: t. Status: accepted.\n")
                yield ("done", {"session_id": "sess-1", "cost": {}})

        self.service.sessions = Silent()

        async def go():
            return [item async for item in self.service.run_step(str(self.repo), self.unit, "spec")]

        done = asyncio.run(go())[-1][1]
        # R2: one repair turn on the session, holding `submit`, and then `failed`.
        self.assertEqual(len(Silent.prompts), 2)
        self.assertIn("without handing back its object", Silent.prompts[1])
        self.assertEqual(done["outcome"], "failed")
        self.assertIn("no-submission", done["error"])
        rows = self.service.unit_history(str(self.repo), self.unit)["transitions"]
        self.assertEqual([r for r in rows if r["artifact"] == "spec.md"], [])
        self.assertFalse(self._plan_gate())


if __name__ == "__main__":
    unittest.main()
