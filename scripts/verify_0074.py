"""`0074` proof: the backlog is ordered by value and effort, and changes nothing but the display.

Plain: no session, no quota, no network. A temporary data root and store, the app driven
in-process over ASGI, the session replaced by a stand-in. Needs `node`; missing is exit 2.
Each claim prints PASS or FAIL: R1, R4, R6, R8 with `spec.md ## Answers, câu 1`, R10, R14,
R15 (every `cos.mjs gate` and `next` answer the same before and after the three kinds of
record), R16, R18, R19, and `--measure` on a fixture `cos.db` (pass, fail, window open).

`--measure` (R20) reads `<COS_DATA_DIR>/cos.db` with `mode=ro`, never through `Data`, and
the merge time from this unit's `ship.md` under any `<COS_DATA_DIR>/units/*/.cos/`. In the
14 days after it, the first `start` of every `(workspace, unit)` — `integrate` and
`estimate` excluded — must carry a `shortlist.rank`: `đạt` (exit 0) with at least one unit
and none `null`, `trượt` (exit 1) otherwise. No merge line, or a window still open, is exit 2.
It writes only `<COS_DATA_DIR>/measurements/0074-<stamp>.json`. It sees only steps the app
ran (`spec.md ## Answers, câu 3`); a terminal run leaves no `start`.

Exit codes: 0 pass, 1 broken, 2 environment not ready.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
COS = REPO / ".claude" / "scripts" / "cos.mjs"
UNIT = "0074_the-backlog-is-ordered-by-feel"
WINDOW = timedelta(days=14)
EXIT_PASS, EXIT_BROKEN, EXIT_ENV = 0, 1, 2
ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)(Z|[+-]\d{2}:?\d{2})?")
EXCLUDED = ("integrate", "estimate")


def say(line: str) -> None:
    print(line, flush=True)


def data_root() -> Path:
    return Path(os.environ.get("COS_DATA_DIR") or "~/.cos").expanduser()


# Copied from `scripts/verify_0061.py:87-112`; the proofs do not import each other.
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
    """R20. Everything it prints, it also writes to one file under `measurements/`."""
    now = now or datetime.now(timezone.utc)
    ships = sorted(root.glob(f"units/*/.cos/{UNIT}/ship.md"))
    installed = next((t for t in (shipped_at(p) for p in ships) if t is not None), None)
    if installed is None:
        say(f"no merge line in any {UNIT}/ship.md under {root}/units: nothing to measure yet")
        return EXIT_ENV
    end = installed + WINDOW
    if now < end:
        say(f"installed {installed.isoformat()}; the window closes {end.isoformat()}, not yet")
        return EXIT_ENV
    db = root / "cos.db"
    if not db.is_file():
        say(f"no {db}")
        return EXIT_ENV
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        rows = conn.execute("SELECT record FROM runs WHERE kind IN ('start', 'shortlist') ORDER BY id").fetchall()
    first: dict[tuple[str, str], dict] = {}
    lists: list[dict] = []
    for (raw,) in rows:
        try:
            r = json.loads(raw)
        except ValueError:
            continue
        at = _when(r.get("at"))
        if r.get("kind") == "shortlist":
            lists.append(r)
            continue
        if at is None or not (installed <= at < end) or r.get("stage") in EXCLUDED:
            continue
        first.setdefault((str(r.get("workspace") or ""), str(r.get("unit") or "")), r)
    started = [
        {"workspace": k[0], "unit": k[1], "stage": r.get("stage"), "at": r.get("at"),
         "rank": (r.get("shortlist") or {}).get("rank"), "record": (r.get("shortlist") or {}).get("record")}
        for k, r in first.items()
    ]
    outside = [s for s in started if s["rank"] is None]
    result = "đạt" if started and not outside else "trượt"
    say(f"installed {installed.isoformat()}, window to {end.isoformat()}: {len(started)} units started, "
        f"{len(outside)} outside the shortlist → {result}")
    for s in started:
        say(f"  {s['unit']} ({s['stage']}, {s['at']}): rank {s['rank']}")
    # `plan.md` Risk 8: a shortlist whose every place drifts still counts, so say how the
    # lists in use were written; drift itself is the board's reading, not re-derived here.
    say(f"{len(lists)} shortlist records in the run log; read their `lệch` marks on the board")
    code = EXIT_PASS if result == "đạt" else EXIT_BROKEN
    out = root / "measurements" / f"0074-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "installed": installed.isoformat(), "window_end": end.isoformat(), "result": result,
        "started": started, "shortlists": len(lists), "exit": code,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    say(f"wrote {out}")
    return code


# ---------------------------------------------------------------------------
# The proof
# ---------------------------------------------------------------------------


def claim(ok: bool, text: str, detail: str = "") -> bool:
    say(f"{'PASS' if ok else 'FAIL'}  {text}{': ' + detail if detail and not ok else ''}")
    return ok


class _Sessions:
    """Replies with whatever `text` holds; waits on `gate` when one is set."""

    def __init__(self) -> None:
        self.text, self.gate, self.calls = "", None, 0

    def in_flight(self):
        return []

    async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        yield ("chunk", self.text)
        yield ("done", {"session_id": "proof", "cost": {"cost_usd": 0.01, "turns": 1}})


ANSWERS = "\n## Answers\n\n### {head}\nDecided by: proof. Date: 2026-09-25. Via: product.\n\nx\n"


def gate_answers(store: Path, units: list[str], stages: list[str]) -> list[tuple]:
    out = []
    for u in units:
        for args in [("gate", u, s) for s in stages] + [("next", u)]:
            r = subprocess.run(["node", str(COS), "--root", str(store), *args],
                               capture_output=True, text=True, cwd=str(REPO))
            out.append((args, r.returncode, r.stdout, r.stderr))
    return out


def tree_hash(root: Path) -> dict:
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


async def proof(tmp: Path) -> bool:
    import httpx

    from coscc import backlog
    from coscc.api import build
    from coscc.config import Config
    from coscc.journal import Journal

    repo = tmp / "work" / "proj"
    repo.mkdir(parents=True)
    app = build(Config(workspaces=(str(repo),), working_dir=str(tmp / "work"), data_dir=str(tmp / "data")))
    service = app.state.service
    sessions = _Sessions()
    service.sessions = sessions
    cwd = str(repo)
    ok = True
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        async def make(slug: str, **files: str) -> str:
            made = (await client.post("/api/units", json={"cwd": cwd, "slug": slug, "brief": "words"})).json()
            for name, text in files.items():
                (Path(made["path"]) / f"{name}.md").write_text(text, encoding="utf-8")
            return made["unit"]

        intent = "# Intent: x\nAuthor: t. Type: feat. Status: accepted.\n\n## Problem\n\np\n"
        a = await make("idea-only")
        b = await make("has-intent", intent=intent)
        await make("finished", intent=intent, spec="# s\nStatus: accepted.\n", plan="# p\nStatus: done.\n")
        await make("dropped", intent=intent + ANSWERS.format(head="Dropped"))
        e = await make("paused", intent=intent + ANSWERS.format(head="Paused"))
        await make("rejected", idea="# Idea: r\nAuthor: t. Status: rejected.\n")
        store = service._units_root(cwd)
        journal = Journal(tmp / "work", tmp / "data")
        key = service._journal_key(cwd)

        async def board() -> dict:
            return (await client.get("/api/board", params={"cwd": cwd})).json()

        async def post(path: str, **body) -> httpx.Response:
            return await client.post(f"/api/backlog/{path}", json={"cwd": cwd, **body})

        async def spec_start() -> dict:
            await client.post("/api/board/run", json={"cwd": cwd, "unit": b, "stage": "spec"})
            return journal.records(key, b, kind="start")[-1]

        data = await board()
        ok &= claim(data["backlog"]["backlog"] == [a, b, e], "R1: the backlog is the idea-only and the paused unit, "
                    "plus the one with an intent", str(data["backlog"]["backlog"]))
        ok &= claim(bool(data["backlog"]["propose_warning"]), "R19: /api/board carries the proposal's warning")

        found = {f"000{i}_f": {"cost_usd": float(i), "turns": 10 * i} for i in range(1, 7)}
        table = {("0001_f",): "S", ("0003_f",): "M", ("0005_f",): "L", ("0001_f", "0003_f"): "S",
                 ("0001_f", "0006_f"): "M"}
        got = {k: backlog.effort_from(list(k), found)["effort"] for k in table}
        ok &= claim(got == table, "R4: the band matches a table worked by hand", str(got))

        sessions.text = "no status line"
        first = await spec_start()
        ok &= claim(first.get("shortlist") == {"rank": None, "of": None, "record": None},
                    "R14: a start before any shortlist has rank null", str(first.get("shortlist")))

        stages = [s["name"] for s in json.loads(subprocess.run(
            ["node", str(COS), "--root", str(store), "status", "--json"], capture_output=True, text=True,
            cwd=str(REPO)).stdout)["stages"]]
        names = sorted(p.name for p in (store / ".cos").iterdir() if p.is_dir())
        before_gates, before_files = gate_answers(store, names, stages), tree_hash(store)

        journal.append({"kind": "estimate-value", "workspace": key, "unit": a, "value": 2, "effort": "L",
                        "effort_source": "guess", "basis": "bớt chi phí", "by": "agent:s1"})
        r1 = await post("estimate", unit=a, value=5, effort="S", basis="của tôi", by="Leif")
        journal.append({"kind": "estimate-value", "workspace": key, "unit": a, "value": 1, "effort": "M",
                        "effort_source": "guess", "basis": "bớt chi phí", "by": "agent:s2"})
        top = next(x for x in (await board())["backlog"]["order"] if x["unit"] == a)
        ok &= claim(r1.status_code == 200 and top["estimate"]["by"] == "Leif" and top["agent_differs"]["by"] == "agent:s2",
                    "R6: agent → person → agent keeps the person's, and shows the agent's beside it", str(top))

        await post("estimate", unit=b, value=1, effort="L", basis="nhỏ", by="Leif")
        dep = await post("relation", unit=a, other=b, type="phụ thuộc", op="add", reason="cần b", by="Leif")
        cycle = await post("relation", unit=b, other=a, type="phụ thuộc", op="add", reason="vòng", by="Leif")
        order = [x["unit"] for x in (await board())["backlog"]["order"]]
        ok &= claim(dep.status_code == 200 and cycle.status_code == 400 and order.index(b) < order.index(a),
                    "R8, câu 1: a value-5 unit that depends on a value-1 unit comes after it; the cycle is refused",
                    f"{dep.status_code} {cycle.status_code} {order}")

        refused = [
            await post("shortlist", units=[a, a], reason="r", by="Leif"),
            await post("shortlist", units=[a, "0003_finished"], reason="r", by="Leif"),
            await post("shortlist", units=[e], reason="r", by="Leif"),
            await post("shortlist", units=[a], reason="r", by="agent:x"),
        ]
        taken = await post("shortlist", units=[a, b], reason="thứ tự của Leif", by="Leif")
        entries = (await board())["backlog"]["shortlist"]
        ok &= claim(all(r.status_code == 400 for r in refused) and taken.status_code == 200
                    and all(x["drift"] for x in entries),
                    "R10: duplicates, a finished unit, no estimate and an agent's name are refused; "
                    "a shortlist that drifts at every place is taken and marked",
                    f"{[r.status_code for r in refused]} {taken.status_code} {[x['drift'] for x in entries]}")

        after_gates, after_files = gate_answers(store, names, stages), tree_hash(store)
        ok &= claim(before_gates == after_gates and len(before_gates) == len(names) * (len(stages) + 1),
                    f"R15: {len(before_gates)} cos.mjs gate/next answers are the same before and after")
        ok &= claim(before_files == after_files, "R16: no file of the store changed")

        second = await spec_start()
        s = second.get("shortlist") or {}
        ok &= claim((s.get("rank"), s.get("of")) == (2, 2) and (s.get("record") or {}).get("n") == 1,
                    "R14: a start after the shortlist says where the unit stood", str(s))

        sessions.text = "```json\n" + json.dumps({"units": [
            {"unit": e, "value": 3, "effort": "M", "similar": [], "basis": "nỗi đau đã gặp thật"},
            {"unit": b, "value": 2, "effort": "S", "similar": [], "basis": "không nêu vế nào"},
        ]}) + "\n```"
        streamed = await client.post("/api/backlog/propose", json={"cwd": cwd})
        done = json.loads(streamed.text.strip().splitlines()[-1]).get("estimate") or {}
        sessions.text = "not json"
        bad = json.loads((await client.post("/api/backlog/propose", json={"cwd": cwd})).text.strip().splitlines()[-1])
        gate = asyncio.Event()
        sessions.gate, calls = gate, sessions.calls
        running = service.propose_estimates(cwd)
        task = asyncio.create_task(running.__anext__())
        for _ in range(200):
            await asyncio.sleep(0.01)
            if sessions.calls > calls:
                break
        second_press = await client.post("/api/backlog/propose", json={"cwd": cwd})
        gate.set()
        await task
        async for _ in running:
            pass
        sessions.gate = None
        ok &= claim(done.get("written") == 1 and [x["unit"] for x in done.get("rejected") or []] == [b]
                    and (bad.get("estimate") or {}).get("outcome") == "failed"
                    and second_press.status_code == 400 and sessions.calls == calls + 1,
                    "R18: one unit without a goal is dropped alone; not-JSON writes nothing; a second press is refused",
                    f"{done} {bad} {second_press.status_code}")
    return ok


def measure_fixture(tmp: Path) -> bool:
    from coscc.journal import Journal

    ok = True
    for case, rank, now, want in (
        ("pass", 1, "2026-10-20T00:00:00+00:00", EXIT_PASS),
        ("fail", None, "2026-10-20T00:00:00+00:00", EXIT_BROKEN),
        ("open", 1, "2026-10-05T00:00:00+00:00", EXIT_ENV),
    ):
        root = tmp / case
        ship = root / "units" / "slot" / ".cos" / UNIT / "ship.md"
        ship.parent.mkdir(parents=True)
        ship.write_text("# Ship\nStatus: accepted.\n\n## What went out\n\nMerged 2026-10-01T10:00:00Z.\n",
                        encoding="utf-8")
        j = Journal(root / "work", root)
        j.append({"kind": "start", "workspace": "/w", "unit": "0080_x", "stage": "intent", "mode": "manual",
                  "at": "2026-10-02T00:00:00+00:00", "shortlist": {"rank": rank, "of": 3, "record": None}})
        j.append({"kind": "start", "workspace": "/w", "unit": "", "stage": "estimate", "mode": "manual",
                  "at": "2026-10-02T00:00:00+00:00"})
        code = measure(root, datetime.fromisoformat(now))
        ok &= claim(code == want, f"--measure on a fixture cos.db, case {case}: exit {want}", f"exit {code}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--measure", action="store_true", help="R20, on the real <COS_DATA_DIR>")
    args = parser.parse_args()
    if args.measure:
        return measure(data_root())
    if not shutil.which("node"):
        say("environment: node is needed")
        return EXIT_ENV
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        ok = asyncio.run(proof(tmp / "proof"))
        ok &= measure_fixture(tmp / "measure")
    say("all claims pass" if ok else "some claims failed")
    return EXIT_PASS if ok else EXIT_BROKEN


if __name__ == "__main__":
    sys.exit(main())
