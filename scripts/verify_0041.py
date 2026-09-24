#!/usr/bin/env python3
"""Proof, and measuring tool, for the store's `0041_the-pr-step-runs-out-of-turns-before-writing-pr-md`.

A `pr` step is handed the pull request the app looked up (R2), its own task (R1), and a
grant that refuses integration (R3). Three modes:

    (default)    the proof. No session, no quota, no network; temporary data root and a
                 fake `gh` first on `PATH`.
                 (a) the pull request already exists: `pr_for_branch` answers `found`, and
                     the `pr` prompt carries its URL and says not to open a second one.
                 (b) `main` is ahead far enough to conflict: `gh` says `CONFLICTING`, the
                     prompt says stop and names *Integrate*, and the grant refuses
                     `git rebase`, `git pull --rebase`, `gh pr update-branch` and a forced push.
                 (c) `gh` fails: `unknown`, and the prompt says the lookup could not be made.
                 (d) `--measure`'s verdict on fixture logs: five done is exit 0; one
                     `max_turns` is exit 1; a unit needing a second `pr` session is exit 1;
                     four is exit 2.
    --measure    the intent's outcome: every `pr` step whose `start` record carries
                 `pr_before` (written only since this unit), in order, one line each and a
                 table per model. Reads `<COS_DATA_DIR>/cos.db` with `mode=ro` and each
                 unit's `pr.md` in the store; writes only
                 `<COS_DATA_DIR>/measurements/0041-<timestamp>.json`. No session.
    --paid       **spends real money and opens real pull requests.** Needs
                 `COS_PROOF_REPO`, a throwaway repository this machine's `gh` can push to.
                 Builds the two cases of `intent.md ## Answers, câu 5` on a clone — a pull
                 request opened before the step, and a commit on `main` that conflicts with
                 the branch — and runs one real `pr` step on each through `Service.run_step`,
                 on the stage's default model. Pushes to that repository's `main` and leaves
                 both pull requests open.

    0  pass (the outcome held, for --measure / --paid)
    1  fail (a claim, or the outcome, did not hold)
    2  the environment could not answer, or there are not yet five `pr` steps to judge
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = Path(__file__).resolve().parent.parent
EXIT_PASS, EXIT_BROKEN, EXIT_ENV = 0, 1, 2

STAGES = ["idea", "intent", "spec", "plan", "impl", "pr", "review", "ship"]
# `intent.md ## Proposed outcome`: five consecutive runs, chosen by the intent's author.
NEEDED = 5
URL = "https://github.com/o/r/pull/7"
HEAD = "a" * 40


def say(ok: bool, claim: str, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {claim}{': ' + detail if detail and not ok else ''}",
          flush=True)
    return ok


# ---------------------------------------------------------------------------
# Reading the run log and the store (--measure)
# ---------------------------------------------------------------------------


def data_root() -> Path:
    return Path(os.environ.get("COS_DATA_DIR") or "~/.cos").expanduser()


def load_steps(db: Path) -> list[dict]:
    """Every `start` paired with the next `end` of the same (root, workspace, unit, stage).

    Copied from `scripts/verify_0037.py`, not imported: one proof script does not depend on
    another. A `start` with no `end` after it (still running, or a process that died) is
    left out.
    """
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT id, at, root, workspace, unit, stage, kind, record FROM runs "
            "WHERE kind IN ('start', 'end') ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    open_starts: dict[tuple, dict] = {}
    steps: list[dict] = []
    for rid, at, root, ws, unit, stage, kind, raw in rows:
        try:
            rec = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        key = (root, ws, unit, stage)
        if kind == "start":
            open_starts[key] = {"id": rid, "at": at, "unit": unit, "stage": stage, "start": rec}
        elif key in open_starts:
            step = open_starts.pop(key)
            step["end"] = rec
            steps.append(step)
    return steps


_STATUS = re.compile(r"\bStatus:\s*([A-Za-z-]+)")
_PR = re.compile(r"\bPR:\s*(https?://\S+?/pull/\d+)")


def pr_md_ok(unit: str) -> bool:
    """Whether the unit's `pr.md` in the store, read now, is accepted and names its PR.

    Found by the unit's name under `<data>/units/*/.cos/`: the run log's `workspace` is a
    key, not a path. Two workspaces holding a unit of one name would both be looked at.
    """
    for path in sorted(data_root().glob(f"units/*/.cos/{unit}/pr.md")):
        head = "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[:5])
        status = _STATUS.search(head)
        if status and status.group(1).lower() == "accepted" and _PR.search(head):
            return True
    return False


def row_of(step: dict) -> dict:
    start, end = step["start"], step["end"]
    return {
        "id": step["id"],
        "at": step["at"],
        "unit": step["unit"],
        "model": ",".join(end.get("models_used") or []) or str(start.get("model") or ""),
        "turns": end.get("turns"),
        "terminal": str(end.get("terminal") or ""),
        "outcome": str(end.get("outcome") or ""),
        "cost_usd": float(end.get("cost_usd") or 0.0),
        "pr_before": str(start.get("pr_before") or ""),
    }


def judge(rows: list[dict]) -> list[dict]:
    """Mark each measured step done or not. A step counts only when it ended `done`, not at
    `max_turns`, its `pr.md` is accepted with `PR:`, and no `pr` step of the same unit
    failed just before it — that one would be the "second session" the intent rules out."""
    failed_last: dict[str, bool] = {}
    for r in rows:
        ok = (r["outcome"] == "done" and r["terminal"] != "max_turns" and r["pr_md"]
              and not failed_last.get(r["unit"]))
        reasons = []
        if r["outcome"] != "done":
            reasons.append(f"outcome {r['outcome']}")
        if r["terminal"] == "max_turns":
            reasons.append("max_turns")
        if not r["pr_md"]:
            reasons.append("pr.md not accepted with PR:")
        if failed_last.get(r["unit"]):
            reasons.append("a second session for this unit")
        r["held"], r["why"] = bool(ok), ", ".join(reasons)
        failed_last[r["unit"]] = not (r["outcome"] == "done" and r["terminal"] != "max_turns")
    return rows


def verdict(rows: list[dict]) -> tuple[int, str]:
    first = rows[:NEEDED]
    if any(not r["held"] for r in first):
        return EXIT_BROKEN, "a pr step did not finish in one session — outcome did NOT hold"
    if len(first) < NEEDED:
        return EXIT_ENV, f"{len(first)} of {NEEDED} pr steps so far — not yet judged"
    if not any(r["pr_before"] for r in first):
        return EXIT_ENV, ("none of the five had a pull request before it started — "
                          "not yet judged; `--paid` builds that case")
    return EXIT_PASS, f"{NEEDED} pr steps in a row, each one session, one with a PR before — outcome HELD"


def print_rows(rows: list[dict]) -> None:
    print("| unit | model | turns | terminal | cost_usd | PR before | pr.md | held |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['unit']} | {r['model']} | {r['turns']} | {r['terminal'] or '-'} | "
              f"{r['cost_usd']:.4f} | {'yes' if r['pr_before'] else 'no'} | "
              f"{'ok' if r['pr_md'] else 'NO'} | {'yes' if r['held'] else 'NO: ' + r['why']} |")


def by_model(rows: list[dict]) -> dict[str, dict]:
    """`intent.md ## Answers, câu 4`: report each model on its own."""
    out: dict[str, dict] = {}
    for r in rows:
        m = out.setdefault(r["model"] or "(unknown)", {"steps": 0, "held": 0, "max_turns": 0,
                                                        "turns": [], "cost_usd": 0.0})
        m["steps"] += 1
        m["held"] += int(r["held"])
        m["max_turns"] += int(r["terminal"] == "max_turns")
        if r["turns"] is not None:
            m["turns"].append(r["turns"])
        m["cost_usd"] = round(m["cost_usd"] + r["cost_usd"], 4)
    return out


def run_measure() -> int:
    db = data_root() / "cos.db"
    if not db.is_file():
        print(f"no run log at {db}")
        return EXIT_ENV
    steps = [s for s in load_steps(db) if s["stage"] == "pr" and "pr_before" in s["start"]]
    rows = []
    for s in steps:
        r = row_of(s)
        r["pr_md"] = pr_md_ok(r["unit"])
        rows.append(r)
    judge(rows)
    print(f"pr steps since 0041 ({len(rows)}; the first {NEEDED} are judged):")
    print_rows(rows)
    models = by_model(rows)
    print("\nper model:")
    print("| model | steps | held | max_turns | turns | cost_usd |")
    print("|---|---|---|---|---|---|")
    for name, m in models.items():
        print(f"| {name} | {m['steps']} | {m['held']} | {m['max_turns']} | "
              f"{','.join(str(t) for t in m['turns']) or '-'} | {m['cost_usd']:.4f} |")
    code, line = verdict(rows)
    out = data_root() / "measurements"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"0041-{time.strftime('%Y%m%dT%H%M%S')}.json"
    path.write_text(json.dumps({"steps": rows, "models": models, "verdict": line, "exit": code},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{line}\nwritten: {path}")
    return code


# ---------------------------------------------------------------------------
# The proof (default mode)
# ---------------------------------------------------------------------------


def fake_gh(bindir: Path, stdout: str, code: int = 0) -> None:
    bindir.mkdir(parents=True, exist_ok=True)
    script = bindir / "gh"
    script.write_text(
        "#!/bin/sh\n"
        f"cat <<'EOF'\n{stdout}\nEOF\n"
        f"[ {code} -eq 0 ] || echo 'gh: not logged in' >&2\n"
        f"exit {code}\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


def lookup_and_prompt(tmp: Path, name: str, stdout: str, code: int = 0) -> tuple[dict, str]:
    """`pr_for_branch` against a fake `gh`, then the `pr` prompt `build_prompt` makes of it."""
    from coscc import integrate
    from coscc.runner import build_prompt

    bindir = tmp / name / "bin"
    fake_gh(bindir, stdout, code)
    saved = os.environ["PATH"]
    os.environ["PATH"] = f"{bindir}{os.pathsep}{saved}"
    try:
        rec = asyncio.run(integrate.pr_for_branch(str(tmp), "fix/proof"))
    finally:
        os.environ["PATH"] = saved
    unit = tmp / name / ".cos" / "0001_proof"
    unit.mkdir(parents=True)
    (unit / "intent.md").write_text("# Intent: proof\nStatus: accepted.\n", encoding="utf-8")
    (unit / "impl.md").write_text("# Impl: proof\nStatus: accepted.\n", encoding="utf-8")
    prompt, _ = build_prompt(tmp / name, unit, "0001_proof", "pr", STAGES, "pr.md",
                             writes_own=True, pr_note=integrate.describe_pr_lookup(rec))
    return rec, prompt


def claims_abc(tmp: Path) -> bool:
    from coscc.policy import check_command, grant_for

    ok = True
    found = json.dumps([{"url": URL, "number": 7, "mergeable": "MERGEABLE", "headRefOid": HEAD}])
    rec, prompt = lookup_and_prompt(tmp, "a", found)
    ok &= say(rec.get("state") == "found" and rec.get("url") == URL,
              "(a) an existing pull request is looked up as found", repr(rec))
    ok &= say(URL in prompt and "Do not run `gh pr create` again" in prompt,
              "(a) the pr prompt carries its URL and says not to open a second one")
    ok &= say("Do the work this unit's plan authorises" not in prompt,
              "(a) the pr prompt no longer carries impl's task")

    conflicting = json.dumps([{"url": URL, "number": 7, "mergeable": "CONFLICTING", "headRefOid": HEAD}])
    rec, prompt = lookup_and_prompt(tmp, "b", conflicting)
    ok &= say(rec.get("mergeable") == "CONFLICTING", "(b) a conflict with main is looked up", repr(rec))
    ok &= say("Do not rebase" in prompt and "*Integrate*" in prompt,
              "(b) the prompt says stop and names Integrate")
    pr = grant_for("pr")
    for command in ("git rebase origin/main", "git pull --rebase", "gh pr update-branch 1 --rebase",
                    "git push --force"):
        reason = check_command(pr, command)
        ok &= say("Integrate" in reason, f"(b) the pr grant refuses `{command}`", reason or "allowed")

    rec, prompt = lookup_and_prompt(tmp, "c", "", code=1)
    ok &= say(rec.get("state") == "unknown" and "not logged in" in rec.get("reason", ""),
              "(c) a failing gh is unknown, with its own words", repr(rec))
    ok &= say("could not ask `gh`" in prompt, "(c) the prompt says the lookup could not be made")
    return ok


def claim_d(tmp: Path) -> bool:
    """`--measure` against fixture logs written by the real journal and a fixture store."""
    from coscc.journal import Journal

    def fixture(name: str, steps: list[tuple[str, str, str, str]]) -> Path:
        root = tmp / "d" / name
        j = Journal(str(root / "ws"), str(root))
        # A `pr` step from before 0041 — no `pr_before` — is never measured.
        j.append({"kind": "start", "workspace": "w", "unit": "0019_old", "stage": "pr", "mode": "manual"})
        j.append({"kind": "end", "workspace": "w", "unit": "0019_old", "stage": "pr",
                  "outcome": "failed", "terminal": "max_turns", "turns": 31})
        for unit, outcome, terminal, before in steps:
            j.append({"kind": "start", "workspace": "w", "unit": unit, "stage": "pr",
                      "mode": "manual", "model": "claude-sonnet-5[1m]", "pr_before": before})
            j.append({"kind": "end", "workspace": "w", "unit": unit, "stage": "pr",
                      "outcome": outcome, "terminal": terminal or None, "turns": 12,
                      "cost_usd": 0.4, "models_used": ["claude-sonnet-5[1m]"]})
            d = root / "units" / "slot" / ".cos" / unit
            d.mkdir(parents=True, exist_ok=True)
            if outcome == "done":
                (d / "pr.md").write_text(f"# PR: x\nPR: {URL}. Status: accepted.\n", encoding="utf-8")
        return root

    def measure(root: Path) -> int:
        saved = os.environ.get("COS_DATA_DIR")
        os.environ["COS_DATA_DIR"] = str(root)
        try:
            return run_measure()
        finally:
            if saved is None:
                os.environ.pop("COS_DATA_DIR", None)
            else:
                os.environ["COS_DATA_DIR"] = saved

    five = [(f"00{50 + i}_u", "done", "", URL if i == 0 else "") for i in range(5)]
    ok = True
    ok &= say(measure(fixture("pass", five)) == EXIT_PASS, "(d) five done, one with a PR before, is exit 0")
    ok &= say(measure(fixture("turns", five[:4] + [("0059_u", "failed", "max_turns", "")])) == EXIT_BROKEN,
              "(d) one max_turns is exit 1")
    twice = five[:3] + [("0058_u", "failed", "", ""), ("0058_u", "done", "", URL)]
    ok &= say(measure(fixture("twice", twice)) == EXIT_BROKEN,
              "(d) a unit that needed a second pr session is exit 1")
    ok &= say(measure(fixture("short", five[:4])) == EXIT_ENV, "(d) four steps is exit 2")
    return ok


def run_proof() -> int:
    for tool in ("git", "uv"):
        if shutil.which(tool) is None:
            print(f"{tool} is not on PATH")
            return EXIT_ENV
    tmp = Path(tempfile.mkdtemp(prefix="verify_0041-"))
    os.environ["COS_DATA_DIR"] = str(tmp / "data")
    os.environ["COS_WORKING_DIR"] = str(tmp)
    try:
        ok = claims_abc(tmp)
        ok &= claim_d(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nall claims held" if ok else "\nat least one claim did not hold")
    return EXIT_PASS if ok else EXIT_BROKEN


# ---------------------------------------------------------------------------
# --paid
# ---------------------------------------------------------------------------


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.name=verify_0041", "-c", "user.email=v@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def accepted(title: str, extra: str = "") -> str:
    return f"# {title}: proof\nAuthor: verify_0041.{extra} Status: accepted.\n\nA fixture.\n"


async def paid_case(service, repo: Path, proof_repo: str, case: str, stamp: str) -> bool:
    from coscc.units import unit_dir

    made = await service.create_unit(str(repo), f"proof-{stamp}-{case}", "verify_0041 --paid")
    unit = made["unit"]
    d = unit_dir(str(repo), unit, service.config.data_dir)
    (d / "intent.md").write_text(accepted("Intent", " Type: fix."), encoding="utf-8")
    (d / "spec.md").write_text("# Spec: proof\nAuthor: verify_0041. Status: skipped.\n", encoding="utf-8")
    (d / "plan.md").write_text(accepted("Plan", " Intent: intent.md."), encoding="utf-8")
    cut = await service.start_branch(str(repo), unit)
    tree, branch = Path(cut["worktree"]), cut["branch"]
    target = f"proof-0041-{stamp}.txt"
    (tree / target).write_text(f"{case} from the branch\n", encoding="utf-8")
    git(tree, "add", "-A")
    git(tree, "commit", "-q", "-m", f"fix: verify_0041 {case}")
    (d / "impl.md").write_text(accepted("Impl", " Intent: intent.md. Plan: plan.md."), encoding="utf-8")
    if case == "existing":
        git(tree, "push", "-q", "-u", "origin", "HEAD")
        subprocess.run(["gh", "pr", "create", "--fill-first", "--head", branch], cwd=tree,
                       capture_output=True, text=True, check=True)
    else:
        other = repo.parent / f"main-{case}"
        subprocess.run(["git", "clone", "-q", proof_repo, str(other)], check=True)
        (other / target).write_text("main says otherwise\n", encoding="utf-8")
        git(other, "add", "-A")
        git(other, "commit", "-q", "-m", "verify_0041: a conflicting commit on main")
        git(other, "push", "-q", "origin", "HEAD:main")
        git(tree, "push", "-q", "-u", "origin", "HEAD")
    starts = 0
    last: dict = {}
    async for kind, payload in service.run_step(str(repo), unit, "pr"):
        if kind == "done":
            last = payload
            starts += 1
    head = "\n".join((d / "pr.md").read_text(encoding="utf-8").splitlines()[:5]) if (d / "pr.md").exists() else ""
    status = _STATUS.search(head)
    ok = say(last.get("outcome") == "done" and last.get("terminal") != "max_turns",
             f"--paid {case}: the pr step finished in one session",
             f"outcome {last.get('outcome')}, terminal {last.get('terminal')}")
    ok &= say(bool(status and status.group(1).lower() == "accepted" and _PR.search(head)),
              f"--paid {case}: pr.md is accepted and names its pull request", head[:200])
    print(f"  {case}: model {last.get('model')}, turns {last.get('turns')}, cost {last.get('cost_usd')}")
    return ok


async def paid_mode(base: Path, proof_repo: str) -> bool:
    from coscc.config import Config
    from coscc.service import Service
    from coscc.sessions import Sessions

    work = base / "work"
    work.mkdir()
    repo = work / "proof"
    subprocess.run(["git", "clone", "-q", proof_repo, str(repo)], check=True)
    config = Config(workspaces=(str(repo),), working_dir=str(work), data_dir=str(base / "data"))
    service = Service(config, Sessions(config))
    stamp = str(int(time.time()))
    ok = True
    for case in ("existing", "conflicting"):
        ok &= await paid_case(service, repo, proof_repo, case, stamp)
    return ok


def run_paid() -> int:
    proof = os.environ.get("COS_PROOF_REPO", "").strip()
    if not proof:
        print("--paid needs COS_PROOF_REPO, a throwaway repository")
        return EXIT_ENV
    for tool in ("git", "gh", "node", "uv"):
        if shutil.which(tool) is None:
            print(f"{tool} is not on PATH")
            return EXIT_ENV
    with tempfile.TemporaryDirectory(prefix="verify-0041-") as d:
        base = Path(d)
        os.environ["COS_DATA_DIR"] = str(base / "data")
        ok = asyncio.run(paid_mode(base, proof))
    return EXIT_PASS if ok else EXIT_BROKEN


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = p.add_mutually_exclusive_group()
    g.add_argument("--measure", action="store_true")
    g.add_argument("--paid", action="store_true")
    args = p.parse_args()
    if args.measure:
        return run_measure()
    if args.paid:
        return run_paid()
    return run_proof()


if __name__ == "__main__":
    raise SystemExit(main())
