"""The `submit` channel, and `submits`, what a stand-in session calls with it."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any

import jsonschema

from coscc.units import contracts, guards, submit
from coscc.units.contracts import ContractError
from coscc.units.submit import AGAIN, Channel


def _filled(channel: Channel | submit.Collector, fields: dict[str, Any]) -> dict[str, Any]:
    if isinstance(channel, submit.Collector):
        # Each core session's empty object: no estimate, nothing needing a person. A feature's
        # session gets only what the test gives.
        empty = {"estimate": "units", "integrate": "needs_person"}.get(channel.kind)
        return {empty: [], **fields} if empty else dict(fields)
    if channel.stage == submit.ROUND:
        return {"verdict": "pass", "findings": [], "screens": [], **fields}
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
        obj.update(impl="novel", files=[], steps=[], rests_on=[])
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

    def test_disjoint_steps_inside_files_are_taken(self):
        said = self.submit(
            files=["a.py", "b.py", "c.py"],
            steps=[self.step("one", "a.py"), self.step("two", "b.py")],
        )
        self.assertFalse(said.get("is_error"), said)


class EveryToolTakesTheDeclaredSchema(unittest.TestCase):
    """The schema of each `submit` is the one generated from the agent's declaration."""

    def test_every_channel_and_collector_submits_against_its_declaration(self):
        for stage in (*submit.STAGE_RESULT, submit.ROUND):
            channel = Channel(
                run="r", stage=stage, directory="/nonexistent", artifact="x.md", own=False
            )
            self.assertEqual(channel.schema, contracts.schema(stage), stage)
        for kind in submit.SESSIONS:
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
                "questions": [{"n": 1, "text": "?"}],
                "type": "fix",
            },
            schema,
        )

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
            "rule": "",
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
        "rule": kw.pop("rule", ""),
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


class AFeatureAddsItsSession(unittest.TestCase):
    """`add_session`: a feature's declaration and purpose under its kind, once."""

    OUTPUT = {"kind": "session", "version": 1, "fields": {"n": "number"}}

    def tearDown(self):
        contracts.ADDED.pop("planted", None)
        submit.SESSIONS.pop("planted", None)

    def test_the_collector_of_an_added_session_keeps_what_fits(self):
        submit.add_session("planted", self.OUTPUT, "Hand the app a number.")
        submit.add_session("planted", self.OUTPUT, "Hand the app a number.")
        collector = submit.Collector("planted")
        self.assertEqual(collector.schema, contracts.schema("planted"))
        self.assertIn("Hand the app a number.", collector.description())
        said = asyncio.run(submits({"mcp_servers": {"cos": collector.server()}}, n=3))
        self.assertFalse(said.get("is_error"))
        self.assertEqual(collector.object(), {"n": 3})
        said = asyncio.run(submits({"mcp_servers": {"cos": collector.server()}}, n=3, m=1))
        self.assertTrue(said["is_error"])

    def test_a_broken_declaration_is_refused_with_its_reason(self):
        with self.assertRaises(ContractError) as e:
            submit.add_session("planted", {**self.OUTPUT, "fields": {"n": "integer"}}, "x")
        self.assertTrue(str(e.exception).startswith("contract-bad-type: planted.n: "))
        self.assertNotIn("planted", submit.SESSIONS)


if __name__ == "__main__":
    unittest.main()
