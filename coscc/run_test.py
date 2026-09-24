"""Tests for the startup banner. Nothing here starts a server.

The banner is the only place a person is told what binding `0.0.0.0` costs them, and
`0011`'s `spec.md` R5 makes that a requirement rather than a courtesy. A test is what
stops it from being quietly shortened later -- there is no other check in this repository
that would notice its absence.
"""

from __future__ import annotations

import unittest

from coscc import run
from coscc.config import Config


class WhatStartupSays(unittest.TestCase):
    def test_the_default_is_no_longer_loopback(self):
        # If this ever goes back, the warning below stops being reachable by default and
        # the test that checks it stops meaning anything.
        self.assertEqual(Config().host, "0.0.0.0")

    def test_binding_every_interface_says_there_is_no_login(self):
        lines = run.banner(Config(host="0.0.0.0", port=8790))
        text = "\n".join(lines)
        self.assertIn("no login", text)
        self.assertIn("quota", text)
        self.assertIn("COS_HOST=127.0.0.1", text)

    def test_a_named_interface_gets_the_same_warning(self):
        # The check is "not loopback", not "is 0.0.0.0" -- someone binding one real
        # interface is exposed the same way and must be told the same thing.
        text = "\n".join(run.banner(Config(host="192.168.1.10", port=8790)))
        self.assertIn("no login", text)

    def test_loopback_is_not_warned_about(self):
        for host in ("127.0.0.1", "localhost", "::1"):
            with self.subTest(host=host):
                text = "\n".join(run.banner(Config(host=host, port=8790)))
                self.assertNotIn("no login", text)

    def test_the_address_is_always_the_first_line(self):
        first = run.banner(Config(host="0.0.0.0", port=9001))[0]
        self.assertEqual(first, "coscc on http://0.0.0.0:9001")



class TheVersionAnswer(unittest.TestCase):
    """`spec.md` R8. The only thing that separates an update from an apparent update."""

    def test_it_matches_the_version_the_repository_declares(self):
        # `cos.mjs check-version` keeps pyproject in step with four other places, so
        # agreeing with pyproject is agreeing with all of them.
        import re
        from pathlib import Path

        declared = re.search(
            r'^version = "([^"]+)"',
            Path("pyproject.toml").read_text(),
            re.MULTILINE,
        ).group(1)
        self.assertEqual(run.installed_version(), declared)

    def test_it_prints_one_line_in_the_pinned_shape(self):
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            run.main(["--version"])
        printed = out.getvalue().splitlines()
        self.assertEqual(len(printed), 1)
        self.assertRegex(printed[0], r"^coscc \S+$")

    def test_a_mistyped_flag_does_not_start_a_server(self):
        # Since 0011 the default bind is 0.0.0.0, so "ignore the argument and carry on"
        # would put a network-reachable server behind a typo.
        with self.assertRaises(SystemExit) as caught:
            run.main(["--verison"])
        self.assertEqual(caught.exception.code, 2)


class TheServerIsHeld(unittest.TestCase):
    """`0068` plan step 5. `main` keeps its own `uvicorn.Server`, registers it for the
    updater, and after `run()` returns installs only when a hand-off was left."""

    def main_with(self, on_run):
        import contextlib
        import io
        from unittest import mock

        from coscc import update

        class FakeServer:
            def __init__(self, config):
                self.config = config
                self.should_exit = False

            def run(self):
                on_run(self)

        finished = []
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch("uvicorn.Server", FakeServer))
            stack.enter_context(mock.patch("uvicorn.Config", lambda *a, **k: (a, k)))
            stack.enter_context(mock.patch.object(run.frontend, "is_packaged", lambda: False))
            stack.enter_context(mock.patch.object(run, "_refuse_a_bundle_that_does_not_match_the_source", lambda *a: None))
            stack.enter_context(mock.patch.object(update, "finish", lambda h: finished.append(h) or 75))
            stack.enter_context(mock.patch.dict("os.environ", {}, clear=False))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            try:
                run.main([])
                code = None
            except SystemExit as e:
                code = e.code
            finally:
                update.SERVER.server = None
        return code, finished

    def test_no_hand_off_means_main_just_returns(self):
        code, finished = self.main_with(lambda server: None)
        self.assertEqual((code, finished), (None, []))

    def test_a_hand_off_installs_once_and_exits_75(self):
        from coscc import update

        def asked_to_stop(server):
            self.assertIs(update.SERVER.server, server)
            update.SERVER.hand_off("the hand-off")  # type: ignore[arg-type]

        code, finished = self.main_with(asked_to_stop)
        self.assertEqual((code, finished), (75, ["the hand-off"]))
        self.assertIsNone(update.take_handoff())


if __name__ == "__main__":
    unittest.main()
