#!/usr/bin/env python3
"""Proof for the store's `0003_one-idea-is-trapped-inside-one-unit`.

One idea, `.cos/ideas/NNNN_<slug>.md`, is the source of several units, and its words live
in that one file. The plan's claims:

    C-a  `status --json`'s `units` array is the same, byte for byte, from `cos.mjs` as it
         was at the merge base and as it is now, on this repository's `.cos/` and on a
         fixture with no `ideas/` (R6)
    C-b  `POST /api/units` with a brief writes an idea file, not an `idea.md` (R1, R8)
    C-c  `POST /api/units` with `idea` opens a second unit from it; `## Units` lists both (R9)
    C-d  the `intent` prompt of each unit carries the brief verbatim, and names the idea
         file among what it included (outcome 3, R10)
    C-e  with an `intent.md` naming the idea in each, `status --json` lists exactly those
         two units under the idea, and reports no problem on either side (outcome 1, R3, R5)
    C-f  exactly one file under `.cos/` carries the brief (outcome 2)
    C-g  an `intent` reply with no `Idea:` line is refused and writes nothing (R11)

    0  every claim held
    1  at least one did not
    2  the environment could not answer: no `node`, `uv` or `git`, or no merge base

**No session, no quota, no network.** The API runs in-process on a temporary data root set
before `coscc` is imported, the remote is a bare directory, and the one session (C-g) is a
stand-in that returns a fixed reply.

**`--store <COS_DATA_DIR> --workspace <path>`** reads a real store instead and writes
nothing. It is the outcome's own measurement (`spec.md ## Answers, câu 1`: *"Đo trong store
của workspace."*): it passes when one idea has two or more units whose `intent.md` is
`accepted` and names it, no link problem is reported, and the idea's `## In their own
words` appears in one file only. It is not a condition of this unit's `done` (`plan.md`
Risk 8).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.proof_harness import EXIT_BROKEN, EXIT_ENV, EXIT_PASS, REPO, say  # noqa: E402

STAGES = ["idea", "intent", "spec", "plan", "impl", "pr", "review", "ship"]
MARK = "ONE-IDEA-MANY-UNITS-MARK-3b9e"
SCRIPT = REPO / ".claude" / "scripts" / "cos.mjs"


def require_environment() -> None:
    for tool in ("node", "uv", "git"):
        if shutil.which(tool) is None:
            print(f"no {tool} on PATH — this proof cannot answer without it")
            raise SystemExit(EXIT_ENV)


def status(script: Path, root: Path) -> dict:
    done = subprocess.run(
        ["node", str(script), "--root", str(root), "status", "--json"],
        capture_output=True, text=True, timeout=30,
    )
    if done.returncode != 0:
        raise RuntimeError(f"{script} status exited {done.returncode}: {done.stderr.strip()}")
    return json.loads(done.stdout)


def git(*args: str) -> str:
    done = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip())
    return done.stdout.strip()


# --- C-a ----------------------------------------------------------------------------------


def fixture_without_ideas(root: Path) -> None:
    """Four shapes of unit a store without `ideas/` holds today."""
    cos = root / ".cos"
    only_idea = cos / "0001_only-an-idea"
    only_idea.mkdir(parents=True)
    (only_idea / "idea.md").write_text("# Idea: x\nAuthor: t. Status: accepted.\n", encoding="utf-8")
    with_intent = cos / "0002_with-an-intent"
    with_intent.mkdir()
    (with_intent / "intent.md").write_text(
        "# Intent: x\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8"
    )
    done = cos / "0003_plan-done"
    done.mkdir()
    for name, line in (("intent.md", "Type: fix. Status: accepted."), ("plan.md", "Status: done.")):
        (done / name).write_text(f"# {name}\n{line}\n", encoding="utf-8")
    (cos / "0004_empty").mkdir()


def claim_a(tmp: Path) -> bool:
    try:
        base = git("merge-base", "HEAD", "origin/main")
        old_text = git("show", f"{base}:.claude/scripts/cos.mjs")
    except RuntimeError as e:
        print(f"no merge base with origin/main to compare against: {e}")
        raise SystemExit(EXIT_ENV)
    old = tmp / "old" / ".claude" / "scripts" / "cos.mjs"
    old.parent.mkdir(parents=True)
    old.write_text(old_text + "\n", encoding="utf-8")
    fixture = tmp / "fixture"
    fixture_without_ideas(fixture)
    ok = True
    for label, root in (("this repository's .cos/", REPO), ("a fixture with no ideas/", fixture)):
        before = json.dumps(status(old, root)["units"], sort_keys=True)
        after = json.dumps(status(SCRIPT, root)["units"], sort_keys=True)
        ok &= say(
            before == after,
            f"C-a `units` from cos.mjs at {base[:7]} and now are identical on {label}",
            f"{len(before)} vs {len(after)} characters",
        )
    return bool(ok)


# --- C-b … C-g ------------------------------------------------------------------------------


def make_repo(root: Path) -> Path:
    repo = root / "work" / "proj"
    repo.mkdir(parents=True)
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    remote = root / "remote.git"
    for argv in (
        ["git", "-C", str(repo), "init", "-q", "-b", "main"],
        ["git", "-C", str(repo), "add", "-A"],
        ["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", "commit", "-q", "-m", "first"],
        ["git", "init", "-q", "--bare", "-b", "main", str(remote)],
        ["git", "-C", str(repo), "remote", "add", "origin", str(remote)],
        ["git", "-C", str(repo), "push", "-q", "origin", "main"],
    ):
        subprocess.run(argv, check=True, capture_output=True)
    return repo


class FixedReply:
    """A session that always says the same thing, however many times it is asked."""

    def __init__(self, text: str) -> None:
        self.text = text

    async def stream(self, cwd, prompt, session_id=None, max_turns=1, **kw):
        yield ("chunk", self.text)
        yield ("done", {"session_id": "verify-one-idea", "cost": {}})


async def claims_b_to_g(root: Path) -> bool:
    import httpx

    from coscc import board, runner, units
    from coscc.api import build
    from coscc.config import Config

    repo = make_repo(root)
    cwd = str(repo)
    app = build(Config(workspaces=(cwd,), working_dir=str(root / "work"), data_dir=str(root / "data")))
    store = units.root(cwd, str(root / "data"))
    cos = store / ".cos"
    brief = f"Một quan sát cần nhiều unit. {MARK}"
    ok = True

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        # C-b
        first = await client.post("/api/units", json={"cwd": cwd, "slug": "first-half", "brief": brief})
        body = first.json() if first.status_code == 200 else {}
        idea = str(body.get("idea") or "")
        idea_file = cos / "ideas" / f"{idea}.md"
        unit_a = str(body.get("unit") or "")
        b = (
            first.status_code == 200 and bool(idea) and bool(unit_a)
            and idea_file.is_file() and not (cos / unit_a / "idea.md").exists()
        )
        ok &= say(
            b, "C-b a brief becomes .cos/ideas/<idea>.md and the unit holds no idea.md",
            f"status {first.status_code}, body {first.text[:300]}",
        )
        if not b:
            for c in ("C-c", "C-d", "C-e", "C-f", "C-g"):
                say(False, f"{c} not measured", "C-b did not hold")
            return False

        # C-c
        second = await client.post("/api/units", json={"cwd": cwd, "slug": "second-half", "idea": idea})
        unit_b = str((second.json() if second.status_code == 200 else {}).get("unit") or "")
        text = idea_file.read_text(encoding="utf-8")
        listed = [ln[2:].strip() for ln in text.splitlines() if ln.startswith("- ")]
        c = second.status_code == 200 and bool(unit_b) and unit_a in listed and unit_b in listed
        ok &= say(
            c, "C-c a second unit is opened from the idea, and ## Units lists both",
            f"status {second.status_code}, {second.text[:300]}; listed {listed}",
        )
        if not c:
            for claim in ("C-d", "C-e", "C-f", "C-g"):
                say(False, f"{claim} not measured", "C-c did not hold")
            return False

    # C-d
    data = await board.read(store)
    d = True
    detail = []
    for name in (unit_a, unit_b):
        row = next((u for u in data["units"] if u["name"] == name), None)
        linked = board.idea_of(data, row, store) if row else None
        prompt, included = runner.build_prompt(
            cwd, cos / name, name, "intent", STAGES, "intent.md", idea=linked,
        )
        this = MARK in prompt and f"ideas/{idea}.md" in included
        d &= this
        detail.append(f"{name}: mark={MARK in prompt} included={included}")
    ok &= say(d, "C-d both units' intent prompts carry the brief and include the idea file", "; ".join(detail))

    # C-g, before any intent.md exists: a reply that forgets `Idea:` writes nothing.
    row = next(u for u in data["units"] if u["name"] == unit_a)
    linked = board.idea_of(data, row, store)
    reply = "# Intent: first half\nAuthor: t. Type: feat. Status: accepted.\n\n## Problem\n\nx\n"
    last = None
    async for item in runner.Runner(FixedReply(reply), None).run(
        workspace=cwd, directory=cos / unit_a, journal_key=cwd, unit=unit_a, stage="intent",
        artifact="intent.md", stages=STAGES, mode="manual", idea=linked,
    ):
        last = item
    outcome = (last[1] if last else {}).get("outcome")
    g = outcome == "failed" and not (cos / unit_a / "intent.md").exists()
    ok &= say(g, "C-g an intent reply with no Idea: line is refused and nothing is written",
              f"outcome={outcome!r}, detail={(last[1] if last else {}).get('detail', '')[:200]!r}")

    # C-e
    for name in (unit_a, unit_b):
        (cos / name / "intent.md").write_text(
            f"# Intent: {name}\nAuthor: t. Type: feat. Idea: ideas/{idea}.md. Status: accepted.\n\n"
            "## Problem\n\nx\n",
            encoding="utf-8",
        )
    got = status(SCRIPT, store)
    entry = next((i for i in got.get("ideas") or [] if i.get("name") == idea), {})
    rows = {u["name"]: u for u in got["units"]}
    e = (
        sorted(entry.get("units") or []) == sorted([unit_a, unit_b])
        and entry.get("problems") == []
        and all(rows[n]["problems"] == [] and rows[n].get("idea") == idea for n in (unit_a, unit_b))
    )
    ok &= say(e, "C-e status lists exactly the two units under the idea, with no problem on either side",
              json.dumps({"idea": entry, "units": {n: rows[n].get("problems") for n in (unit_a, unit_b)}})[:400])

    # C-f
    carrying = [p for p in cos.rglob("*") if p.is_file() and MARK in p.read_text(encoding="utf-8", errors="replace")]
    ok &= say(len(carrying) == 1 and carrying[0] == idea_file,
              "C-f exactly one file under .cos/ carries the brief", str(carrying))
    return bool(ok)


# --- --store --------------------------------------------------------------------------------


def own_words(text: str) -> str:
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.rstrip() == "## In their own words")
    except StopIteration:
        return ""
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start + 1:end]).strip()


def measure_store(data_dir: str, workspace: str) -> int:
    os.environ["COS_DATA_DIR"] = data_dir
    from coscc import units

    store = units.root(workspace, data_dir)
    cos = store / ".cos"
    got = status(SCRIPT, store)
    rows = {u["name"]: u for u in got["units"]}
    best = None
    for entry in got.get("ideas") or []:
        name = entry["name"]
        declared = [
            n for n, u in rows.items()
            if u.get("idea") == name and (u.get("artifacts", {}).get("intent.md") or {}).get("status") == "accepted"
        ]
        link_problems = list(entry.get("problems") or []) + [
            p for n in declared for p in rows[n]["problems"] if "idea" in p.lower()
        ]
        words = own_words((cos / entry["file"]).read_text(encoding="utf-8", errors="replace"))
        copies = [
            p for p in cos.rglob("*.md")
            if words and words in p.read_text(encoding="utf-8", errors="replace")
        ] if words else []
        passed = len(declared) >= 2 and not link_problems and len(copies) == 1
        print(f"{name}: accepted units naming it {declared}; link problems {link_problems}; "
              f"files carrying its own words {[str(p.relative_to(cos)) for p in copies]}")
        if passed:
            best = name
    ok = say(best is not None, "outcome: one idea is the source of two accepted intents, and its words live once",
             f"in {cos}")
    return EXIT_PASS if ok else EXIT_BROKEN


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--store", help="a COS_DATA_DIR to read, never written")
    parser.add_argument("--workspace", help="the workspace whose store to read")
    args = parser.parse_args()
    require_environment()
    if args.store or args.workspace:
        if not (args.store and args.workspace):
            print("--store and --workspace go together")
            return EXIT_ENV
        return measure_store(str(Path(args.store).expanduser()), str(Path(args.workspace).expanduser()))

    with tempfile.TemporaryDirectory(prefix="verify-one-idea-") as d:
        root = Path(d)
        # Before `coscc` is imported: it must open this directory, not `~/.cos`.
        os.environ["COS_DATA_DIR"] = str(root / "data")
        os.environ["COS_WORKING_DIR"] = str(root / "work")
        print(f"temporary data root: {root}")
        ok = claim_a(root)
        try:
            ok &= asyncio.run(claims_b_to_g(root))
        except Exception as e:  # a missing mechanism is a failed claim, not a crash
            say(False, "C-b … C-g could not be measured", f"{type(e).__name__}: {e}")
            ok = False
        return EXIT_PASS if ok else EXIT_BROKEN


if __name__ == "__main__":
    raise SystemExit(main())
