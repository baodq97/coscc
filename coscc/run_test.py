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

    def test_binding_every_interface_says_plain_http_is_readable(self):
        # `0070` R12: there is a login now, and what is left to say is the wire.
        lines = run.banner(Config(host="0.0.0.0", port=8790))
        text = "\n".join(lines)
        self.assertIn("login page", text)
        self.assertIn("readable", text)
        self.assertIn("COS_HOST=127.0.0.1", text)

    def test_a_named_interface_gets_the_same_warning(self):
        # The check is "not loopback", not "is 0.0.0.0" -- someone binding one real
        # interface is exposed the same way and must be told the same thing.
        text = "\n".join(run.banner(Config(host="192.168.1.10", port=8790)))
        self.assertIn("readable", text)
        self.assertIn("login page", text)

    def test_loopback_is_not_warned_about(self):
        for host in ("127.0.0.1", "localhost", "::1"):
            with self.subTest(host=host):
                text = "\n".join(run.banner(Config(host=host, port=8790)))
                self.assertNotIn("readable", text)

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

    def test_reset_password_takes_nothing_after_it(self):
        with self.assertRaises(SystemExit) as caught:
            run.main(["reset-password", "--now"])
        self.assertEqual(caught.exception.code, 2)


class ResetPassword(unittest.TestCase):
    """`0070` R10: the way back from a forgotten password, at a shell on this machine."""

    def test_reset_password_clears_and_names_the_database(self):
        import contextlib
        import io
        import tempfile
        from unittest import mock

        from coscc.data import Data

        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            data.auth_set_password("h", 1)
            data.auth_session_add("s", 1, 10**10)
            out = io.StringIO()
            with mock.patch.dict("os.environ", {"COS_DATA_DIR": d}), \
                    contextlib.redirect_stdout(out):
                run.main(["reset-password"])
            self.assertEqual(
                out.getvalue().strip(),
                f"coscc: password and sessions removed from {data.db_path}",
            )
            self.assertTrue(data.db_path.is_absolute())
            self.assertEqual(data.auth_state("s"), (False, None))


class TheServerIsHeld(unittest.TestCase):
    """`0068` plan step 5. `main` keeps its own `uvicorn.Server`, registers it for the
    updater, and after `run()` returns installs only when a hand-off was left."""

    def main_with(self, on_run, order=None, recover=None):
        import contextlib
        import io
        from unittest import mock

        from coscc import events, recovery, update

        order = [] if order is None else order

        class FakeServer:
            def __init__(self, config):
                order.append("server")
                self.config = config
                self.should_exit = False

            def run(self):
                on_run(self)

        finished = []
        with contextlib.ExitStack() as stack:
            # `0073`: never the real `cos.db` of whoever runs the tests.
            stack.enter_context(mock.patch.object(
                events, "purge_on_start", lambda config: order.append("purge") or (0, 0)))
            stack.enter_context(mock.patch.object(
                recovery, "recover_on_start",
                recover or (lambda config: order.append("recover") or 0)))
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

    def test_step_events_are_purged_before_the_server_is_built(self):
        """`0073` R14: before the first request, and a purge that fails does not stop it."""
        from unittest import mock

        from coscc import events

        order: list[str] = []
        self.main_with(lambda server: None, order)
        self.assertEqual(order, ["recover", "purge", "server"])

        def fails(config):
            raise RuntimeError("busy")

        import contextlib
        import io

        err = io.StringIO()
        with mock.patch.object(events, "purge_on_start", fails), contextlib.redirect_stderr(err):
            run.purge_events(object())
        self.assertIn("step events were not purged this start", err.getvalue())

    def test_a_recovery_that_fails_is_one_line_and_the_app_goes_on(self):
        """`0092` R5: one line on stderr, and `main` still reaches the purge and the server."""
        import contextlib
        import io
        from unittest import mock

        from coscc import recovery

        def fails(config):
            raise RuntimeError("busy")

        err = io.StringIO()
        with mock.patch.object(recovery, "recover_on_start", fails), contextlib.redirect_stderr(err):
            run.recover_steps(object())
        lines = err.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("were not ended this start: RuntimeError: busy", lines[0])

        order: list[str] = []

        def fails_in_order(config):
            order.append("recover")
            raise RuntimeError("busy")

        with contextlib.redirect_stderr(io.StringIO()):
            code, _ = self.main_with(lambda server: None, order, recover=fails_in_order)
        self.assertEqual((code, order), (None, ["recover", "purge", "server"]))

    def test_uvicorn_serves_the_guarded_app_and_trusts_no_proxy_header(self):
        """`0070` step 4: the guard is the target, and `X-Forwarded-For` is never read."""
        seen = []

        def capture(server):
            seen.append(server.config)

        self.main_with(capture)
        (args, kwargs), = seen
        self.assertEqual(args, ("coscc.coscc:served",))
        self.assertIs(kwargs["factory"], True)
        self.assertIs(kwargs["proxy_headers"], False)

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
