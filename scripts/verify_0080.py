"""`0080` proof: a spike that runs out of turns still leaves the `spike.md` it measured.

Plain: no session, no quota, no network. Temporary stores and data roots, the session
replaced by a stand-in. Needs `node` and `git`, and a merge-base between `HEAD` and
`origin/main`; any missing is exit 2. Each claim prints PASS or FAIL:

- R9: `.claude/scripts/cos.mjs` is unchanged since the merge-base, and a unit whose spec
  is accepted with no `[unmeasured]` item is offered `plan`, its `spike` gate closed and its
  `plan` gate open.
- R10 on a fixture `cos.db`: `--measure` passes, fails and waits where `spec.md ## Answers,
  câu 1` says it should.
- R11 (a) through `Service.run_step`: a stand-in that keeps its progress file and stops at
  the turn ceiling leaves the unit a `spike.md` equal to it, an `end` row with
  `outcome: exhausted` and `spike_md: progress`, and no scratch.

`--measure` (R10) reads `<COS_DATA_DIR>/cos.db` with `mode=ro`, never through `Data`
(`0076`), and the merge time from this unit's `ship.md` under any
`<COS_DATA_DIR>/units/*/.cos/`. The window runs from it to `2026-11-01T00:00:00+07:00` —
the time zone is chosen, not sourced. Every `end` row with `stage: spike` in it is counted
by its `spike_md`: `withheld` (a Stop, a changed worktree) is left out of the count; `none`
or `unusable` is a failure, exit 1 even while the window is open; a row with no `spike_md`
at all (a step the installed copy ran before it carried this unit) is left out and counted
apart. With no failure: fewer than 3 steps counted, or the window still open, is exit 2;
otherwise exit 0. It also prints how many of those steps ended `exhausted`
(`spec.md ## Answers, câu 2`). It writes only `<COS_DATA_DIR>/measurements/0080-<stamp>.json`.
Run it at a terminal: inside a step it can read only that step's own data root.

What it does not measure: whether a real session keeps the progress file (spec C5); a step
cut short by the app going down, which writes no `end`; a spike run at a terminal.

Exit codes: 0 pass, 1 broken, 2 environment not ready.
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
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
COS = REPO / ".claude" / "scripts" / "cos.mjs"
UNIT = "0080_a-spike-runs-out-of-turns-before-writing-spike-md"
WINDOW_END = datetime.fromisoformat("2026-11-01T00:00:00+07:00")
ENOUGH = 3
FAILED = ("none", "unusable")
EXIT_PASS, EXIT_BROKEN, EXIT_ENV = 0, 1, 2
ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)(Z|[+-]\d{2}:?\d{2})?")


def say(line: str) -> None:
    print(line, flush=True)


def data_root() -> Path:
    return Path(os.environ.get("COS_DATA_DIR") or "~/.cos").expanduser()


# Copied from `scripts/verify_0074.py:54-81`; the proofs do not import each other.
def shipped_at(ship: Path) -> datetime | None:
    """The first ISO-8601 timestamp under `## What went out`, in UTC; `None` if there is none."""
    try:
        text = ship.read_text(encoding="utf-8")
    except OSError:
        return None
    body: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = line.strip() == "## What went out"
            continue
        if inside:
            body.append(line)
    m = ISO.search("\n".join(body))
    if not m:
        return None
    zone = m.group(2) or "Z"
    zone = "+00:00" if zone == "Z" else (zone if ":" in zone else f"{zone[:3]}:{zone[3:]}")
    try:
        at = datetime.fromisoformat(m.group(1) + zone)
    except ValueError:
        return None
    return at.astimezone(timezone.utc)


def _when(at: str) -> datetime | None:
    try:
        t = datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return None
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def measure(root: Path, now: datetime | None = None) -> int:
    """R10. Everything it prints, it also writes to one file under `measurements/`."""
    now = now or datetime.now(timezone.utc)
    ships = sorted(root.glob(f"units/*/.cos/{UNIT}/ship.md"))
    merged = next((t for t in (shipped_at(p) for p in ships) if t is not None), None)
    if merged is None:
        say(f"no merge line in any {UNIT}/ship.md under {root}/units: nothing to measure yet")
        return EXIT_ENV
    db = root / "cos.db"
    if not db.is_file():
        say(f"no {db}")
        return EXIT_ENV
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        rows = conn.execute("SELECT record FROM runs WHERE kind = 'end' ORDER BY id").fetchall()
    steps: list[dict] = []
    for (raw,) in rows:
        try:
            r = json.loads(raw)
        except ValueError:
            continue
        at = _when(r.get("at"))
        if r.get("stage") != "spike" or at is None or not (merged <= at < WINDOW_END):
            continue
        steps.append({"unit": r.get("unit"), "at": r.get("at"), "outcome": r.get("outcome"),
                      "spike_md": r.get("spike_md")})
    by: dict[str, int] = {}
    for s in steps:
        key = s["spike_md"] if s["spike_md"] is not None else "(no field)"
        by[key] = by.get(key, 0) + 1
    counted = [s for s in steps if s["spike_md"] not in (None, "withheld")]
    failed = [s for s in counted if s["spike_md"] in FAILED]
    exhausted = sum(1 for s in steps if s["outcome"] == "exhausted")
    closed = now >= WINDOW_END
    if failed:
        result, code = "sai", EXIT_BROKEN
    elif len(counted) < ENOUGH:
        result, code = f"chưa đủ: {len(counted)} bước, cần {ENOUGH}", EXIT_ENV
    elif not closed:
        result, code = "cửa sổ còn mở", EXIT_ENV
    else:
        result, code = "đạt", EXIT_PASS
    say(f"merged {merged.isoformat()}, window to {WINDOW_END.isoformat()}: "
        f"{len(counted)} spike steps counted")
    for key in sorted(by):
        say(f"  spike_md {key}: {by[key]}")
    say(f"  withheld, not counted: {by.get('withheld', 0)}; no field, not counted: {by.get('(no field)', 0)}")
    say(f"  outcome exhausted: {exhausted}")
    for s in failed:
        say(f"  failed: {s['unit']} at {s['at']} ({s['outcome']}, spike_md {s['spike_md']})")
    say(f"→ {result}")
    out = root / "measurements" / f"0080-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "merged": merged.isoformat(), "window_end": WINDOW_END.isoformat(), "result": result,
        "counted": len(counted), "by_spike_md": by, "exhausted": exhausted, "steps": steps,
        "exit": code,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    say(f"wrote {out}")
    return code


# ---------------------------------------------------------------------------
# The proof
# ---------------------------------------------------------------------------


def claim(ok: bool, text: str, detail: str = "") -> bool:
    say(f"{'PASS' if ok else 'FAIL'}  {text}{': ' + detail if detail and not ok else ''}")
    return ok


def run(*args: str, cwd: Path = REPO) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), capture_output=True, text=True, cwd=str(cwd))


def loop_unchanged(tmp: Path) -> bool | None:
    """R9. `None` when the environment cannot answer."""
    base = run("git", "merge-base", "HEAD", "origin/main")
    if base.returncode != 0 or not base.stdout.strip():
        say(f"environment: no merge-base between HEAD and origin/main ({base.stderr.strip()})")
        return None
    ok = claim(run("git", "diff", "--quiet", base.stdout.strip(), "HEAD", "--",
                   ".claude/scripts/cos.mjs").returncode == 0,
               f"R9: cos.mjs unchanged since {base.stdout.strip()[:12]}")
    unit = "0001_no-unmeasured-question"
    d = tmp / ".cos" / unit
    d.mkdir(parents=True)
    (d / "idea.md").write_text("# Idea: x\nAuthor: t. Status: accepted.\n", encoding="utf-8")
    (d / "intent.md").write_text("# Intent: x\nAuthor: t. Type: fix. Status: accepted.\n", encoding="utf-8")
    (d / "spec.md").write_text(
        "# Spec: x\nIntent: intent.md. Author: t. Status: accepted.\n\n## Concerns\n\n- C1. đo rồi.\n",
        encoding="utf-8",
    )
    nxt = run("node", str(COS), "--root", str(tmp), "next", unit)
    try:
        stage = json.loads(nxt.stdout).get("stage")
    except ValueError:
        stage = None
    ok &= claim(stage == "plan", "R9: next offers plan to a unit with no [unmeasured] item",
                nxt.stdout.strip() or nxt.stderr.strip())
    spike = run("node", str(COS), "--root", str(tmp), "gate", unit, "spike")
    ok &= claim(spike.returncode == 1, "R9: its spike gate is closed", f"exit {spike.returncode}")
    plan = run("node", str(COS), "--root", str(tmp), "gate", unit, "plan")
    ok &= claim(plan.returncode == 0, "R9: its plan gate is open", f"exit {plan.returncode} {plan.stdout.strip()}")
    ok &= claim(not (d / "spike.md").exists(), "R9: no ninth artifact")
    return ok


def measure_fixture(tmp: Path) -> bool:
    from coscc.journal import Journal

    closed, open_ = "2026-11-02T00:00:00+00:00", "2026-10-10T00:00:00+00:00"
    cases = (
        ("pass", ["reply", "progress", "reply"], closed, EXIT_PASS),
        ("none", ["reply", "progress", "reply", "none"], closed, EXIT_BROKEN),
        ("unusable", ["reply", "unusable", "reply", "reply"], closed, EXIT_BROKEN),
        ("withheld-left-out", ["reply", "reply", "progress", "withheld"], closed, EXIT_PASS),
        ("too-few", ["reply", "reply"], closed, EXIT_ENV),
        ("open", ["reply", "reply", "reply"], open_, EXIT_ENV),
        ("failed-while-open", ["reply", "none"], open_, EXIT_BROKEN),
        ("no-field-left-out", ["reply", "reply", None], closed, EXIT_ENV),
    )
    ok = True
    for case, values, now, want in cases:
        root = tmp / case
        ship = root / "units" / "slot" / ".cos" / UNIT / "ship.md"
        ship.parent.mkdir(parents=True)
        ship.write_text("# Ship\nStatus: accepted.\n\n## What went out\n\nMerged 2026-10-01T10:00:00Z.\n",
                        encoding="utf-8")
        j = Journal(root / "work", root)
        for i, value in enumerate(values):
            j.append({"kind": "end", "workspace": "/w", "unit": f"00{i}_x", "stage": "spike",
                      "outcome": "exhausted" if value != "reply" else "done",
                      "at": f"2026-10-0{i + 2}T00:00:00+00:00",
                      **({"spike_md": value} if value is not None else {})})
        # Neither of these is counted: another stage, and a spike before the merge.
        j.append({"kind": "end", "workspace": "/w", "unit": "009_x", "stage": "plan", "outcome": "failed",
                  "at": "2026-10-05T00:00:00+00:00"})
        j.append({"kind": "end", "workspace": "/w", "unit": "009_x", "stage": "spike", "outcome": "exhausted",
                  "spike_md": "none", "at": "2026-09-30T00:00:00+00:00"})
        code = measure(root, datetime.fromisoformat(now))
        ok &= claim(code == want, f"R10: --measure on a fixture cos.db, case {case}: exit {want}", f"exit {code}")
    return ok


PROGRESS = ("# Spike: x\nSpec: spec.md. Author: ᛈ Perthro. Round: 1. Status: accepted.\n\n"
            "## U1\n\nVerdict: holds.\n\n```\n$ python -c 'print(1)'\n1\n```\n")


class _RunsOut:
    """Keeps its progress file in `cwd`, replies with prose, and stops at the turn ceiling."""

    def in_flight(self):
        return []

    async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
        (Path(cwd) / "spike.md").write_text(PROGRESS, encoding="utf-8")
        yield ("chunk", "Tôi hết lượt.")
        yield ("done", {"session_id": "proof", "terminal_reason": "max_turns",
                        "cost": {"turns": 81, "cost_usd": 0.01}})


def through_the_service(tmp: Path) -> bool:
    """R11 (a), end to end through `Service.run_step`."""
    from coscc import units
    from coscc.config import Config
    from coscc.service import Service

    repo, tree = tmp / "work" / "proj", tmp / "tree"
    for where in (repo, tree):
        where.mkdir(parents=True)
        run("git", "init", "-q", "-b", "main", cwd=where)
        run("git", "-c", "user.name=T", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
            "commit", "-q", "--allow-empty", "-m", "first", cwd=where)
    data = tmp / "data"
    service = Service(Config(workspaces=(str(repo),), working_dir=str(tmp / "work"), data_dir=str(data)),
                      _RunsOut())
    made = asyncio.run(service.create_unit(str(repo), "a-problem", "some words"))
    d = Path(made["path"])
    (d / "intent.md").write_text("# Intent: x\nAuthor: t. Type: fix. Status: accepted.\n", encoding="utf-8")
    (d / "spec.md").write_text(
        "# Spec: x\nIntent: intent.md. Author: t. Status: accepted.\n\n## Concerns\n\n"
        "- [unmeasured] U1. does it exit?\n", encoding="utf-8",
    )
    unit = made["unit"]
    scratch = units.spike_dir(str(repo), unit, str(data))
    where = {"path": str(tree), "branch": "fix/a-problem", "base": None}

    async def go():
        with mock.patch.object(Service, "_worktree", mock.AsyncMock(return_value=where)):
            return [i async for i in service.run_step(str(repo), unit, "spike")]

    out = asyncio.run(go())
    ok = claim(out[-1][1].get("outcome") == "exhausted", "R11 (a): the step ends exhausted", str(out[-1]))
    written = d / "spike.md"
    ok &= claim(written.is_file() and written.read_text(encoding="utf-8") == PROGRESS,
                "R11 (a): the unit's spike.md is the progress file, byte for byte")
    ok &= claim(not scratch.exists(), "R11 (a): the scratch is gone")
    ends = []
    with sqlite3.connect(f"file:{data / 'cos.db'}?mode=ro", uri=True) as conn:
        for (raw,) in conn.execute("SELECT record FROM runs WHERE kind = 'end' ORDER BY id"):
            r = json.loads(raw)
            if r.get("unit") == unit and r.get("stage") == "spike":
                ends.append(r)
    got = [(r.get("outcome"), r.get("spike_md")) for r in ends]
    ok &= claim(got == [("exhausted", "progress")], "R11 (a): its end row says exhausted and progress", str(got))
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--measure", action="store_true", help="R10, on the real <COS_DATA_DIR>")
    args = parser.parse_args()
    if args.measure:
        return measure(data_root())
    for tool in ("node", "git"):
        if not shutil.which(tool):
            say(f"environment: {tool} is needed")
            return EXIT_ENV
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        loop = loop_unchanged(tmp / "loop")
        if loop is None:
            return EXIT_ENV
        ok = loop
        ok &= measure_fixture(tmp / "measure")
        ok &= through_the_service(tmp / "service")
    say("all claims pass" if ok else "some claims failed")
    return EXIT_PASS if ok else EXIT_BROKEN


if __name__ == "__main__":
    sys.exit(main())
