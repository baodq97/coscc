"""Both release presses through `Service`, against a bare remote and a fake `gh`.

The fake `gh` is a script on a temporary `PATH`. It keeps its pull requests in a JSON file,
reads heads off the bare remote, squashes a merge for real in a scratch clone and pushes it
to `main`, and logs every call. It does not do what the real `gh pr merge --delete-branch`
does to local branches (plan Risk 3). `scripts/verify_0046.py` drives the same `Fixture`."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.config import Config
from coscc.git import fetches
from coscc.github import integrate
from coscc.service import Service
from coscc.kernel import Invalid

REPO = Path(__file__).resolve().parents[2]
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

    def service(self) -> Service:
        config = Config(
            workspaces=(self.cwd,),
            working_dir=str(self.base / "work"),
            data_dir=str(self.base / "data"),
        )
        return Service(config, mock.MagicMock())

    def units(self, service: Service) -> None:
        for slug, kind, pr in (("one-thing", "feat", 11), ("two-thing", "fix", 12)):
            made = asyncio.run(service.answers.create_unit(self.cwd, slug, "fixture"))
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


def press(service: Service, phase: str, cwd: str, version: str) -> dict:
    """One press, as the route makes it: an `Invalid` before the first item, else the record."""

    async def go() -> dict:
        run = (
            service.release.release_prepare
            if phase == "prepare"
            else service.release.release_publish
        )
        done: dict = {}
        async for kind, payload in run(cwd, version):
            if kind == "done":
                done = payload["release"]
        return done

    return asyncio.run(go())


@unittest.skipUnless(shutil.which("uv"), "uv is needed")
class ReleasingThroughTheService(unittest.TestCase):
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
        self.service = self.fx.service()
        self.fx.units(self.service)
        self.key = self.service.ws.key(self.fx.cwd)

    def records(self) -> list[dict]:
        return self.service.ws.journal().records(self.key, kind="release")

    def block(self) -> dict:
        return asyncio.run(self.service.board(self.fx.cwd))["release"]

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
            got = asyncio.run(fx.service().board(fx.cwd))["release"]
        self.assertEqual(got["state"], "nothing")
        self.assertEqual(fx.calls(), [])

    def test_refusals_leave_one_record_each_and_change_nothing(self):
        for version, said in (("0.2.0-rc.1", "prerelease"), ("0.1.0", "not greater")):
            with self.subTest(version=version), self.assertRaises(Invalid) as caught:
                press(self.service, "prepare", self.fx.cwd, version)
            self.assertIn(said, str(caught.exception))
        self.assertEqual([r["outcome"] for r in self.records()], ["refused", "refused"])
        self.assertEqual(self.fx.remote_ref("refs/heads/chore/release-0-1-0"), "")
        self.assertFalse(self.service.release._releasing)

    def test_gh_offline_is_named_as_gh_not_as_check_version(self):
        self.fx.state.write_text(json.dumps({"offline": True}), encoding="utf-8")
        with self.assertRaises(Invalid) as caught:
            press(self.service, "prepare", self.fx.cwd, "0.2.0")
        said = str(caught.exception)
        self.assertTrue(said.startswith("the release could not be read: "), said)
        self.assertIn("could not resolve api.github.com", said)
        self.assertNotIn("check-version", said)
        self.assertEqual([r["outcome"] for r in self.records()], ["refused"])

    def test_prepare_then_publish(self):
        rec = press(self.service, "prepare", self.fx.cwd, "0.2.0")
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
            press(self.service, "prepare", self.fx.cwd, "0.2.0")
        self.assertIn("already open", str(caught.exception))
        self.fx.set_checks("pending")
        self.assertIn("still running", self.block()["disabled_reason"])
        with self.assertRaises(Invalid):
            press(self.service, "publish", self.fx.cwd, "0.2.0")
        self.fx.set_checks("pass")
        self.assertTrue(self.block()["enabled"])
        rec = press(self.service, "publish", self.fx.cwd, "0.2.0")
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
        rec = press(self.service, "prepare", self.fx.cwd, "0.2.0")
        self.assertEqual(rec["outcome"], "failed")
        self.assertIn("no credentials here", rec["detail"])
        self.assertEqual(git(self.fx.workspace, "branch", "--list", "chore/release-0-2-0"), "")
        self.assertFalse(self.service.release.release_tree_path(self.fx.cwd).exists())
        hook.unlink()
        rec = press(self.service, "prepare", self.fx.cwd, "0.2.0")
        self.assertEqual(rec["outcome"], "opened", rec.get("detail"))
        self.assertEqual(self.fx.remote_ref("refs/heads/chore/release-0-2-0"), rec["head"])

    def test_a_push_after_the_checks_passed_is_not_merged(self):
        # The merge used to read the pull request again and pin whatever head it found.
        opened = press(self.service, "prepare", self.fx.cwd, "0.2.0")["head"]
        main = self.fx.remote_ref("refs/heads/main")
        real = integrate.required_checks

        async def then_somebody_pushes(*args, **kwargs):
            got = await real(*args, **kwargs)
            self.fx.push_onto("chore/release-0-2-0")
            return got

        with mock.patch.object(integrate, "required_checks", then_somebody_pushes):
            rec = press(self.service, "publish", self.fx.cwd, "0.2.0")
        self.assertEqual(rec["outcome"], "failed", rec)
        merges = [c for c in self.fx.calls() if c.startswith("pr merge")]
        self.assertEqual(len(merges), 1)
        self.assertIn(f"--match-head-commit {opened}", merges[0])
        self.assertEqual(self.fx.remote_ref("refs/heads/main"), main)
        self.assertEqual(self.fx.remote_ref("refs/tags/v0.2.0"), "")


if __name__ == "__main__":
    unittest.main()
