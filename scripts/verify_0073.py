"""`0073` proof: a running step can be watched from another browser, and read back after.

Plain: no session, no quota, no network. A temporary data root, a workspace that is not a
git checkout (so no remote and no `gh` are reached), the app driven in-process over ASGI,
and only `ClaudeSDKClient` in `coscc.sessions` replaced by a scripted client that also asks
the grant's gate. Claims R1-R9, R13-R15. Needs `node`; missing is exit 2.

`--browser` starts `coscc.run` in a child process on `COS_PORT` (default 18773) with the
same kind of scripted client, writes a password and two sessions into a temporary data
root, and drives Google Chrome through two contexts that share no cookie: A starts the
step, B watches it. Claims R10, R11 (on a list filled to `WATCH_WINDOW` too), R12, the row
being read staying put on a running and on an ended step (`review.md` F5, F6), and the
reading back after `end`. Needs a bundle
built for that port (`COS_HOST=127.0.0.1 COS_PORT=18773 uv run coscc-build`), playwright
and chrome; any missing is exit 2. Loopback, headless, one machine (spec C8): it does not
measure the intent's outcome, which is a person's two-browser trial before 2026-10-15.

Exit codes: 0 pass, 1 broken, 2 environment not ready.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
COS = REPO / ".claude" / "scripts" / "cos.mjs"
EXIT_PASS, EXIT_BROKEN, EXIT_ENV = 0, 1, 2
INTENT = "# Intent: x\nAuthor: t. Type: feat. Status: accepted.\n\n## Problem\n\np\n"
SPEC = "# Spec: x\nAuthor: t. Status: accepted.\n\n## Requirements\n\nr\n"


def say(line: str) -> None:
    print(line, flush=True)


def claim(ok: bool, text: str, detail: str = "") -> bool:
    say(f"{'PASS' if ok else 'FAIL'}  {text}{': ' + detail if detail and not ok else ''}")
    return ok


# -- the scripted client ------------------------------------------------------------


class Script:
    """A `ClaudeSDKClient` that replays `Script.plan`: SDK messages, `"deny"` (asks the
    grant's gate for a command it refuses), an `asyncio.Event` to wait on, or a callable
    returning either. Spends nothing."""

    plan: list = []

    def __init__(self, options=None):
        self.options = options
        self._transport = None

    async def connect(self):
        pass

    async def query(self, text):
        pass

    async def receive_response(self):
        for item in list(type(self).plan):
            if callable(item) and not isinstance(item, type):
                item = item()
                if asyncio.iscoroutine(item):
                    item = await item
                if item is None:
                    continue
            if item == "deny":
                await self.options.can_use_tool("Bash", {"command": "rm -rf /"}, None)
                continue
            if isinstance(item, asyncio.Event):
                await item.wait()
                continue
            yield item

    async def disconnect(self):
        pass


class Odd:
    """A message kind no SDK sends today: it must arrive as `system`."""

    def __init__(self):
        self.what = "odd"


def messages():
    import claude_agent_sdk as sdk

    return sdk


def result(turns: int):
    sdk = messages()
    return sdk.ResultMessage(
        subtype="success", duration_ms=10, duration_api_ms=10, is_error=False,
        num_turns=turns, session_id="proof-73", total_cost_usd=0.01,
        model_usage={"m": {"inputTokens": 1, "outputTokens": 2, "cacheReadInputTokens": 3,
                           "cacheCreationInputTokens": 4}},
    )


def assistant(mid: str, *blocks):
    return messages().AssistantMessage(content=list(blocks), model="m", message_id=mid, session_id="proof-73")


# -- plain --------------------------------------------------------------------------


def counts(db: Path) -> dict:
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("runs", "transitions", "outputs")}


def gate_answers(store: Path, units: list[str], stages: list[str]) -> list[tuple]:
    out = []
    for u in units:
        for args in [("gate", u, s) for s in stages] + [("next", u)]:
            r = subprocess.run(["node", str(COS), "--root", str(store), *args],
                               capture_output=True, text=True, cwd=str(REPO))
            out.append((args, r.returncode, r.stdout, r.stderr))
    return out


async def plain(tmp: Path) -> bool:
    import httpx
    from unittest import mock

    import claude_agent_sdk as sdk

    from coscc import auth, events
    from coscc import service as service_mod
    from coscc import sessions as sessions_mod
    from coscc.api import build
    from coscc.config import Config
    from coscc.data import Busy, Data
    from coscc.journal import Journal

    repo = tmp / "work" / "proj"
    repo.mkdir(parents=True)
    config = Config(workspaces=(str(repo),), working_dir=str(tmp / "work"), data_dir=str(tmp / "data"))
    app = build(config)
    service = app.state.service
    cwd = str(repo)
    key = service._journal_key(cwd)
    data = Data(config.data_dir)
    journal = Journal(tmp / "work", data)
    ok = True

    # R6: when each write of events finished, and when the `end` record was written.
    writes: list[tuple[float, float, list[int], str]] = []
    real_add = Data.step_events_add

    def timed_add(self, run, rows, timeout=None):
        began = time.time()
        added = real_add(self, run, rows, timeout=timeout)
        writes.append((began * 1000, time.time() * 1000, [r["seq"] for r in rows], run))
        return added

    ends: dict[str, float] = {}
    real_finished = Journal.finished

    def timed_finished(self, workspace, unit, stage, outcome, **extra):
        ends[unit] = time.time() * 1000
        return real_finished(self, workspace, unit, stage, outcome, **extra)

    patches = [
        mock.patch.object(sessions_mod, "ClaudeSDKClient", Script),
        mock.patch.object(Data, "step_events_add", timed_add),
        mock.patch.object(Journal, "finished", timed_finished),
    ]
    for p in patches:
        p.start()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            async def make(slug: str) -> str:
                made = (await client.post("/api/units", json={"cwd": cwd, "slug": slug, "brief": "words"})).json()
                (Path(made["path"]) / "intent.md").write_text(INTENT, encoding="utf-8")
                return made["unit"]

            watched, same_a, same_b, busy, slow = [await make(s) for s in (
                "watched", "same-a", "same-b", "busy-to-the-end", "a-slow-reader")]
            store = service._units_root(cwd)

            async def run(unit: str) -> dict:
                r = await client.post("/api/board/run", json={"cwd": cwd, "unit": unit, "stage": "spec"})
                lines = [json.loads(x) for x in r.text.splitlines() if x.strip()]
                return lines[-1] if lines else {}

            def get(path: str, unit: str, **params):
                return client.get(path, params={"cwd": cwd, "unit": unit, **params})

            # ---- the watched step, held in the middle -------------------------------
            hold = asyncio.Event()
            Script.plan = [
                assistant("m1", sdk.TextBlock("thinking out loud"), sdk.ThinkingBlock("hmm", "sig")),
                assistant("m1", sdk.ToolUseBlock("t1", "Read", {"file_path": "x"})),
                *["deny"] * 7,
                sdk.UserMessage(content=[sdk.ToolResultBlock("t1", "contents", False)]),
                sdk.UserMessage(content=[sdk.ToolResultBlock("t2", "preview", False)],
                                tool_use_result={"persistedOutputPath": "/tmp/p.txt", "persistedOutputSize": 90000}),
                sdk.SystemMessage(subtype="init", data={}),
                sdk.RateLimitEvent(rate_limit_info=None, uuid="u", session_id="proof-73"),
                Odd(),
                assistant("m2", sdk.ThinkingBlock("A" * 70_000, "sig")),
                hold,
                assistant("m2", sdk.TextBlock(SPEC)),
                result(2),
            ]
            step = asyncio.create_task(run(watched))
            while not service._recorders or next(iter(service._recorders.values())).seq < 17:
                await asyncio.sleep(0.01)
            [rid] = list(service._recorders)
            await asyncio.sleep(1.3)  # R6: past one write
            running = (await client.get("/api/board/running", params={"cwd": cwd})).json()
            steps = (await client.get("/api/board/steps", params={"cwd": cwd})).json()
            live_page = (await get("/api/board/events", watched, run=rid, before="18", limit="5")).json()
            live_one = (await get("/api/board/events", watched, run=rid, seq="3")).json()
            follow = asyncio.create_task(get("/api/board/events/follow", watched, run=rid, after="10"))
            await asyncio.sleep(0.1)
            hold.set()
            done = await step
            followed = [json.loads(x) for x in (await follow).text.splitlines() if x.strip()]

            ok &= claim(done.get("outcome") == "done", "the watched step ends done", str(done))
            starts = journal.records(key, watched, kind="start")
            finals = journal.records(key, watched, kind="end")
            stored, _ = data.step_events_page(rid, None, 500)
            ok &= claim(
                running["running"][watched][0]["run"] == rid and steps[0]["run"] == rid
                and starts[-1].get("run") == rid and finals[-1].get("run") == rid
                and all(e["run"] == rid for e in stored),
                "R1: run is on /api/board/running, /api/board/steps, start, end and every event",
            )
            chat_before = len(data.step_events_page("none", None, 1)[0])
            with data.connect() as conn:
                rows_before = conn.execute("SELECT COUNT(*) FROM step_runs").fetchone()[0]
            Script.plan = [assistant("c1", sdk.TextBlock("hi")), result(1)]
            async for _ in service.stream(cwd, "hello"):
                pass
            with data.connect() as conn:
                rows_after = conn.execute("SELECT COUNT(*) FROM step_runs").fetchone()[0]
            ok &= claim(rows_before == rows_after and chat_before == 0, "R1: a chat turn writes no step_runs row")
            say("NOTE  R1: integrate (Gebo) is not run here -- it needs git and gh; it passes no step handle")

            seqs = [e["seq"] for e in stored]
            kinds = [e["kind"] for e in stored]
            want = ["turn", "text", "thinking", "tool_use", *["denied"] * 7, "tool_result", "tool_result",
                    "system", "system", "system", "turn", "thinking", "text", "result", "end"]
            end = stored[-1]
            ok &= claim(seqs == list(range(1, len(stored) + 1)), "R2: seq runs 1..N with no gap", str(seqs))
            ok &= claim(kinds == want, "R3: every kind, in order, denied x7, system x3", str(kinds))
            turns = [e["n"] for e in stored if e["kind"] == "turn"]
            ok &= claim(turns[-1] == 2 and stored[-3]["kind"] == "text" and stored[-2]["num_turns"] == 2,
                        "R3: the last turn equals the script's num_turns")
            ok &= claim(stored[12].get("persisted_path") == "/tmp/p.txt", "R3: tool_result carries persisted_path")
            ok &= claim((end["outcome"], end["detail"]) == (finals[-1]["outcome"], finals[-1].get("detail") or ""),
                        "R3: end matches the end record's outcome and detail")
            long = stored[-4]
            ok &= claim(len(long["thinking"]) == 64_000 and long["truncated"] and long["length"] == 70_000,
                        "R4: a 70 000-character field is stored as 64 000, truncated, length 70000")

            mine = [w for w in writes if w[3] == rid]
            slowest = max(w[1] - w[0] for w in mine)
            written_at = {s: w[1] for w in mine for s in w[2]}
            late = max(written_at[e["seq"]] - e["at"] for e in stored)
            ok &= claim(late <= 1000 + 2 * slowest + 50,
                        f"R6: every event is on disk within 1000 ms of its at plus the write (worst {late:.0f} ms, "
                        f"slowest write {slowest:.0f} ms)")
            ok &= claim(max(written_at.values()) <= ends[watched], "R6: every event was stored before the end record")
            row = data.step_run(rid)
            ok &= claim(row["events"] == len(stored) and row["bytes"] > 0 and row["ended_at"] and row["stage"] == "spec",
                        "R6: the index row has what it should", str(row))

            page = (await get("/api/board/events", watched, run=rid)).json()
            capped = (await get("/api/board/events", watched, run=rid, limit="9999")).json()
            before = (await get("/api/board/events", watched, run=rid, before="5", limit="2")).json()
            one = (await get("/api/board/events", watched, run=rid, seq="3")).json()
            again = (await get("/api/board/events", watched, run=rid, before="18", limit="5")).json()
            ok &= claim(len(page["events"]) == len(stored) and capped["events"] == page["events"]
                        and [e["seq"] for e in before["events"]] == [3, 4] and one["events"] == live_one["events"],
                        "R7: the default page, the cap, before and seq")
            ok &= claim(again["events"] == live_page["events"] and len(again["events"]) == 5
                        and live_page["status"] == "running" and again["status"] == "ended",
                        "R7: the same pages while running and after")
            data.step_run_open("r-unknown", str(tmp / "work"), key, watched, "plan", 1000)
            journal.started(key, watched, "plan", "manual", run="r-none")
            unknown = service.events_page(cwd, watched, "r-unknown")
            nothing = service.events_page(cwd, watched, "r-none")
            ok &= claim((unknown["status"], nothing["status"]) == ("ended-unknown", "none"),
                        "R7: ended-unknown and none", f"{unknown['status']} {nothing['status']}")
            ok &= claim(PAGE_OK(events), "R7: 200 by default, 500 at most")

            f_seqs = [x["seq"] for x in followed if x["type"] == "event"]
            ok &= claim(f_seqs == list(range(11, len(stored) + 1)) and followed[-1].get("kind") == "end",
                        "R8: following from after=10 gets 11..N once each, in order, ending at end", str(f_seqs))
            status_line = [json.loads(x) for x in (await get("/api/board/events/follow", watched, run=rid)).text.splitlines()]
            ok &= claim([x["type"] for x in status_line] == ["status"] and status_line[0]["status"] == "ended",
                        "R8: following a finished run answers its status at once")

            # ---- R8: a reader that never reads is cut; the step goes on -----------------
            hold2 = asyncio.Event()
            Script.plan = [hold2, assistant("s1", *[sdk.ThinkingBlock(f"t{i}", "s") for i in range(events.SUB_LIMIT + 100)]),
                           assistant("s1", sdk.TextBlock(SPEC)), result(1)]
            step = asyncio.create_task(run(slow))
            while not service._recorders:
                await asyncio.sleep(0.01)
            recorder = next(iter(service._recorders.values()))
            q, _ = recorder.subscribe(0)
            hold2.set()
            done = await step
            items = [q.get_nowait() for _ in range(q.qsize())]
            ok &= claim(done.get("outcome") == "done" and items and items[-1][0] == "cut"
                        and q not in recorder.subscribers,
                        f"R8: an unread follower is cut at {events.SUB_LIMIT} and the step ends done")

            # ---- R5 -------------------------------------------------------------------
            def simple():
                return [assistant("r1", sdk.TextBlock("x")), "deny", assistant("r1", sdk.TextBlock(SPEC)), result(1)]

            Script.plan = simple()
            a = await run(same_a)
            Script.plan = simple()
            with mock.patch.object(service_mod.events, "Recorder", lambda *a, **k: None):
                b = await run(same_b)

            def records(unit):
                ignore = ("at", "run", "events_lost", "session_id", "unit")
                return [{k: v for k, v in r.items() if k not in ignore}
                        for r in journal.records(key, unit) if r["kind"] in ("start", "end")]

            def artifact(unit):
                return (store / ".cos" / unit / "spec.md").read_bytes()

            ok &= claim(a["outcome"] == b["outcome"] == "done" and artifact(same_a) == artifact(same_b)
                        and records(same_a) == records(same_b),
                        "R5: with and without a recorder: the same outcome, artifact, start and end")
            Script.plan = simple()
            with mock.patch.object(Data, "step_events_add", side_effect=Busy("held")):
                c = await run(busy)
            lost = journal.records(key, busy, kind="end")[-1].get("events_lost")
            ok &= claim(c["outcome"] == "done" and (lost or 0) > 0,
                        f"R5: cos.db busy through the last write: still done, events_lost {lost}")

            # ---- R9 -------------------------------------------------------------------
            guard = auth.Guard(app, data)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=guard), base_url="http://t") as bare:
                refused = [
                    (await bare.get(path, params={"cwd": cwd, "unit": watched, "run": rid})).status_code
                    for path in ("/api/board/events", "/api/board/events/follow")
                ]
            ok &= claim(all(s in (401, 303, 307, 403) for s in refused),
                        "R9: without a session both routes are refused", str(refused))

            # ---- R15 ------------------------------------------------------------------
            stages = [s["name"] for s in json.loads(subprocess.run(
                ["node", str(COS), "--root", str(store), "status", "--json"], capture_output=True, text=True,
                cwd=str(REPO)).stdout)["stages"]]
            names = sorted(p.name for p in (store / ".cos").iterdir() if p.is_dir())
            db = data.db_path
            before_all = (counts(db), gate_answers(store, names, stages))
            for _ in range(3):
                await get("/api/board/events", watched, run=rid)
                await get("/api/board/events", watched, run=rid, seq="2")
                await get("/api/board/events/follow", watched, run=rid)
            after_all = (counts(db), gate_answers(store, names, stages))
            ok &= claim(before_all == after_all,
                        "R15: reading, paging and following change no row of runs, transitions or outputs, "
                        "and no gate or next answer")
            with data.connect() as conn:
                purged = conn.execute("SELECT COUNT(*) FROM step_runs WHERE purged_at IS NOT NULL").fetchone()[0]
            ok &= claim(purged == 0, "R14: nothing was purged outside a purge call")

            # ---- R13, through the page's own module ------------------------------------
            from coscc import state as page_state

            lost_run = journal.records(key, busy, kind="end")[-1]["run"]
            unknown_note = page_state._watch_note(service.events_page(cwd, watched, "r-unknown"))
            lost_note = page_state._watch_note(service.events_page(cwd, busy, lost_run))
            data.step_events_purge(int(time.time() * 1000) + 10**9, 10**12, "2026-09-25T00:00:00+00:00")
            gone = service.events_page(cwd, watched, rid)
            notes = [page_state.NO_RUN_NOTE, page_state._watch_note(gone), unknown_note, lost_note]
            for n in notes:
                say(f"      {n}")
            ok &= claim(
                gone["status"] == "purged"
                and notes[0] == "no event stream: this step ran before events were recorded"
                and notes[1].startswith("events purged (")
                and notes[2].startswith("the app stopped while this step ran; no events after ")
                and notes[3] == f"{lost} events missing",
                "R13: the four notes, in the page's words",
            )
    finally:
        for p in patches:
            p.stop()

    # ---- R14, in a data root of its own -------------------------------------------
    pdata = Data(tmp / "purge")
    pjournal = Journal(tmp / "work", pdata)
    day = 24 * 3600 * 1000
    now = int(time.time() * 1000)
    for run, age in (("p-old", 31), ("p-mid", 2), ("p-new", 1)):
        pdata.step_run_open(run, "/w", key, "0001_a", "impl", now - age * day)
        pdata.step_events_add(run, [{"run": run, "seq": n, "at": now, "kind": "text", "text": "y" * 500}
                                    for n in range(1, 11)])
    size = pdata.step_run("p-new")["bytes"]
    runs, freed = await events.purge(pdata, pjournal, now=now, keep_bytes=size)
    rows = pjournal.records(kind="events-purge")
    ok &= claim(
        (runs, freed) == (2, 2 * size) and pdata.step_run("p-old")["purged_at"] and pdata.step_run("p-mid")["purged_at"]
        and not pdata.step_run("p-new")["purged_at"] and len(rows) == 1 and rows[0]["workspace"] == ""
        and pdata.step_events_page("p-old", None, 5) == ([], False),
        "R14: whole runs, by age then oldest-first by size; index rows kept; one events-purge row",
    )
    return ok


def PAGE_OK(events) -> bool:
    return events.PAGE_DEFAULT == 200 and events.PAGE_MAX == 500


# -- the browser --------------------------------------------------------------------


# 350 live events at 10 a second: B opens on 200 rows, so its list reaches `WATCH_WINDOW`
# (400) while following with about 150 to go, and F6 is measured on a running step.
BACKLOG, LIVE, RATE, CHARS = 500, 350, 10, 2100
WINDOW = 400


def browser_plan(go_file: Path) -> list:
    """500 events of 2 100 characters at once, then -- once the driver says go -- 350 more
    at 10 a second, then the artifact and the result."""
    sdk = messages()
    rng = __import__("random").Random(73)

    def text(i):
        return f"{i:05d} " + "".join(rng.choice("abcdefghij klmnop") for _ in range(CHARS - 6))

    async def wait_go():
        while not go_file.exists():
            await asyncio.sleep(0.1)
        return None

    def later(i):
        async def one():
            await asyncio.sleep(1 / RATE)
            return assistant("b1", sdk.ThinkingBlock(text(i), "s"))
        return one

    return [
        assistant("b1", *[sdk.ThinkingBlock(text(i), "s") for i in range(BACKLOG)]),
        wait_go,
        *[later(BACKLOG + i) for i in range(LIVE)],
        assistant("b1", sdk.TextBlock(SPEC)),
        result(1),
    ]


def serve(go_file: str) -> None:
    """The child process: `coscc.run` with the scripted client in place."""
    from coscc import run as run_mod
    from coscc import sessions as sessions_mod

    Script.plan = browser_plan(Path(go_file))
    sessions_mod.ClaudeSDKClient = Script
    run_mod.main([])


# A row counts the first time its seq is in the DOM, added or rewritten in place: a list at
# `WATCH_WINDOW` rows changes no child, only `data-seq`. While `__pause` is set (B is reading
# older rows and not following, so live rows are only counted), a first sighting is counted
# in `__skipped` rather than timed.
OBSERVE = """
window.__rows = {}; window.__lat = []; window.__base = null; window.__pause = false; window.__skipped = 0;
function __see(r, now) {
  const seq = +r.dataset.seq;
  if (window.__rows[seq]) return;
  window.__rows[seq] = true;
  if (window.__base === null || seq <= window.__base) return;
  if (window.__pause) window.__skipped++; else window.__lat.push([seq, now - +r.dataset.at]);
}
new MutationObserver(ms => {
  const now = Date.now();
  for (const m of ms) {
    if (m.type === 'attributes') {
      if (m.target.classList && m.target.classList.contains('watch-ev')) __see(m.target, now);
      continue;
    }
    for (const n of m.addedNodes) {
      if (n.nodeType !== 1) continue;
      const rows = n.classList && n.classList.contains('watch-ev') ? [n] : n.querySelectorAll('.watch-ev');
      for (const r of rows) __see(r, now);
    }
  }
}).observe(document, {subtree: true, childList: true, attributes: true, attributeFilter: ['data-seq']});
"""

# The first row in view (seq None) or the row carrying seq: [seq, its top minus the list's top].
WHERE = """seq => { const l = document.getElementById('watch-list');
    const r = seq === null ? [...l.querySelectorAll('.watch-ev')].find(
        x => x.getBoundingClientRect().bottom > l.getBoundingClientRect().top)
      : l.querySelector('.watch-ev[data-seq="' + seq + '"]');
    return r ? [+r.dataset.seq, r.getBoundingClientRect().top - l.getBoundingClientRect().top] : null; }"""


def browser() -> int:
    try:
        import argon2
        import httpx
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        say(f"missing: {e}")
        return EXIT_ENV
    from coscc import auth
    from coscc.data import Data

    if shutil.which("node") is None:
        say("node is not on PATH")
        return EXIT_ENV
    port = int(os.environ.get("COS_PORT") or 18773)
    base = f"http://127.0.0.1:{port}"
    tmp = Path(tempfile.mkdtemp(prefix="verify-0073-"))
    data_dir, work = tmp / "data", tmp / "work"
    repo = work / "proj"
    repo.mkdir(parents=True)
    go_file = tmp / "go"
    data = Data(data_dir)
    data.auth_set_password(argon2.PasswordHasher().hash(secrets.token_urlsafe(24)), int(time.time()))

    def session() -> str:
        now = int(time.time())
        token = secrets.token_urlsafe(32)
        data.auth_session_add(auth._sha(token), now, now + auth.SESSION_TTL)
        return token

    tok_a, tok_b = session(), session()
    env = {k: v for k, v in os.environ.items() if not (k.startswith("__REFLEX") and not v)}
    env.update(COS_HOST="127.0.0.1", COS_PORT=str(port), COS_WORKING_DIR=str(work),
               COS_DATA_DIR=str(data_dir), COS_WORKSPACES=str(repo))
    proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--serve", str(go_file)],
                            cwd=str(REPO), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    ok = True
    reader = None
    try:
        for _ in range(300):
            try:
                if httpx.get(base + "/api/health", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if proc.poll() is not None:
                say((proc.stdout.read() or b"").decode()[-2000:])
                return EXIT_ENV
            time.sleep(0.3)
        else:
            return EXIT_ENV
        hdr = {"Origin": base}
        a = httpx.Client(base_url=base, cookies={auth.COOKIE: tok_a}, headers=hdr, timeout=60)
        made = a.post("/api/units", json={"cwd": str(repo), "slug": "watched-from-b", "brief": "w"}).json()
        unit = made["unit"]
        (Path(made["path"]) / "intent.md").write_text(INTENT, encoding="utf-8")
        store = Path(made["path"]).parents[1]
        gate = subprocess.run(["node", str(COS), "--root", str(store), "gate", unit, "spec"],
                              capture_output=True, text=True, cwd=str(REPO))
        if gate.returncode != 0:
            say(f"the spec gate is closed: {gate.stdout}{gate.stderr}")
            return EXIT_ENV
        outcome: dict = {}

        def run_a():
            with a.stream("POST", "/api/board/run", json={"cwd": str(repo), "unit": unit, "stage": "spec"},
                          timeout=None) as r:
                for line in r.iter_lines():
                    if line.strip():
                        outcome.update(json.loads(line))

        reader = threading.Thread(target=run_a, daemon=True)
        reader.start()
        for _ in range(200):
            running = a.get("/api/board/running", params={"cwd": str(repo)}).json()
            rows = (running.get("running") or {}).get(unit) or []
            if rows and rows[0].get("run"):
                page = a.get("/api/board/events", params={"cwd": str(repo), "unit": unit, "run": rows[0]["run"]}).json()
                if page["events"] and page["events"][-1]["seq"] >= BACKLOG:
                    break
            time.sleep(0.2)
        frames: list[tuple[float, int]] = []
        ws_name = repo.name
        with sync_playwright() as p:
            chrome = p.chromium.launch(channel="chrome", headless=True)

            def context(token):
                ctx = chrome.new_context(viewport={"width": 1280, "height": 900})
                ctx.add_cookies([{"name": auth.COOKIE, "value": token, "url": base}])
                return ctx

            ctx_a, ctx_b = context(tok_a), context(tok_b)
            b = ctx_b.new_page()
            b.add_init_script(OBSERVE)
            b.on("websocket", lambda ws: ws.on("framereceived", lambda m: frames.append(
                (time.time(), len(m.encode()) if isinstance(m, str) else len(m)))
                if isinstance(m, str) and "watch_events" in m else None))
            b.goto(f"{base}/board?ws={ws_name}", wait_until="domcontentloaded", timeout=60_000)
            b.wait_for_selector("[data-testid=card-watch]", timeout=60_000)
            b.click("[data-testid=card-watch]")
            b.wait_for_function("document.querySelectorAll('#watch-list .watch-ev').length >= 200", timeout=60_000)
            b.wait_for_timeout(500)
            first = b.eval_on_selector_all("#watch-list .watch-ev", "rs => rs.map(r => +r.dataset.seq)")
            ok &= claim(len(first) == 200, "R10: B opens on the last page of 200", f"{len(first)} rows")
            b.evaluate(f"window.__base = {max(first)}")
            go_file.write_text("go")
            end_seq = None

            def rows_now() -> list[int]:
                return b.eval_on_selector_all("#watch-list .watch-ev", "rs => rs.map(r => +r.dataset.seq)")

            # `review.md` F6, on the running step. (a) B's list is full and following, so each
            # live batch drops its oldest rows; B scrolls a quarter of the way down and reads.
            b.wait_for_function(f"document.querySelectorAll('#watch-list .watch-ev').length >= {WINDOW}",
                                timeout=90_000)
            b.eval_on_selector("#watch-list", "l => { l.scrollTop = l.scrollHeight / 4; }")
            b.wait_for_timeout(300)
            before = b.evaluate(f"({WHERE})(null)")
            top_before = rows_now()[0]
            b.wait_for_timeout(2500)
            after = b.evaluate(WHERE, before[0])
            top_after = rows_now()[0]
            ok &= claim(top_after > top_before and after is not None and abs(after[1] - before[1]) <= 2,
                        "F6 (a): reading a full list that follows, the row read stays put as live rows drop",
                        f"first row {top_before} -> {top_after}; seq {before[0]} at {before[1]:.0f}px -> "
                        f"{'gone' if after is None else f'{after[1]:.0f}px'}")
            # (b) *older* pressed by hand while live batches still arrive, then *Jump to latest*.
            b.evaluate("window.__pause = true")
            before = b.evaluate(f"({WHERE})(null)")
            top_before = rows_now()[0]
            b.click("#watch-older")
            b.wait_for_function(f"+document.querySelector('#watch-list .watch-ev').dataset.seq < {top_before}",
                                timeout=30_000)
            b.wait_for_timeout(1200)
            after = b.evaluate(WHERE, before[0])
            ok &= claim(after is not None and abs(after[1] - before[1]) <= 2,
                        "F6 (b): *older* pressed while live rows arrive keeps the row read where it was",
                        f"seq {before[0]} at {before[1]:.0f}px -> {'gone' if after is None else f'{after[1]:.0f}px'}")
            live_ok = outcome.get("type") not in ("done", "error")
            ok &= claim(live_ok, "F6: both were measured while the step was still running", str(outcome)[:200])
            newest = max(rows_now())
            b.click("#watch-live")
            b.wait_for_function(f"Math.max(...[...document.querySelectorAll('#watch-list .watch-ev')]"
                                f".map(r => +r.dataset.seq)) > {newest} + 50", timeout=30_000)
            b.wait_for_timeout(300)
            at_end = b.eval_on_selector("#watch-list", "l => l.scrollHeight - l.scrollTop - l.clientHeight")
            ok &= claim(at_end < 40, "*Jump to latest* brings B back to the bottom", f"{at_end:.0f}px from it")
            b.evaluate("window.__pause = false")
            for _ in range(600):
                if outcome.get("type") in ("done", "error"):
                    break
                time.sleep(0.2)
            b.wait_for_function("[...document.querySelectorAll('.watch-ev')].some(r => r.textContent.includes('ended:'))",
                                timeout=60_000)
            b.wait_for_timeout(800)
            lat = sorted(d for _, d in b.evaluate("window.__lat"))
            skipped = b.evaluate("window.__skipped")
            ok &= claim(outcome.get("outcome") == "done", "the step A started ends done", str(outcome)[:300])
            if lat:
                say(f"      latency at -> DOM in B, ms: n {len(lat)} min {lat[0]} p50 {lat[len(lat) // 2]} "
                    f"p99 {lat[int(len(lat) * .99)]} max {lat[-1]}; {skipped} not timed (read while "
                    f"B was not following)")
            ok &= claim(len(lat) + skipped >= LIVE and lat[-1] <= 2000,
                        "R11: every live event B followed reached its DOM within 2000 ms of its at",
                        f"{len(lat)} timed, {skipped} not")
            biggest = max((n for _, n in frames), default=0)
            say(f"      largest frame carrying watch_events: {biggest} bytes (spike U4 passed at 1649608)")
            ok &= claim(biggest <= 1_649_608, "R11: no frame carrying watch_events above 1 649 608 bytes")
            count_before = b.eval_on_selector_all("#watch-list .watch-ev", "rs => rs.length")
            first_seq = b.eval_on_selector("#watch-list .watch-ev", "r => +r.dataset.seq")
            b.click("#watch-older")
            b.wait_for_function(f"+document.querySelector('#watch-list .watch-ev').dataset.seq < {first_seq}",
                                timeout=30_000)
            ok &= claim(True, f"R10: #watch-older loads the page before ({count_before} rows before)")
            # R12: collapsed, opened whole, collapsed again.
            row = b.locator("#watch-list .watch-ev", has=b.locator(".watch-expand")).last
            short = len(row.locator(".watch-body").inner_text())
            row.locator(".watch-expand").click()
            b.wait_for_selector(".watch-open", timeout=30_000)
            whole = len(b.locator(".watch-open").inner_text())
            b.locator(".watch-collapse").click()
            b.wait_for_selector(".watch-open", state="detached", timeout=30_000)
            again = len(row.locator(".watch-body").inner_text())
            ok &= claim(short <= 2000 < whole and again == short,
                        "R12: collapsed, Expand shows the stored length, Collapse goes back", f"{short} {whole} {again}")
            # After `end`: reload, the unit's timeline, the step's row, and back to seq 1.
            b.goto(f"{base}/unit?ws={ws_name}&id={unit}&tab=timeline", wait_until="domcontentloaded", timeout=60_000)
            b.wait_for_selector(".watch-run", timeout=60_000)
            b.locator(".watch-run").last.click()
            b.wait_for_function("document.querySelectorAll('#watch-list .watch-ev').length > 0", timeout=60_000)
            status = b.locator("#watch-pane").inner_text()
            # Rows are drawn by position, so a prepended page mostly rewrites rows in place:
            # the seqs are read off the list after each load, not from added nodes.
            shown: set[int] = set()

            # `review.md` F5: loading older rows keeps the row being read where it was --
            # scrolled to the top (the observer presses *older*), once while the list grows
            # and once when it is full and drops its newest rows, then pressed by hand from a
            # quarter of the way down.
            where = WHERE
            place: list[str] = []
            for how in ("top", "top", "hand"):
                current = rows_now()
                shown.update(current)
                if b.is_disabled("#watch-older"):
                    place.append(f"{how}: nothing older")
                    break
                n = len(current)
                if how == "top":
                    before = b.evaluate(f"() => {{ document.getElementById('watch-list').scrollTop = 0; "
                                        f"return ({where})(null); }}")
                else:
                    b.eval_on_selector("#watch-list", "l => { l.scrollTop = l.scrollHeight / 4; }")
                    b.wait_for_timeout(200)
                    before = b.evaluate(f"({where})(null)")
                    b.click("#watch-older")
                b.wait_for_function(f"+document.querySelector('#watch-list .watch-ev').dataset.seq < {current[0]}",
                                    timeout=30_000)
                b.wait_for_timeout(400)
                after = b.evaluate(where, before[0])
                place.append(f"{how} {n}->{len(rows_now())} rows: seq {before[0]} at {before[1]:.0f}px -> "
                             f"{'gone' if after is None else f'{after[1]:.0f}px'}")
                ok &= claim(after is not None and abs(after[1] - before[1]) <= 2,
                            f"F5: {how}, {n} rows: the row read before *older* is where it was", place[-1])
            say("      " + "; ".join(place))

            for _ in range(20):
                current = rows_now()
                shown.update(current)
                if 1 in shown or b.is_disabled("#watch-older"):
                    break
                b.click("#watch-older")
                b.wait_for_function(f"+document.querySelector('#watch-list .watch-ev').dataset.seq < {current[0]}",
                                    timeout=30_000)
            shown.update(rows_now())
            seen = sorted(shown)
            end_seq = max(seen) if seen else 0
            ok &= claim("ended" in status and "ended:" in status and seen == list(range(1, end_seq + 1)),
                        f"after end: B reloads, opens the timeline row and reads 1..{end_seq} with nothing missing",
                        f"ended in pane: {'ended' in status}, end row: {'ended:' in status}, seen {len(seen)}, "
                        f"missing {sorted(set(range(1, end_seq + 1)) - set(seen))[:10]}")
            ca = {(c["name"], c["value"]) for c in ctx_a.cookies()}
            cb = {(c["name"], c["value"]) for c in ctx_b.cookies()}
            shared = bool(ca & cb)
            say(f"A and B share any cookie: {shared}")
            ok &= claim(not shared, "A and B are two sessions")
            chrome.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)
    return EXIT_PASS if ok else EXIT_BROKEN


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--serve")
    args = parser.parse_args()
    if args.serve:
        serve(args.serve)
        return EXIT_PASS
    if shutil.which("node") is None:
        say("node is not on PATH")
        return EXIT_ENV
    if args.browser:
        return browser()
    with tempfile.TemporaryDirectory(prefix="verify-0073-") as d:
        ok = asyncio.run(plain(Path(d)))
    return EXIT_PASS if ok else EXIT_BROKEN


if __name__ == "__main__":
    sys.exit(main())
