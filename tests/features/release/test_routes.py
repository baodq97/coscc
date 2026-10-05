"""Both release presses and the three routes, against a bare remote and a fake `gh`.

The fake `gh` is a script on a temporary `PATH`. It keeps its pull requests in a JSON file,
reads heads off the bare remote, squashes a merge for real in a scratch clone and pushes it
to `main`, and logs every call. It does not do what the real `gh pr merge --delete-branch`
does to local branches. The presses are driven through a `Release` on the feature's own `Ctx`;
the routes through an app built with the feature."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from coscc import features
from coscc.config import Config
from coscc.features import release
from coscc.git import fetches
from coscc.github import integrate
from coscc.http import plugin
from coscc.http.app import Core, build
from coscc.kernel import Invalid
from coscc.loop import run

REPO = Path(__file__).resolve().parents[3]
ID = ("-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false")

PYPROJECT = '[project]\nname = "fixture"\nversion = "0.1.0"\nrequires-python = ">=3.11"\ndependencies = []\n'
PACKAGE = '{\n  "name": "fixture",\n  "version": "0.1.0",\n  "private": true\n}\n'
LOCK = (
    '{\n  "name": "fixture",\n  "version": "0.1.0",\n  "lockfileVersion": 3,\n  "requires": true,\n'
    '  "packages": {\n    "": {\n      "name": "fixture",\n      "version": "0.1.0"\n    }\n  }\n}\n'
)

FAKE_GH = r"""#!__PYTHON__
import json, subprocess, sys, tempfile
STATE, REMOTE = __STATE__, __REMOTE__
ID = ["-c", "user.name=gh", "-c", "user.email=gh@example.invalid", "-c", "commit.gpgsign=false"]
args = sys.argv[1:]
with open(STATE + ".log", "a") as log:
    log.write(" ".join(args) + "\n")
state = json.load(open(STATE))
def save(): json.dump(state, open(STATE, "w"))
def opt(name, default=""):
    return args[args.index(name) + 1] if name in args else default
def ref(r):
    p = subprocess.run(["git", "--git-dir", REMOTE, "rev-parse", "--verify", "-q", r], capture_output=True, text=True)
    return p.stdout.strip()
def out(obj, code=0):
    print(json.dumps(obj)); sys.exit(code)
prs = state.setdefault("prs", {})
if args[:2] == ["pr", "list"]:
    if state.get("offline"):
        print("could not resolve api.github.com", file=sys.stderr); sys.exit(1)
    if opt("--state") == "merged":
        out([{"number": int(n), "mergeCommit": {"oid": p["merge"]}, "headRefOid": p["head"]}
             for n, p in prs.items() if p["state"] == "MERGED" and p["branch"] == opt("--head")])
    out([{"number": int(n), "headRefOid": ref("refs/heads/" + p["branch"]), "headRefName": p["branch"],
          "mergeable": "MERGEABLE"} for n, p in prs.items() if p["state"] == "OPEN"])
if args[:2] == ["pr", "create"]:
    n = str(state.setdefault("next", 40)); state["next"] += 1
    prs[n] = {"branch": opt("--head"), "state": "OPEN", "title": opt("--title"), "head": "", "merge": ""}
    save(); print("https://github.com/o/r/pull/" + n); sys.exit(0)
if args[:2] == ["pr", "checks"]:
    bucket = state.get("checks", "pass")
    out([] if bucket == "none" else [{"name": "test", "bucket": bucket}], 8 if bucket == "pending" else 0)
if args[:2] == ["pr", "merge"]:
    p = prs[args[2]]
    head = ref("refs/heads/" + p["branch"])
    if opt("--match-head-commit") != head:
        print("head moved", file=sys.stderr); sys.exit(1)
    with tempfile.TemporaryDirectory() as tmp:
        run = lambda *a: subprocess.run(["git", "-C", tmp, *ID, *a], check=True, capture_output=True)
        subprocess.run(["git", "clone", "-q", REMOTE, tmp], check=True, capture_output=True)
        run("merge", "--squash", "origin/" + p["branch"])
        run("commit", "-q", "-m", p["title"] + " (#" + args[2] + ")")
        run("push", "-q", "origin", "main", ":refs/heads/" + p["branch"])
        p["merge"] = subprocess.run(["git", "-C", tmp, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    p["head"], p["state"] = head, "MERGED"
    save(); sys.exit(0)
if args[:2] == ["pr", "view"]:
    p = prs[args[2]]
    out({"state": p["state"], "mergeCommit": {"oid": p["merge"]} if p["merge"] else None})
if args[:2] == ["release", "view"]:
    if ref("refs/tags/" + args[2]):
        out({"url": "https://github.com/o/r/releases/tag/" + args[2], "isDraft": False})
    print("release not found", file=sys.stderr); sys.exit(1)
if args[:2] == ["run", "list"]:
    tag = opt("--branch")
    out([{"status": "completed", "conclusion": "success", "url": "https://github.com/o/r/actions/runs/1"}]
        if ref("refs/tags/" + tag) else [])
print("fake gh: not understood: " + " ".join(args), file=sys.stderr); sys.exit(2)
"""


def git(where: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(where), *ID, *args], capture_output=True, text=True, check=True
    ).stdout.strip()


class Fixture:
    """A workspace one release behind: `v0.1.0`, then `feat` of unit A (#11), `fix` of
    unit B (#12) and `build(deps)` of no unit (#13). `feats=False` leaves out the `feat`."""

    def __init__(self, base: Path, feats: bool = True, commits: bool = True):
        self.base = base
        self.remote = base / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        self.seed = base / "seed"
        subprocess.run(
            ["git", "clone", "-q", str(self.remote), str(self.seed)],
            check=True,
            capture_output=True,
        )
        (self.seed / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
        (self.seed / "package.json").write_text(PACKAGE, encoding="utf-8")
        (self.seed / "package-lock.json").write_text(LOCK, encoding="utf-8")
        subprocess.run(["uv", "lock", "-q"], cwd=self.seed, check=True, capture_output=True)
        git(self.seed, "add", "-A")
        git(self.seed, "commit", "-q", "-m", "chore: seed")
        git(self.seed, "tag", "v0.1.0")
        git(self.seed, "push", "-q", "origin", "main", "v0.1.0")
        if commits:
            for i, subject in enumerate(
                ("feat: one (#11)", "fix: two (#12)", "build(deps): bump (#13)")
            ):
                if subject.startswith("feat") and not feats:
                    continue
                (self.seed / f"f{i}.txt").write_text(subject, encoding="utf-8")
                git(self.seed, "add", "-A")
                git(self.seed, "commit", "-q", "-m", subject)
            git(self.seed, "push", "-q", "origin", "main")
        self.workspace = base / "work" / "proj"
        subprocess.run(
            ["git", "clone", "-q", str(self.remote), str(self.workspace)],
            check=True,
            capture_output=True,
        )
        git(self.workspace, "config", "user.name", "t")
        git(self.workspace, "config", "user.email", "t@example.invalid")
        git(self.workspace, "config", "commit.gpgsign", "false")
        self.cwd = str(self.workspace)
        self.bin = base / "bin"
        self.bin.mkdir()
        self.state = base / "gh.json"
        self.state.write_text("{}", encoding="utf-8")
        (self.bin / "gh").write_text(
            FAKE_GH.replace("__PYTHON__", sys.executable)
            .replace("__STATE__", repr(str(self.state)))
            .replace("__REMOTE__", repr(str(self.remote))),
            encoding="utf-8",
        )
        (self.bin / "gh").chmod(0o755)
        self.env = {
            "COS_DATA_DIR": str(base / "data"),
            "COS_WORKING_DIR": str(base / "work"),
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
        }

    def core(self) -> Core:
        return Core(self.config(), mock.MagicMock())

    def config(self) -> Config:
        return Config(
            workspaces=(self.cwd,),
            working_dir=str(self.base / "work"),
            data_dir=str(self.base / "data"),
        )

    def units(self, service: Core) -> None:
        asyncio.run(self.make_units(service))

    async def make_units(self, service: Core) -> None:
        for slug, kind, pr in (("one-thing", "feat", 11), ("two-thing", "fix", 12)):
            made = await service.answers.create_unit(self.cwd, slug, "fixture")
            directory = Path(made["path"])
            (directory / "intent.md").write_text(
                f"# Intent: {slug}\nAuthor: t. Type: {kind}. Status: accepted.\n", encoding="utf-8"
            )
            (directory / "pr.md").write_text(
                f"# PR: {slug}\nPR: https://github.com/o/r/pull/{pr}. Status: accepted.\n",
                encoding="utf-8",
            )

    def gh_state(self) -> dict:
        return json.loads(self.state.read_text(encoding="utf-8"))

    def set_checks(self, bucket: str) -> None:
        data = self.gh_state()
        data["checks"] = bucket
        self.state.write_text(json.dumps(data), encoding="utf-8")

    def calls(self) -> list[str]:
        log = Path(str(self.state) + ".log")
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    def push_onto(self, branch: str) -> str:
        """Somebody else's commit on `branch` of the remote; its SHA."""
        git(self.seed, "fetch", "-q", "origin", branch)
        git(self.seed, "switch", "-q", "--detach", "FETCH_HEAD")
        (self.seed / "stranger.txt").write_text("not the app's\n", encoding="utf-8")
        git(self.seed, "add", "-A")
        git(self.seed, "commit", "-q", "-m", "stranger")
        git(self.seed, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
        return git(self.seed, "rev-parse", "HEAD")

    def remote_ref(self, ref: str) -> str:
        p = subprocess.run(
            ["git", "--git-dir", str(self.remote), "rev-parse", "--verify", "-q", ref],
            capture_output=True,
            text=True,
        )
        return p.stdout.strip()


def press(rel: release.Release, phase: str, cwd: str, version: str) -> dict:
    """One press, as the route makes it: an `Invalid` before the first item, else the record."""

    async def go() -> dict:
        run = rel.release_prepare if phase == "prepare" else rel.release_publish
        done: dict = {}
        async for kind, payload in run(cwd, version):
            if kind == "done":
                done = payload["release"]
        return done

    return asyncio.run(go())


@unittest.skipUnless(shutil.which("uv"), "uv is needed")
class ReleasingThroughTheFeature(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = Fixture(Path(self._tmp.name))
        patch = mock.patch.dict(os.environ, self.fx.env)
        patch.start()
        self.addCleanup(patch.stop)
        shared = mock.patch.object(fetches, "shared", fetches.Fetches())
        shared.start()
        self.addCleanup(shared.stop)
        self.core = self.fx.core()
        self.rel = release.Release(plugin.ctx_of(self.core, release.FEATURE))
        self.fx.units(self.core)
        self.key = self.core.ws.key(self.fx.cwd)

    def records(self) -> list[dict]:
        return self.core.ws.journal().records(self.key, kind="release")

    def block(self) -> dict:
        # `fresh`: the answers `gh` gives now, not the ones held from the last call.
        block = asyncio.run(self.rel.view(self.fx.cwd, fresh=True))
        assert block is not None
        return block

    def test_the_board_shows_what_is_unreleased_and_proposes_a_minor(self):
        got = self.block()
        self.assertEqual(
            (got["state"], got["last_tag"], got["proposed"], got["count"]),
            ("ready", "v0.1.0", "0.2.0", 2),
        )
        self.assertEqual(
            sorted((u["type"], u["pr"]) for u in got["units"]), [("feat", 11), ("fix", 12)]
        )
        self.assertEqual([c["subject"] for c in got["unmatched"]], ["build(deps): bump (#13)"])
        self.assertEqual((got["button"], got["enabled"]), ("prepare", True))

    def test_a_workspace_never_released_asks_gh_nothing(self):
        # `gh pr list` ran on every read of a workspace, tag or none. A fixture of
        # its own: the shared one's units sit between `pr` and `ship`, and the integration block
        # asks `gh` for them.
        fx = Fixture(Path(self._tmp.name) / "untagged")
        git(fx.workspace, "tag", "-d", "v0.1.0")
        with mock.patch.dict(os.environ, fx.env):
            core = fx.core()
            got = asyncio.run(release.Release(plugin.ctx_of(core, release.FEATURE)).view(fx.cwd))
        assert got is not None
        self.assertEqual(got["state"], "nothing")
        self.assertEqual(fx.calls(), [])

    def test_refusals_leave_one_record_each_and_change_nothing(self):
        for version, said in (("0.2.0-rc.1", "prerelease"), ("0.1.0", "not greater")):
            with self.subTest(version=version), self.assertRaises(Invalid) as caught:
                press(self.rel, "prepare", self.fx.cwd, version)
            self.assertIn(said, str(caught.exception))
        self.assertEqual([r["outcome"] for r in self.records()], ["refused", "refused"])
        self.assertEqual(self.fx.remote_ref("refs/heads/chore/release-0-1-0"), "")
        self.assertFalse(self.rel._releasing)

    def test_gh_offline_is_named_as_gh_not_as_check_version(self):
        self.fx.state.write_text(json.dumps({"offline": True}), encoding="utf-8")
        with self.assertRaises(Invalid) as caught:
            press(self.rel, "prepare", self.fx.cwd, "0.2.0")
        said = str(caught.exception)
        self.assertTrue(said.startswith("the release could not be read: "), said)
        self.assertIn("could not resolve api.github.com", said)
        self.assertNotIn("check-version", said)
        self.assertEqual([r["outcome"] for r in self.records()], ["refused"])

    def test_prepare_then_publish(self):
        rec = press(self.rel, "prepare", self.fx.cwd, "0.2.0")
        self.assertEqual(rec["outcome"], "opened", rec.get("detail"))
        branch = self.fx.remote_ref("refs/heads/chore/release-0-2-0")
        self.assertEqual(branch, rec["head"])
        files = git(self.fx.workspace, "fetch", "-q", "origin", "chore/release-0-2-0") or git(
            self.fx.workspace, "diff", "--name-only", f"{branch}~1", branch
        )
        self.assertEqual(
            sorted(files.splitlines()),
            ["package-lock.json", "package.json", "pyproject.toml", "uv.lock"],
        )
        with self.assertRaises(Invalid) as caught:
            press(self.rel, "prepare", self.fx.cwd, "0.2.0")
        self.assertIn("already open", str(caught.exception))
        self.fx.set_checks("pending")
        self.assertIn("still running", self.block()["disabled_reason"])
        with self.assertRaises(Invalid):
            press(self.rel, "publish", self.fx.cwd, "0.2.0")
        self.fx.set_checks("pass")
        self.assertTrue(self.block()["enabled"])
        rec = press(self.rel, "publish", self.fx.cwd, "0.2.0")
        self.assertEqual(rec["outcome"], "tagged", rec.get("detail"))
        self.assertEqual(self.fx.remote_ref("refs/tags/v0.2.0"), rec["merge_sha"])
        self.assertEqual(self.fx.remote_ref("refs/heads/main"), rec["merge_sha"])
        self.assertEqual(self.block()["state"], "published")
        self.assertEqual(
            [r["outcome"] for r in self.records()], ["opened", "refused", "refused", "tagged"]
        )

    def test_a_refused_push_leaves_no_branch_and_the_next_prepare_runs(self):
        # The local branch stayed at the release commit and blocked every retry.
        hook = self.fx.remote / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho 'no credentials here' >&2\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        rec = press(self.rel, "prepare", self.fx.cwd, "0.2.0")
        self.assertEqual(rec["outcome"], "failed")
        self.assertIn("no credentials here", rec["detail"])
        self.assertEqual(git(self.fx.workspace, "branch", "--list", "chore/release-0-2-0"), "")
        self.assertFalse(self.rel.release_tree_path(self.fx.cwd).exists())
        hook.unlink()
        rec = press(self.rel, "prepare", self.fx.cwd, "0.2.0")
        self.assertEqual(rec["outcome"], "opened", rec.get("detail"))
        self.assertEqual(self.fx.remote_ref("refs/heads/chore/release-0-2-0"), rec["head"])

    def test_a_push_after_the_checks_passed_is_not_merged(self):
        # The merge used to read the pull request again and pin whatever head it found.
        opened = press(self.rel, "prepare", self.fx.cwd, "0.2.0")["head"]
        main = self.fx.remote_ref("refs/heads/main")
        real = integrate.required_checks

        async def then_somebody_pushes(*args, **kwargs):
            got = await real(*args, **kwargs)
            self.fx.push_onto("chore/release-0-2-0")
            return got

        with mock.patch.object(integrate, "required_checks", then_somebody_pushes):
            rec = press(self.rel, "publish", self.fx.cwd, "0.2.0")
        self.assertEqual(rec["outcome"], "failed", rec)
        merges = [c for c in self.fx.calls() if c.startswith("pr merge")]
        self.assertEqual(len(merges), 1)
        self.assertIn(f"--match-head-commit {opened}", merges[0])
        self.assertEqual(self.fx.remote_ref("refs/heads/main"), main)
        self.assertEqual(self.fx.remote_ref("refs/tags/v0.2.0"), "")


def with_release() -> tuple[features.Feature, ...]:
    """The app's features, release among them and on, as in a workspace that turned it on."""
    on = dataclasses.replace(release.FEATURE, default="on")
    return (*(f for f in features.FEATURES if f.name != "release"), on)


@unittest.skipUnless(shutil.which("uv"), "uv is needed")
class OverHttp(unittest.IsolatedAsyncioTestCase):
    """An app built with the fixture's workspace; a subclass says which features it carries."""

    def carried(self) -> tuple[features.Feature, ...]:
        return with_release()

    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.fx = Fixture(Path(tmp.name))
        for patch in (
            mock.patch.dict(os.environ, self.fx.env),
            mock.patch.object(fetches, "shared", fetches.Fetches()),
            mock.patch.object(features, "FEATURES", self.carried()),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.app = build(self.fx.config())
        self.core = self.app.state.core
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )
        self.addAsyncCleanup(self.client.aclose)

    def records(self) -> list[dict]:
        return self.core.ws.journal().records(self.core.ws.key(self.fx.cwd), kind="release")


class ReleasingOverHttp(OverHttp):
    """Over HTTP: a refusal is a 400 before any line of output, with one record."""

    async def test_a_refused_press_is_a_400_and_one_record(self):
        for route in ("/api/release/prepare", "/api/release/publish"):
            with self.subTest(route=route):
                got = await self.client.post(
                    route, json={"cwd": self.fx.cwd, "version": "0.2.0-rc.1"}
                )
                self.assertEqual(got.status_code, 400)
                self.assertIn("prerelease", got.json()["error"])
        self.assertEqual(
            [(r["phase"], r["outcome"]) for r in self.records()],
            [("prepare", "refused"), ("publish", "refused")],
        )

    async def test_bad_bodies_are_400(self):
        for body in ({"cwd": "/etc", "version": "0.2.0"}, [], {}):
            with self.subTest(body=body):
                got = await self.client.post("/api/release/prepare", json=body)
                self.assertEqual(got.status_code, 400)

    async def test_an_unknown_workspace_is_400_on_the_read_too(self):
        got = await self.client.get("/api/release", params={"cwd": "/etc"})
        self.assertEqual(got.status_code, 400)

    async def test_the_read_is_the_block_of_the_workspace(self):
        await self.fx.make_units(self.core)
        got = await self.client.get("/api/release", params={"cwd": self.fx.cwd})
        self.assertEqual(got.status_code, 200)
        block = got.json()
        self.assertEqual(
            (block["state"], block["last_tag"], block["proposed"], block["button"]),
            ("ready", "v0.1.0", "0.2.0", "prepare"),
        )
        self.assertNotIn("origin_sha", block)


class ReleaseOffForAWorkspace(OverHttp):
    """`off` is no panel, no press, no `git` and no record."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        got = await self.client.post(
            "/api/features", json={"cwd": self.fx.cwd, "name": "release", "state": "off"}
        )
        self.assertEqual(got.status_code, 200)

    async def test_the_read_is_null(self):
        got = await self.client.get("/api/release", params={"cwd": self.fx.cwd})
        self.assertEqual((got.status_code, got.json()), (200, None))

    async def test_a_press_is_a_400_before_any_git_or_gh_and_leaves_no_record(self):
        with mock.patch.object(release, "fetch_with_tags", side_effect=AssertionError("git ran")):
            for route in ("/api/release/prepare", "/api/release/publish"):
                with self.subTest(route=route):
                    got = await self.client.post(
                        route, json={"cwd": self.fx.cwd, "version": "0.2.0"}
                    )
                    self.assertEqual(got.status_code, 400)
                    self.assertIn("release is off", got.json()["error"])
        self.assertEqual(self.fx.calls(), [])
        self.assertEqual(self.records(), [])


class ReleaseNotCarried(OverHttp):
    """An app built without the feature has no release routes and no `release` on its board."""

    def carried(self) -> tuple[features.Feature, ...]:
        return tuple(f for f in features.FEATURES if f.name != "release")

    async def test_the_routes_are_not_there(self):
        self.assertEqual(
            (await self.client.get("/api/release", params={"cwd": self.fx.cwd})).status_code, 404
        )
        for route in ("/api/release/prepare", "/api/release/publish"):
            got = await self.client.post(route, json={"cwd": self.fx.cwd, "version": "0.2.0"})
            self.assertIn(got.status_code, (404, 405), route)
        self.assertEqual(self.fx.calls(), [])

    async def test_the_board_has_no_release(self):
        self.assertNotIn("release", await self.core.board(self.fx.cwd))


class ReadingCostsLittle(OverHttp):
    """What a board read and the panel's read ask of `gh` and of the loop."""

    def pr_lists(self) -> int:
        return len([c for c in self.fx.calls() if c.startswith("pr list")])

    async def test_a_board_read_asks_for_no_release_detail_and_the_panel_adds_one_pr_list_at_most(
        self,
    ):
        await self.fx.make_units(self.core)
        tags: list[list[str]] = []
        real = run.ask

        async def counting(args, **kwargs):
            if args[:1] == ["check-tag"]:
                tags.append(list(args))
            return await real(args, **kwargs)

        with mock.patch.object(run, "ask", counting):
            await self.core.board(self.fx.cwd)
            self.assertEqual(tags, [])
            for word in ("pr checks", "release view", "run list"):
                self.assertEqual([c for c in self.fx.calls() if c.startswith(word)], [], word)
            lists = self.pr_lists()
            self.assertLessEqual(lists, 1)
            got = await self.client.get("/api/release", params={"cwd": self.fx.cwd})
        self.assertEqual(got.status_code, 200)
        self.assertEqual(tags, [["check-tag", "v0.1.0"]])
        # The held answer is asked again in the background, once.
        self.assertLessEqual(self.pr_lists() - lists, 1)


class ShuttingDownWithAnAskRunning(unittest.IsolatedAsyncioTestCase):
    async def test_an_ask_of_the_panel_is_cancelled_and_waited_for(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        workspace = Path(tmp.name) / "work" / "proj"
        workspace.mkdir(parents=True)
        core = Core(
            Config(
                workspaces=(str(workspace),),
                working_dir=str(Path(tmp.name) / "work"),
                data_dir=str(Path(tmp.name) / "data"),
            ),
            mock.MagicMock(),
        )
        ctx = plugin.ctx_of(core, release.FEATURE)
        never = asyncio.Event()
        ask = ctx.asks.ask((str(workspace), "status", "v0.1.0"), never.wait)
        await asyncio.wait_for(core.shutdown(), 5)
        self.assertTrue(ask.cancelled())


if __name__ == "__main__":
    unittest.main()
