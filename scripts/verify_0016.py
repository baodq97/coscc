#!/usr/bin/env python3
"""Proof for the store's `0016_no-human-in-the-loop`.

The plan's six claims, C1-C6: a person can answer an item under `## Open questions`
through the app, the answer is appended and nothing above it moves, a bad request writes
nothing, the next stage's prompt carries the answer, and the run log records it as a
person's.

    0  every claim held
    1  at least one did not
    2  the environment could not answer: no `node`, or no `uv`

**No session, no quota, no network.** Nothing here runs a stage; C5 builds the prompt a
stage would receive and reads it. Everything is written under one temporary directory,
set as both `COS_DATA_DIR` and `COS_WORKING_DIR` before the app is imported, so
`~/.cos` is never opened.

**This does not measure the outcome in `intent.md`.** That needs a real answer on a real
artifact and a real, paid stage that uses it. It is measured at `ship`, not here.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.proof_harness import EXIT_BROKEN, EXIT_ENV, EXIT_PASS, say  # noqa: E402

MARK = "ANSWER-MARK-0016-7f3a"

FIXTURE = (
    "# Intent: a question the proof invented\n"
    "Author: verify_0016. Type: feat. Status: accepted.\n\n"
    "## Problem\n\n"
    "1. A numbered line that is not a question, because it is not under Open questions.\n\n"
    "## Open questions\n\n"
    "1. **First?** Asked here\n"
    "   and continued on this line.\n"
    "2. **Second?** The one this proof answers.\n"
    "3. **Third?**\n"
)


def require_environment() -> None:
    for tool in ("node", "uv"):
        if shutil.which(tool) is None:
            print(f"no {tool} on PATH — this proof cannot answer without it")
            raise SystemExit(EXIT_ENV)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def status_json(units_root: Path) -> dict:
    from coscc import harness

    done = subprocess.run(
        ["node", str(harness.script()), "--root", str(units_root), "status", "--json"],
        capture_output=True, text=True, timeout=60,
    )
    return json.loads(done.stdout)


async def run(root: Path) -> bool:
    import httpx

    from coscc import board
    from coscc.api import build
    from coscc.config import Config
    from coscc.history import History
    from coscc.runner import build_prompt

    try:
        from coscc.state import _questions
    except ImportError:  # a tree without `0016`: C1 fails by name instead of a traceback
        def _questions(unit: dict) -> tuple[int, list]:
            return -1, []

    workspace = root / "work" / "proj"
    workspace.mkdir(parents=True)
    cwd = str(workspace)
    data_dir = root / "data"
    app = build(Config(workspaces=(cwd,), working_dir=str(root / "work"), data_dir=str(data_dir)))
    service = app.state.service

    if not hasattr(service, "answer"):
        print("note: Service.answer does not exist in this tree")
    made = await service.create_unit(cwd, "a-question-the-proof-invented", "verify_0016 fixture")
    unit, directory = made["unit"], Path(made["path"])
    intent = directory / "intent.md"
    # Fixture data for the proof, written before anything is measured. Not a hand edit of
    # a real artifact: this unit lives in a temporary directory and dies with it.
    intent.write_text(FIXTURE, encoding="utf-8")
    units_root = service._units_root(cwd)

    ok = True

    # C1 (R1, R7)
    before = status_json(units_root)
    [u] = [x for x in before["units"] if x["name"] == unit]
    [through_board] = [x for x in (await board.read(units_root))["units"] if x["name"] == unit]
    waiting, _ = _questions(through_board)
    ok &= say(
        u.get("open") == 3 and waiting == 3,
        "C1 status --json and the board both count 3 open",
        f"status --json open={u.get('open')}, board open_questions={waiting}",
    )

    body = {
        "cwd": cwd, "unit": unit, "artifact": "intent.md", "question": 2,
        "answer": f"Tách ra. {MARK}", "answered_by": "verify_0016",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proof"
    ) as client:
        # C4 (R3) first, on the untouched file: seven bad requests, nothing written.
        pristine = sha(intent)
        bad = {
            "(a) no such unit": {"unit": "0099_nothing"},
            "(b) artifact without questions": {"artifact": "spec.md"},
            "(c) no such question": {"question": 9},
            "(d) empty answer": {"answer": "  \n "},
            "(e) no name": {"answered_by": " "},
            "(g) a line that reads as a heading": {"answer": "ok\n## Status: rejected"},
        }
        codes = {}
        for name, over in bad.items():
            got = await client.post("/api/units/answer", json=body | over)
            codes[name] = got.status_code
        # (f) needs a closed unit: a second unit, rejected, with the same questions.
        closed = await service.create_unit(cwd, "a-closed-unit-the-proof-invented", "verify_0016")
        closed_intent = Path(closed["path"]) / "intent.md"
        closed_intent.write_text(FIXTURE.replace("Status: accepted", "Status: rejected"), encoding="utf-8")
        closed_before = sha(closed_intent)
        got = await client.post("/api/units/answer", json=body | {"unit": closed["unit"]})
        codes["(f) closed unit"] = got.status_code
        ok &= say(
            len(codes) == 7
            # 400 exactly, not any 4xx: a tree with no such route answers 404 to every one
            # of these and writes nothing, and that must not count as refusing them.
            and all(c == 400 for c in codes.values())
            and sha(intent) == pristine
            and sha(closed_intent) == closed_before,
            "C4 seven bad requests are each refused and change no byte",
            json.dumps(codes, ensure_ascii=False),
        )

        # C2 (R2)
        before_bytes = intent.read_bytes()
        got = await client.post("/api/units/answer", json=body)
        text = intent.read_text(encoding="utf-8")
        ok &= say(
            got.status_code == 200 and "### Câu 2" in text and body["answer"] in text,
            "C2 the answer is accepted and is on disk under ### Câu 2",
            f"{got.status_code} {got.text[:200]}",
        )

    # C3 (R4)
    after_bytes = intent.read_bytes()
    after = status_json(units_root)
    [u2] = [x for x in after["units"] if x["name"] == unit]
    # `parseStatus`'s reading, as `status --json` reports it per artifact file.
    status = ((u2.get("artifacts") or {}).get("intent.md") or {}).get("status")
    tail = after_bytes[len(before_bytes):].decode("utf-8", "replace")
    ok &= say(
        after_bytes.startswith(before_bytes) and "## Answers" in tail and status == "accepted",
        "C3 every byte before ## Answers is unchanged and the status still reads accepted",
        f"prefix kept={after_bytes.startswith(before_bytes)}, status={status}",
    )

    # C5 (R5)
    stages = [s["name"] if isinstance(s, dict) else s for s in after.get("stages") or []]
    prompt, _ = build_prompt(cwd, directory, unit, "spec", stages, "spec.md")
    ok &= say(MARK in prompt, "C5 the prompt for spec carries the answer", f"stages={stages}")

    # C6 (R9 as the plan redefined it)
    rows = History(str(root / "work"), data_dir).outputs(str(workspace.resolve()), unit)
    mine = [r for r in rows if r.get("source") == "answer"]
    ok &= say(
        len(mine) == 1 and mine[0].get("actor") == "human:verify_0016" and u2.get("open") == 2,
        "C6 one outputs row as human:<name>, and 2 still open",
        f"rows={[(r.get('actor'), r.get('source')) for r in mine]}, open={u2.get('open')}",
    )
    return bool(ok)


def main() -> int:
    require_environment()
    with tempfile.TemporaryDirectory(prefix="verify-0016-") as d:
        root = Path(d)
        # Before `coscc` is imported: `coscc/state.py` builds an app from the environment
        # at import, and that app must open this directory, not `~/.cos`.
        os.environ["COS_DATA_DIR"] = str(root / "data")
        os.environ["COS_WORKING_DIR"] = str(root / "work")
        for name, fallback in (("COS_PORT", "8790"), ("COS_HOST", "127.0.0.1")):
            if not os.environ.get(name, "").strip():
                os.environ[name] = fallback
        print(f"temporary data root: {root}")
        return EXIT_PASS if asyncio.run(run(root)) else EXIT_BROKEN


if __name__ == "__main__":
    raise SystemExit(main())
