"""Tests for the one place logic lives.

These exist because `spec.md` R10 is a structural rule with no automated enforcement: if
the page and the API drift apart, it will be because someone put a decision in one of them.
Testing the service directly — with no web framework in the test — is what makes that
drift visible as a missing test rather than as a bug only one entry point has.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cos_baodo.config import Config
from cos_baodo.service import Invalid, Service
from cos_baodo.sessions import Sessions

REPO = str(Path(__file__).resolve().parent.parent)


def _service(**kw) -> Service:
    config = Config(workspaces=(REPO,), **kw)
    return Service(config, Sessions(config))


class TheGate(unittest.TestCase):
    def test_a_directory_outside_the_list_is_refused_everywhere(self):
        s = _service()
        for call in (
            lambda: s.sessions_for("/etc"),
            lambda: s.history("/etc", "abc"),
            lambda: s.check_send("/etc", "hi"),
        ):
            with self.assertRaises(Invalid):
                call()

    def test_the_reason_names_the_directory_so_a_caller_can_show_it(self):
        with self.assertRaises(Invalid) as e:
            _service().sessions_for("/nope")
        self.assertIn("/nope", str(e.exception))

    def test_a_configured_workspace_passes_the_gate(self):
        self.assertEqual(_service().sessions_for(REPO)["cwd"], REPO)


class WhatCountsAsInvalid(unittest.TestCase):
    def test_history_needs_a_session_id(self):
        with self.assertRaises(Invalid):
            _service().history(REPO, "")

    def test_empty_prompt_is_refused_before_anything_is_spent(self):
        # Quota matters here: this refusal must land before a session is created.
        for text in ("", "   ", "\n"):
            with self.assertRaises(Invalid):
                _service().check_send(REPO, text)

    def test_check_send_accepts_real_text(self):
        self.assertIsNone(_service().check_send(REPO, "hello"))


class TheGateWithAStore(unittest.TestCase):
    """`spec.md` R21 end to end: the gate, not just the store, must hold.

    The store tests prove a bad entry is dropped on read. These prove that dropping it
    actually closes every working path, which is the claim that matters.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _svc(self):
        config = Config(workspaces=(), working_dir=str(self.root))
        return Service(config, Sessions(config))

    def test_a_stored_workspace_passes_the_gate(self):
        (self.root / "repo").mkdir()
        s = self._svc()
        s.store.add("repo", "My repo")
        self.assertEqual(s.sessions_for(str(self.root / "repo"))["cwd"], str(self.root / "repo"))

    def test_a_hand_edited_entry_pointing_outside_closes_every_path(self):
        s = self._svc()
        s.store.path.write_text(json.dumps({
            "version": 1, "workspaces": [{"name": "/etc"}, {"name": "../../etc"}],
        }))
        for call in (
            lambda: s.sessions_for("/etc"),
            lambda: s.history("/etc", "abc"),
            lambda: s.check_send("/etc", "hi"),
        ):
            with self.assertRaises(Invalid):
                call()

    def test_a_sibling_of_the_working_folder_is_refused(self):
        s = self._svc()
        s.store.add("repo")
        with self.assertRaises(Invalid):
            s.check_send(str(self.root.parent), "hi")

    def test_a_subdirectory_nobody_added_is_refused(self):
        (self.root / "stray").mkdir()
        s = self._svc()
        with self.assertRaises(Invalid):
            s.check_send(str(self.root / "stray"), "hi")

    def test_removing_a_workspace_closes_the_gate_again(self):
        (self.root / "repo").mkdir()
        s = self._svc()
        s.store.add("repo")
        s.sessions_for(str(self.root / "repo"))
        s.store.remove("repo")
        with self.assertRaises(Invalid):
            s.check_send(str(self.root / "repo"), "hi")

    def test_no_working_folder_means_no_store_and_0002_behaviour(self):
        config = Config(workspaces=(REPO,))
        s = Service(config, Sessions(config))
        self.assertIsNone(s.store)
        self.assertEqual(s.workspaces()["paths"], [REPO])

    def test_the_count_is_reported_and_tracks_both_sources(self):
        (self.root / "a").mkdir()
        config = Config(workspaces=(REPO,), working_dir=str(self.root))
        s = Service(config, Sessions(config))
        self.assertEqual(s.workspaces()["count"], 1)
        s.store.add("a")
        self.assertEqual(s.workspaces()["count"], 2)
        s.store.remove("a")
        self.assertEqual(s.workspaces()["count"], 1)

    def test_a_missing_directory_is_flagged_not_hidden(self):
        s = self._svc()
        s.store.add("gone")
        row = next(r for r in s.workspaces()["workspaces"] if r["name"] == "gone")
        self.assertTrue(row["missing"])


class OneMembershipQuestion(unittest.TestCase):
    """`spec.md` R10, at the place `0003` found it broken.

    The session layer keeps its own guard — it is the last thing before a CLI process is
    spawned — but it must answer the same question the service gate answers. Before this,
    a store-backed workspace passed the gate and was refused one layer down, and only a
    real clone-and-send found it.
    """

    def test_the_session_layer_sees_store_workspaces_too(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "repo").mkdir()
            config = Config(workspaces=(), working_dir=str(root))
            s = Service(config, Sessions(config))
            s.store.add("repo")
            self.assertTrue(s.sessions.membership(str(root / "repo")))

    def test_the_session_layer_still_refuses_what_the_gate_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            config = Config(workspaces=(), working_dir=d)
            s = Service(config, Sessions(config))
            self.assertFalse(s.sessions.membership("/etc"))


class NoWebFrameworkLeaksIn(unittest.TestCase):
    def test_service_module_imports_no_web_framework(self):
        """`spec.md` R10 in the only form a test can hold it.

        If the service ever imports aiohttp, FastAPI or Reflex, logic has started moving
        back towards one entry point and the two will drift.
        """
        source = (Path(__file__).parent / "service.py").read_text()
        for banned in ("import aiohttp", "import fastapi", "import reflex", "from fastapi", "from aiohttp", "from reflex"):
            self.assertNotIn(banned, source)


if __name__ == "__main__":
    unittest.main()
