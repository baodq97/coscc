#!/usr/bin/env python3
"""Proof for `0061_a-review-round-is-spent-on-what-does-not-block`.

Not built yet: the plan's step 6 replaces this default mode with the proof of R14.

`--measure` (R13) reads every `<COS_DATA_DIR>/units/<slot>/.cos/` through
`cos.mjs --root <slot> status --json` -- never its own parse of `review.md` -- and reports,
for units numbered 15 and up (`0005`-`0008` are left out, `intent.md ## Answers, câu 2`):

    the mean review rounds per unit, all rounds and counted rounds, before and after
    the wasted rounds of kind (a) after: a counted round whose open findings are all low
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
import json
import os
import re
import shutil
import subprocess
import sys
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
    rounds = rounds_of(unit)
    counted = [r for r in rounds if r.get("verdict") == "changes-requested"]
    wasted = 0
    for r in counted:
        still = [f for f in r.get("findings") or [] if f.get("label") == "open"]
        if still and all(f.get("severity") == "low" for f in still):
            wasted += 1
    unrated = sum(
        1 for r in rounds if any(f.get("severity") is None for f in r.get("findings") or [])
    )
    return {"rounds": len(rounds), "counted": len(counted), "wasted_a": wasted, "unrated_rounds": unrated}


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--measure", action="store_true", help="measure the intent's outcome (R13)")
    args = parser.parse_args()
    if args.measure:
        return measure(data_root())
    say("not built yet")
    return EXIT_ENV


if __name__ == "__main__":
    sys.exit(main())
