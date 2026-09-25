"""`0085` proof: a review that runs out of turns leaves a round, or at least says what it opened.

Plain: no session, no quota, no network. Temporary stores and data roots, the session
replaced by a stand-in and the gate by one that says open (the real `review` gate reads
`gh pr checks`). Needs `node` and `git`; either missing is exit 2. Each claim prints PASS
or FAIL:

- R1: `review`'s grant is 40 turns and $4.0.
- R8, R9 through `node cos.mjs --root <fixture>`: a `draft` whose last round is `incomplete`
  is not "finish and accept review.md" and its `ship` gate is closed; the same full rounds
  with or without an `incomplete` one between them use the same number of rounds, under
  `COS_REVIEW_ROUNDS` 3 and 2.
- R2, R4, R6, R7 through `Service.run_step`: a stand-in that stops at `max_turns` is reopened
  once, on its own session id, with no tools, one turn and a handle of its own; its round is
  appended with Round 1 byte for byte, and the `end` row says `exhausted`, `incomplete`, the
  session's whole cost and the closing turn's share.
- R5: a closing turn that answers `Verdict: pass` leaves `review.md` as it was.
- R2, the other way: a review that finishes is called once and has no `closing`.
- R11: the review after one that wrote nothing is told which file it had opened.
- R14 on a fixture `cos.db`: `--measure` fails, waits and passes where `spec.md ## Answers,
  câu 2` and `câu 3` say it should.

`--measure` (R14, R15) reads `<COS_DATA_DIR>/cos.db` with `mode=ro`, never through `Data`
(`0076`), the merge time from this unit's `ship.md` under any `<COS_DATA_DIR>/units/*/.cos/`,
and each unit's `review.md` beside it. It takes the first 10 `end` rows of `review` from the
merge on whose `review_md` is not `withheld`, leaving out rows with no `review_md` (a step
the installed copy ran before it carried this unit). One is broken when it ended
`exhausted` and wrote neither `round` nor `incomplete`, or when it says it wrote one and
its unit's `review.md` holds no round whose `Reviewed:` is the head its `start` row
recorded. Any broken: exit 1. Fewer than 10: exit 2, however late (`spec.md ## Answers, câu
2`). Otherwise exit 0. It prints how many ended `exhausted` and each `closing.cost_usd`, and
R15: the median `turns` of `done` review steps whose round is Round 1, and of those whose
round follows a `pass` — over every such row in `cos.db`, not only after the merge; no
threshold. It writes only `<COS_DATA_DIR>/measurements/0085-<stamp>.json`. Run it at a
terminal: inside a step it can read only that step's own data root.

What it does not measure: a real closing turn, its cost after a 40-turn session, or whether a
real session answers it in the shape R4 asks; a review run at a terminal; a step cut short
by the app going down.

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
import statistics
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
COS = REPO / ".claude" / "scripts" / "cos.mjs"
UNIT = "0085_a-review-runs-out-of-turns-and-writes-nothing"
ENOUGH = 10
WRITTEN = ("round", "incomplete")
EXIT_PASS, EXIT_BROKEN, EXIT_ENV = 0, 1, 2
ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)(Z|[+-]\d{2}:?\d{2})?")
ROUND_HEAD = re.compile(r"^## Round (\d+)\s*$")
# `cos.mjs` `ROUND_META`, copied; the proofs do not import the app for `--measure`.
ROUND_META = re.compile(
    r"^Reviewed:\s*([0-9a-f]{7,40})\.?\s+Verdict:\s*(pass|changes-requested|needs-person|incomplete)\.?\s*$",
    re.IGNORECASE,
)


def say(line: str) -> None:
    print(line, flush=True)


def data_root() -> Path:
    return Path(os.environ.get("COS_DATA_DIR") or "~/.cos").expanduser()


# Copied from `scripts/verify_0080.py:71-96`; the proofs do not import each other.
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


def rounds_of(text: str) -> list[dict]:
    """Every `## Round N` above `## Answers`, with its `reviewed` and `verdict` as `cos.mjs` reads them."""
    out: list[dict] = []
    for line in text.splitlines():
        if line.rstrip() == "## Answers":
            break
        head = ROUND_HEAD.match(line)
        if head:
            out.append({"n": int(head.group(1)), "reviewed": None, "verdict": None, "seen": False})
            continue
        if line.startswith("## "):
            if out:
                out[-1]["seen"] = True
            continue
        if out and not out[-1]["seen"] and line.strip():
            out[-1]["seen"] = True
            m = ROUND_META.match(line.strip())
            if m:
                out[-1]["reviewed"], out[-1]["verdict"] = m.group(1).lower(), m.group(2).lower()
    return out


def _round_for(root: Path, unit: str, head: str) -> tuple[dict | None, dict | None]:
    """The round of `unit`'s `review.md` that reviewed `head`, and the round before it."""
    if not head:
        return None, None
    for review in sorted(root.glob(f"units/*/.cos/{unit}/review.md")):
        try:
            rounds = rounds_of(review.read_text(encoding="utf-8"))
        except OSError:
            continue
        for i, r in enumerate(rounds):
            if r["reviewed"] and len(r["reviewed"]) >= 7 and head.lower().startswith(r["reviewed"]):
                return r, (rounds[i - 1] if i else None)
    return None, None


def measure(root: Path, now: datetime | None = None) -> int:
    """R14, R15. Everything it prints, it also writes to one file under `measurements/`."""
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
        rows = conn.execute("SELECT record FROM runs WHERE kind IN ('start', 'end') ORDER BY id").fetchall()
    heads: dict[str, str] = {}
    ends: list[dict] = []
    for (raw,) in rows:
        try:
            r = json.loads(raw)
        except ValueError:
            continue
        if r.get("stage") != "review":
            continue
        if r.get("kind") == "start":
            if r.get("run"):
                heads[str(r["run"])] = str(r.get("head") or "")
            continue
        ends.append(r)

    steps: list[dict] = []
    left_out = {"withheld": 0, "no field": 0}
    for r in ends:
        at = _when(r.get("at"))
        if at is None or at < merged:
            continue
        if r.get("review_md") is None:
            left_out["no field"] += 1
            continue
        if r.get("review_md") == "withheld":
            left_out["withheld"] += 1
            continue
        steps.append(r)
    counted = steps[:ENOUGH]
    judged: list[dict] = []
    for r in counted:
        head = heads.get(str(r.get("run") or ""), "")
        why = ""
        if r.get("outcome") == "exhausted" and r.get("review_md") not in WRITTEN:
            why = f"exhausted, review_md {r.get('review_md')}"
        elif r.get("review_md") in WRITTEN and _round_for(root, str(r.get("unit")), head)[0] is None:
            why = f"review_md {r.get('review_md')}, but no round of its review.md reviewed {head or '(no head)'}"
        judged.append({"unit": r.get("unit"), "at": r.get("at"), "outcome": r.get("outcome"),
                       "review_md": r.get("review_md"), "head": head,
                       "closing_cost_usd": (r.get("closing") or {}).get("cost_usd"), "broken": why})
    broken = [s for s in judged if s["broken"]]
    exhausted = [s for s in judged if s["outcome"] == "exhausted"]
    if broken:
        result, code = "sai", EXIT_BROKEN
    elif len(counted) < ENOUGH:
        result, code = f"chưa đủ: {len(counted)} bước, cần {ENOUGH}", EXIT_ENV
    else:
        result, code = "đạt", EXIT_PASS
    say(f"merged {merged.isoformat()}: {len(counted)} review steps counted "
        f"(withheld, not counted: {left_out['withheld']}; no field, not counted: {left_out['no field']})")
    say(f"  outcome exhausted: {len(exhausted)}")
    for s in exhausted:
        say(f"  closing.cost_usd {s['unit']} at {s['at']}: {s['closing_cost_usd']}")
    for s in broken:
        say(f"  broken: {s['unit']} at {s['at']}: {s['broken']}")

    # R15: every `done` review in the log, joined to the round it wrote by its start's head.
    first, after_pass = [], []
    for r in ends:
        if r.get("outcome") != "done" or not isinstance(r.get("turns"), int):
            continue
        rnd, before = _round_for(root, str(r.get("unit")), heads.get(str(r.get("run") or ""), ""))
        if rnd is None:
            continue
        if rnd["n"] == 1:
            first.append(r["turns"])
        elif before is not None and before["verdict"] == "pass":
            after_pass.append(r["turns"])
    groups = {
        "round 1": {"steps": len(first), "median_turns": statistics.median(first) if first else None},
        "after a pass": {"steps": len(after_pass), "median_turns": statistics.median(after_pass) if after_pass else None},
    }
    for name, g in groups.items():
        say(f"  R15 {name}: {g['steps']} steps, median turns {g['median_turns']}")
    say(f"→ {result}")
    out = root / "measurements" / f"0085-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "merged": merged.isoformat(), "result": result, "counted": len(counted),
        "left_out": left_out, "exhausted": len(exhausted), "steps": judged, "r15": groups,
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


def run(*args: str, cwd: Path = REPO, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), capture_output=True, text=True, cwd=str(cwd),
                          env={**os.environ, **(env or {})})


def r1() -> bool:
    from coscc.policy import grant_for

    g = grant_for("review")
    return claim((g.max_turns, g.max_budget_usd) == (40, 4.0), "R1: review's grant is 40 turns and $4.0",
                 f"{g.max_turns} turns, ${g.max_budget_usd}")


SHA = "a" * 40
HEADER = "# Review: x\nSpec: spec.md. Author: t. Status: {status}.\n\n"


def full_round(n: int, verdict: str, head: str = SHA) -> str:
    return f"## Round {n}\n\nReviewed: {head}. Verdict: {verdict}.\n\n### Findings\n\n- F1 [open] a.py:3 — high — x\n"


def incomplete_round(n: int, head: str = SHA, verdict: str = "incomplete") -> str:
    return (f"## Round {n}\n\nReviewed: {head}. Verdict: {verdict}.\n\n### Reviewed so far\n\n- a.py\n\n"
            "### Findings\n\n- F1 [open] a.py:3 — high — x\n\n### What was not reviewed\n\n- b.py\n")


def chain(d: Path) -> None:
    d.mkdir(parents=True)
    files = {
        "idea.md": "# Idea: x\nAuthor: t. Status: accepted.\n",
        "intent.md": "# Intent: x\nAuthor: t. Type: fix. Status: accepted.\n",
        "spec.md": "# Spec: x\nIntent: intent.md. Author: t. Status: accepted.\n\n## Concerns\n\n- C1. đo rồi.\n",
        "plan.md": "# Plan: x\nIntent: intent.md. Author: t. Status: accepted.\n",
        "impl.md": "# Impl: x\nIntent: intent.md. Plan: plan.md. Author: t. Status: accepted.\n",
        "pr.md": "# PR: x\nPR: https://github.com/o/r/pull/7. Status: accepted.\n",
    }
    for name, body in files.items():
        (d / name).write_text(body, encoding="utf-8")


def loop(tmp: Path) -> bool:
    """R8, R9 through the command itself."""
    ok = True

    def unit_with(name: str, status: str, rounds: list[str]) -> str:
        d = tmp / ".cos" / name
        chain(d)
        (d / "review.md").write_text(HEADER.format(status=status) + "\n".join(rounds), encoding="utf-8")
        return name

    def nxt(unit: str, limit: int = 3) -> str:
        out = run("node", str(COS), "--root", str(tmp), "next", unit, env={"COS_REVIEW_ROUNDS": str(limit)})
        try:
            return str(json.loads(out.stdout).get("action"))
        except ValueError:
            return out.stdout + out.stderr

    a = unit_with("0001_incomplete", "draft", [full_round(1, "changes-requested"), incomplete_round(2)])
    action = nxt(a)
    ok &= claim("finish and accept" not in action and "review round 2 is incomplete" in action and "--repo" in action,
                "R8 (a): next names the incomplete round and asks for --repo, not 'finish and accept review.md'", action)
    ship = run("node", str(COS), "--root", str(tmp), "gate", a, "ship")
    said = ship.stdout + ship.stderr
    ok &= claim(ship.returncode == 1 and 'review.md is "draft"' in said,
                "R8 (a): its ship gate is closed", f"exit {ship.returncode}: {said.strip()}")

    plain = unit_with("0002_plain", "changes-requested", [full_round(1, "changes-requested"), full_round(2, "changes-requested")])
    mixed = unit_with("0003_mixed", "changes-requested",
                      [full_round(1, "changes-requested"), incomplete_round(2), full_round(3, "changes-requested")])
    for limit, want in ((3, "(2 of 3 rounds used)"), (2, "needs a person")):
        got = (nxt(plain, limit), nxt(mixed, limit))
        ok &= claim(all(want in g for g in got),
                    f"R9 ({'b' if limit == 3 else 'c'}): with and without an incomplete round, COS_REVIEW_ROUNDS={limit} says {want}",
                    " | ".join(got))
    return ok


def measure_fixture(tmp: Path) -> bool:
    from coscc.journal import Journal

    head = "b" * 40
    good = HEADER.format(status="changes-requested") + full_round(1, "changes-requested", head)
    cases = (
        ("pass", ["round"] * 7 + ["incomplete"] * 3, "2026-10-20", EXIT_PASS),
        ("exhausted-none", ["round"] * 9 + ["exhausted-none"], "2026-10-20", EXIT_BROKEN),
        ("no-round-for-its-head", ["round"] * 9 + ["round-elsewhere"], "2026-10-20", EXIT_BROKEN),
        ("withheld-and-no-field-left-out", ["round"] * 10 + ["withheld", None], "2026-10-20", EXIT_PASS),
        ("too-few", ["round"] * 9 + ["withheld"], "2026-10-05", EXIT_ENV),
        ("too-few-after-the-deadline", ["round"] * 9, "2026-12-01", EXIT_ENV),
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
            unit = f"00{i:02d}_x"
            d = root / "units" / "slot" / ".cos" / unit
            d.mkdir(parents=True)
            (d / "review.md").write_text(good, encoding="utf-8")
            run_id = f"r{i}"
            j.append({"kind": "start", "workspace": "/w", "unit": unit, "stage": "review", "mode": "manual",
                      "run": run_id, "head": "c" * 40 if value == "round-elsewhere" else head,
                      "at": f"2026-10-02T00:{i:02d}:00+00:00"})
            md = {"exhausted-none": "none", "round-elsewhere": "round"}.get(value, value)
            j.append({"kind": "end", "workspace": "/w", "unit": unit, "stage": "review", "run": run_id,
                      "outcome": "exhausted" if value in ("exhausted-none", "incomplete") else "done",
                      "turns": 12, "at": f"2026-10-02T00:{i:02d}:30+00:00",
                      **({"review_md": md} if md is not None else {}),
                      **({"closing": {"terminal": "completed", "turns": 1, "cost_usd": 0.4}} if md in ("incomplete", "none") else {})})
        # Neither of these is counted: another stage, and a review before the merge.
        j.append({"kind": "end", "workspace": "/w", "unit": "0099_x", "stage": "plan", "outcome": "exhausted",
                  "at": "2026-10-05T00:00:00+00:00"})
        j.append({"kind": "end", "workspace": "/w", "unit": "0099_x", "stage": "review", "outcome": "exhausted",
                  "review_md": "none", "at": "2026-09-30T00:00:00+00:00"})
        code = measure(root, datetime.fromisoformat(now + "T00:00:00+00:00"))
        ok &= claim(code == want, f"R14: --measure on a fixture cos.db, case {case}: exit {want}", f"exit {code}")
    return ok


def _tool_use(path: str):
    from claude_agent_sdk import AssistantMessage, ToolUseBlock

    return AssistantMessage(content=[ToolUseBlock(id="t1", name="Read", input={"file_path": path})],
                            model="m", message_id="m-read")


class _Scripted:
    """The session, one scripted answer per call: `(first, closing)` for each step in turn."""

    def __init__(self, tree: Path):
        self.tree = tree
        self.calls: list[dict] = []
        self.plan: list = []

    def in_flight(self):
        return []

    async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
        self.calls.append({"text": text, "session_id": session_id, "max_turns": max_turns, **kw})
        answer = self.plan.pop(0)
        for item in answer(text, session_id, kw):
            yield item


def _head_in(text: str) -> str:
    m = re.search(r"Reviewed: ([0-9a-f]{40})\. Verdict: incomplete", text) or re.search(r"\n    ([0-9a-f]{40})\n", text)
    return m.group(1) if m else ""


def through_the_service(tmp: Path) -> bool:
    """R2, R4-R7, R11, end to end through `Service.run_step`."""
    from coscc import board
    from coscc.config import Config
    from coscc.service import Service

    repo, tree = tmp / "work" / "proj", tmp / "tree"
    for where in (repo, tree):
        where.mkdir(parents=True)
        run("git", "init", "-q", "-b", "main", cwd=where)
        run("git", "-c", "user.name=T", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
            "commit", "-q", "--allow-empty", "-m", "first", cwd=where)
    head = run("git", "rev-parse", "HEAD", cwd=tree).stdout.strip()
    data = tmp / "data"
    sessions = _Scripted(tree)
    service = Service(Config(workspaces=(str(repo),), working_dir=str(tmp / "work"), data_dir=str(data)), sessions)
    before = HEADER.format(status="changes-requested") + full_round(1, "changes-requested")
    opened = str(tree / "coscc" / "x.py")

    def unit(slug: str) -> tuple[str, Path]:
        made = asyncio.run(service.create_unit(str(repo), slug, "some words"))
        d = Path(made["path"])
        shutil.rmtree(d)
        chain(d)
        (d / "review.md").write_text(before, encoding="utf-8")
        return made["unit"], d / "review.md"

    where = {"path": str(tree), "branch": "fix/a-problem", "base": None}

    def step(name: str) -> list:
        async def go():
            with mock.patch.object(Service, "_worktree", mock.AsyncMock(return_value=where)), \
                 mock.patch.object(board, "gate", mock.AsyncMock(return_value=(True, "open"))), \
                 mock.patch.object(Service, "_post_new_rounds", mock.AsyncMock(return_value=[])):
                return [i async for i in service.run_step(str(repo), name, "review")]
        return asyncio.run(go())

    def ends(name: str) -> list[dict]:
        out = []
        with sqlite3.connect(f"file:{data / 'cos.db'}?mode=ro", uri=True) as conn:
            for (raw,) in conn.execute("SELECT record FROM runs WHERE kind = 'end' ORDER BY id"):
                r = json.loads(raw)
                if r.get("unit") == name and r.get("stage") == "review":
                    out.append(r)
        return out

    def runs_out(text, session_id, kw):
        if kw.get("step") is not None and kw["step"].recorder is not None:
            kw["step"].recorder.message(_tool_use(opened))
        yield ("chunk", "Tôi hết lượt, chưa kết luận được.")
        yield ("done", {"session_id": "s1", "terminal_reason": "max_turns",
                        "cost": {"turns": 41, "cost_usd": 1.00}})

    def closes(verdict: str):
        def answer(text, session_id, kw):
            reply = HEADER.format(status="draft") + incomplete_round(2, _head_in(text), verdict)
            yield ("chunk", reply)
            yield ("done", {"session_id": "s1", "terminal_reason": "budget_exhausted",
                            "cost": {"turns": 1, "cost_usd": 1.40}})
        return answer

    def finishes(text, session_id, kw):
        yield ("chunk", HEADER.format(status="changes-requested") + full_round(2, "changes-requested", _head_in(text)))
        yield ("done", {"session_id": "s2", "terminal_reason": "success", "cost": {"turns": 9, "cost_usd": 0.3}})

    ok = True
    # R2, R4, R6, R7.
    a, da = unit("closing-writes")
    sessions.plan = [runs_out, closes("incomplete")]
    out = step(a)
    first, closing = sessions.calls[-2:]
    ok &= claim(len(sessions.calls) == 2, "R2: a review stopped at max_turns is reopened once", str(len(sessions.calls)))
    ok &= claim(
        closing["session_id"] == "s1" and closing["tools"] == [] and closing["max_turns"] == 1
        and callable(closing.get("can_use_tool")) and closing.get("step") is not None
        and closing["step"] is not first.get("step"),
        "R2: on its own session id, with no tools, one turn, a refusing callback and a handle of its own",
        str({k: closing.get(k) for k in ("session_id", "tools", "max_turns")}),
    )
    text = da.read_text(encoding="utf-8")
    ok &= claim(full_round(1, "changes-requested").rstrip() in text
                and text.index(f"## Round 2\n\nReviewed: {head}. Verdict: incomplete.") > text.index("## Round 1"),
                "R4: Round 2 is appended as incomplete for the head the step ran on, Round 1 byte for byte")
    ok &= claim("Status: draft." in text.split("## Round 1")[0], "R4: under a draft header")
    got = ends(a)
    end = got[-1] if got else {}
    ok &= claim(
        (out[-1][1].get("outcome"), end.get("outcome"), end.get("review_md")) == ("exhausted", "exhausted", "incomplete"),
        "R6: the end row says exhausted and review_md incomplete", str(end),
    )
    ok &= claim(end.get("cost_usd") == 1.4 and end.get("closing") == {"terminal": "budget_exhausted", "turns": 1, "cost_usd": 0.4},
                "R7: cost_usd is the session's whole 1.4, closing.cost_usd its 0.4, turns 1, terminal budget_exhausted",
                str({"cost_usd": end.get("cost_usd"), "closing": end.get("closing")}))

    # R5, then R11 on the same unit.
    b, db = unit("closing-says-pass")
    sessions.plan = [runs_out, closes("pass")]
    step(b)
    got = ends(b)
    end = got[-1] if got else {}
    ok &= claim(db.read_text(encoding="utf-8") == before, "R5: a closing turn that says pass leaves review.md byte for byte")
    ok &= claim(end.get("review_md") == "none" and "closing" in end, "R5: its end row says none, and closing is there", str(end))
    sessions.plan = [finishes]
    step(b)
    prompt = sessions.calls[-1]["text"]
    ok &= claim("coscc/x.py" in prompt and "no conclusion about any of them was written" in prompt,
                "R11: the next review is told the file the last one opened and concluded nothing about")

    # R2, the other way.
    c, _ = unit("review-finishes")
    n = len(sessions.calls)
    sessions.plan = [finishes]
    step(c)
    got = ends(c)
    end = got[-1] if got else {}
    ok &= claim(len(sessions.calls) == n + 1, "R2: a review that finishes is called once", str(len(sessions.calls) - n))
    ok &= claim(end.get("review_md") == "round" and "closing" not in end,
                "R6: its end row says round and has no closing", str(end))
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--measure", action="store_true", help="R14, R15, on the real <COS_DATA_DIR>")
    args = parser.parse_args()
    if args.measure:
        return measure(data_root())
    for tool in ("node", "git"):
        if not shutil.which(tool):
            say(f"environment: {tool} is needed")
            return EXIT_ENV
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        ok = r1()
        ok &= loop(tmp / "loop")
        ok &= through_the_service(tmp / "service")
        ok &= measure_fixture(tmp / "measure")
    say("all claims pass" if ok else "some claims failed")
    return EXIT_PASS if ok else EXIT_BROKEN


if __name__ == "__main__":
    sys.exit(main())
