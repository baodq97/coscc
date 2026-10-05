"""`coscc/features/vault/__init__.py`, the agent's side: the tools, the guard and the prompt block.

The store's `age` is a script in a temporary folder that only scrambles bytes, so no test needs
the real one. `Bed` is the fixture `test_vault_http.py` builds on.
"""

from __future__ import annotations

import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc import vault
from coscc.http import plugin
from coscc.agent import policy
from coscc.http.app import build
from coscc.config import Config
from coscc.store.db import Data
from coscc.features import vault as feature
from coscc.kernel import Facts, Runs
from tests.features.ctx import ctx_for

AGE = """#!{python}
import base64, sys
args = sys.argv[1:]
if "-d" in args:
    data = sys.stdin.buffer.read()
    if not data.startswith(b"FAKE-AGE:"):
        sys.exit("not an age file")
    sys.stdout.buffer.write(base64.b64decode(data[9:])[::-1])
else:
    sys.stdout.buffer.write(b"FAKE-AGE:" + base64.b64encode(sys.stdin.buffer.read()[::-1]))
"""
KEYGEN = """#!{python}
import sys
args = sys.argv[1:]
if "-o" in args:
    open(args[args.index("-o") + 1], "w").write("AGE-SECRET-KEY-FAKE\\n")
else:
    print("age1fake")
"""

# Values with characters each of the five forms treats differently.
DB = b"bait/Value+9f3a=&x y"
TOKEN = b'bait "quoted" \xc3\xa9 2'
OTHER = b"other-bait-77"


def git(tree: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=tree,
        check=True,
        capture_output=True,
    )


class Bed(unittest.IsolatedAsyncioTestCase):
    """An app on temporary folders, holding three bait secrets: `ws:db` of the workspace,
    `global:tok` granted to it and `global:other` that is not."""

    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.ws = self.root / "work" / "proj"
        self.ws.mkdir(parents=True)
        self.config = Config(
            workspaces=(str(self.ws),),
            working_dir=str(self.root / "work"),
            data_dir=str(self.root / "data"),
        )
        bins = self.root / "bin"
        bins.mkdir()
        for name, text in (("age", AGE), ("age-keygen", KEYGEN)):
            (bins / name).write_text(text.format(python=sys.executable))
            (bins / name).chmod(bins.stat().st_mode | stat.S_IXUSR)
        self.store = vault.Store(
            data=Data(self.config.data_dir),
            config_home=str(self.root / "cfg"),
            home=str(self.root / "home"),
            age=str(bins / "age"),
            age_keygen=str(bins / "age-keygen"),
        )
        patch = mock.patch.object(feature, "make_store", lambda _ctx: self.store)
        patch.start()
        self.addCleanup(patch.stop)
        self.app = build(self.config)
        self.core = self.app.state.core
        self.ctx = plugin.ctx_of(self.core, feature.FEATURE)
        self.key = plugin.Workspaces.key(str(self.ws))
        self.make("ws:db", DB, self.key, "the database", ("impl",))
        self.make("global:tok", TOKEN, "", "a shared token", ("spike",))
        self.store.grant("global:tok", self.key)
        self.make("global:other", OTHER, "", "not for proj", ("impl",))

    def make(self, name, value, workspace, description, stages=("impl",)):
        self.store.create(name, workspace, description, stages=stages)
        self.store.put(name, workspace, value)

    def values(self) -> dict[str, bytes]:
        """Every bait value there is, whichever workspace sees it."""
        return {s.name: self.store.open(s.name, s.workspace) for s in self.store.all()}

    def assert_clean(self, *sources: tuple[str, bytes | str]):
        """No bait in any of the five forms in any of `sources`."""
        found = [(w, d.encode() if isinstance(d, str) else d) for w, d in sources]
        self.assertEqual(vault.scan(self.values(), found), [])

    def journal(self):
        journal = self.ctx.runs.journal()
        assert journal is not None
        return journal

    def tree(self) -> Path:
        """A repository on `main` with one commit, for a unit's worktree."""
        tree = self.root / "tree"
        tree.mkdir(exist_ok=True)
        git(tree, "init", "-q", "-b", "main")
        (tree / "README").write_text("hello\n")
        git(tree, "add", ".")
        git(tree, "commit", "-q", "-m", "first")
        return tree

    def facts(self, stage="impl", tree: Path | None = None, **over) -> Facts:
        directory = self.root / "unit"
        directory.mkdir(exist_ok=True)
        work = tree or self.root / "tree"
        work.mkdir(exist_ok=True)
        fields = {
            "workspace": str(self.ws),
            "workspace_key": self.key,
            "unit": "0001_thing",
            "stage": stage,
            "run": "r1",
            "tree": str(work),
            "directory": directory,
            "scratch": None,
            "commands": policy.IMPL_COMMANDS,
            "resumed": False,
        }
        return Facts(**{**fields, **over})

    def tools(self, facts: Facts) -> dict:
        found = feature.build_tools(facts, self.ctx, lambda: self.store)
        return {t.name: t for t in found}

    async def call(self, facts: Facts, name: str, args: dict) -> dict:
        got = await self.tools(facts)[name].handler(args)
        self.assertEqual([c["type"] for c in got["content"]], ["text"])
        self.assert_clean(("reply", got["content"][0]["text"]))
        return json.loads(got["content"][0]["text"])


def names_in(schema, found=None) -> set[str]:
    """Every property name anywhere in a JSON schema."""
    found = set() if found is None else found
    if isinstance(schema, dict):
        for k, v in schema.items():
            if k == "properties":
                found |= set(v)
            names_in(v, found)
    elif isinstance(schema, list):
        for v in schema:
            names_in(v, found)
    return found


class TheToolsTakeNoValue(Bed):
    async def test_no_input_schema_has_a_parameter_a_value_could_ride_in(self):
        tools = self.tools(self.facts())
        self.assertEqual(set(tools), set(feature.TOOL_NAMES))
        allowed = {"command", "uses", "name", "mode", "var", "timeout", "capture", "description"}
        for tool in tools.values():
            self.assertLessEqual(names_in(tool.input_schema), allowed, tool.name)
        self.assertEqual(names_in(tools["vault_generate"].input_schema), {"name", "description"})
        self.assertEqual(names_in(tools["vault_list"].input_schema), set())

    def test_the_app_offers_the_tool_to_impl_and_spike_and_to_no_prose_stage(self):
        hooks = self.core.steps.hooks
        for stage in ("impl", "spike"):
            self.assertEqual(
                [t.server for t in hooks.for_step(stage, str(self.ws)).tools], ["vault"]
            )
        for stage in ("plan", "spec", "review", "pr"):
            self.assertEqual(hooks.for_step(stage, str(self.ws)).tools, ())


class ListingSecrets(Bed):
    async def test_it_names_what_the_workspace_sees_and_never_a_value(self):
        got = await self.call(self.facts(), "vault_list", {})
        rows = {r["name"]: r for r in got["secrets"]}
        self.assertEqual(set(rows), {"ws:db", "global:tok"})
        self.assertEqual(rows["ws:db"]["description"], "the database")
        self.assertIs(rows["ws:db"]["usable_now"], True)
        self.assertIs(rows["global:tok"]["usable_now"], False)
        for row in rows.values():
            self.assertFalse({"value", "length", "hash", "size"} & set(row))

    async def test_a_spike_sees_which_it_may_use(self):
        got = await self.call(self.facts("spike"), "vault_list", {})
        usable = {r["name"]: r["usable_now"] for r in got["secrets"]}
        self.assertEqual(usable, {"ws:db": False, "global:tok": True})


class ExecutingACommand(Bed):
    async def test_the_output_is_filtered_and_the_use_is_written_once(self):
        args = {
            "command": 'echo "the value is $DB"',
            "uses": [{"name": "ws:db", "mode": "env", "var": "DB"}],
        }
        got = await self.call(self.facts(), "vault_exec", args)
        self.assertEqual(got["exit_code"], 0)
        self.assertEqual(got["stdout"].strip(), "the value is [secret:ws:db]")
        self.assertEqual(got["masked"], {"ws:db": 1})
        lines = self.journal().records(self.key, "0001_thing", kind="vault")
        self.assertEqual([(r["action"], r["actor"]) for r in lines], [("use", "agent:impl")])
        self.assert_clean(("journal", json.dumps(lines)))

    async def test_a_secret_that_may_not_be_used_stops_the_whole_command(self):
        marker = self.root / "tree" / "ran"
        self.facts()
        args = {
            "command": "mkdir ran",
            "uses": [
                {"name": "ws:db", "mode": "env"},
                {"name": "global:other", "mode": "env"},
                {"name": "ws:nope", "mode": "env"},
                {"name": "global:tok", "mode": "env"},
                {"name": "ws:db", "mode": "ssh"},
            ],
        }
        got = await self.call(self.facts(), "vault_exec", args)
        codes = {r["secret"] + "/" + r["code"] for r in got["refusals"]}
        self.assertEqual(
            codes,
            {
                "global:other/not-granted",
                "ws:nope/unknown-secret",
                "global:tok/stage-not-allowed",
                "ws:db/mode-not-allowed",
            },
        )
        self.assertFalse(marker.exists())

    async def test_a_line_the_grant_refuses_is_command_refused_with_its_words(self):
        for line in ("curl http://example.invalid", f"cat {self.store.dir}/x", "cat ~/x $(id)"):
            got = await self.call(self.facts(), "vault_exec", {"command": line})
            self.assertEqual(got["result"], "command-refused", line)
            self.assertTrue(got["reason"], line)

    async def test_a_command_the_workspace_added_to_the_list_is_run(self):
        facts = self.facts(commands=(*policy.IMPL_COMMANDS, "id"))
        got = await self.call(facts, "vault_exec", {"command": "id -u"})
        self.assertEqual(got["exit_code"], 0)
        got = await self.call(self.facts(), "vault_exec", {"command": "id -u"})
        self.assertEqual(got["result"], "command-refused")

    async def test_a_wrong_argument_is_refused_without_running(self):
        for args in (
            {},
            {"command": "  "},
            {"command": "true", "uses": "ws:db"},
            {"command": "true", "uses": [{"name": "ws:db"}]},
            {"command": "true", "capture": 3},
        ):
            self.assertEqual(
                (await self.call(self.facts(), "vault_exec", args))["result"], "refused", args
            )


class GeneratingASecret(Bed):
    async def test_it_makes_a_ws_secret_nobody_sees_and_writes_one_line(self):
        got = await self.call(
            self.facts(), "vault_generate", {"name": "ws:gen", "description": "d"}
        )
        self.assertEqual(got, {"result": "made", "name": "ws:gen"})
        made = self.store.get("ws:gen", self.key)
        assert made is not None
        self.assertEqual(
            (made.has_value, made.created_by, made.description), (True, "agent:impl", "d")
        )
        self.assertEqual(len(self.store.open("ws:gen", self.key)), 43)
        lines = self.journal().records(self.key, None, kind="vault")
        self.assertEqual([(r["action"], r["name"]) for r in lines], [("create", "ws:gen")])

    async def test_a_taken_name_a_global_name_and_a_bad_name_are_refused(self):
        before = self.store.open("ws:db", self.key)
        for name in ("ws:db", "global:new", "ws:Bad Name"):
            got = await self.call(self.facts(), "vault_generate", {"name": name})
            self.assertEqual(got["result"], "refused", name)
        self.assertEqual(self.store.open("ws:db", self.key), before)
        self.assertIsNone(self.store.get("global:new", ""))


class TheGuardHoldsWhatCarriesAValueOut(Bed):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        # No test has a pull request: `ship` reads this one instead of asking `gh`.
        pr = mock.patch.object(
            vault.sources, "_pull_request", return_value=[("pull-request", b"{}")]
        )
        pr.start()
        self.addCleanup(pr.stop)

    def leak(self, text: bytes):
        self.facts()
        (self.root / "unit" / "pr.md").write_bytes(text)

    def test_pr_ship_and_integrate_are_refused_naming_the_secret_and_never_the_value(self):
        self.tree()
        self.leak(b"## Body\nthe key is " + DB + b"\n")
        for stage in ("pr", "ship", "integrate"):
            words = feature._leaks(self.ctx, lambda: self.store, self.facts(stage))
            self.assertIn("ws:db", words or "", stage)
            self.assertNotIn("global:other", words or "", stage)
            self.assert_clean((stage, words or ""))

    def test_each_hit_is_one_vault_leak_line_with_a_name_a_place_and_a_form_only(self):
        self.tree()
        self.leak(DB)
        feature._leaks(self.ctx, lambda: self.store, self.facts("pr"))
        lines = self.journal().records(self.key, "0001_thing", kind="vault-leak")
        self.assertEqual(
            [(r["name"], r["where"], r["form"], r["stage"]) for r in lines],
            [("ws:db", "artifact:pr.md", "raw", "pr")],
        )
        self.assertEqual(
            set(lines[0]),
            {"v", "at", "kind", "workspace", "unit", "stage", "scan", "name", "where", "form"},
        )
        self.assert_clean(("journal", json.dumps(lines)))

    def test_an_encoded_value_is_found_too(self):
        import base64

        self.tree()
        self.leak(base64.b64encode(b"x" + TOKEN + b"y"))
        words = feature._leaks(self.ctx, lambda: self.store, self.facts("pr"))
        self.assertIn("global:tok", words or "")
        (line,) = self.journal().records(self.key, "0001_thing", kind="vault-leak")
        self.assertEqual((line["name"], line["form"]), ("global:tok", "base64"))

    def test_a_value_committed_on_the_unit_branch_is_found(self):
        tree = self.tree()
        git(tree, "checkout", "-q", "-b", "unit")
        (tree / "config.txt").write_bytes(b"password = " + DB + b"\n")
        git(tree, "add", ".")
        git(tree, "commit", "-q", "-m", "oops")
        words = feature._leaks(self.ctx, lambda: self.store, self.facts("integrate", tree=tree))
        self.assertIn("ws:db", words or "")
        (line,) = self.journal().records(self.key, "0001_thing", kind="vault-leak")
        self.assertEqual(line["where"], "commits")

    def test_a_clean_unit_passes_and_a_second_clean_scan_writes_nothing(self):
        self.tree()
        for stage in ("pr", "ship", "integrate"):
            self.assertIsNone(feature._leaks(self.ctx, lambda: self.store, self.facts(stage)))
        self.assertEqual(self.journal().records(self.key, None, kind="vault-leak"), [])

    def test_every_other_stage_abstains_even_with_a_leak(self):
        self.tree()
        self.leak(DB)
        for stage in ("idea", "intent", "spec", "plan", "impl", "spike", "review", "estimate"):
            self.assertIsNone(
                feature._leaks(self.ctx, lambda: self.store, self.facts(stage)), stage
            )
        self.assertEqual(self.journal().records(self.key, None, kind="vault-leak"), [])

    def test_a_workspace_with_no_secret_it_sees_is_not_scanned(self):
        self.tree()
        self.leak(OTHER)
        self.store.delete("global:tok", "")
        self.store.delete("ws:db", self.key)
        self.assertIsNone(feature._leaks(self.ctx, lambda: self.store, self.facts("pr")))

    def test_the_kernel_refuses_the_step_and_the_integration_through_it(self):
        self.tree()
        self.leak(DB)
        said = self.core.steps.feature_refusal(self.facts("integrate"))
        self.assertTrue(said.startswith("vault-leak: "), said)
        self.assertIn("ws:db", said)
        self.assert_clean(("said", said))

    def test_commits_or_a_pull_request_that_cannot_be_read_hold_the_step(self):
        self.facts()
        words = feature._leaks(self.ctx, lambda: self.store, self.facts("pr"))
        self.assertIn("commits could not be read", words or "")
        self.tree()
        with mock.patch.object(vault.sources, "_pull_request", return_value=[]):
            words = feature._leaks(self.ctx, lambda: self.store, self.facts("ship"))
        self.assertIn("pull-request could not be read", words or "")
        self.assertIsNone(feature._leaks(self.ctx, lambda: self.store, self.facts("ship")))

    def test_a_run_log_that_cannot_be_read_holds_the_step(self):
        ctx = ctx_for(
            runs=Runs(lambda: None, None),
            units=self.ctx.units,
            settings=self.ctx.settings,
            store=self.ctx.store,
        )
        self.assertIn("cannot", feature._leaks(ctx, lambda: self.store, self.facts("pr")) or "")


if __name__ == "__main__":
    unittest.main()
