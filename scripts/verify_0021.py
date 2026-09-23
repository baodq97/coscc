#!/usr/bin/env python3
"""Proof for the store's `0021_review-findings-never-reach-the-pull-request`.

The plan's seven claims, C1-C7: every review round becomes exactly one comment on the pull
request, carrying the whole round under a first line that says an agent wrote it; nothing
ever calls `gh pr review`; a failed post loses no byte of `review.md` and the board says
the round is not on the PR; neither gate changes its answer because of a comment; every
attempt is one run-log row; and the session's grant and guard are untouched.

    0  every claim held
    1  at least one did not
    2  the environment could not answer: no `node`, `uv` or `git`, or no `main` to diff

**No session, no quota, no network.** `gh` is a fake: a Python script put first on `PATH`
that keeps the pull request's comments in a JSON file, logs every argv, answers the two
questions the `review` and `ship` gates ask, and fails the two comment calls while a flag
file exists — only those, so the `review` gate stays open and the step still runs.
A flag file rather than a variable because `coscc/harness.py` `child_env` passes `gh` only
`PATH`, `HOME`, `LC_ALL` and `NO_COLOR` — a variable would never arrive. The review
session is a fake too: it replies with a fixed `review.md`. Everything is written under one
temporary directory, set as both `COS_DATA_DIR` and `COS_WORKING_DIR` before the app is
imported, so `~/.cos` is never opened.

**This does not measure the outcome in `intent.md`.** That is comments on real pull
requests, counted with `gh pr view` — the plan's `## Proof`, part B, after the merge.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.proof_harness import EXIT_BROKEN, EXIT_ENV, EXIT_PASS, REPO, say  # noqa: E402

PR_URL = "https://github.com/example/proof/pull/7"
SLUG = "findings-the-proof-invented"

FIRST_LINE = re.compile(
    r"^\*\*coscc review, round (\d+) of (\S+)\.\*\* Written by an agent session, not a person\. "
    r"This comment is not an approval\.$"
)

FAKE_GH = r'''#!{python}
"""A fake gh for verify_0021. State lives beside this file."""
import json, os, subprocess, sys
here = os.path.dirname(os.path.abspath(__file__))
argv = sys.argv[1:]
with open(os.path.join(here, "argv.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps(argv) + "\n")
talks_comments = argv[:2] == ["pr", "comment"] or (argv[:2] == ["pr", "view"] and "comments" in argv)
if talks_comments and os.path.exists(os.path.join(here, "FAIL")):
    sys.stderr.write("HTTP 502: the fake GitHub is down\n")
    sys.exit(1)
store = os.path.join(here, "comments.json")
comments = json.load(open(store, encoding="utf-8")) if os.path.exists(store) else []
if argv[:2] == ["pr", "checks"]:
    print(json.dumps([{{"name": "test", "bucket": "pass"}}]))
elif argv[:2] == ["pr", "view"] and "comments" in argv:
    print(json.dumps({{"comments": comments}}))
elif argv[:2] == ["pr", "view"]:
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    print(json.dumps({{"state": "OPEN", "headRefOid": head}}))
elif argv[:2] == ["pr", "comment"] and argv[3:5] == ["--body-file", "-"]:
    url = argv[2] + "#issuecomment-" + str(len(comments) + 1)
    comments.append({{"body": sys.stdin.read(), "url": url}})
    json.dump(comments, open(store, "w", encoding="utf-8"))
    print(url)
else:
    sys.stderr.write("the fake gh does not know: " + " ".join(argv) + "\n")
    sys.exit(2)
'''


def rnd(n: int, sha: str, verdict: str, findings: list[str]) -> str:
    return (
        f"## Round {n}\n\nReviewed: {sha}. Verdict: {verdict}.\n\n"
        "### Findings\n\n" + "\n".join(findings) + "\n\n"
        "### What was not reviewed\n\nnothing the proof did not invent\n"
    )


def header(status: str) -> str:
    return f"# Review: findings the proof invented\nAuthor: verify_0021. Status: {status}.\n\n"


def require_environment() -> None:
    for tool in ("node", "uv", "git"):
        if shutil.which(tool) is None:
            print(f"no {tool} on PATH — this proof cannot answer without it")
            raise SystemExit(EXIT_ENV)


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def gates(units_root: Path, unit: str, repo: Path) -> list[tuple[int, str]]:
    from coscc import harness

    out = []
    for stage in ("review", "ship"):
        done = subprocess.run(
            ["node", str(harness.script()), "--root", str(units_root), "gate", unit, stage,
             "--repo", str(repo)],
            capture_output=True, text=True, timeout=60, env=harness.child_env(),
        )
        out.append((done.returncode, (done.stdout + done.stderr).strip()))
    return out


def trunk() -> str | None:
    for ref in ("main", "origin/main"):
        if subprocess.run(["git", "rev-parse", "--verify", "--quiet", ref], cwd=REPO,
                          capture_output=True).returncode == 0:
            return ref
    return None


async def run(root: Path, fakebin: Path) -> bool:
    import httpx

    from coscc import board as board_reader
    from coscc.api import build
    from coscc.config import Config
    from coscc.journal import Journal

    workspace = root / "work" / "proj"
    workspace.mkdir(parents=True)
    git(workspace, "init", "-q", "-b", "main")
    git(workspace, "-c", "user.name=proof", "-c", "user.email=proof@example.invalid",
        "commit", "-q", "--allow-empty", "-m", "the only commit")
    git(workspace, "switch", "-q", "-c", f"feat/{SLUG}")
    sha = git(workspace, "rev-parse", "HEAD")

    cwd = str(workspace)
    app = build(Config(workspaces=(cwd,), working_dir=str(root / "work"), data_dir=str(root / "data")))
    service = app.state.service
    made = service.create_unit(cwd, SLUG, "verify_0021 fixture")
    unit, directory = made["unit"], Path(made["path"])
    units_root = service._units_root(cwd)
    # Fixture data, written before anything is measured, in a unit that lives in a
    # temporary directory and dies with it.
    for name, title in (("intent.md", "Intent"), ("spec.md", "Spec"), ("plan.md", "Plan"),
                        ("impl.md", "Impl")):
        extra = " Type: feat." if name == "intent.md" else ""
        (directory / name).write_text(
            f"# {title}: findings the proof invented\nAuthor: verify_0021.{extra} Status: accepted.\n",
            encoding="utf-8",
        )
    (directory / "pr.md").write_text(
        f"# PR: findings the proof invented\nAuthor: verify_0021. Status: accepted.\nPR: {PR_URL}\n",
        encoding="utf-8",
    )
    findings = {
        1: ["- F1 [open] [high] a policy lets `gh api` through",
            "- F2 [open] [high] a gate reads a remote branch without a fetch"],
        2: [f"- F1 [fixed {sha}] [high] a policy lets `gh api` through",
            "- F2 [open] [high] a gate reads a remote branch without a fetch",
            "- F3 [open] [low] a *typo* in a comment"],
        3: [f"- F1 [fixed {sha}] [high] a policy lets `gh api` through",
            f"- F2 [fixed {sha}] [high] a gate reads a remote branch without a fetch",
            f"- F3 [fixed {sha}] [low] a *typo* in a comment"],
    }
    r1 = rnd(1, sha, "changes-requested", findings[1])
    r2 = rnd(2, sha, "changes-requested", findings[2])
    r3 = rnd(3, sha, "pass", findings[3])
    review = directory / "review.md"
    review.write_text(header("changes-requested") + r1, encoding="utf-8")

    class ReviewSession:
        """What a review session would reply: round 1 kept, round 2 added."""

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            yield ("chunk", header("changes-requested") + r1 + "\n" + r2)
            yield ("done", {"session_id": "verify-0021", "cost": {}})

    service.sessions = ReviewSession()
    attempts = 0

    async def run_review() -> dict:
        last = {}
        async for kind, payload in service.run_step(cwd, unit, "review"):
            if kind == "done":
                last = payload
        return last

    ok = True

    # C4 (R6, R7): the review step writes round 2 while GitHub is down.
    (fakebin / "FAIL").write_text("")
    failed = await run_review()
    attempts += len(failed.get("comments") or [])
    bytes_when_failed = review.read_bytes()
    (fakebin / "FAIL").unlink()
    [u] = [x for x in (await service.board(cwd))["units"] if x["name"] == unit]
    shown = {r["n"]: r["comment"] for r in u["rounds"]}
    # The same step again with GitHub up, from the same starting file: R2 posts round 2.
    review.write_text(header("changes-requested") + r1, encoding="utf-8")
    posted = await run_review()
    attempts += len(posted.get("comments") or [])
    ok &= say(
        failed.get("outcome") == "done"
        and bytes_when_failed == review.read_bytes()
        and not shown.get(2, {}).get("posted", True)
        and "502" in (shown.get(2, {}).get("reason") or "")
        and [c.get("state") for c in posted.get("comments") or []] == ["posted"],
        "C4 a failed post writes review.md byte for byte as a good one, and the board says "
        "round 2 is not on the PR with gh's reason",
        f"failed={failed.get('outcome')}/{failed.get('comments')}, same bytes="
        f"{bytes_when_failed == review.read_bytes()}, board={shown.get(2)}, "
        f"then={posted.get('comments')}",
    )

    # Round 3 arrives the way a terminal writes it: appended, with no run at all.
    review.write_text(
        header("accepted") + r1 + "\n" + r2 + "\n" + r3, encoding="utf-8"
    )
    gates_before = gates(units_root, unit, workspace)

    # C1 (R1, R8): every round pressed twice through the route.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proof"
    ) as client:
        states = {}
        for n in (1, 2, 3):
            for press in (1, 2):
                got = await client.post(
                    "/api/units/review-comment", json={"cwd": cwd, "unit": unit, "round": n}
                )
                attempts += 1
                states[(n, press)] = f"{got.status_code}:{got.json().get('state')}"
    comments = json.loads((fakebin / "comments.json").read_text(encoding="utf-8"))
    marks = [
        [line.strip() for line in c["body"].splitlines() if line.strip()][-1] for c in comments
    ]
    want = {f"<!-- coscc-review unit={unit} round={n} -->" for n in (1, 2, 3)}
    ok &= say(
        len(comments) == 3 and len(set(marks)) == 3 and set(marks) == want,
        "C1 three rounds, each pressed twice, make exactly three comments with three markers",
        f"comments={len(comments)}, marks={marks}, presses={states}",
    )

    # C2 (R3, R4)
    by_round = {int(re.search(r"round=(\d+)", m).group(1)): c for m, c in zip(marks, comments)}
    missing = []
    for n, c in by_round.items():
        first = c["body"].splitlines()[0]
        m = FIRST_LINE.match(first)
        if not m or m.groups() != (str(n), unit):
            missing.append(f"round {n} first line: {first!r}")
        missing += [f"round {n}: {f}" for f in findings[n] if f not in c["body"]]
    ok &= say(
        len(by_round) == 3 and not missing,
        "C2 every comment opens with the fixed first line and carries every finding verbatim",
        "; ".join(missing),
    )

    # C3 (R5)
    calls = [json.loads(line) for line in (fakebin / "argv.jsonl").read_text().splitlines()]
    reviews = [c for c in calls if c[:2] == ["pr", "review"]]
    ok &= say(
        not reviews and any(c[:2] == ["pr", "comment"] for c in calls),
        "C3 gh was never asked for pr review",
        f"{reviews}",
    )

    # C5 (R10)
    gates_after = gates(units_root, unit, workspace)
    ok &= say(
        gates_before == gates_after,
        "C5 the review and ship gates say the same thing before and after the comments",
        f"before={gates_before}, after={gates_after}",
    )
    print(f"      (review gate: exit {gates_after[0][0]}; ship gate: exit {gates_after[1][0]})")

    # C6 (R13)
    rows = Journal(str(root / "work"), str(root / "data")).records(
        str(workspace.resolve()), kind="pr-comment"
    )
    ok &= say(
        len(rows) == attempts == 8,
        "C6 one pr-comment row per attempt",
        f"rows={len(rows)}, attempts={attempts}, "
        f"outcomes={[(r.get('round'), r.get('outcome')) for r in rows]}",
    )
    return bool(ok)


def c7() -> bool:
    """C7 (R11): the grant table and the guard were not touched by this branch."""
    ref = trunk()
    if ref is None:
        print("no main or origin/main to diff against — C7 cannot answer")
        raise SystemExit(EXIT_ENV)
    policy = subprocess.run(["git", "diff", "--quiet", ref, "--", "coscc/policy.py"], cwd=REPO)
    runner = subprocess.run(["git", "diff", ref, "--", "coscc/runner.py"], cwd=REPO,
                            capture_output=True, text=True)
    touched = [
        line for line in runner.stdout.splitlines()
        if line[:1] in "+-" and not line.startswith(("+++", "---")) and "beyond_reading" in line
    ]
    return say(
        policy.returncode == 0 and not touched,
        f"C7 coscc/policy.py is unchanged from {ref} and no line with beyond_reading moved",
        f"policy diff exit={policy.returncode}, runner lines={touched}",
    )


def main() -> int:
    require_environment()
    with tempfile.TemporaryDirectory(prefix="verify-0021-") as d:
        root = Path(d)
        fakebin = root / "fakebin"
        fakebin.mkdir()
        gh = fakebin / "gh"
        gh.write_text(FAKE_GH.format(python=sys.executable), encoding="utf-8")
        gh.chmod(0o755)
        # Before `coscc` is imported: `coscc/state.py` builds an app from the environment
        # at import, and that app must open this directory, not `~/.cos`.
        os.environ["PATH"] = f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}"
        os.environ["COS_DATA_DIR"] = str(root / "data")
        os.environ["COS_WORKING_DIR"] = str(root / "work")
        for name, fallback in (("COS_PORT", "8790"), ("COS_HOST", "127.0.0.1")):
            if not os.environ.get(name, "").strip():
                os.environ[name] = fallback
        print(f"temporary data root: {root}")
        ok = asyncio.run(run(root, fakebin))
        ok = c7() and ok
        return EXIT_PASS if ok else EXIT_BROKEN


if __name__ == "__main__":
    raise SystemExit(main())
