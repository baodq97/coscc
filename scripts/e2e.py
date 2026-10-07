#!/usr/bin/env python3
"""The end-to-end cases: the studio as built, in chromium, behind the login.

    npm run e2e
    npm run e2e -- --serve <state.json>   # the same fixture, to look at by hand

It serves `coscc.run:served` on a free loopback port over a temporary working folder and data
root holding two workspaces, `proj` and `other`, each a clone of a bare-directory remote with
one unit. A password nobody types and one session are written into that data root before the
app starts. No session is opened, no quota is spent and nothing leaves the machine.

The studio is built first when `coscc/_studio/` is missing or older than `ui/src`. Each case
is one function named for what it shows and prints `PASS` or `FAIL` for each thing it checks.

Exit codes: 0 every case passed, 1 one did not, 2 the environment is not ready (no studio
build, no chromium). It is not part of `npm test`.
"""

from __future__ import annotations

import io
import json
import shutil
import signal
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from coscc.config import COOKIE
from coscc.http import auth
from scripts.proof_harness import (
    EXIT_BROKEN,
    EXIT_PASS,
    RealApp,
    ensure_studio,
    make_repo,
    require_browser,
    say,
    seed_fixture,
    seed_session,
)

SIZE = {"width": 1440, "height": 900}
TIMEOUT_MS = 20_000
SHELL = "#studio-shell"


def intent(title: str) -> str:
    return (
        f"# Intent: {title}\nAuthor: e2e. Type: feat. Status: accepted.\n\n"
        "## Problem\n\nMột unit để mở trên trang.\n"
    )


def load_fixture(api: httpx.Client, root: Path, data_dir: Path, *repos: Path) -> None:
    """One unit in each workspace, made through the app's own route, then stated as rows."""
    units = []
    for repo in repos:
        api.post("/api/workspaces", json={"name": repo.name}).raise_for_status()
        made = api.post("/api/units", json={"cwd": str(repo), "slug": "alpha", "brief": "e2e"})
        made.raise_for_status()
        (Path(made.json()["path"]) / "intent.md").write_text(intent("alpha"), encoding="utf-8")
        units.append(
            (repo, made.json()["unit"], {"statuses": {"intent.md": "accepted"}, "type": "feat"})
        )
    seed_fixture(root, data_dir, units)


def open_studio(context, base: str, path: str):
    """A page at `path` once the studio's shell is drawn."""
    page = context.new_page()
    page.goto(base + path, wait_until="load", timeout=TIMEOUT_MS)
    page.wait_for_selector(SHELL, timeout=TIMEOUT_MS)
    return page


def a_page_without_a_session_is_sent_to_the_login(browser, base) -> bool:
    context = browser.new_context(viewport=SIZE)
    try:
        page = context.new_page()
        page.goto(base + "/", wait_until="load", timeout=TIMEOUT_MS)
        path, shell = page.evaluate("location.pathname"), page.locator(SHELL).count()
        return say(
            path == auth.LOGIN and not shell,
            "/ with no session lands on the login and draws no studio",
            f"at {path}, shell {shell}",
        )
    finally:
        context.close()


def the_studio_opens_every_screen_at_its_address_and_after_a_reload(context, base) -> bool:
    ok = True
    for path, text in (
        ("/", "cos studio"),
        ("/up-next", "Up next"),
        ("/work/proj", "Alpha"),
        ("/unit/proj/1", "Alpha"),
    ):
        page = open_studio(context, base, path)
        try:
            page.wait_for_selector(f"text={text}", timeout=TIMEOUT_MS)
            page.reload(wait_until="load")
            page.wait_for_selector(SHELL, timeout=TIMEOUT_MS)
            there = page.evaluate("location.pathname")
            ok &= say(there == path, f"{path} shows {text!r} and stays after a reload", there)
        finally:
            page.close()
    return ok


def a_feature_page_is_drawn_by_the_studio(context, base) -> bool:
    """`coscc/features/vault/ui/index.tsx` is built into the studio: its sidebar entry opens it."""
    page = open_studio(context, base, "/feature/vault")
    try:
        page.wait_for_selector("text=Add secret", timeout=TIMEOUT_MS)
        return say(True, "/feature/vault draws the vault page with its Add secret button")
    except Exception as e:  # noqa: BLE001 - reported as the failure it is
        return say(False, "/feature/vault draws the vault page", type(e).__name__)
    finally:
        page.close()


def a_pack_is_turned_off_and_its_default_process_chosen(context, base, api) -> bool:
    """Settings › Each project holds the pack: its select changes what a new unit walks, its
    toggle refuses new units, and the diagram of the chosen process is drawn."""
    cwd = api.get("/api/workspaces").json()["workspaces"][0]["path"]
    page = open_studio(context, base, "/may-do")
    try:
        page.wait_for_selector("text=New units walk")
        page.select_option("select", "coscc-sdlc/short")
        page.wait_for_function(
            "fetch('/api/packs?cwd=' + encodeURIComponent(%r)).then(r => r.json()).then(p => p[0].process === 'coscc-sdlc/short')"
            % cwd
        )
        drawn = page.locator("svg.pd").count()
        got = api.get("/api/packs", params={"cwd": cwd}).json()[0]["process"]
        ok = say(
            got == "coscc-sdlc/short" and drawn >= 1,
            "the select saves the default and the diagram is drawn",
            f"{got}, {drawn}",
        )
        page.locator(".toggle").nth(2).click()
        page.wait_for_timeout(500)
        off = api.get("/api/packs", params={"cwd": cwd}).json()[0]["on"]
        refused = api.post("/api/units", json={"cwd": cwd, "slug": "nope", "brief": "x"})
        ok &= say(
            off is False
            and refused.status_code == 400
            and refused.json().get("code") == "no-process",
            "off refuses a new unit as no-process",
            str(refused.status_code),
        )
        api.post(
            "/api/packs",
            json={"cwd": cwd, "name": "coscc-sdlc", "on": True, "process": "coscc-sdlc/full"},
        )
        return ok
    finally:
        page.close()


def a_person_builds_an_agent_and_a_process(context, base, api) -> bool:
    """New agent from `impl`; a process on it, refused for want of a review, then fixed and saved."""
    cwd = api.get("/api/workspaces").json()["workspaces"][0]["path"]
    page = open_studio(context, base, "/agents")
    try:
        page.get_by_role("button", name="New agent").click()
        page.get_by_label("Name").fill("Tidy")
        page.select_option("select", "impl")
        page.get_by_role("button", name="Create agent").click()
        page.wait_for_url("**/agents/tidy")
        page.wait_for_selector("text=tidy")
        made = next(
            r
            for r in api.get("/api/agents", params={"cwd": cwd}).json()["rows"]
            if r["key"] == "tidy"
        )
        ok = say(
            made.get("own") is True and made.get("pack") == "local",
            "the new agent is a whole row of the owner's own pack",
        )

        page.goto(base + "/may-do", wait_until="load")
        page.wait_for_selector("text=New units walk")
        mine = page.locator(".pack-card").filter(has=page.get_by_text("Yours", exact=True)).first
        mine.get_by_role("button", name="New process").click()
        page.get_by_label("Process name").fill("tiny")
        for what in ("agent:intent", "agent:tidy", "action:open-pr", "action:merge"):
            page.get_by_label("Add a step").select_option(what)
        page.get_by_label("Step name").nth(1).fill("impl")
        page.get_by_label("Step name").nth(1).blur()
        page.get_by_role("button", name="Save process").click()
        page.wait_for_selector("text=a review state is not on every path")
        refused = page.locator(".pe-step.bad").count()
        ok &= say(
            refused >= 1, "a save with no review is refused beside the step it names", str(refused)
        )
        page.get_by_label("Add a step").select_option("agent:review")
        page.get_by_label("Move up").last.click()
        page.get_by_role("button", name="+ Add a way on").nth(3).click()
        page.get_by_label("When").last.select_option("field")
        page.get_by_label("Field").last.select_option("verdict")
        page.get_by_label("Value").last.select_option("changes-requested")
        page.get_by_label("Goes to").last.select_option("impl")
        page.get_by_role("button", name="Save process").click()
        page.wait_for_selector(".pack-editor", state="detached")
        refs = [
            p["ref"] for p in api.get("/api/packs", params={"cwd": cwd}).json()[-1]["processes"]
        ]
        ok &= say("local/tiny" in refs, "the saved process is the owner's own", str(refs))
        return ok
    finally:
        page.close()


def a_pack_is_exported_and_imported(context, base, api) -> bool:
    """The owner's pack exported as a zip; the same zip refused as imported (its keys are taken), a
    zip with a path out of its folder refused, a new pack accepted and left off. Runs after the
    case that builds the agent and the process."""
    cwd = api.get("/api/workspaces").json()["workspaces"][0]["path"]
    page = open_studio(context, base, "/may-do")
    try:
        page.wait_for_selector("text=New units walk")
        mine = page.locator(".pack-card").filter(has=page.get_by_text("Yours", exact=True)).first
        ok = True
        with page.expect_download() as got:
            mine.get_by_role("link", name="Export").click()
        data = api.get("/api/packs/local/export", params={"cwd": cwd}).content
        ok &= say(
            got.value.suggested_filename == "local.zip" and zipfile.is_zipfile(io.BytesIO(data)),
            "Export downloads the pack's zip",
            got.value.suggested_filename,
        )

        def upload(blob: bytes):
            page.get_by_role("button", name="Import a pack").first.click()
            page.get_by_label("Pack file").set_input_files(
                {"name": "pack.zip", "mimeType": "application/zip", "buffer": blob}
            )
            page.get_by_role("button", name="Import", exact=True).click()

        upload(data)
        page.wait_for_selector("text=Not imported")
        ok &= say(
            page.locator(".pe-refused li").count() >= 1,
            "a pack whose keys are taken is refused with its reasons",
        )
        page.keyboard.press("Escape")

        bad = io.BytesIO()
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr(
                ".claude-plugin/plugin.json", json.dumps({"name": "extra", "version": "1.0.0"})
            )
            z.writestr("../x.md", "x")
        upload(bad.getvalue())
        page.wait_for_selector("text=Not imported")
        ok &= say(True, "a zip with a path out of its folder is refused")
        page.keyboard.press("Escape")

        row = zipfile.ZipFile(io.BytesIO(data)).read("agents/tidy.md")
        good = io.BytesIO()
        with zipfile.ZipFile(good, "w") as z:
            z.writestr(
                ".claude-plugin/plugin.json", json.dumps({"name": "extra", "version": "1.0.0"})
            )
            z.writestr("agents/tidy-two.md", row.replace(b"Tidy", b"TidyTwo", 1))
        upload(good.getvalue())
        page.locator("text=Added extra").or_(page.locator(".pe-refused")).first.wait_for()
        if page.locator(".pe-refused").count():
            return say(False, "a good pack is imported", page.locator(".pe-refused").inner_text())
        extra = next(
            p for p in api.get("/api/packs", params={"cwd": cwd}).json() if p["name"] == "extra"
        )
        return (
            say(
                extra["on"] is False and extra.get("imported") is True,
                "an imported pack arrives off",
                str(extra["on"]),
            )
            and ok
        )
    finally:
        page.close()


def the_sidebar_is_a_drawer_on_a_phone(browser, base, token) -> bool:
    context = browser.new_context(viewport={"width": 390, "height": 844})
    try:
        context.add_cookies([{"name": COOKIE, "value": token, "url": base}])
        page = open_studio(context, base, "/agents")
        side = page.locator(".side").bounding_box()
        shut = side is not None and side["x"] + side["width"] <= 1
        page.locator(".menu-btn").click()
        page.wait_for_timeout(300)
        side = page.locator(".side").bounding_box()
        opened = side is not None and side["x"] >= 0
        wide = page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        return say(
            shut and opened and wide,
            "at 390 px the sidebar is shut, the menu button opens it, nothing scrolls sideways",
            f"{shut}, {opened}, {wide}",
        )
    finally:
        context.close()


def an_unknown_api_path_is_a_404_not_the_page(api: httpx.Client) -> bool:
    got = api.get("/api/x")
    return say(got.status_code == 404, "an unknown /api/x is a 404", str(got.status_code))


def run(case, *args) -> bool:
    """One case; a case that raises has failed, and the others still run."""
    print(f"\n{case.__name__}")
    try:
        return case(*args)
    except Exception as e:  # noqa: BLE001 - reported as the failure it is
        return say(
            False, "the case ran to its end", f"{type(e).__name__}: {str(e).splitlines()[0]}"
        )


def fixture_app(stack):
    """The app on the fixture; returns `(app, token, root, data_dir, outside)`."""
    root = Path(tempfile.mkdtemp(prefix="cos-e2e-work-")).resolve()
    data_dir = Path(tempfile.mkdtemp(prefix="cos-e2e-data-")).resolve()
    outside = Path(tempfile.mkdtemp(prefix="cos-e2e-remote-")).resolve()
    for d in (root, data_dir, outside):
        stack.callback(shutil.rmtree, d, ignore_errors=True)
    repos = [make_repo(root, outside, n, f"{n}.git", "e2e\n") for n in ("proj", "other")]
    token = seed_session(data_dir)
    app = stack.enter_context(RealApp(root, data_dir))
    api = stack.enter_context(httpx.Client(base_url=app.base, timeout=60, cookies={COOKIE: token}))
    load_fixture(api, root, data_dir, *repos)
    return app, token, api


def main() -> int:
    from contextlib import ExitStack

    ensure_studio()
    serving = sys.argv[1:2] == ["--serve"]
    if serving:
        with ExitStack() as stack:
            app, token, _ = fixture_app(stack)
            cookie = {"name": COOKIE, "value": token, "domain": app.host, "path": "/"}
            Path(sys.argv[2]).write_text(json.dumps({"cookies": [cookie], "origins": []}))
            print(f"serving {app.base}", flush=True)
            signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
            signal.sigwait({signal.SIGINT, signal.SIGTERM})
        return EXIT_PASS
    playwright, browser = require_browser()
    results: list[bool] = []
    try:
        with ExitStack() as stack:
            app, token, api = fixture_app(stack)
            context = browser.new_context(viewport=SIZE)
            context.set_default_timeout(TIMEOUT_MS)
            context.add_cookies([{"name": COOKIE, "value": token, "url": app.base}])
            try:
                results.append(
                    run(a_page_without_a_session_is_sent_to_the_login, browser, app.base)
                )
                results.append(
                    run(
                        the_studio_opens_every_screen_at_its_address_and_after_a_reload,
                        context,
                        app.base,
                    )
                )
                results.append(run(a_feature_page_is_drawn_by_the_studio, context, app.base))
                results.append(
                    run(a_pack_is_turned_off_and_its_default_process_chosen, context, app.base, api)
                )
                results.append(run(a_person_builds_an_agent_and_a_process, context, app.base, api))
                results.append(run(a_pack_is_exported_and_imported, context, app.base, api))
                results.append(run(the_sidebar_is_a_drawer_on_a_phone, browser, app.base, token))
                results.append(run(an_unknown_api_path_is_a_404_not_the_page, api))
            finally:
                context.close()
    finally:
        browser.close()
        playwright.stop()
    print(f"\n{sum(results)}/{len(results)} cases passed")
    return EXIT_PASS if results and all(results) else EXIT_BROKEN


if __name__ == "__main__":
    sys.exit(main())
