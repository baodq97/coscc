"""Tests for the startup banner. Nothing here starts a server.

A test is what stops it from being quietly shortened later -- there is no other check in this
repository that would notice its absence."""

from __future__ import annotations

import json
import unittest

from coscc import run
from coscc.config import Config


class WhatStartupSays(unittest.TestCase):
    def test_the_default_is_no_longer_loopback(self):
        # If this ever goes back, the warning below stops being reachable by default and
        # the test that checks it stops meaning anything.
        self.assertEqual(Config().host, "0.0.0.0")

    def test_binding_every_interface_says_plain_http_is_readable(self):
        # There is a login now, and what is left to say is the wire.
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
    """The only thing that separates an update from an apparent update."""

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
    """The way back from a forgotten password, at a shell on this machine."""

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
    """The snapshot at a terminal, so `cos.mjs gate` can still be asked there."""

    def test_coscc_state_prints_what_cos_mjs_state_reads(self):
        import contextlib
        import io
        import json
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path
        from unittest import mock

        from coscc import units
        from coscc.agent import harness
        from coscc.data import Data

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
            gate = subprocess.run(
                [
                    "node",
                    str(harness.script()),
                    "--root",
                    str(store),
                    "--state",
                    "-",
                    "gate",
                    "0013_open-question",
                    "plan",
                ],
                input=out.getvalue(),
                capture_output=True,
                text=True,
                env=harness.child_env(),
            )
            self.assertEqual(gate.returncode, 1)
            self.assertIn("spec.md", gate.stderr)
            with (
                mock.patch.dict("os.environ", env),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as refused,
            ):
                run.main(["state", "nobody"])
            self.assertEqual(refused.exception.code, 2)


class TheSkipCommand(unittest.TestCase):
    """A spec is skipped on a person's decision, or their delegate's, and on no agent's. `coscc
    skip` is how a person records one."""

    UNIT = "0013_open-question"

    def setUp(self):
        import shutil
        import tempfile
        from pathlib import Path

        from coscc import units
        from coscc.data import Data

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
        import subprocess

        from coscc.agent import harness

        code, snapshot, _ = self.coscc("state", "proj")
        self.assertEqual(code, 0)
        return subprocess.run(
            [
                "node",
                str(harness.script()),
                "--root",
                str(self.store),
                "--state",
                "-",
                "gate",
                self.UNIT,
                "plan",
            ],
            input=snapshot,
            capture_output=True,
            text=True,
            env=harness.child_env(),
        )

    def spec_rows(self):
        from coscc.units.history import History

        rows = History(self.env["COS_WORKING_DIR"], self.data).transitions(self.key, self.UNIT)
        return [r for r in rows if r["artifact"] == "spec.md"]

    def test_a_persons_skip_opens_plan_through_the_skip_decision_guard(self):
        self.assertEqual(self.gate_plan().returncode, 1)
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
        self.assertEqual(gate.returncode, 0, gate.stderr)

    def test_delegated_says_whose_it_is(self):
        self.assertEqual(
            self.coscc("skip", "proj", self.UNIT, "spec", "--delegated", "asked to")[0], 0
        )
        self.assertEqual(self.spec_rows()[-1]["authority"], "delegated")
        self.assertEqual(self.gate_plan().returncode, 0)

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
        self.assertEqual(gate.returncode, 1)
        self.assertIn(
            "spec.md is skipped by unknown, not by a person or their delegate", gate.stderr
        )

    def test_what_it_refuses(self):
        for args in (
            ("skip", "proj", self.UNIT, "plan", "why"),  # the machine has no skipped plan
            ("skip", "proj", self.UNIT, "spec"),  # no reason
            ("skip", "proj", "0099_nobody", "spec", "why"),
            ("skip", "nowhere", self.UNIT, "spec", "why"),
        ):
            self.assertEqual(self.coscc(*args)[0], 2, args)
        self.assertEqual(self.spec_rows()[-1]["to_state"], "draft")


class TheVaultMeasureDecidesOnThreeCounts(unittest.TestCase):
    def test_it_passes_on_ten_secrets_three_tools_and_no_hit_and_on_nothing_less(self):
        self.assertTrue(run.measure_passes(10, 3, 0))
        self.assertTrue(run.measure_passes(12, 5, 0))
        for counts in ((9, 3, 0), (10, 2, 0), (10, 3, 1), (0, 0, 0)):
            with self.subTest(counts=counts):
                self.assertFalse(run.measure_passes(*counts))


class TheVaultMeasureCommand(unittest.TestCase):
    """`coscc vault-measure` on a workspace whose run log shows ten secrets used through four
    programs, with the fake `age` standing in for the real one."""

    UNIT = "0001_demo"
    PROGRAMS = ("curl", "psql", "ssh", "git")

    def setUp(self):
        import tempfile
        from pathlib import Path

        from coscc import units
        from coscc.data import Data
        from coscc.runlog.journal import Journal
        from coscc.vault import Store, record
        from tests.vault import fakes

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.work, self.data_dir = root / "work", root / "data"
        (self.work / "proj").mkdir(parents=True)
        self.data = Data(self.data_dir)
        with self.data.connect() as conn:
            conn.execute(
                "INSERT INTO workspaces (root, name, added_at) VALUES (?, 'proj', 't')",
                (str(self.work),),
            )
        self.key = units.key(self.work / "proj")
        fakes.install(root / "bin")
        self.env = {
            "COS_DATA_DIR": str(self.data_dir),
            "COS_WORKING_DIR": str(self.work),
            "COS_UPDATE_CHECK": "0",
            "XDG_CONFIG_HOME": str(root / "config"),
            "HOME": str(root / "home"),
            **fakes.on_path(root),
        }
        self.store = Store(
            self.data,
            str(root / "config"),
            str(root / "home"),
            age=str(root / "bin" / "age"),
            age_keygen=str(root / "bin" / "age-keygen"),
        )
        self.journal = Journal(self.work, self.data)
        self.values = {}
        for i in range(10):
            name = f"ws:s{i}"
            self.values[name] = f"seeded-value-number-{i:02d}".encode()
            self.store.create(name, self.key)
            self.store.put(name, self.key, self.values[name])
            record(
                self.journal,
                "use",
                name,
                self.key,
                "agent:impl",
                names=[name],
                unit=self.UNIT,
                stage="impl",
                run="run-1",
                modes=["env"],
                programs=[self.PROGRAMS[i % 4]],
                codes=[],
            )

    def coscc(self, *args):
        import contextlib
        import io
        from unittest import mock

        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.dict("os.environ", self.env),
            # The one stream a GET route holds open never ends, so a second is all it gets.
            mock.patch.object(run, "ROUTE_WAIT", 1.0),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
            self.assertRaises(SystemExit) as done,
        ):
            run.main(list(args))
        return done.exception.code, out.getvalue(), err.getvalue()

    def result(self):
        (path,) = sorted((self.data_dir / "measurements").glob("vault-*.json"))
        return path, json.loads(path.read_text())

    def test_it_passes_on_seeded_data_and_writes_names_only_under_measurements(self):
        code, out, err = self.coscc("vault-measure", "proj")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("10 secrets used, 4 tools, 0 hits", out)
        path, result = self.result()
        self.assertEqual(
            (result["pass"], result["hits"], result["tools_used"]),
            (True, [], ["curl", "git", "psql", "ssh"]),
        )
        self.assertEqual(result["secrets_used"], sorted(self.values))
        for value in self.values.values():
            self.assertNotIn(value.decode(), path.read_text())

    def test_one_value_in_the_run_log_fails_it_and_the_file_names_where(self):
        self.journal.append(
            {
                "kind": "note",
                "workspace": self.key,
                "unit": self.UNIT,
                "text": "copied " + self.values["ws:s3"].decode(),
            }
        )
        code, out, _ = self.coscc("vault-measure", "proj")
        self.assertEqual(code, 1)
        self.assertIn("1 hits", out)
        path, result = self.result()
        self.assertEqual(
            (result["pass"], result["hits"]),
            (False, [{"name": "ws:s3", "where": f"{self.UNIT}/run-log", "form": "raw"}]),
        )
        self.assertNotIn(self.values["ws:s3"].decode(), path.read_text())

    def test_a_value_in_the_body_of_a_get_route_fails_it_too(self):
        from unittest import mock

        body = json.dumps({"seen": self.values["ws:s7"].decode()}).encode()
        with mock.patch.object(run, "_route_bodies", lambda config, cwd: [("GET /api/x", body)]):
            code, _, _ = self.coscc("vault-measure", "proj")
        self.assertEqual(code, 1)
        _, result = self.result()
        self.assertEqual([h["where"] for h in result["hits"]], ["GET /api/x"])

    def test_refused_uses_and_uses_outside_the_window_do_not_count(self):
        from coscc.vault import record

        record(
            self.journal,
            "use",
            "ws:nope",
            self.key,
            "agent:impl",
            names=["ws:nope"],
            unit=self.UNIT,
            stage="impl",
            run="run-2",
            modes=["env"],
            programs=["rsync"],
            codes=["unknown-secret"],
        )
        code, out, _ = self.coscc("vault-measure", "proj", "--since", "2999-01-01")
        self.assertEqual(code, 1)
        self.assertIn("0 secrets used, 0 tools", out)
        code, out, _ = self.coscc("vault-measure", "proj", "--until", "2999-01-01")
        self.assertEqual(code, 0)
        self.assertIn("10 secrets used, 4 tools", out)

    def test_it_says_its_usage_and_knows_only_the_workspaces_it_has(self):
        self.assertEqual(self.coscc("vault-measure")[0], 2)
        self.assertEqual(self.coscc("vault-measure", "proj", "extra")[0], 2)
        self.assertEqual(self.coscc("vault-measure", "nowhere")[0], 2)


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
            stack.enter_context(mock.patch("uvicorn.Server", FakeServer))
            stack.enter_context(mock.patch("uvicorn.Config", lambda *a, **k: (a, k)))
            stack.enter_context(mock.patch.object(run.frontend, "is_packaged", lambda: False))
            stack.enter_context(
                mock.patch.object(
                    run, "_refuse_a_bundle_that_does_not_match_the_source", lambda *a: None
                )
            )
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

    def test_no_hand_off_means_main_just_returns(self):
        code, finished = self.main_with(lambda server: None)
        self.assertEqual((code, finished), (None, []))

    def test_step_events_are_purged_before_the_server_is_built(self):
        """Before the first request, and a purge that fails does not stop it."""
        from unittest import mock

        from coscc.runlog import events

        order: list[str] = []
        self.main_with(lambda server: None, order)
        self.assertEqual(order, ["recover", "purge", "server"])

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
        self.assertEqual((code, order), (None, ["recover", "purge", "server"]))

    def test_uvicorn_serves_the_guarded_app_and_trusts_no_proxy_header(self):
        """The guard is the target, and `X-Forwarded-For` is never read."""
        seen = []

        def capture(server):
            seen.append(server.config)

        self.main_with(capture)
        ((args, kwargs),) = seen
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
