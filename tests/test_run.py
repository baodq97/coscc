"""Tests for the startup banner. Nothing here starts a server.

A test is what stops it from being quietly shortened later -- there is no other check in this
repository that would notice its absence."""

from __future__ import annotations

import json
import unittest

from coscc import run
from coscc.config import Config


class WhatStartupSays(unittest.TestCase):
    def test_binding_every_interface_says_plain_http_is_readable(self):
        # There is a login now, and what is left to say is the wire.
        lines = run.banner(Config(host="0.0.0.0", port=8790))
        text = "\n".join(lines)
        self.assertIn("login page", text)
        self.assertIn("readable", text)
        self.assertIn("COS_HOST=127.0.0.1", text)

    def test_loopback_is_not_warned_about(self):
        for host in ("127.0.0.1", "localhost", "::1"):
            with self.subTest(host=host):
                text = "\n".join(run.banner(Config(host=host, port=8790)))
                self.assertNotIn("readable", text)


class TheVersionAnswer(unittest.TestCase):
    """The only thing that separates an update from an apparent update."""

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

    def test_it_matches_the_version_the_repository_declares(self):
        # `coscc.loop check-version` keeps pyproject in step with four other places, so
        # agreeing with pyproject is agreeing with all of them.
        import re
        from pathlib import Path

        declared = re.search(
            r'^version = "([^"]+)"',
            Path("pyproject.toml").read_text(),
            re.MULTILINE,
        ).group(1)
        self.assertEqual(run.installed_version(), declared)


class ResetPassword(unittest.TestCase):
    """The way back from a forgotten password, at a shell on this machine."""

    def test_reset_password_clears_and_names_the_database(self):
        import contextlib
        import io
        import tempfile
        from unittest import mock

        from coscc.store.db import Data

        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            data.auth_set_password("h", 1)
            data.auth_session_add("s", 1, 10**10)
            out = io.StringIO()
            with (
                mock.patch.dict("os.environ", {"COS_DATA_DIR": d}),
                contextlib.redirect_stdout(out),
            ):
                run.main(["reset-password"])
            self.assertEqual(
                out.getvalue().strip(),
                f"coscc: password and sessions removed from {data.db_path}",
            )
            self.assertTrue(data.db_path.is_absolute())
            self.assertEqual(data.auth_state("s"), (False, None))


class TheStateCommand(unittest.TestCase):
    """The snapshot at a terminal, so `coscc.loop gate` can still be asked there."""

    def test_coscc_state_prints_what_the_loop_state_reads(self):
        import contextlib
        import io
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from unittest import mock

        from coscc import units
        from coscc.store.db import Data
        from coscc.loop import run as loop

        fixture = Path(__file__).resolve().parent / "units" / "testdata" / "meta_store"
        with tempfile.TemporaryDirectory() as d:
            work, data_dir = Path(d) / "work", Path(d) / "data"
            (work / "proj").mkdir(parents=True)
            data = Data(data_dir)
            with data.connect() as conn:
                conn.execute(
                    "INSERT INTO workspaces (root, name, added_at) VALUES (?, 'proj', 't')",
                    (str(work),),
                )
            store = units.root(work / "proj", data_dir)
            shutil.copytree(fixture, store)
            out = io.StringIO()
            env = {"COS_DATA_DIR": str(data_dir), "COS_WORKING_DIR": str(work)}
            with (
                mock.patch.dict("os.environ", env),
                contextlib.redirect_stdout(out),
                self.assertRaises(SystemExit) as done,
            ):
                run.main(["state", "proj"])
            self.assertEqual(done.exception.code, 0)
            snapshot = json.loads(out.getvalue())
            self.assertEqual(snapshot["workspace"], "proj")
            gate = loop.ask_sync(
                ["--root", str(store), "--state", "-", "gate", "0013_open-question", "plan"],
                stdin=out.getvalue(),
            )
            self.assertEqual(gate.code, 1)
            self.assertIn("spec.md", gate.err)
            with (
                mock.patch.dict("os.environ", env),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as refused,
            ):
                run.main(["state", "nobody"])
            self.assertEqual(refused.exception.code, 2)


class TheSkipCommand(unittest.TestCase):
    """A spec is skipped on a person's decision and on no agent's. `coscc
    skip` is how a person records one."""

    UNIT = "0013_open-question"

    def setUp(self):
        import shutil
        import tempfile
        from pathlib import Path

        from coscc import units
        from coscc.store.db import Data

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work, self.data_dir = Path(tmp.name) / "work", Path(tmp.name) / "data"
        (work / "proj").mkdir(parents=True)
        self.data = Data(self.data_dir)
        with self.data.connect() as conn:
            conn.execute(
                "INSERT INTO workspaces (root, name, added_at) VALUES (?, 'proj', 't')",
                (str(work),),
            )
        self.store = units.root(work / "proj", self.data_dir)
        self.key = units.key(work / "proj")
        shutil.copytree(
            Path(__file__).resolve().parent / "units" / "testdata" / "meta_store", self.store
        )
        self.env = {"COS_DATA_DIR": str(self.data_dir), "COS_WORKING_DIR": str(work)}

    def coscc(self, *args):
        import contextlib
        import io
        from unittest import mock

        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.dict("os.environ", self.env),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
            self.assertRaises(SystemExit) as done,
        ):
            run.main(list(args))
        return done.exception.code, out.getvalue(), err.getvalue()

    def gate_plan(self):
        from coscc.loop import run as loop

        code, snapshot, _ = self.coscc("state", "proj")
        self.assertEqual(code, 0)
        return loop.ask_sync(
            ["--root", str(self.store), "--state", "-", "gate", self.UNIT, "plan"],
            stdin=snapshot,
        )

    def spec_rows(self):
        from coscc.units.history import History

        rows = History(self.env["COS_WORKING_DIR"], self.data).transitions(self.key, self.UNIT)
        return [r for r in rows if r["artifact"] == "spec.md"]

    def test_a_persons_skip_opens_plan_through_the_skip_decision_guard(self):
        self.assertEqual(self.gate_plan().code, 1)
        code, out, _ = self.coscc("skip", "proj", self.UNIT, "spec", "one", "file,", "no", "schema")
        self.assertEqual(code, 0)
        self.assertIn("skipped by person", out)
        row = self.spec_rows()[-1]
        self.assertEqual(
            (row["to_state"], row["guard"], row["authority"], row["actor"]),
            ("skipped", "skip-decision", "person", "human:terminal"),
        )
        self.assertEqual(
            json.loads(row["inputs"]), {"authority": "person", "reason": "one file, no schema"}
        )
        gate = self.gate_plan()
        self.assertEqual(gate.code, 0, gate.err)

    def test_a_skip_no_person_recorded_keeps_plan_shut(self):
        from coscc.units.history import History

        self.assertEqual(self.coscc("state", "proj")[0], 0)
        # What the end of a spec step records when its file says `Status: skipped`.
        History(self.env["COS_WORKING_DIR"], self.data).record_many(
            [
                {
                    "workspace": self.key,
                    "unit": self.UNIT,
                    "artifact": "spec.md",
                    "to_state": "skipped",
                    "actor": "stage:spec",
                    "session": "s1",
                    "source": "run:spec",
                }
            ]
        )
        gate = self.gate_plan()
        self.assertEqual(gate.code, 1)
        self.assertIn("spec.md is skipped by unknown, not by a person", gate.err)

    def test_what_it_refuses(self):
        for args in (
            ("skip", "proj", self.UNIT, "plan", "why"),  # the machine has no skipped plan
            ("skip", "proj", self.UNIT, "spec"),  # no reason
            ("skip", "proj", "0099_nobody", "spec", "why"),
            ("skip", "nowhere", self.UNIT, "spec", "why"),
        ):
            self.assertEqual(self.coscc(*args)[0], 2, args)
        self.assertEqual(self.spec_rows()[-1]["to_state"], "draft")


class TheServerIsHeld(unittest.TestCase):
    """`main` keeps its own `uvicorn.Server`, registers it for the updater, and after `run()`
    returns installs only when a hand-off was left."""

    def main_with(self, on_run, order=None, recover=None):
        import contextlib
        import io
        from unittest import mock

        from coscc import update
        from coscc.runlog import events, recovery

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
            # Never the real `cos.db` of whoever runs the tests.
            stack.enter_context(
                mock.patch.object(
                    events, "purge_on_start", lambda config: order.append("purge") or (0, 0)
                )
            )
            stack.enter_context(
                mock.patch.object(
                    recovery,
                    "recover_on_start",
                    recover or (lambda config: order.append("recover") or 0),
                )
            )
            # Nor the real temp directory and data root.
            stack.enter_context(
                mock.patch.object(run, "sweep_scratch", lambda config: order.append("sweep"))
            )
            stack.enter_context(mock.patch("uvicorn.Server", FakeServer))
            stack.enter_context(mock.patch("uvicorn.Config", lambda *a, **k: (a, k)))
            stack.enter_context(
                mock.patch.object(update, "finish", lambda h: finished.append(h) or 75)
            )
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

    def test_step_events_are_purged_before_the_server_is_built(self):
        """Before the first request, and a purge that fails does not stop it."""
        from unittest import mock

        from coscc.runlog import events

        order: list[str] = []
        self.main_with(lambda server: None, order)
        self.assertEqual(order, ["recover", "purge", "sweep", "server"])

        def fails(config):
            raise RuntimeError("busy")

        with (
            mock.patch.object(events, "purge_on_start", fails),
            self.assertLogs("coscc.run", "ERROR") as log,
        ):
            run.purge_events(object())
        self.assertEqual(log.records[0].getMessage(), "step events were not purged this start")
        self.assertIsInstance(log.records[0].exc_info[1], RuntimeError)

    def test_a_recovery_that_fails_is_logged_and_the_app_goes_on(self):
        """One log record with its traceback, and `main` still reaches the purge and the server."""
        from unittest import mock

        from coscc.runlog import recovery

        def fails(config):
            raise RuntimeError("busy")

        with (
            mock.patch.object(recovery, "recover_on_start", fails),
            self.assertLogs("coscc.run", "ERROR") as log,
        ):
            run.recover_steps(object())
        [record] = log.records
        self.assertIn("were not ended this start", record.getMessage())
        self.assertEqual(str(record.exc_info[1]), "busy")

        order: list[str] = []

        def fails_in_order(config):
            order.append("recover")
            raise RuntimeError("busy")

        with self.assertLogs("coscc.run", "ERROR"):
            code, _ = self.main_with(lambda server: None, order, recover=fails_in_order)
        self.assertEqual((code, order), (None, ["recover", "purge", "sweep", "server"]))

    def test_uvicorn_serves_the_guarded_app_and_trusts_no_proxy_header(self):
        """The guard is the target, and `X-Forwarded-For` is never read."""
        seen = []

        def capture(server):
            seen.append(server.config)

        self.main_with(capture)
        ((args, kwargs),) = seen
        self.assertEqual(args, ("coscc.run:served",))
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


class StartupSweepsScratch(unittest.TestCase):
    def test_a_unit_no_workspace_has_loses_its_scratch(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest import mock

        from coscc import units
        from coscc.units import scratch

        with tempfile.TemporaryDirectory() as d:
            base = Path(d).resolve()
            (base / "tmp").mkdir()
            ws = base / "repo"
            data = base / "data"
            (units.cos_dir(ws, data) / "0001_kept").mkdir(parents=True)
            with mock.patch.object(tempfile, "tempdir", str(base / "tmp")):
                kept = scratch.ensure(ws, "0001_kept", data)
                ghost = scratch.ensure(ws, "9999_ghost", data)
                config = SimpleNamespace(workspaces=[str(ws)], working_dir=None, data_dir=data)
                run.sweep_scratch(config)
            self.assertTrue(all(p.exists() for p in kept))
            self.assertFalse(any(p.exists() for p in ghost))


if __name__ == "__main__":
    unittest.main()
