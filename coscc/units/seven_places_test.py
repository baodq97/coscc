"""`0136` R17. The seven places `docs/architecture/fsm.md` §6 names where an agent's free text
became a machine decision, one class each, `Place1` to `Place7`, carrying the words of its
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
from coscc.github.prmachine_test import HEAD, FakeGh, run
from coscc.github.prmachine_test import Fixture as _PrFixture
from coscc.config import Config
from coscc.service import Service
from coscc.service.service_test import create_sync
from coscc.units import autopilot as ap
from coscc.units import board as board_reader
from coscc.units import guards
from coscc.units.board_test import _store
from coscc.units.meta_test import snapshot_of


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


class Place4(unittest.TestCase):
    """§6, 4: "Gebo's `[needs-person]` lines decide the integration outcome." Now the outcome
    is R7's order: the head on git moved, else the `needs_person` of the object Gebo handed
    back through `submit`, else `failed`. A real conflict on a bare remote, as
    `integrate_service_test.GeboThroughTheService` sets it up."""

    from coscc.service.integrate_service_test import GeboThroughTheService as _G

    setUp, _gh, _no_act, remote_head, records = _G.setUp, _G._gh, _G._no_act, _G.remote_head, _G.records
    del _G

    def _integrate(self, reply: str, said: list[dict]) -> dict:
        from coscc.service.integrate_service_test import StandIn

        async def act(tree, gate):
            return reply

        self.service.sessions = StandIn(act, said=said)

        async def go():
            done = {}
            async for kind, payload in self.service.integrate(self.cwd, self.unit):
                if kind == "done":
                    done = payload["integration"]
            return done

        return asyncio.run(go())

    def test_a_needs_person_line_the_object_does_not_carry_decides_nothing(self):
        rec = self._integrate("[needs-person] f.txt: one side wants `main`, the other `branch`", [])
        self.assertEqual((rec["outcome"], rec["needs_person"]), ("failed", []))
        self.assertEqual(self.remote_head(), self.head_before)

    def test_the_objects_needs_person_decides_when_the_reply_says_nothing(self):
        rec = self._integrate("Stopped.", [{"commit": "", "why": "A keeps x, B drops x"}])
        self.assertEqual((rec["outcome"], rec["needs_person"]), ("needs-person", ["A keeps x, B drops x"]))


class _Asked(unittest.TestCase):
    """A unit with two open questions, and sessions that hand back one object."""

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
        intent = "# Intent: x\nAuthor: t. Type: feat. Status: accepted.\n\n## Problem\n\np\n"
        made = create_sync(self.service, str(self.repo), "asked", "x")
        self.unit = made["unit"]
        (Path(made["path"]) / "intent.md").write_text(intent, encoding="utf-8")
        (Path(made["path"]) / "spec.md").write_text(
            "# Spec: a\nIntent: intent.md. Author: t. Status: draft.\n\n## Open questions\n\n1. Nhánh mới?\n2. Tiền?\n",
            encoding="utf-8")

    def _stream(self, reply: str, obj: dict | None):
        class Session:
            def in_flight(self):
                return []

            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                yield ("chunk", reply)
                if obj is not None:
                    await submits(kw, **obj)
                yield ("done", {"session_id": "s1", "cost": {"cost_usd": 0.02, "turns": 1}})

        self.service.sessions = Session()

    def _end(self, stage: str, unit: str) -> dict:
        from coscc.runlog.journal import Journal

        journal = Journal(Path(self.config.working_dir), Path(self.config.data_dir))
        key = self.service._journal_key(str(self.repo))
        return [r for r in journal.records(key, unit, kind="end") if r.get("stage") == stage][-1]


class Place5(_Asked):
    """§6, 5: "The estimate's JSON becomes backlog rows." Now the estimate is the object the
    session hands back through `submit`, guard `run-submitted` ends the run, and every row it
    writes carries `authority: agent` (R9)."""

    GOAL = "bớt can thiệp tay"

    def _estimate(self, prose: str, obj: dict | None) -> dict:
        self._stream(prose, obj)

        async def go():
            return [item async for item in self.service.propose_estimates(str(self.repo))]

        return asyncio.run(go())[-1][1]["estimate"]

    def _rows(self) -> list[dict]:
        from coscc.runlog.journal import Journal

        journal = Journal(Path(self.config.working_dir), Path(self.config.data_dir))
        return journal.records(self.service._journal_key(str(self.repo)), kind="estimate-value")

    def _one(self, value: int) -> dict:
        return {"unit": self.unit, "value": value, "effort": "S", "similar": [], "basis": f"{self.GOAL}: x",
                "relations": []}

    def test_the_rows_are_the_objects_and_an_agents(self):
        prose = "```json\n" + json.dumps({"units": [self._one(5)]}) + "\n```"
        done = self._estimate(prose, {"units": [self._one(2)]})
        self.assertEqual(done["outcome"], "done")
        self.assertEqual([(r["unit"], r["value"], r["authority"]) for r in self._rows()], [(self.unit, 2, "agent")])
        self.assertEqual(self._end("estimate", "")["guard"], "run-submitted")

    def test_a_json_block_in_the_reply_with_no_object_writes_nothing(self):
        done = self._estimate("```json\n" + json.dumps({"units": [self._one(5)]}) + "\n```", None)
        self.assertEqual(done["outcome"], "failed")
        self.assertIn("no-submission", done["detail"])
        self.assertEqual(self._rows(), [])


class Place6(_PrFixture):
    """§6, 6: "The reviewed sha and the merge pin are copied by the model out of prompt prose."
    Now `ship` is the PR machine's (R13): guard `ship-ready` reads the head the app recorded
    when the review run opened (R3 c) and the head its own `gh pr view` found, and the merge is
    pinned to that read (R10). Here `review.md` and `ship.md` name other commits."""

    PROSE = "## Round 1\nReviewed: {sha}. Verdict: pass.\n\n### Findings\n\nNone.\n"

    def test_the_merge_is_pinned_to_the_head_the_guard_read_not_the_one_the_prose_names(self):
        gh = FakeGh()
        m = self.machine(gh)
        run(m.open_pr(self.unit()))
        self.a_round(head=HEAD)
        (self.directory / "review.md").write_text(self.PROSE.format(sha="0" * 40), encoding="utf-8")
        (self.directory / "ship.md").write_text("# Ship\nStatus: draft.\nmerge with --match-head-commit " + "0" * 40 + "\n")
        out = run(m.ship(self.unit()))
        self.assertEqual(out.result, "merged")
        [merge] = [c for c in gh.calls if c[:2] == ["pr", "merge"]]
        self.assertEqual(merge[-1], HEAD)
        [requested] = [r for r in self.rows("ship.md") if r["to_state"] == "draft"]
        self.assertEqual(requested["guard"], "ship-ready")
        self.assertEqual({k: json.loads(requested["inputs"])[k] for k in ("head", "reviewed_head")},
                         {"head": HEAD, "reviewed_head": HEAD})

    def test_prose_naming_the_head_does_not_stand_for_a_round_of_it(self):
        # The pull request moved on after the round the app holds; `review.md` claims the new head.
        gh = FakeGh(head="b" * 40)
        m = self.machine(gh)
        run(m.open_pr(self.unit()))
        self.a_round(head=HEAD)
        (self.directory / "review.md").write_text(self.PROSE.format(sha="b" * 40), encoding="utf-8")
        out = run(m.ship(self.unit()))
        self.assertEqual((out.result, out.guard), ("refused", "ship-ready"))
        self.assertIn("head-moved", out.reasons)
        self.assertEqual([c for c in gh.calls if c[:2] == ["pr", "merge"]], [])


class Place7(unittest.TestCase):
    """§6, 7: "The autopilot matches English substrings of `cos.mjs`'s messages." Now `next`
    and the gate hand out codes from `guards.REASONS` beside their words, and the autopilot
    branches on the codes (R11). Here `cos.mjs` rewords a reason, and the autopilot still
    reads the unit as it did."""

    ROW = {"name": "0001_x", "questions": []}

    def _next(self, files: dict[str, str], old: str, new: str) -> dict:
        """`next` for one unit, asked of a copy of the harness script with `old` reworded."""
        with tempfile.TemporaryDirectory() as d:
            text = harness.script().read_text(encoding="utf-8")
            self.assertIn(old, text)
            script = Path(d) / "harness" / "cos.mjs"
            script.parent.mkdir()
            script.write_text(text.replace(old, new), encoding="utf-8")
            store = Path(d) / "store"
            _store(store, {"0001_x": files})
            state = snapshot_of(store)
            with mock.patch.object(harness, "script", lambda: script):
                return asyncio.run(board_reader.next_step(store, "0001_x", state=state))

    def _read_as(self, nxt: dict, reason: str) -> None:
        self.assertEqual(set(nxt["reasons"]) - set(guards.REASONS), set())
        self.assertIsNone(ap.stop_for(self.ROW, nxt, None, False))
        self.assertEqual(ap.reason_for(nxt, "", None), (reason, nxt["action"]))
        # The words alone, as the autopilot read them before, would now stop the unit.
        self.assertEqual(ap.stop_for(self.ROW, {**nxt, "reasons": []}, None, False)["kind"], "f")

    def test_a_closed_unit_is_passed_over_as_closed_whatever_the_words(self):
        nxt = self._next(
            {"intent.md": "# I\nType: feat. Status: accepted.\n", "spec.md": "# S\nStatus: rejected.\n"},
            "`closed — ${s.name} rejected`", "`shut: ${s.name} was turned down`",
        )
        self.assertEqual((nxt["stage"], nxt["action"]), ("", "shut: spec was turned down"))
        self.assertIn("closed", nxt["reasons"])
        self._read_as(nxt, "closed")

    def test_a_finished_unit_is_passed_over_as_finished_whatever_the_words(self):
        nxt = self._next(
            {"intent.md": "# I\nType: feat. Status: accepted.\n", "plan.md": "# P\nStatus: done.\n"},
            "action: 'finished'", "action: 'all done'",
        )
        self.assertEqual((nxt["stage"], nxt["action"], nxt["reasons"]), ("", "all done", ["finished"]))
        self._read_as(nxt, "finished")


class TheSevenPlacesAreAllHere(unittest.TestCase):
    """R17: one case per place of `docs/architecture/fsm.md` §6, each carrying its number and
    its words, and no eighth."""

    def test_there_is_one_class_per_place_and_each_quotes_its_place(self):
        places = sorted(n for n, v in globals().items() if n.startswith("Place") and isinstance(v, type))
        self.assertEqual(places, [f"Place{n}" for n in range(1, 8)])
        fsm = (Path(__file__).resolve().parents[2] / "docs" / "architecture" / "fsm.md").read_text(encoding="utf-8")
        for n in range(1, 8):
            doc = globals()[f"Place{n}"].__doc__ or ""
            with self.subTest(place=n):
                self.assertTrue(doc.startswith(f"§6, {n}: "), doc[:40])
                self.assertIn(f"\n{n}. ", fsm)


if __name__ == "__main__":
    unittest.main()
