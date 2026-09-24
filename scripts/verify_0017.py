"""Proof for `0017_units-share-one-working-tree`: two units, two trees, no crossed commits.

    uv run python scripts/verify_0017.py              # no session, no quota, no network
    uv run python scripts/verify_0017.py --this-repo  # prepare a worktree of this checkout, npm test in both
    COS_PROOF_REPO=<throwaway repo> uv run python scripts/verify_0017.py --paid   # real money

Default mode: a temporary data root, a temporary repository whose `origin` is a bare
directory, and the real `Service`. Only the session is a stand-in: it writes a file, runs
`git commit` in the `cwd` it was handed, and writes `impl.md` into the unit's directory,
which is what `Runner.run` checks after an `impl` step (`coscc/runner.py`).

Exit codes: 0 pass, 1 broken, 2 the environment is not ready.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

EXIT_PASS, EXIT_BROKEN, EXIT_ENV = 0, 1, 2


def say(ok: bool, claim: str, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {claim}" + ("" if ok else f": {detail}"))
    return ok


def git(where: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(where), "-c", "user.name=T", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def accepted(kind: str, extra: str = "") -> str:
    return f"# {kind}: proof\nAuthor: verify_0017.{extra} Status: accepted.\n\nbody\n"


def seed(service, cwd: str, units: list[str]) -> None:
    """Give each unit an accepted intent (Type: fix) and an accepted plan, spec skipped."""
    from coscc.units import unit_dir
    for unit in units:
        d = unit_dir(cwd, unit, service.config.data_dir)
        (d / "intent.md").write_text(accepted("Intent", " Type: fix."), encoding="utf-8")
        (d / "spec.md").write_text(
            "# Spec: proof\nAuthor: verify_0017. Status: skipped.\n\nA fixture.\n",
            encoding="utf-8")
        (d / "plan.md").write_text(
            "# Plan: proof\nIntent: intent.md. Spec: skipped (proof fixture). "
            "Author: verify_0017. Status: accepted.\n\nCreate a file named after the unit "
            "and commit it.\n",
            encoding="utf-8",
        )


def temp_repo(base: Path, lock: str | None = None) -> Path:
    remote = base / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    repo = base / "work" / "proj"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("proof\n", encoding="utf-8")
    if lock is not None:
        (repo / "package.json").write_text('{"name":"p","version":"1.0.0"}', encoding="utf-8")
        (repo / "package-lock.json").write_text(lock, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "first")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "origin", "main")
    return repo


class CommittingSession:
    """Stands in for `Sessions`: one commit in `cwd`, then `impl.md` in the unit's directory."""

    def __init__(self, service):
        self.service = service
        self.cwds: list[str] = []
        self.membership = None

    async def stream(self, cwd, text, session_id=None, max_turns=1, workspace=None, **kw):
        from coscc.units import unit_dir
        self.cwds.append(cwd)
        unit = Path(cwd).name
        await asyncio.sleep(0)  # let the other step interleave
        (Path(cwd) / f"{unit}.txt").write_text(unit + "\n", encoding="utf-8")
        git(Path(cwd), "add", "-A")
        git(Path(cwd), "commit", "-q", "-m", f"work of {unit}")
        head = git(Path(cwd), "rev-parse", "HEAD")
        d = unit_dir(workspace or cwd, unit, self.service.config.data_dir)
        (d / "impl.md").write_text(accepted("Impl") + f"\ncommit {head}\n", encoding="utf-8")
        yield ("done", {"session_id": f"s-{unit}", "cost": {}, "terminal_reason": ""})


def make_service(base: Path, repo: Path):
    from coscc.config import Config
    from coscc.service import Service
    config = Config(workspaces=(str(repo),), working_dir=str(base / "work"),
                    data_dir=str(base / "data"))
    service = Service.__new__(Service)
    fake = CommittingSession(service)
    Service.__init__(service, config, fake)
    return service, fake


async def run_impl(service, cwd: str, unit: str) -> dict:
    last = {}
    async for kind, payload in service.run_step(cwd, unit, "impl"):
        if kind == "done":
            last = payload
    return last


def crossed(repo: Path, a: tuple[str, str, str], b: tuple[str, str, str]) -> int:
    """Commits one unit made (its tree's reflog) that sit on the other unit's branch."""
    n = 0
    for (_, tree, _), (_, _, other) in ((a, b), (b, a)):
        mine = set(git(Path(tree), "reflog", "--format=%H", "HEAD").split())
        mine -= set(git(repo, "log", "--format=%H", "main").split())
        theirs = set(git(repo, "log", "--format=%H", f"main..{other}").split())
        n += len(mine & theirs)
    return n


async def default_mode(base: Path) -> bool:
    from coscc import board as board_reader
    from coscc import worktrees
    from coscc.service import Invalid

    ok = True
    repo = temp_repo(base)
    cwd = str(repo)
    service, fake = make_service(base, repo)
    before = (git(repo, "rev-parse", "HEAD"), git(repo, "branch", "--show-current"),
              git(repo, "status", "--porcelain"))

    # R8: one unit exists, two more are created at once.
    first = await service.create_unit(cwd, "already-here", "w")
    both = await asyncio.gather(
        service.create_unit(cwd, "unit-a", "w"), service.create_unit(cwd, "unit-b", "w"))
    numbers = {first["unit"][:4]} | {m["unit"][:4] for m in both}
    ok &= say(len(numbers) == 3, "R8 three units, three different numbers", str(numbers))

    a, b = both[0]["unit"], both[1]["unit"]
    seed(service, cwd, [a, b])
    cut = {}
    for unit in (a, b):
        cut[unit] = await service.start_branch(cwd, unit)
        await service.set_mode(cwd, unit, "impl", "autonomous")

    listed = {Path(t["path"]).resolve(): t["branch"] for t in
              (await __import__("coscc.gitops", fromlist=["x"]).worktree_list(repo))}
    want = {Path(cut[u]["worktree"]).resolve(): cut[u]["branch"] for u in (a, b)}
    ok &= say(all(listed.get(p) == br for p, br in want.items())
              and {cut[a]["branch"], cut[b]["branch"]} == {"fix/unit-a", "fix/unit-b"},
              "R1 git worktree list shows one tree per unit, each on the branch unit-branch named",
              f"listed={listed}, want={want}")

    # R3: record the --repo the gate and next are asked with.
    seen: list[tuple[str, str]] = []
    real_gate, real_next = board_reader.gate, board_reader.next_step

    async def gate(units_root, unit, stage, repo=None, **kw):
        seen.append((unit, str(repo)))
        return await real_gate(units_root, unit, stage, repo=repo, **kw)

    async def next_step(units_root, unit, repo=None, **kw):
        seen.append((unit, str(repo)))
        return await real_next(units_root, unit, repo=repo, **kw)

    board_reader.gate, board_reader.next_step = gate, next_step
    try:
        for unit in (a, b):
            await service.next_step(cwd, unit)
        # R4: both impl steps at once. Neither may be refused.
        results = await asyncio.gather(
            run_impl(service, cwd, a), run_impl(service, cwd, b), return_exceptions=True)
    finally:
        board_reader.gate, board_reader.next_step = real_gate, real_next

    refused = [r for r in results if isinstance(r, BaseException) or r.get("outcome") != "done"]
    ok &= say(not refused, "R4 two impl steps ran together and neither was refused", repr(refused))
    trees = {u: str(Path(cut[u]["worktree"])) for u in (a, b)}
    ok &= say(sorted(fake.cwds) == sorted(trees.values()),
              "R3 each session ran in its own unit's worktree", f"{fake.cwds} vs {trees}")
    ok &= say(bool(seen) and all(r == trees[u] for u, r in seen),
              "R3 gate and next were asked with --repo <that unit's worktree>", str(seen))
    n = crossed(repo, (a, trees[a], cut[a]["branch"]), (b, trees[b], cut[b]["branch"]))
    print(f"crossed: {n}")
    ok &= say(n == 0 and all(git(repo, "log", "--format=%s", f"main..{cut[u]['branch']}")
                              == f"work of {u}" for u in (a, b)),
              "R4 each branch carries exactly its own unit's commit, crossed: 0", str(n))

    after = (git(repo, "rev-parse", "HEAD"), git(repo, "branch", "--show-current"),
             git(repo, "status", "--porcelain"))
    ok &= say(after == before, "R2 the workspace's HEAD, branch and status never changed",
              f"{before} -> {after}")

    # R9: the board shows each unit at the stage cos.mjs names with --repo <its tree>.
    board = await service.board(cwd)
    on_board = {u["name"]: u for u in board["units"]}
    ok &= say(all(on_board[u]["worktree"]["path"] == trees[u] for u in (a, b)),
              "R9 the board carries each unit's worktree", str({u: on_board[u].get("worktree") for u in (a, b)}))

    # R10: a finished unit's tree goes only when gh says merged at the tree's head.
    head = git(Path(trees[a]), "rev-parse", "HEAD")

    async def gh_merged(argv, cwd_, stdin):
        return 0, f'{{"state":"MERGED","headRefOid":"{head}"}}', ""

    async def gh_open(argv, cwd_, stdin):
        return 0, f'{{"state":"OPEN","headRefOid":"{head}"}}', ""

    unit_a = {"name": a, "next": "finished", "pr": {"url": "https://github.com/o/r/pull/1"}}
    kept = await worktrees.remove_if_finished(cwd, a, unit_a, service.config.data_dir, gh=gh_open)
    gone = await worktrees.remove_if_finished(cwd, a, unit_a, service.config.data_dir, gh=gh_merged)
    ok &= say(not kept["removed"] and gone["removed"] and not Path(trees[a]).exists()
              and git(repo, "branch", "--list", cut[a]["branch"]) == "",
              "R10 an open PR keeps the tree; a merged one at the tree's head removes tree and branch",
              f"kept={kept}, gone={gone}")

    # R6: a broken package-lock.json stops impl, naming `npm ci`.
    base6 = base / "r6"
    base6.mkdir()
    repo6 = temp_repo(base6, lock="{ not json")
    service6, _ = make_service(base6, repo6)
    made = await service6.create_unit(str(repo6), "broken-lock", "w")
    seed(service6, str(repo6), [made["unit"]])
    cut6 = await service6.start_branch(str(repo6), made["unit"])
    try:
        await run_impl(service6, str(repo6), made["unit"])
        said = "not refused"
    except Invalid as e:
        said = str(e)
    ok &= say(not cut6["prepare"]["ok"] and "npm ci" in said,
              "R6 impl is refused on a tree whose preparing failed, naming npm ci and its exit code",
              said[:300])
    return ok


def this_repo_mode(base: Path) -> bool:
    """R5 on this repository: a fresh prepared worktree runs `npm test` like the checkout."""
    from coscc import worktrees
    head = git(REPO, "rev-parse", "HEAD")
    tree = base / "tree"
    git(REPO, "worktree", "add", "--detach", str(tree), head)
    try:
        started = time.monotonic()
        prepared = asyncio.run(worktrees.prepare(tree, str(REPO)))
        print(f"prepare: {prepared['commands']} ok={prepared['ok']} "
              f"in {time.monotonic() - started:.0f}s")
        if not prepared["ok"]:
            return say(False, "R5 preparing the worktree", worktrees.describe_failure(prepared))
        # The worktree runs under what a session would read: this process's environment
        # with an app's `COS_HOST`/`COS_PORT` in it, `child_env` laid on top the way
        # `claude_agent_sdk` does. Nothing is patched back in -- review F1 was a proof that
        # repaired `COS_PORT` for itself and so could not see the product break on it.
        from unittest import mock
        from coscc import sessions
        from coscc.config import from_env
        from coscc.data import Data
        parent = {**os.environ, "COS_HOST": "127.0.0.1", "COS_PORT": "8790"}
        # Since `0076` a session's `COS_DATA_DIR` is a directory of its own.
        scratch = base / "session-data"
        scratch.mkdir()
        with mock.patch.dict(os.environ, parent, clear=True):
            env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
            env.update(sessions.child_env(
                str(tree), str(REPO), data_dir=str(scratch),
                app_db=Data(from_env().data_dir).db_path,
            ))
        codes = {}
        for label, where in (("worktree", tree), ("checkout", REPO)):
            e = env if label == "worktree" else {**os.environ, "COS_PORT": "8790"}
            if label == "checkout":
                e.pop("VIRTUAL_ENV", None)
            codes[label] = subprocess.run(["npm", "test"], cwd=str(where), env=e,
                                          capture_output=True, text=True).returncode
        print(f"npm test exit: worktree={codes['worktree']} checkout={codes['checkout']}")
        return say(codes["worktree"] == codes["checkout"],
                   "R5 npm test exits the same in a fresh prepared worktree as in the checkout",
                   str(codes))
    finally:
        subprocess.run(["git", "-C", str(REPO), "worktree", "remove", "--force", str(tree)],
                       capture_output=True)
        try:
            worktrees.prepare_record(tree).unlink()
        except OSError:
            pass


async def paid_mode(base: Path, proof_repo: str) -> bool:
    """The intent's own test, with real sessions: two impl steps at once, 0 crossed."""
    from coscc import frontend
    from coscc.config import Config
    from coscc.service import Service
    from coscc.sessions import Sessions

    work = base / "work"
    work.mkdir()
    repo = work / "proof"
    subprocess.run(["git", "clone", "-q", proof_repo, str(repo)], check=True)
    config = Config(workspaces=(str(repo),), working_dir=str(work), data_dir=str(base / "data"))
    service = Service(config, Sessions(config))
    web = frontend.web_dir(REPO) / "index.html"
    mtime = web.stat().st_mtime if web.exists() else None
    stamp = str(int(time.time()))
    made = [await service.create_unit(str(repo), f"proof-{stamp}-{x}", "verify_0017 --paid")
            for x in ("a", "b")]
    names = [m["unit"] for m in made]
    seed(service, str(repo), names)
    cut = {}
    for unit in names:
        cut[unit] = await service.start_branch(str(repo), unit)
        await service.set_mode(str(repo), unit, "impl", "autonomous")
    results = await asyncio.gather(*(run_impl(service, str(repo), u) for u in names),
                                   return_exceptions=True)
    ok = say(all(not isinstance(r, BaseException) and r.get("outcome") == "done" for r in results),
             "R4 two real impl steps ran together and neither was refused", repr(results)[:500])
    a, b = names
    n = crossed(repo, (a, cut[a]["worktree"], cut[a]["branch"]), (b, cut[b]["worktree"], cut[b]["branch"]))
    print(f"crossed: {n}")
    ok &= say(n == 0, "the intent's test: 0 commits of one unit on the other's branch", str(n))
    ok &= say(all(git(repo, "log", "--format=%H", f"main..{cut[u]['branch']}") for u in names),
              "each stage made at least one commit on its own branch")
    now = web.stat().st_mtime if web.exists() else None
    ok &= say(now == mtime, "R7 the running app's index.html was not rebuilt", f"{mtime} -> {now}")
    return ok


def main() -> int:
    for tool in ("node", "git", "uv"):
        if shutil.which(tool) is None:
            print(f"{tool} is not on PATH")
            return EXIT_ENV
    args = sys.argv[1:]
    with tempfile.TemporaryDirectory(prefix="verify-0017-") as d:
        base = Path(d)
        # Before `coscc` builds anything from the environment.
        os.environ["COS_DATA_DIR"] = str(base / "data")
        if "--paid" in args:
            proof = os.environ.get("COS_PROOF_REPO", "").strip()
            if not proof:
                print("--paid needs COS_PROOF_REPO, a throwaway repository")
                return EXIT_ENV
            ok = asyncio.run(paid_mode(base, proof))
        elif "--this-repo" in args:
            if shutil.which("npm") is None:
                print("npm is not on PATH")
                return EXIT_ENV
            ok = this_repo_mode(base)
        else:
            ok = asyncio.run(default_mode(base))
    return EXIT_PASS if ok else EXIT_BROKEN


if __name__ == "__main__":
    sys.exit(main())
