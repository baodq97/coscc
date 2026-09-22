#!/usr/bin/env python3
"""Proof for .cos/0013_board-cannot-say-what-happened.

The claim in `intent.md`'s "Proposed outcome": for the units in this repository's `.cos/`,
the product lists **every time an artifact was edited after it had been settled**, each
with its unit, its artifact, the state it came from, the state it went to, and when —
checked against git. Before this unit, the number it could list was 0.

    0  every claim held
    1  at least one did not
    2  the environment could not answer: no git, not a checkout, no writable database

`scripts/verify_0003.py:8-14` explains why 2 is kept apart from 1. It matters here for the
usual reason: "this machine has no git" and "the product lost 43 events" are different
facts, and only the second is this file's to report.

**The expected number is computed from git on every run, and is never written down here.**
It was 39 when `intent.md` was written on 2026-09-22, 41 when `plan.md` was written, 42
when the implementation began and 43 by the time step 4 landed — and two of those increases
were this unit's own artifacts being corrected after acceptance, which is the very event
being counted. `plan.md` Risk 2 is that measurement. A proof asserting `== 39` would have
gone red on the next commit and been fixed by editing the number, which is the opposite of
what it is for.

**The two counts come from two implementations on purpose.** `coscc/backfill.py` makes one
`git log --no-renames --name-status` pass over the whole directory and reads blobs through
`git cat-file --batch`. This file walks **one path at a time**, asking `git log -- <path>`
and `git show <sha>:<path>` per commit, and parses the status with its own expression. Two
programs agreeing on 43 is evidence; one program agreeing with itself is not
(`plan.md` Risk 1).

It creates no session and sends no prompt, so it spends no account quota, and it clones
nothing, so it needs no network. It writes into a **temporary** data root, never `~/.cos`:
this is a measurement, not an install step.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.proof_harness import EXIT_BROKEN, EXIT_ENV, EXIT_PASS, say  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
COS = REPO / ".cos"

# This file's own reading of a status line, written differently from
# `coscc/backfill._status_of` on purpose. Same rule as `.claude/scripts/cos.mjs:49-55`:
# the first `Status:` in the file.
_STATUS = re.compile(r"\bStatus:\s*([A-Za-z]+)", re.IGNORECASE)

# Also this file's own. `coscc/states.json` is what the product reads; a proof that asked
# the product what counts as settled could not catch the product being wrong about it.
_SETTLED = {"accepted", "skipped", "done"}
_ARTIFACTS = (
    "idea.md", "intent.md", "spec.md", "plan.md",
    "impl.md", "pr.md", "review.md", "ship.md",
)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True
    ).stdout


def require_environment() -> None:
    """Refuse to report a broken product when the environment simply cannot answer."""
    try:
        git("rev-parse", "--show-toplevel")
    except FileNotFoundError:
        print("no git on PATH — this proof reads history, so it cannot run")
        raise SystemExit(EXIT_ENV)
    except subprocess.CalledProcessError:
        print(f"{REPO} is not a git checkout")
        raise SystemExit(EXIT_ENV)
    if not COS.is_dir():
        print(f"no {COS} — there is no history here to measure")
        raise SystemExit(EXIT_ENV)


def expected_from_git() -> tuple[Counter, dict[str, list]]:
    """Post-settlement edits, counted one file at a time, without the product's code.

    Returns a count per `unit/artifact` and, for each, the `(from, to)` pairs in order —
    the second is what claim 1 checks the *content* of the events against, not just how
    many there were.
    """
    counts: Counter = Counter()
    detail: dict[str, list] = {}
    paths = [
        p for p in git("ls-tree", "-r", "--name-only", "HEAD", ".cos").split()
        if p.rsplit("/", 1)[-1] in _ARTIFACTS
    ]
    for path in paths:
        previous = None
        for sha in git("log", "--reverse", "--format=%H", "HEAD", "--", path).split():
            try:
                text = git("show", f"{sha}:{path}")
            except subprocess.CalledProcessError:
                continue
            found = _STATUS.search(text)
            state = found.group(1).lower() if found else None
            if previous in _SETTLED:
                key = path[len(".cos/"):]
                counts[key] += 1
                detail.setdefault(key, []).append((previous, state))
            previous = state
    return counts, detail


def measured_from_product(cwd: str, data_dir: str) -> tuple[Counter, dict[str, list], dict]:
    """The same thing, asked of the product's own read path.

    Goes through `Service.unit_history` rather than reading the tables, because what the
    outcome promises is that **the product** can list these — a proof querying SQLite
    directly would pass over a read path that does not work.
    """
    from coscc.config import Config
    from coscc.service import Service
    from coscc.sessions import Sessions

    config = Config(workspaces=(cwd,), working_dir=cwd, data_dir=data_dir)
    service = Service(config, Sessions(config))

    counts: Counter = Counter()
    detail: dict[str, list] = {}
    payloads: dict = {}
    present = sorted(p.name for p in COS.iterdir() if p.is_dir())
    for unit in present:
        found = service.unit_history(cwd, unit)
        payloads[unit] = found
        for row in found["transitions"]:
            if row["from_state"] in _SETTLED:
                key = f"{unit}/{row['artifact']}"
                counts[key] += 1
                detail.setdefault(key, []).append((row["from_state"], row["to_state"]))
    return counts, detail, payloads


def claim_1(wanted: Counter, wanted_detail: dict, got: Counter, got_detail: dict) -> bool:
    """R2. The same events, on the same artifacts, in the same order."""
    total_wanted, total_got = sum(wanted.values()), sum(got.values())
    ok = say(
        wanted == got,
        f"the product lists every post-settlement edit git knows about "
        f"({total_got} of {total_wanted}, over {len(got)} artifacts)",
        "" if wanted == got else _difference(wanted, got),
    )
    same_states = wanted_detail == got_detail
    return say(
        same_states,
        "and each one carries the state it came from and the state it went to",
        "" if same_states else _first_mismatch(wanted_detail, got_detail),
    ) and ok


def _difference(wanted: Counter, got: Counter) -> str:
    lines = []
    for key in sorted(set(wanted) | set(got)):
        if wanted[key] != got[key]:
            lines.append(f"{key}: git says {wanted[key]}, the product says {got[key]}")
    return "; ".join(lines[:8])


def _first_mismatch(wanted: dict, got: dict) -> str:
    for key in sorted(set(wanted) | set(got)):
        if wanted.get(key) != got.get(key):
            return f"{key}: git {wanted.get(key)} vs product {got.get(key)}"
    return ""


def claim_2(payloads: dict) -> bool:
    """R3. Every field present, and what is not known says so rather than being blank."""
    required = ("unit", "artifact", "from_state", "to_state", "at", "actor", "session", "source")
    blanks = []
    unknown_actors = 0
    total = 0
    for unit, found in payloads.items():
        for row in found["transitions"]:
            total += 1
            for field in required:
                value = row.get(field)
                if value is None or str(value).strip() == "":
                    blanks.append(f"{unit}/{row.get('artifact')}.{field}")
            if row.get("actor") == "unknown":
                unknown_actors += 1
    ok = say(
        not blanks and total > 0,
        f"every one of the {total} transitions carries all {len(required)} fields",
        "; ".join(blanks[:8]) if blanks else ("no transitions at all" if not total else ""),
    )
    # Not a failure — a statement. `spec.md` C1: git knows commit authors, not sessions,
    # so imported history has no provenance and must not look as though it has.
    print(
        f"    note: {unknown_actors} of {total} say their actor is 'unknown' — "
        "imported history has no session behind it, by construction"
    )
    return ok


def claim_3(cwd: str, data_dir: str) -> bool:
    """R1. No current-state column: delete the last transition and the state moves back.

    The row deleted is chosen to be one where `from_state` differs from `to_state`. Most
    transitions in this repository are `accepted → accepted` — a settled artifact edited
    in place — and deleting one of those would leave the projection unchanged, which is
    the same answer a stored state column would give. The check has to be able to fail.

    It edits the database by hand, which nothing in the product ever does. That is the
    point: it is the only way to show there is no second copy of the state to disagree
    with the log. The database is the temporary one this run created.
    """
    import sqlite3

    from coscc.data import Data
    from coscc.history import History

    root = str(Path(cwd).resolve())
    data = Data(data_dir)
    with sqlite3.connect(data.db_path) as conn:
        # It has to be the **last** transition of its artifact, not merely a late one
        # that changed a state. Deleting a row with others after it leaves the projection
        # exactly where it was — measured here on 2026-09-22, where the newest
        # state-changing row was `0013/plan.md: not started → accepted` with two
        # `accepted → accepted` corrections stacked on top of it.
        found = conn.execute(
            "SELECT id, unit, artifact, from_state, to_state FROM transitions "
            "WHERE root = ? AND from_state <> to_state AND id IN ("
            "  SELECT MAX(id) FROM transitions WHERE root = ? GROUP BY workspace, unit, artifact"
            ") ORDER BY id DESC LIMIT 1",
            (root, root),
        ).fetchone()
        if found is None:
            return say(
                False,
                "the projection follows the log",
                "no transition that changed a state, so the check could not be made to fail",
            )
        conn.execute("DELETE FROM transitions WHERE id = ?", (found[0],))

    history = History(cwd, data_dir)
    after = history.state(cwd, found[1])[found[2]]
    return say(
        after == found[3],
        f"deleting the last state-changing transition of {found[1]}/{found[2]} moves it "
        f"back from {found[4]} to {after}, so nothing else stores it",
        "" if after == found[3] else f"expected {found[3]}, got {after}",
    )


def claim_4() -> bool:
    """R6. A different state set loads from a file and drives a unit, with no code change."""
    from coscc import states
    from coscc.data import Data
    from coscc.history import History, settled_edits

    other = {
        "name": "proof-set",
        "absent": "nowhere",
        "settled": ["sealed"],
        "stages": [{"name": "note", "artifact": "note.txt", "statuses": ["open", "sealed"]}],
    }
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "states.json"
        path.write_text(json.dumps(other), encoding="utf-8")
        history = History(tmp, Data(Path(tmp) / "data"), machine=states.load(path))
        history.record("w", "0001_x", "note.txt", "open")
        history.record("w", "0001_x", "note.txt", "sealed")
        history.record("w", "0001_x", "note.txt", "sealed", source="commit:zz")
        rows = history.transitions("w", "0001_x")
        edits = settled_edits(rows, history.machine)
        projected = history.state("w", "0001_x")
    return say(
        len(rows) == 3 and len(edits) == 1 and projected == {"note.txt": "sealed"},
        "a state set loaded from a file — different stages, different words for settled — "
        "drives a unit end to end with no Python changed",
        f"rows={len(rows)} edits={len(edits)} state={projected}",
    )


def working_tree() -> set[str]:
    """`git status --porcelain`, as a set of lines."""
    return {line for line in git("status", "--porcelain").splitlines() if line.strip()}


def claim_5(before: set[str]) -> bool:
    """R7. Reading a repository leaves nothing of coscc's in it.

    Compared against a snapshot taken before the import rather than against an empty
    status. A clean tree is not what R7 asks for — the person running this may have work
    in progress, and a proof that failed on that would be measuring the developer instead
    of the product. What must be true is that **nothing new appeared**.
    """
    added = sorted(working_tree() - before)
    return say(
        not added,
        "reading this repository's history added nothing to its git status",
        "; ".join(added[:8]),
    )


def main() -> int:
    require_environment()

    cwd = str(REPO)
    before = working_tree()
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = str(Path(tmp) / "data")

        # Imported here rather than at the top: the import path is only correct once the
        # data root is known, and nothing above this line touches the product.
        from coscc import backfill
        from coscc.data import Data
        from coscc.history import History

        try:
            Data(data_dir).ensure_dir()
        except OSError as e:
            print(f"could not create a data root at {data_dir}: {e}")
            return EXIT_ENV

        if "--import" in sys.argv:
            # `plan.md` steps 6 and 7. Run without this flag the proof measures what the
            # product holds *right now*, which is how it is red before the import exists.
            try:
                summary = backfill.run(History(cwd, data_dir), REPO, workspace=cwd)
            except backfill.NotAGitCheckout as e:
                print(f"the import could not read this repository: {e}")
                return EXIT_ENV
            print(
                f"imported {summary['added']} of {summary['scanned']} transitions "
                f"over {len(summary['units'])} units"
            )

        wanted, wanted_detail = expected_from_git()
        got, got_detail, payloads = measured_from_product(cwd, data_dir)

        results = [
            claim_1(wanted, wanted_detail, got, got_detail),
            claim_2(payloads),
            claim_3(cwd, data_dir),
            claim_4(),
            claim_5(before),
        ]
    return EXIT_PASS if all(results) else EXIT_BROKEN


if __name__ == "__main__":
    raise SystemExit(main())
