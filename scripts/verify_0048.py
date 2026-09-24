#!/usr/bin/env python3
"""Proof for the store's `0048_two-steps-starting-together-race-on-git-fetch` (R9, R10).

Twenty rounds. Each pushes a new commit to the remote, then starts `spec` for two units of
one workspace together (`asyncio.gather` over `Service.run_step`), and reads back the two
`start` records the run log holds. A record passes when its `base.sha` is the tip that
round pushed and `base.fresh` is true.

    0  `--baseline` lost at least 1/40 records to a ref-lock race, and the coordinated
       run lost 0/40
    1  the coordinated run lost at least one record
    2  the environment could not answer: no `node`, `uv` or `git`, or `--baseline`
       reproduced no race — then the coordinated 0/40 proves nothing (`spec.md` C4)

`--baseline` alone runs only the uncoordinated scenario and prints its count.

**No session, no quota, no network.** The session is a stand-in that replies with an
accepted `spec.md`; `gh` is a script that always exits 1; `origin` is a bare repository in
the same temporary directory, which is both `COS_DATA_DIR` and `COS_WORKING_DIR` before
`coscc` is imported, so `~/.cos` is never opened.

Between rounds the coordinator's clock moves 30s: each round stands for a separate press,
so a round must not reuse the fetch of the round before (`intent.md ## Answers, câu 1`).

**What this does not prove**: the intent's own test — two steps pressed together twenty
times on the board, against a real GitHub remote — which needs a network and a person.
A bare directory on the same disk answers far faster than GitHub, so how often the race
happens here says nothing about how often it happens there.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.proof_harness import EXIT_BROKEN, EXIT_ENV, EXIT_PASS, say  # noqa: E402

FAKE_GH = "#!/bin/sh\necho 'the fake gh answers nothing' >&2\nexit 1\n"
ROUNDS = 20
# Seconds. `intent.md ## Answers, câu 1`: "together" is under `FETCH_TIMEOUT` apart.
TOGETHER = 20.0


def require_environment() -> None:
    for tool in ("node", "uv", "git"):
        if shutil.which(tool) is None:
            print(f"no {tool} on PATH — this proof cannot answer without it")
            raise SystemExit(EXIT_ENV)


def git(cwd: Path | str, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def commit(cwd: Path | str, name: str, body: str, message: str) -> None:
    Path(cwd, name).write_text(body, encoding="utf-8")
    git(cwd, "add", name)
    git(cwd, "commit", "-q", "-m", message)


class StandIn:
    """`Sessions.stream`'s shape: replies with an accepted `spec.md`, spends nothing."""

    async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
        yield ("chunk", "# Spec: verify_0048\nAuthor: verify_0048. Status: accepted.\n")
        yield ("done", {"session_id": "sess-0048", "text": "",
                        "cost": {"turns": 1, "cost_usd": 0.0}, "terminal_reason": "success"})


class Uncoordinated:
    """`--baseline`: straight through `gitops.fetch` — no join, no reuse, no retry."""

    async def fetch(self, path, remote="origin", branch="main"):
        from coscc import fetches, gitops

        started = time.monotonic()
        try:
            await gitops.fetch(Path(path), remote, branch)
        except gitops.GitError as e:
            raise fetches.FetchFailed(str(e), 1) from e
        return {"outcome": "fetched", "attempts": 1, "age": round(time.monotonic() - started, 1)}


async def scenario(root: Path, coordinated: bool) -> tuple[int, int, list[str]]:
    """Twenty rounds. Returns (records read, records lost to a race, other failures)."""
    from coscc import fetches
    from coscc.api import build
    from coscc.config import Config
    from coscc.journal import Journal

    remote = root / "remote.git"
    git(root, "init", "-q", "--bare", "-b", "main", str(remote))
    workspace = root / "work" / "proj"
    workspace.mkdir(parents=True)
    git(workspace, "init", "-q", "-b", "main")
    commit(workspace, "README.md", "proof\n", "the first commit")
    git(workspace, "remote", "add", "origin", str(remote))
    git(workspace, "push", "-q", "-u", "origin", "main")
    pusher = root / "pusher"
    git(root, "clone", "-q", str(remote), str(pusher))

    cwd = str(workspace)
    app = build(Config(workspaces=(cwd,), working_dir=str(root / "work"),
                       data_dir=str(root / "data")))
    service = app.state.service
    service.sessions = StandIn()
    journal = Journal(str(root / "work"), str(root / "data"))
    key = service._journal_key(cwd)

    made = []
    for n in range(2 * ROUNDS):
        slug = f"proof-unit-{n + 1:02d}"
        unit = await service.create_unit(cwd, slug, "verify_0048 fixture")
        Path(unit["path"], "intent.md").write_text(
            f"# Intent: {slug}\nAuthor: verify_0048. Type: fix. Status: accepted.\n",
            encoding="utf-8",
        )
        made.append(unit["unit"])

    offset = [0.0]
    coordinator = fetches.Fetches(clock=lambda: time.monotonic() + offset[0])
    shared = coordinator if coordinated else Uncoordinated()

    async def drain(unit: str) -> None:
        async for _ in service.run_step(cwd, unit, "spec"):
            pass

    read = lost = 0
    other: list[str] = []
    tally: dict[str, int] = {}
    with mock.patch.object(fetches, "shared", shared):
        for r in range(ROUNDS):
            commit(pusher, f"round-{r + 1}.txt", f"{r}\n", f"round {r + 1}")
            git(pusher, "push", "-q", "origin", "main")
            tip = git(pusher, "rev-parse", "HEAD")
            offset[0] += fetches.REUSE_SECONDS
            pair = made[2 * r: 2 * r + 2]
            results = await asyncio.gather(*(drain(u) for u in pair), return_exceptions=True)
            for unit, result in zip(pair, results):
                if isinstance(result, BaseException):
                    other.append(f"round {r + 1} {unit}: run_step raised {result!r}")
            starts = []
            for unit in pair:
                recs = [x for x in journal.records(key, unit)
                        if x.get("kind") == "start" and x.get("stage") == "spec"]
                starts.extend(recs[-1:])
            if len(starts) != 2:
                other.append(f"round {r + 1}: {len(starts)} start record(s), not 2")
            else:
                apart = abs((datetime.fromisoformat(starts[0]["at"])
                             - datetime.fromisoformat(starts[1]["at"])).total_seconds())
                if apart >= TOGETHER:
                    other.append(f"round {r + 1}: the two starts are {apart}s apart")
            for rec in starts:
                read += 1
                base = rec.get("base") or {}
                how = base.get("fetch") or {}
                tally[f"{how.get('outcome')}/{how.get('attempts')}"] = (
                    tally.get(f"{how.get('outcome')}/{how.get('attempts')}", 0) + 1
                )
                if base.get("sha") == tip[:7] and base.get("fresh") is True:
                    continue
                if any(m in str(base.get("reason") or "") for m in fetches.RACE_MARKERS):
                    if not lost:
                        print(f"      first race, round {r + 1}: {base.get('reason')}")
                    lost += 1
                else:
                    other.append(f"round {r + 1} {rec.get('unit')}: base={base} tip={tip[:7]}")
    print(f"      base.fetch, outcome/attempts: {dict(sorted(tally.items()))}")
    return read, lost, other


def run_mode(coordinated: bool) -> tuple[int, int, list[str]]:
    name = "coordinated" if coordinated else "baseline"
    with tempfile.TemporaryDirectory(prefix=f"verify-0048-{name}-") as d:
        root = Path(d)
        # Same data root for the whole process would mix the two scenarios' run logs.
        os.environ["COS_DATA_DIR"] = str(root / "data")
        os.environ["COS_WORKING_DIR"] = str(root / "work")
        print(f"{name}: temporary data root {root}")
        return asyncio.run(scenario(root, coordinated))


def main(argv: list[str]) -> int:
    require_environment()
    baseline_only = "--baseline" in argv
    fakebin = Path(tempfile.mkdtemp(prefix="verify-0048-bin-"))
    try:
        gh = fakebin / "gh"
        gh.write_text(FAKE_GH, encoding="utf-8")
        gh.chmod(0o755)
        # Before `coscc` is imported: `coscc/state.py` builds an app from the environment
        # at import, and that app must open a temporary directory, not `~/.cos`.
        os.environ["PATH"] = f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}"
        os.environ["COS_DATA_DIR"] = str(fakebin / "data")
        os.environ["COS_WORKING_DIR"] = str(fakebin / "work")
        for name, value in (("GIT_AUTHOR_NAME", "proof"), ("GIT_COMMITTER_NAME", "proof"),
                            ("GIT_AUTHOR_EMAIL", "proof@example.invalid"),
                            ("GIT_COMMITTER_EMAIL", "proof@example.invalid")):
            os.environ[name] = value
        for name, fallback in (("COS_PORT", "8790"), ("COS_HOST", "127.0.0.1")):
            if not os.environ.get(name, "").strip():
                os.environ[name] = fallback

        b_read, b_lost, b_other = run_mode(coordinated=False)
        print(f"baseline: {b_lost}/{b_read} start records lost to a ref-lock race")
        for line in b_other:
            print(f"      baseline, not a race: {line}")
        if b_lost == 0:
            print("the baseline reproduced no ref-lock race here, so a coordinated 0/40 "
                  "would prove nothing — exit 2 (spec.md C4)")
            return EXIT_ENV
        if baseline_only:
            return EXIT_PASS

        read, lost, other = run_mode(coordinated=True)
        ok = say(read == 2 * ROUNDS and lost == 0 and not other,
                 f"R9 all {2 * ROUNDS} start records carry the round's tip and fresh = true",
                 f"read={read}, lost to a race={lost}, other={other}")
        print(f"coordinated: {lost}/{read} lost to a ref-lock race, {len(other)} other failure(s)")
        return EXIT_PASS if ok else EXIT_BROKEN
    finally:
        shutil.rmtree(fakebin, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
