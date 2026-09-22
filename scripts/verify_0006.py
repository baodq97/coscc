#!/usr/bin/env python3
"""Proof for .cos/0006_demo-data-and-no-durable-store.

`intent.md` asks for one thing that can come back false: five interaction flows completed
in a browser **against real data**, and all five still right after the app is stopped and
started again. This command is that sentence, executed.

    0  all five flows worked, and all five survived a restart
    1  at least one did not
    2  the environment is not ready — no browser, no build, stale build, port in use

Exit 2 is kept apart from exit 1 for the reason the page proof gives: collapsing them would report
"chromium is not installed" as "the page is broken".

**It spends a little account quota.** Flow 4 sends one short prompt, because a chat flow
that never talks to a model is not the flow. It is one message with a four-word answer, and
it is the only session this creates. Compare `scripts/verify_0004.py`, which makes the same
trade for the same reason.

**It never presses the run button.** `plan.md` Risk 5: a board step in `impl`/`autonomous`
carries a $5 ceiling, and a proof that runs one on every invocation is a proof nobody runs.
Flow 3 reads an artifact and a timeline, which is what `intent.md` asks of it.

It needs `COS_PORT` free, because the compiled bundle hardcodes the address it opens its
WebSocket against. Stop the app before running this.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cos_baodo.config import from_env
from cos_baodo.data import Data
from cos_baodo.store import Store
from scripts.proof_harness import (
    EXIT_BROKEN,
    EXIT_PASS,
    RealApp,
    require_browser,
    require_build,
    require_free_port,
)

FLOWS = 5  # from intent.md. Change it there, not here.

PAGE_TIMEOUT_MS = 20_000
SETTLE_MS = 1_200

# One short prompt with a short answer, sent once.
PROMPT = "Reply with exactly: READY"


class Flow:
    """One of the five. Prints as it goes, so a failure half way still says what worked."""

    def __init__(self, number: int, title: str):
        self.number, self.title, self.ok, self.why = number, title, True, ""

    def check(self, what: str, condition: bool, detail: str = "") -> bool:
        if not condition:
            self.ok = False
            self.why = self.why or f"{what}: {detail}" if detail else what
        print(f"  {'ok  ' if condition else 'FAIL'} {what}" + (
            f" — {detail}" if not condition and detail else ""
        ))
        return condition

    def report(self) -> bool:
        print(f"{'PASS' if self.ok else 'FAIL'}  flow {self.number}: {self.title}"
              + ("" if self.ok else f" — {self.why}"))
        return self.ok


# --------------------------------------------------------------------------
# the scene — a real working folder with a real work unit in it
# --------------------------------------------------------------------------

UNIT = "0001_a-real-unit-for-the-proof"
INTENT = """# Intent: A real unit, written by the proof
Author: scripts/verify_0006.py. Status: accepted.

## Problem

This file exists so the board has something true to read. It is written to a scratch
directory that the proof deletes when it finishes.

## Proposed outcome

The artifact tab shows this text, read off disk, rather than anything invented in the page.
"""


def make_scene(root: Path, data_dir: Path) -> None:
    """One workspace holding one work unit. No clone, so no network.

    The first one is put in the store directly, because the page needs something to be
    looking at before flow 1 can do anything. The second is only a directory on disk —
    flow 1 adopts it *through the page*, which is the part being measured.
    """
    ws = root / "proof-workspace"
    (ws / ".cos" / UNIT).mkdir(parents=True, exist_ok=True)
    (ws / ".cos" / UNIT / "intent.md").write_text(INTENT, encoding="utf-8")
    (root / "second-workspace").mkdir(parents=True, exist_ok=True)
    Store(root, data_dir).add("proof-workspace", label="Written by the proof")


def open_page(browser, base: str):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(base, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    page.wait_for_selector("#studio-shell", timeout=PAGE_TIMEOUT_MS)
    # `on_mount` only runs once the WebSocket is up, so waiting for a value it fills is
    # waiting for the connection — the same reasoning `verify_0003.py` uses.
    page.wait_for_function(
        "() => { const n = document.querySelector('#workspace-count');"
        " return n && !n.textContent.trim().startsWith('0'); }",
        timeout=PAGE_TIMEOUT_MS,
    )
    return page


def goto(page, screen: str) -> None:
    page.click(f"#nav-{screen}")
    page.wait_for_timeout(SETTLE_MS)


# --------------------------------------------------------------------------
# the five flows
# --------------------------------------------------------------------------


def flow_1_workspaces(page, root: Path) -> Flow:
    f = Flow(1, "manage and choose a workspace")
    goto(page, "workspaces")
    f.check("the scene's workspace is listed",
            page.locator("[data-testid=workspace-card]").count() >= 1)

    page.click("#new-workspace")
    page.wait_for_timeout(600)
    page.fill("#workspace-name", "second-workspace")
    page.fill("#workspace-label", "Adopted by the proof")
    page.click("#save-workspace")
    page.wait_for_timeout(SETTLE_MS * 2)
    cards = page.inner_text("#workspace-grid")
    f.check("a second workspace was adopted through the page",
            "second-workspace" in cards, cards[:160].replace("\n", " "))
    f.check("its label is shown", "Adopted by the proof" in cards)

    # A name that may not become a path has to be refused *by the service*, with the
    # service's own words reaching the page unrewritten.
    page.click("#new-workspace")
    page.wait_for_timeout(600)
    page.fill("#workspace-name", "../escape")
    page.click("#save-workspace")
    page.wait_for_timeout(SETTLE_MS)
    refused = page.locator("#workspace-form-error")
    f.check("a traversal name is refused, in the service's words",
            refused.count() > 0 and "invalid workspace name" in refused.inner_text())
    page.keyboard.press("Escape")
    page.wait_for_timeout(600)

    page.select_option("#workspace-switcher", value=str(root / "proof-workspace"))
    page.wait_for_timeout(SETTLE_MS * 2)
    f.check("the switcher moves to the chosen workspace",
            "proof-workspace" in page.inner_text("#studio-shell"))
    return f


def flow_2_board(page) -> Flow:
    f = Flow(2, "find and open a work unit on the board")
    goto(page, "board")
    f.check("the real unit is on the board",
            page.locator(f"#unit-{UNIT}").count() == 1,
            page.inner_text("#studio-shell")[:200].replace("\n", " "))

    page.fill("#work-search", "no-such-unit-anywhere")
    page.wait_for_timeout(SETTLE_MS)
    f.check("a search that matches nothing empties the board",
            page.locator("[data-testid=work-card]").count() == 0)

    page.fill("#work-search", "real unit")
    page.wait_for_timeout(SETTLE_MS)
    f.check("a search that matches finds it",
            page.locator(f"#unit-{UNIT}").count() == 1)

    page.click(f"#unit-{UNIT}")
    page.wait_for_timeout(SETTLE_MS)
    f.check("the drawer opens on that unit", UNIT in page.inner_text("[aria-label='Work detail']"))
    return f


def flow_3_artifact_and_timeline(page) -> Flow:
    f = Flow(3, "read a unit's artifact and its timeline")

    # The drawer opens on Overview, and only the open tab is in the document. `spec.md`
    # R17 lives here, so it is measured before anything switches away from it.
    grants = page.locator("#next-grants")
    f.check("the next step's grant is shown before the button",
            grants.count() > 0 and "tools:" in grants.inner_text(),
            "" if grants.count() else "#next-grants is not on the page")
    run = page.locator("#run-step")
    f.check("the run button says it spends quota",
            run.count() > 0 and "spends quota" in run.inner_text(),
            run.inner_text() if run.count() else "no #run-step")

    page.click("button:has-text('Artifact')")
    page.wait_for_timeout(SETTLE_MS)
    body = page.inner_text("#artifact-body")
    f.check("the artifact is the text on disk, not anything invented",
            "written by the proof" in body, body[:160].replace("\n", " "))

    page.click("button:has-text('Timeline')")
    page.wait_for_timeout(SETTLE_MS)
    timeline = page.inner_text("#timeline-body")
    f.check("the timeline says plainly that nothing has run",
            "No step of this unit has been run" in timeline,
            timeline[:160].replace("\n", " "))
    page.keyboard.press("Escape")
    page.wait_for_timeout(600)
    return f


def flow_4_session(page) -> Flow:
    """The one flow that spends quota. One prompt, one short answer."""
    f = Flow(4, "open a conversation and send a message")
    goto(page, "sessions")
    before = page.locator("[data-testid=session-row]").count()

    page.fill("#chat-prompt", PROMPT)
    page.wait_for_timeout(300)
    page.click("#send-message")
    try:
        page.wait_for_function(
            "() => document.querySelectorAll('[data-testid=chat-message]').length >= 2"
            " && document.querySelectorAll('[data-testid=chat-message]')[1]"
            "      .innerText.trim().length > 4",
            timeout=180_000,
        )
        answered = True
    except Exception:
        answered = False
    log = page.inner_text("#chat-log")
    f.check("the model answered in the page", answered, log[:200].replace("\n", " "))
    f.check("READY came back", "READY" in log, log[:200].replace("\n", " "))

    # The list is refreshed by the handler once the reply is finished. Give it a few
    # tries rather than one fixed wait: a slow last chunk is not the same failure as a
    # list that never refreshes, and only the second one is worth reporting.
    after = before
    for _ in range(10):
        page.wait_for_timeout(1000)
        after = page.locator("[data-testid=session-row]").count()
        if after > before:
            break
    detail = f"{before} before, {after} after"
    if after <= before:
        err = page.locator("#page-error")
        detail += f"; error banner: {err.inner_text()!r}" if err.count() else "; no error banner"
        detail += f"; sending={page.locator('#send-message').get_attribute('data-loading')!r}"
    f.check("the conversation is now in the list", after > before, detail)
    return f


def flow_5_appearance(page, data_dir: Path) -> Flow:
    f = Flow(5, "change an interface preference")
    goto(page, "settings")
    page.click("#density-compact")
    page.wait_for_timeout(SETTLE_MS)
    f.check("the page is in compact density",
            page.get_attribute("#studio-shell", "data-density") == "compact",
            str(page.get_attribute("#studio-shell", "data-density")))
    # Read from the database rather than from the page: `spec.md` R14 is about the store,
    # and a value that only lives in the browser would pass a page-only check.
    stored = Data(data_dir).pref("density")
    f.check("it was written to the database", stored == "compact", repr(stored))
    return f


# --------------------------------------------------------------------------
# after the restart
# --------------------------------------------------------------------------


def survives_restart(page, root: Path) -> Flow:
    f = Flow(FLOWS + 1, "all five are still right after a restart")
    f.check("the preference came back",
            page.get_attribute("#studio-shell", "data-density") == "compact",
            str(page.get_attribute("#studio-shell", "data-density")))

    goto(page, "workspaces")
    cards = page.inner_text("#workspace-grid")
    f.check("the adopted workspace is still listed", "second-workspace" in cards)
    f.check("its label is still there", "Adopted by the proof" in cards)

    page.select_option("#workspace-switcher", value=str(root / "proof-workspace"))
    page.wait_for_timeout(SETTLE_MS * 2)
    goto(page, "board")
    f.check("the board still shows the unit", page.locator(f"#unit-{UNIT}").count() == 1)

    goto(page, "sessions")
    f.check("the conversation is still listed",
            page.locator("[data-testid=session-row]").count() >= 1,
            "the session the SDK stored is not being read back")
    return f


def run() -> int:
    config = from_env()
    require_build(config)
    require_free_port(config)

    playwright, browser = require_browser()
    root = Path(tempfile.mkdtemp(prefix="cos0006-work-"))
    data_dir = Path(tempfile.mkdtemp(prefix="cos0006-data-"))
    flows: list[Flow] = []
    try:
        make_scene(root, data_dir)
        app = RealApp(config, root, data_dir)
        with app:
            page = open_page(browser, app.base)
            flows.append(flow_1_workspaces(page, root))
            flows.append(flow_2_board(page))
            flows.append(flow_3_artifact_and_timeline(page))
            flows.append(flow_4_session(page))
            flows.append(flow_5_appearance(page, data_dir))
            page.close()

        print("\n-- stopped the app; starting it again --\n")
        with RealApp(config, root, data_dir) as again:
            page = open_page(browser, again.base)
            flows.append(survives_restart(page, root))
            page.close()
    finally:
        browser.close()
        playwright.stop()
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(data_dir, ignore_errors=True)

    print()
    passed = len([f for f in flows if f.ok])
    done = [f for f in flows if f.number <= FLOWS]
    print(f"{len([f for f in done if f.ok])}/{FLOWS} flows on real data")
    for f in flows:
        f.report()
    if passed == len(flows):
        print(f"\nPASS — {FLOWS}/{FLOWS} flows work on real data and survive a restart.")
        return EXIT_PASS
    print(f"\nFAIL — {len(flows) - passed} of {len(flows)} did not hold.")
    return EXIT_BROKEN


if __name__ == "__main__":
    sys.exit(run())
