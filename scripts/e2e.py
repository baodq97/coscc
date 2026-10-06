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

import json
import shutil
import signal
import sys
import tempfile
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
