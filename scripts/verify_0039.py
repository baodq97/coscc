#!/usr/bin/env python3
"""Proof for `0039_unknowns-reach-impl-unmeasured`, spec R4.

`spike` is a ninth stage that runs only when `spec.md` marks a concern `[unmeasured]`, or
when `spike.md` already exists. Every other unit must see the loop exactly as it was. This
runs the `cos.mjs` at `git merge-base HEAD origin/main` beside the one in this checkout,
over every unit in `.cos/`, and compares, byte for byte:

    next <unit>
    gate <unit> <stage>        for each stage that existed before, stdout, stderr and exit
    status --json              with the `stages` key removed -- the one difference allowed

Both run with `--root` and no `--repo`, so no `gh` is asked and the answers are
deterministic.

    0  every comparison was identical, and there was at least one
    1  at least one differed -- the unit and the command are printed
    2  the environment could not answer: no `node`, or no `git`, or no merge-base

`--root <dir>` (repeatable) adds another repository-shaped root, such as the app's store at
`~/.cos/units/<slot>` (spec C6). Nothing requires it.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OLD_STAGES = ("idea", "intent", "spec", "plan", "impl", "implement", "pr", "review", "ship")
EXIT_PASS, EXIT_BROKEN, EXIT_ENV = 0, 1, 2


def say(line: str) -> None:
    print(line, flush=True)


def run(script: Path, root: Path, *args: str) -> tuple[int, str, str]:
    r = subprocess.run(
        ["node", str(script), "--root", str(root), *args],
        capture_output=True, text=True, cwd=str(REPO),
    )
    return r.returncode, r.stdout, r.stderr


def without_stages(out: str) -> str:
    try:
        data = json.loads(out)
    except ValueError:
        return out
    data.pop("stages", None)
    return json.dumps(data, indent=2, sort_keys=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", action="append", default=[], help="another root to compare")
    args = parser.parse_args()

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

    new = REPO / ".claude" / "scripts" / "cos.mjs"
    roots = [REPO, *(Path(r).expanduser().resolve() for r in args.root)]
    compared = 0
    units_seen = 0
    differ: list[str] = []
    with tempfile.TemporaryDirectory(prefix="verify_0039-") as tmp:
        old = Path(tmp) / "cos.mjs"
        old.write_text(old_text.stdout, encoding="utf-8")
        for root in roots:
            cos = root / ".cos"
            if not cos.is_dir():
                say(f"environment: {root} has no .cos/")
                return EXIT_ENV
            units = sorted(p.name for p in cos.iterdir() if p.is_dir())
            units_seen += len(units)
            a, b = run(old, root, "status", "--json"), run(new, root, "status", "--json")
            compared += 1
            if (a[0], without_stages(a[1]), a[2]) != (b[0], without_stages(b[1]), b[2]):
                differ.append(f"{root}: status --json")
            for unit in units:
                calls = [("next", unit)] + [("gate", unit, stage) for stage in OLD_STAGES]
                for call in calls:
                    compared += 1
                    if run(old, root, *call) != run(new, root, *call):
                        differ.append(f"{root}: {' '.join(call)}")

    for d in differ:
        say(f"differs: {d}")
    if differ:
        say(f"different: {len(differ)} of {compared} comparisons across {units_seen} units")
        return EXIT_BROKEN
    if compared == 0 or units_seen == 0:
        say("nothing was compared")
        return EXIT_BROKEN
    say(f"identical: {compared} comparisons across {units_seen} units (base {sha[:7]})")
    return EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
