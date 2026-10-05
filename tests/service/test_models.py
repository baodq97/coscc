"""Tests for `Models` in `coscc/service/models.py`, split from `tests/service/test_service.py`."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.bus import Bus
from coscc.config import Config
from coscc.kernel import Invalid
from coscc.service import Service
from tests.service.test_service import create_sync
from tests.units.test_submit import submits as _submits


class AStageRunsOnTheModelSettingsNames(unittest.TestCase):
    """The setting chooses the model a step's session is created with, and nothing else."""

    class Probe:
        bus = Bus()

        def __init__(self):
            self.models = []

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            self.models.append(kw.get("model"))
            yield ("chunk", "# Spec: a problem\nAuthor: t. Status: accepted.\n\n## Body\n")
            await _submits(kw)
            yield ("done", {"session_id": "sess-m", "cost": {}})

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "work" / "proj"
        self.repo.mkdir(parents=True)
        self.probe = self.Probe()
        self.service = Service(
            Config(
                workspaces=(str(self.repo),),
                working_dir=str(self.root / "work"),
                data_dir=str(self.root / "data"),
            ),
            self.probe,
        )
        self.made = create_sync(self.service, str(self.repo), "a-problem", "some words")
        (Path(self.made["path"]) / "intent.md").write_text(
            "# Intent: a problem\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8"
        )

    def _run(self, stage: str):
        async def go():
            return [
                i
                async for i in self.service.steps.run_step(str(self.repo), self.made["unit"], stage)
            ]

        return asyncio.run(go())

    def _start(self):
        journal = self.service.ws.journal()
        return journal.records(self.service.ws.key(str(self.repo)), kind="start")[-1]

    def test_the_shipped_default_reaches_the_session(self):
        self._run("spec")
        self.assertEqual(self.probe.models, ["claude-opus-5-5[1m]"])
        self.assertEqual(self._start()["model_source"], "default")

    def test_an_override_reaches_the_session_and_the_log_then_goes_away(self):
        self.service.agents.set_agent_field("spec", "model", "claude-sonnet-5")
        self._run("spec")
        self.assertEqual(self.probe.models[-1], "claude-sonnet-5")
        self.assertEqual(
            (self._start()["model"], self._start()["model_source"]),
            ("claude-sonnet-5", "override"),
        )
        self.service.agents.set_agent_field("spec", "model", None)
        self._run("spec")
        self.assertEqual(self._start()["model_source"], "default")

    def test_the_start_record_names_the_build_that_ran_it(self):
        # Three fields more, and none of the ones already there is lost.
        from unittest import mock

        with mock.patch.object(
            self.service.updater,
            "me",
            lambda: {"version": "9.8.7", "commit": "d" * 40, "shape": "x"},
        ):
            self._run("spec")
        start = self._start()
        self.assertEqual((start["app_version"], start["app_commit"]), ("9.8.7", "d" * 40))
        self.assertEqual(start["pointed"], [])
        for kept in (
            "included",
            "prompt_chars",
            "granted",
            "max_turns",
            "head",
            "model",
            "model_source",
            "base",
            "system_prompt",
            "instructions",
        ):
            self.assertIn(kept, start)

    def test_a_build_that_cannot_be_read_is_two_empty_strings_and_the_step_runs(self):
        from unittest import mock

        def broken():
            raise OSError("no identity")

        with mock.patch.object(self.service.updater, "me", broken):
            self._run("spec")
        start = self._start()
        self.assertEqual((start["app_version"], start["app_commit"]), ("", ""))
        self.assertEqual(self.probe.models, ["claude-opus-5-5[1m]"])

    def test_bad_names_and_empty_models_are_invalid(self):
        for name, model in (("bogus", "m"), ("spec", "  "), ("spec", 3), ("", "m"), (None, "m")):
            with self.assertRaises(Invalid, msg=(name, model)):
                self.service.agents.set_agent_field(name, "model", model)

    def test_each_change_leaves_one_setting_record(self):
        self.service.agents.set_agent_field("impl", "model", "a")
        self.service.agents.set_agent_field("impl", "model", "b")
        self.service.agents.set_agent_field("impl", "model", None)
        records = self.service.ws.journal().records("", kind="agent-setting")
        self.assertEqual(
            [(r["agent"], r["field"], r["old"], r["new"]) for r in records],
            [
                ("impl", "model", None, "a"),
                ("impl", "model", "a", "b"),
                ("impl", "model", "b", None),
            ],
        )

    def test_the_gate_is_asked_the_same_question_either_way(self):
        from coscc.units import board as board_reader

        seen = []
        real = board_reader.gate

        async def spy(*a, **kw):
            seen.append((a, kw))
            return await real(*a, **kw)

        with mock.patch.object(board_reader, "gate", spy):
            self._run("spec")
            self.service.agents.set_agent_field("spec", "model", "x")
            self._run("spec")
        self.assertEqual(len(seen), 2)
        # The snapshot is the unit as it stands, and the first step wrote `spec.md` between the two
        # asks; the question is the rest.
        without_state = [(a, {k: v for k, v in kw.items() if k != "state"}) for a, kw in seen]
        self.assertEqual(without_state[0], without_state[1])

    def test_a_spec_step_has_no_label_and_the_default_effort(self):
        self._run("spec")
        start = self._start()
        self.assertEqual(
            (start["label_declared"], start["label"], start["label_source"]), (None, None, None)
        )
        self.assertEqual((start["effort"], start["effort_source"]), ("medium", "default"))
        self.assertNotIn("impl_run", start)

    def test_an_effort_override_of_max_is_taken_and_logged(self):
        self.service.agents.set_agent_field("impl:novel", "effort", "max")
        self.service.agents.set_agent_field("impl:novel", "effort", None)
        records = self.service.ws.journal().records("", kind="agent-setting")
        self.assertEqual(
            [(r["agent"], r["field"], r["old"], r["new"]) for r in records],
            [("impl:novel", "effort", None, "max"), ("impl:novel", "effort", "max", None)],
        )
        for name, effort in (
            ("chat", "low"),
            ("plan:novel", "low"),
            ("impl", "turbo"),
            ("bogus", "low"),
        ):
            with self.assertRaises(Invalid, msg=(name, effort)):
                self.service.agents.set_agent_field(name, "effort", effort)

    def test_a_novel_row_takes_a_model_override(self):
        self.service.agents.set_agent_field("review:novel", "model", "m")
        [review] = [r for r in self.service.agents.agent_page()["rows"] if r["key"] == "review"]
        self.assertEqual(
            (review["variants"][0]["model"], review["variants"][0]["model_source"]),
            ("m", "override"),
        )
        self.assertEqual(review["config"]["model_source"], "default")

    def test_chat_uses_cos_model_and_is_logged(self):
        service = Service(
            Config(
                workspaces=(str(self.repo),),
                working_dir=str(self.root / "work"),
                data_dir=str(self.root / "data"),
                model="env-model",
            ),
            self.probe,
        )

        async def go():
            return [i async for i in service.chat.stream(str(self.repo), "hi")]

        asyncio.run(go())
        self.assertEqual(self.probe.models[-1], "env-model")
        [rec] = service.ws.journal().records(service.ws.key(str(self.repo)), kind="chat")
        self.assertEqual((rec["model"], rec["model_source"]), ("env-model", "COS_MODEL"))

    def test_settings_never_show_the_trial(self):
        """The Agents page shows `models.json` and the overrides, never an arm's model."""
        page = self.service.agents.agent_page()
        rows = [r["config"] for r in page["rows"]] + page["others"]
        self.assertFalse([r for r in rows if r["model_source"] == "trial"])
