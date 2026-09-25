#!/usr/bin/env python3
"""Proof for `0053_the-page-sends-every-card-several-times-on-each-change` (`plan.md` step 1).

What the page receives over its socket, counted in chromium the way `idea.md` measured it:
CDP `Network.webSocketFrameReceived`, payload bytes summed, from the start of a navigation
until the page is shown. *Shown* is `spec.md ## Design`, *Phép đo*: every card the Board
lists is a `[data-testid=work-card]` in the DOM, then 2 s pass with no frame (chosen, C3).
The time of R4 stops when the cards are all there, not after the quiet.

    (a) R1   fixture 42: loading `/board?ws=coscc` receives ≤ 256000 B
    (b) R4   fixture 42: the median of three loads has every card in the DOM < 1000 ms
    (c) R8   across the load's deltas exactly one var carries a list of cards (items with
             both `id` and `lane`), and no delta carries one card twice
    (d) R9, R11  no delta of the load carries a non-empty `messages`, `conversations` or
             `usage_rows`. The fixture has a conversation holding a 72.9 KiB message and
             units with a cost, so each of the three would be sent if it were read
    (e) R2   from `other`'s Board, choosing `coscc` in the workspace select receives
             ≤ 256000 B until `coscc`'s Board is shown
    (f) R3   in-process: a step ends while its unit's dialog is open; every delta from
             the one carrying its `done` on, re-serialized, is ≤ 256000 B together
    (g)      no card lost: the DOM holds as many cards as `/api/board` lists, and each
             lane as many as `coscc.state._lane` puts there
    (h) R5   fixture 200: (a) and (b) again, printed with bytes per card; no threshold
    (i) C4   in-process: the delta of one `_apply_running` that changes a card's `live`,
             and of one that changes nothing; printed, no threshold

    0  every claim PASS
    1  a claim FAIL
    2  the environment is not ready: no build for this address, the port in use, no
       browser, no `git`, or `--url` without `COS_PROOF_PASSWORD`

Plain: the checkout's bundle on `COS_HOST`/`COS_PORT` (build it for `127.0.0.1:18753`), a
temporary working folder with two workspaces, `coscc` and `other` (empty), and a seeded
session past the `0070` login, as `verify_0056` does. `coscc`'s store is filled with this
checkout's own `.cos/NNNN_*` in number order, repeated under `1000+` numbers past the
fourteen there are — the way `spike.md ## Probe code` grew 70 units to 200. `gh` is a fake
first on `PATH` that refuses everything, and `CLAUDE_CONFIG_DIR` points into the temporary
folder, so no request leaves the machine and `~/.claude` is not read. (f) and (i) drive
`StudioState` through Reflex's own event processor, as `verify_0051` does, with the session
replaced as in `verify_0034`: a delta there is re-serialized, not a frame (`spike.md ## U1`
measured the two 358 B apart on 1487670 B).

`--url <base> --ws <name> --from <name>` measures a running app instead — the installed one,
at a terminal — logging in with `COS_PROOF_PASSWORD`. It runs (a) to (e) on the workspace
`--ws`, and prints how many cards it had (`spec.md ## Answers, câu 2`). It writes nothing
and runs no step, so (f) — a real step, real money — is not here. The 1000 ms of (b) is
applied only on a loopback host; across a LAN the time is printed and not judged (R4).
Neither mode opens a session or spends quota.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argon2  # noqa: E402
import httpx  # noqa: E402

from coscc import auth  # noqa: E402
from coscc.config import from_env  # noqa: E402
from coscc.data import Data  # noqa: E402
from scripts.proof_harness import (  # noqa: E402
    EXIT_BROKEN,
    EXIT_ENV,
    EXIT_PASS,
    REPO,
    RealApp,
    require_browser,
    require_build,
    require_free_port,
    say,
)

LIMIT_B = 256000          # 250 KiB, `intent.md ## Proposed outcome`
LIMIT_MS = 1000.0         # R4, `intent.md ## Answers, câu 2`
QUIET_S = 2.0             # *shown*: this long with no frame (chosen, `spec.md` C3)
RUNS = 3                  # loads per fixture; the median is chosen, not measured
LONG_MESSAGE = 74650      # the 72.9 KiB prompt `idea.md` found in `messages`
PAGE_TIMEOUT_MS = 120_000
SIZE = {"width": 1440, "height": 900}
LANES = ("Planned", "In progress", "Needs you", "Complete")
UNIT = re.compile(r"^(\d{4})_(.+)$")
MARK = "_rx_state_"

FAKE_GH = """#!/bin/sh
echo "the fake gh for verify_0053 knows nothing: $*" >&2
exit 1
"""

CARDS_IN_DOM = """
(ids) => {
  const want = new Set(ids);
  const seen = new Set();
  for (const e of document.querySelectorAll('[data-testid=work-card]')) {
    const id = e.id.slice(5);
    if (want.has(id)) seen.add(id);
  }
  return seen.size >= want.size;
}
"""

LANE_COUNTS = """
(names) => Object.fromEntries(names.map(n => [n,
  document.querySelectorAll('[data-testid="lane-' + n + '"] [data-testid=work-card]').length]))
"""


# --------------------------------------------------------------------------
# what a frame carries
# --------------------------------------------------------------------------


def dumps(value) -> str:
    from reflex_base.utils import format

    return format.json_dumps(value, separators=(",", ":"))


def size(value) -> int:
    return len(dumps(value).encode())


def deltas(payloads: list[str]):
    """The `delta` of every state update in a list of Socket.IO frames."""
    for p in payloads:
        i = p.find("[")
        if i < 0:
            continue
        try:
            msg = json.loads(p[i:])
        except ValueError:
            continue
        for part in msg[1:] if isinstance(msg, list) else []:
            if isinstance(part, str):
                try:
                    part = json.loads(part)
                except ValueError:
                    continue
            if isinstance(part, dict) and part.get("delta"):
                yield part["delta"]


def flat(delta: dict):
    """(var name, value) for every var of every state in one delta."""
    for vars_ in delta.values():
        for k, v in vars_.items():
            yield k.removesuffix(MARK), v


def card_list(value) -> bool:
    return (isinstance(value, list) and bool(value)
            and all(isinstance(x, dict) and "id" in x and "lane" in x for x in value))


def r8(all_deltas: list[dict]) -> tuple[bool, str]:
    """One var carries cards, and within a delta no card id comes twice."""
    names: set[str] = set()
    twice: list[str] = []
    for d in all_deltas:
        count: dict[str, int] = {}
        for name, value in flat(d):
            if card_list(value):
                names.add(name)
                for x in value:
                    count[x["id"]] = count.get(x["id"], 0) + 1
        twice += [f"{k}×{n}" for k, n in count.items() if n > 1]
    ok = len(names) == 1 and not twice
    return ok, f"vars carrying cards: {sorted(names)}; ids sent more than once in a delta: {len(twice)} ({twice[:3]})"


def r9_r11(all_deltas: list[dict]) -> tuple[bool, str]:
    sent = sorted({name for d in all_deltas for name, v in flat(d)
                   if name in ("messages", "conversations", "usage_rows") and v})
    return not sent, f"sent non-empty: {sent}"


def per_var(all_deltas: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for d in all_deltas:
        for name, value in flat(d):
            out[name] = out.get(name, 0) + size(value)
    return out


# --------------------------------------------------------------------------
# the page
# --------------------------------------------------------------------------


def context(browser, base: str, cookie: str | None, password: str | None):
    ctx = browser.new_context(viewport=SIZE)
    ctx.set_default_timeout(PAGE_TIMEOUT_MS)
    if cookie:
        ctx.add_cookies([{"name": auth.COOKIE, "value": cookie, "url": base}])
    else:
        # A tab of its own, closed before the measured one opens: its socket is not counted.
        login = ctx.new_page()
        login.goto(base + auth.LOGIN)
        login.fill("#password", password or "")
        login.press("#password", "Enter")
        login.wait_for_selector("#studio-shell", timeout=PAGE_TIMEOUT_MS)
        login.close()
    return ctx


def watch(ctx, page) -> list[tuple[float, str]]:
    frames: list[tuple[float, str]] = []
    cdp = ctx.new_cdp_session(page)
    cdp.send("Network.enable")
    cdp.on("Network.webSocketFrameReceived",
           lambda e: frames.append((time.monotonic(), e["response"]["payloadData"])))
    return frames


def quiet(frames: list, since: float) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        last = frames[-1][0] if frames else since
        if time.monotonic() - max(last, since) >= QUIET_S:
            return
        time.sleep(0.1)


def wait_board(page, ids: list[str]) -> None:
    if ids:
        page.wait_for_function(CARDS_IN_DOM, arg=ids, polling="raf", timeout=PAGE_TIMEOUT_MS)
    else:
        page.wait_for_selector("[data-testid=empty-board]", timeout=PAGE_TIMEOUT_MS)


def load(browser, base, cookie, password, ws: str, ids: list[str]) -> dict:
    """One fresh tab at `/board?ws=<ws>`, as `idea.md` measured it."""
    ctx = context(browser, base, cookie, password)
    try:
        page = ctx.new_page()
        frames = watch(ctx, page)
        page.goto(f"{base}/board?ws={quote(ws)}", wait_until="commit")
        wait_board(page, ids)
        shown = page.evaluate("performance.now()")
        quiet(frames, time.monotonic())
        payloads = [p for _, p in frames]
        return {
            "ms": shown, "payloads": payloads, "bytes": sum(len(p.encode()) for p in payloads),
            "dom": page.evaluate("document.querySelectorAll('[data-testid=work-card]').length"),
            "lanes": page.evaluate(LANE_COUNTS, list(LANES)),
        }
    finally:
        ctx.close()


def switch(browser, base, cookie, password, ws: str, ids: list[str],
           other: str, other_ids: list[str]) -> dict:
    """R2: `other`'s Board shown, then `ws` chosen in the workspace select."""
    ctx = context(browser, base, cookie, password)
    try:
        page = ctx.new_page()
        frames = watch(ctx, page)
        page.goto(f"{base}/board?ws={quote(other)}", wait_until="commit")
        wait_board(page, other_ids)
        quiet(frames, time.monotonic())
        mark, t0 = len(frames), time.monotonic()
        page.select_option("#workspace-switcher", label=ws)
        page.wait_for_function(f"() => location.search.includes('ws={quote(ws)}')",
                               timeout=PAGE_TIMEOUT_MS)
        wait_board(page, ids)
        quiet(frames, t0)
        payloads = [p for _, p in frames[mark:]]
        return {"payloads": payloads, "bytes": sum(len(p.encode()) for p in payloads)}
    finally:
        ctx.close()


def board_ids(api: httpx.Client, cwd: str) -> tuple[list[str], list[dict]]:
    rows = api.get("/api/board", params={"cwd": cwd}).json().get("units") or []
    return [u["name"] for u in rows], rows


def browser_claims(browser, base, cookie, password, ws, ids, other, other_ids,
                   rows: list[dict] | None, judge_time: bool) -> tuple[list[bool], list[dict]]:
    ok: list[bool] = []
    runs = [load(browser, base, cookie, password, ws, ids) for _ in range(RUNS)]
    for i, r in enumerate(runs, 1):
        print(f"      load {i}: {r['bytes']} B in {len(r['payloads'])} frames; every card at "
              f"{r['ms']:.0f} ms; {r['dom']} cards in the DOM")
    worst = max(r["bytes"] for r in runs)
    ok.append(say(worst <= LIMIT_B, f"(a) R1 loading the Board of {len(ids)} cards receives ≤ {LIMIT_B} B",
                  f"{worst} B"))
    median = statistics.median(r["ms"] for r in runs)
    if judge_time:
        ok.append(say(median < LIMIT_MS, f"(b) R4 every card in the DOM, median of {RUNS}, < {LIMIT_MS:.0f} ms",
                      f"{median:.0f} ms"))
    else:
        print(f"INFO  (b) R4 not a loopback host: median {median:.0f} ms, recorded, not judged")
    loaded = [d for r in runs for d in deltas(r["payloads"])]
    held, why = r8(loaded)
    ok.append(say(held, "(c) R8 one var carries the cards, and no delta carries a card twice", why))
    held, why = r9_r11(loaded)
    ok.append(say(held, "(d) R9, R11 the load sends no conversation, no message, no Usage row", why))
    moved = switch(browser, base, cookie, password, ws, ids, other, other_ids)
    ok.append(say(moved["bytes"] <= LIMIT_B,
                  f"(e) R2 choosing {ws} from {other}'s Board receives ≤ {LIMIT_B} B", f"{moved['bytes']} B"))
    print(f"      switch: {moved['bytes']} B in {len(moved['payloads'])} frames")
    if rows is not None:
        from coscc.state import _lane

        want = {n: 0 for n in LANES}
        for u in rows:
            if ((u.get("hold") or {}).get("state") or "") != "dropped":
                want[_lane(u)] += 1
        got = runs[0]["lanes"]
        ok.append(say(runs[0]["dom"] == len(rows) and got == want,
                      "(g) every card listed is in the DOM, and each lane holds what `_lane` puts there",
                      f"dom {runs[0]['dom']} of {len(rows)}; lanes {got} want {want}"))
    heavy = sorted(per_var(list(deltas(runs[0]["payloads"]))).items(), key=lambda kv: -kv[1])
    print("      vars over 1 KiB in load 1: "
          + ", ".join(f"{k} {v}" for k, v in heavy if v > 1024))
    return ok, runs


# --------------------------------------------------------------------------
# the fixture
# --------------------------------------------------------------------------


def fill(store: Path, start: int, stop: int) -> list[str]:
    """Units `start`..`stop-1` of the fixture: this checkout's `.cos/NNNN_*` in order, then
    the same again under `1000+i` (`spike.md ## Probe code`, `probe.py`)."""
    real = sorted(p for p in (REPO / ".cos").iterdir() if p.is_dir() and UNIT.match(p.name))
    store.mkdir(parents=True, exist_ok=True)
    names = []
    for i in range(start, stop):
        src = real[i % len(real)]
        name = src.name if i < len(real) else f"{1000 + i:04d}_{UNIT.match(src.name)[2]}"
        shutil.copytree(src, store / name)
        names.append(name)
    return names


def seed_session(data_dir: Path) -> str:
    """`0070`: a password nobody types and one live session, as `verify_0056` seeds them."""
    data = Data(data_dir)
    now = int(time.time())
    data.auth_set_password(argon2.PasswordHasher().hash(secrets.token_urlsafe(24)), now)
    token = secrets.token_urlsafe(32)
    data.auth_session_add(auth._sha(token), now, now + auth.SESSION_TTL)
    return token


def seed_conversation(workspace: Path) -> None:
    """One conversation in the SDK's store under `CLAUDE_CONFIG_DIR`, holding a message as
    long as the step prompt `idea.md` found in `messages`."""
    from claude_agent_sdk._internal import sessions as sdk

    where = sdk._get_projects_dir() / sdk._sanitize_path(sdk._canonicalize_path(str(workspace)))
    where.mkdir(parents=True, exist_ok=True)
    sid, parent, lines = str(uuid.uuid4()), None, []
    for i, (kind, content) in enumerate([
        ("user", "verify_0053: một cuộc hội thoại để đếm"),
        ("assistant", [{"type": "text", "text": "Đây là câu trả lời ngắn."}]),
        ("user", "prompt của một step " + "x" * (LONG_MESSAGE - 20)),
        ("assistant", [{"type": "text", "text": "Đã đọc."}]),
    ]):
        me = str(uuid.uuid4())
        lines.append(json.dumps({
            "type": kind, "uuid": me, "parentUuid": parent, "sessionId": sid,
            "cwd": str(workspace), "timestamp": f"2026-09-24T01:00:0{i}.000Z",
            "isSidechain": False, "userType": "external",
            "message": {"role": kind, "content": content},
        }))
        parent = me
    (where / f"{sid}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def seed_cost(working: Path, data_dir: Path, workspace: Path, names: list[str]) -> None:
    """A finished, costed `plan` run for each of `names`, so Usage has rows to send."""
    from coscc.journal import Journal

    journal = Journal(working, data_dir)
    key = str(workspace.resolve())
    for i, name in enumerate(names, 1):
        journal.started(key, name, "plan", "manual")
        # `journal._fold` reads a run's cost off the `end` record itself.
        journal.finished(key, name, "plan", "done",
                         input_tokens=1000 * i, output_tokens=300 * i, cost_usd=0.05 * i)


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=proof", "-c", "user.email=proof@example.invalid", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout.strip()


# --------------------------------------------------------------------------
# (f) and (i): in-process
# --------------------------------------------------------------------------


class _Client:
    async def disconnect(self) -> None:
        pass


class Standin:
    """A session that holds its step until released, then replies with an accepted plan."""

    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
        step = kw.get("step")
        if step is not None:
            step.client = _Client()
        try:
            yield ("chunk", "# Plan: a proof\n")
            await asyncio.wait_for(self.release.wait(), 60)
            yield ("chunk", "Intent: intent.md. Spec: spec.md. Author: verify_0053. Status: accepted.\n\n## Body\n")
            yield ("done", {"session_id": "verify-0053", "terminal_reason": "success",
                            "cost": {"output_tokens": 3, "turns": 1, "cost_usd": 0.01}})
        finally:
            if step is not None:
                await step.close()


class Recorder:
    """Stands in for the socket: every `StateUpdate` the processor would emit, kept."""

    def __init__(self) -> None:
        self.deltas: list[dict] = []
        self.at: list[float] = []

    async def emit_update(self, update, token) -> None:
        if update.delta:
            self.deltas.append(update.delta)
            self.at.append(time.monotonic())


TOKEN = "verify-0053-tab"


async def in_process(workspace: Path, cards: int) -> list[bool]:
    from reflex.event import Event
    from reflex.istate.manager.memory import StateManagerMemory
    from reflex.istate.manager.token import BaseStateToken
    from reflex.state import State
    from reflex_base.constants import RouteVar
    from reflex_base.event.processor import BaseStateEventProcessor
    from reflex_base.utils.format import format_event_handler

    from coscc import units
    from coscc.state import RUNNING_POLL, SERVICE, StudioState

    ok: list[bool] = []
    cwd = str(workspace)
    standin = Standin()
    SERVICE.sessions = standin
    # The fixture first, so the unit made after it takes the next number rather than one of
    # theirs.
    fill(units.root(workspace, os.environ["COS_DATA_DIR"]) / ".cos", 0, cards - 1)
    made = await SERVICE.create_unit(cwd, "proof-live", "verify_0053 fixture")
    live = made["unit"]
    here = Path(made["path"])
    (here / "intent.md").write_text(
        "# Intent: proof live\nAuthor: verify_0053. Type: perf. Status: accepted.\n", encoding="utf-8")
    (here / "spec.md").write_text(
        "# Spec: proof live\nAuthor: verify_0053. Status: accepted.\n", encoding="utf-8")
    await SERVICE._worktree(cwd, live)

    recorder = Recorder()
    manager = StateManagerMemory()
    processor = BaseStateEventProcessor().configure(state_manager=manager, event_namespace=recorder)
    key = BaseStateToken(ident=TOKEN, cls=State)

    async def arrive(href: str, sid: str = "verify", settle: float = 1.0) -> None:
        async with manager.modify_state(key) as root:
            if not root.router_data:
                root.router_data = {RouteVar.CLIENT_TOKEN: TOKEN}
        router_data = {RouteVar.PATH: href.partition("?")[0], RouteVar.ORIGIN: href,
                       RouteVar.SESSION_ID: sid, RouteVar.CLIENT_TOKEN: TOKEN,
                       RouteVar.HEADERS: {"origin": "http://verify"}}
        name = format_event_handler(StudioState.event_handlers["arrive"])
        await processor.enqueue(TOKEN, Event(name=name, payload={}, router_data=router_data))
        await asyncio.sleep(settle)

    async def read(*names: str) -> tuple:
        async with manager.modify_state(key) as root:
            s = await root.get_state(StudioState)
            return tuple(getattr(s, n) for n in names)

    async def until(test, limit: float) -> bool:
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            if await test():
                return True
            await asyncio.sleep(0.2)
        return False

    async with processor:
        await arrive(f"/unit?ws={workspace.name}&id={live}", settle=2.0)
        asked = await until(lambda: _is(read("run_stage", "unit_id"), ("plan", live)), 60)
        stage, unit, said = await read("run_stage", "unit_id", "run_said")
        if not asked:
            ok.append(say(False, "(f) the dialog of the unit offers `plan`", f"{stage!r} {unit!r} {said!r}"))
            await arrive(f"/?ws={workspace.name}", settle=RUNNING_POLL + 1)
            return ok

        mark = len(recorder.deltas)
        name = format_event_handler(StudioState.event_handlers["run_step"])
        await processor.enqueue(TOKEN, Event(name=name, payload={}))
        await asyncio.sleep(1.5)
        standin.release.set()

        async def settled() -> bool:
            said, notice = await read("run_said", "notice")
            last = recorder.at[-1] if recorder.at else 0
            return (notice.startswith("plan ") and said and not said.startswith("Asking")
                    and time.monotonic() - last >= QUIET_S)

        await until(settled, 90)
        done_at = next((i for i in range(mark, len(recorder.deltas))
                        if any(n == "notice" and str(v).startswith("plan ")
                               for n, v in flat(recorder.deltas[i]))), None)
        notice, = await read("notice")
        if done_at is None:
            ok.append(say(False, "(f) R3 a step ended with its dialog open", f"notice {notice!r}"))
        else:
            after = recorder.deltas[done_at:]
            total = sum(size(d) for d in after)
            ok.append(say(total <= LIMIT_B,
                          f"(f) R3 from the delta carrying the step's `done` on, the page receives ≤ {LIMIT_B} B",
                          f"{total} B"))
            print(f"      step end: {total} B in {len(after)} deltas ({notice!r}); "
                  "vars over 1 KiB: " + ", ".join(
                      f"{k} {v}" for k, v in sorted(per_var(after).items(), key=lambda kv: -kv[1]) if v > 1024))

        # (i) C4: one ask of `Service.running` that changes a card's `live`, then one that does not.
        row = {"kind": "step", "stage": "impl", "agent": {"glyph": "ᚢ", "name": "Uruz"},
               "started": "2026-09-25T01:00:00+00:00", "turns": None, "cost_usd": None}
        for label, answer in (("changes one card's live", {"running": {live: [row]}, "unknown_end": {}}),
                              ("changes nothing", {"running": {live: [row]}, "unknown_end": {}}),
                              ("clears it again", {"running": {}, "unknown_end": {}})):
            async with manager.modify_state(key) as root:
                s = await root.get_state(StudioState)
                await root._get_resolved_delta()
                root._clean()
                s._apply_running(answer)
                delta = await root._get_resolved_delta()
                root._clean()
            print(f"INFO  (i) C4 one `_apply_running` that {label}: {size(delta)} B "
                  f"({', '.join(sorted({n for n, _ in flat(delta)})) or 'nothing'})")

        await arrive(f"/?ws={workspace.name}", settle=RUNNING_POLL + 1)
    return ok


async def _is(pending, want: tuple) -> bool:
    return (await pending) == want


# --------------------------------------------------------------------------
# the two modes
# --------------------------------------------------------------------------


def plain() -> int:
    for tool in ("git", "node"):
        if shutil.which(tool) is None:
            print(f"no {tool} on PATH", file=sys.stderr)
            return EXIT_ENV
    # As `verify_0071`: a blank `__REFLEX_*` a step inherits would leave `/` a 404.
    for name in [k for k, v in os.environ.items() if k.startswith("__REFLEX") and not v]:
        del os.environ[name]
    config = from_env()
    require_build(config)
    require_free_port(config)
    top = Path(tempfile.mkdtemp(prefix="cos-0053-")).resolve()
    try:
        fakebin = top / "fakebin"
        fakebin.mkdir()
        (fakebin / "gh").write_text(FAKE_GH, encoding="utf-8")
        (fakebin / "gh").chmod(0o755)
        # (f)'s workspace, a clone of a bare-directory remote so its unit can have a tree.
        inproc = top / "inproc"
        remote, proj = inproc / "remote.git", inproc / "work" / "proj"
        proj.mkdir(parents=True)
        git(inproc, "init", "-q", "--bare", "-b", "main", str(remote))
        git(proj, "init", "-q", "-b", "main")
        git(proj, "commit", "-q", "--allow-empty", "-m", "the only commit")
        git(proj, "remote", "add", "origin", str(remote))
        git(proj, "push", "-q", "-u", "origin", "main")
        # Before anything imports `coscc.state`, which builds its `SERVICE` from these.
        os.environ["PATH"] = f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}"
        os.environ["CLAUDE_CONFIG_DIR"] = str(top / "claude")
        os.environ["COS_DATA_DIR"] = str(inproc / "data")
        os.environ["COS_WORKING_DIR"] = str(inproc / "work")
        os.environ["COS_WORKSPACES"] = str(proj)
        for name, value in (("GIT_AUTHOR_NAME", "proof"), ("GIT_COMMITTER_NAME", "proof"),
                            ("GIT_AUTHOR_EMAIL", "proof@example.invalid"),
                            ("GIT_COMMITTER_EMAIL", "proof@example.invalid")):
            os.environ[name] = value

        from coscc import units

        root, data_dir = top / "work", top / "data"
        (root / "coscc").mkdir(parents=True)
        (root / "other").mkdir()
        data_dir.mkdir()
        token = seed_session(data_dir)
        seed_conversation(root / "coscc")
        store = units.root(root / "coscc", data_dir) / ".cos"
        names = fill(store, 0, 42)
        seed_cost(root, data_dir, root / "coscc", names[:5])

        ok: list[bool] = []
        playwright, browser = require_browser()
        try:
            with RealApp(config, root, data_dir) as app:
                api = httpx.Client(base_url=app.base, timeout=300, cookies={auth.COOKIE: token})
                for ws in ("coscc", "other"):
                    added = api.post("/api/workspaces", json={"name": ws})
                    if added.status_code != 200:
                        print(f"could not adopt {ws}: {added.text}", file=sys.stderr)
                        return EXIT_ENV
                ids, rows = board_ids(api, str(root / "coscc"))
                print(f"fixture 42: {len(ids)} units on /api/board")
                claims, runs42 = browser_claims(browser, app.base, token, None, "coscc", ids,
                                                "other", [], rows, judge_time=True)
                ok += claims

                fill(store, 42, 200)
                ids200, _ = board_ids(api, str(root / "coscc"))
                runs200 = [load(browser, app.base, token, None, "coscc", ids200) for _ in range(RUNS)]
                for label, n, runs in (("42", len(ids), runs42), ("200", len(ids200), runs200)):
                    b = max(r["bytes"] for r in runs)
                    ms = statistics.median(r["ms"] for r in runs)
                    print(f"INFO  (h) R5 fixture {label}: {n} cards, {b} B, median {ms:.0f} ms, "
                          f"{b / max(n, 1):.0f} B per card; loads {[r['bytes'] for r in runs]} B, "
                          f"{[round(r['ms']) for r in runs]} ms")
                api.close()
        finally:
            browser.close()
            playwright.stop()

        ok += asyncio.run(in_process(proj, 42))
    finally:
        shutil.rmtree(top, ignore_errors=True)
    print(f"\n{sum(ok)}/{len(ok)} claims PASS")
    return EXIT_PASS if ok and all(ok) else EXIT_BROKEN


def against(base: str, ws: str, other: str) -> int:
    password = os.environ.get("COS_PROOF_PASSWORD", "")
    if not password:
        print("--url needs COS_PROOF_PASSWORD, the running app's master password", file=sys.stderr)
        return EXIT_ENV
    base = base.rstrip("/")
    with httpx.Client(base_url=base, timeout=300) as api:
        if api.post(auth.LOGIN, data={"password": password}).status_code >= 400:
            print(f"{base} refused the password", file=sys.stderr)
            return EXIT_ENV
        paths = {w["name"]: w["path"] for w in api.get("/api/workspaces").json().get("workspaces") or []}
        if ws not in paths or other not in paths:
            print(f"--ws {ws!r} and --from {other!r} must both be workspaces of {base}: {sorted(paths)}",
                  file=sys.stderr)
            return EXIT_ENV
        ids, _ = board_ids(api, paths[ws])
        other_ids, _ = board_ids(api, paths[other])
    print(f"{ws}: {len(ids)} cards on /api/board at {time.strftime('%Y-%m-%d %H:%M:%S')}; "
          f"{other}: {len(other_ids)}")
    loopback = (urlparse(base).hostname or "") in ("127.0.0.1", "localhost", "::1")
    playwright, browser = require_browser()
    try:
        ok, _ = browser_claims(browser, base, None, password, ws, ids, other, other_ids,
                               None, judge_time=loopback)
    finally:
        browser.close()
        playwright.stop()
    print(f"\n{sum(ok)}/{len(ok)} claims PASS")
    return EXIT_PASS if ok and all(ok) else EXIT_BROKEN


def main(argv: list[str]) -> int:
    if argv[:1] == ["--url"]:
        args = dict(zip(argv[0::2], argv[1::2]))
        if len(argv) != 6 or not {"--url", "--ws", "--from"} <= set(args):
            print("usage: verify_0053.py [--url <base> --ws <name> --from <name>]", file=sys.stderr)
            return EXIT_ENV
        return against(args["--url"], args["--ws"], args["--from"])
    if argv:
        print("usage: verify_0053.py [--url <base> --ws <name> --from <name>]", file=sys.stderr)
        return EXIT_ENV
    return plain()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
