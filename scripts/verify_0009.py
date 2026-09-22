#!/usr/bin/env python3
"""Exercise the isolated prototype through the built Reflex frontend; no AI calls."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cos_baodo.config import from_env
from verify_0004 import RealApp, _port_free, require_browser, require_build


def exercise(browser, base: str, screenshots: Path | None = None) -> None:
    from playwright.sync_api import expect

    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors: list[str] = []
    business_requests: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "request",
        lambda request: business_requests.append(request.url)
        if "/api/" in request.url else None,
    )
    page.goto(base + "/prototype")
    expect(page.locator("#prototype-shell")).to_be_visible()
    expect(page.get_by_text("Build with intent.", exact=True)).to_be_visible()

    def nav(name: str) -> None:
        page.locator(f"#nav-{name}").click()

    nav("workspaces")
    page.locator("#new-workspace").click()
    page.locator("#workspace-name").fill("Sandbox")
    page.locator("#save-workspace").click()
    expect(page.get_by_role("heading", name="Sandbox", exact=True)).to_be_visible()
    page.get_by_role("button", name="Edit Sandbox", exact=True).click()
    page.locator("#workspace-name").fill("Sandbox renamed")
    page.locator("#save-workspace").click()
    expect(page.get_by_role("heading", name="Sandbox renamed", exact=True)).to_be_visible()
    page.get_by_role("button", name="Open Sandbox renamed", exact=True).click()
    expect(page.get_by_text("A fresh start.", exact=True)).to_be_visible()
    page.locator("#workspace-switcher").select_option("atlas")
    print("PASS 1/5: create, rename and select demo workspaces")

    nav("board")
    page.locator("#work-search").fill("home for")
    expect(page.locator("[data-testid='work-card']")).to_have_count(1)
    page.locator("#unit-COS-014").click()
    expect(page.get_by_role("dialog")).to_be_visible()
    print("PASS 2/5: search board and open work detail")

    page.get_by_role("tab", name="Artifacts", exact=True).click()
    expect(page.get_by_text("Demo artifact", exact=True)).to_be_visible()
    page.get_by_role("tab", name="Timeline", exact=True).click()
    expect(page.get_by_text("Intent accepted by agent", exact=True)).to_be_visible()
    page.get_by_role("tab", name="Overview", exact=True).click()
    page.locator("#run-demo").click()
    expect(page.get_by_text("Simulation complete. No files changed.", exact=True)).to_be_visible()
    page.keyboard.press("Escape")
    page.locator("#work-search").fill("")
    print("PASS 3/5: artifacts, timeline and explicitly simulated live run")

    nav("sessions")
    page.locator("#chat-prompt").fill("Help me explore the navigation")
    page.locator("#send-demo").click()
    expect(page.get_by_text("Help me explore the navigation", exact=True)).to_be_visible()
    expect(page.get_by_text("Simulated reply", exact=True)).to_be_visible()
    page.locator("#session-atlas-2").click()
    expect(page.get_by_text("Help me explore the navigation", exact=True)).to_have_count(0)
    page.locator("#session-atlas-1").click()
    expect(page.get_by_text("Help me explore the navigation", exact=True)).to_be_visible()
    print("PASS 4/5: demo reply and separate session history")

    nav("settings")
    page.get_by_role("button", name="Dark", exact=True).click()
    page.wait_for_function(
        "document.documentElement.classList.contains('dark') || "
        "document.querySelector('.radix-themes')?.classList.contains('dark')"
    )
    page.get_by_role("button", name="Compact", exact=True).click()
    expect(page.locator("#prototype-shell")).to_have_attribute("data-density", "compact")
    page.get_by_role("button", name="Comfortable", exact=True).click()
    page.get_by_role("button", name="Light", exact=True).click()
    page.wait_for_function(
        "!document.documentElement.classList.contains('dark') && "
        "!document.querySelector('.radix-themes')?.classList.contains('dark')"
    )
    print("PASS 5/5: appearance and density controls change the interface")

    for scenario, message in [
        ("empty", "A fresh start."),
        ("loading", "Getting things ready"),
        ("error", "Something didn't connect."),
    ]:
        page.locator("#preview-scenario").select_option(scenario)
        expect(page.get_by_text(message, exact=True)).to_be_visible()
        page.locator("#restore-preview").click()

    page.locator("#open-search").click()
    page.locator("#command-query").fill("board")
    page.get_by_role("button", name="Go to Board", exact=True).click()
    expect(page.get_by_role("heading", name="Work board", exact=True)).to_be_visible()

    for mode in ("Light", "Dark"):
        page.set_viewport_size({"width": 1440, "height": 1000})
        nav("settings")
        page.get_by_role("button", name=mode, exact=True).click()
        for width in (390, 768, 1440):
            page.set_viewport_size({"width": width, "height": 1000})
            for screen in ("overview", "workspaces", "board", "sessions", "activity", "settings"):
                if width < 1024:
                    page.locator("#mobile-navigation").click()
                    page.locator(f"#mobile-nav-{screen}").click()
                else:
                    nav(screen)
                page.wait_for_timeout(120)
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= window.innerWidth + 1"
                ), f"{screen} overflows at {width}px in {mode} mode"
                if screenshots and screen in ("overview", "board") and width in (390, 1440):
                    screenshots.mkdir(parents=True, exist_ok=True)
                    page.screenshot(
                        path=str(screenshots / f"{screen}-{width}-{mode.lower()}.png"),
                        full_page=True,
                    )

    assert not errors, errors
    assert not business_requests, business_requests
    print("PASS: six screens, three widths, two appearances, preview states, no business API calls")
    page.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    config = from_env()
    require_build(config)
    if not _port_free(config.host, config.port):
        print(f"port {config.port} is occupied; do not stop another user's app", file=sys.stderr)
        raise SystemExit(2)
    playwright, browser = require_browser()
    try:
        with tempfile.TemporaryDirectory(prefix="cos-prototype-") as folder:
            with RealApp(config, Path(folder)) as app:
                exercise(browser, app.base, args.screenshots)
    finally:
        browser.close()
        playwright.stop()


if __name__ == "__main__":
    main()
