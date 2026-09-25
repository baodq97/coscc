"""`0044` proof: Jera answers an open question from precedent, and only when asked.

Plain: no session, no quota, no network. A temporary data root and store, the app driven
in-process over ASGI, every session replaced by a stand-in that replies with fixed text,
and `Sessions.stream` itself replaced by a counter that fails if anything reaches it. Needs
`node`; missing is exit 2. Each claim prints `PASS R<n>` or `FAIL R<n>: <why>`: R1, R3, R5,
R7, R9, R11 and R14 (`cos.test.mjs`'s own test, run by name).

`--measure --cwd <workspace>` spends real money, up to 8 sessions at the `precedent` grant's
ceiling ($1.00 each, chosen, not measured). For each of the eight units `spec.md` R16 names
it takes the questions that already have an answer, hides the answers, builds the store of
precedent without any block of those eight units, and asks Jera. It writes into no artifact
and no run log: only `<COS_DATA_DIR>/measurements/0044-<YYYY-MM-DD>.json` and a `.md` beside
it, one row per question with the old answer, Jera's verdict and its category, for Leif to
grade against `spec.md` R6 and for the originator to sample ten (`intent.md ## Answers,
câu 3`). It prints `answer: <k>/<n> = <p>%` and `>= 60%: yes|no` (`intent.md ## Answers,
câu 1`).

Exit codes: 0 pass (or, with `--measure`, >= 60%), 1 broken (below 60%), 2 environment not
ready.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
COS_TEST = REPO / ".claude" / "scripts" / "cos.test.mjs"
EXIT_PASS, EXIT_BROKEN, EXIT_ENV = 0, 1, 2
# `spec.md` R16: the units whose open questions were answered when the intent was written.
MEASURED = ("0003", "0019", "0025", "0028", "0035", "0036", "0037", "0039")
THRESHOLD = 0.60


def say(line: str) -> None:
    print(line, flush=True)


def data_root() -> Path:
    return Path(os.environ.get("COS_DATA_DIR") or "~/.cos").expanduser()


def claim(ok: bool, req: str, why: str = "") -> bool:
    say(f"PASS {req}" if ok else f"FAIL {req}: {why or 'no reason given'}")
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


REAL_CALLS = {"n": 0}


def _no_real_session() -> None:
    """Every real session this proof could reach goes through `Sessions.stream`."""
    from coscc import sessions

    async def refuse(self, *a, **kw):  # noqa: ARG001
        REAL_CALLS["n"] += 1
        raise RuntimeError("verify_0044 must not open a real session")
        yield  # pragma: no cover — makes this an async generator

    sessions.Sessions.stream = refuse


def tree_hash(root: Path) -> dict:
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


INTENT = "# Intent: {t}\nAuthor: t. Type: feat. Status: accepted.\n\n## Problem\n\np\n"
PRECEDENT_SPEC = (
    "# Spec: earlier\nIntent: intent.md. Author: t. Status: accepted.\n\n## Requirements\n\nR1. x\n\n"
    "## Open questions\n\n1. Tên nhánh lấy từ đâu?\n\n## Answers\n\n### Câu 1\n"
    "Answered by: owner. Date: 2026-09-01. Via: product.\n\nLấy từ Type của intent.\n"
)
ASKED_SPEC = (
    "# Spec: now\nIntent: intent.md. Author: t. Status: draft.\n\n## Requirements\n\nR1. y\n\n"
    "## Open questions\n\n1. Tên nhánh của unit mới lấy từ đâu?\n2. Có nên tốn thêm tiền không?\n"
)
REVIEW = (
    "# Review: now\nPR: x. Author: t. Status: changes-requested.\n\n## Round 1\n"
    "Reviewed: abc. Verdict: changes-requested.\n\n### Findings\n\n- F1 [open] high: x\n"
)


def reply(*items: dict) -> str:
    return "Reasoning first.\n\n```json\n" + json.dumps(list(items), ensure_ascii=False) + "\n```\n"


async def proof(tmp: Path) -> bool:
    import httpx

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
    journal = Journal(tmp / "work", tmp / "data")
    key = service._journal_key(cwd)
    ok = True
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        async def make(slug: str, **files: str) -> str:
            made = (await client.post("/api/units", json={"cwd": cwd, "slug": slug, "brief": "words"})).json()
            for name, text in files.items():
                (Path(made["path"]) / f"{name}.md").write_text(text, encoding="utf-8")
            return made["unit"]

        earlier = await make("earlier", intent=INTENT.format(t="earlier"), spec=PRECEDENT_SPEC)
        asked = await make("asked", intent=INTENT.format(t="asked"), spec=ASKED_SPEC, review=REVIEW)
        store = service._units_root(cwd)
        spec_path = store / ".cos" / asked / "spec.md"
        cite = f"{earlier}/spec.md#Câu 1"

        async def board_unit(name: str) -> dict:
            data = (await client.get("/api/board", params={"cwd": cwd})).json()
            return next(u for u in data["units"] if u["name"] == name)

        async def ask() -> httpx.Response:
            return await client.post("/api/units/precedent", json={"cwd": cwd, "unit": asked})

        def jera_rows() -> list[dict]:
            return [r for r in journal.records(key) if r.get("kind") == "precedent"
                    or (r.get("kind") in ("start", "end") and r.get("stage") == "precedent")]

        # R1: a board read, a step run to its end and an answer start nothing of Jera's.
        try:
            await board_unit(asked)
            sessions.text = "no status line"
            await client.post("/api/board/run", json={"cwd": cwd, "unit": earlier, "stage": "plan"})
            await client.post("/api/units/answer", json={
                "cwd": cwd, "unit": earlier, "artifact": "spec.md", "question": 1, "answer": "Vẫn vậy."})
            await board_unit(asked)
            rows = jera_rows()
            ran = [r for r in journal.records(key, earlier) if r.get("kind") == "end" and r.get("stage") == "plan"]
            ok &= claim(not rows and bool(ran), "R1", f"{len(rows)} Jera rows in the run log, {len(ran)} plan steps ended")
        except Exception as e:  # noqa: BLE001 — a missing piece fails this line, not the proof
            ok &= claim(False, "R1", f"{type(e).__name__}: {e}")

        # R9 first, on a reply where every question needs a person; then R3 on a reply that
        # names only `review.md` and `F1`.
        try:
            before_q, before = (await board_unit(asked))["questions"], tree_hash(store)
            sessions.text = reply(
                {"artifact": "spec.md", "n": 1, "verdict": "needs-person", "category": "product-direction",
                 "text": "Đề xuất: lấy từ Type.", "reason": "hướng sản phẩm", "cites": []},
                {"artifact": "spec.md", "n": 2, "verdict": "needs-person", "category": "significant-spend",
                 "text": "Đề xuất: không.", "reason": "tiền", "cites": []},
            )
            r = await ask()
            after_q = (await board_unit(asked))["questions"]
            flags = [q.get("needs_person") for q in after_q]
            strip = lambda qs: [{k: q[k] for k in ("artifact", "n", "text", "answered")} for q in qs]  # noqa: E731
            ok &= claim(r.status_code == 200 and strip(before_q) == strip(after_q) and before == tree_hash(store)
                        and flags == [True, True],
                        "R9", f"{r.status_code} {r.text[:200]} flags={flags}")
        except Exception as e:  # noqa: BLE001
            ok &= claim(False, "R9", f"{type(e).__name__}: {e}")

        try:
            before = tree_hash(store)
            sessions.text = reply(
                {"artifact": "review.md", "n": "F1", "verdict": "answer", "category": "other",
                 "text": "Đã sửa.", "cites": [cite]},
                {"artifact": "review.md", "n": 1, "verdict": "answer", "category": "other",
                 "text": "Đã sửa.", "cites": [cite]},
            )
            r = await ask()
            direct = await service._append_answers(
                cwd, asked, [("review.md", "F1", "x"), ("review.md", 1, "x")],
                "Jera", "precedent", "agent:Jera", "precedent",
            )
            ok &= claim(r.status_code == 200 and before == tree_hash(store) and not direct["written"]
                        and len(direct["skipped"]) == 2,
                        "R3", f"{r.status_code} {r.text[:200]} direct={direct}")
        except Exception as e:  # noqa: BLE001
            ok &= claim(False, "R3", f"{type(e).__name__}: {e}")

        # R5: a citation the store did not hold writes nothing and leaves a needs-person row.
        try:
            before = spec_path.read_bytes()
            sessions.text = reply({"artifact": "spec.md", "n": 1, "verdict": "answer", "category": "other",
                                   "text": "Lấy từ Type.", "cites": ["9999_x/spec.md#Câu 1"]})
            r = await ask()
            last = [x for x in journal.records(key, asked, kind="precedent") if x.get("n") == 1][-1]
            ok &= claim(r.status_code == 200 and spec_path.read_bytes() == before
                        and last.get("verdict") == "needs-person",
                        "R5", f"{r.status_code} {last}")
        except Exception as e:  # noqa: BLE001
            ok &= claim(False, "R5", f"{type(e).__name__}: {e}")

        # R7: a valid verdict appends exactly one block and nothing above it moves; a question a
        # person answers while the session runs is skipped, with the reason logged.
        try:
            before = spec_path.read_bytes()
            sessions.text = reply(
                {"artifact": "spec.md", "n": 1, "verdict": "answer", "category": "other",
                 "text": "Lấy từ Type của intent, như unit trước.", "cites": [cite]},
                {"artifact": "spec.md", "n": 2, "verdict": "answer", "category": "other",
                 "text": "Không.", "cites": [cite]},
            )
            sessions.gate = asyncio.Event()
            running = asyncio.create_task(ask())
            for _ in range(200):
                if sessions.calls and service._active.get((key, asked)) is not None:
                    break
                await asyncio.sleep(0.01)
            person = await client.post("/api/units/answer", json={
                "cwd": cwd, "unit": asked, "artifact": "spec.md", "question": 2, "answer": "Có."})
            after_person = spec_path.read_bytes()
            sessions.gate.set()
            r = await running
            sessions.gate = None
            after = spec_path.read_bytes()
            added = after[len(after_person):].decode("utf-8")
            header = f"Answered by: Jera. Date: {date.today().isoformat()}. Via: precedent."
            skipped = [x for x in journal.records(key, asked, kind="precedent")
                       if x.get("n") == 2 and x.get("verdict") == "skipped"]
            ok &= claim(person.status_code == 200 and r.status_code == 200 and after.startswith(before)
                        and after.startswith(after_person) and added.count("### Câu") == 1
                        and "### Câu 1" in added and header in added and f"Tiền lệ: {cite}" in added
                        and bool(skipped) and bool(skipped[-1].get("reason")),
                        "R7", f"{person.status_code} {r.status_code} added={added!r} skipped={skipped}")
        except Exception as e:  # noqa: BLE001
            sessions.gate = None
            ok &= claim(False, "R7", f"{type(e).__name__}: {e}")

        # R11: nobody answers under Jera's name.
        try:
            r = await client.post("/api/units/answer", json={
                "cwd": cwd, "unit": asked, "artifact": "spec.md", "question": 1, "answer": "x",
                "answered_by": " jera "})
            ok &= claim(r.status_code == 400, "R11", f"{r.status_code} {r.text[:200]}")
        except Exception as e:  # noqa: BLE001
            ok &= claim(False, "R11", f"{type(e).__name__}: {e}")
    return ok


def r14() -> bool:
    r = subprocess.run(["node", "--test", "--test-name-pattern", "0044 R14", str(COS_TEST)],
                       capture_output=True, text=True, cwd=str(REPO))
    ran = "0044 R14" in r.stdout  # a pattern that matches nothing still exits 0
    return claim(r.returncode == 0 and ran, "R14", (r.stdout + r.stderr)[-400:])


# ---------------------------------------------------------------------------
# --measure: real sessions, on the real store
# ---------------------------------------------------------------------------


async def measure(cwd: str) -> int:
    from coscc import board as board_reader
    from coscc import models, precedent, units
    from coscc.config import from_env
    from coscc.data import Data
    from coscc.policy import grant_for
    from coscc.sessions import Sessions

    config = from_env()
    root = units.root(cwd, config.data_dir)
    data = await board_reader.read(root)
    wanted = [u for u in data["units"] if str(u.get("name") or "")[:4] in MEASURED]
    if not wanted:
        say(f"none of {', '.join(MEASURED)} is in {root}")
        return EXIT_ENV
    prefs = str(Data(config.data_dir).prefs().get("decision_preferences") or "")
    entries = precedent.entries(data["units"], prefs, {u["name"] for u in wanted})
    grant = grant_for("precedent")
    stored = Data(config.data_dir).prefs()
    model_over = {k[len(models.PREFIX):]: str(v) for k, v in stored.items() if k.startswith(models.PREFIX)}
    effort_over = {k[len(models.EFFORT_PREFIX):]: str(v) for k, v in stored.items()
                   if k.startswith(models.EFFORT_PREFIX)}
    defaults, _ = models.load_defaults()
    model, _, effort, _ = models.resolve(models.PRECEDENT, None, model_over, effort_over, defaults, config.model)
    sessions = Sessions(config)
    rows, runs = [], []
    try:
        for u in wanted:
            answered = {(a["artifact"], a["n"]): a for a in u.get("answers") or []}
            questions = [{"unit": u["name"], "artifact": q["artifact"], "n": q["n"], "text": q["text"]}
                         for q in u.get("questions") or []
                         if q.get("answered") and q.get("artifact") != "review.md"]
            if not questions:
                continue
            prompt = precedent.build_prompt(questions, entries)
            got, end, failure = await precedent.ask(sessions, cwd, prompt, grant, model, effort)
            found = precedent.verdicts(got, questions, {e["id"] for e in entries})
            runs.append({"unit": u["name"], "questions": len(questions), "failed": failure or found["failed"],
                         "cost": end.get("cost") or {}, "session_id": end.get("session_id", "")})
            for v in found["verdicts"]:
                old = answered.get((v["artifact"], v["n"])) or {}
                rows.append({"unit": u["name"], **v, "old_answer": old.get("text", ""), "old_by": old.get("by", "")})
    finally:
        await sessions.close_all()
    n = len(rows)
    k = sum(1 for r in rows if r["verdict"] == "answer")
    share = k / n if n else 0.0
    out = data_root() / "measurements" / f"0044-{date.today().isoformat()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"runs": runs, "rows": rows, "answer": k, "of": n, "share": share,
                               "threshold": THRESHOLD, "entries": len(entries)},
                              indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md = [f"# 0044 --measure, {date.today().isoformat()}", "",
          "| unit | artifact | câu | câu trả lời cũ | kết luận | category | chữ của Jera | tiền lệ | chấm |",
          "|---|---|---|---|---|---|---|---|---|"]
    cell = lambda s: str(s).replace("|", "\\|").replace("\n", " ")  # noqa: E731
    for r in rows:
        md.append(f"| {r['unit']} | {r['artifact']} | {r['n']} | {cell(r['old_answer'])} | {r['verdict']} | "
                  f"{r['category']} | {cell(r['text'])} | {cell('; '.join(r['cites']))} | |")
    out.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")
    say(f"answer: {k}/{n} = {share * 100:.0f}%")
    say(f">= 60%: {'yes' if n and share >= THRESHOLD else 'no'}")
    say(f"cost: ${sum(float((r['cost'] or {}).get('cost_usd') or 0) for r in runs):.2f} over {len(runs)} sessions")
    say(f"wrote {out} and {out.with_suffix('.md')}")
    return EXIT_PASS if n and share >= THRESHOLD else EXIT_BROKEN


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--measure", action="store_true", help="R16: real sessions, costs money")
    parser.add_argument("--cwd", help="the workspace whose store --measure reads")
    args = parser.parse_args()
    if not shutil.which("node"):
        say("environment: node is needed")
        return EXIT_ENV
    if args.measure:
        if not args.cwd:
            say("--measure needs --cwd <workspace>")
            return EXIT_ENV
        return asyncio.run(measure(args.cwd))
    _no_real_session()
    with tempfile.TemporaryDirectory() as d:
        ok = asyncio.run(proof(Path(d) / "proof"))
    ok &= r14()
    say(f"real sessions opened: {REAL_CALLS['n']}")
    ok &= REAL_CALLS["n"] == 0
    say("all claims pass" if ok else "some claims failed")
    return EXIT_PASS if ok else EXIT_BROKEN


if __name__ == "__main__":
    sys.exit(main())
