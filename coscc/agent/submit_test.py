"""`0136` R2, R3: the `submit` channel, and `submits`, what a stand-in session calls with it."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any

import jsonschema

from coscc.agent import submit
from coscc.agent.submit import AGAIN, Channel


def _filled(channel: Channel, fields: dict[str, Any]) -> dict[str, Any]:
    obj: dict[str, Any] = {"stage": channel.stage, "judgement": "ready", "questions": []}
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
        # The SDK's own words, `spike.md ## U1`.
        return {"content": [{"type": "text", "text": f"Input validation error: {e.message}"}], "is_error": True}
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
        jsonschema.validate({"stage": "intent", "judgement": "not-ready", "questions": [{"n": 1, "text": "?"}]}, schema)

    def test_a_stage_without_a_result_opens_no_channel(self):
        for stage in ("review", "pr", "ship", "integrate"):
            self.assertIsNone(submit.schema_for(stage), stage)

    def test_every_kind_of_object_is_closed(self):
        for name, schema in {**submit.SCHEMAS, "stage": submit.stage_result_schema("spec")}.items():
            self.assertIs(schema["additionalProperties"], False, name)


class TheChannelChecksWhatTheSchemaCannot(unittest.TestCase):
    """R3 a and b, and R2's words when it refuses."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "intent.md").write_text("# Intent: x\n", encoding="utf-8")

    def _channel(self, **kw: Any) -> Channel:
        return Channel(run="run-1", stage="spec", directory=self.dir, artifact="spec.md", own=False, **kw)

    def test_an_accepted_object_is_kept_with_the_revision_the_app_took(self):
        channel = self._channel()
        said = asyncio.run(channel.handle(_filled(channel, {"unmeasured": ["U1"]})))
        self.assertNotIn("is_error", said)
        self.assertEqual(channel.received["object"]["unmeasured"], ["U1"])
        self.assertEqual(channel.received["revision"], submit.revision(self.dir, "spec.md", own=False))

    def test_an_object_from_a_run_that_is_not_open_is_refused(self):
        """R3 a."""
        channel = self._channel(open_run=lambda: "run-2")
        said = asyncio.run(channel.handle(_filled(channel, {})))
        self.assertTrue(said["is_error"])
        self.assertIn("wrong-run", said["content"][0]["text"])
        self.assertIsNone(channel.received)

    def test_the_artifacts_changing_after_the_object_makes_it_stale(self):
        """R3 b: what the object judged is the revision the app hashed when it came."""
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
        """R2, `spike.md ## U2`."""
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
        said = asyncio.run(submits({"mcp_servers": {"cos": channel.server()}}, judgement="accepted"))
        self.assertTrue(said["is_error"])
        self.assertIsNone(channel.received)


if __name__ == "__main__":
    unittest.main()
