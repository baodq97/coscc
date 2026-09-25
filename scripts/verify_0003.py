#!/usr/bin/env python3
"""Proof for .cos/0003_unproven-page, against the page that replaced it.

Exits 0 only when the page is reachable **and** the check that says so can be made to
fail. An earlier unit closed with three green claims sitting on top of a dead page, so measuring
only the good case is the mistake this command exists to not repeat.

Exit codes are kept apart deliberately (`spec.md` R1):

    0  the page works and the check is capable of failing
    1  the page is broken, or the check could not be made to fail
    2  the environment is not ready — no browser, no build, stale build, port in use

Collapsing 2 into 1 would report "chromium is not installed" as "the page is dead".

It creates no session and sends no prompt, so it spends no account quota (`spec.md` R6),
and it clones nothing, so it needs no network.
"""

from __future__ import annotations

import http.server
import os
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from coscc.config import from_env
from coscc.service import Service
from coscc.sessions import Sessions
from scripts.proof_harness import (
    EXIT_ENV,
    EXIT_PASS,
    RealApp,
    require_browser,
    require_build,
    require_free_port,
    say,
    wait_closed,
)
# This proof's exit 1 means "the page is broken"; the shared module names it for what is
# broken in general.
from scripts.proof_harness import EXIT_BROKEN as EXIT_PAGE

WORKSPACES = 2  # from intent.md. Change it there, not here.

PAGE_TIMEOUT_MS = 15_000  # how long the page gets to show live data before it has failed

# --------------------------------------------------------------------------
# the scene
# --------------------------------------------------------------------------


def make_workspaces(root: Path, config) -> None:
    """Two workspaces, adopted from directories. No clone, so no network."""
    scoped = from_env(
        {
            **os.environ,
            "COS_WORKING_DIR": str(root),
            "COS_DATA_DIR": str(root),
            "COS_WORKSPACES": "",
        }
    )
    service = Service(scoped, Sessions(scoped))
    for i in range(WORKSPACES):
        name = f"project-{i + 1}"
        (root / name).mkdir(parents=True, exist_ok=True)
        service.store.add(name, label=f"Workspace {i + 1}")


class StaticOnly:
    """The broken scene: the real bundle, served with nothing behind it.

    This is the symptom of 2026-09-21 rather than its cause (`spec.md` C2). The page
    renders and opens its WebSocket at the address baked into the bundle, and nobody
    answers — which is what a user saw.
    """

    def __init__(self, directory: Path):
        self.directory = directory

    def __enter__(self):
        d = str(self.directory)

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=d, **kw)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        return False


# --------------------------------------------------------------------------
# the measurement — one function, run against both scenes
# --------------------------------------------------------------------------


def page_renders(browser, url: str) -> str:
    """Return "" when the page itself loaded, or a reason why it did not.

    Claim 2 is only worth anything if the broken page actually rendered. Without this,
    an empty build directory would serve a 404, live data would be absent for a reason
    that has nothing to do with the backend, and the negative control would report
    success while testing nothing — the proof quietly degrading into the same shape of
    lie this unit exists to catch.
    """
    page = browser.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        try:
            # The shell, not a heading. The page was replaced and a heading is the
            # kind of thing a redesign moves; the shell is the thing that either mounted
            # or did not.
            page.wait_for_selector("#studio-shell", timeout=PAGE_TIMEOUT_MS)
        except Exception:
            body = (page.text_content("body") or "")[:200].replace("\n", " ")
            return f"the page did not render; saw: {body!r}"
        return ""
    finally:
        page.close()


def live_data_reaches_the_page(browser, url: str, working_dir: str, count: int) -> str:
    """Return "" when the page shows live backend data, or a reason why it did not.

    Deliberately not a search for Reflex's "Connection Error" text (`spec.md` R4): that
    string is Reflex's to change. These two values only appear if `on_mount` ran, and
    `on_mount` only runs if the WebSocket connected — so their presence *is* the
    connection, observed rather than inferred from a label.

    Both moved onto `#working-dir` and `#workspace-count`. The phrasing of the
    count — "N workspace(s)" — is still the exact string this waits for. The working
    folder sits under *Where the data is*, closed until opened (UI standard S3), so this
    opens it first.
    """
    page = browser.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        try:
            page.locator("#data-roots summary").click(timeout=PAGE_TIMEOUT_MS)
            page.get_by_text(working_dir, exact=False).first.wait_for(
                timeout=PAGE_TIMEOUT_MS
            )
        except Exception:
            return f"the working folder {working_dir} never appeared on the page"
        try:
            page.get_by_text(f"{count} workspace(s)", exact=False).first.wait_for(
                timeout=PAGE_TIMEOUT_MS
            )
        except Exception:
            body = (page.text_content("body") or "")[:200].replace("\n", " ")
            return f"the count of {count} never appeared on the page; saw: {body!r}"
        return ""
    finally:
        page.close()


# --- the craft floor (`spec.md` R22-R25) -------------------------------------
#
# These four are the only measurable part of "the page should be good". They are
# necessary, not sufficient, and `spec.md` C7 says so in as many words: a page can
# pass every one of them and still be unpleasant. They live here rather than in
# `verify_0005.py` because each one needs a real browser — a layout that overflows and a
# colour that fails contrast are both invisible to an HTTP check.

# Phone, tablet, laptop. `spec.md` R24 fixes these three.
WIDTHS = (390, 768, 1280)

# WCAG 2.1 AA for body text. The standard is the source; the number is not ours.
MIN_CONTRAST = 4.5

# Measured on the text that is actually on the page, not on `body`. `body` carries the
# browser's defaults here: Reflex hands `App(style=...)` to components, and the theme's
# colours live on the `.radix-themes` node, so reading `body` measures nothing that anyone
# sees. Found on 2026-09-21 by probing the rendered page, which reported 1.00:1.
_CONTRAST_JS = """
() => {
  const px = (c) => {
    const m = (c || '').match(/[\\d.]+/g);
    return m ? m.slice(0, 3).map(Number) : null;
  };
  const transparent = (c) => !c || /rgba\\(.*,\\s*0\\s*\\)/.test(c) || c === 'transparent';
  const lum = (rgb) => {
    const f = rgb.map((v) => {
      const s = v / 255;
      return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2];
  };
  // Walk up for the first background that is actually painted; `transparent` shows
  // whatever is behind it, and comparing against that would measure nothing.
  const bgOf = (el) => {
    for (let n = el; n; n = n.parentElement) {
      const c = getComputedStyle(n).backgroundColor;
      if (!transparent(c)) return px(c) || [255, 255, 255];
    }
    return [255, 255, 255];
  };
  const ratio = (fg, bg) => {
    const a = lum(fg), b = lum(bg);
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
  };

  let worst = { ratio: 99, text: '', fg: null, bg: null };
  const nodes = document.querySelectorAll('p, h1, h2, h3, h4, h5, h6');
  for (const el of nodes) {
    const text = (el.textContent || '').trim();
    if (!text) continue;
    const box = el.getBoundingClientRect();
    if (box.width < 1 || box.height < 1) continue;
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.opacity === '0') continue;
    const r = ratio(px(s.color) || [0, 0, 0], bgOf(el));
    if (r < worst.ratio) worst = { ratio: r, text: text.slice(0, 40), fg: s.color, bg: null };
  }
  // No text at all is a failure, not a pass by default.
  if (worst.text === '') return { ratio: 0, text: '(no text found on the page)' };
  return worst;
}
"""


def _appearance(page) -> str:
    """Light or dark, read off the document rather than off our own state."""
    return page.evaluate(
        "() => document.documentElement.classList.contains('dark') ? 'dark' :"
        " (document.querySelector('.radix-themes')?.classList.contains('dark') ? 'dark' : 'light')"
    )


def the_page_meets_the_craft_floor(browser, url: str) -> str:
    """Return "" when R22-R25 all hold, or the first reason one did not."""
    page = browser.new_page(viewport={"width": WIDTHS[-1], "height": 900})
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        page.wait_for_selector(".radix-themes", timeout=PAGE_TIMEOUT_MS)

        # R22 — the theme is declared, not inherited from the library's defaults.
        accent = page.get_attribute(".radix-themes", "data-accent-color")
        radius = page.get_attribute(".radix-themes", "data-radius")
        if accent != "iris" or radius != "large":
            return (
                "the theme is not the declared one: "
                f"data-accent-color={accent!r}, data-radius={radius!r}"
            )

        # R24 — nothing pushes the document sideways at any of the three widths.
        overflow = (
            "() => document.documentElement.scrollWidth -"
            " document.documentElement.clientWidth"
        )
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(250)
            over = page.evaluate(overflow)
            # One pixel of slack: sub-pixel rounding is not a layout defect.
            if over > 1:
                return f"the page scrolls sideways at {width}px by {over}px"

        # The scene above is small — two workspaces with short names — so it can sit
        # inside 390px whether or not the layout would contain a wide thing. Measured on
        # 2026-09-21 by deleting the table's scroll container: the check still passed. So
        # the measurement proves it is alive before being trusted, the same way claim 2
        # below does for the page as a whole.
        page.set_viewport_size({"width": WIDTHS[0], "height": 900})
        page.evaluate(
            "() => { const d = document.createElement('div');"
            " d.id = 'overflow-canary'; d.style.width = '3000px'; d.style.height = '1px';"
            " document.body.appendChild(d); }"
        )
        page.wait_for_timeout(200)
        caught = page.evaluate(overflow)
        page.evaluate("() => document.getElementById('overflow-canary')?.remove()")
        if caught <= 1:
            return (
                "the overflow measurement is dead: a 3000px element at "
                f"{WIDTHS[0]}px reported {caught}px of overflow"
            )
        page.set_viewport_size({"width": WIDTHS[-1], "height": 900})
        page.wait_for_timeout(200)

        # R23 — the mode changes from the page, and survives a reload. The page puts the
        # control in the top bar; before that it was on the Settings screen only.
        toggle = page.locator("#color-mode button").first
        if toggle.count() == 0:
            return "the page has no #color-mode control"
        before = _appearance(page)
        toggle.click()
        page.wait_for_timeout(350)
        after = _appearance(page)
        if after == before:
            return f"the colour mode control did not change anything (still {before!r})"

        page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        page.wait_for_selector(".radix-themes", timeout=PAGE_TIMEOUT_MS)
        page.wait_for_timeout(400)
        if _appearance(page) != after:
            return (
                f"the colour mode did not survive a reload: chose {after!r}, "
                f"came back {_appearance(page)!r}"
            )

        # R25 — text is legible in *both* appearances, not just the one we landed in.
        toggle = page.locator("#color-mode button").first
        for _ in range(2):
            measured = page.evaluate(_CONTRAST_JS)
            if measured["ratio"] < MIN_CONTRAST:
                return (
                    f"worst text contrast is {measured['ratio']:.2f}:1 in "
                    f"{_appearance(page)} mode, below {MIN_CONTRAST}:1 — "
                    f"on {measured['text']!r} ({measured.get('fg')})"
                )
            toggle.click()
            page.wait_for_timeout(350)
        return ""
    finally:
        page.close()


def run() -> int:
    config = from_env()
    built = require_build(config)
    require_free_port(config)

    playwright, browser = require_browser()
    root = Path(tempfile.mkdtemp(prefix="cos0003-"))
    results: list[bool] = []
    try:
        make_workspaces(root, config)

        # --- claim 1: the page shows what the backend says ---
        with RealApp(config, root) as app:
            api = httpx.get(f"{app.base}/api/workspaces", timeout=30).json()
            ok = api["count"] == WORKSPACES and api["working_dir"] == str(root)
            results.append(
                say(ok, f"the backend reports {WORKSPACES} workspaces", str(api)[:120])
            )
            reason = live_data_reaches_the_page(
                browser, app.base, api["working_dir"], api["count"]
            )
            results.append(
                say(not reason, "the page shows the backend's working folder and count", reason)
            )

            reason = the_page_meets_the_craft_floor(browser, app.base)
            results.append(
                say(
                    not reason,
                    "the page holds the craft floor: declared theme, three widths, "
                    "colour mode that persists, AA contrast",
                    reason,
                )
            )

        # --- claim 2: the same measurement fails when nothing is behind the page ---
        # The app must be gone first. The bundle points at its address, so a lingering
        # server would let the "broken" page connect and quietly make this claim vacuous.
        if not wait_closed(config.host, config.port):
            print(
                f"{config.host}:{config.port} did not close after the app was stopped; "
                "the broken scene would not be broken.",
                file=sys.stderr,
            )
            raise SystemExit(EXIT_ENV)

        with StaticOnly(built) as static:
            # The page must genuinely render first, or the next claim is measuring a 404.
            not_rendered = page_renders(browser, static.base)
            results.append(
                say(
                    not not_rendered,
                    "the broken scene still renders the real page",
                    not_rendered,
                )
            )
            reason = live_data_reaches_the_page(
                browser, static.base, str(root), WORKSPACES
            )
            results.append(
                say(
                    bool(reason),
                    "the same check FAILS when no backend is reachable",
                    "it passed against a page with no backend — this proof cannot go red",
                )
            )
    finally:
        browser.close()
        playwright.stop()
        import shutil

        shutil.rmtree(root, ignore_errors=True)

    print()
    if all(results):
        print("PASS — the page works, and the check that says so can fail.")
        return EXIT_PASS
    print(f"FAIL — {results.count(False)} of {len(results)} checks did not hold.")
    return EXIT_PAGE


if __name__ == "__main__":
    sys.exit(run())
