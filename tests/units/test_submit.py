"""The `submit` channel, and `submits`, what a stand-in session calls with it."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any, get_args

import jsonschema

from coscc.units import submit
from coscc.units.submit import AGAIN, Channel


def _filled(channel: Channel | submit.Collector, fields: dict[str, Any]) -> dict[str, Any]:
    if isinstance(channel, submit.Collector):
        # Each session's empty object: no estimate, nothing needing a person.
        empty = {"estimate": "units", "integrate": "needs_person"}[channel.kind]
        return {empty: [], **fields}
    if channel.stage == submit.ROUND:
        return {"verdict": "pass", "findings": [], "screens": [], **fields}
    obj: dict[str, Any] = {"stage": channel.stage, "judgement": "ready", "questions": []}
    if channel.stage == "impl":
        obj["needs_person"] = []
    if channel.stage == "spec":
        obj["unmeasured"] = []
    if channel.stage == "spike":
        obj["verdicts"] = []
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


class TheSchemaOfAStageResult(unittest.TestCase):
    def test_spec_names_its_unmeasured_and_spike_its_verdicts(self):
        spec = submit.stage_result_schema("spec")
        self.assertIn("unmeasured", spec["required"])
        spike = submit.stage_result_schema("spike")
        self.assertIn("verdicts", spike["required"])
        self.assertNotIn("unmeasured", submit.stage_result_schema("plan")["properties"])

    def test_a_judgement_is_ready_or_not_ready_and_nothing_a_person_decides(self):
        schema = submit.stage_result_schema("intent")
        for word in ("accepted", "rejected", "done", "draft"):
            with self.assertRaises(jsonschema.ValidationError, msg=word):
                jsonschema.validate({"stage": "intent", "judgement": word, "questions": []}, schema)
        jsonschema.validate(
            {"stage": "intent", "judgement": "not-ready", "questions": [{"n": 1, "text": "?"}]},
            schema,
        )

    def test_a_stage_without_a_result_opens_no_channel(self):
        for stage in ("pr", "ship", "integrate"):
            self.assertIsNone(submit.schema_for(stage), stage)

    def test_review_hands_back_a_round_and_impl_its_claims(self):
        self.assertIs(submit.schema_for("review"), submit.SCHEMAS["review-round"])
        self.assertIn("needs_person", submit.stage_result_schema("impl")["required"])
        self.assertNotIn("needs_person", submit.stage_result_schema("plan")["properties"])
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
                submit.schema_for("review"),
            )
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(
                {"verdict": "incomplete", "findings": [], "screens": []},
                submit.schema_for("review"),
            )

    def test_every_kind_of_object_is_closed(self):
        for name, schema in {**submit.SCHEMAS, "stage": submit.stage_result_schema("spec")}.items():
            self.assertIs(schema["additionalProperties"], False, name)


class EachMapCoversItsLiteral(unittest.TestCase):
    def test_the_maps_cover_the_literals(self):
        self.assertEqual(set(submit.JUDGEMENTS), set(get_args(submit.Judgement)))
        self.assertEqual(set(submit.ROUND_STATES), set(get_args(submit.Verdict)))


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

    def test_every_refusal_says_to_correct_the_object_and_call_again(self):
        channel = self._channel(open_run=lambda: "other")
        for obj in ({"stage": "plan", "judgement": "ready", "questions": []}, _filled(channel, {})):
            said = asyncio.run(channel.handle(obj))
            self.assertTrue(said["content"][0]["text"].endswith(AGAIN), said)
        self.assertEqual(channel.refused, 2)
        self.assertIn(AGAIN, channel.description())

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
        self.assertNotIn("head", submit.SCHEMAS["review-round"]["properties"])

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
            open_findings=("F2",),
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


class AStandInReachesTheChannelThroughItsServer(unittest.TestCase):
    def test_submits_finds_the_channel_by_the_server_the_runner_passed(self):
        d = Path(tempfile.mkdtemp())
        channel = Channel(run="r", stage="plan", directory=d, artifact="plan.md", own=False)
        kw = {"mcp_servers": {"cos": channel.server()}}
        asyncio.run(submits(kw, judgement="not-ready"))
        self.assertEqual(channel.received["object"]["judgement"], "not-ready")
        self.assertIsNone(asyncio.run(submits({})))

    def test_a_schema_error_comes_back_as_the_sdk_says_it(self):
        d = Path(tempfile.mkdtemp())
        channel = Channel(run="r", stage="plan", directory=d, artifact="plan.md", own=False)
        said = asyncio.run(
            submits({"mcp_servers": {"cos": channel.server()}}, judgement="accepted")
        )
        self.assertTrue(said["is_error"])
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

    def test_the_two_are_the_grants_that_submit_and_are_no_stage(self):
        from coscc.agent import policy

        self.assertEqual(set(submit.SESSIONS), set(policy.SUBMITTING_SESSIONS))
        for kind in submit.SESSIONS:
            grant = policy.grant_for(kind)
            self.assertTrue(grant.submits, kind)
            self.assertGreaterEqual(grant.max_turns, policy.SUBMIT_TURNS, kind)


if __name__ == "__main__":
    unittest.main()
