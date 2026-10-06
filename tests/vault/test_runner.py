"""Running a command with secrets: what is checked first, what is cleaned after, what is logged."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.store.journal import Journal
from coscc.vault.runner import Result, Use, record, run
from coscc.vault.store import BadSecret
from tests.vault.fakes import make_store, on_path

WS = "/ws/proj"
TOKEN = b"tok-9f8e7d6c5b4a"


class Rig:
    """A store on fakes, a run log, a tmpfs stand-in and a `run` that fills in what a caller has."""

    def __init__(self, test: unittest.TestCase, ssh: bool = False):
        self.store, self.root = make_store(test, ssh)
        self.work = self.root / "work"
        self.work.mkdir()
        # Short, as `/run/user/<uid>` is: an `ssh-agent` socket path past 108 bytes cannot bind.
        self.xdg = Path(tempfile.mkdtemp(dir="/dev/shm" if os.path.isdir("/dev/shm") else None))
        test.addCleanup(shutil.rmtree, self.xdg, True)
        self.journal = Journal(self.work, self.store.data)
        env = mock.patch.dict(os.environ, {**on_path(self.root), "XDG_RUNTIME_DIR": str(self.xdg)})
        env.start()
        test.addCleanup(env.stop)

    def secret(self, name: str, value: bytes, workspace: str = WS, **more) -> None:
        self.store.create(name, workspace, **more)
        self.store.put(name, workspace, value)

    def run(self, command: str, *uses: Use, stage: str = "impl", **more) -> Result:
        return run(
            self.store,
            command=command,
            uses=uses,
            workspace=WS,
            stage=stage,
            unit="0001_demo",
            run="run-1",
            cwd=str(self.work),
            journal=self.journal,
            **more,
        )

    def lines(self) -> list[dict]:
        return self.journal.records(kind="vault")

    def everything_logged(self) -> str:
        return json.dumps(self.journal.records())


class ACommandGetsTheSecretsItAskedFor(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(self)
        self.rig.secret("ws:tok", TOKEN, modes=("env", "file", "placeholder"))

    def test_an_env_secret_is_in_the_variable_and_masked_on_the_way_out(self):
        done = self.rig.run('echo "the token is $T"', Use("ws:tok", "env", "T"))
        self.assertEqual(done.exit_code, 0)
        self.assertEqual(done.stdout, "the token is [secret:ws:tok]\n")
        self.assertEqual(
            (done.masked, done.refusals, done.refused, done.captured), ({"ws:tok": 1}, (), "", 0)
        )

    def test_an_env_secret_with_no_variable_named_gets_one_made_from_its_name(self):
        done = self.rig.run('echo "$SECRET_TOK"', Use("ws:tok", "env"))
        self.assertEqual(done.stdout, "[secret:ws:tok]\n")

    def test_a_file_secret_is_a_private_file_on_tmpfs_gone_when_the_command_ends(self):
        done = self.rig.run(
            'stat -c %a "$KEYFILE"; echo "$KEYFILE"; cat "$KEYFILE"',
            Use("ws:tok", "file", "KEYFILE"),
        )
        mode, path, body = done.stdout.split("\n")
        self.assertEqual((mode, body), ("600", "[secret:ws:tok]"))
        self.assertTrue(path.startswith(str(self.rig.xdg)))
        self.assertFalse(Path(path).exists())
        self.assertEqual(list(self.rig.xdg.iterdir()), [])

    def test_a_placeholder_is_replaced_by_the_value_only_after_the_line_passed_the_check(self):
        done = self.rig.run("echo {{secret:ws:tok}} | wc -c", Use("ws:tok", "placeholder"))
        self.assertEqual(done.stdout.strip(), str(len(TOKEN) + 1))

    def test_a_placeholder_with_no_use_named_is_asked_of_the_policy_as_a_placeholder(self):
        self.rig.secret("ws:env-only", b"another-value-1", modes=("env",))
        done = self.rig.run("echo {{secret:ws:env-only}}")
        self.assertEqual(done.refusals[0][:2], ("ws:env-only", "mode-not-allowed"))

    def test_the_filter_covers_every_visible_secret_and_every_form_not_only_the_ones_used(self):
        self.rig.secret("ws:other", b"unused-value-77")
        done = self.rig.run(
            'printf %s "$T" | base64; echo unused-value-77', Use("ws:tok", "env", "T")
        )
        self.assertEqual(done.stdout, "[secret:ws:tok]\n[secret:ws:other]\n")
        self.assertEqual(done.masked, {"ws:tok": 1, "ws:other": 1})

    def test_stderr_is_filtered_too(self):
        done = self.rig.run('ls "/nowhere-$T"', Use("ws:tok", "env", "T"))
        self.assertNotEqual(done.exit_code, 0)
        self.assertIn("[secret:ws:tok]", done.stderr)
        self.assertNotIn(TOKEN.decode(), done.stderr)

    def test_a_stream_is_cut_at_thirty_thousand_characters(self):
        done = self.rig.run("printf '%40000s' x")
        self.assertEqual(len(done.stdout), 30000)


class AnSshKeyGoesThroughAPrivateAgent(unittest.TestCase):
    def test_it_is_loaded_through_stdin_and_agent_and_directory_are_gone_after(self):
        rig = Rig(self, ssh=True)
        key = b"-----BEGIN FAKE KEY-----\nabc\n-----END FAKE KEY-----\n"
        rig.secret("ws:key", key, modes=("ssh",))
        done = rig.run(
            'echo "$SSH_AUTH_SOCK"; cat "$SSH_AUTH_SOCK.pid"; echo; cat "$SSH_AUTH_SOCK.key"',
            Use("ws:key", "ssh"),
        )
        sock, pid, size = done.stdout.split()
        self.assertEqual(int(size), len(key))
        self.assertTrue(sock.startswith(str(rig.xdg)))
        self.assertFalse(Path(sock).parent.exists())
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pid), 0)
        self.assertEqual(list(rig.xdg.iterdir()), [])


class NothingRunsWhileASecretIsRefused(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(self)
        self.rig.secret("ws:tok", TOKEN, modes=("env",))
        self.rig.store.create("global:cloud", "")
        self.rig.store.put("global:cloud", "", b"cloud-value-123")

    def refused(self, command, *uses, **more):
        with mock.patch("coscc.vault.runner._execute") as executed:
            done = self.rig.run(command, *uses, **more)
        executed.assert_not_called()
        return done

    def test_one_refused_secret_stops_the_whole_call_and_each_refusal_says_why(self):
        done = self.refused(
            "echo hi",
            Use("ws:tok", "env", "T"),
            Use("global:cloud", "env", "C"),
            Use("ws:nope", "env", "N"),
        )
        self.assertEqual(
            (done.exit_code, done.stdout, done.stderr, done.refused), (None, "", "", "")
        )
        codes = {name: code for name, code, _ in done.refusals}
        self.assertEqual(codes, {"global:cloud": "not-granted", "ws:nope": "unknown-secret"})
        for name, _, sentence in done.refusals:
            self.assertIn(name, sentence)

    def test_the_stage_and_the_way_of_passing_are_each_a_refusal(self):
        by_stage = self.refused("echo hi", Use("ws:tok", "env", "T"), stage="spike")
        self.assertEqual(by_stage.refusals[0][:2], ("ws:tok", "stage-not-allowed"))
        self.assertIn("spike", by_stage.refusals[0][2])
        by_mode = self.refused("echo hi", Use("ws:tok", "file", "T"))
        self.assertEqual(by_mode.refusals[0][:2], ("ws:tok", "mode-not-allowed"))
        self.rig.store.create("global:jump", "", broker=True)
        self.rig.store.put("global:jump", "", b"broker-key-1")
        self.rig.store.grant("global:jump", WS)
        by_broker = self.refused("echo hi", Use("global:jump", "env", "J"))
        self.assertEqual(by_broker.refusals[0][:2], ("global:jump", "broker-ssh-only"))

    def test_a_refused_command_with_a_placeholder_never_has_the_value_put_in(self):
        self.rig.store.set_policy("ws:tok", WS, ("impl",), ("env", "placeholder"))
        with (
            mock.patch("coscc.vault.runner._execute") as executed,
            mock.patch.object(self.rig.store, "open") as opened,
            mock.patch.object(self.rig.store, "values_for") as values,
        ):
            done = self.rig.run("rm -rf /etc/x {{secret:ws:tok}}", Use("ws:tok", "placeholder"))
        executed.assert_not_called()
        opened.assert_not_called()
        values.assert_not_called()
        self.assertIn("rm", done.refused)
        self.assertNotIn(TOKEN.decode(), self.rig.everything_logged())

    def test_a_value_that_would_change_which_programs_the_line_runs_is_refused_unrun(self):
        self.rig.secret("ws:evil", b"x' ; wc #", modes=("placeholder",))
        done = self.refused("echo '{{secret:ws:evil}}'")
        self.assertIn("would change which programs", done.refused)
        self.assertNotIn("wc #", self.rig.everything_logged())

    def test_a_vault_line_naming_a_secret_is_refused(self):
        for path in (self.rig.store.dir / "abc.age", self.rig.store.identity):
            with self.subTest(path=path.name):
                done = self.refused(f"cat {path}")
                self.assertIn("secrets", done.refused)

    def test_a_line_reaching_the_host_or_removing_outside_is_refused(self):
        for line in ("git push origin main", "gh pr merge 7", "rm -rf /etc/x"):
            with self.subTest(line=line):
                self.assertTrue(self.refused(line, Use("ws:tok", "env", "T")).refused)

    def test_a_line_that_substitutes_or_cannot_be_read_is_refused(self):
        for line in ("echo $(id)", "echo `id`", "cat <(ls)", "echo 'open"):
            with self.subTest(line=line):
                done = self.refused(line, Use("ws:tok", "env", "T"))
                self.assertTrue(done.refused)
                self.assertEqual(done.refusals, ())

    def test_a_program_no_list_names_is_not_refused_for_its_name(self):
        with mock.patch("coscc.vault.runner._execute") as executed:
            executed.return_value = (0, b"", b"")
            done = self.rig.run("curl --version", Use("ws:tok", "env", "T"))
        self.assertEqual(done.refused, "")

    def test_a_variable_name_that_is_not_one_is_refused(self):
        done = self.refused("echo hi", Use("ws:tok", "env", "1 BAD"))
        self.assertIn("not an environment variable name", done.refused)

    def test_a_capture_that_would_overwrite_or_is_no_ws_name_is_refused_before_anything_runs(self):
        for target in ("ws:tok", "global:cloud", "nonsense"):
            with self.subTest(target=target), mock.patch("coscc.vault.runner._execute") as executed:
                with self.assertRaises(BadSecret):
                    self.rig.run("echo hi", capture=target)
                executed.assert_not_called()


class ATimeoutStillCleansUp(unittest.TestCase):
    def test_the_command_is_killed_and_the_tmpfs_directory_is_gone(self):
        rig = Rig(self)
        rig.secret("ws:tok", TOKEN, modes=("file",))
        done = rig.run('echo "$F"; sleep 30', Use("ws:tok", "file", "F"), timeout=1)
        self.assertIsNone(done.exit_code)
        self.assertIn("timed out after 1 s", done.stderr)
        path = Path(done.stdout.strip())
        self.assertTrue(str(path).startswith(str(rig.xdg)))
        self.assertFalse(path.exists())
        self.assertEqual(list(rig.xdg.iterdir()), [])
        (line,) = rig.lines()
        self.assertIsNone(line["exit_code"])


class CaptureKeepsTheOutputAndNeverReturnsIt(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(self)

    def test_stdout_is_stored_minus_one_newline_and_not_returned(self):
        done = self.rig.run("echo made-up-value-42", capture="ws:made")
        self.assertEqual(
            (done.exit_code, done.stdout, done.captured), (0, "", len("made-up-value-42"))
        )
        self.assertEqual(self.rig.store.open("ws:made", WS), b"made-up-value-42")
        self.assertEqual(self.rig.store.get("ws:made", WS).created_by, "agent:impl")
        again = self.rig.run("echo made-up-value-42")
        self.assertEqual(again.stdout, "[secret:ws:made]\n")

    def test_the_secret_it_makes_is_one_create_line_before_the_use(self):
        self.rig.run("echo made-up-value-42", capture="ws:made")
        self.assertEqual(
            [(r["action"], r["name"], r["actor"], r.get("via")) for r in self.rig.lines()],
            [("create", "ws:made", "agent:impl", "capture"), ("use", "", "agent:impl", None)],
        )
        self.assertNotIn("made-up-value-42", self.rig.everything_logged())

    def test_what_it_keeps_is_masked_in_stderr_too(self):
        done = self.rig.run("echo made-up-value-42 | tee /dev/stderr", capture="ws:made")
        self.assertEqual(done.captured, len("made-up-value-42"))
        self.assertNotIn("made-up-value-42", done.stderr)
        self.assertIn("[secret:ws:made]", done.stderr)
        self.assertEqual(done.masked, {"ws:made": 2})

    def test_a_failed_or_empty_command_stores_nothing(self):
        failed = self.rig.run("echo abc; false", capture="ws:a")
        empty = self.rig.run("printf ''", capture="ws:b")
        self.assertEqual((failed.exit_code, failed.captured, empty.captured), (1, 0, 0))
        self.assertIn("nothing was kept", empty.stderr)
        self.assertEqual([s.name for s in self.rig.store.all()], [])
        self.assertEqual([r["action"] for r in self.rig.lines()], ["use", "use"])

    def test_too_much_output_is_not_stored(self):
        done = self.rig.run("printf '%70000s' x", capture="ws:big")
        self.assertEqual((done.exit_code, done.captured, done.stdout), (0, 0, ""))
        self.assertIsNone(self.rig.store.get("ws:big", WS))


class EveryCallLeavesOneLineWithNoValueInIt(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(self)
        self.rig.secret("ws:tok", TOKEN, modes=("env", "placeholder"))

    def test_a_use_is_one_line_naming_the_secret_the_modes_and_the_programs(self):
        self.rig.run('printf %s "$T" | base64', Use("ws:tok", "env", "T"))
        (line,) = self.rig.lines()
        self.assertEqual(
            {k: line[k] for k in ("action", "name", "names", "workspace", "unit", "stage", "run")},
            {
                "action": "use",
                "name": "ws:tok",
                "names": ["ws:tok"],
                "workspace": WS,
                "unit": "0001_demo",
                "stage": "impl",
                "run": "run-1",
            },
        )
        self.assertEqual(
            (line["actor"], line["modes"], line["programs"]),
            ("agent:impl", ["env"], ["printf", "base64"]),
        )
        self.assertEqual((line["exit_code"], line["codes"], line["masked"]), (0, [], {"ws:tok": 1}))

    def test_a_refused_use_is_a_line_too_with_its_codes(self):
        self.rig.run(
            "echo hi", Use("ws:tok", "env", "T"), Use("ws:nope", "env", "N"), stage="spike"
        )
        (line,) = self.rig.lines()
        self.assertEqual(line["codes"], ["stage-not-allowed", "unknown-secret"])
        self.assertEqual(line["stage"], "spike")

    def test_a_refused_command_is_a_line_with_the_words_of_the_check(self):
        self.rig.run("git push origin main", Use("ws:tok", "env", "T"))
        (line,) = self.rig.lines()
        self.assertIn("push", line["refused"])

    def test_the_actor_can_be_named(self):
        self.rig.run("echo hi", actor="agent:custom")
        self.assertEqual(self.rig.lines()[0]["actor"], "agent:custom")

    def test_no_line_of_the_log_holds_the_value_or_the_line_that_had_it_put_in(self):
        self.rig.run("echo {{secret:ws:tok}} | wc -c", Use("ws:tok", "placeholder"))
        self.rig.run('echo "$T"', Use("ws:tok", "env", "T"))
        everything = self.rig.everything_logged()
        self.assertNotIn(TOKEN.decode(), everything)
        self.assertIn("ws:tok", everything)

    def test_a_log_that_cannot_be_written_does_not_stop_the_call(self):
        from coscc.store.db import Busy

        journal = mock.Mock(spec=Journal)
        journal.append.side_effect = Busy("held")
        record(journal, "use", "ws:tok", WS, "agent:impl")
        record(None, "use", "ws:tok", WS, "agent:impl")
        journal.append.assert_called_once()


if __name__ == "__main__":
    unittest.main()
