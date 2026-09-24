"""Proof for `0035_a-parallel-unit-falls-behind-and-nobody-integrates-it`, R14.

**No session, no quota, no network.** A temporary data root, a bare-directory remote, and a
fake `gh` first on `PATH` that answers `pr list`, `pr view`, `pr checks` and
`pr update-branch` — the last by really rebasing the branch in a scratch clone and pushing
it to the bare remote, as GitHub would. Claims R1, R2, R4, R12, R9 and R10 each print
`PASS` or `FAIL`.

Exit 0 every claim passed; 1 one failed; 2 `node`, `uv` or `git` is missing.

`--paid` would run a real Gebo session on a clone of `COS_PROOF_REPO`. It is **not built**:
it exits 2 with that sentence, so nobody reads its absence as a pass.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

SLUG = "proof-of-integration"
PR = 7

FAKE_GH = r'''#!{python}
import json, os, subprocess, sys, tempfile
here = os.path.dirname(os.path.abspath(__file__))
state = json.load(open(os.path.join(here, "state.json")))
args = sys.argv[1:]
with open(os.path.join(here, "calls.log"), "a") as log:
    log.write(" ".join(args) + "\n")
remote, branch = state["remote"], state["branch"]
def head():
    return subprocess.run(["git", "-C", remote, "rev-parse", "refs/heads/" + branch],
                          capture_output=True, text=True).stdout.strip()
if args[:2] == ["pr", "list"]:
    if state.get("fail_list"):
        print("gh: HTTP 502 from the fake", file=sys.stderr); sys.exit(1)
    print(json.dumps([{{"number": {pr}, "headRefOid": head(), "headRefName": branch,
                       "mergeable": state.get("mergeable", "MERGEABLE")}}]))
elif args[:2] == ["pr", "view"]:
    print(json.dumps({{"state": "OPEN", "headRefOid": head(), "mergeable": state.get("mergeable")}}))
elif args[:2] == ["pr", "checks"]:
    print(json.dumps(state.get("checks", [])))
elif args[:2] == ["pr", "update-branch"]:
    if state.get("update_refuses"):
        print("the fake refuses: merge conflict", file=sys.stderr); sys.exit(1)
    if state.get("update_noop"):
        sys.exit(0)
    tmp = tempfile.mkdtemp()
    g = ["git", "-c", "user.name=gh", "-c", "user.email=gh@example.invalid"]
    subprocess.run(g + ["clone", "-q", remote, tmp], check=True)
    subprocess.run(g + ["-C", tmp, "switch", "-q", branch], check=True)
    subprocess.run(g + ["-C", tmp, "rebase", "-q", "origin/main"], check=True)
    subprocess.run(g + ["-C", tmp, "push", "-q", "--force", "origin", branch], check=True)
else:
    print("fake gh: unexpected " + " ".join(args), file=sys.stderr); sys.exit(1)
'''

RESULTS: list[tuple[str, bool, str]] = []


def claim(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'  — ' + detail if detail and not ok else ''}")


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=proof", "-c", "user.email=proof@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout.strip()


def commit(where: Path, name: str, text: str, push: str) -> str:
    (where / name).write_text(text, encoding="utf-8")
    git(where, "add", "-A")
    git(where, "commit", "-q", "-m", f"touch {name}")
    git(where, "push", "-q", "origin", push)
    return git(where, "rev-parse", "HEAD")


def cos(units_root: Path, *args: str) -> tuple[int, str]:
    from coscc import harness

    done = subprocess.run(
        ["node", str(harness.script()), "--root", str(units_root), *args],
        capture_output=True, text=True, timeout=60, env=harness.child_env(),
    )
    return done.returncode, (done.stdout + done.stderr).strip()


async def run(root: Path, fakebin: Path) -> None:
    from coscc import integrate
    from coscc.api import build
    from coscc.config import Config
    from coscc.service import Invalid

    integrate.POLL_DELAY = 0.0  # the fake answers at once; no reason to wait 8s
    remote = root / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = root / "seed"
    subprocess.run(["git", "clone", "-q", str(remote), str(seed)], check=True, capture_output=True)
    commit(seed, "f.txt", "one\n", "main")
    workspace = root / "work" / "proj"
    subprocess.run(["git", "clone", "-q", str(remote), str(workspace)], check=True, capture_output=True)

    cwd = str(workspace)
    app = build(Config(workspaces=(cwd,), working_dir=str(root / "work"), data_dir=str(root / "data")))
    service = app.state.service
    made = await service.create_unit(cwd, SLUG, "verify_0035 fixture")
    unit, directory = made["unit"], Path(made["path"])
    units_root = service._units_root(cwd)
    for name in ("intent.md", "spec.md", "plan.md", "impl.md"):
        extra = " Type: feat." if name == "intent.md" else ""
        (directory / name).write_text(f"# X: fixture\nAuthor: verify_0035.{extra} Status: accepted.\n", encoding="utf-8")
    (directory / "pr.md").write_text(
        f"# PR: fixture\nPR: https://github.com/o/r/pull/{PR}. Status: accepted.\n", encoding="utf-8")
    other = (await service.create_unit(cwd, "outside-the-window", "no pr.md"))["unit"]
    branch = f"feat/{SLUG}"

    git(seed, "switch", "-q", "-c", branch)
    commit(seed, "g.txt", "branch work\n", branch)
    git(seed, "switch", "-q", "main")
    state = {"remote": str(remote), "branch": branch, "mergeable": "MERGEABLE",
             "checks": [{"name": "tests", "bucket": "pass"}]}

    def put(**change) -> None:
        state.update(change)
        for k in [k for k, v in change.items() if v is None]:
            state.pop(k)
        (fakebin / "state.json").write_text(json.dumps(state), encoding="utf-8")

    put()
    git(workspace, "fetch", "-q", "origin")
    git(workspace, "branch", branch, f"origin/{branch}")
    tree = Path((await service._worktree(cwd, unit))["path"])
    key = service._journal_key(cwd)
    journal = service._journal()

    async def integration() -> dict:
        board = await service.board(cwd)
        return next(u for u in board["units"] if u["name"] == unit).get("integration") or {}

    def records(kind: str) -> list[dict]:
        return journal.records(key, kind=kind)

    def refs() -> tuple[str, str]:
        return (git(remote, "rev-parse", f"refs/heads/{branch}"), git(tree, "rev-parse", "HEAD"))

    async def run_integrate(name: str = unit) -> dict:
        done: dict = {}
        async for kind, payload in service.integrate(cwd, name):
            if kind == "done":
                done = payload["integration"]
        return done

    calls = lambda: (fakebin / "calls.log").read_text().splitlines() if (fakebin / "calls.log").exists() else []  # noqa: E731

    # --- R1 --------------------------------------------------------------------
    seen = {}
    seen["current"] = (await integration()).get("state")
    commit(seed, "h.txt", "main moves\n", "main")
    git(workspace, "fetch", "-q", "origin")
    behind = await integration()
    seen["behind"] = behind.get("state")
    put(mergeable="CONFLICTING")
    seen["conflicting"] = (await integration()).get("state")
    put(mergeable="MERGEABLE", fail_list=True)
    unknown = await integration()
    seen["unknown"] = unknown.get("state")
    put(fail_list=None)

    # --- R2 --------------------------------------------------------------------
    before_calls = len([c for c in calls() if "update-branch" in c])
    for _ in range(3):
        await service.board(cwd)
    claim("R2: three board reads start nothing",
          not records("integration") and not records("start")
          and len([c for c in calls() if "update-branch" in c]) == before_calls,
          f"integration={len(records('integration'))} start={len(records('start'))}")

    # --- R12 -------------------------------------------------------------------
    attempts = 0
    refusals = {}
    start_refs = refs()

    async def refused(label: str, name: str = unit) -> None:
        nonlocal attempts
        attempts += 1
        try:
            await run_integrate(name)
            refusals[label] = ""
        except Invalid as e:
            refusals[label] = str(e)

    await refused("not in the window", other)
    mark = service._take(key, unit, "step", "review")
    mark.phase = "running"
    await refused("a review step is running")
    service._release(key, unit, mark)
    (tree / "g.txt").write_text("dirty\n", encoding="utf-8")
    await refused("dirty worktree")
    git(tree, "checkout", "--", "g.txt")
    git(tree, "switch", "-q", "--detach")
    await refused("not on the branch")
    git(tree, "switch", "-q", branch)
    git(tree, "commit", "-q", "--allow-empty", "-m", "local only")
    await refused("local head differs")
    git(tree, "reset", "-q", "--keep", "HEAD~1")
    put(fail_list=True)
    await refused("no button (unknown)")
    put(fail_list=None)
    wants = {
        "not in the window": "not between pr and ship", "a review step is running": "a review step is running",
        "dirty worktree": "uncommitted", "not on the branch": "not on the unit's branch",
        "local head differs": "not the pull request's head", "no button (unknown)": "nothing to integrate",
    }
    bad = {k: v for k, v in refusals.items() if wants[k] not in v}
    claim("R12: each condition refuses with its own reason, and no ref moved",
          not bad and refs() == start_refs and not records("start"),
          f"{bad} refs {start_refs} -> {refs()}")

    # --- R4 --------------------------------------------------------------------
    old_head = refs()[0]
    (directory / "review.md").write_text(
        f"# Review: fixture\nAuthor: verify_0035. Status: accepted.\n\n## Round 1\n\n"
        f"Reviewed: {old_head}. Verdict: pass.\n\n### Findings\n\nnone\n\n### What was not reviewed\n\nnothing\n",
        encoding="utf-8")
    attempts += 1
    pushed = await run_integrate()
    new_remote, new_tree = refs()
    claim("R4: behind is rebased by the app, the tree follows, no session",
          pushed.get("outcome") == "pushed" and new_remote != old_head and new_tree == new_remote
          and pushed.get("mode") == "mechanical" and not records("start"),
          f"{pushed.get('outcome')} {pushed.get('detail')} remote={new_remote[:7]} tree={new_tree[:7]}")

    # --- R10 -------------------------------------------------------------------
    code, said = cos(units_root, "gate", unit, "ship", "--repo", str(tree))
    code_n, said_n = cos(units_root, "next", unit, "--repo", str(tree))
    try:
        offered = json.loads(said_n).get("stage")
    except ValueError:
        offered = None
    claim("R10: after the push the ship gate closes (rewritten) and next offers review",
          code == 1 and "rewritten" in said and offered == "review", f"gate {code}: {said[:160]} | next: {said_n[:160]}")

    put(checks=[{"name": "tests", "bucket": "fail"}])
    seen["red-after-integration"] = (await integration()).get("state")
    put(checks=[{"name": "tests", "bucket": "pass"}])
    claim("R1: five fixtures, five states",
          all(k == v for k, v in seen.items()) and len(seen) == 5 and behind.get("behind") == 1
          and bool(behind.get("origin_sha")) and bool(unknown.get("reason")),
          f"{seen} behind={behind.get('behind')}")

    # --- R9 --------------------------------------------------------------------
    commit(seed, "i.txt", "main moves again\n", "main")
    git(workspace, "fetch", "-q", "origin")
    put(update_refuses=True)
    attempts += 1
    refused_rec = await run_integrate()
    put(update_refuses=None, update_noop=True)
    attempts += 1
    failed_rec = await run_integrate()
    put(update_noop=None)
    rows = records("integration")
    outcomes = {r.get("outcome") for r in rows if r.get("mode") == "mechanical"}
    claim("R9: one record per integration, and every mechanical outcome is recorded",
          len(rows) == attempts and {"pushed", "refused", "failed"} <= outcomes
          and refused_rec.get("outcome") == "refused" and failed_rec.get("outcome") == "failed",
          f"records={len(rows)} attempts={attempts} outcomes={outcomes}")


def main() -> int:
    if "--paid" in sys.argv[1:]:
        print("--paid is not built in this unit: no real Gebo session has been run by this proof")
        return 2
    for tool in ("node", "uv", "git"):
        if shutil.which(tool) is None:
            print(f"no {tool} on PATH — this proof cannot answer without it")
            return 2
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        fakebin = root / "bin"
        fakebin.mkdir()
        gh = fakebin / "gh"
        gh.write_text(FAKE_GH.format(python=sys.executable, pr=PR), encoding="utf-8")
        gh.chmod(0o755)
        os.environ["PATH"] = f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}"
        os.environ["COS_DATA_DIR"] = str(root / "data")
        os.environ["COS_WORKING_DIR"] = str(root / "work")
        asyncio.run(run(root, fakebin))
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)} of {len(RESULTS)} claims passed")
    return 1 if failed or not RESULTS else 0


if __name__ == "__main__":
    sys.exit(main())
