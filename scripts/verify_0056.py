#!/usr/bin/env python3
"""Proof for `0056_the-page-has-no-url-and-reload-loses-place` (`plan.md` step 7).

Every screen has an address, and in chromium, for each of the nine places below, the three
things the intent's outcome asks for hold:

    (a) the address pasted into a new tab opens that place — the right screen, the
        workspace in `ws`, and for `/unit` the unit and its tab; over HTTP a 200, after at
        most one 307, never the SPA fallback's 404 (`spec.md` R4)
    (b) a reload there stays there (R5); before the Board's reload a unit is written to
        the store, and after it its card is on the page (R15)
    (c) moved from A to B with a control on the page, Back returns to A and Forward to B
        (R6); from a unit, a change of tab is replaced, so Back leaves the unit (R7)

and beside them: one `/_event` socket through all of (c) (R14), a unit that does not exist
(R9), `/unit` with no `id` (R10), a workspace not on the list and a tab that does not exist
(R8), a dropped unit read-only but for its hold panel (R11), and — in place of `spec.md`
R20, which names three proofs `0070` left at the login page (`plan.md` Risk 4) — `/` still
opening Overview with a live socket.

    0  every line PASS
    1  a line FAIL
    2  the environment is not ready: no build, a stale one, no browser, the port in use,
       or `--url` without `COS_PROOF_PASSWORD`

Plain: the checkout's bundle on `COS_PORT` (`uv run coscc-build` first, as `verify_0071`),
a temporary data root with two workspaces, `proj` and `other`, and a seeded session, so the
`0070` login is passed without `/setup`. `--url <base>` measures a running app instead —
an install from `install.sh`, say — logging in with `COS_PROOF_PASSWORD`, on its first
workspace and the first card its Board shows; it writes nothing, so R15's late unit and
R11 (no dropped unit to open) print `SKIP`, which the plain run never accepts. Neither
opens a session or spends quota. Never run this beside `verify_0003`, `0006` or `0071`.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argon2  # noqa: E402
import httpx  # noqa: E402

from coscc import auth, hold  # noqa: E402
from coscc.config import from_env  # noqa: E402
from coscc.data import Data  # noqa: E402
from scripts.proof_harness import (  # noqa: E402
    EXIT_BROKEN,
    EXIT_ENV,
    EXIT_PASS,
    RealApp,
    require_browser,
    require_build,
    require_free_port,
    say,
)

SIZE = {"width": 1440, "height": 900}  # the sidebar is on screen from `lg` up
PAGE_TIMEOUT_MS = 20_000
PLACE_TIMEOUT_S = 15.0
SCREENS = ("overview", "workspaces", "board", "sessions", "activity", "settings")
TAB_LABELS = {"overview": "Overview", "artifacts": "Artifact", "questions": "Questions",
              "comments": "PR comments", "timeline": "Timeline"}
GIT_ID = ("-c", "user.name=verify", "-c", "user.email=verify@example.invalid",
          "-c", "commit.gpgsign=false")

# Where the page says it is. The screen is the sidebar button marked `aria-current=page`;
# the workspace is the select's chosen option, whose text is the workspace's name.
WHERE_JS = """
() => {
  const cur = document.querySelector('[id^="nav-"][aria-current="page"]');
  const sel = document.querySelector('#workspace-switcher');
  const opt = sel && sel.selectedOptions && sel.selectedOptions[0];
  const dlg = document.querySelector('[role=dialog]');
  const tab = dlg && dlg.querySelector('[role=tab][aria-selected="true"]');
  // Radix names a trigger `<base>-trigger-<value>`; its text carries the label twice.
  const value = tab && (tab.id.match(/-trigger-(.+)$/) || [])[1];
  return {
    screen: cur ? cur.id.slice(4) : '', ws: opt ? opt.textContent.trim() : '',
    dialog: dlg ? dlg.innerText : null, tab: value || '',
    loc: location.pathname + location.search,
  };
}
"""


def git(where: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(where), *GIT_ID, *args], capture_output=True, check=True)


def make_repo(root: Path, outside: Path, name: str) -> Path:
    """A workspace: a clone of a bare-directory remote, one commit on `main`."""
    remote = outside / f"{name}.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    proj = root / name
    subprocess.run(["git", "clone", "-q", str(remote), str(proj)], check=True, capture_output=True)
    git(proj, "symbolic-ref", "HEAD", "refs/heads/main")
    (proj / "README.md").write_text("verify_0056\n", encoding="utf-8")
    git(proj, "add", "-A")
    git(proj, "commit", "-q", "-m", "a repository")
    git(proj, "push", "-q", "origin", "main")
    return proj


def seed_session(data_dir: Path) -> str:
    """`0070`: a password nobody types and one live session, as `verify_0071` seeds them."""
    data = Data(data_dir)
    now = int(time.time())
    data.auth_set_password(argon2.PasswordHasher().hash(secrets.token_urlsafe(24)), now)
    token = secrets.token_urlsafe(32)
    data.auth_session_add(auth._sha(token), now, now + auth.SESSION_TTL)
    return token


def make_unit(api: httpx.Client, cwd: str, slug: str, intent: str) -> str:
    made = api.post("/api/units", json={"cwd": cwd, "slug": slug, "brief": "verify_0056"})
    if made.status_code != 200:
        raise SystemExit(f"could not make a unit: {made.text}")
    (Path(made.json()["path"]) / "intent.md").write_text(intent, encoding="utf-8")
    return str(made.json()["unit"])


def intent(title: str, tail: str = "") -> str:
    return (f"# Intent: {title}\nAuthor: verify_0056. Type: feat. Status: accepted.\n\n"
            "## Problem\n\nMột unit để mở bằng địa chỉ.\n\n"
            "## Open questions\n\n1. **Câu hỏi còn mở của unit này?**\n" + tail)


# --------------------------------------------------------------------------
# the page
# --------------------------------------------------------------------------


class Want:
    """A place as the page draws it: screen, workspace name, and the open unit and tab."""

    def __init__(self, screen: str, ws: str, unit: str = "", tab: str = "overview"):
        self.screen, self.ws, self.unit, self.tab = screen, ws, unit, tab

    def href(self) -> str:
        if self.unit:
            tab = "" if self.tab == "overview" else f"&tab={self.tab}"
            return f"/unit?ws={quote(self.ws)}&id={self.unit}{tab}"
        return f"{'/' if self.screen == 'overview' else '/' + self.screen}?ws={quote(self.ws)}"

    def miss(self, seen: dict) -> str:
        """Empty when `seen` is this place, else what differs."""
        screen = "board" if self.unit else self.screen
        got = []
        if seen["screen"] != screen:
            got.append(f"screen {seen['screen']!r}")
        if seen["ws"] != self.ws:
            got.append(f"ws {seen['ws']!r}")
        if self.unit:
            if seen["dialog"] is None or self.unit not in seen["dialog"]:
                got.append("no dialog for the unit")
            elif seen["tab"] != self.tab:
                got.append(f"tab {seen['tab']!r}")
        elif seen["dialog"] is not None:
            got.append("a dialog is open")
        return ", ".join(got)

    def __str__(self) -> str:
        return self.href()


def settle(page, want: Want) -> str:
    """Wait until the page is at `want`; empty when it got there, else where it is."""
    deadline = time.monotonic() + PLACE_TIMEOUT_S
    miss = "never read"
    while time.monotonic() < deadline:
        seen = page.evaluate(WHERE_JS)
        miss = want.miss(seen)
        if not miss:
            page.wait_for_timeout(300)
            if not want.miss(page.evaluate(WHERE_JS)):
                return ""
        page.wait_for_timeout(150)
    return f"{miss} at {seen['loc']}"


def goto(page, base: str, href: str) -> str:
    """R4 over HTTP: empty when the last response is a 200 after at most one 307."""
    response = page.goto(base + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    if response is None:
        return "no response"
    hops, request = [], response.request.redirected_from
    while request is not None:
        hops.append(request.response().status if request.response() else 0)
        request = request.redirected_from
    if response.status != 200 or len(hops) > 1 or any(h != 307 for h in hops):
        return f"status {response.status} after {hops}"
    page.wait_for_selector("#studio-shell", timeout=PAGE_TIMEOUT_MS)
    return ""


def pasted(context, base: str, want: Want) -> tuple[bool, str]:
    """(a): a new tab of the same logged-in context."""
    page = context.new_page()
    try:
        http = goto(page, base, want.href())
        where = "" if http else settle(page, want)
        return not (http or where), http or where
    finally:
        page.close()


def reloaded(context, base: str, want: Want, before=None, after: str = "") -> tuple[bool, str]:
    """(b): arrive, then reload. `before` runs just before the reload; `after` is a selector
    that must then be on the page (R15)."""
    page = context.new_page()
    try:
        http = goto(page, base, want.href()) or settle(page, want)
        if http:
            return False, f"before the reload: {http}"
        if before:
            before()
        page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        where = settle(page, want)
        if not where and after:
            try:
                page.wait_for_selector(after, timeout=PAGE_TIMEOUT_MS)
            except Exception:  # noqa: BLE001 - reported as the miss it is
                where = f"{after} is not on the page after the reload"
        return not where, where
    finally:
        page.close()


def moved(page, a: Want, b: Want, act) -> tuple[bool, str]:
    """(c): at `a`, `act` moves to `b` through the page; Back is `a` again, Forward `b`."""
    for label, step, want in (("act", act, b), ("back", page.go_back, a),
                              ("forward", page.go_forward, b)):
        step()
        where = settle(page, want)
        if where:
            return False, f"after {label}: {where}"
    return True, ""


def click(page, sel: str):
    return lambda: page.click(sel)


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------


def measure(browser, base: str, cookie: str | None, password: str | None,
            ws: str, other: str, unit: str, other_unit: str, dropped: str, late) -> list[bool]:
    ok: list[bool] = []
    context = browser.new_context(viewport=SIZE)
    context.set_default_timeout(PAGE_TIMEOUT_MS)
    sockets, closed, frames = [], [], []
    try:
        if cookie:
            context.add_cookies([{"name": auth.COOKIE, "value": cookie, "url": base}])
        else:
            login = context.new_page()
            login.goto(base + auth.LOGIN)
            login.fill("#password", password or "")
            login.press("#password", "Enter")
            login.wait_for_selector("#studio-shell", timeout=PAGE_TIMEOUT_MS)
            login.close()

        places = [Want(s, ws) for s in SCREENS] + [
            Want("unit", ws, unit), Want("unit", ws, unit, "questions"),
            Want("unit", other, other_unit)]

        # (a) and (b), each place in a tab of its own.
        for want in places:
            held, why = pasted(context, base, want)
            ok.append(say(held, f"(a) {want} pasted into a new tab opens that place", why))
        for want in places:
            if want.screen == "board" and late is not None:
                held, why = reloaded(context, base, want, before=late[0], after=late[1])
                claim = f"(b) {want} reloaded stays, and shows the unit written just before (R15)"
            else:
                held, why = reloaded(context, base, want)
                claim = f"(b) {want} reloaded stays"
            ok.append(say(held, claim, why))
        if late is None:
            print("SKIP  (b) R15 a unit written before the Board's reload: --url writes nothing")

        # (c) on one tab, whose socket is counted throughout (R14).
        page = context.new_page()

        def on_socket(socket):
            if "/_event" in socket.url:
                sockets.append(socket.url)
                socket.on("close", lambda s: closed.append(s.url))
                socket.on("framereceived", lambda f: frames.append(1))

        page.on("websocket", on_socket)
        start = Want("overview", ws)
        why = goto(page, base, start.href()) or settle(page, start)
        ok.append(say(not why, f"(c) the walk starts at {start}", why))
        # R20's stand-in: `/` behind the login still opens Overview, on a socket that talks.
        ok.append(say(not why and page.locator("#studio-shell").count() == 1 and len(frames) > 0,
                      "R20 (in its stead, plan Risk 4) / opens Overview, #studio-shell, the socket "
                      "receives frames", f"frames={len(frames)} {why}"))
        here = start
        for screen in SCREENS[1:] + ("overview",):
            there = Want(screen, ws)
            held, why = moved(page, here, there, click(page, f"#nav-{screen}"))
            ok.append(say(held, f"(c) {here} → {there} by the sidebar, Back, Forward", why))
            here = there
        board = Want("board", ws)
        page.click("#nav-board")
        settle(page, board)
        opened = Want("unit", ws, unit)
        held, why = moved(page, board, opened, click(page, f'[id="unit-{unit}"]'))
        ok.append(say(held, f"(c) {board} → {opened} by its card, Back, Forward", why))
        # R7: a tab is replaced, so Back from the unit's Questions leaves the unit.
        asked = Want("unit", ws, unit, "questions")
        held, why = moved(page, board, asked,
                          lambda: page.get_by_role("tab", name=TAB_LABELS["questions"]).click())
        ok.append(say(held, f"(c) {opened} → {asked} by its tab; Back is {board}, not the "
                            "Overview tab (R7)", why))
        held, why = moved(page, asked, board,
                          lambda: page.get_by_role("button", name="Close work detail").first.click())
        ok.append(say(held, f"(c) {asked} → {board} by the dialog's close button (R13)", why))
        there = Want("board", other)
        held, why = moved(page, board, there,
                          lambda: page.select_option("#workspace-switcher", label=other))
        ok.append(say(held, f"(c) {board} → {there} by the workspace select", why))
        ok.append(say(len(sockets) == 1 and not closed,
                      "R14 one /_event socket through every move of (c), none closed",
                      f"opened={len(sockets)} closed={len(closed)}"))

        # R9, R10, R8 on fresh tabs.
        probe = context.new_page()
        goto(probe, base, f"/unit?ws={quote(ws)}&id=9999_nope")
        try:
            probe.wait_for_selector("#unit-not-found", timeout=PAGE_TIMEOUT_MS)
            text = probe.inner_text("#unit-not-found")
            link = probe.locator("#unit-not-found a").get_attribute("href") or ""
            held = "9999_nope" in text and ws in text and link == f"/board?ws={quote(ws)}"
            why = f"text={text!r} link={link!r}"
        except Exception as e:  # noqa: BLE001
            held, why = False, f"{type(e).__name__}"
        ok.append(say(held, "R9 a unit that does not exist shows #unit-not-found, its name, "
                            "the workspace's, and a link to its Board", why))
        goto(probe, base, f"/unit?ws={quote(ws)}")
        why = settle(probe, Want("board", ws))
        loc = probe.evaluate("location.pathname + location.search")
        ok.append(say(not why and loc == f"/board?ws={quote(ws)}",
                      "R10 /unit without an id is replaced by the Board's address", f"{loc} {why}"))
        goto(probe, base, "/board?ws=nope-not-listed")
        try:
            probe.wait_for_function(
                "() => (document.querySelector('#page-notice') || {}).textContent"
                " && document.querySelector('#page-notice').textContent"
                ".includes('That workspace is not on the list.')", timeout=PAGE_TIMEOUT_MS)
            probe.wait_for_function("() => !location.search.includes('nope-not-listed')",
                                    timeout=PAGE_TIMEOUT_MS)
            held, why = True, ""
        except Exception:  # noqa: BLE001
            held = False
            why = probe.evaluate("location.pathname + location.search")
        ok.append(say(held, "R8 a workspace not on the list says so, and its ws is replaced", why))
        goto(probe, base, f"/unit?ws={quote(ws)}&id={unit}&tab=bogus")
        why = settle(probe, Want("unit", ws, unit))
        loc = probe.evaluate("location.pathname + location.search")
        ok.append(say(not why and "bogus" not in loc,
                      "R8 a tab that does not exist opens Overview, and is replaced", f"{loc} {why}"))

        # R11.
        if dropped:
            # Overview holds the hold panel and the run controls; Questions the answer box.
            goto(probe, base, f"/unit?ws={quote(ws)}&id={dropped}")
            why = settle(probe, Want("unit", ws, dropped))
            present = {sel: probe.locator(sel).count() for sel in (
                # `cos.mjs` `HOLD_MOVES`: a dropped unit's one move is `paused`.
                "#unit-dropped", "#hold-paused")}
            absent = {sel: probe.locator(sel).count() for sel in (
                "#run-step", "#cut-branch", "#integration-panel", "#outcome-panel",
                "#integrate-button", "#outcome-button")}
            probe.get_by_role("tab", name=TAB_LABELS["questions"]).click()
            why = why or settle(probe, Want("unit", ws, dropped, "questions"))
            asked_text = probe.locator("#questions-body").inner_text() if not why else ""
            absent |= {sel: probe.locator(sel).count() for sel in (
                '[id^="post-round-"]', 'button[id^="answer-"]',
                'textarea[aria-label^="Answer to"]')}
            held = (not why and all(present.values()) and not any(absent.values())
                    and "Câu hỏi còn mở" in asked_text)
            ok.append(say(held, "R11 a dropped unit opens read-only: #unit-dropped and its hold move, its "
                                "question shown with no box to answer it, nothing that writes",
                          f"{why} present={present} absent={absent}"))
        else:
            print("SKIP  R11 no dropped unit to open: --url writes nothing")
        probe.close()
        page.close()
    finally:
        context.close()
    return ok


def plain() -> int:
    # As `verify_0071`: a blank `__REFLEX_*` a step inherits would leave `/` a 404.
    for name in [k for k, v in os.environ.items() if k.startswith("__REFLEX") and not v]:
        del os.environ[name]
    config = from_env()
    require_build(config)
    require_free_port(config)
    playwright, browser = require_browser()
    root = Path(tempfile.mkdtemp(prefix="cos-0056-work-")).resolve()
    data_dir = Path(tempfile.mkdtemp(prefix="cos-0056-data-")).resolve()
    outside = Path(tempfile.mkdtemp(prefix="cos-0056-remote-")).resolve()
    try:
        proj, other = make_repo(root, outside, "proj"), make_repo(root, outside, "other")
        token = seed_session(data_dir)
        with RealApp(config, root, data_dir) as app:
            api = httpx.Client(base_url=app.base, timeout=30, cookies={auth.COOKIE: token})
            for name in ("proj", "other"):
                added = api.post("/api/workspaces", json={"name": name})
                if added.status_code != 200:
                    print(f"could not adopt {name}: {added.text}", file=sys.stderr)
                    return EXIT_ENV
            alpha = make_unit(api, str(proj), "alpha", intent("alpha"))
            beta = make_unit(api, str(other), "beta", intent("beta"))
            gone = make_unit(api, str(proj), "gone", intent(
                "gone", "\n## Answers\n"
                + hold.block("dropped", "verify", "2026-09-24", "no longer needed")))
            written: list[str] = []

            def write_late():
                written.append(make_unit(api, str(proj), "late", intent("late")))

            late = (write_late, '[id^="unit-"][id$="_late"]')
            ok = measure(browser, app.base, token, None, "proj", "other",
                         alpha, beta, gone, late)
            api.close()
    finally:
        browser.close()
        playwright.stop()
        for d in (root, data_dir, outside):
            shutil.rmtree(d, ignore_errors=True)
    print(f"\n{sum(ok)}/{len(ok)} lines PASS")
    return EXIT_PASS if ok and all(ok) else EXIT_BROKEN


def against(base: str) -> int:
    password = os.environ.get("COS_PROOF_PASSWORD", "")
    if not password:
        print("--url needs COS_PROOF_PASSWORD, the running app's master password",
              file=sys.stderr)
        return EXIT_ENV
    playwright, browser = require_browser()
    try:
        with httpx.Client(base_url=base, timeout=30) as api:
            if api.post(auth.LOGIN, data={"password": password}).status_code >= 400:
                print(f"{base} refused the password", file=sys.stderr)
                return EXIT_ENV
            rows = api.get("/api/workspaces").json().get("workspaces") or []
            if len(rows) < 2:
                print("--url needs two workspaces to move between", file=sys.stderr)
                return EXIT_ENV
            first, second = rows[0], rows[1]
            cards = [u["name"] for u in (api.get("/api/board", params={"cwd": first["path"]})
                                         .json().get("units") or [])]
            others = [u["name"] for u in (api.get("/api/board", params={"cwd": second["path"]})
                                          .json().get("units") or [])]
        if not cards or not others:
            print("--url needs a unit in each of the first two workspaces", file=sys.stderr)
            return EXIT_ENV
        ok = measure(browser, base.rstrip("/"), None, password, first["name"], second["name"],
                     cards[0], others[0], "", None)
    finally:
        browser.close()
        playwright.stop()
    print(f"\n{sum(ok)}/{len(ok)} lines PASS")
    return EXIT_PASS if ok and all(ok) else EXIT_BROKEN


def main(argv: list[str]) -> int:
    if argv[:1] == ["--url"]:
        if len(argv) != 2:
            print("usage: verify_0056.py [--url <base>]", file=sys.stderr)
            return EXIT_ENV
        return against(argv[1])
    return plain()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
