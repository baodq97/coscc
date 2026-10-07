"""The `submit` channel, and `submits`, what a stand-in session calls with it."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import jsonschema

from coscc import kernel
from coscc.agent import pack
from coscc.units import contracts, guards, submit
from coscc.units.contracts import ContractError
from coscc.units.submit import AGAIN, Channel


def _filled(channel: Channel | submit.Collector, fields: dict[str, Any]) -> dict[str, Any]:
    if isinstance(channel, submit.Collector):
        # Each core session's empty object: no estimate, nothing needing a person. A feature's
        # session gets only what the test gives.
        empty = {"estimate": "units", "integrate": "needs_person"}.get(channel.kind)
        return {empty: [], **fields} if empty else dict(fields)
    if channel.is_round:
        met = {"criterion": "R1", "source": "a requirement", "met": "yes", "evidence": "a.py:1"}
        return {"verdict": "pass", "criteria": [met], "findings": [], "screens": [], **fields}
    obj: dict[str, Any] = {"stage": channel.stage, "judgement": "ready", "questions": []}
    if channel.stage == "intent":
        obj["type"] = "feat"
    if channel.stage == "impl":
        obj["needs_person"] = []
    if channel.stage == "spec":
        obj["unmeasured"] = []
    if channel.stage == "spike":
        obj["verdicts"] = []
    if channel.stage == "plan":
        obj.update(variant="novel", files=[], steps=[], rests_on=[])
    return {**obj, **fields}


async def submits(kw: dict[str, Any], **fields: Any) -> dict[str, Any] | None:
    """What a stand-in `stream` calls before its `done` to hand back an object, as a real
    session calls `submit`: checked against the tool's schema, then through its handler.
    `None` when the call it was given carries no channel. `fields` overrides the defaults:
    `judgement: ready`, no questions, and nothing unmeasured.
    """
    config = (kw.get("mcp_servers") or {}).get(submit.SERVER)
    channel = Channel.of(config) if config is not None else None
    if channel is None:
        return None
    obj = _filled(channel, fields)
    try:
        jsonschema.validate(obj, channel.schema)
    except jsonschema.ValidationError as e:
        return {
            "content": [{"type": "text", "text": f"Input validation error: {e.message}"}],
            "is_error": True,
        }
    return await channel.handle(obj)


class APlanStepNamesOnlyItsFiles(unittest.TestCase):
    """A step's paths are files of the plan's `files`, and no path is in two steps."""

    def submit(self, **fields: Any) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as d:
            channel = Channel(run="r", stage="plan", directory=d, artifact="plan.md", own=False)
            channel.verdict = lambda *_: guards.OPEN  # ty: ignore[invalid-assignment]
            return asyncio.run(channel.handle(_filled(channel, fields)))

    def step(self, title: str, *paths: str) -> dict[str, Any]:
        return {"title": title, "paths": list(paths), "report": "done"}

    def test_a_step_naming_a_path_outside_files_is_refused_with_the_path(self):
        said = self.submit(files=["a.py"], steps=[self.step("one", "a.py", "x.py")])
        self.assertTrue(said.get("is_error"), said)
        self.assertIn("x.py", said["content"][0]["text"])
        self.assertIn("one", said["content"][0]["text"])

    def test_a_path_in_two_steps_is_refused_with_the_path(self):
        said = self.submit(
            files=["a.py", "b.py"], steps=[self.step("one", "a.py"), self.step("two", "a.py")]
        )
        self.assertTrue(said.get("is_error"), said)
        self.assertIn("a.py", said["content"][0]["text"])

    def test_a_file_not_named_as_the_repository_names_it_is_refused(self):
        # A label reads `files` as written: `./coscc/agent/policy.py` would miss the
        # security surface and run as routine.
        for path in ("./coscc/agent/policy.py", "coscc/loop/rules.py:40", "`a.py`", "/a.py"):
            with self.subTest(path=path):
                said = self.submit(files=[path], steps=[])
                self.assertTrue(said.get("is_error"), said)
                self.assertIn(path, said["content"][0]["text"])

    def test_disjoint_steps_inside_files_are_taken(self):
        said = self.submit(
            files=["a.py", "b.py", "c.py"],
            steps=[self.step("one", "a.py"), self.step("two", "b.py")],
        )
        self.assertFalse(said.get("is_error"), said)


class EveryToolTakesTheDeclaredSchema(unittest.TestCase):
    """The schema of each `submit` is the one generated from the agent's declaration."""

    def test_every_channel_and_collector_submits_against_its_declaration(self):
        for stage in ("idea", "intent", "spec", "spike", "plan", "impl", "review"):
            channel = Channel(
                run="r", stage=stage, directory="/nonexistent", artifact="x.md", own=False
            )
            self.assertEqual(channel.schema, contracts.schema(stage), stage)
        for kind, out in contracts.declarations().items():
            if out["kind"] == "session":
                self.assertEqual(submit.Collector(kind).schema, contracts.schema(kind), kind)

    def test_a_judgement_is_ready_or_not_ready_and_nothing_a_person_decides(self):
        schema = contracts.schema("intent")
        for word in ("accepted", "rejected", "done", "draft"):
            with self.assertRaises(jsonschema.ValidationError, msg=word):
                said = {"stage": "intent", "judgement": word, "questions": [], "type": "fix"}
                jsonschema.validate(said, schema)
        jsonschema.validate(
            {
                "stage": "intent",
                "judgement": "not-ready",
                "questions": [{"n": 1, "text": "?", "recommendation": "no"}],
                "type": "fix",
            },
            schema,
        )

    def test_a_question_without_a_recommendation_is_refused(self):
        for stage in ("intent", "spec", "plan"):
            item = contracts.schema(stage)["properties"]["questions"]["items"]
            jsonschema.validate({"n": 1, "text": "?", "recommendation": ""}, item)
            with self.assertRaises(jsonschema.ValidationError, msg=stage) as raised:
                jsonschema.validate({"n": 1, "text": "?"}, item)
            self.assertIn("'recommendation' is a required property", str(raised.exception), stage)

    def test_a_stage_without_a_declaration_opens_no_channel(self):
        for stage in ("pr", "ship"):
            with self.assertRaises(ContractError, msg=stage):
                Channel(run="r", stage=stage, directory="/nonexistent", artifact="x.md", own=False)

    def test_review_hands_back_a_round_and_impl_its_claims(self):
        self.assertIn("needs_person", contracts.schema("impl")["required"])
        self.assertNotIn("needs_person", contracts.schema("plan")["properties"])
        finding = {
            "id": "F1",
            "state": "withdrawn",
            "fixed_in": "",
            "severity": "low",
            "criterion": "R1",
            "path": "a.py",
            "lines": "3",
            "text": "t",
        }
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(
                {"verdict": "pass", "findings": [finding], "screens": []},
                contracts.schema("review"),
            )
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(
                {"verdict": "incomplete", "findings": [], "screens": []},
                contracts.schema("review"),
            )


class TheChannelChecksWhatTheSchemaCannot(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "intent.md").write_text("# Intent: x\n", encoding="utf-8")

    def _channel(self, **kw: Any) -> Channel:
        return Channel(
            run="run-1", stage="spec", directory=self.dir, artifact="spec.md", own=False, **kw
        )

    def test_an_accepted_object_is_kept_with_the_revision_the_app_took(self):
        channel = self._channel()
        said = asyncio.run(channel.handle(_filled(channel, {"unmeasured": ["U1"]})))
        self.assertNotIn("is_error", said)
        self.assertEqual(channel.received["object"]["unmeasured"], ["U1"])
        self.assertEqual(
            channel.received["revision"], submit.revision(self.dir, "spec.md", own=False)
        )

    def test_an_object_from_a_run_that_is_not_open_is_refused(self):
        channel = self._channel(open_run=lambda: "run-2")
        said = asyncio.run(channel.handle(_filled(channel, {})))
        self.assertTrue(said["is_error"])
        self.assertIn("wrong-run", said["content"][0]["text"])
        self.assertIsNone(channel.received)

    def test_the_artifacts_changing_after_the_object_makes_it_stale(self):
        """What the object judged is the revision the app hashed when it came."""
        channel = self._channel()
        asyncio.run(channel.handle(_filled(channel, {})))
        got = channel.received
        from coscc.units import guards

        self.assertTrue(guards.stage_result(channel.inputs(got["object"], got["revision"])).open)
        (self.dir / "intent.md").write_text("# Intent: y\n", encoding="utf-8")
        verdict = guards.stage_result(channel.inputs(got["object"], got["revision"]))
        self.assertEqual(verdict.reasons, ("stale-revision",))

    def test_a_prose_stages_own_artifact_is_not_part_of_its_revision(self):
        before = submit.revision(self.dir, "spec.md", own=False)
        (self.dir / "spec.md").write_text("# Spec: x\n", encoding="utf-8")
        self.assertEqual(submit.revision(self.dir, "spec.md", own=False), before)
        self.assertNotEqual(submit.revision(self.dir, "spec.md", own=True), before)

    def test_a_stage_that_writes_its_own_file_submits_only_once_it_is_there(self):
        channel = Channel(run="r", stage="impl", directory=self.dir, artifact="impl.md", own=True)
        said = asyncio.run(channel.handle(_filled(channel, {})))
        self.assertTrue(said["is_error"])
        self.assertIsNone(channel.received)

    def test_the_wrong_stage_is_refused(self):
        channel = self._channel()
        said = asyncio.run(channel.handle({"stage": "plan", "judgement": "ready", "questions": []}))
        self.assertTrue(said["is_error"])

    def test_the_last_accepted_object_is_the_one_kept(self):
        channel = self._channel()
        asyncio.run(channel.handle(_filled(channel, {"judgement": "ready"})))
        asyncio.run(channel.handle(_filled(channel, {"judgement": "not-ready"})))
        self.assertEqual(channel.received["object"]["judgement"], "not-ready")


def a_head(test: unittest.TestCase, sha: str = "c" * 40) -> str:
    """A review run outside a git checkout reads no head, and guard `review-round` then refuses its
    round; a test of a review that is not about git gives it one, for the test."""
    from unittest import mock

    patcher = mock.patch("coscc.runner.step._head_of", mock.AsyncMock(return_value=sha))
    patcher.start()
    test.addCleanup(patcher.stop)
    return sha


def finding(fid: str, state: str = "open", severity: str = "medium", **kw: Any) -> dict[str, Any]:
    """One finding of a review round's object, for a stand-in review to submit."""
    return {
        "id": fid,
        "state": state,
        "fixed_in": kw.pop("fixed_in", "abc1234" if state == "fixed" else ""),
        "severity": severity,
        "criterion": kw.pop("criterion", "R1"),
        "path": kw.pop("path", "coscc/x.py"),
        "lines": kw.pop("lines", "1"),
        "text": kw.pop("text", f"what {fid} says"),
        **kw,
    }


class ARoundIsOfTheHeadTheAppRecorded(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def _channel(self, head: str = "a" * 40) -> Channel:
        return Channel(
            run="r", stage="review", directory=self.dir, artifact="review.md", own=False, head=head
        )

    def test_a_round_is_kept_with_the_head_and_never_names_one(self):
        channel = self._channel()
        said = asyncio.run(
            channel.handle(
                _filled(channel, {"verdict": "changes-requested", "findings": [finding("F1")]})
            )
        )
        self.assertNotIn("is_error", said)
        got = channel.inputs(channel.received["object"], channel.received["revision"])
        self.assertEqual((got["head"], got["object"]["verdict"]), ("a" * 40, "changes-requested"))
        self.assertNotIn("head", channel.schema["properties"])

    def test_a_run_that_read_no_head_cannot_hand_back_a_round(self):
        channel = self._channel(head="")
        said = asyncio.run(channel.handle(_filled(channel, {})))
        self.assertTrue(said["content"][0]["text"].endswith(AGAIN))
        self.assertIn("no-head", said["content"][0]["text"])

    def test_an_id_twice_or_a_fix_with_no_commit_is_refused(self):
        channel = self._channel()
        for findings in (
            [finding("F1"), finding("F1")],
            [finding("F2", "fixed", fixed_in="")],
            [finding("F3", fixed_in="abc1234")],
        ):
            said = asyncio.run(channel.handle(_filled(channel, {"findings": findings})))
            self.assertTrue(said.get("is_error"), findings)
            self.assertTrue(said["content"][0]["text"].endswith(AGAIN))
        self.assertIsNone(channel.received)

    def test_a_finding_names_a_criterion_the_round_graded(self):
        channel = self._channel()
        for crit in ("R9", "S1", "x"):
            obj = _filled(channel, {"findings": [finding("F1", criterion=crit)]})
            said = asyncio.run(channel.handle(obj))
            self.assertTrue(said.get("is_error"), crit)
        self.assertIsNone(channel.received)
        ok = _filled(channel, {"findings": [finding("F1", criterion="R1")]})
        self.assertNotIn("is_error", asyncio.run(channel.handle(ok)))

    def test_pass_is_refused_while_a_criterion_is_not_met_and_a_criterion_is_graded_once(self):
        channel = self._channel()
        no = {"criterion": "R1", "source": "s", "met": "no", "evidence": "a.py:1"}
        said = asyncio.run(channel.handle(_filled(channel, {"verdict": "pass", "criteria": [no]})))
        self.assertIn("R1 is `no`", said["content"][0]["text"])
        said = asyncio.run(channel.handle(_filled(channel, {"criteria": [no, no]})))
        self.assertIn("more than once", said["content"][0]["text"])
        failing = {"verdict": "changes-requested", "criteria": [no]}
        self.assertNotIn("is_error", asyncio.run(channel.handle(_filled(channel, failing))))


class ImplClaimsOnlyAnOpenFinding(unittest.TestCase):
    """Guard `impl-claim`, asked at `submit`."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "impl.md").write_text("# Impl: x\n", encoding="utf-8")

    def _channel(self) -> Channel:
        return Channel(
            run="r",
            stage="impl",
            directory=self.dir,
            artifact="impl.md",
            own=True,
            open_ids=("F2",),
            claims_round=3,
        )

    def test_an_open_finding_of_the_last_round_may_be_claimed(self):
        channel = self._channel()
        said = asyncio.run(channel.handle(_filled(channel, {"needs_person": ["F2"]})))
        self.assertNotIn("is_error", said)
        got = channel.inputs(channel.received["object"], channel.received["revision"])
        self.assertEqual((got["claims"], got["claims_round"]), (["F2"], 3))

    def test_any_other_id_is_refused_naming_the_open_ones(self):
        channel = self._channel()
        said = asyncio.run(channel.handle(_filled(channel, {"needs_person": ["F1"]})))
        self.assertTrue(said["is_error"])
        self.assertIn("impl-claim", said["content"][0]["text"])
        self.assertIn("F2", said["content"][0]["text"])
        self.assertTrue(said["content"][0]["text"].endswith(AGAIN))
        self.assertIsNone(channel.received)


class ASessionThatIsNoStageHandsBackItsObject(unittest.TestCase):
    """Gebo and the estimate each submit against their own schema."""

    def test_each_kind_keeps_the_object_that_fits_its_schema(self):
        good = {
            "integrate": {"needs_person": [{"commit": "abc1234", "why": "A vs B"}]},
            "estimate": {
                "units": [
                    {
                        "unit": "0001_a",
                        "value": 3,
                        "effort": "M",
                        "similar": [],
                        "basis": "x",
                        "relations": [],
                    }
                ]
            },
        }
        for kind, obj in good.items():
            collector = submit.Collector(kind)
            said = asyncio.run(submits({"mcp_servers": {"cos": collector.server()}}, **obj))
            self.assertFalse(said.get("is_error"), kind)
            self.assertEqual(collector.object(), obj, kind)
            self.assertIn(AGAIN, collector.description())

    def test_an_object_outside_its_schema_is_not_kept(self):
        for kind, bad in (
            ("integrate", {"needs_person": ["a line"]}),
            ("estimate", {"units": "none"}),
        ):
            collector = submit.Collector(kind)
            said = asyncio.run(submits({"mcp_servers": {"cos": collector.server()}}, **bad))
            self.assertTrue(said["is_error"], kind)
            self.assertIsNone(collector.object(), kind)


class ATriggeredRowsCollectorKeepsWhatFits(unittest.TestCase):
    """A row whose output is `proposal` hands its object back through a `Collector` of its own."""

    def test_the_scan_rows_collector_keeps_what_fits_its_schema(self):
        collector = submit.Collector("scan")
        self.assertEqual(collector.schema, contracts.schema("scan"))
        self.assertIn("work you propose", collector.description())
        said = asyncio.run(submits({"mcp_servers": {"cos": collector.server()}}, proposals=[]))
        self.assertFalse(said.get("is_error"))
        self.assertEqual(collector.object(), {"proposals": []})
        said = asyncio.run(submits({"mcp_servers": {"cos": collector.server()}}, n=3))
        self.assertTrue(said["is_error"])


class AVerdictCitesItsEvidence(unittest.TestCase):
    """The outcome grader's `submit` refuses a `yes` or a `no` that cites no `path:lines`."""

    def _said(self, *criteria: dict[str, str]) -> tuple[dict[str, Any], submit.Collector]:
        collector = submit.Collector("outcome")
        said = asyncio.run(
            submits({"mcp_servers": {"cos": collector.server()}}, criteria=list(criteria))
        )
        return said, collector

    def test_yes_or_no_without_path_lines_is_refused_and_unclear_needs_none(self):
        for met in ("yes", "no"):
            said, collector = self._said(
                {"criterion": "O1", "source": "s", "met": met, "evidence": "it is there"}
            )
            self.assertTrue(said["is_error"], met)
            self.assertIn("path:lines", said["content"][0]["text"])
            self.assertIsNone(collector.object())
        said, collector = self._said(
            {"criterion": "O1", "source": "s", "met": "no", "evidence": "coscc/bus.py:12-30 gone"},
            {"criterion": "W1", "source": "t", "met": "unclear", "evidence": "measured on runs"},
        )
        self.assertFalse(said.get("is_error"))
        self.assertEqual(len(collector.object()["criteria"]), 2)

    def test_no_criterion_or_one_twice_is_refused(self):
        self.assertIn("at least one", submit.verdict_problem({"criteria": []}))
        one = {"criterion": "O1", "source": "s", "met": "unclear", "evidence": "x"}
        self.assertIn("more than once", submit.verdict_problem({"criteria": [one, one]}))

    def test_the_verdict_is_its_worst_criterion(self):
        c = lambda met: {"met": met}
        self.assertEqual(contracts.graded([c("yes"), c("unclear")]), "unclear")
        self.assertEqual(contracts.graded([c("unclear"), c("no")]), "not-met")
        self.assertEqual(contracts.graded([c("yes")]), "met")


if __name__ == "__main__":
    unittest.main()


def _draft_agent(**over: Any) -> dict[str, Any]:
    fields = {
        "name": "Tidy",
        "description": "Proposes changes to the review skill from the interventions.",
        "model": {"id": "claude-sonnet-5-5[1m]", "effort": "low"},
        "tools": {"Read": "allow"},
        "input": {
            "artifacts": [],
            "outputs": [],
            "answers": False,
            "findings": False,
            "data": ["interventions"],
        },
        "output": pack.BLANK["output"],
        "trigger": {"manual": True},
        "ceilings": {"turns": 4, "usd": 0.3},
    }
    return {"key": "tidy", "fields": {**fields, **over}, "body": "Read the interventions."}


def _full() -> dict[str, Any]:
    return json.loads(json.dumps(pack.processes()[pack.DEFAULT_PROCESS]))


class ADraftPassesTheLoadChecks(unittest.TestCase):
    """Dagaz's `submit` refuses a draft a person's save would refuse, with the save's reasons."""

    catalog = {t.name: t.effect for t in kernel.BUILTINS}

    def _said(self, **obj: Any) -> tuple[dict[str, Any], submit.Collector]:
        collector = submit.Collector("dagaz", self.catalog)
        said = asyncio.run(collector.handle({"why": "it serves the task", **obj}))
        return said, collector

    def test_a_row_and_fulls_shape_are_taken_and_kept(self):
        process = {"name": "docs", "process": _full()}
        said, collector = self._said(agent=_draft_agent(), process=process)
        self.assertFalse(said.get("is_error"), said)
        self.assertEqual(collector.object()["process"], process)

    def test_a_draft_with_neither_is_refused(self):
        said, collector = self._said()
        self.assertTrue(said["is_error"])
        self.assertIn("an `agent`, a `process` or both", said["content"][0]["text"])
        self.assertIsNone(collector.object())

    def test_a_tool_off_the_catalog_is_refused(self):
        said, _ = self._said(agent=_draft_agent(tools={"send_email": "allow"}))
        self.assertIn("tools.send_email: no such tool in the catalog", said["content"][0]["text"])

    def test_a_writing_tool_on_a_row_leif_starts_is_refused(self):
        said, _ = self._said(agent=_draft_agent(trigger={"leif": True}, tools={"Bash": "allow"}))
        self.assertIn("holds only reading tools, not Bash", said["content"][0]["text"])

    def test_a_taken_key_and_an_unreadable_output_are_refused(self):
        said, _ = self._said(agent={**_draft_agent(), "key": "scan"})
        self.assertIn("scan is taken", said["content"][0]["text"])
        bad = {"kind": "proposal", "version": 1, "fields": {"items": "text"}}
        said, _ = self._said(agent=_draft_agent(output=bad))
        self.assertIn("contract-field-missing: tidy.proposals", said["content"][0]["text"])

    def test_a_process_without_review_before_merge_is_refused(self):
        full = _full()
        full["states"]["impl"]["next"] = [{"to": "pr"}]
        full["states"]["pr"]["next"] = [{"to": "ship"}]
        del full["states"]["review"]
        said, _ = self._said(process={"name": "docs", "process": full})
        self.assertIn("a review state is not on every path to it", said["content"][0]["text"])

    def test_a_guard_not_listed_is_refused(self):
        full = _full()
        full["states"]["plan"]["next"] = [{"to": "impl", "when": {"guard": "looks-fine"}}]
        said, _ = self._said(process={"name": "docs", "process": full})
        self.assertIn("no guard looks-fine", said["content"][0]["text"])

    def test_a_process_may_run_the_agent_drafted_beside_it(self):
        full = _full()
        full["states"]["intent"]["agent"] = "tidy"
        del full["states"]["idea"]
        full["start"] = "intent"
        said, _ = self._said(process={"name": "docs", "process": full})
        self.assertIn("agent tidy is no row", said["content"][0]["text"])
        row = _draft_agent(
            output={
                "kind": "artifact",
                "version": 1,
                "by": "app",
                "fields": contracts.output("intent")["fields"],
            },
            trigger=None,
        )
        del row["fields"]["trigger"]
        said, _ = self._said(agent=row, process={"name": "docs", "process": full})
        self.assertFalse(said.get("is_error"), said)
