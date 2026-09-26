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

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from coscc import auth, hold  # noqa: E402
from coscc.config import from_env  # noqa: E402
from scripts.proof_harness import (  # noqa: E402
    EXIT_BROKEN,
    EXIT_PASS,
    RealApp,
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
    finally:
        browser.close()
        playwright.stop()
        for d in (root, data_dir, outside):
            shutil.rmtree(d, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} cases passed")
    return EXIT_PASS if results and all(results) else EXIT_BROKEN


if __name__ == "__main__":
    sys.exit(main())
