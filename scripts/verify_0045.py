"""Proof for `0045_a-unit-cannot-be-paused-or-dropped-from-the-board`, claims A–G.

**No session, no quota, no network.** A temporary data root, a bare-directory remote, and a
fake `gh` first on `PATH` that logs every call and answers `pr list` (PR #7 open on the
unit's branch), `pr close` and `pr checks`. Every move goes through `POST /api/units/hold`
on the ASGI app, in-process. Each claim prints `PASS` or `FAIL`.

It does not measure the intent's outcome on the real `0032`, which lives in the app's store
and not in this repository (`spec.md` C9): after shipping, a person pauses `0032` from the
board and records what `cos.mjs next 0032_…` printed and the `hold` row on *Activity*.

Exit 0 every claim passed; 1 one failed; 2 `node`, `uv` or `git` is missing.
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

SLUG = "proof-of-hold"
BRANCH = f"feat/{SLUG}"
PR = 7
STAGES = ("idea", "intent", "spec", "plan", "impl", "pr", "review", "ship")

FAKE_GH = r'''#!{python}
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]
with open(os.path.join(here, "calls.log"), "a") as log:
    log.write(" ".join(args) + "\n")
if args[:2] == ["pr", "list"]:
    print(json.dumps([{{"number": 3, "headRefOid": "b" * 40, "headRefName": "feat/somebody-else", "mergeable": "MERGEABLE"}},
                      {{"number": {pr}, "headRefOid": "a" * 40, "headRefName": "{branch}", "mergeable": "MERGEABLE"}}]))
elif args[:2] == ["pr", "close"]:
    pass
elif args[:2] == ["pr", "checks"]:
    print(json.dumps([{{"name": "tests", "bucket": "pass"}}]))
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


def cos(units_root: Path, *args: str) -> tuple[int, str, str]:
    from coscc import harness

    done = subprocess.run(
        ["node", str(harness.script()), "--root", str(units_root), *args],
        capture_output=True, text=True, timeout=60, env=harness.child_env(),
    )
    return done.returncode, done.stdout, done.stderr


async def run(root: Path, fakebin: Path) -> None:
    import httpx

    from coscc import worktrees
    from coscc.api import build
    from coscc.config import Config

    remote = root / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = root / "seed"
    subprocess.run(["git", "clone", "-q", str(remote), str(seed)], check=True, capture_output=True)
    (seed / "f.txt").write_text("one\n", encoding="utf-8")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "seed")
    git(seed, "push", "-q", "origin", "main", f"main:{BRANCH}")
    workspace = root / "work" / "proj"
    subprocess.run(["git", "clone", "-q", str(remote), str(workspace)], check=True, capture_output=True)
    git(workspace, "branch", BRANCH, f"origin/{BRANCH}")

    cwd = str(workspace)
    config = Config(workspaces=(cwd,), working_dir=str(root / "work"), data_dir=str(root / "data"))
    app = build(config)
    service = app.state.service
    made = await service.create_unit(cwd, SLUG, "verify_0045 fixture")
    unit, directory = made["unit"], Path(made["path"])
    other = (await service.create_unit(cwd, "left-alone", "verify_0045 bystander"))["unit"]
    (Path(made["path"]).parent / other / "intent.md").write_text(
        "# X: bystander\nAuthor: verify_0045. Type: fix. Status: accepted.\n", encoding="utf-8")
    units_root = service._units_root(cwd)
    for name in ("intent.md", "spec.md", "plan.md", "impl.md"):
        extra = " Type: feat." if name == "intent.md" else ""
        (directory / name).write_text(f"# X: fixture\nAuthor: verify_0045.{extra} Status: accepted.\n", encoding="utf-8")
    (directory / "pr.md").write_text(
        f"# PR: fixture\nPR: https://github.com/o/r/pull/{PR}. Status: accepted.\n", encoding="utf-8")
    tree = Path((await service._worktree(cwd, unit))["path"])
    calls = fakebin / "calls.log"
    key = service._journal_key(cwd)

    async def tree_now():
        return await worktrees.find(cwd, unit, config.data_dir)

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proof")

    async def hold(to: str, reason: str) -> httpx.Response:
        return await client.post("/api/units/hold", json={"cwd": cwd, "unit": unit, "to": to, "reason": reason, "by": "verify_0045"})

    def next_json(*extra: str) -> dict:
        return json.loads(cos(units_root, "next", unit, *extra)[1])

    # A
    calls.write_text("", encoding="utf-8")
    r = await hold("paused", "chờ 0034 — proof")
    plain, with_repo = next_json(), next_json("--repo", str(tree))
    claim(
        "A. after a pause, next offers no stage, with and without --repo, and gh is not called",
        r.status_code == 200 and plain["stage"] == "" and with_repo["stage"] == ""
        and plain["action"].startswith("paused — chờ 0034 — proof") and calls.read_text() == "",
        f"{r.status_code} {r.text} / {plain} / {with_repo} / gh: {calls.read_text()!r}",
    )

    # B
    status = json.loads(cos(units_root, "status", "--json")[1])
    by_name = {u["name"]: u for u in status["units"]}
    board = await service.board(cwd)
    on_board = next(u for u in board["units"] if u["name"] == unit)
    claim(
        "B. status --json and the board carry hold.state paused; the bystander carries none",
        (by_name[unit].get("hold") or {}).get("state") == "paused"
        and by_name[other].get("hold") is None
        and [u["name"] for u in status["units"] if u.get("hold")] == [unit]
        and (on_board.get("hold") or {}).get("state") == "paused",
        f"{by_name[unit].get('hold')} / {by_name[other].get('hold')} / {on_board.get('hold')}",
    )

    # C
    rows = service._journal().records(key, kind="hold")
    claim(
        "C. the run log has one hold row with the reason",
        len(rows) == 1 and rows[0]["reason"] == "chờ 0034 — proof" and (rows[0]["from"], rows[0]["to"]) == ("active", "paused"),
        str(rows),
    )

    # D
    gates = {s: cos(units_root, "gate", unit, s, "--repo", str(tree)) for s in STAGES}
    claim(
        "D. every stage's gate exits 1 and says the unit is paused",
        all(code == 1 and "the unit is paused: chờ 0034 — proof" in err for code, _, err in gates.values()),
        str({s: (c, e.strip()[-80:]) for s, (c, _, e) in gates.items()}),
    )

    # E
    calls.write_text("", encoding="utf-8")
    r = await hold("dropped", "không chứng minh được giá trị")
    logged = calls.read_text().splitlines()
    gone = await tree_now() is None
    kept = git(workspace, "branch", "--list", BRANCH).strip("* +") == BRANCH
    nxt = await service.next_step(cwd, unit)
    await service.board(cwd)
    still_gone = await tree_now() is None
    claim(
        "E. a drop closes #7 only, without --delete-branch, removes the worktree, keeps the branch, "
        "and no read reopens the tree",
        r.status_code == 200 and f"pr close {PR}" in logged and not any("pr close 3" in l for l in logged)
        and not any("--delete-branch" in l for l in logged) and gone and kept and still_gone and nxt["stage"] == "",
        f"{r.status_code} {r.text} / gh: {logged} / gone={gone} kept={kept} still_gone={still_gone} next={nxt}",
    )

    # F
    before = (directory / "intent.md").read_bytes()
    r = await hold("active", "thẳng về")
    claim(
        "F. dropped → active is refused with 400 and intent.md does not change a byte",
        r.status_code == 400 and (directory / "intent.md").read_bytes() == before,
        f"{r.status_code} {r.text}",
    )

    # G
    back = await hold("paused", "xem lại")
    on = await hold("active", "làm tiếp")
    offered = next_json("--repo", cwd)
    claim(
        "G. dropped → paused → active is allowed, and next offers a stage again",
        back.status_code == 200 and on.status_code == 200 and offered["stage"] == "review" and "hold" not in offered,
        f"{back.status_code} {back.text} / {on.status_code} {on.text} / {offered}",
    )
    await client.aclose()


def main() -> int:
    for tool in ("node", "uv", "git"):
        if shutil.which(tool) is None:
            print(f"no {tool} on PATH — this proof cannot answer without it")
            return 2
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        fakebin = root / "bin"
        fakebin.mkdir()
        gh = fakebin / "gh"
        gh.write_text(FAKE_GH.format(python=sys.executable, pr=PR, branch=BRANCH), encoding="utf-8")
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
