#!/usr/bin/env python3
"""Proof for 0001_product-describes-a-state-it-is-not-in (`plan.md` step 9, `spec.md` R12).

Not named `verify_0001.py`: that name belongs to this repository's own
`0001_no-session-management`. That this unit's number collides with it is itself one
instance of the fault it fixes (symptom 4), and the unit keeps its number on purpose.

`intent.md` names four things a person who never read the source must manage on a workspace
whose repository already holds 14 units in its own `.cos/`. This command is those four,
measured through the page and the API the page uses, in six claims:

    1  (R6)      the empty board names the 14, the store it did read, and the repository
    2  (R8)      a 15th directory in the repository is counted after a reload
    3  (R10)     a unit made through the API is numbered 0016, and the repository's
                 `.cos/` is not written to
    4  (R4)      that unit's card sits in the intent column, its badge reading Ready
                 (`0100` R1, R3: the stage columns replaced the lanes)
    5  (R1, R3)  the branch is cut from the remote's main — one commit ahead of the local
                 one — the page names origin/main and that commit, and it tracks nothing
    6  (R2)      with origin unreachable nothing is cut, and the page says so

    0  all six held
    1  at least one did not
    2  the environment is not ready — no build, stale build, no browser, port in use

No session, no quota, no network: the remote is a bare directory. It needs `COS_PORT`
free, for the reason `scripts/proof_harness.py` gives. It gets past the `0070` login with a
seeded session, as `verify_0053` does.

**It does not look at a clone that stayed fresh.** `0014`'s proof cloned anew each run,
which is why it never saw symptoms 1 and 4 (`intent.md`, Constraints). Here the local
`main` is deliberately one commit behind `origin` before the branch is cut.
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argon2  # noqa: E402
import httpx  # noqa: E402

from coscc import auth, units  # noqa: E402
from coscc.config import from_env  # noqa: E402
from coscc.data import Data  # noqa: E402
from scripts.proof_harness import (  # noqa: E402
    EXIT_BROKEN,
    EXIT_PASS,
    RealApp,
    require_browser,
    require_build,
    require_free_port,
    say,
)

CLAIMS = 6
HOST_UNITS = 14

PAGE_TIMEOUT_MS = 20_000
SETTLE_MS = 1_200
# `gitops.FETCH_TIMEOUT` is 20s; a local bare remote answers well inside that, so the page
# is given a little more than the fetch could ever take.
CUT_TIMEOUT_MS = 30_000

GIT_ID = ("-c", "user.name=verify", "-c", "user.email=verify@example.invalid",
          "-c", "commit.gpgsign=false")


def git(where: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(
        ["git", "-C", str(where), *GIT_ID, *args],
        capture_output=True, text=True, check=check,
    ).stdout.strip()


# --------------------------------------------------------------------------
# the scene
# --------------------------------------------------------------------------


def make_scene(root: Path, outside: Path) -> tuple[Path, Path]:
    """A remote, a clone of it with 14 units in its `.cos/`, and `main` one commit behind.

    The remote lives outside the working folder so it is never mistaken for a workspace.
    The clone is made with git directly, because `gitops.check_url` takes only `https://`.
    """
    remote = outside / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    proj = root / "proj"
    subprocess.run(["git", "clone", "-q", str(remote), str(proj)],
                   check=True, capture_output=True)
    git(proj, "symbolic-ref", "HEAD", "refs/heads/main")
    (proj / "README.md").write_text("a repository that used .cos/ before the store\n",
                                    encoding="utf-8")
    for i in range(1, HOST_UNITS + 1):
        d = proj / ".cos" / f"{i:04d}_u{chr(ord('a') + i - 1)}"
        d.mkdir(parents=True)
        (d / "idea.md").write_text(f"# Idea: u{i}\nStatus: accepted.\n", encoding="utf-8")
    git(proj, "add", "-A")
    git(proj, "commit", "-q", "-m", "fourteen units, the old way")
    git(proj, "push", "-q", "origin", "main")

    # A second clone moves the remote on by one commit. `proj` does not fetch it, so its
    # `main` and `origin/main` are both one behind — the state symptom 1 was measured in.
    other = outside / "other"
    subprocess.run(["git", "clone", "-q", str(remote), str(other)],
                   check=True, capture_output=True)
    (other / "NEWS.md").write_text("landed elsewhere\n", encoding="utf-8")
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "a commit proj has not seen")
    git(other, "push", "-q", "origin", "main")
    return remote, proj


def seed_session(data_dir: Path) -> str:
    """`0070`: a password nobody types and one live session, as `verify_0053` seeds them."""
    data = Data(data_dir)
    now = int(time.time())
    data.auth_set_password(argon2.PasswordHasher().hash(secrets.token_urlsafe(24)), now)
    token = secrets.token_urlsafe(32)
    data.auth_session_add(auth._sha(token), now, now + auth.SESSION_TTL)
    return token


def open_page(browser, base: str, token: str):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.add_cookies([{"name": auth.COOKIE, "value": token, "url": base}])
    page = ctx.new_page()
    page.goto(base, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    page.wait_for_selector("#studio-shell", timeout=PAGE_TIMEOUT_MS)
    page.wait_for_function(
        "() => { const n = document.querySelector('#workspace-count');"
        " return n && !n.textContent.trim().startsWith('0'); }",
        timeout=PAGE_TIMEOUT_MS,
    )
    return page


def board(page) -> None:
    page.click("#nav-board")
    page.wait_for_timeout(SETTLE_MS)


def reload_board(page) -> None:
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    page.wait_for_selector("#studio-shell", timeout=PAGE_TIMEOUT_MS)
    page.wait_for_function(
        "() => { const n = document.querySelector('#workspace-count');"
        " return n && !n.textContent.trim().startsWith('0'); }",
        timeout=PAGE_TIMEOUT_MS,
    )
    board(page)


def note(page) -> str:
    n = page.locator("#board-note")
    return n.inner_text() if n.count() else ""


def cut_from_page(page, unit: str, expect: str) -> str:
    """Open the unit, press the button, and return what `#page-notice` says."""
    page.click(f"#unit-{unit}")
    page.wait_for_timeout(SETTLE_MS)
    page.click("#cut-branch")
    try:
        page.wait_for_function(
            "(want) => { const n = document.querySelector('#page-notice');"
            " return n && n.innerText.includes(want); }",
            arg=expect, timeout=CUT_TIMEOUT_MS,
        )
    except Exception:
        pass
    n = page.locator("#page-notice")
    said = n.inner_text() if n.count() else ""
    page.keyboard.press("Escape")
    page.wait_for_timeout(600)
    return said


def write_intent(unit_path: Path, slug: str) -> None:
    (unit_path / "intent.md").write_text(
        f"# Intent: {slug}\nAuthor: verify. Type: fix. Status: accepted.\n", encoding="utf-8"
    )


# --------------------------------------------------------------------------
# the claims
# --------------------------------------------------------------------------


def run() -> int:
    # As `verify_0071`: a blank `__REFLEX_*` a step inherits would leave `/` a 404.
    for name in [k for k, v in os.environ.items() if k.startswith("__REFLEX") and not v]:
        del os.environ[name]
    config = from_env()
    require_build(config)
    require_free_port(config)

    playwright, browser = require_browser()
    root = Path(tempfile.mkdtemp(prefix="cos-sid-work-")).resolve()
    data_dir = Path(tempfile.mkdtemp(prefix="cos-sid-data-")).resolve()
    outside = Path(tempfile.mkdtemp(prefix="cos-sid-remote-")).resolve()
    results: list[bool] = []
    try:
        remote, proj = make_scene(root, outside)
        token = seed_session(data_dir)
        with RealApp(config, root, data_dir) as app:
            api = httpx.Client(base_url=app.base, timeout=CUT_TIMEOUT_MS / 1000,
                               cookies={auth.COOKIE: token})
            added = api.post("/api/workspaces", json={"name": "proj"})
            if added.status_code != 200:
                print(f"could not adopt the workspace: {added.text}", file=sys.stderr)
                return EXIT_BROKEN
            cwd = str(proj)
            store = str(units.root(cwd, data_dir))

            page = open_page(browser, app.base, token)
            board(page)

            # 1 (R6)
            said = note(page)
            results.append(say(
                all(x in said for x in (f"holds {HOST_UNITS} work units", store, str(proj))),
                f"1 the empty board names the {HOST_UNITS} units, the store and the repository",
                said[:300].replace("\n", " "),
            ))

            # 2 (R8)
            (proj / ".cos" / "0015_extra").mkdir()
            reload_board(page)
            said = note(page)
            results.append(say(
                "holds 15 work units" in said, "2 a 15th directory is counted after a reload",
                said[:300].replace("\n", " "),
            ))

            # 3 (R10)
            before = sorted(p.name for p in (proj / ".cos").iterdir())
            made = api.post("/api/units", json={
                "cwd": cwd, "slug": "first-here", "brief": "started by the proof",
            })
            body = made.json() if made.status_code == 200 else {}
            unit = str(body.get("unit") or "")
            after = sorted(p.name for p in (proj / ".cos").iterdir())
            results.append(say(
                unit == "0016_first-here" and before == after,
                "3 the new unit is 0016 and the repository's .cos/ is untouched",
                f"got {unit or made.text!r}; .cos/ changed: {before != after}",
            ))

            # 4 (R4)
            reload_board(page)
            card = page.locator(f'[data-testid="column-intent"] #unit-{unit}')
            placed = card.count()
            ready = card.get_by_text("Ready", exact=True).count() if placed else 0
            results.append(say(
                bool(unit) and placed == 1 and ready == 1,
                "4 its card is in the intent column, and its badge reads Ready",
                f"in intent: {placed}, Ready badges: {ready}",
            ))

            # 5 (R1, R3)
            ahead = git(remote, "rev-parse", "main")
            short = git(remote, "rev-parse", "--short=7", "main")
            behind = git(proj, "rev-parse", "main")
            branch = "fix/first-here"
            if body.get("path"):
                write_intent(Path(body["path"]), "first-here")
            reload_board(page)
            said = cut_from_page(page, unit, short)
            cut = git(proj, "rev-parse", "--verify", "--quiet", branch, check=False)
            merge = git(proj, "config", f"branch.{branch}.merge", check=False)
            results.append(say(
                ahead != behind and "origin/main" in said and short in said
                and cut == ahead and merge == "",
                "5 the branch is cut from origin's main, the page names it, it tracks nothing",
                f"notice {said[:200]!r}; branch at {cut[:7] or 'nothing'}, remote {short}, "
                f"local main {behind[:7]}; upstream {merge!r}",
            ))

            # 6 (R2)
            second = api.post("/api/units", json={
                "cwd": cwd, "slug": "second-here", "brief": "started by the proof",
            }).json()
            unit2 = str(second.get("unit") or "")
            if second.get("path"):
                write_intent(Path(second["path"]), "second-here")
            git(proj, "remote", "set-url", "origin", str(outside / "gone.git"))
            head = git(proj, "rev-parse", "HEAD")
            reload_board(page)
            said = cut_from_page(page, unit2, "no branch was cut")
            exists = git(proj, "branch", "--list", "fix/second-here")
            results.append(say(
                "origin" in said and "no branch was cut" in said
                and git(proj, "rev-parse", "HEAD") == head and exists == "",
                "6 with origin unreachable nothing is cut, and the page says so",
                f"notice {said[:200]!r}; branch listed: {exists!r}",
            ))
            page.close()
            api.close()
    finally:
        browser.close()
        playwright.stop()
        for d in (root, data_dir, outside):
            shutil.rmtree(d, ignore_errors=True)

    passed = sum(results)
    print(f"\n{passed}/{CLAIMS} claims held")
    return EXIT_PASS if passed == CLAIMS and len(results) == CLAIMS else EXIT_BROKEN


if __name__ == "__main__":
    sys.exit(run())
