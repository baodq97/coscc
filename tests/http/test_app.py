"""Tests for `Core`, the app's assembly (`coscc/http/app.py`), and the helpers other tests share.

Testing the parts directly — with no web framework in the test — is what makes that drift visible
as a missing test rather than as a bug only one entry point has."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from coscc import units
from coscc.agent import harness
from coscc.config import Config
from coscc.http.app import Core
from coscc.kernel import Invalid
from coscc.agent.sessions import Sessions
from coscc.store.journal import totals_of
from coscc.units.history import History
from tests.units.test_meta import seed

REPO = str(Path(__file__).resolve().parents[2])


def create_sync(core: Core, *args):
    return asyncio.run(core.answers.create_unit(*args))


def seed_unit(core: Core, cwd: str, unit: str, **kw) -> None:
    """Test glue: `unit` of workspace `cwd` stated as rows in the core's database (see `seed`)."""
    seed(core.ws.unit_meta(), core.ws.key(cwd), unit, **kw)


def _core(**kw) -> Core:
    config = Config(workspaces=(REPO,), **kw)
    return Core(config, Sessions(config))


def use_sessions(core, fake) -> None:
    """`fake` in place of `core.sessions`, in every part that holds it."""
    old = core.sessions
    for part in [core, *vars(core).values()]:
        if getattr(part, "sessions", None) is old:
            part.sessions = fake


def use_config(core, config) -> None:
    """`config` in place of `core.config`, in every part that holds it."""
    old = core.config
    for part in [core, *vars(core).values()]:
        if getattr(part, "config", None) is old:
            part.config = config


class TheGate(unittest.TestCase):
    def test_a_directory_outside_the_list_is_refused_everywhere(self):
        s = _core()
        for call in (
            lambda: s.chat.sessions_for("/etc"),
            lambda: s.chat.history("/etc", "abc"),
            lambda: s.chat.check_send("/etc", "hi"),
        ):
            with self.assertRaises(Invalid):
                call()

    def test_a_configured_workspace_passes_the_gate(self):
        self.assertEqual(_core().chat.sessions_for(REPO)["cwd"], REPO)


class WhatCountsAsInvalid(unittest.TestCase):
    def test_history_needs_a_session_id(self):
        with self.assertRaises(Invalid):
            _core().chat.history(REPO, "")

    def test_empty_prompt_is_refused_before_anything_is_spent(self):
        # Quota matters here: this refusal must land before a session is created.
        for text in ("", "   ", "\n"):
            with self.assertRaises(Invalid):
                _core().chat.check_send(REPO, text)


class TheGateWithAStore(unittest.TestCase):
    """The store tests prove a bad entry is dropped on read. These prove that dropping it
    actually closes every working path, which is the claim that matters."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _svc(self):
        config = Config(workspaces=(), working_dir=str(self.root), data_dir=str(self.root))
        return Core(config, Sessions(config))

    def test_a_stored_workspace_passes_the_gate(self):
        (self.root / "repo").mkdir()
        s = self._svc()
        s.ws.store.add("repo", "My repo")
        self.assertEqual(
            s.chat.sessions_for(str(self.root / "repo"))["cwd"], str(self.root / "repo")
        )

    def test_a_hand_edited_entry_pointing_outside_closes_every_path(self):
        s = self._svc()
        # What somebody with `sqlite3` on the command line could type. The name is
        # rejected on read, so the row exists and the workspace does not.
        with s.ws.store.data.write() as conn:
            for name in ("/etc", "../../etc"):
                conn.execute(
                    "INSERT INTO workspaces (root, name, label, added_at) "
                    "VALUES (?, ?, '', '2026-01-01T00:00:00+00:00')",
                    (str(s.ws.store.working_dir), name),
                )
        for call in (
            lambda: s.chat.sessions_for("/etc"),
            lambda: s.chat.history("/etc", "abc"),
            lambda: s.chat.check_send("/etc", "hi"),
        ):
            with self.assertRaises(Invalid):
                call()

    def test_a_sibling_of_the_working_folder_is_refused(self):
        s = self._svc()
        s.ws.store.add("repo")
        with self.assertRaises(Invalid):
            s.chat.check_send(str(self.root.parent), "hi")

    def test_a_subdirectory_nobody_added_is_refused(self):
        (self.root / "stray").mkdir()
        s = self._svc()
        with self.assertRaises(Invalid):
            s.chat.check_send(str(self.root / "stray"), "hi")

    def test_removing_a_workspace_closes_the_gate_again(self):
        (self.root / "repo").mkdir()
        s = self._svc()
        s.ws.store.add("repo")
        s.chat.sessions_for(str(self.root / "repo"))
        s.ws.store.remove("repo")
        with self.assertRaises(Invalid):
            s.chat.check_send(str(self.root / "repo"), "hi")


class ARefusalFromRunnerStaysARefusal(unittest.TestCase):
    """The boundary `coscc/http/routes.py` depends on, and the one review caught open.

    `run_step` maps this layer's refusals with one `except RunError`. 0012 introduced a
    second exception type on that path -- `harness.MissingRules`, raised when a stage's
    rules cannot be found -- and for one commit it escaped: measured 2026-09-22, a
    workspace whose harness had no skills produced an unhandled
    `MissingRules` where a 400 was intended, so the page would have shown a 500 for the
    exact failure 0012 was built to report clearly.

    The scenario is not hypothetical: it is a release whose copy step left out `skills/`, which is one of the things
    `harness.wheel_complaints` exists to refuse.
    """

    def test_a_stage_whose_rules_are_missing_is_invalid_not_an_escaped_exception(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            workspace = root / "work" / "proj"
            workspace.mkdir(parents=True)
            # A unit's artifacts live in the product's store, never in the workspace tree, so the
            # fixture has to be built where the product looks.
            unit = units.unit_dir(workspace, "0009_a-test-unit", root / "data")
            unit.mkdir(parents=True)
            (unit / "intent.md").write_text("I", encoding="utf-8")
            (unit / "spec.md").write_text("S", encoding="utf-8")

            # A harness carrying no skills: the Board reads, every Run refuses.
            half = root / "half"
            half.mkdir()
            (half / "skills").mkdir()

            config = Config(
                workspaces=(),
                working_dir=str(root / "work"),
                data_dir=str(root / "data"),
            )
            core = Core(config, Sessions(config))
            asyncio.run(core.ws.add("proj"))
            seed_unit(
                core,
                str(workspace),
                "0009_a-test-unit",
                statuses={"intent.md": "accepted", "spec.md": "accepted"},
            )

            originals = (harness.PACKAGE_HARNESS, harness.CHECKOUT_HARNESS)
            harness.PACKAGE_HARNESS = Path("/nonexistent/packaged")
            harness.CHECKOUT_HARNESS = half
            try:

                async def go():
                    async for _ in core.steps.run_step(str(workspace), "0009_a-test-unit", "plan"):
                        pass

                with self.assertRaises(Invalid) as caught:
                    asyncio.run(go())
            finally:
                harness.PACKAGE_HARNESS, harness.CHECKOUT_HARNESS = originals

            message = str(caught.exception)
            self.assertIn("plan", message)
            self.assertIn("SKILL.md", message)


if __name__ == "__main__":
    unittest.main()


def unit_history(core: Core, cwd: str, unit: str) -> dict:
    """The transitions of a unit and where each artifact stands, read from the log."""
    history = History(core.config.working_dir, core.config.data_dir)
    rows = history.transitions(core.ws.key(cwd), unit)
    state = {r["artifact"]: r["to_state"] for r in rows}
    edits = sum(1 for r in rows if history.machine.is_settled(r["from_state"]))
    return {"transitions": rows, "state": state, "settled_edits": edits}


def timeline(core: Core, cwd: str, unit: str) -> dict:
    runs = core.ws.journal().timeline(core.ws.key(cwd), unit)
    return {"runs": runs, "cost": totals_of(runs)}


class OneMembershipQuestion(unittest.TestCase):
    """The session layer keeps its own guard — it is the last thing before a CLI process is
    spawned — but it must answer the same question the core gate answers. Before this,
    a store-backed workspace passed the gate and was refused one layer down, and only a
    real clone-and-send found it."""

    def test_the_session_layer_sees_store_workspaces_too(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "repo").mkdir()
            config = Config(workspaces=(), working_dir=str(root), data_dir=str(root))
            s = Core(config, Sessions(config))
            s.ws.store.add("repo")
            self.assertTrue(s.sessions.membership(str(root / "repo")))

    def test_the_session_layer_still_refuses_what_the_gate_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            config = Config(workspaces=(), working_dir=d, data_dir=d)
            s = Core(config, Sessions(config))
            self.assertFalse(s.sessions.membership("/etc"))
