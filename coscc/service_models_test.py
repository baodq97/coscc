"""Tests for `ModelsMixin` in `coscc/service_models.py`, split from `coscc/service_test.py` (`0095`).
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.config import Config
from coscc.service_common import Invalid
from coscc.service import Service
from coscc.service_test import create_sync


class AStageRunsOnTheModelSettingsNames(unittest.TestCase):
    """`0004_no-setting-says-which-model-runs-a-stage`. The setting chooses the model a
    step's session is created with, and nothing else."""

    class Probe:
        def __init__(self):
            self.models = []

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            self.models.append(kw.get("model"))
            yield ("chunk", "# Spec: a problem\nAuthor: t. Status: accepted.\n\n## Body\n")
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
            return [i async for i in self.service.run_step(str(self.repo), self.made["unit"], stage)]

        return asyncio.run(go())

    def _start(self):
        journal = self.service._journal()
        return journal.records(self.service._journal_key(str(self.repo)), kind="start")[-1]

    def test_the_shipped_default_reaches_the_session(self):
        self._run("spec")
        self.assertEqual(self.probe.models, ["claude-opus-5-5[1m]"])
        self.assertEqual(self._start()["model_source"], "default")

    def test_an_override_reaches_the_session_and_the_log_then_goes_away(self):
        asyncio.run(self.service.set_stage_model("spec", "claude-sonnet-5"))
        self._run("spec")
        self.assertEqual(self.probe.models[-1], "claude-sonnet-5")
        self.assertEqual(
            (self._start()["model"], self._start()["model_source"]),
            ("claude-sonnet-5", "override"),
        )
        asyncio.run(self.service.set_stage_model("spec", None))
        self._run("spec")
        self.assertEqual(self._start()["model_source"], "default")

    def test_the_start_record_names_the_build_that_ran_it(self):
        # `0094` R13, R16: three fields more, and none of the ones already there is lost.
        from unittest import mock

        with mock.patch.object(self.service.updater, "me",
                               lambda: {"version": "9.8.7", "commit": "d" * 40, "shape": "x"}):
            self._run("spec")
        start = self._start()
        self.assertEqual((start["app_version"], start["app_commit"]), ("9.8.7", "d" * 40))
        self.assertEqual(start["pointed"], [])
        for kept in ("included", "prompt_chars", "granted", "max_turns", "head", "model",
                     "model_source", "base", "system_prompt", "instructions"):
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
                asyncio.run(self.service.set_stage_model(name, model))

    def test_each_change_leaves_one_setting_record(self):
        asyncio.run(self.service.set_stage_model("impl", "a"))
        asyncio.run(self.service.set_stage_model("impl", "b"))
        asyncio.run(self.service.set_stage_model("impl", None))
        records = self.service._journal().records("", kind="setting")
        self.assertEqual(
            [(r["name"], r["old"], r["new"]) for r in records],
            [("model:impl", None, "a"), ("model:impl", "a", "b"), ("model:impl", "b", None)],
        )

    def test_the_gate_is_asked_the_same_question_either_way(self):
        from coscc import board as board_reader

        seen = []
        real = board_reader.gate

        async def spy(*a, **kw):
            seen.append((a, kw))
            return await real(*a, **kw)

        with mock.patch.object(board_reader, "gate", spy):
            self._run("spec")
            asyncio.run(self.service.set_stage_model("spec", "x"))
            self._run("spec")
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0], seen[1])

    def test_a_spec_step_has_no_label_and_the_default_effort(self):
        # `0033` R10: a stage before `plan` has no label; effort comes from R8's table.
        self._run("spec")
        start = self._start()
        self.assertEqual((start["label_declared"], start["label"], start["label_source"]), (None, None, None))
        self.assertEqual((start["effort"], start["effort_source"]), ("high", "default"))
        self.assertNotIn("impl_run", start)

    def test_an_effort_override_of_max_is_taken_and_logged(self):
        # R7: `max` only through an override; R9: every change is a `setting` record.
        asyncio.run(self.service.set_stage_effort("impl:novel", "max"))
        asyncio.run(self.service.set_stage_effort("impl:novel", None))
        records = self.service._journal().records("", kind="setting")
        self.assertEqual(
            [(r["name"], r["old"], r["new"]) for r in records],
            [("effort:impl:novel", None, "max"), ("effort:impl:novel", "max", None)],
        )
        for name, effort in (("chat", "low"), ("plan:novel", "low"), ("impl", "turbo"), ("bogus", "low")):
            with self.assertRaises(Invalid, msg=(name, effort)):
                asyncio.run(self.service.set_stage_effort(name, effort))

    def test_a_novel_row_takes_a_model_override(self):
        asyncio.run(self.service.set_stage_model("review:novel", "m"))
        rows = {r["name"]: r for r in asyncio.run(self.service.stage_models())["rows"]}
        self.assertEqual((rows["review:novel"]["model"], rows["review:novel"]["source"]), ("m", "override"))
        self.assertEqual(rows["review"]["source"], "default")

    def test_model_prefs_are_not_preferences(self):
        asyncio.run(self.service.set_stage_model("impl", "a"))
        self.assertNotIn("model:impl", self.service.preferences())
        with self.assertRaises(Invalid):
            self.service.set_preference("model:impl", "b")

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
            return [i async for i in service.stream(str(self.repo), "hi")]

        asyncio.run(go())
        self.assertEqual(self.probe.models[-1], "env-model")
        [rec] = service._journal().records(service._journal_key(str(self.repo)), kind="chat")
        self.assertEqual((rec["model"], rec["model_source"]), ("env-model", "COS_MODEL"))

    def test_settings_names_cos_model_as_the_fallback(self):
        self.assertIn("cos_model", self.service.settings())
        self.assertNotIn("model", self.service.settings())

    def test_settings_shows_the_novel_impl_ceilings_after_impl(self):
        """`0062` R9."""
        rows = self.service.settings()["grants"]
        stages = [r["stage"] for r in rows]
        impl, novel = rows[stages.index("impl")], rows[stages.index("impl:novel")]
        self.assertEqual(stages.index("impl:novel"), stages.index("impl") + 1)
        self.assertEqual((novel["max_turns"], novel["max_budget_usd"]), (250, 16.0))
        self.assertEqual((impl["max_turns"], impl["max_budget_usd"]), (120, 8.0))
        for field in ("tools", "commands", "warning"):
            self.assertEqual(novel[field], impl[field], field)
        for stage in ("pr:novel", "review:novel", "ship:novel"):
            self.assertNotIn(stage, stages)

    def test_a_grants_tools_and_commands_are_also_lists(self):
        """`0082` F2: the page lists them; the joined strings stay in the API as they were."""
        for row in self.service.settings()["grants"]:
            self.assertEqual(", ".join(row["tool_list"]) or "none", row["tools"], row["stage"])
            self.assertEqual(", ".join(row["command_list"]) or "none", row["commands"], row["stage"])
