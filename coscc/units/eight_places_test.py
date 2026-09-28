"""`0136` R17. The eight places `docs/architecture/fsm.md` §6 names where an agent's free text
became a machine decision, one class each, `Place1` to `Place8`, carrying the words of its
item. Each asserts two things: the transition went through the guard named for it, and where
the prose of an artifact says the opposite of the structured state, the structured state wins.

The file grows with `plan.md ## Order of work`: each step that moves a place adds its class.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.agent.submit_test import a_head, finding, submits
from coscc.agent import harness
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

    def test_the_spec_needs_a_spike_for_the_u_ids_its_object_names_not_its_file(self):
        """R4: the `U<n>` of the stage result, carried to `cos.mjs` in the snapshot."""
        class Replies:
            async def stream(self, cwd, prompt, session_id=None, max_turns=1, **kw):
                yield ("chunk", "# Spec: a problem\nAuthor: t. Status: accepted.\n\n## Concerns\n\n- none\n")
                await submits(kw, judgement="ready", unmeasured=["U1"])
                yield ("done", {"session_id": "sess-1", "cost": {}})

        self.service.sessions = Replies()

        async def go():
            return [item async for item in self.service.run_step(str(self.repo), self.unit, "spec")]

        self.assertEqual(asyncio.run(go())[-1][1]["outcome"], "done")
        repo = str(self.repo)
        said = asyncio.run(board_reader.next_step(
            self.service._units_root(repo), self.unit, state=self.service._snapshot(repo, [self.unit]),
        ))
        self.assertEqual(said["stage"], "spike")
        self.assertFalse(self._plan_gate())

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


class _Review(unittest.TestCase):
    """A unit with everything up to `review` accepted, and a review or an impl run on it."""

    def setUp(self):
        self.head = a_head(self, "d" * 40)
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
        self.unit, self.dir = made["unit"], Path(made["path"])
        for name, title in (("intent", "Intent"), ("spec", "Spec"), ("plan", "Plan"), ("impl", "Impl"), ("pr", "PR")):
            extra = " Type: feat." if name == "intent" else ""
            (self.dir / f"{name}.md").write_text(f"# {title}: a problem\nAuthor: t.{extra} Status: accepted.\n", encoding="utf-8")

    def _run(self, stage: str, reply: str, **obj) -> dict:
        directory = self.dir

        class Replies:
            async def stream(self, cwd, prompt, session_id=None, max_turns=1, **kw):
                if stage == "impl":
                    (directory / "impl.md").write_text(reply, encoding="utf-8")
                else:
                    yield ("chunk", reply)
                await submits(kw, **obj)
                yield ("done", {"session_id": "sess-1", "cost": {}})

        self.service.sessions = Replies()

        async def open_gate(units_root, unit, stage, repo=None, **kw):
            return True, f"open: {stage} may proceed"

        async def go():
            return [item async for item in self.service.run_step(str(self.repo), self.unit, stage)]

        with mock.patch.object(board_reader, "gate", open_gate):
            return asyncio.run(go())[-1][1]

    def _row(self, artifact: str) -> dict:
        rows = self.service.unit_history(str(self.repo), self.unit)["transitions"]
        row = dict([r for r in rows if r["artifact"] == artifact][-1])
        return {**row, "inputs": json.loads(row["inputs"]) if isinstance(row.get("inputs"), str) else row.get("inputs")}

    def _unit(self) -> dict:
        [u] = asyncio.run(self.service.board(str(self.repo)))["units"]
        return u

    def _status(self) -> dict:
        """The unit as `cos.mjs status --json` reads it from the app's snapshot."""
        repo = str(self.repo)
        source, stdin = board_reader._source(self.service._snapshot(repo, [self.unit]))
        argv = [str(harness.script()), "--root", str(self.service._units_root(repo)), *source, "status", "--json"]
        code, out, err = asyncio.run(board_reader._ask(argv, 30.0, stdin))
        self.assertEqual(code, 0, err)
        [u] = [u for u in json.loads(out)["units"] if u["name"] == self.unit]
        return u


# The review's prose: a header and a round saying the opposite of the object below it.
PASSING_PROSE = (
    "# Review: a problem\nPR: pr.md. Author: t. Status: accepted.\n\n## Round 1\n\n"
    "Reviewed: 0000000. Verdict: pass.\n\n### What was checked\n\nEverything.\n\n### Findings\n\nNone.\n"
)


class Place2(_Review):
    """§6, 2: "`Verdict`, a finding's label and its severity, as the review wrote them, decide
    the `ship` gate." Now guard `review-round` records the round the run handed back, at the
    head the app read when it opened (R3 c, R5)."""

    def test_a_round_whose_prose_passes_asks_for_changes_when_its_object_does(self):
        done = self._run("review", PASSING_PROSE, verdict="changes-requested",
                         findings=[finding("F1", "open", "high", path="coscc/x.py", lines="3", text="broken")])
        self.assertEqual(done["outcome"], "done", done["error"])
        self.assertNotIn("ingest_error", done)
        row = self._row("review.md")
        self.assertEqual((row["to_state"], row["guard"], row["authority"]), ("changes-requested", "review-round", "agent"))
        [r] = self._unit()["rounds"]
        self.assertEqual((r["verdict"], r["findings_open"], r["open_ids"]), ("changes-requested", 1, ["F1"]))
        # The file is the app's rendering of the object, the head its own read.
        text = (self.dir / "review.md").read_text(encoding="utf-8")
        self.assertIn(f"Reviewed: {self.head}. Verdict: changes-requested.", text)
        self.assertIn("- F1 [open] coscc/x.py:3 — high — broken", text)
        self.assertIn("### What was checked\n\nEverything.", text)
        self.assertNotIn("0000000", text)

    def test_the_head_a_round_is_of_is_the_apps_never_the_models(self):
        self._run("review", PASSING_PROSE, verdict="pass")
        self.assertEqual(self._row("review.md")["inputs"]["head"], self.head)
        review = self._status()["artifacts"]["review.md"]["review"]
        self.assertEqual([(r["n"], r["reviewed"], r["verdict"]) for r in review["rounds"]], [(1, self.head, "pass")])


class Place3(_Review):
    """§6, 3: "`impl.md ## Needs a person` sends the unit back to `review`." Now guard
    `impl-claim` checks the ids an impl run hands back, and the section is prose (R6)."""

    def setUp(self):
        super().setUp()
        done = self._run("review", PASSING_PROSE, verdict="changes-requested", findings=[
            finding("F1", "open", "high"), finding("F2", "open", "medium"),
        ])
        self.assertEqual(done["outcome"], "done", done["error"])

    def _impl(self, prose_claims: str, claims: list[str]) -> dict:
        reply = (
            "# Impl: a problem\nIntent: intent.md. Plan: plan.md. Author: t. Status: accepted.\n\n"
            f"## What was built\n\nx\n\n## Needs a person\n\n{prose_claims}\n"
        )
        return self._run("impl", reply, needs_person=claims)

    def test_a_claim_the_prose_does_not_make_still_counts(self):
        done = self._impl("", ["F1", "F2"])
        self.assertEqual(done["outcome"], "done", done["error"])
        self.assertEqual(self._row("impl.md")["inputs"]["claims"], ["F1", "F2"])
        claims = self._status()["artifacts"]["impl.md"]["needsPerson"]
        self.assertEqual([c["id"] for c in claims], ["F1", "F2"])

    def test_a_claim_only_the_prose_makes_counts_for_nothing(self):
        done = self._impl("- F1: needs a login\n- F2: costs money", [])
        self.assertEqual(done["outcome"], "done", done["error"])
        self.assertEqual(self._status()["artifacts"]["impl.md"]["needsPerson"], [])

    def test_a_finding_the_last_round_did_not_leave_open_is_refused_at_submit(self):
        done = self._impl("- F9: nothing", ["F9"])
        self.assertEqual(done["outcome"], "failed")
        self.assertIn("no-submission", done["error"])


if __name__ == "__main__":
    unittest.main()
