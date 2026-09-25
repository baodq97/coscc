"""`0082` proof: the board reads like a tool — no name fields, plain words, one state each.

    uv run python scripts/verify_0082.py                       # plain
    COS_HOST=127.0.0.1 COS_PORT=18782 uv run coscc-build && \\
        uv run python scripts/verify_0082.py --browser         # a real page
    uv run python scripts/verify_0082.py --screens .screens/manifest.json

No session, no quota, no network. Plain drives the app in-process over ASGI on a temporary
data root, as `verify_0074` does; needs `node`, `uv` and `git` (missing is exit 2). Each
claim prints PASS or FAIL:

- R3   every route that recorded a typed name, called with none, records `owner`; a name
       sent along is still recorded as sent; an old run-log row reads as before.
- R9   `update_status` in four states: `line` holds no `COS_`, `actions` no button that
       cannot be used.
- R11  a finished unit with open questions is not `answerable`.
- R12  three fixtures give the three `attention_reason`s.
- R18  no line of `.claude/CLAUDE.md` says "typed" about `Answered by`, `stopped_by`, `by`
       or `Recorded by`.
- R21  `git diff origin/main -- .claude/scripts/cos.mjs` is empty.

`--browser` starts `coscc.run` on 127.0.0.1:18782 with `scripts/capture_screens.py`'s
fixture and a seeded login, and drives chromium: R17 (no text outside a card, no two text
boxes of a card overlapping, at 1440×900 and 390×844), R10, R11, R14, R3 and R9 on the page,
and R8 (the consequence sentence beside `#run-step` when the stage is `pr`). It needs the
port free and a bundle built for it.

`--screens <manifest>` checks the manifest `capture_screens.py` wrote: `head` is `HEAD`,
not dirty, the six addresses of unit A, 12 shots, and `hits == []` (R6, R7).

Exit codes: 0 pass, 1 broken, 2 environment not ready.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
EXIT_PASS, EXIT_BROKEN, EXIT_ENV = 0, 1, 2
HOST, PORT = "127.0.0.1", 18782
ADDRESSES = ["/board", "/backlog", "/settings", "/sessions", "/unit?ws=proj&id=0001_fresh-intent",
             "/unit?ws=proj&id=0004_finished&tab=questions"]
# R18: the recorded fields whose name a person used to type.
NAME_FIELDS = ("Answered by", "stopped_by", "`by`", "Recorded by")
VIETNAMESE = re.compile(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", re.I)


def say(line: str) -> None:
    print(line, flush=True)


def claim(ok: bool, text: str, detail: str = "") -> bool:
    say(f"{'PASS' if ok else 'FAIL'}  {text}{': ' + detail if detail and not ok else ''}")
    return ok


class _Sessions:
    def in_flight(self):
        return []

    async def stream(self, *a, **kw):
        yield ("done", {"session_id": "proof", "cost": {"cost_usd": 0.0, "turns": 1}})


class _Updater:
    """Stands in for `Updater.status` with one fixed answer."""

    def __init__(self, status: dict) -> None:
        self._status = status

    def status(self) -> dict:
        return self._status


# R9's four states, as `Updater.status` shapes them (`coscc/updater.py:416-438`).
UPDATE_STATES = {
    "unconfigured": {"shape": "service", "state": "idle", "version": "0.12.0", "commit": "a" * 40,
                     "release": {"state": "up-to-date"},
                     "local": {"state": "unconfigured", "reason": "COS_UPDATE_LOCAL_FROM chưa đặt"}},
    "no working folder": {"shape": "service", "state": "idle", "version": "0.12.0", "commit": "a" * 40,
                          "release": {"state": "up-to-date"},
                          "local": {"state": "unconfigured", "reason": "chưa có working folder"}},
    "nothing newer": {"shape": "service", "state": "idle", "version": "0.12.0", "commit": "a" * 40,
                      "release": {"state": "up-to-date"}, "local": {"state": "idle", "workspace": "proj"}},
    "newer ready": {"shape": "service", "state": "idle", "version": "0.12.0", "commit": "a" * 40,
                    "release": {"state": "ready", "version": "0.13.0"}, "local": {"state": "idle", "workspace": "proj"}},
}


INTENT = "# Intent: x\nAuthor: t. Type: feat. Status: accepted.\n\n## Problem\n\np\n"
QUESTIONS = "\n## Open questions\n\n1. First?\n2. Second?\n"


async def plain(tmp: Path) -> bool:
    import httpx

    from coscc import service as service_mod
    from coscc.api import build
    from coscc.config import Config
    from coscc.journal import Journal

    repo = tmp / "work" / "proj"
    repo.mkdir(parents=True)
    app = build(Config(workspaces=(str(repo),), working_dir=str(tmp / "work"), data_dir=str(tmp / "data")))
    service = app.state.service
    service.sessions = _Sessions()
    cwd = str(repo)
    ok = True
    owner = getattr(service_mod, "OWNER", None)
    ok &= claim(owner == "owner", "R3: service.OWNER is the fixed word 'owner'", repr(owner))
    owner = "owner"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        async def make(slug: str, **files: str) -> str:
            made = (await client.post("/api/units", json={"cwd": cwd, "slug": slug, "brief": "words"})).json()
            for name, text in files.items():
                (Path(made["path"]) / f"{name}.md").write_text(text, encoding="utf-8")
            return made["unit"]

        asked = await make("asked", intent=INTENT + QUESTIONS)
        done = await make("finished", intent=INTENT + QUESTIONS, spec="# s\nStatus: accepted.\n",
                          plan="# p\nStatus: done.\n")
        other = await make("other", intent=INTENT)
        journal = Journal(tmp / "work", tmp / "data")
        key = service._journal_key(cwd)
        store = service._units_root(cwd) / ".cos"

        async def post(path: str, **body):
            return await client.post(path, json={"cwd": cwd, **body})

        # R3, one route at a time, with no name at all.
        r = await post("/api/units/answer", unit=asked, artifact="intent.md", question=1, answer="Yes.")
        text = (store / asked / "intent.md").read_text(encoding="utf-8")
        ok &= claim(r.status_code == 200 and f"Answered by: {owner}" in text,
                    "R3: /api/units/answer with no name writes 'Answered by: owner'", f"{r.status_code} {r.text[:200]}")
        r = await post("/api/units/answer", unit=asked, artifact="intent.md", question=2, answer="No.",
                       answered_by="Someone Else")
        text = (store / asked / "intent.md").read_text(encoding="utf-8")
        ok &= claim(r.status_code == 200 and "Answered by: Someone Else" in text,
                    "R3/C3: a name sent along is still recorded as sent", f"{r.status_code} {r.text[:200]}")
        r = await post("/api/units/outcome", unit=done, result="đạt", measured_by="agent", source="verify_0082")
        text = (store / done / "intent.md").read_text(encoding="utf-8")
        ok &= claim(r.status_code == 200 and f"Recorded by: {owner}" in text,
                    "R3: /api/units/outcome with no recorded_by writes 'Recorded by: owner'", f"{r.status_code} {r.text[:200]}")
        r = await post("/api/units/hold", unit=other, to="paused", reason="waiting for a decision")
        text = (store / other / "intent.md").read_text(encoding="utf-8")
        ok &= claim(r.status_code == 200 and owner in text.split("### Paused", 1)[-1],
                    "R3: /api/units/hold with no by records owner", f"{r.status_code} {r.text[:200]}")
        await post("/api/units/hold", unit=other, to="active", reason="decided")
        r = await post("/api/backlog/estimate", unit=asked, value=3, effort="M", basis="a guess")
        ok &= claim(r.status_code == 200, "R3: /api/backlog/estimate with no by is accepted", f"{r.status_code} {r.text[:200]}")
        r = await post("/api/backlog/relation", unit=asked, other=other, type="liên quan", op="add", reason="same screen")
        ok &= claim(r.status_code == 200, "R3: /api/backlog/relation with no by is accepted", f"{r.status_code} {r.text[:200]}")
        r = await post("/api/backlog/shortlist", units=[asked], reason="first")
        ok &= claim(r.status_code == 200, "R3: /api/backlog/shortlist with no by is accepted", f"{r.status_code} {r.text[:200]}")
        rows = journal.records(key)
        kinds = {row.get("kind"): row.get("by") for row in rows if row.get("kind") in ("estimate-value", "relation", "shortlist", "hold")}
        ok &= claim(all(v == owner for v in kinds.values()) and len(kinds) == 4,
                    "R3: the estimate, relation, shortlist and hold rows say by: owner", str(kinds))
        journal.append({"kind": "estimate-value", "workspace": key, "unit": other, "value": 2, "effort": "S",
                        "effort_source": "person", "similar": [], "basis": "old", "effort_basis": "", "by": "Bao"})
        data = (await client.get("/api/board", params={"cwd": cwd})).json()
        unit = next(u for u in data["units"] if u["name"] == other)
        ok &= claim((unit.get("backlog") or {}).get("by") == "Bao" or "Bao" in json.dumps(data["backlog"]),
                    "R3: an old row with a typed by reads as before", json.dumps(unit.get("backlog"))[:200])

        # R11.
        fin = next(u for u in data["units"] if u["name"] == done)
        ok &= claim(fin.get("answerable") is False and fin.get("next") == "finished",
                    "R11: a finished unit with open questions is not answerable",
                    f"answerable={fin.get('answerable')!r} next={fin.get('next')!r}")
        live = next(u for u in data["units"] if u["name"] == asked)
        ok &= claim(live.get("answerable") is True, "R11: an unfinished unit is answerable", repr(live.get("answerable")))

    # R12, the three kinds on three board units shaped as `board.read` returns them.
    reason = getattr(service_mod, "attention_reason", None)
    fixtures = {
        "Accept intent.md": {"next": "accept intent.md", "stages": [{"stage": "intent", "status": "draft"}]},
        "Changes requested": {"next": "impl", "stages": [{"stage": "intent", "status": "accepted"},
                                                         {"stage": "review", "status": "changes-requested"}]},
        "Needs a person": {"next": "waiting", "stages": [{"stage": "review", "status": "changes-requested"}],
                           "person_findings": [{"id": "F1", "answered": False}]},
    }
    got = {want: (reason(u) if reason else None) for want, u in fixtures.items()}
    ok &= claim(all(k == v for k, v in got.items()), "R12: three fixtures give the three attention reasons", str(got))
    calm = {"next": "spec", "stages": [{"stage": "intent", "status": "accepted"}]}
    ok &= claim(reason is not None and reason(calm) == "", "R12: a unit outside Needs you has no reason")

    # R9.
    for name, status in UPDATE_STATES.items():
        service.updater = _Updater(status)
        out = service.update_status()
        line, actions = str(out.get("line") or ""), list(out.get("actions") or [])
        usable = {"apply-release", "now-release"} if name == "newer ready" else set()
        ok &= claim(line != "" and "COS_" not in line and not VIETNAMESE.search(line) and set(actions) == usable,
                    f"R9: update status '{name}' says '{line}' and offers {actions}",
                    f"line={line!r} actions={actions}")
    return ok


def docs_and_loop() -> bool:
    ok = True
    lines = (REPO / ".claude" / "CLAUDE.md").read_text(encoding="utf-8").splitlines()
    bad = [f"{i}: {line.strip()[:120]}" for i, line in enumerate(lines, 1)
           if "typed" in line and any(f in line for f in NAME_FIELDS)]
    ok &= claim(not bad, "R18: no line of .claude/CLAUDE.md says 'typed' about a recorded name", "; ".join(bad))
    diff = subprocess.run(["git", "-C", str(REPO), "diff", "origin/main", "--", ".claude/scripts/cos.mjs"],
                          capture_output=True, text=True)
    ok &= claim(diff.returncode == 0 and diff.stdout == "", "R21: cos.mjs is unchanged against origin/main",
                diff.stderr.strip() or f"{len(diff.stdout)} bytes of diff")
    return ok


def screens(manifest_path: Path) -> bool:
    try:
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        say(f"cannot read {manifest_path}: {e}")
        raise SystemExit(EXIT_ENV)
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    ok = claim(m.get("head") == head and m.get("dirty") is False, "screens: taken at HEAD on a clean tree",
               f"head {str(m.get('head'))[:7]} vs {head[:7]}, dirty {m.get('dirty')}")
    ok &= claim(m.get("addresses") == ADDRESSES, "screens: the six addresses of unit A", str(m.get("addresses")))
    ok &= claim(len(m.get("shots") or []) == 12, "screens: 12 shots", str(len(m.get("shots") or [])))
    ok &= claim(m.get("hits") == [], "R6, R7: no hit of S3 or S4 on any shot", json.dumps(m.get("hits"))[:400])
    return ok


# R17, measured as `.cos/0082_*/spike.md ## U1` did: every text-bearing leaf of every card.
CARD_JS = """
() => [...document.querySelectorAll('[data-testid=work-card]')].map(card => {
  const box = card.getBoundingClientRect();
  if (!box.width) return null;
  const leaves = [...card.querySelectorAll('*')].filter(e => e.children.length === 0 && e.textContent.trim());
  const rects = leaves.map(e => ({t: e.textContent.trim().slice(0, 30), r: e.getBoundingClientRect()}))
                      .filter(x => x.r.width > 0 && x.r.height > 0);
  const out = rects.filter(x => x.r.left < box.left - 0.5 || x.r.right > box.right + 0.5
                                || x.r.top < box.top - 0.5 || x.r.bottom > box.bottom + 0.5).map(x => x.t);
  const pairs = [];
  for (let i = 0; i < rects.length; i++) for (let j = i + 1; j < rects.length; j++) {
    const a = rects[i].r, b = rects[j].r;
    if (a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5)
      pairs.push(rects[i].t + ' / ' + rects[j].t);
  }
  return {id: card.id, badges: card.querySelectorAll('.rt-Badge').length, out, pairs};
}).filter(Boolean)
"""


def browser() -> bool:
    os.environ["COS_HOST"], os.environ["COS_PORT"] = HOST, str(PORT)
    for name in [k for k, v in os.environ.items() if k.startswith("__REFLEX") and not v]:
        del os.environ[name]
    import httpx

    from coscc import auth, frontend
    from coscc.config import from_env

    os.environ[frontend.WEB_WORKDIR_VAR] = str(frontend.web_dir(REPO))
    from scripts import capture_screens as cap
    from scripts.proof_harness import RealApp, require_browser, require_build, require_free_port

    config = from_env()
    require_free_port(config)
    require_build(config)
    playwright, chrome = require_browser()
    roots = [Path(tempfile.mkdtemp(prefix=f"cos-0082-{n}-")).resolve() for n in ("work", "data", "remote")]
    work, data_dir, outside = roots
    (outside / "bin").mkdir()
    (outside / "bin" / "gh").write_text(cap.FAKE_GH, encoding="utf-8")
    (outside / "bin" / "gh").chmod(0o755)
    os.environ["PATH"] = f"{outside / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}"
    os.environ["CLAUDE_CONFIG_DIR"] = str(outside / "claude")
    ok = True
    try:
        proj = cap.make_repo(work, outside)
        token = cap.seed_session(data_dir)
        cap.seed_conversation(proj)
        with RealApp(config, work, data_dir) as app:
            with httpx.Client(base_url=app.base, timeout=30, cookies={auth.COOKIE: token}) as api:
                api.post("/api/workspaces", json={"name": "proj"})
                cap.make_fixture(api, proj)
                board = api.get("/api/board", params={"cwd": str(proj)}).json()
            backlog_units = list(board["backlog"].get("backlog") or [])

            def page_at(address: str, size=(1440, 900)):
                context = chrome.new_context(viewport={"width": size[0], "height": size[1]})
                context.add_cookies([{"name": auth.COOKIE, "value": token, "url": app.base}])
                page = context.new_page()
                page.goto(app.base + address, wait_until="domcontentloaded")
                page.wait_for_selector("#studio-shell", timeout=20_000)
                page.wait_for_timeout(cap.SETTLE_MS)
                return context, page

            for size in cap.SIZES:
                context, page = page_at("/board", size)
                cards = page.evaluate(CARD_JS)
                context.close()
                worst = max(cards, key=lambda c: c["badges"]) if cards else {}
                say(f"      {size[0]}x{size[1]}: {len(cards)} cards; most badges {worst.get('badges')} on {worst.get('id')}")
                bad = [c for c in cards if c["out"] or c["pairs"]]
                ok &= claim(cards != [] and not bad, f"R17: no card text outside its card or overlapping at {size[0]}x{size[1]}",
                            json.dumps(bad)[:400])
            fin = [c for c in board["units"] if c["name"] == "0004_finished"][0]
            context, page = page_at("/board")
            card = page.locator("#unit-0004_finished").inner_text()
            context.close()
            ok &= claim("outcome" in card.lower() and "waiting on you" not in card and bool(fin.get("outcome_label")),
                        "R17/R11: the 0004 card carries its outcome badge and no 'waiting on you'", card.replace("\n", " | "))

            context, page = page_at("/backlog")
            rows = page.locator("[data-testid=backlog-row]").count()
            body = page.inner_text("body")
            context.close()
            say(f"      /backlog: {rows} rows on the page, {len(backlog_units)} backlog units in /api/board, spec said 4")
            joined = [line for line in body.splitlines() if len(re.findall(r"\b\d{4}_[a-z0-9-]+", line)) > 1 and "," in line]
            ok &= claim(rows == len(backlog_units) and rows > 0 and not joined,
                        "R10: /backlog has one row per backlog unit and no line joins slugs with commas", str(joined)[:200])

            context, page = page_at("/unit?ws=proj&id=0004_finished&tab=questions")
            areas = page.locator("#questions-body textarea").count()
            said = page.locator("#questions-body").inner_text()
            context.close()
            ok &= claim(areas == 0 and "Not answered" in said, "R11: 0004's Questions tab has no textarea", f"{areas} textareas")

            context, page = page_at("/sessions")
            listed = page.locator("[data-testid=session-row]").count()
            body = page.inner_text("body")
            context.close()
            ok &= claim(listed == 1 and "**" not in body and not cap.PATTERNS[1][1].search(body),
                        "R14: /sessions lists one conversation, no raw ** and no UUID", f"{listed} rows")

            named = []
            for address in ADDRESSES:
                context, page = page_at(address)
                named += [f"{address}: {x}" for x in page.evaluate(
                    "() => [...document.querySelectorAll('input,textarea')].map(e => (e.placeholder||'') + ' ' + (e.getAttribute('aria-label')||''))"
                    ".filter(t => /name|tên/i.test(t) && !/short-name-for-the-problem|Name for the work unit/i.test(t))")]
                context.close()
            ok &= claim(not named, "R3: no input asks for a name on the six addresses of unit A", str(named)[:300])

            context, page = page_at("/settings")
            upd = page.locator("#update-panel")
            text = upd.inner_text() if upd.count() else ""
            disabled = upd.locator("button:disabled").count() if upd.count() else -1
            context.close()
            ok &= claim(upd.count() == 1 and disabled == 0 and "COS_" not in text,
                        "R9: /settings' Updates section has no disabled button and no COS_", f"disabled={disabled} {text[:120]!r}")

            context, page = page_at("/unit?ws=proj&id=0001_fresh-intent")
            page.wait_for_selector("#run-step", timeout=20_000)
            near = page.locator("#run-consequence").inner_text() if page.locator("#run-consequence").count() else ""
            context.close()
            ok &= claim(bool(near) and "quota" in near, "R8: one consequence sentence stands beside #run-step", repr(near))
            from coscc import service as service_mod
            ok &= claim("gh" in service_mod.CONSEQUENCE.get("pr", "") and "gh" in service_mod.CONSEQUENCE.get("ship", ""),
                        "R8: the pr and ship sentence names this machine's gh login",
                        str({k: service_mod.CONSEQUENCE.get(k) for k in ("pr", "ship")}))
    finally:
        chrome.close()
        playwright.stop()
        for d in roots:
            shutil.rmtree(d, ignore_errors=True)
    return ok


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--browser", action="store_true")
    p.add_argument("--screens", type=Path)
    args = p.parse_args()
    if args.screens:
        return EXIT_PASS if screens(args.screens) else EXIT_BROKEN
    missing = [tool for tool in ("node", "uv", "git") if shutil.which(tool) is None]
    if missing:
        say(f"missing: {', '.join(missing)}")
        return EXIT_ENV
    if args.browser:
        return EXIT_PASS if browser() else EXIT_BROKEN
    tmp = Path(tempfile.mkdtemp(prefix="cos-0082-")).resolve()
    try:
        ok = asyncio.run(plain(tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ok &= docs_and_loop()
    say("0082: " + ("every claim holds" if ok else "a claim failed"))
    return EXIT_PASS if ok else EXIT_BROKEN


if __name__ == "__main__":
    sys.exit(main())
