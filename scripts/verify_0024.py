#!/usr/bin/env python3
"""Proof for the store's `0024_the-page-cannot-run-a-second-review-round`.

The page's run button used to pick its own stage -- "the first required stage with no
artifact" -- so after a review asked for changes it offered `ship`, whose gate is closed,
and nothing else. This drives the page's state through the fix -> review-again loop and
checks, at every step, that the stage the page offers is the stage `cos.mjs next` prints.

    0  every claim held
    1  at least one did not
    2  the environment could not answer: no `node`, `uv` or `git`

**The page's own state, driven through Reflex's own event processor, not a browser**
(`0024` spec, Answers, Câu 3: no new dependency; intent, Answers, Câu 4: the proof must
drive the page). Every step is an event the page would send -- `load`, `choose_workspace`,
`open_unit`, `load_next` (*Ask again*), `set_mode`, `run_step` -- handed to a
`BaseStateEventProcessor` over an in-memory state manager, which is the code path the
running app takes: foreground handlers under the state lock, background ones through a
`StateProxy`, and an event a handler returns (`open_unit` and `run_step` both return
`StudioState.load_next`) chained through the processor's queue rather than called here.
"The stage the page offers" is `StudioState.next_stage` read back from that state after
the queue drains -- nothing in this file computes it. What is not exercised is the
compiled JavaScript and the socket between it and this state; the browser proofs
(`verify_0003.py`) drive those for the page, not for this button. `SERVICE` is the one `coscc/state.py` builds at import, from the
environment set below.

**No session, no quota, no network.** `gh` is a fake first on `PATH`: it reads CI's verdict
from a file beside it (`child_env` passes no other variable through), answers the pull
request's head with the workspace's `HEAD`, and keeps comments in a JSON file. The session
is a fake too: for `impl` it commits a change in the temporary workspace and writes
`impl.md`; for `review` it replies with a fixed `review.md`. Everything lives under one
temporary directory, so `~/.cos` is never opened.

**This does not measure `0024`'s R7** -- a real unit, a real review, pressed on the real
page. `ship.md` records that.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.proof_harness import EXIT_BROKEN, EXIT_ENV, EXIT_PASS, say  # noqa: E402

SLUG = "a-loop-the-proof-invented"
PR_URL = "https://github.com/example/proof/pull/9"

FAKE_GH = r'''#!{python}
"""A fake gh for verify_0024. State lives beside this file."""
import json, os, subprocess, sys
here = os.path.dirname(os.path.abspath(__file__))
argv = sys.argv[1:]
with open(os.path.join(here, "argv.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps(argv) + "\n")
store = os.path.join(here, "comments.json")
comments = json.load(open(store, encoding="utf-8")) if os.path.exists(store) else []
if argv[:2] == ["pr", "checks"]:
    bucket = open(os.path.join(here, "CI"), encoding="utf-8").read().strip()
    print(json.dumps([{{"name": "test", "bucket": bucket}}]))
elif argv[:2] == ["pr", "view"] and "comments" in argv:
    print(json.dumps({{"comments": comments}}))
elif argv[:2] == ["pr", "view"]:
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    print(json.dumps({{"state": "OPEN", "headRefOid": head}}))
elif argv[:2] == ["pr", "comment"] and argv[3:5] == ["--body-file", "-"]:
    url = argv[2] + "#issuecomment-" + str(len(comments) + 1)
    comments.append({{"body": sys.stdin.read(), "url": url}})
    json.dump(comments, open(store, "w", encoding="utf-8"))
    print(url)
else:
    sys.stderr.write("the fake gh does not know: " + " ".join(argv) + "\n")
    sys.exit(2)
'''


def require_environment() -> None:
    for tool in ("node", "uv", "git"):
        if shutil.which(tool) is None:
            print(f"no {tool} on PATH — this proof cannot answer without it")
            raise SystemExit(EXIT_ENV)


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=proof", "-c", "user.email=proof@example.invalid", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout.strip()


def rnd(n: int, sha: str, verdict: str, findings: list[str]) -> str:
    return (
        f"## Round {n}\n\nReviewed: {sha}. Verdict: {verdict}.\n\n"
        "### Findings\n\n" + "\n".join(findings) + "\n\n"
        "### What was not reviewed\n\nnothing the proof did not invent\n"
    )


def header(status: str) -> str:
    return f"# Review: a loop the proof invented\nAuthor: verify_0024. Status: {status}.\n\n"


def rounds_part(text: str) -> str:
    """Everything from the first round on: the part the runner must keep byte for byte."""
    at = text.find("## Round 1")
    return text[at:] if at != -1 else ""


class FakeSession:
    """Counts every session the app starts, and does what the next step would do."""

    def __init__(self) -> None:
        self.calls = 0
        self.reply = None  # set before each press: a callable returning the reply text

    async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
        self.calls += 1
        yield ("chunk", self.reply())
        yield ("done", {"session_id": f"verify-0024-{self.calls}", "cost": {}})


def write_unit(directory: Path, files: dict[str, str]) -> None:
    for name, body in files.items():
        (directory / name).write_text(body, encoding="utf-8")


def accepted(title: str, extra: str = "") -> str:
    return f"# {title}: a loop the proof invented\nAuthor: verify_0024.{extra} Status: accepted.\n"


class Page:
    """One browser tab's `StudioState`, driven the way the running app drives it.

    `fire` hands an event to Reflex's `BaseStateEventProcessor` and waits until its queue
    and every task it started -- chained and background ones included -- are done.
    `read` takes the state lock and reads the page's vars back. Nothing here assigns a
    var or calls a handler's body directly.
    """

    TOKEN = "verify-0024-tab"
    DRAIN = 120.0  # seconds; a press waits on the fake session and on two fake `gh` calls

    def __init__(self, processor, manager, studio, root_cls) -> None:
        self.processor, self.manager = processor, manager
        self.studio, self.root_cls = studio, root_cls

    async def fire(self, handler: str, **payload) -> bool:
        from reflex.event import Event
        from reflex_base.utils.format import format_event_handler

        name = format_event_handler(self.studio.event_handlers[handler])
        return await self._drain(Event(name=name, payload=payload))

    async def arrive_at(self, href: str, sid: str = "verify") -> bool:
        """`0056`. What a browser sends when it lands on `href`: the route's `on_load`,
        `arrive`, with the address and the socket's id in `router_data`. Since `0056` a
        press of a navigation button only returns a redirect, and there is no browser here
        to follow it, so the proof arrives where the button would have sent it."""
        from reflex.event import Event
        from reflex.istate.manager.token import BaseStateToken
        from reflex_base.constants import RouteVar
        from reflex_base.utils.format import format_event_handler

        async with self.manager.modify_state(
            BaseStateToken(ident=self.TOKEN, cls=self.root_cls)
        ) as root:
            if not root.router_data:
                # Else the processor rehydrates first, which needs a registered App.
                root.router_data = {RouteVar.CLIENT_TOKEN: self.TOKEN}
        path = href.partition("?")[0]
        router_data = {RouteVar.PATH: path, RouteVar.ORIGIN: href, RouteVar.SESSION_ID: sid,
                       RouteVar.CLIENT_TOKEN: self.TOKEN,
                       RouteVar.HEADERS: {"origin": "http://verify"}}
        name = format_event_handler(self.studio.event_handlers["arrive"])
        return await self._drain(Event(name=name, payload={}, router_data=router_data))

    async def _drain(self, event) -> bool:
        from coscc import state

        await self.processor.enqueue(self.TOKEN, event)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.DRAIN
        while loop.time() < deadline:
            await asyncio.sleep(0.02)
            # Since `0056` a unit opens over the Board, so `poll_running` asks on until the
            # page leaves it: that one task never ends here, and is not waited for. It is
            # one per tab, and `_POLLING` holds the tab while it runs.
            polling = 1 if self.TOKEN in state._POLLING else 0
            if len(self.processor._tasks) <= polling and self.processor._queue.empty():
                return True
        return False

    async def read(self) -> dict:
        from reflex.istate.manager.token import BaseStateToken

        async with self.manager.modify_state(
            BaseStateToken(ident=self.TOKEN, cls=self.root_cls)
        ) as root:
            s = await root.get_state(self.studio)
            return {
                "cwd": s.cwd, "unit": s.unit_id, "next_stage": s.next_stage,
                "run_said": s.run_said, "notice": s.notice, "error": s.error,
                "running": [r.unit for r in s.running_steps],
            }


async def run(root: Path, fakebin: Path, workspace: Path) -> bool:
    from reflex.istate.manager.memory import StateManagerMemory
    from reflex.state import State
    from reflex_base.event.processor import BaseStateEventProcessor

    from coscc.state import StudioState

    manager = StateManagerMemory()
    processor = BaseStateEventProcessor().configure(state_manager=manager)
    async with processor:
        return await loop_through(Page(processor, manager, StudioState, State),
                                  root, fakebin, workspace)


async def loop_through(page: Page, root: Path, fakebin: Path, workspace: Path) -> bool:
    from coscc import harness
    from coscc.service import Invalid
    from coscc.state import SERVICE

    cwd = str(workspace)
    session = FakeSession()
    SERVICE.sessions = session
    units_root = SERVICE._units_root(cwd)
    ok = True
    ci = fakebin / "CI"

    def tree_of(unit: str) -> Path:
        """Since `0017` a unit's branch and commits live in its own worktree."""
        from coscc import worktrees
        return worktrees.path(cwd, unit, SERVICE.config.data_dir)

    def script_says(unit: str) -> dict:
        # The checkout the app reads for this unit: its worktree (`0017`).
        repo = tree_of(unit) if tree_of(unit).exists() else Path(cwd)
        done = subprocess.run(
            ["node", str(harness.script()), "--root", str(units_root), "next", unit,
             "--repo", str(repo)],
            capture_output=True, text=True, timeout=60, env=harness.child_env(),
        )
        return json.loads(done.stdout) if done.returncode == 0 else {"stage": f"exit {done.returncode}"}

    async def check(label: str, unit: str, want: str, how: str = "open") -> bool:
        """R1: the stage the page offers is what the script prints, and here that is `want`.

        `how` is what a person did to make the page ask: `open` the unit's card, press
        *Ask again* (`ask`), or nothing -- `after` reads what the last press left, which is
        the `load_next` that `run_step` chains when it ends.
        """
        drained = True
        if how == "open":
            # `0056`: the card's redirect, followed. By way of the Board, as a person
            # closes the dialog before opening a card again; arriving twice at the same
            # unit reads nothing the second time.
            drained = (await page.arrive_at(f"/board?ws={workspace.name}")
                       and await page.arrive_at(f"/unit?ws={workspace.name}&id={unit}"))
        elif how == "ask":
            drained = await page.fire("load_next")
        got = await page.read()
        script = script_says(unit)
        return say(
            drained and got["unit"] == unit
            and got["next_stage"] == script.get("stage") == want,
            f"{label} ({how}): the page offers {want or 'nothing'!r}, as cos.mjs next does",
            f"page={got['next_stage']!r}, script={script.get('stage')!r}, "
            f"said={got['run_said']!r}, unit={got['unit']!r}, drained={drained}",
        )

    async def press(stage: str) -> dict:
        """The run button: one `run_step` event on the page, which runs `next_stage`.

        Refuses to press when the page does not offer `stage` -- the button runs whatever
        the page offers, so pressing it then would prove something else.
        """
        got = await page.read()
        if got["next_stage"] != stage:
            return {**got, "pressed": False, "_sessions": 0, "drained": True}
        before = session.calls
        drained = await page.fire("run_step")
        after = await page.read()
        return {**after, "pressed": True, "_sessions": session.calls - before,
                "drained": drained}

    def ran(done: dict, stage: str, outcome: str) -> bool:
        return (done["pressed"] and done["drained"] and not done["running"]
                and done["notice"].startswith(f"{stage} {outcome}"))

    async def make(slug: str, files: dict[str, str]) -> tuple[str, Path]:
        made = await SERVICE.create_unit(cwd, slug, "verify_0024 fixture")
        directory = Path(made["path"])
        write_unit(directory, files)
        return made["unit"], directory

    chain = {
        "intent.md": accepted("Intent", " Type: fix."),
        "spec.md": accepted("Spec"), "plan.md": accepted("Plan"), "impl.md": accepted("Impl"),
        "pr.md": accepted("PR") + f"PR: {PR_URL}\n",
    }

    # --- the loop, on one unit -------------------------------------------------
    sha1 = git(workspace, "rev-parse", "HEAD")
    r1 = rnd(1, sha1, "changes-requested", ["- F1 [open] [high] the thing the proof invented"])
    unit, directory = await make(SLUG, {**chain, "review.md": header("changes-requested") + r1})
    review = directory / "review.md"
    ci.write_text("pass")

    # The page loads and a person picks the workspace, as on the Workspaces screen. Since
    # `0056` both are one arrival at the address the choice redirects to.
    drained = await page.arrive_at(f"/board?ws={workspace.name}")
    got = await page.read()
    ok &= say(drained and got["cwd"] == cwd,
              "the page loaded and chose the temporary workspace through its own handlers",
              f"cwd={got['cwd']!r}, error={got['error']!r}, drained={drained}")

    # b: changes asked, nothing on the branch yet. Opening the card is what asks.
    ok &= await check("b", unit, "impl")

    # R4, on the page: `ship` is not what it offers, so the button cannot run it.
    got = await page.read()
    ok &= say(got["next_stage"] != "ship",
              "R4 the page does not offer ship while cos.mjs asks for a fix",
              f"next_stage={got['next_stage']!r}")
    # R4, under the page: a client that asks for ship anyway is refused by the gate
    # before a session starts. No page control sends this; it is the route's guard.
    before = session.calls
    try:
        async for _ in SERVICE.run_step(cwd, unit, "ship"):
            pass
        refused = False
    except Invalid:
        refused = True
    ok &= say(refused and session.calls == before,
              "R4 running ship while its gate is closed is refused, and no session starts",
              f"refused={refused}, sessions={session.calls - before}")

    fixes = 0

    def impl_reply() -> str:
        nonlocal fixes
        fixes += 1
        wt = tree_of(unit)
        (wt / f"fix{fixes}.txt").write_text(f"fix {fixes}\n", encoding="utf-8")
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", f"fix {fixes}")
        (directory / "impl.md").write_text(
            accepted("Impl") + f"\nfix {fixes}: {git(wt, 'rev-parse', 'HEAD')}\n",
            encoding="utf-8",
        )
        return "impl recorded"

    # A fix is code, and `impl` holds tools only in `autonomous` -- in `manual` the app
    # writes `impl.md` from the reply and the session can commit nothing. Set the way the
    # page's mode control sets it: `StudioState.set_mode`, on the stage the page offers.
    ok &= say(await page.fire("set_mode", value="autonomous")
              and not (await page.read())["error"],
              "the page's mode control set impl to autonomous",
              f"{await page.read()}")
    session.reply = impl_reply
    done = await press("impl")
    ok &= say(ran(done, "impl", "done") and done["_sessions"] == 1,
              "R5 the impl press ran exactly one session and started nothing after it",
              f"notice={done['notice']!r}, error={done['error']!r}, "
              f"sessions={done['_sessions']}, offered={done['next_stage']!r}")

    # d: the fix is on the head; CI runs. What `run_step` chained on its way out already
    # asked: the page is showing that answer now, with nobody pressing anything.
    ci.write_text("pending")
    ok &= await check("d-pending", unit, "", how="ask")
    # R4/R5, on the page: the button with nothing offered starts nothing.
    before = session.calls
    drained = await page.fire("run_step")
    got = await page.read()
    ok &= say(drained and session.calls == before
              and got["notice"] == "There is no next step to run.",
              "R4 pressing run while the page offers nothing starts no session",
              f"notice={got['notice']!r}, sessions={session.calls - before}")
    ci.write_text("fail")
    ok &= await check("d-red", unit, "impl", how="ask")
    done = await press("impl")
    ok &= say(ran(done, "impl", "done") and done["_sessions"] == 1,
              "R5 the second impl press ran exactly one session",
              f"notice={done['notice']!r}, error={done['error']!r}, "
              f"sessions={done['_sessions']}")

    # c: CI green on the fix. The press's own re-ask ran while CI was still red.
    ok &= await check("d-red, after the press", unit, "impl", how="after")
    ci.write_text("pass")
    ok &= await check("c", unit, "review", how="ask")

    # R3, the refusal: a reply that changes round 1 is not written. Since 2026-09-23 a
    # reply carries only its own round and the runner keeps the earlier ones
    # (`coscc/runner.py`, `merge_review`), so dropping round 1 is no longer the way to lose
    # it; rewriting it is.
    sha2 = git(tree_of(unit), "rev-parse", "HEAD")
    r2 = rnd(2, sha2, "pass", [f"- F1 [fixed {sha2}] [high] the thing the proof invented"])
    old = review.read_bytes()
    forged = r1.replace("Verdict: changes-requested", "Verdict: pass")
    session.reply = lambda: header("accepted") + forged + "\n" + r2
    done = await press("review")
    ok &= say(ran(done, "review", "failed") and review.read_bytes() == old
              and done["_sessions"] == 1,
              "R3 a review reply that changes round 1 is refused and review.md is untouched",
              f"notice={done['notice']!r}, same={review.read_bytes() == old}")
    ok &= await check("c, after the refusal", unit, "review", how="after")

    # The review round the button runs: the reply is round 2 alone; the runner writes
    # round 1 back from the file, verbatim, and round 2 after it.
    session.reply = lambda: header("accepted") + r2
    done = await press("review")
    new = review.read_text(encoding="utf-8")
    old_rounds = rounds_part(old.decode("utf-8")).rstrip()
    ok &= say(
        ran(done, "review", "done") and done["_sessions"] == 1
        and new.count("\n## Round ") == old.decode("utf-8").count("\n## Round ") + 1
        and rounds_part(new).encode("utf-8").startswith(old_rounds.encode("utf-8")),
        "R3 the review press adds exactly one round and keeps round 1 byte for byte",
        f"notice={done['notice']!r}, error={done['error']!r}",
    )

    # g: the pass, and nothing moved since -- read off what the review press re-asked,
    # then again by opening the card.
    ok &= await check("g", unit, "ship", how="after")
    ok &= await check("g", unit, "ship")

    # i: code lands after the pass.
    (tree_of(unit) / "late.txt").write_text("after the pass\n", encoding="utf-8")
    git(tree_of(unit), "add", "-A")
    git(tree_of(unit), "commit", "-q", "-m", "after the pass")
    ok &= await check("i", unit, "review", how="ask")

    # --- the other rows, each its own unit -------------------------------------
    first, _ = await make("a-fresh-one", {"intent.md": accepted("Intent", " Type: fix.")})
    ok &= await check("a", first, "spec")

    e_unit, _ = await make("pr-open-ci-red", chain)
    ci.write_text("fail")
    ok &= await check("e (red)", e_unit, "impl")
    ci.write_text("pass")
    ok &= await check("e (green)", e_unit, "review")

    h_unit, _ = await make("closed-long-ago", {
        **chain, "plan.md": accepted("Plan").replace("accepted", "done"),
        "review.md": header("changes-requested") + r1,
    })
    ok &= await check("h", h_unit, "")

    f_unit, _ = await make("out-of-rounds", {**chain, "review.md": header("changes-requested") + r1})
    os.environ["COS_REVIEW_ROUNDS"] = "1"
    try:
        ok &= await check("f", f_unit, "")
        said = (await page.read())["run_said"]
        ok &= say("needs a person" in said, "f the page shows cos.mjs's needs-a-person line",
                  said)
    finally:
        del os.environ["COS_REVIEW_ROUNDS"]

    # R5, overall: every session was one press.
    ok &= say(session.calls == 4, "R5 four presses, four sessions, nothing started on its own",
              f"sessions={session.calls}")
    return bool(ok)


def main() -> int:
    require_environment()
    with tempfile.TemporaryDirectory(prefix="verify-0024-") as d:
        root = Path(d)
        fakebin = root / "fakebin"
        fakebin.mkdir()
        gh = fakebin / "gh"
        gh.write_text(FAKE_GH.format(python=sys.executable), encoding="utf-8")
        gh.chmod(0o755)
        workspace = root / "work" / "proj"
        workspace.mkdir(parents=True)
        # Since `0030` opening a unit's tree onto its branch fetches `origin` first, and a
        # fetch that fails leaves the page no tree to ask about (`0056` review round 1, F4).
        remote = root / "remote.git"
        git(root, "init", "-q", "--bare", "-b", "main", str(remote))
        git(workspace, "init", "-q", "-b", "main")
        git(workspace, "commit", "-q", "--allow-empty", "-m", "the only commit")
        git(workspace, "remote", "add", "origin", str(remote))
        git(workspace, "push", "-q", "-u", "origin", "main")
        git(workspace, "switch", "-q", "-c", f"fix/{SLUG}")
        # Before `coscc` is imported: `coscc/state.py` builds its `SERVICE` from the
        # environment at import, and that must open this directory, not `~/.cos`.
        os.environ["PATH"] = f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}"
        os.environ["COS_DATA_DIR"] = str(root / "data")
        os.environ["COS_WORKING_DIR"] = str(root / "work")
        os.environ["COS_WORKSPACES"] = str(workspace)
        os.environ.pop("COS_REVIEW_ROUNDS", None)
        for name, fallback in (("COS_PORT", "8790"), ("COS_HOST", "127.0.0.1")):
            if not os.environ.get(name, "").strip():
                os.environ[name] = fallback
        print(f"temporary data root: {root}")
        ok = asyncio.run(run(root, fakebin, workspace))
        return EXIT_PASS if ok else EXIT_BROKEN


if __name__ == "__main__":
    raise SystemExit(main())
