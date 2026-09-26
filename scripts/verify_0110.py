"""`0110` proof: what a shipped unit's `impl` and `review` cost, before and after `impl` is
handed the findings earlier reviews raised on its files.

Plain: no session, no quota, no network. A temporary `cos.db` whose `runs` rows are written
by `coscc.journal.Journal` itself, with its clock patched, and `--measure` run on it. Each
case prints PASS or FAIL:

- no `ship` `done` of `0110` yet → `chưa merge`, exit 2;
- 10 units after the merge at ≤ $7.00 → `đạt`, exit 0; over → `không đạt`, exit 1;
- 9 units → `chưa đủ mẫu (n=9)`, exit 2;
- R7 counts a `start` of `impl` whose `prior_findings` carries no `error`;
- a unit whose first `impl` started before the merge is only under `tham khảo`;
- `baseline` drops a unit with no `impl` `done`, and reproduces R2's numbers.

`--measure --workspace <path|name> --window baseline|result` is R1 (`result`) and R2
(`baseline`). It reads `<COS_DATA_DIR>/cos.db` with `mode=ro`, never through `Data`
(`0076`), does not import `coscc`, and writes nothing. `--workspace` matches a row's
`workspace` — the checkout's resolved path — whole or by its last component. A row's time is
`record.at`, its outcome `record.outcome`, its cost `record.cost_usd`.

- A unit's cost: the sum of `cost_usd` of every `end` `done` of `impl` and `review` of that
  unit in that workspace, whenever it ran (`intent.md ## Answers, câu 1`). Its `impl` count
  is how many of those `end` rows are `impl`; a rerun is every one past the first.
- `baseline` (R2): units with an `end` `ship` `done` in [`BASELINE_FROM`, `BASELINE_TO`],
  both ends included, and at least one `impl` `done` (`spike.md ## U1`). Exit 0 only when
  n, the mean, the distribution and the reruns are exactly `BASELINE`'s; 1 otherwise.
- `result` (R1): the merge is the `at` of the first `end` `ship` `done` of a unit named
  `0110_*`. Scored: units with an `end` `ship` `done` after it and at or before
  `RESULT_TO`, whose first `start` of `impl` is after it, with at least one `impl` `done`
  (`spec.md ## Answers, câu 2`). `tham khảo`, not scored: the same without the condition on
  the first `start`. The last line is `đạt` (exit 0), `không đạt` (exit 1) or
  `chưa đủ mẫu (n=<n>)` (exit 2).

Run it at a terminal: inside a step `COS_DATA_DIR` is that step's own scratch root, and it
reads an empty database (`.claude/rules/coscc-sessions.md`).

Exit codes: 0 pass, 1 broken, 2 environment not ready.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sqlite3
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
UNIT_PREFIX = "0110_"
COSTED = ("impl", "review")
ENOUGH = 10          # R1, `intent.md ## Answers, câu 7`
TARGET = 7.00        # R1: the mean a unit may cost, in dollars
BASELINE_FROM = "2026-09-24T00:00:00+00:00"   # R2
BASELINE_TO = "2026-09-26T07:45:47+00:00"     # R2: when `0054` shipped
RESULT_TO = "2026-10-16T23:59:59+00:00"       # R1
# R2, `intent.md ## Answers, câu 1` and `câu 3`.
BASELINE = {"n": 47, "mean": "9.70", "impls": "1:13 2:20 3:9 4:3 5:2", "reruns": 55}
EXIT_PASS, EXIT_BROKEN, EXIT_ENV = 0, 1, 2


def say(line: str) -> None:
    print(line, flush=True)


def data_root() -> Path:
    return Path(os.environ.get("COS_DATA_DIR") or "~/.cos").expanduser()


def _when(at: object) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(at))
    except (TypeError, ValueError):
        return None
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def records(db: Path, workspace: str) -> list[dict]:
    """Every `start` and `end` row of `workspace`, in the order they were written."""
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT record FROM runs WHERE kind IN ('start','end') ORDER BY id"
        ).fetchall()
    out: list[dict] = []
    for (raw,) in rows:
        try:
            r = json.loads(raw)
        except ValueError:
            continue
        ws = str(r.get("workspace") or "")
        if workspace in (ws, Path(ws).name) and _when(r.get("at")) is not None:
            out.append(r)
    return out


def per_unit(rows: list[dict]) -> dict[str, dict]:
    """Unit → its `ship` `done` times, first `impl` start, cost and `impl` count."""
    units: dict[str, dict] = {}
    for r in rows:
        u = units.setdefault(str(r.get("unit") or ""), {
            "ships": [], "first_impl": None, "impl": 0.0, "review": 0.0, "impls": 0,
            "impl_starts": 0, "impl_starts_recorded": 0,
        })
        at, stage = _when(r.get("at")), r.get("stage")
        if r.get("kind") == "start" and stage == "impl":
            u["first_impl"] = u["first_impl"] or at
            u["impl_starts"] += 1
            # A record carrying `error` is a step that was handed nothing.
            pf = r.get("prior_findings")
            u["impl_starts_recorded"] += isinstance(pf, dict) and "error" not in pf
        if r.get("kind") != "end" or r.get("outcome") != "done":
            continue
        if stage == "ship":
            u["ships"].append(at)
        elif stage in COSTED:
            u[stage] += float(r.get("cost_usd") or 0)
            u["impls"] += stage == "impl"
    return units


def shipped_in(units: dict[str, dict], after: datetime, until: datetime, inclusive: bool) -> list[str]:
    """Units with a `ship` `done` in the window and at least one `impl` `done`."""
    def inside(t: datetime) -> bool:
        return (after <= t if inclusive else after < t) and t <= until
    return sorted(n for n, u in units.items() if u["impls"] and any(inside(t) for t in u["ships"]))


def summary(units: dict[str, dict], names: list[str]) -> dict:
    n = len(names)
    hist: dict[int, int] = {}
    for name in names:
        hist[units[name]["impls"]] = hist.get(units[name]["impls"], 0) + 1

    def mean(key: str) -> float:
        return sum(units[x][key] for x in names) / n if n else 0.0
    return {
        "n": n,
        "mean": f"{sum(units[x]['impl'] + units[x]['review'] for x in names) / n:.2f}" if n else "—",
        "impl": f"{mean('impl'):.2f}" if n else "—",
        "review": f"{mean('review'):.2f}" if n else "—",
        "impls": " ".join(f"{k}:{v}" for k, v in sorted(hist.items())),
        "reruns": sum(units[x]["impls"] - 1 for x in names),
        # R7: every `start` of `impl` of the unit carries `prior_findings`.
        "recorded": sum(
            1 for x in names
            if units[x]["impl_starts"] and units[x]["impl_starts_recorded"] == units[x]["impl_starts"]
        ),
    }


def show(label: str, s: dict) -> None:
    say(f"{label}: n {s['n']}; trung bình ${s['mean']} (impl ${s['impl']}, review ${s['review']}); "
        f"số lần impl {s['impls'] or '—'}; chạy lại {s['reruns']}")


def measure(root: Path, workspace: str, window: str) -> int:
    if not workspace or window not in ("baseline", "result"):
        say("need --workspace and --window baseline|result")
        return EXIT_ENV
    db = root / "cos.db"
    if not db.is_file():
        say(f"no {db}")
        return EXIT_ENV
    units = per_unit(records(db, workspace))

    if window == "baseline":
        names = shipped_in(units, _when(BASELINE_FROM), _when(BASELINE_TO), inclusive=True)
        s = summary(units, names)
        say(f"workspace {workspace}; baseline: ship done in [{BASELINE_FROM}, {BASELINE_TO}], "
            f"with an impl done")
        show("baseline", s)
        got = {k: s[k] for k in BASELINE}
        if got == BASELINE:
            say("khớp R2")
            return EXIT_PASS
        say(f"không khớp R2: muốn {BASELINE}, được {got}")
        return EXIT_BROKEN

    merges = sorted(t for n, u in units.items() if n.startswith(UNIT_PREFIX) for t in u["ships"])
    if not merges:
        say("chưa merge")
        return EXIT_ENV
    merge, until = merges[0], _when(RESULT_TO)
    literal = shipped_in(units, merge, until, inclusive=False)
    scored = [n for n in literal if units[n]["first_impl"] and units[n]["first_impl"] > merge]
    s = summary(units, scored)
    say(f"workspace {workspace}; merge {merge.isoformat()}; ship done after it, up to {RESULT_TO}")
    show("kết luận (impl đầu sau merge)", s)
    say(f"  mọi start impl mang prior_findings: {s['recorded']} / {s['n']}")
    show("tham khảo (mọi unit ship sau merge)", summary(units, literal))
    if datetime.now(timezone.utc) <= until:
        say(f"cửa sổ còn mở tới {RESULT_TO}")
    if s["n"] < ENOUGH:
        say(f"chưa đủ mẫu (n={s['n']})")
        return EXIT_ENV
    if float(s["mean"]) <= TARGET:
        say("đạt")
        return EXIT_PASS
    say("không đạt")
    return EXIT_BROKEN


# ---------------------------------------------------------------------------
# The proof
# ---------------------------------------------------------------------------


def claim(ok: bool, text: str, detail: str = "") -> bool:
    say(f"{'PASS' if ok else 'FAIL'}  {text}{': ' + detail if detail and not ok else ''}")
    return ok


class _Fixture:
    """`runs` rows written by `Journal` itself, each at the time it is told."""

    def __init__(self, data: Path) -> None:
        sys.path.insert(0, str(REPO))
        from coscc import journal as journal_mod

        self.mod = journal_mod
        self.journal = journal_mod.Journal(data / "work" / "proj", data)
        self.key = str((data / "work" / "proj").resolve())

    def row(self, at: datetime, unit: str, stage: str, kind: str, **extra: object) -> None:
        saved = self.mod._now
        self.mod._now = lambda: at.isoformat(timespec="seconds")
        try:
            if kind == "start":
                self.journal.started(self.key, unit, stage, "manual", **extra)
            else:
                self.journal.finished(self.key, unit, stage, kind, **extra)
        finally:
            self.mod._now = saved

    def unit(self, t: datetime, unit: str, impls: int, impl_cost: float, review_cost: float,
             ship: bool = True, **start: object) -> datetime:
        """`impls` rounds of `impl` then `review`, the cost split evenly, then `ship`."""
        for _ in range(impls):
            self.row(t, unit, "impl", "start", **start)
            t += timedelta(minutes=1)
            self.row(t, unit, "impl", "done", cost_usd=impl_cost / impls)
            t += timedelta(minutes=1)
            self.row(t, unit, "review", "start")
            t += timedelta(minutes=1)
            self.row(t, unit, "review", "done", cost_usd=review_cost / impls)
            t += timedelta(minutes=1)
        if ship:
            self.row(t, unit, "ship", "start")
            t += timedelta(minutes=1)
            self.row(t, unit, "ship", "done")
            t += timedelta(minutes=1)
        return t


def _run(build, window: str) -> tuple[int, list[str]]:
    with tempfile.TemporaryDirectory() as d:
        data = Path(d) / "data"
        data.mkdir()
        build(_Fixture(data))
        out = io.StringIO()
        with redirect_stdout(out):
            code = measure(data, "proj", window)
    return code, out.getvalue().splitlines()


MERGE = datetime(2026, 9, 28, tzinfo=timezone.utc)


def _after(n: int, cost: float, early: bool = False, errors: int = 0):
    def build(f: _Fixture) -> None:
        if early:
            # Its first `impl` before the merge, its `ship` after it.
            f.unit(MERGE - timedelta(hours=2), "0200_early", 1, 50.0, 0.0, ship=False)
            f.unit(MERGE + timedelta(hours=1), "0200_early", 1, 50.0, 0.0)
        # Its `ship` `done` a minute before `MERGE`.
        f.unit(MERGE - timedelta(minutes=6), "0110_x", 1, 5.0, 2.0)
        t = MERGE + timedelta(hours=2)
        for i in range(n):
            record = {"bytes": 0, "error": "OSError: x"} if i < errors else {"bytes": 0}
            t = f.unit(t, f"{300 + i:04d}_u", 1 + i % 2, cost - 2.0, 2.0, prior_findings=record)
    return build


def prove() -> int:
    ok = True
    code, lines = _run(lambda f: f.unit(MERGE, "0200_u", 1, 5.0, 2.0), "result")
    ok &= claim(code == EXIT_ENV and lines[-1:] == ["chưa merge"], "no 0110 ship → chưa merge, exit 2",
                f"exit {code}, {lines[-1:]}")
    code, lines = _run(_after(10, 6.50), "result")
    ok &= claim(code == EXIT_PASS and lines[-1] == "đạt", "10 units at $6.50 → đạt, exit 0",
                f"exit {code}, {lines}")
    ok &= claim(any(x.strip() == "mọi start impl mang prior_findings: 10 / 10" for x in lines),
                "R7 is counted from the start rows", f"{lines}")
    code, lines = _run(_after(10, 6.50, errors=1), "result")
    ok &= claim(any(x.strip() == "mọi start impl mang prior_findings: 9 / 10" for x in lines),
                "a start whose record carries error is not counted for R7", f"{lines}")
    code, lines = _run(_after(10, 7.50), "result")
    ok &= claim(code == EXIT_BROKEN and lines[-1] == "không đạt", "10 units at $7.50 → không đạt, exit 1",
                f"exit {code}, {lines}")
    code, lines = _run(_after(9, 6.50), "result")
    ok &= claim(code == EXIT_ENV and lines[-1] == "chưa đủ mẫu (n=9)", "9 units → chưa đủ mẫu (n=9), exit 2",
                f"exit {code}, {lines}")
    code, lines = _run(_after(10, 6.50, early=True), "result")
    scored = next((x for x in lines if x.startswith("kết luận")), "")
    reference = next((x for x in lines if x.startswith("tham khảo")), "")
    ok &= claim(code == EXIT_PASS and "n 10;" in scored and "n 11;" in reference,
                "a unit whose first impl ran before the merge is only under tham khảo",
                f"exit {code}, {lines}")

    def baseline(f: _Fixture) -> None:
        t = _when(BASELINE_FROM)
        i = 0
        for impls, count in ((1, 13), (2, 20), (3, 9), (4, 3), (5, 2)):
            for _ in range(count):
                t = f.unit(t, f"{i:04d}_b", impls, 7.00, 2.70)
                i += 1
        # `0070`: shipped with no `impl` `done`, so R2 drops it.
        f.row(t, "0070_n", "review", "done", cost_usd=3.82)
        f.row(t, "0070_n", "ship", "done")
        # Its `ship` `done` a second after the window closed.
        f.unit(_when(BASELINE_TO) + timedelta(seconds=1) - timedelta(minutes=5), "0999_late", 1, 1.0, 1.0)

    code, lines = _run(baseline, "baseline")
    ok &= claim(code == EXIT_PASS and lines[-1] == "khớp R2",
                "baseline drops a unit with no impl done and reproduces R2", f"exit {code}, {lines}")
    say("PASS — every claim holds." if ok else "FAIL — a claim did not hold.")
    return EXIT_PASS if ok else EXIT_BROKEN


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--workspace", default="")
    ap.add_argument("--window", default="")
    args = ap.parse_args()
    if args.measure:
        return measure(data_root(), args.workspace, args.window)
    return prove()


if __name__ == "__main__":
    sys.exit(main())
