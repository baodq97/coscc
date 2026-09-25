#!/usr/bin/env python3
"""Screenshots of the app as built from this checkout, for a unit that changes a screen.

`0083_ui-work-ships-without-anyone-looking-at-the-screen` R4 and R5. The `impl` of a unit
that changes a file `.claude/rules/ui-standard.md` lists runs this after its last commit
touching such a file; the `review` of that unit opens every PNG it wrote with `Read`.

    uv run python scripts/capture_screens.py <address>... [--out <dir>]

Each address is a path of the app, starting with `/`, at most six of them. Each is taken
at 1440×900 and 390×844, full page, into `<out>/<address slug>-<W>x<H>.png` (`<out>`
defaults to `.screens/` in this checkout, which git ignores). The PNGs and manifest of the
last run there are removed first and nothing else; a `<out>` that holds files but no
`manifest.json` is refused, since this command did not write it. So is a tree with
uncommitted changes: the screens must be `head`'s. Beside
them `<out>/manifest.json` records `head` (this checkout's `HEAD`, 40 hex), `dirty` (the
tree changed while it ran), the time, the addresses, the sizes, every shot, and `hits`: every place the visible text of a
page matched one of the six patterns of `S3` and `S4` that can be measured (`scan`).
A hit is reported, never an exit code.

The app runs on a temporary data root with one workspace, `proj`, a clone of a bare
directory, and four units in it, always the same, so a spec can name its addresses:

    0001_fresh-intent      an accepted intent, nothing else
    0002_open-question     an intent with one open question nobody answered
    0003_awaiting-ship     every artifact up to a passing review.md; pr.md names
                           github.com/o/r/pull/1
    0004_finished          plan.md: done; its intent has a `## Proposed outcome` whose
                           deadline (2026-09-20) has passed and two open questions
                           (`0082` R19)

and one chat conversation in a temporary `CLAUDE_CONFIG_DIR`, titled `Backlog screen
plan`, whose reply is markdown (`seed_conversation`). Beside each PNG it writes the page's
visible text as `<address slug>-<W>x<H>.txt`.

For example `/board`, `/settings`, or `/unit?ws=proj&id=0002_open-question&tab=questions`
(`tab` is one of `coscc/place.py`'s `TABS`, lowercase; any other value opens `overview`).
The fixture's paths live under `/tmp/`, so a screen that shows the workspace's path today
hits `S3` on every run; say so rather than hide it.

A page is taken full length, except one with a dialog open: the dialog scrolls inside
itself over a fixed backdrop, so a full-page image would cut it at the viewport and show
the page behind it instead. That one is taken as the viewport shows it, and what the
dialog holds below its fold is not in the image (`full_page` in the manifest says which).

It logs in by writing a password hash and one session into that root before the app
starts (as `scripts/verify_0071.py` does), puts a `gh` first on `PATH` that answers
`pr list` with `[]` and refuses the rest, and drops blank `__REFLEX_*` as
`verify_0070 --browser` does. No session, no quota, no network.

**It overwrites `<repo>/.web`.** `coscc.run` always serves `<repo>/.web`, so the bundle
built for this port replaces the one the checkout had (`.cos/0083_*/spike.md ## U2`). When
that one was current for this environment's `COS_HOST`/`COS_PORT` before the run, it is
built again at the end (about 26 s, measured there) and a line says so; a failed rebuild
prints the command to run and does not change the exit code.

    0  every address taken at both sizes
    1  a page did not open: the login page, or no `#studio-shell` in time
    2  the environment is not ready — too many addresses, one not starting with `/`, an
       `<out>` this command did not write, uncommitted changes, the port in use, no build,
       no browser
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = Path(__file__).resolve().parent.parent

# `run.py` points Reflex at `<repo>/.web` whatever the environment says; the build must
# write where the app will read. Set before anything imports Reflex.
from coscc import frontend  # noqa: E402

os.environ[frontend.WEB_WORKDIR_VAR] = str(frontend.web_dir(REPO))

import httpx  # noqa: E402

from coscc import auth, build  # noqa: E402
from coscc.config import from_env  # noqa: E402
from scripts.proof_harness import (  # noqa: E402
    EXIT_BROKEN,
    EXIT_ENV,
    EXIT_PASS,
    RealApp,
    port_free,
    require_browser,
)
from scripts.verify_0071 import make_repo, seed_session  # noqa: E402

HOST, PORT = "127.0.0.1", 18783  # chosen: a port no other proof here uses
SIZES = ((1440, 900), (390, 844))  # the two `verify_0056` and `verify_0071` measured
MAX_ADDRESSES = 6  # 6 × 2 sizes = 12 images, spec R4's ceiling (chosen, not measured)
PAGE_TIMEOUT_MS = 20_000
SETTLE_MS = 1_500  # for the socket to fill the page after `#studio-shell` shows

# `spec.md` R5: what `S3` and `S4` forbid that a pattern can find in visible text.
PATTERNS = (
    ("sha", re.compile(r"\b[0-9a-f]{40}\b")),
    ("uuid", re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")),
    ("epoch", re.compile(r"\b(?:\d{13}|\d{10})\b")),
    ("env", re.compile(r"\b(?:COS|COSCC)_[A-Z0-9_]+")),
    ("path", re.compile(r"(?:/home/|/tmp/|~/)\S+")),
    ("iso-time", re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")),
)

INTENT = "# Intent: {title}\nAuthor: capture_screens. Type: feat. Status: accepted.\n\n## Problem\n\n{problem}\n"
ROUND = "\n## Round 1\n\nReviewed: {sha}. Verdict: pass.\n\n### Findings\n\n### What was not reviewed\n\nNothing.\n"
FIXTURE = {
    "fresh-intent": {"intent.md": INTENT.format(title="fresh intent", problem="Một intent vừa được chấp nhận.")},
    "open-question": {
        "intent.md": INTENT.format(title="open question", problem="Một intent còn một câu hỏi.")
        + "\n## Open questions\n\n1. Có nên làm việc này tuần này không?\n",
    },
    "awaiting-ship": {
        "intent.md": INTENT.format(title="awaiting ship", problem="Một unit đã qua review, chờ ship."),
        "spec.md": "# Spec: awaiting ship\nIntent: intent.md. Author: capture_screens. Status: accepted.\n",
        "plan.md": "# Plan: awaiting ship\nAuthor: capture_screens. Status: accepted.\n",
        "impl.md": "# Impl: awaiting ship\nAuthor: capture_screens. Status: accepted.\n",
        "pr.md": "# PR: awaiting ship\nPR: https://github.com/o/r/pull/1. Author: capture_screens. Status: accepted.\n",
        "review.md": "# Review: awaiting ship\nAuthor: capture_screens. Status: accepted.\n" + ROUND.format(sha="a" * 40),
    },
    # `0082` R19: a deadline already past, so the card carries an outcome badge, and two
    # questions nobody answered, so the Questions tab has something to show read-only.
    "finished": {
        "intent.md": INTENT.format(title="finished", problem="Một unit đã xong.")
        + "\n## Proposed outcome\n\nĐến hết ngày 2026-09-20, việc này đã được đo.\n"
        + "\n## Open questions\n\n1. Có cần đo lại sau một tuần không?\n2. Ai đọc kết quả?\n",
        "spec.md": "# Spec: finished\nAuthor: capture_screens. Status: accepted.\n",
        "plan.md": "# Plan: finished\nAuthor: capture_screens. Status: done.\n",
    },
}

FAKE_GH = "#!/bin/sh\nif [ \"$1\" = pr ] && [ \"$2\" = list ]; then echo '[]'; exit 0; fi\nexit 1\n"


# `0082` R19: one conversation with a title and a markdown reply, written where the SDK
# reads its sessions (`.cos/0082_*/spike.md ## U2`). No session is opened, no quota spent.
CHAT_TITLE = "Backlog screen plan"
CHAT = (
    ("user", "Can the backlog be a table?"),
    ("assistant", [{"type": "text", "text": "Yes. The **plan** is:\n\n- one row per unit\n- *Edit* opens in the row"}]),
)


def seed_conversation(workspace: Path) -> str:
    """One conversation under `CLAUDE_CONFIG_DIR` for `workspace`; returns its session id.
    `CLAUDE_CONFIG_DIR` must already be set, and the app started after this."""
    import uuid

    from claude_agent_sdk._internal import sessions as sdk

    where = sdk._get_projects_dir() / sdk._sanitize_path(sdk._canonicalize_path(str(workspace)))
    where.mkdir(parents=True, exist_ok=True)
    sid, parent, lines = str(uuid.uuid4()), None, []
    for i, (kind, content) in enumerate(CHAT):
        me = str(uuid.uuid4())
        lines.append(json.dumps({
            "type": kind, "uuid": me, "parentUuid": parent, "sessionId": sid,
            "cwd": str(workspace), "timestamp": f"2026-09-24T01:00:0{i}.000Z",
            "isSidechain": False, "userType": "external",
            "message": {"role": kind, "content": content},
        }))
        parent = me
    lines.append(json.dumps({"type": "custom-title", "customTitle": CHAT_TITLE, "sessionId": sid}))
    (where / f"{sid}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sid


def scan(text: str) -> list[tuple[str, str]]:
    """Every place `text` matches one of `PATTERNS`, as `(kind, snippet)`: the match with up
    to 20 characters either side, on one line."""
    hits = []
    for kind, pattern in PATTERNS:
        for m in pattern.finditer(text):
            around = text[max(0, m.start() - 20):m.end() + 20]
            hits.append((kind, " ".join(around.split())))
    return hits


def slug(address: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "-", address).strip("-") or "root"


def git_out(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True).stdout


def with_env(**values: str) -> dict[str, str]:
    return {**os.environ, **values}


def bundle_state(config) -> str:
    return build.check(config, build.web_dir() / "build" / "client")[0]


def run_build(env: dict[str, str]) -> bool:
    """`coscc-build` in a child, so it reads `env` as a fresh process would."""
    return subprocess.run([sys.executable, "-m", "coscc.build"], cwd=REPO, env=env).returncode == 0


def make_fixture(api: httpx.Client, proj: Path) -> None:
    """The four units, numbered 0001–0004 in this order, through the app's own route."""
    for name, files in FIXTURE.items():
        made = api.post("/api/units", json={"cwd": str(proj), "slug": name, "brief": f"The {name.replace('-', ' ')} fixture."})
        if made.status_code != 200:
            raise RuntimeError(f"could not make the unit {name}: {made.text}")
        for file, text in files.items():
            (Path(made.json()["path"]) / file).write_text(text, encoding="utf-8")


def shoot(browser, base: str, token: str, address: str, size: tuple[int, int], out: Path) -> tuple[Path, str, str, bool]:
    """One address at one size: the PNG, the URL it ended on, the visible text, and whether
    the image is the full page. Raises `RuntimeError` when the page is not the app's."""
    context = browser.new_context(viewport={"width": size[0], "height": size[1]})
    context.add_cookies([{"name": auth.COOKIE, "value": token, "url": base}])
    try:
        page = context.new_page()
        page.goto(base + address, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        try:
            page.wait_for_selector("#studio-shell", timeout=PAGE_TIMEOUT_MS)
        except Exception:
            raise RuntimeError(f"{address} at {size[0]}x{size[1]}: no #studio-shell within {PAGE_TIMEOUT_MS // 1000}s, on {page.url}")
        if "/login" in page.url:
            raise RuntimeError(f"{address} at {size[0]}x{size[1]}: landed on the login page, {page.url}")
        page.wait_for_timeout(SETTLE_MS)
        path = out / f"{slug(address)}-{size[0]}x{size[1]}.png"
        full = page.locator("[role=dialog]").count() == 0
        page.screenshot(path=str(path), full_page=full)
        text = page.inner_text("body")
        # `0082`: the visible text beside the image, so a proof can count what the page says.
        path.with_suffix(".txt").write_text(text, encoding="utf-8")
        return path, page.url, text, full
    finally:
        context.close()


def parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("addresses", nargs="+", metavar="address")
    p.add_argument("--out", type=Path, default=REPO / ".screens")
    return p.parse_args(argv)


def run(argv: list[str]) -> int:
    args = parse(argv)
    if len(args.addresses) > MAX_ADDRESSES:
        print(f"{len(args.addresses)} addresses, at most {MAX_ADDRESSES} — {MAX_ADDRESSES * len(SIZES)} images is the ceiling", file=sys.stderr)
        return EXIT_ENV
    bad = [a for a in args.addresses if not a.startswith("/")]
    if bad:
        print(f"an address is a path of the app and starts with /: {', '.join(bad)}", file=sys.stderr)
        return EXIT_ENV
    refused = out_refused(args.out.resolve())
    if refused:
        print(refused, file=sys.stderr)
        return EXIT_ENV
    # Review round 1, F2: a screen taken from uncommitted work may not be the one the pull
    # request carries, and `head` would still name the last commit.
    dirty = git_out("status", "--porcelain").rstrip("\n")
    if dirty:
        print(f"the tree has uncommitted changes; commit them first, so `head` is what was taken:\n{dirty}", file=sys.stderr)
        return EXIT_ENV

    for name in [k for k, v in os.environ.items() if k.startswith("__REFLEX") and not v]:
        del os.environ[name]
    # The bundle this checkout had, and whether it was current for this environment: only
    # a current one is worth the rebuild at the end.
    before_env = {k: os.environ.get(k, "") for k in ("COS_HOST", "COS_PORT")}
    before = from_env()
    restore = bundle_state(before) == build.OK and (before.host, before.port) != (HOST, PORT)

    os.environ["COS_HOST"], os.environ["COS_PORT"] = HOST, str(PORT)
    config = from_env()
    if not port_free(HOST, PORT):
        print(f"{HOST}:{PORT} is already in use — another capture, or something else, holds it", file=sys.stderr)
        return EXIT_ENV

    roots: list[Path] = []
    code = EXIT_ENV
    try:
        if bundle_state(config) != build.OK and not run_build(with_env()):
            print("the bundle for the capture port could not be built", file=sys.stderr)
            return EXIT_ENV
        code = capture(args, config, roots)
        return code
    finally:
        for d in roots:
            shutil.rmtree(d, ignore_errors=True)
        if restore:
            rebuild = with_env(**before_env)
            if run_build(rebuild):
                print(f"restored the bundle for {before.host}:{before.port} in {frontend.web_dir(REPO)}")
            else:
                print(f"could not restore the bundle for {before.host}:{before.port} — run:\n"
                      f"    COS_HOST={before_env['COS_HOST']} COS_PORT={before_env['COS_PORT']} uv run coscc-build", file=sys.stderr)
        else:
            print("no bundle to restore: the checkout had none current for this environment")


def out_refused(out: Path) -> str | None:
    """Why `out` may not be written into, or `None`. Only a directory this command wrote —
    one holding `manifest.json` — or an empty or missing one is: `--out .` must not reach
    the checkout (review round 1, F1)."""
    if out.exists() and not out.is_dir():
        return f"{out} is a file, not a directory"
    if out.is_dir() and any(out.iterdir()) and not (out / "manifest.json").is_file():
        return f"{out} holds files and no manifest.json — not a directory this command wrote; name an empty one"
    return None


def clear_out(out: Path) -> None:
    """Removes what a previous run wrote — its PNGs and manifest — and nothing else."""
    out.mkdir(parents=True, exist_ok=True)
    for old in [*out.glob("*.png"), *out.glob("*.txt"), out / "manifest.json"]:
        old.unlink(missing_ok=True)


def capture(args: argparse.Namespace, config, roots: list[Path]) -> int:
    out = args.out.resolve()
    clear_out(out)

    playwright, browser = require_browser()
    work = Path(tempfile.mkdtemp(prefix="cos-0083-work-")).resolve()
    data_dir = Path(tempfile.mkdtemp(prefix="cos-0083-data-")).resolve()
    outside = Path(tempfile.mkdtemp(prefix="cos-0083-remote-")).resolve()
    roots += [work, data_dir, outside]
    bin_dir = outside / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH, encoding="utf-8")
    (bin_dir / "gh").chmod(0o755)
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    # `RealApp` hands `os.environ` to the app (`scripts/proof_harness.py:131-136`).
    os.environ["CLAUDE_CONFIG_DIR"] = str(outside / "claude")

    shots, hits = [], []
    started = time.monotonic()
    try:
        proj = make_repo(work, outside)
        token = seed_session(data_dir)
        seed_conversation(proj)
        with RealApp(config, work, data_dir) as app:
            with httpx.Client(base_url=app.base, timeout=30, cookies={auth.COOKIE: token}) as api:
                added = api.post("/api/workspaces", json={"name": "proj"})
                if added.status_code != 200:
                    print(f"could not adopt the workspace: {added.text}", file=sys.stderr)
                    return EXIT_BROKEN
                try:
                    make_fixture(api, proj)
                except RuntimeError as e:
                    print(str(e), file=sys.stderr)
                    return EXIT_BROKEN
            for address in args.addresses:
                for size in SIZES:
                    try:
                        path, url, text, full = shoot(browser, app.base, token, address, size, out)
                    except RuntimeError as e:
                        print(str(e), file=sys.stderr)
                        return EXIT_BROKEN
                    where = f"{size[0]}x{size[1]}"
                    shots.append({"address": address, "size": where, "path": str(path.relative_to(REPO) if path.is_relative_to(REPO) else path),
                                  "url": url, "full_page": full})
                    found = scan(text)
                    hits += [{"address": address, "size": where, "kind": k, "snippet": s} for k, s in found]
                    print(f"{where} {address} -> {shots[-1]['path']} ({path.stat().st_size} bytes, {len(found)} hits)")
    finally:
        browser.close()
        playwright.stop()

    manifest = {
        "head": git_out("rev-parse", "HEAD").strip(),
        "dirty": bool(git_out("status", "--porcelain").strip()),
        "taken_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "addresses": args.addresses,
        "sizes": [f"{w}x{h}" for w, h in SIZES],
        "shots": shots,
        "hits": hits,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(shots)} screenshots and manifest.json in {out}, head {manifest['head'][:7]}"
          f"{' (dirty tree)' if manifest['dirty'] else ''}, {len(hits)} hits, {time.monotonic() - started:.1f}s")
    return EXIT_PASS


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
