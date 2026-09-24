#!/usr/bin/env python3
"""Proof for `0071_an-answer-sent-from-the-board-can-vanish-silently` (`plan.md` step 6).

Every press of *Send this answer* ends in a block under `## Answers` or in a reason the
person reads inside the open unit dialog — never in nothing. Measured in chromium, on the
page as built, at two widths (1280×900 and 390×844, chosen by `spec.md` R2), each on fresh
units:

    1  (a)  a valid answer to question 1 appends one `### Câu 1`, nothing above moves,
            and the dialog says so in view
    2  (b)  a refused answer (no name) writes nothing, keeps the text, and the reason is
            in view in the dialog
    3  (c)  text in question 2's box, Send pressed on question 3: nothing is written, the
            text stays, and the reason names both questions, in view
    4  F<n> a finding the last review round confirmed needs a person is answered into
            `review.md`, and the dialog says so in view
    5  (R7) a refused Pause on the Overview tab is reported in view in the dialog
    6  (R8, R9) closed, the dialog's message is the page's; reopened, it is gone; Dismiss
            inside the dialog clears the page's copy too
    7  the measure can fail: run on the page's own `#page-notice` while the dialog is open,
       it does not pass

"In view" is `spec.md` R2's measure, one function: inside `[role=dialog]`, its box inside
the viewport, and `elementFromPoint` at its centre is it or inside it. Before it is taken
the dialog is scrolled to its bottom, and the claim requires that it did scroll — a sticky
message that passes only because nothing scrolled has measured nothing.

    0  every claim held at both widths
    1  at least one did not
    2  the environment is not ready — no build, stale build, no browser, port in use

No session, no quota: nothing here runs a stage. It needs `COS_PORT` free, for the reason
`scripts/proof_harness.py` gives, and a bundle built for that port; never run it alongside
`verify_0003` or `verify_0006`. Beside a running board:
`COS_PORT=8791 uv run coscc-build && COS_PORT=8791 uv run python scripts/verify_0071.py`.
Since `0070` every route sits behind the login, so this writes a password hash and one
session into the temporary data root before the app starts, and carries that cookie.

**It does not measure the intent's outcome.** That is a person's trial on the real board,
installed from a wheel, before 2026-10-08 (`intent.md ## Answers, câu 4`).
"""

from __future__ import annotations

import hashlib
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

from coscc import auth  # noqa: E402
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

WIDTHS = ((1280, 900), (390, 844))
CLAIMS = 7 * len(WIDTHS)

PAGE_TIMEOUT_MS = 20_000
SETTLE_MS = 800
ANSWER_TIMEOUT_MS = 20_000

GIT_ID = ("-c", "user.name=verify", "-c", "user.email=verify@example.invalid",
          "-c", "commit.gpgsign=false")

# Long enough that the Questions tab overflows the dialog at both widths, so scrolling to
# the bottom moves something and the sticky copy is actually tested.
FILLER = "".join(f"   Dòng giải thích {i} của câu hỏi, để tab đủ dài mà cuộn.\n" for i in range(12))
INTENT = (
    "# Intent: ba câu để trả lời\n"
    "Author: verify_0071. Type: fix. Status: accepted.\n\n"
    "## Problem\n\nMột unit có ba câu hỏi đang mở.\n\n"
    "## Open questions\n\n"
    f"1. **Câu thứ nhất?**\n{FILLER}"
    f"2. **Câu thứ hai?**\n{FILLER}"
    f"3. **Câu thứ ba?**\n{FILLER}"
)
# `coscc/api_test.py`'s `0028` fixture: round 2 confirmed F2 and F3 need a person.
_ROUND = "\n## Round {n}\n\nReviewed: aaaaaaa. Verdict: {v}.\n\n### Findings\n\n{f}\n"
FINDING_FILES = {
    "spec.md": "Status: accepted.\n",
    "plan.md": "Status: accepted.\n",
    "impl.md": "# Impl\nStatus: accepted.\n\n## Needs a person\n\n- F2: no budget\n- F3: no gh\n",
    "pr.md": "PR: https://github.com/o/r/pull/3. Status: accepted.\n",
    "review.md": (
        "# Review: q\nAuthor: t. Status: changes-requested.\n"
        + _ROUND.format(n=1, v="changes-requested", f="- F2 [open] b\n- F3 [open] c")
        + _ROUND.format(n=2, v="needs-person", f="- F2 [needs-person] b\n- F3 [needs-person] c")
    ),
}

# `spec.md` R2, in one place. `inside` is false only for claim 7, which asks whether the
# rest of the measure would still refuse the page's own copy.
IN_VIEW_JS = """
([sel, inside]) => {
  const el = document.querySelector(sel);
  if (!el) return "not on the page";
  const dlg = document.querySelector('[role=dialog]');
  if (inside && !(dlg && dlg.contains(el))) return "not inside the dialog";
  const r = el.getBoundingClientRect();
  if (r.width === 0 || r.height === 0) return "it has no size";
  if (r.top < 0 || r.left < 0 || r.bottom > innerHeight + 0.5 || r.right > innerWidth + 0.5)
    return `outside the viewport: ${Math.round(r.left)},${Math.round(r.top)} to `
      + `${Math.round(r.right)},${Math.round(r.bottom)} in ${innerWidth}x${innerHeight}`;
  const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  if (!hit || !(hit === el || el.contains(hit)))
    return "covered by " + (hit ? hit.tagName + (hit.id ? "#" + hit.id : "")
      + (hit.className && typeof hit.className === "string" ? "." + hit.className.split(" ")[0] : "") : "nothing");
  return "";
}
"""

TO_BOTTOM_JS = """
() => { const d = document.querySelector('[role=dialog]');
        if (!d) return -1; d.scrollTop = d.scrollHeight; return d.scrollTop; }
"""


def git(where: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(where), *GIT_ID, *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_repo(root: Path, outside: Path) -> Path:
    """A workspace: a clone of a bare-directory remote, one commit on `main`."""
    remote = outside / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    proj = root / "proj"
    subprocess.run(["git", "clone", "-q", str(remote), str(proj)], check=True, capture_output=True)
    git(proj, "symbolic-ref", "HEAD", "refs/heads/main")
    (proj / "README.md").write_text("verify_0071\n", encoding="utf-8")
    git(proj, "add", "-A")
    git(proj, "commit", "-q", "-m", "a repository")
    git(proj, "push", "-q", "origin", "main")
    return proj


def seed_session(data_dir: Path) -> str:
    """`0070`: a password (never typed — nobody logs in with it) and one live session."""
    data = Data(data_dir)
    now = int(time.time())
    data.auth_set_password(argon2.PasswordHasher().hash(secrets.token_urlsafe(24)), now)
    token = secrets.token_urlsafe(32)
    data.auth_session_add(auth._sha(token), now, now + auth.SESSION_TTL)
    return token


def make_unit(api: httpx.Client, cwd: str, slug: str, files: dict[str, str]) -> tuple[str, Path]:
    made = api.post("/api/units", json={"cwd": cwd, "slug": slug, "brief": "verify_0071"})
    if made.status_code != 200:
        raise SystemExit(f"could not make a unit: {made.text}")
    body = made.json()
    path = Path(body["path"])
    for name, text in files.items():
        (path / name).write_text(text, encoding="utf-8")
    return str(body["unit"]), path


# --------------------------------------------------------------------------
# the page
# --------------------------------------------------------------------------


def open_board(browser, base: str, token: str, size: tuple[int, int]):
    context = browser.new_context(viewport={"width": size[0], "height": size[1]})
    context.add_cookies([{"name": auth.COOKIE, "value": token, "url": base}])
    page = context.new_page()
    page.goto(base, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    page.wait_for_selector("#studio-shell", timeout=PAGE_TIMEOUT_MS)
    page.wait_for_function(
        "() => { const n = document.querySelector('#workspace-count');"
        " return n && !n.textContent.trim().startsWith('0'); }",
        timeout=PAGE_TIMEOUT_MS,
    )
    if page.locator("#nav-board").is_visible():
        page.click("#nav-board")
    else:
        # Below `lg` the sidebar is a menu in its own dialog.
        page.click("#mobile-navigation")
        page.click("#mobile-nav-board")
        page.wait_for_selector("[role=dialog]", state="detached", timeout=PAGE_TIMEOUT_MS)
    page.wait_for_timeout(SETTLE_MS)
    return context, page


def open_unit(page, unit: str, tab: str | None) -> None:
    page.locator(f"#unit-{unit}").click()
    page.wait_for_selector("[role=dialog]", timeout=PAGE_TIMEOUT_MS)
    page.wait_for_timeout(SETTLE_MS)
    if tab:
        page.get_by_role("tab", name=tab).click()
        page.wait_for_timeout(SETTLE_MS)


def close_dialog(page) -> None:
    page.keyboard.press("Escape")
    page.wait_for_selector("[role=dialog]", state="detached", timeout=PAGE_TIMEOUT_MS)
    page.wait_for_timeout(SETTLE_MS // 2)


def by_id(key: str) -> str:
    return f'[id="{key}"]'


def box(page, key: str):
    return page.get_by_label(f"Answer to {key}", exact=True)


def type_in(page, locator, text: str) -> None:
    locator.fill(text)
    page.wait_for_timeout(300)


def press(page, key: str) -> None:
    page.evaluate(TO_BOTTOM_JS)
    page.locator(by_id(f"answer-{key}")).click()


def wait_text(page, sel: str, *parts: str) -> str:
    """What `sel` says once it contains every part, or whatever it says at the timeout."""
    try:
        page.wait_for_function(
            "([sel, parts]) => { const n = document.querySelector(sel);"
            " return n && parts.every(p => n.innerText.includes(p)); }",
            arg=[sel, list(parts)], timeout=ANSWER_TIMEOUT_MS,
        )
    except Exception:
        pass
    n = page.locator(sel)
    return n.inner_text() if n.count() else ""


def in_view(page, sel: str, inside: bool = True) -> str:
    """`spec.md` R2 after scrolling the dialog to its bottom; `""` when it holds."""
    scrolled = page.evaluate(TO_BOTTOM_JS)
    page.wait_for_timeout(150)
    why = page.evaluate(IN_VIEW_JS, [sel, inside])
    if not why and inside and scrolled <= 0:
        return "the dialog did not scroll, so the sticky copy was not tested"
    return why


# --------------------------------------------------------------------------
# the claims
# --------------------------------------------------------------------------


def one_width(browser, base, token, api, cwd, size) -> list[bool]:
    w = f"{size[0]}px"
    results: list[bool] = []
    answers, a_dir = make_unit(api, cwd, f"answers-{size[0]}", {"intent.md": INTENT})
    finding, f_dir = make_unit(api, cwd, f"finding-{size[0]}", FINDING_FILES)
    intent, review = a_dir / "intent.md", f_dir / "review.md"
    context, page = open_board(browser, base, token, size)
    try:
        open_unit(page, answers, "Questions")
        page.locator("#answer-by").fill("verify_0071")
        page.wait_for_timeout(300)

        # 1 (a)
        before = intent.read_bytes()
        type_in(page, box(page, "intent.md#1"), "Trả lời câu một.")
        press(page, "intent.md#1")
        said = wait_text(page, "#detail-notice", "Answered question 1")
        after = intent.read_bytes()
        why = in_view(page, "#detail-notice")
        tail = after[len(before):].decode("utf-8", "replace")
        results.append(say(
            "Answered question 1 of intent.md" in said and after.startswith(before)
            and tail.count("### Câu 1") == 1 and "Trả lời câu một." in tail and not why,
            f"1 [{w}] (a) a valid answer appends one ### Câu 1 and the dialog says so in view",
            f"notice {said[:160]!r}; prefix kept {after.startswith(before)}; "
            f"appended {tail[:120]!r}; in view: {why or 'yes'}",
        ))

        # 2 (b)
        page.locator("#answer-by").fill("")
        page.wait_for_timeout(300)
        before = sha(intent.read_bytes())
        type_in(page, box(page, "intent.md#2"), "Câu hai, nhưng không ký tên.")
        press(page, "intent.md#2")
        said = wait_text(page, "#detail-error", "say who is answering")
        kept = box(page, "intent.md#2").input_value()
        why = in_view(page, "#detail-error")
        results.append(say(
            "say who is answering" in said and sha(intent.read_bytes()) == before
            and kept == "Câu hai, nhưng không ký tên." and not why,
            f"2 [{w}] (b) a refusal writes nothing, keeps the text, and is in view",
            f"error {said[:160]!r}; file unchanged {sha(intent.read_bytes()) == before}; "
            f"box {kept!r}; in view: {why or 'yes'}",
        ))

        # 3 (c)
        press(page, "intent.md#3")
        said = wait_text(page, "#detail-error", "question 3 of intent.md", "question 2 of intent.md")
        kept = box(page, "intent.md#2").input_value()
        why = in_view(page, "#detail-error")
        results.append(say(
            "question 3 of intent.md" in said and "question 2 of intent.md" in said
            and sha(intent.read_bytes()) == before and kept == "Câu hai, nhưng không ký tên."
            and not why,
            f"3 [{w}] (c) Send on question 3 with text in question 2's box names both, in view",
            f"error {said[:200]!r}; file unchanged {sha(intent.read_bytes()) == before}; "
            f"box {kept!r}; in view: {why or 'yes'}",
        ))

        # 5 (R7), on the same unit, before it is closed
        page.get_by_role("tab", name="Overview").click()
        page.wait_for_timeout(SETTLE_MS)
        page.locator("#hold-reason").fill("")
        page.wait_for_timeout(300)
        page.evaluate(TO_BOTTOM_JS)
        page.locator("#hold-paused").click()
        said = wait_text(page, "#detail-notice", "Not changed:")
        why = in_view(page, "#detail-notice")
        r7 = say(
            said.startswith("Not changed:") and not why,
            f"5 [{w}] (R7) a refused Pause on the Overview tab is reported in view",
            f"notice {said[:160]!r}; in view: {why or 'yes'}",
        )

        # 7: the same measure on the page's own copy must not pass
        control = page.evaluate(IN_VIEW_JS, ["#page-notice", False])
        # Refused for what the dialog does to it, not merely for being missing.
        c7 = control.startswith(("covered", "outside"))
        if control == "":
            print("the check could not be made to fail: #page-notice passed the R2 measure "
                  "while the dialog was open")

        # 6 (R8, R9)
        close_dialog(page)
        page_said = page.locator("#page-notice").inner_text() if page.locator("#page-notice").count() else ""
        open_unit(page, answers, None)
        gone = page.locator("#detail-notice").count() == 0
        page.locator("#hold-reason").fill("")
        page.wait_for_timeout(300)
        page.locator("#hold-paused").click()
        wait_text(page, "#detail-notice", "Not changed:")
        page.locator("#detail-messages").get_by_role("button", name="Dismiss message").click()
        page.wait_for_timeout(SETTLE_MS)
        close_dialog(page)
        cleared = page.locator("#page-notice").count() == 0
        r8 = say(
            page_said == said and gone and cleared,
            f"6 [{w}] (R8, R9) closed, the page shows the dialog's message; reopened, it is "
            "gone; Dismiss in the dialog clears the page's copy",
            f"page said {page_said[:120]!r}; gone on reopen {gone}; cleared {cleared}",
        )

        # 4 F<n>
        before = review.read_bytes()
        open_unit(page, finding, "Questions")
        page.locator("#answer-by").fill("verify_0071")
        page.wait_for_timeout(300)
        type_in(page, box(page, "review.md#F2"), "Người quyết: chấp nhận.")
        press(page, "review.md#F2")
        said = wait_text(page, "#detail-notice", "Answered finding F2")
        after = review.read_bytes()
        why = in_view(page, "#detail-notice")
        tail = after[len(before):].decode("utf-8", "replace")
        results.append(say(
            "Answered finding F2 of review.md" in said and after.startswith(before)
            and tail.count("### F2") == 1 and not why,
            f"4 [{w}] F<n> a finding is answered into review.md and the dialog says so in view",
            f"notice {said[:160]!r}; prefix kept {after.startswith(before)}; "
            f"appended {tail[:120]!r}; in view: {why or 'yes'}",
        ))
        results += [r7, r8]
        results.append(say(c7, f"7 [{w}] the measure refuses #page-notice while the dialog is open",
                           control or "it passed"))
    finally:
        context.close()
    return results


def run() -> int:
    # A step the app starts inherits `__REFLEX_*` blank, and `run.py`'s `setdefault` keeps
    # a blank mount flag: no page, `/` a 404 (measured 2026-09-24, as `verify_0070.py
    # --browser` found). Only the blank ones go; one somebody set is kept.
    for name in [k for k, v in os.environ.items() if k.startswith("__REFLEX") and not v]:
        del os.environ[name]
    config = from_env()
    require_build(config)
    require_free_port(config)

    playwright, browser = require_browser()
    root = Path(tempfile.mkdtemp(prefix="cos-0071-work-")).resolve()
    data_dir = Path(tempfile.mkdtemp(prefix="cos-0071-data-")).resolve()
    outside = Path(tempfile.mkdtemp(prefix="cos-0071-remote-")).resolve()
    results: list[bool] = []
    try:
        proj = make_repo(root, outside)
        token = seed_session(data_dir)
        with RealApp(config, root, data_dir) as app:
            api = httpx.Client(base_url=app.base, timeout=30,
                               cookies={auth.COOKIE: token})
            added = api.post("/api/workspaces", json={"name": "proj"})
            if added.status_code != 200:
                print(f"could not adopt the workspace: {added.text}", file=sys.stderr)
                return EXIT_BROKEN
            for size in WIDTHS:
                results += one_width(browser, app.base, token, api, str(proj), size)
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
