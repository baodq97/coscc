#!/usr/bin/env python3
"""Proof for `0061_a-review-round-is-spent-on-what-does-not-block`, spec R14 and R13.

Plain, it runs the `cos.mjs` at `git merge-base HEAD origin/main` beside the one in this
checkout, over every unit in `.cos/` and every `--root` given (repeatable, e.g.
`~/.cos/units/<slot>`), both with `--root` and no `--repo`, and requires them to agree on

    next <unit>
    gate <unit> <stage>        for every stage, stdout, stderr and exit
    status --json              once `severity` is removed from every finding and
                               `nonBlocking` from every unit -- the two differences allowed

then runs `--measure` against a store it builds in a temporary directory, and requires the
exit codes below. Copied from `scripts/verify_0039.py`, not imported: one proof script does
not depend on another.

    0  every claim held
    1  at least one did not -- it is printed
    2  no `node`, no `git`, or no merge-base with origin/main

`--measure` (R13) reads every `<COS_DATA_DIR>/units/<slot>/.cos/` through
`cos.mjs --root <slot> status --json` -- never its own parse of `review.md` -- and reports,
for units numbered 15 and up (`0005`-`0008` are left out, `intent.md ## Answers, câu 2`):

    the mean review rounds per unit, all rounds and counted rounds, before and after
    the wasted rounds of kind (a) after: a counted round whose every finding not closed
      is `[open]`, low, and not rated higher by an earlier round
    how many units shipped after this one merged
    how many rounds carry a finding whose severity is not readable (spec C9)

The line between "before" and "after" is the first ISO-8601 timestamp under
`## What went out` in this unit's own `ship.md` (`write-ship` invariant 1 records
`mergedAt`). A unit is "after" when its `ship.md` is accepted and its own timestamp is
later. A unit whose timestamp cannot be read is printed and counted in neither group.
Kind (b) needs the pull request's history; a person reads it, and this does not conclude
it. It writes `<COS_DATA_DIR>/measurements/0061-<timestamp>.json` and nothing else.

    0  at least 5 units after, and no wasted round of kind (a) among them
    1  at least one wasted round of kind (a) after
    2  no line yet (this unit has not shipped), fewer than 5 units after, or no `node`
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
COS = REPO / ".claude" / "scripts" / "cos.mjs"
SLUG = "a-review-round-is-spent-on-what-does-not-block"
FIRST_MEASURED = 15
ENOUGH = 5
EXIT_PASS, EXIT_BROKEN, EXIT_ENV = 0, 1, 2
ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)(Z|[+-]\d{2}:?\d{2})?")


def say(line: str) -> None:
    print(line, flush=True)


def data_root() -> Path:
    return Path(os.environ.get("COS_DATA_DIR") or "~/.cos").expanduser()


def status_json(script: Path, root: Path) -> dict | None:
    r = subprocess.run(
        ["node", str(script), "--root", str(root), "status", "--json"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    try:
        return json.loads(r.stdout)
    except ValueError:
        say(f"cannot read status --json of {root}: {(r.stderr or r.stdout).strip()[:200]}")
        return None


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


def rounds_of(unit: dict) -> list[dict]:
    review = (unit.get("artifacts") or {}).get("review.md") or {}
    return ((review.get("review") or {}).get("rounds")) or []


def count(unit: dict) -> dict:
    """Kind (a) is a counted round whose every finding still open was low and did not block.

    "Still open" is every finding not closed -- `[fixed <sha>]`, or `[answered]` with a block
    in `review.md ## Answers` -- not only those labelled `[open]`: a `[claim-rejected]`, a
    `[needs-person]`, an unreadable label, an unbacked `[answered]`, or a low an earlier round
    rated higher (`cos.mjs` R3(c)) blocks, and a round that asked for it was not wasted
    (`0061` review round 1, F1).
    """
    rounds = rounds_of(unit)
    answered = set(((unit.get("artifacts") or {}).get("review.md") or {}).get("personAnswers") or [])
    higher: set[str] = set()
    counted = 0
    wasted = 0
    for r in rounds:
        findings = r.get("findings") or []
        if r.get("verdict") == "changes-requested":
            counted += 1
            still = [
                f for f in findings
                if not (f.get("label") == "fixed" or (f.get("label") == "answered" and f.get("id") in answered))
            ]
            if still and all(
                f.get("label") == "open" and f.get("severity") == "low" and f.get("id") not in higher
                for f in still
            ):
                wasted += 1
        higher |= {f.get("id") for f in findings if f.get("severity") in ("high", "medium")}
    unrated = sum(
        1 for r in rounds if any(f.get("severity") is None for f in r.get("findings") or [])
    )
    return {"rounds": len(rounds), "counted": counted, "wasted_a": wasted, "unrated_rounds": unrated}


def mean(values: list[int]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def measure(root: Path) -> int:
    """R13. Everything it prints, it also writes to one file under `measurements/`."""
    if not shutil.which("node"):
        say("environment: node is needed")
        return EXIT_ENV
    slots = sorted(p for p in (root / "units").glob("*") if (p / ".cos").is_dir())
    rows: list[dict] = []
    line: datetime | None = None
    for slot in slots:
        data = status_json(COS, slot)
        if data is None:
            return EXIT_ENV
        for unit in data.get("units") or []:
            number = unit.get("number")
            if not isinstance(number, int) or number < FIRST_MEASURED:
                continue
            ship = slot / ".cos" / unit["name"] / "ship.md"
            accepted = ((unit.get("artifacts") or {}).get("ship.md") or {}).get("status") == "accepted"
            at = shipped_at(ship) if accepted else None
            if unit.get("slug") == SLUG and at is not None:
                line = at
            rows.append({"slot": slot.name, "unit": unit["name"], "shipped_at": at, **count(unit)})

    before: list[dict] = []
    after: list[dict] = []
    unread: list[str] = []
    for row in rows:
        if row["shipped_at"] is None:
            unread.append(f"{row['slot']}/{row['unit']}")
        elif line is not None and row["shipped_at"] > line:
            after.append(row)
        else:
            before.append(row)

    def group(name: str, members: list[dict]) -> dict:
        return {
            "group": name,
            "units": len(members),
            "mean_rounds": mean([r["rounds"] for r in members]),
            "mean_counted_rounds": mean([r["counted"] for r in members]),
            "wasted_a": sum(r["wasted_a"] for r in members),
            "unrated_rounds": sum(r["unrated_rounds"] for r in members),
        }

    summary = [group("before", before), group("after", after)]
    say(f"line: {line.isoformat() if line else 'none — ' + SLUG + ' has no accepted ship.md with a timestamp'}")
    for g in summary:
        say(
            f"{g['group']}: {g['units']} units, mean rounds {g['mean_rounds']}, "
            f"mean counted rounds {g['mean_counted_rounds']}, wasted (a) {g['wasted_a']}, "
            f"rounds with an unrated finding {g['unrated_rounds']}"
        )
    for name in unread:
        say(f"no ship timestamp, counted in neither group: {name}")
    say("kind (b) is read by a person from each pull request's history; this does not conclude it")

    wasted = summary[1]["wasted_a"]
    if line is None or (wasted == 0 and len(after) < ENOUGH):
        code = EXIT_ENV
    elif wasted > 0:
        code = EXIT_BROKEN
    else:
        code = EXIT_PASS

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out = root / "measurements" / f"0061-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "line": line.isoformat() if line else None,
        "groups": summary,
        "units": [
            {**r, "shipped_at": r["shipped_at"].isoformat() if r["shipped_at"] else None}
            for r in rows
        ],
        "unread": unread,
        "exit": code,
    }
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    say(f"wrote {out}")
    return code


# ---------------------------------------------------------------------------
# The proof (R14), and --measure on a store built here
# ---------------------------------------------------------------------------


def claim(ok: bool, text: str, detail: str = "") -> bool:
    say(f"{'PASS' if ok else 'FAIL'}  {text}{': ' + detail if detail and not ok else ''}")
    return ok


def run(script: Path, root: Path, *args: str) -> tuple[int, str, str]:
    r = subprocess.run(
        ["node", str(script), "--root", str(root), *args],
        capture_output=True, text=True, cwd=str(REPO),
    )
    return r.returncode, r.stdout, r.stderr


def without_added(out: str) -> str:
    """`status --json` with the two fields this unit adds taken out."""
    try:
        data = json.loads(out)
    except ValueError:
        return out
    for unit in data.get("units") or []:
        unit.pop("nonBlocking", None)
        for r in rounds_of(unit):
            for f in r.get("findings") or []:
                f.pop("severity", None)
    return json.dumps(data, indent=2, sort_keys=True)


def unchanged(old: Path, roots: list[Path]) -> bool:
    """R14: the old `cos.mjs` and this one answer every unit alike."""
    compared = 0
    units_seen = 0
    differ: list[str] = []
    for root in roots:
        cos = root / ".cos"
        if not cos.is_dir():
            differ.append(f"{root} has no .cos/")
            continue
        a, b = run(old, root, "status", "--json"), run(COS, root, "status", "--json")
        compared += 1
        if (a[0], without_added(a[1]), a[2]) != (b[0], without_added(b[1]), b[2]):
            differ.append(f"{root}: status --json")
        try:
            stages = [s["name"] for s in json.loads(b[1])["stages"]] + ["implement"]
        except (ValueError, KeyError, TypeError):
            differ.append(f"{root}: status --json names no stages")
            continue
        units = sorted(p.name for p in cos.iterdir() if p.is_dir())
        units_seen += len(units)
        for unit in units:
            for call in [("next", unit)] + [("gate", unit, stage) for stage in stages]:
                compared += 1
                if run(old, root, *call) != run(COS, root, *call):
                    differ.append(f"{root}: {' '.join(call)}")
    for d in differ:
        say(f"differs: {d}")
    return claim(
        not differ and units_seen > 0,
        f"R14: gate, next and status --json unchanged but for severity and nonBlocking "
        f"({compared} comparisons across {units_seen} units)",
        f"{len(differ)} differ" if differ else "no unit was compared",
    )


REVIEW = "# Review: x\nPR: pr.md. Author: t. Status: accepted.\n\n{rounds}"
ROUND = "## Round {n}\n\nReviewed: {sha}. Verdict: {verdict}.\n\n### Findings\n\n{findings}\n"
SHIP = "# Ship: x\nReview: review.md. Author: t. Status: accepted.\n\n## What went out\n\n- `mergedAt`: {at}.\n"


HIGH = ["- F1 [open] a.py:1 — high — x"]
WASTED = ["- F1 [open] a.py:1 — low — x"]
# Counted rounds that were right to ask: a low sits `[open]` beside a finding that still
# blocks, in each of the shapes `count` must read as open (`0061` review round 1, F1). The
# last one is a low that an earlier round rated high.
BESIDE_A_LOW = [
    ["- F1 [open] a.py:1 — low — x\n- F2 [claim-rejected] b.py:1 — high — y"],
    ["- F1 [open] a.py:1 — low — x\n- F2 [needs-person] b.py:1 — high — y"],
    ["- F1 [open] a.py:1 — low — x\n- F2 [later] b.py:1 — high — y"],
    ["- F1 [open] a.py:1 — low — x\n- F2 [answered] b.py:1 — high — y"],
    ["- F1 [open] a.py:1 — high — x", "- F1 [open] a.py:1 — low — x"],
]


def fake_unit(cos: Path, name: str, at: str | None, asked: list[str] = HIGH) -> None:
    """A unit whose `asked` rounds each end `changes-requested` -- one string of finding
    lines per round -- then a round that passes."""
    unit = cos / name
    unit.mkdir(parents=True)
    (unit / "intent.md").write_text("# I\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8")
    rounds = [
        ROUND.format(n=n, sha=f"{n:x}" * 40, verdict="changes-requested", findings=findings)
        for n, findings in enumerate(asked, start=1)
    ]
    rounds.append(ROUND.format(n=len(asked) + 1, sha="f" * 40, verdict="pass", findings=f"- F1 [fixed {'f' * 40}] a.py:1 — high — x"))
    (unit / "review.md").write_text(REVIEW.format(rounds="\n".join(rounds)), encoding="utf-8")
    if at is not None:
        (unit / "ship.md").write_text(SHIP.format(at=at), encoding="utf-8")


def measured(tmp: Path, name: str, after: int, asked: list[list[str]] | None = None, line: bool = True) -> int:
    """Build a store under `tmp/name`, run `measure` on it quietly, return its exit code.
    `asked[i]`, when given, is the rounds of the i-th unit shipped after; the rest get `HIGH`."""
    root = tmp / name
    cos = root / "units" / "slot" / ".cos"
    cos.mkdir(parents=True)
    fake_unit(cos, "0020_before", "2026-09-01T00:00:00Z")
    fake_unit(cos, f"0061_{SLUG}", "2026-09-25T00:00:00Z" if line else None)
    asked = asked or []
    for i in range(after):
        rounds = asked[i] if i < len(asked) else HIGH
        fake_unit(cos, f"{70 + i:04d}_after-{i}", f"2026-10-0{i + 1}T00:00:00Z", rounds)
    quiet = io.StringIO()
    with contextlib.redirect_stdout(quiet):
        code = measure(root)
    written = list((root / "measurements").glob("0061-*.json"))
    return code if len(written) == 1 else -1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--measure", action="store_true", help="measure the intent's outcome (R13)")
    parser.add_argument("--root", action="append", default=[], help="another root to compare")
    args = parser.parse_args()
    if args.measure:
        return measure(data_root())

    if not shutil.which("node") or not shutil.which("git"):
        say("environment: node and git are both needed")
        return EXIT_ENV
    base = subprocess.run(
        ["git", "merge-base", "HEAD", "origin/main"], capture_output=True, text=True, cwd=str(REPO)
    )
    if base.returncode != 0:
        say(f"environment: no merge-base with origin/main: {base.stderr.strip()}")
        return EXIT_ENV
    sha = base.stdout.strip()
    old_text = subprocess.run(
        ["git", "show", f"{sha}:.claude/scripts/cos.mjs"], capture_output=True, text=True, cwd=str(REPO)
    )
    if old_text.returncode != 0:
        say(f"environment: cannot read cos.mjs at {sha}: {old_text.stderr.strip()}")
        return EXIT_ENV
    say(f"base: {sha[:7]}")

    results: list[bool] = []
    with tempfile.TemporaryDirectory(prefix="verify_0061-") as tmp:
        old = Path(tmp) / "cos.mjs"
        old.write_text(old_text.stdout, encoding="utf-8")
        roots = [REPO, *(Path(r).expanduser().resolve() for r in args.root)]
        results.append(unchanged(old, roots))

        t = Path(tmp)
        for name, kwargs, want, text in [
            ("five", {"after": 5}, EXIT_PASS, "5 units shipped after, no wasted round (a): exit 0"),
            ("wasted", {"after": 5, "asked": [WASTED]}, EXIT_BROKEN,
             "one of them spent a counted round on lows only: exit 1"),
            ("beside", {"after": 5, "asked": BESIDE_A_LOW}, EXIT_PASS,
             "a low open beside a finding still blocking is not a wasted round: exit 0"),
            ("four", {"after": 4}, EXIT_ENV, "4 units shipped after: exit 2"),
            ("no-line", {"after": 5, "line": False}, EXIT_ENV, "this unit has no ship.md: exit 2"),
        ]:
            got = measured(t, name, **kwargs)
            results.append(claim(got == want, f"R13 --measure: {text}", f"exit {got}"))

    if all(results):
        say(f"all {len(results)} claims held")
        return EXIT_PASS
    return EXIT_BROKEN


if __name__ == "__main__":
    sys.exit(main())
