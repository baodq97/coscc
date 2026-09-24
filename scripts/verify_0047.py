#!/usr/bin/env python3
"""Proof for `0047_outcomes-are-measured-by-hand-if-at-all`.

The plan's seven claims, C1-C7: a finished unit's outcome is recorded as a `### Outcome`
block appended to `intent.md` and nothing above it moves (C1); the block changes nothing
`cos.mjs` decides but `outcome` (C2), and no answer above it (C3); every bad request writes
nothing (C4); the board labels each unit against its deadline (C5); a deadline passing
writes nothing and starts nothing (C6); and the run log records the block as a person's (C7).

    0  every claim held
    1  at least one did not
    2  the environment could not answer: no `node`, or no `uv`

**No session, no quota, no network.** Everything is written under one temporary directory,
set as both `COS_DATA_DIR` and `COS_WORKING_DIR` before the app is imported, so `~/.cos` is
never opened. C5 fixes the board's `today` at 2026-10-08, the day the intent names.

**This does not measure the outcome in `intent.md`.** That is three real units, `0019`,
`0025` and `0037`, carrying a block and a label on the real board on 2026-10-08.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.proof_harness import EXIT_BROKEN, EXIT_ENV, EXIT_PASS, say  # noqa: E402

STAGES = ("idea", "intent", "spec", "plan", "impl", "pr", "review", "ship")


def fixture(deadline: str | None) -> str:
    outcome = f"By {deadline}, three of three." if deadline else "Soon, and no date is written."
    return (
        "# Intent: an outcome the proof invented\n"
        "Author: verify_0047. Type: feat. Status: accepted.\n\n"
        f"## Proposed outcome\n\n{outcome}\n\n"
        "## Open questions\n\n1. **First?**\n\n"
        "## Answers\n\n### Câu 1\nAnswered by: verify_0047. Date: 2026-09-24. Via: product.\n\n"
        "Có, và dòng này phải giữ nguyên.\n"
    )


def require_environment() -> None:
    for tool in ("node", "uv"):
        if shutil.which(tool) is None:
            print(f"no {tool} on PATH — this proof cannot answer without it")
            raise SystemExit(EXIT_ENV)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cos(units_root: Path, *args: str) -> subprocess.CompletedProcess:
    from coscc import harness

    return subprocess.run(
        ["node", str(harness.script()), "--root", str(units_root), *args],
        capture_output=True, text=True, timeout=60,
    )


def unit_json(units_root: Path, unit: str) -> dict:
    [u] = [x for x in json.loads(cos(units_root, "status", "--json").stdout)["units"] if x["name"] == unit]
    return u


def gates(units_root: Path, unit: str) -> list[int]:
    return [cos(units_root, "gate", unit, stage).returncode for stage in STAGES]


class Oct8(datetime.date):
    """The board's `today` for C5: the day after `0019`, `0025` and `0037` fall due."""

    @classmethod
    def today(cls):
        return cls(2026, 10, 8)


async def run(root: Path) -> bool:
    from coscc import service as service_module
    from coscc.config import Config
    from coscc.history import History
    from coscc.service import Invalid, Service
    from coscc.sessions import Sessions

    workspace = root / "work" / "proj"
    workspace.mkdir(parents=True)
    cwd = str(workspace)
    data_dir = root / "data"
    config = Config(workspaces=(cwd,), working_dir=str(root / "work"), data_dir=str(data_dir))
    service = Service(config, Sessions(config))
    if not hasattr(service, "record_outcome"):
        print("note: Service.record_outcome does not exist in this tree")
    units_root = service._units_root(cwd)

    async def make(slug: str, deadline: str | None, finished: bool = True) -> tuple[str, Path]:
        made = await service.create_unit(cwd, slug, "verify_0047 fixture")
        directory = Path(made["path"])
        # Fixture data for the proof, written before anything is measured. Not a hand edit
        # of a real artifact: these units live in a temporary directory and die with it.
        (directory / "intent.md").write_text(fixture(deadline), encoding="utf-8")
        for stage in ("spec", "impl", "pr", "review", "ship"):
            (directory / f"{stage}.md").write_text(f"# {stage}\nStatus: accepted.\n", encoding="utf-8")
        (directory / "plan.md").write_text(
            f"# plan\nStatus: {'done' if finished else 'accepted'}.\n", encoding="utf-8"
        )
        if not finished:
            (directory / "ship.md").unlink()
        return made["unit"], directory

    three = [await make(f"due-unit-{i}", "2026-10-07") for i in (1, 2, 3)]
    later, _ = await make("a-later-unit", "2026-10-31")
    undated, _ = await make("an-undated-unit", None)
    open_unit, open_dir = await make("an-unfinished-unit", "2026-10-07", finished=False)
    first, first_dir = three[0]
    intent = first_dir / "intent.md"

    async def record(unit: str, **over):
        kw = {"result": "đạt", "measured_by": "agent", "source": "verify_0047, npm test",
              "reason": "", "note": "", "recorded_by": "verify_0047", **over}
        return await service.record_outcome(cwd, unit, **kw)

    ok = True

    # C4 (R7) first, on untouched files.
    pristine = sha(intent)
    bad = {
        "result not one of three": {"result": "maybe"},
        "đạt without a source": {"source": ""},
        "không đo được without a reason": {"result": "không đo được", "source": ""},
        "no name": {"recorded_by": " "},
        "a name on two lines": {"recorded_by": "A\nStatus: rejected"},
        "no measured by": {"measured_by": ""},
        "a line starting with #": {"note": "ok\n## Status: rejected"},
    }
    said: dict[str, str] = {}
    for name, over in bad.items():
        try:
            await record(first, **over)
            said[name] = ""
        except Invalid as e:
            said[name] = str(e)
    open_before = sha(open_dir / "intent.md")
    try:
        await record(open_unit)
        said["an unfinished unit"] = ""
    except Invalid as e:
        said["an unfinished unit"] = str(e)
    sectioned, sectioned_dir = await make("a-sectioned-unit", "2026-10-07")
    (sectioned_dir / "intent.md").write_text(fixture("2026-10-07") + "\n## Later\n\nx\n", encoding="utf-8")
    sectioned_before = sha(sectioned_dir / "intent.md")
    try:
        await record(sectioned)
        said["a section after ## Answers"] = ""
    except Invalid as e:
        said["a section after ## Answers"] = str(e)
    ok &= say(
        len(said) == 9 and all(said.values())
        and sha(intent) == pristine
        and sha(open_dir / "intent.md") == open_before
        and sha(sectioned_dir / "intent.md") == sectioned_before,
        "C4 nine bad requests are each refused with words and change no byte",
        json.dumps(said, ensure_ascii=False),
    )

    # C2 and C3 need the state before any block.
    before_json = unit_json(units_root, first)
    before_gates = gates(units_root, first)

    # C1 (R1)
    before_bytes = intent.read_bytes()
    await record(first, note="Ghi chú của proof.")
    after_bytes = intent.read_bytes()
    names = sorted(p.name for p in first_dir.iterdir())
    ok &= say(
        after_bytes[:len(before_bytes)] == before_bytes
        and "### Outcome" in after_bytes[len(before_bytes):].decode("utf-8")
        and set(names) <= {f"{s}.md" for s in STAGES},
        "C1 the block is appended, every byte before it is unchanged, and the unit holds only artifacts",
        f"prefix kept={after_bytes[:len(before_bytes)] == before_bytes}, files={names}",
    )

    # C2 (R5): one valid block from the service, one broken block appended where a block goes.
    with intent.open("a", encoding="utf-8") as f:
        f.write("\n### Outcome\nno header line\n\nResult: đạt\n")
    after_json = unit_json(units_root, first)
    after_gates = gates(units_root, first)
    strip = lambda u: {k: v for k, v in u.items() if k != "outcome"}  # noqa: E731
    ok &= say(
        strip(after_json) == strip(before_json)
        and after_json["next"] == before_json["next"]
        and after_json["problems"] == before_json["problems"]
        and after_gates == before_gates
        and (after_json.get("outcome") or {}).get("result") == "met"
        and (after_json.get("outcome") or {}).get("invalid") == 1,
        "C2 status --json differs only in outcome, and every gate exits as before",
        f"next={after_json['next']}, gates before={before_gates} after={after_gates}, "
        f"outcome={json.dumps(after_json.get('outcome'), ensure_ascii=False)}",
    )

    # C3 (R6)
    answer_of = lambda u: [  # noqa: E731
        q.get("answer", {}).get("text")
        for q in ((u.get("artifacts") or {}).get("intent.md") or {}).get("questions") or []
        if q.get("n") == 1
    ]
    ok &= say(
        answer_of(after_json) == answer_of(before_json) == ["Có, và dòng này phải giữ nguyên."],
        "C3 the answer to question 1 reads the same with an Outcome block after it",
        f"before={answer_of(before_json)}, after={answer_of(after_json)}",
    )

    # C5 (R8), with the board's today fixed at 2026-10-08.
    async def labels() -> dict[str, dict | None]:
        with mock.patch.object(service_module, "date", Oct8):
            data = await service.board(cwd)
        return {u["name"]: u.get("outcome_label") for u in data["units"]}

    # The first unit already carries a block; its two siblings show the state before one.
    seen = await labels()
    due_before = [(seen.get(u) or {}).get("text") for u, _ in three[1:]]
    await record(three[1][0], result="trượt", source="verify_0047, board")
    await record(three[2][0], result="không đo được", source="", reason="không có script")
    seen = await labels()
    got = [(seen.get(u) or {}) for u, _ in three]
    ok &= say(
        due_before == ["tới hạn — chưa đo"] * 2
        and [g.get("text") for g in got] == ["đạt", "trượt", "không đo được"]
        and got[1].get("hint") == "cân nhắc bỏ hoặc làm lại"
        and got[2].get("counted") is False
        and (seen.get(later) or {}).get("text") == "chưa tới hạn"
        and seen.get(undated) is None,
        "C5 due, then đạt / trượt (with its hint) / không đo được (not counted); later is chưa tới hạn; no date, no label",
        f"before={due_before}, after={[g.get('text') for g in got]}, "
        f"later={(seen.get(later) or {}).get('text')}, undated={seen.get(undated)}",
    )

    # C6 (R9): reading a board with an overdue, unrecorded unit writes nothing and starts nothing.
    journal = service._journal()
    key = service._journal_key(cwd)
    rows_before = len(journal.records(key))
    seen = await labels()
    await labels()
    rows_after = len(journal.records(key))
    sessions = service.sessions_for(cwd)["sessions"]
    ok &= say(
        (seen.get(open_unit) or {}).get("text") == "tới hạn — chưa đo"
        and rows_after == rows_before and sessions == [],
        "C6 two board reads over an overdue unit add no run-log row and no session",
        f"rows {rows_before}->{rows_after}, sessions={len(sessions)}",
    )

    # C7
    rows = History(str(root / "work"), data_dir).outputs(str(workspace.resolve()), first)
    mine = [r for r in rows if r.get("source") == "outcome"]
    ok &= say(
        len(mine) == 1 and mine[0].get("actor") == "human:verify_0047" and mine[0].get("path") == "intent.md",
        "C7 one outputs row as human:<name> with source outcome",
        f"rows={[(r.get('actor'), r.get('source'), r.get('path')) for r in mine]}",
    )
    return bool(ok)


def main() -> int:
    require_environment()
    with tempfile.TemporaryDirectory(prefix="verify-0047-") as d:
        root = Path(d)
        # Before `coscc` is imported, as `verify_0016` does: nothing may open `~/.cos`.
        os.environ["COS_DATA_DIR"] = str(root / "data")
        os.environ["COS_WORKING_DIR"] = str(root / "work")
        for name, fallback in (("COS_PORT", "8790"), ("COS_HOST", "127.0.0.1")):
            if not os.environ.get(name, "").strip():
                os.environ[name] = fallback
        print(f"temporary data root: {root}")
        return EXIT_PASS if asyncio.run(run(root)) else EXIT_BROKEN


if __name__ == "__main__":
    raise SystemExit(main())
