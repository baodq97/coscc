#!/usr/bin/env python3
"""The end-to-end cases: the page as built, in chromium, behind the login (`0095` R13).

    COS_HOST=127.0.0.1 COS_PORT=<port> uv run coscc-build && npm run e2e

It starts `coscc.run` on the address the bundle was built for (`COS_HOST`, `COS_PORT`), on a
temporary working folder and data root with two workspaces, `proj` and `other`, each a clone
of a bare-directory remote. A password nobody types and one session are written into that
data root before the app starts, so every case runs past the `0070` login without `/setup`.
No session is opened, no quota is spent and nothing leaves the machine.

Each case is one function named for what it shows, and prints `PASS` or `FAIL` for each
thing it checks. The cases came from the browser proofs `0095` retired; `impl.md` of that
unit names which claim went where.

Exit codes: 0 every case passed, 1 one did not, 2 the environment is not ready — no bundle
for this address, the port in use, or no chromium. It is not part of `npm test`.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from coscc import auth, hold
from coscc.runlog import notices  # noqa: E402
from coscc.config import from_env  # noqa: E402
from coscc.runlog.journal import Journal  # noqa: E402
from scripts.proof_harness import (  # noqa: E402
    EXIT_BROKEN,
    EXIT_PASS,
    Cut,
    RealApp,
    free_port,
    make_repo,
    require_browser,
    require_build,
    require_free_port,
    seed_session,
    say,
)

SIZE = {"width": 1440, "height": 900}  # the sidebar is on screen from `lg` up
TIMEOUT_MS = 20_000
PLACE_TIMEOUT_S = 15.0
SCREENS = ("overview", "workspaces", "board", "sessions", "activity", "settings")

# Where the page says it is: the sidebar button marked `aria-current=page`, the chosen
# workspace, the open dialog and its selected tab.
WHERE_JS = """
() => {
  const cur = document.querySelector('[id^="nav-"][aria-current="page"]');
  const sel = document.querySelector('#workspace-switcher');
  const opt = sel && sel.selectedOptions && sel.selectedOptions[0];
  const dlg = document.querySelector('[role=dialog]');
  const tab = dlg && dlg.querySelector('[role=tab][aria-selected="true"]');
  const value = tab && (tab.id.match(/-trigger-(.+)$/) || [])[1];
  return {
    screen: cur ? cur.id.slice(4) : '', ws: opt ? opt.textContent.trim() : '',
    dialog: dlg ? dlg.innerText : null, tab: value || '',
    loc: location.pathname + location.search,
  };
}
"""


# --------------------------------------------------------------------------
# the fixture
# --------------------------------------------------------------------------


def intent(title: str, tail: str = "") -> str:
    return (f"# Intent: {title}\nAuthor: e2e. Type: feat. Status: accepted.\n\n"
            "## Problem\n\nMột unit để mở trên trang.\n\n"
            "## Open questions\n\n1. **Câu hỏi còn mở của unit này?**\n" + tail)


class Scene:
    """The two workspaces and their units, made through the app's own routes."""

    def __init__(self, api: httpx.Client, proj: Path, other: Path) -> None:
        self.api, self.paths = api, {}
        self.alpha = self.unit(proj, "alpha", intent("alpha"))
        self.beta = self.unit(other, "beta", intent("beta"))
        self.gone = self.unit(proj, "gone", intent(
            "gone", "\n## Answers\n" + hold.block("dropped", "e2e", "2026-09-26", "no longer needed")))

    def unit(self, cwd: Path, slug: str, text: str) -> str:
        made = self.api.post("/api/units", json={"cwd": str(cwd), "slug": slug, "brief": "e2e"})
        made.raise_for_status()
        self.paths[made.json()["unit"]] = Path(made.json()["path"])
        (self.paths[made.json()["unit"]] / "intent.md").write_text(text, encoding="utf-8")
        return str(made.json()["unit"])


# --------------------------------------------------------------------------
# the page
# --------------------------------------------------------------------------


def href(screen: str, ws: str, unit: str = "", tab: str = "overview") -> str:
    if unit:
        return f"/unit?ws={quote(ws)}&id={unit}" + ("" if tab == "overview" else f"&tab={tab}")
    return f"{'/' if screen == 'overview' else '/' + screen}?ws={quote(ws)}"


def miss(seen: dict, screen: str, ws: str, unit: str = "", tab: str = "overview") -> str:
    """Empty when `seen` is that place, else what differs."""
    got = []
    if seen["screen"] != ("board" if unit else screen):
        got.append(f"screen {seen['screen']!r}")
    if seen["ws"] != ws:
        got.append(f"ws {seen['ws']!r}")
    if unit and (seen["dialog"] is None or unit not in seen["dialog"]):
        got.append("no dialog for the unit")
    elif unit and seen["tab"] != tab:
        got.append(f"tab {seen['tab']!r}")
    elif not unit and seen["dialog"] is not None:
        got.append("a dialog is open")
    return ", ".join(got)


def settle(page, *place: str) -> str:
    """Wait until the page is at `place`; empty when it got there, else where it is."""
    deadline, why = time.monotonic() + PLACE_TIMEOUT_S, "never read"
    while time.monotonic() < deadline:
        seen = page.evaluate(WHERE_JS)
        why = miss(seen, *place)
        if not why:
            page.wait_for_timeout(300)
            if not miss(page.evaluate(WHERE_JS), *place):
                return ""
        page.wait_for_timeout(150)
    return f"{why} at {seen['loc']}"


def arrive(page, base: str, path: str) -> str:
    """Empty when the last response is a 200 after at most one 307 and the shell is drawn."""
    response = page.goto(base + path, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
    if response is None:
        return "no response"
    hops, request = [], response.request.redirected_from
    while request is not None:
        hops.append(request.response().status if request.response() else 0)
        request = request.redirected_from
    if response.status != 200 or len(hops) > 1 or any(h != 307 for h in hops):
        return f"status {response.status} after {hops}"
    page.wait_for_selector("#studio-shell", timeout=TIMEOUT_MS)
    return ""


# --------------------------------------------------------------------------
# the cases
# --------------------------------------------------------------------------


def every_place_opens_at_its_address_and_stays_after_a_reload(context, base, scene) -> bool:
    """`0003`, `0056` (a) and (b): each screen and a unit on a tab, pasted into a new tab."""
    places = [(s, "proj") for s in SCREENS] + [
        ("unit", "proj", scene.alpha), ("unit", "proj", scene.alpha, "questions"),
        ("unit", "other", scene.beta)]
    ok = True
    for place in places:
        page = context.new_page()
        try:
            why = arrive(page, base, href(*place)) or settle(page, *place)
            if not why:
                page.reload(wait_until="domcontentloaded", timeout=TIMEOUT_MS)
                why = settle(page, *place)
            ok &= say(not why, f"{href(*place)} opens there and a reload stays", why)
        finally:
            page.close()
    return ok


def back_and_forward_return_to_the_screens_the_page_moved_between(context, base, scene) -> bool:
    """`0056` (c): moved with the sidebar, Back and Forward retrace it."""
    page = context.new_page()
    try:
        why = arrive(page, base, href("board", "proj")) or settle(page, "board", "proj")
        for label, step, place in (("the sidebar", lambda: page.click("#nav-settings"), ("settings", "proj")),
                                   ("Back", page.go_back, ("board", "proj")),
                                   ("Forward", page.go_forward, ("settings", "proj"))):
            if why:
                break
            step()
            why = settle(page, *place)
            why = why and f"after {label}: {why}"
        return say(not why, "board → settings, Back to board, Forward to settings", why)
    finally:
        page.close()


def the_board_search_keeps_only_the_cards_it_matches(context, base, scene) -> bool:
    """`0006` flow 2: a search that matches nothing empties the board; one that matches finds it."""
    page = context.new_page()
    try:
        why = arrive(page, base, href("board", "proj")) or settle(page, "board", "proj")
        card = f'[id="unit-{scene.alpha}"]'
        if not why:
            page.wait_for_selector(card, timeout=TIMEOUT_MS)
            page.fill("#work-search", "no-unit-is-called-this")
            page.wait_for_selector(card, state="detached", timeout=TIMEOUT_MS)
            none = page.locator('[data-testid="work-card"]').count()
            page.fill("#work-search", "alpha")
            page.wait_for_selector(card, timeout=TIMEOUT_MS)
            shown = page.locator('[data-testid="work-card"]').count()
            why = "" if (none, shown) == (0, 1) else f"{none} cards for nothing, {shown} for alpha"
        return say(not why, "a search for nothing shows no card, one for alpha shows alpha alone", why)
    finally:
        page.close()


def an_answer_sent_from_the_dialog_is_appended_and_shown(context, base, scene) -> bool:
    """`0071` (a), `0016`: Send this answer appends one block and the dialog shows it."""
    page = context.new_page()
    words = "Có, đã trả lời từ bộ e2e."
    try:
        place = ("unit", "proj", scene.alpha, "questions")
        why = arrive(page, base, href(*place)) or settle(page, *place)
        if not why:
            page.fill('[aria-label="Answer to intent.md#1"]', words)
            page.click('[id="answer-intent.md#1"]')
            text, deadline = "", time.monotonic() + PLACE_TIMEOUT_S
            while time.monotonic() < deadline and words not in text:
                page.wait_for_timeout(200)
                text = (scene.paths[scene.alpha] / "intent.md").read_text(encoding="utf-8")
            said = "Answered question 1 of intent.md"
            dialog = page.locator("[role=dialog]", has_text=said)
            shown = dialog.count() or (dialog.wait_for(timeout=TIMEOUT_MS) or 1)
            why = ("" if text.count("### Câu 1") == 1 and text.startswith(intent("alpha").rstrip("\n"))
                   and shown else f"file ends {text[-200:]!r}")
        return say(not why, "one ### Câu 1 is appended, nothing above it moves, and the dialog says so", why)
    finally:
        page.close()


def a_dropped_unit_opens_with_nothing_that_writes(context, base, scene) -> bool:
    """`0056` R11: its question is shown with no box to answer it."""
    page = context.new_page()
    try:
        place = ("unit", "proj", scene.gone, "questions")
        why = arrive(page, base, href(*place)) or settle(page, *place)
        if not why:
            dialog = page.locator("[role=dialog]")
            present = dialog.locator("#unit-dropped").count()
            boxes = dialog.locator('[id^="answer-"]').count()
            why = "" if present and not boxes else f"#unit-dropped {present}, answer buttons {boxes}"
        return say(not why, "a dropped unit is marked dropped and offers no answer", why)
    finally:
        page.close()


def a_page_without_a_session_is_sent_to_the_login(browser, base) -> bool:
    """`0070`: a context carrying no cookie is let into no screen."""
    context = browser.new_context(viewport=SIZE)
    try:
        page = context.new_page()
        page.goto(base + "/board?ws=proj", wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
        path = page.evaluate("location.pathname")
        shell = page.locator("#studio-shell").count()
        return say(path == auth.LOGIN and not shell, "/board with no session lands on /login and draws no screen",
                   f"at {path}, shell {shell}")
    finally:
        context.close()


def logging_out_ends_at_the_login_page(context, base, scene) -> bool:
    """`0070` --browser: *Log out* ends the session and `/` then asks for the password."""
    page = context.new_page()
    try:
        why = arrive(page, base, href("overview", "proj")) or settle(page, "overview", "proj")
        if not why:
            page.click("#logout")
            page.wait_for_url(f"**{auth.LOGIN}*", timeout=TIMEOUT_MS)
            page.goto(base + "/", wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
            path = page.evaluate("location.pathname")
            why = "" if path == auth.LOGIN else f"/ after logging out is {path}"
        return say(not why, "Log out lands on /login, and / asks for the password again", why)
    finally:
        page.close()


# --------------------------------------------------------------------------
# `0113`: the notices, through a connection a proxy cuts
# --------------------------------------------------------------------------

NOTICES_DOC = Path(__file__).resolve().parent.parent / ".claude" / "docs" / "coscc-notices.md"
# R14: every notice reaches both listeners within this many seconds of its record.
NOTICE_LIMIT_S = 60.0
# How long past its record the case waits before calling a notice missing: longer than the
# limit, so a late one is reported late, with its figure, rather than missing.
NOTICE_WAIT_S = 90.0
OUTAGE_S = 5.0

# After `spike.md ## U2`'s `drive.py`: every cursor write with the notices in the DOM at that
# moment, the moment each notice's node first appeared, and every time one was added.
NOTICE_INIT = """
window.__probe_writes = [];
window.__probe_dom = {};
window.__probe_adds = [];
const _set = Storage.prototype.setItem;
Storage.prototype.setItem = function (k, v) {
  if (k === "coscc_notice_after") {
    const ids = [...document.querySelectorAll("[data-notice-id]")].map(e => +e.dataset.noticeId);
    window.__probe_writes.push({v: +v, rendered: ids});
  }
  return _set.call(this, k, v);
};
new MutationObserver(ms => {
  for (const m of ms) for (const n of m.addedNodes)
    if (n.nodeType === 1 && n.dataset && n.dataset.noticeId) {
      window.__probe_adds.push(+n.dataset.noticeId);
      if (!(n.dataset.noticeId in window.__probe_dom)) window.__probe_dom[n.dataset.noticeId] = Date.now();
    }
}).observe(document, {childList: true, subtree: true});
"""

# Review round 1, F2: both listeners start with a cursor from a run log that is gone, past
# every row, and must be handed a `head` that sets it back. Once per browser profile.
STALE_CURSOR = "999999999"
STALE_INIT = """
if (!localStorage.getItem("e2e_seeded")) {
  localStorage.setItem("e2e_seeded", "1");
  localStorage.setItem("coscc_notice_after", "%s");
}
""" % STALE_CURSOR

NOTICE_STATE_JS = """
() => ({dom: window.__probe_dom || {}, adds: window.__probe_adds || [], writes: window.__probe_writes || [],
        opens: window.__coscc_notice_opens || 0, cursor: localStorage.getItem('coscc_notice_after')})
"""


def listener_block() -> str:
    """The terminal command of R11, exactly as the document gives it."""
    text = NOTICES_DOC.read_text(encoding="utf-8")
    body = text.split("<!-- listener -->", 1)[1].split("<!-- /listener -->", 1)[0].strip()
    return body.removeprefix("```bash").removesuffix("```").strip()


def write_notices(journal, key: str, tag: str, only_one: bool = False) -> tuple[dict[int, float], set[int]]:
    """R2's five source records and three that are no notice, as another process writes them
    (R7's 20 s road). `({id: when its append returned}, {ids that must reach nobody})`."""
    base = {"workspace": key, "unit": "0001_notices"}
    told = [
        {**base, "kind": "autopilot-stop", "stage": "", "stop": "a", "reason": "open questions: spec.md question 1"},
        {**base, "kind": "questions", "stage": "spec", "questions": [{"artifact": "spec.md", "n": 1}]},
        {**base, "kind": "end", "stage": "impl", "outcome": "failed"},
        {**base, "kind": "ship", "stage": "ship", "result": "refused"},
        {**base, "kind": "ship", "stage": "ship", "result": "shipped"},
    ]
    untold = [
        {**base, "kind": "end", "stage": "spec", "outcome": "done"},
        {**base, "kind": "autopilot-stop", "stage": "", "stop": "", "reason": ""},
        {**base, "kind": "autopilot-stop", "stage": "", "stop": "full", "reason": "waiting for a free place"},
    ]
    if only_one:
        told, untold = told[-1:], []
    when: dict[str, float] = {}
    for i, record in enumerate(told + untold):
        journal.append({**record, "e2e": f"{tag}-{i}"})
        when[f"{tag}-{i}"] = time.time()
    ids = {r["e2e"]: rid for rid, r in journal.notice_rows(0, notices.SOURCE_KINDS) if str(r.get("e2e", "")).startswith(f"{tag}-")}
    return ({ids[f"{tag}-{i}"]: when[f"{tag}-{i}"] for i in range(len(told))},
            {ids[f"{tag}-{i}"] for i in range(len(told), len(told) + len(untold))})


def arrivals(page, heard: list) -> dict:
    """When each notice id first reached the page's DOM and the terminal, and any id either
    was handed twice."""
    st = page.evaluate(NOTICE_STATE_JS)
    term: dict[int, float] = {}
    kinds: dict[int, str] = {}
    twice = []
    for at, line in list(heard):
        if line.get("type") != "notice":
            continue
        if line["id"] in term:
            twice.append(line["id"])
        term.setdefault(line["id"], at)
        kinds[line["id"]] = line["kind"]
    adds = st["adds"]
    return {
        "page": {int(k): v / 1000 for k, v in st["dom"].items()}, "term": term, "kinds": kinds,
        "twice": sorted(twice + [i for i in set(adds) if adds.count(i) > 1]), "state": st,
    }


def expect_notices(page, heard: list, told: dict[int, float], untold: set[int], label: str) -> bool:
    deadline = max(told.values()) + NOTICE_WAIT_S
    seen = arrivals(page, heard)
    while time.time() < deadline and not (set(told) <= set(seen["page"]) and set(told) <= set(seen["term"])):
        page.wait_for_timeout(250)
        seen = arrivals(page, heard)
    # One more look, so a notice that is no notice has had the same time to arrive.
    page.wait_for_timeout(1000)
    seen = arrivals(page, heard)
    missing = {side: sorted(set(told) - set(seen[side])) for side in ("page", "term")}
    ok = say(not missing["page"] and not missing["term"],
             f"{label}: every notice reaches the page and the terminal", f"missing {missing}")
    kinds = sorted({seen["kinds"][i] for i in told if i in seen["kinds"]})
    if len(told) == 5:
        ok &= say(kinds == sorted(notices.KINDS), f"{label}: the five kinds are {', '.join(kinds)}", f"got {kinds}")
    leaked = sorted(untold & (set(seen["page"]) | set(seen["term"])))
    if untold:
        ok &= say(not leaked, f"{label}: the done end, the cleared stop and the full stop reach neither", f"{leaked}")
    late = {side: max((seen[side][i] - told[i] for i in told if i in seen[side]), default=float("nan"))
            for side in ("page", "term")}
    ok &= say(all(v <= NOTICE_LIMIT_S for v in late.values()),
              f"{label}: the latest took {late['page']:.2f} s to the page and {late['term']:.2f} s to the "
              f"terminal, limit {NOTICE_LIMIT_S:.0f} s", "")
    ok &= say(not seen["twice"], f"{label}: no notice arrives twice", f"{seen['twice']}")
    return ok


def cursor_never_passes_the_dom(page, known: set[int], label: str) -> bool:
    writes = page.evaluate(NOTICE_STATE_JS)["writes"]
    bad = [w["v"] for w in writes if w["v"] in known and w["v"] not in w["rendered"]]
    passed = [w["v"] for w in writes if any(i <= w["v"] and i not in w["rendered"] for i in known)]
    return say(not bad and not passed,
               f"{label}: {len(writes)} cursor writes, none past a notice not yet in the DOM",
               f"not in the DOM {bad}, passed one {passed}")


def notices_through(browser, app, cut, journal, key: str, token: str, jar: Path, where: Path, mode: str) -> bool:
    """One cut, `close` or `stall`, with a fresh browser profile and a fresh terminal cursor."""
    ok = True
    heard: list[tuple[float, dict]] = []
    after_file = where / f"after-{mode}"
    after_file.write_text(STALE_CURSOR + "\n", encoding="utf-8")
    proc = subprocess.Popen(
        ["bash", "-c", listener_block()],
        env={**os.environ, "COSCC_BASE": app.base, "COSCC_JAR": str(jar), "COSCC_AFTER_FILE": str(after_file)},
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, start_new_session=True,
    )

    def listen() -> None:
        for line in proc.stdout:
            try:
                heard.append((time.time(), json.loads(line)))
            except ValueError:
                pass

    threading.Thread(target=listen, daemon=True).start()
    context = browser.new_context(viewport=SIZE)
    context.set_default_timeout(TIMEOUT_MS)
    context.add_cookies([{"name": auth.COOKIE, "value": token, "url": app.base}])
    context.add_init_script(STALE_INIT)
    context.add_init_script(NOTICE_INIT)
    try:
        page = context.new_page()
        why = arrive(page, app.base, href("board", "proj"))
        deadline = time.monotonic() + 30

        def cursors() -> tuple[str, str]:
            return (page.evaluate(NOTICE_STATE_JS)["cursor"] or "",
                    after_file.read_text().strip() if after_file.is_file() else "")

        while not why and time.monotonic() < deadline and not all(c and c != STALE_CURSOR for c in cursors()):
            page.wait_for_timeout(200)
        if not why and time.monotonic() >= deadline:
            why = f"the page or the terminal kept its cursor: {cursors()}"
        if not say(not why, f"{mode}: the page and the terminal set a cursor past every row back to the head",
                   why):
            return False

        first, untold = write_notices(journal, key, f"{mode}-1")
        ok &= expect_notices(page, heard, first, untold, f"{mode}, connected")

        # The server ends each stream after `notices.LIFETIME_SECONDS` and the page connects
        # again, which is an open too: start right after one, so the next is that far away.
        was = page.evaluate(NOTICE_STATE_JS)["opens"]
        deadline = time.monotonic() + notices.LIFETIME_SECONDS + 10
        while page.evaluate(NOTICE_STATE_JS)["opens"] == was and time.monotonic() < deadline:
            page.wait_for_timeout(100)
        before = page.evaluate(NOTICE_STATE_JS)["opens"]
        ok &= say(before > was, f"{mode}: the page connects again when the server ends its stream",
                  f"still {was} after {notices.LIFETIME_SECONDS + 10:.0f} s")
        for nav in ("#nav-sessions", "#nav-board"):
            page.click(nav)
            page.wait_for_timeout(1500)
        opens = page.evaluate(NOTICE_STATE_JS)["opens"]
        ok &= say(opens == before, f"{mode}: two route changes open no second connection", f"{before} → {opens}")

        (cut.close if mode == "close" else cut.stall)()
        page.wait_for_timeout(1500)
        second, untold = write_notices(journal, key, f"{mode}-2")
        page.wait_for_timeout(int((OUTAGE_S - 1.5) * 1000))
        cut.restore()
        ok &= expect_notices(page, heard, second, untold, f"{mode}, written during the cut")
        ok &= cursor_never_passes_the_dom(page, set(first) | set(second), mode)

        page.close()
        third, _ = write_notices(journal, key, f"{mode}-3", only_one=True)
        [(gone, written)] = third.items()
        page = context.new_page()
        why = arrive(page, app.base, href("board", "proj"))
        seen = arrivals(page, heard)
        while not why and gone not in seen["page"] and time.time() < written + NOTICE_WAIT_S:
            page.wait_for_timeout(250)
            seen = arrivals(page, heard)
        took = seen["page"].get(gone, float("nan")) - written
        ok &= say(not why and took <= NOTICE_LIMIT_S,
                  f"{mode}: a notice written with no tab open reaches the next tab in {took:.2f} s", why)
        return ok
    finally:
        context.close()
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        proc.wait(timeout=10)


def notices_reach_the_page_and_a_terminal_through_close_and_stall(browser, config) -> bool:
    """`0113` R14. The app behind a `Cut` on the bundle's address; the page as built and the
    terminal command of `.claude/docs/coscc-notices.md`, both through it. Records are written
    from this process, so they take R7's 20 s road; the 5 s one is `service_notices_test.py`'s."""
    if not (shutil.which("bash") and shutil.which("curl")):
        return say(False, "bash and curl are on PATH, for the terminal listener")
    root = Path(tempfile.mkdtemp(prefix="cos-e2e-notices-work-")).resolve()
    data_dir = Path(tempfile.mkdtemp(prefix="cos-e2e-notices-data-")).resolve()
    outside = Path(tempfile.mkdtemp(prefix="cos-e2e-notices-remote-")).resolve()
    try:
        proj = make_repo(root, outside, "proj", "proj.git", "e2e\n")
        token = seed_session(data_dir)
        jar = data_dir / "cookies.txt"
        jar.write_text(f"{config.host}\tFALSE\t/\tFALSE\t0\t{auth.COOKIE}\t{token}\n", encoding="utf-8")
        behind = free_port(config.host)
        with RealApp(config, root, data_dir, behind=behind) as app, Cut(config.host, config.port, behind) as cut, \
                httpx.Client(base_url=app.direct, timeout=30, cookies={auth.COOKIE: token}) as api:
            api.post("/api/workspaces", json={"name": "proj"}).raise_for_status()
            journal = Journal(root, data_dir)
            ok = True
            for mode in ("close", "stall"):
                ok &= notices_through(browser, app, cut, journal, str(proj.resolve()), token, jar, data_dir, mode)
            return ok
    finally:
        for d in (root, data_dir, outside):
            shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------


def run(case, *args) -> bool:
    """One case; a case that raises has failed, and the others still run."""
    print(f"\n{case.__name__}")
    try:
        return case(*args)
    except Exception as e:  # noqa: BLE001 - reported as the failure it is
        return say(False, "the case ran to its end", f"{type(e).__name__}: {str(e).splitlines()[0]}")


def main() -> int:
    # A step inherits `__REFLEX_*` blank, and a blank mount flag leaves `/` a 404.
    for name in [k for k, v in os.environ.items() if k.startswith("__REFLEX") and not v]:
        del os.environ[name]
    config = from_env()
    require_build(config)
    require_free_port(config)
    playwright, browser = require_browser()
    root = Path(tempfile.mkdtemp(prefix="cos-e2e-work-")).resolve()
    data_dir = Path(tempfile.mkdtemp(prefix="cos-e2e-data-")).resolve()
    outside = Path(tempfile.mkdtemp(prefix="cos-e2e-remote-")).resolve()
    results: list[bool] = []
    try:
        proj, other = (make_repo(root, outside, name, f"{name}.git", "e2e\n") for name in ("proj", "other"))
        token = seed_session(data_dir)
        with RealApp(config, root, data_dir) as app, \
                httpx.Client(base_url=app.base, timeout=30, cookies={auth.COOKIE: token}) as api:
            for name in ("proj", "other"):
                api.post("/api/workspaces", json={"name": name}).raise_for_status()
            scene = Scene(api, proj, other)
            context = browser.new_context(viewport=SIZE)
            context.set_default_timeout(TIMEOUT_MS)
            context.add_cookies([{"name": auth.COOKIE, "value": token, "url": app.base}])
            try:
                for case in (every_place_opens_at_its_address_and_stays_after_a_reload,
                             back_and_forward_return_to_the_screens_the_page_moved_between,
                             the_board_search_keeps_only_the_cards_it_matches,
                             an_answer_sent_from_the_dialog_is_appended_and_shown,
                             a_dropped_unit_opens_with_nothing_that_writes):
                    results.append(run(case, context, app.base, scene))
                results.append(run(a_page_without_a_session_is_sent_to_the_login, browser, app.base))
                # Last: it ends the one session every other case runs on.
                results.append(run(logging_out_ends_at_the_login_page, context, app.base, scene))
            finally:
                context.close()
        # `0113`: after the app above has let go of the bundle's address, where its `Cut` stands.
        results.append(run(notices_reach_the_page_and_a_terminal_through_close_and_stall, browser, config))
    finally:
        browser.close()
        playwright.stop()
        for d in (root, data_dir, outside):
            shutil.rmtree(d, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} cases passed")
    return EXIT_PASS if results and all(results) else EXIT_BROKEN


if __name__ == "__main__":
    sys.exit(main())
