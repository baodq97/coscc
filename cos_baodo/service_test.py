"""Tests for the one place logic lives.

These exist because `spec.md` R10 is a structural rule with no automated enforcement: if
the page and the API drift apart, it will be because someone put a decision in one of them.
Testing the service directly — with no web framework in the test — is what makes that
drift visible as a missing test rather than as a bug only one entry point has.
"""

from __future__ import annotations

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
