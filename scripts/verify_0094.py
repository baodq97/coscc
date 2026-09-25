"""`0094` proof: every turn carries less of the rules and the unit's artifacts.

Plain: no session, no quota, no network. Each claim prints PASS or FAIL:

- `measure_fixture` (R12): a temporary `cos.db` whose `runs` rows are written by
  `coscc.journal.Journal` itself; `--measure` on it exits 0 where R1 holds, 1 where one
  condition fails, 2 where a stage has too few steps after, and 2 without `--min-version`.
- R6, R7, R10, R11: `coscc.rules_budget_test`, in a child process.
- R13, R16: the `start` row carries `app_version`, `app_commit` and `pointed`
  (`coscc.runner_test`, `coscc.service_test`), in a child process.
- R14, R15: what the prompts of `impl`, `pr`, `ship`, `review` and Gebo carry and name,
  and that every path they name may be `Read` under the step's grant, in a child process.

`--measure --workspace <name> --min-version X.Y.Z --before-since <ISO> [--repo <dir>]` is
R1. It reads `<COS_DATA_DIR>/cos.db` with `mode=ro`, never through `Data` (`0076`), and does
not import `coscc`. Each `end` row of `pr`, `ship`, `impl` or `review` in that workspace is
paired with the nearest `start` before it of the same workspace, unit and stage. Tokens per
turn = (`input_tokens` + `cache_creation_tokens` + `cache_read_tokens`) / `turns` (R2); a row
with no `turns` or `turns` 0 is left out and counted.

- After: the `start` carries `app_version` at least `--min-version`, compared as three
  integers; a `-rc.N` suffix is ignored, and that is printed. With `--repo`, a non-empty
  `app_commit` must also descend from the squash commit on this unit's `ship.md` merge line.
- Before: the last 20 per stage whose `start` has no `app_version` field and an `at` at or
  after `--before-since` — the moment the build carrying `0088` began running, given by
  whoever measures (R4).
- Reference only (R5): the last 20 per stage before `--before-since`.

A `start` whose `app_version` is empty or lower is counted apart and in neither side. Exit
2 when a stage has fewer than 10 steps after or before (spec C1; the script does not lower
it), or an argument is missing; 1 when a condition of R1 is false; 0 when all three hold.
It writes only `<COS_DATA_DIR>/measurements/0094-<stamp>.json`. Run it at a terminal: inside
a step it reads that step's own scratch data root.

`--workspace` matches a row's `workspace` — the checkout's resolved path — whole or by its
last component.

What it does not measure: whether an agent Read the rule or document it was pointed to
(spec C6); the preset and tool schemas (spec, Out of scope); chat, `estimate`, Gebo and
terminal sessions (no board `start` row, or not in R1).

Exit codes: 0 pass, 1 broken, 2 environment not ready.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import statistics
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
UNIT = "0094_every-turn-carries-thirty-thousand-tokens-of-rules"
STAGES = ("pr", "ship", "impl", "review")
WINDOW = 20      # R4: the last 20 steps before, per stage
ENOUGH = 10      # R1.3 and C1
DROP = 0.30      # R1.1, R1.2: at least 30% less
EXIT_PASS, EXIT_BROKEN, EXIT_ENV = 0, 1, 2
# `coscc/journal.py:68-75`, copied: this script does not import `coscc` for `--measure`.
CONTEXT_FIELDS = ("input_tokens", "cache_creation_tokens", "cache_read_tokens")
VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(-rc\.\d+)?$")
SHA = re.compile(r"\b[0-9a-f]{40}\b")


def say(line: str) -> None:
    print(line, flush=True)


def data_root() -> Path:
    return Path(os.environ.get("COS_DATA_DIR") or "~/.cos").expanduser()


def version(text: str) -> tuple[tuple[int, int, int] | None, bool]:
    """`(major, minor, patch)` and whether a `-rc.N` was dropped; `None` if unreadable."""
    m = VERSION.match((text or "").strip())
    if not m:
        return None, False
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))), bool(m.group(4))


def _when(at: str) -> datetime | None:
    try:
        t = datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return None
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def squash_commit(root: Path) -> str:
    """The first full SHA under `## What went out` of this unit's `ship.md`, or `""`."""
    for ship in sorted(root.glob(f"units/*/.cos/{UNIT}/ship.md")):
        try:
            text = ship.read_text(encoding="utf-8")
        except OSError:
            continue
        body = text.split("## What went out", 1)[1].split("\n## ", 1)[0] if "## What went out" in text else ""
        m = SHA.search(body)
        if m:
            return m.group(0)
    return ""


def descends(repo: str, ancestor: str, commit: str) -> bool:
    return subprocess.run(
        ["git", "-C", repo, "merge-base", "--is-ancestor", ancestor, commit],
        capture_output=True,
    ).returncode == 0


def pairs(db: Path, workspace: str) -> list[dict]:
    """Every `end` of the four stages in `workspace`, with the `start` it closes."""
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT id, record FROM runs WHERE kind IN ('start','end') ORDER BY id"
        ).fetchall()
    open_: dict[tuple[str, str, str], dict] = {}
    out: list[dict] = []
    for _, raw in rows:
        try:
            r = json.loads(raw)
        except ValueError:
            continue
        ws = str(r.get("workspace") or "")
        if r.get("stage") not in STAGES or workspace not in (ws, Path(ws).name):
            continue
        key = (ws, str(r.get("unit") or ""), str(r.get("stage")))
        if r.get("kind") == "start":
            open_[key] = r
        elif key in open_:
            out.append({"start": open_.pop(key), "end": r})
    return out


def per_turn(end: dict) -> float | None:
    turns = end.get("turns")
    if not isinstance(turns, (int, float)) or turns <= 0:
        return None
    return sum(float(end.get(f) or 0) for f in CONTEXT_FIELDS) / turns


def _median(xs: list[float]) -> float | None:
    return statistics.median(xs) if xs else None


def _fmt(x: float | None) -> str:
    return "—" if x is None else f"{x:,.0f}"


def measure(root: Path, workspace: str, min_version: str, before_since: str,
            repo: str = "", now: datetime | None = None) -> int:
    """R1, R5, R12. Everything it prints, it also writes to one file under `measurements/`."""
    now = now or datetime.now(timezone.utc)
    floor, rc = version(min_version)
    since = _when(before_since)
    if not workspace or floor is None or since is None:
        say("need --workspace, --min-version X.Y.Z and --before-since <ISO>")
        return EXIT_ENV
    if rc:
        say(f"--min-version {min_version}: the -rc suffix is ignored, compared as {floor}")
    db = root / "cos.db"
    if not db.is_file():
        say(f"no {db}")
        return EXIT_ENV
    squash = squash_commit(root) if repo else ""
    if repo and not squash:
        say(f"no merge SHA in any {UNIT}/ship.md under {root}/units: ancestry not checked")

    found = pairs(db, workspace)
    no_turns: dict[str, int] = {s: 0 for s in STAGES}
    apart: dict[str, int] = {s: 0 for s in STAGES}
    not_descended: dict[str, int] = {s: 0 for s in STAGES}
    after: dict[str, list[dict]] = {s: [] for s in STAGES}
    before: dict[str, list[dict]] = {s: [] for s in STAGES}
    reference: dict[str, list[dict]] = {s: [] for s in STAGES}
    for p in found:
        start, end = p["start"], p["end"]
        stage = start["stage"]
        value = per_turn(end)
        if value is None:
            no_turns[stage] += 1
            continue
        step = {
            "unit": start.get("unit"), "at": start.get("at"), "per_turn": value,
            "total": sum(float(end.get(f) or 0) for f in CONTEXT_FIELDS),
            "prompt_chars": start.get("prompt_chars"),
            "app_version": start.get("app_version"), "app_commit": start.get("app_commit"),
        }
        if "app_version" in start:
            v, _ = version(str(start.get("app_version") or ""))
            if v is None or v < floor:
                apart[stage] += 1
                continue
            commit = str(start.get("app_commit") or "")
            if squash and commit and not descends(repo, squash, commit):
                not_descended[stage] += 1
                continue
            after[stage].append(step)
            continue
        at = _when(start.get("at"))
        if at is None:
            continue
        (before if at >= since else reference)[stage].append(step)
    for s in STAGES:
        before[s] = before[s][-WINDOW:]
        reference[s] = reference[s][-WINDOW:]

    say(f"workspace {workspace}; after: app_version ≥ {'.'.join(map(str, floor))}; "
        f"before: no app_version, at ≥ {since.isoformat()}, last {WINDOW}; "
        f"reference: before {since.isoformat()}, last {WINDOW}")
    stats: dict[str, dict] = {}
    for s in STAGES:
        b = _median([x["per_turn"] for x in before[s]])
        a = _median([x["per_turn"] for x in after[s]])
        r = _median([x["per_turn"] for x in reference[s]])
        drop = None if not b or a is None else (b - a) / b
        cumulative = None if not r or a is None else (r - a) / r
        stats[s] = {
            "before_n": len(before[s]), "after_n": len(after[s]), "reference_n": len(reference[s]),
            "before": b, "after": a, "reference": r, "drop": drop, "cumulative_drop": cumulative,
            "prompt_chars_before": _median([x["prompt_chars"] for x in before[s] if isinstance(x["prompt_chars"], int)]),
            "prompt_chars_after": _median([x["prompt_chars"] for x in after[s] if isinstance(x["prompt_chars"], int)]),
            "total_before": _median([x["total"] for x in before[s]]),
            "total_after": _median([x["total"] for x in after[s]]),
            "no_turns": no_turns[s], "version_apart": apart[s], "not_descended": not_descended[s],
        }
        st = stats[s]
        say(f"  {s:6} before {st['before_n']:2} × {_fmt(b)}  after {st['after_n']:2} × {_fmt(a)}  "
            f"drop {'—' if drop is None else f'{drop:.0%}'}")
        say(f"         prompt_chars {_fmt(st['prompt_chars_before'])} → {_fmt(st['prompt_chars_after'])}; "
            f"tokens per step {_fmt(st['total_before'])} → {_fmt(st['total_after'])} (C3, not scored)")
        say(f"         R5, not scored: reference {st['reference_n']} × {_fmt(r)}, cumulative "
            f"{'—' if cumulative is None else f'{cumulative:.0%}'}")
        say(f"         left out: no turns {no_turns[s]}; app_version empty or lower {apart[s]}; "
            f"not after the squash {not_descended[s]}")

    def holds(*names: str) -> bool:
        return all(stats[n]["drop"] is not None and stats[n]["drop"] >= DROP for n in names)

    r11, r12 = holds("pr", "ship"), holds("impl", "review")
    r13 = all(stats[s]["after_n"] >= ENOUGH for s in STAGES)
    short = [s for s in STAGES if stats[s]["after_n"] < ENOUGH or stats[s]["before_n"] < ENOUGH]
    say(f"R1.1 pr and ship ≥ {DROP:.0%} less: {r11}")
    say(f"R1.2 impl and review ≥ {DROP:.0%} less: {r12}")
    say(f"R1.3 ≥ {ENOUGH} steps after each: {r13}")
    if short:
        result, code = f"không đo được: dưới {ENOUGH} bước trước hoặc sau ở {', '.join(short)}", EXIT_ENV
    elif r11 and r12 and r13:
        result, code = "đạt", EXIT_PASS
    else:
        result, code = "trượt", EXIT_BROKEN
    say(f"→ {result}")
    out = root / "measurements" / f"0094-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "workspace": workspace, "min_version": min_version, "before_since": before_since,
        "squash": squash, "stats": stats, "r1": {"1": r11, "2": r12, "3": r13},
        "result": result, "exit": code,
        "steps": {"before": before, "after": after, "reference": reference},
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    say(f"wrote {out}")
    return code


# ---------------------------------------------------------------------------
# The proof
# ---------------------------------------------------------------------------


def claim(ok: bool, text: str, detail: str = "") -> bool:
    say(f"{'PASS' if ok else 'FAIL'}  {text}{': ' + detail if detail and not ok else ''}")
    return ok


def _fixture(data: Path, after_per_turn: dict[str, int], after_n: dict[str, int]) -> None:
    """`runs` rows written by `Journal` itself: 12 steps per stage before at 30 000 tokens per
    turn, then `after_n` steps at `after_per_turn`, on a build carrying `app_version` 9.9.9."""
    sys.path.insert(0, str(REPO))
    from coscc.journal import Journal

    journal = Journal(data / "work" / "proj", data)
    key = str((data / "work" / "proj").resolve())

    def step(stage: str, n: int, per: int, **start: str) -> None:
        # (per + per + 2 * per) / 4 turns = `per` tokens per turn.
        unit = f"{n:04d}_x-{stage}"
        journal.started(key, unit, stage, "manual", prompt_chars=1000, **start)
        journal.finished(key, unit, stage, "done", input_tokens=per, cache_creation_tokens=per,
                         cache_read_tokens=per * 2, output_tokens=1, turns=4)

    for s in STAGES:
        for n in range(12):
            step(s, n, 30_000)
        for n in range(after_n[s]):
            step(s, 100 + n, after_per_turn[s], app_version="9.9.9", app_commit="")


def measure_fixture() -> bool:
    good = {s: 15_000 for s in STAGES}
    cases = [
        ("R1 holds", good, {s: 10 for s in STAGES}, "9.9.9", 0),
        ("review only 17% less", {**good, "review": 25_000}, {s: 10 for s in STAGES}, "9.9.9", 1),
        ("ship has 5 steps after", good, {**{s: 10 for s in STAGES}, "ship": 5}, "9.9.9", 2),
        ("no --min-version", good, {s: 10 for s in STAGES}, "", 2),
    ]
    ok = True
    for name, per, n, floor, want in cases:
        with tempfile.TemporaryDirectory() as d:
            data = Path(d) / "data"
            data.mkdir()
            _fixture(data, per, n)
            with open(os.devnull, "w") as sink:
                stdout, sys.stdout = sys.stdout, sink
                try:
                    got = measure(data, "proj", floor, "2000-01-01T00:00:00+00:00")
                finally:
                    sys.stdout = stdout
            written = list((data / "measurements").glob("0094-*.json"))
            ok &= claim(got == want and (want == 2 and not floor or len(written) == 1),
                        f"measure_fixture: {name} → exit {want}",
                        f"exit {got}, {len(written)} measurement files")
    return ok


def unittests(label: str, *names: str) -> bool:
    out = subprocess.run([sys.executable, "-m", "unittest", *names], cwd=str(REPO),
                         capture_output=True, text=True)
    last = (out.stderr.strip().splitlines() or [""])[-1]
    return claim(out.returncode == 0, f"{label}: {', '.join(names)}", last)


def prove() -> int:
    ok = measure_fixture()
    ok &= unittests("R6, R7, R10, R11", "coscc.rules_budget_test")
    ok &= unittests(
        "R13, R16",
        "coscc.runner_test.TheStartRecordSaysWhatRanAndWhatWasNamed",
        "coscc.service_test.AStageRunsOnTheModelSettingsNames.test_the_start_record_names_the_build_that_ran_it",
        "coscc.service_test.AStageRunsOnTheModelSettingsNames."
        "test_a_build_that_cannot_be_read_is_two_empty_strings_and_the_step_runs",
        "coscc.integrate_service_test.GeboThroughTheService."
        "test_the_start_record_names_the_artifacts_it_pointed_at_and_the_build",
    )
    ok &= unittests(
        "R14, R15",
        "coscc.runner_test.ThePointingStagesNameTheirFiles",
        "coscc.runner_test.EveryPathAPromptNamesCanBeRead",
        "coscc.runner_test.OpenFindings",
        "coscc.runner_test.AFixRoundCarriesTheFindings",
        "coscc.runner_test.TheStagesThatReadWholeInputsKeepTheirPrompt",
        "coscc.integrate_test.ThePrompt",
    )
    say("PASS — every claim holds." if ok else "FAIL — a claim did not hold.")
    return EXIT_PASS if ok else EXIT_BROKEN


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--workspace", default="")
    ap.add_argument("--min-version", default="")
    ap.add_argument("--before-since", default="")
    ap.add_argument("--repo", default="")
    args = ap.parse_args()
    if args.measure:
        return measure(data_root(), args.workspace, args.min_version, args.before_since, args.repo)
    return prove()


if __name__ == "__main__":
    sys.exit(main())
