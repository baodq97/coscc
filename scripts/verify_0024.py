#!/usr/bin/env python3
"""Proof for the store's `0024_the-page-cannot-run-a-second-review-round`.

The page's run button used to pick its own stage -- "the first required stage with no
artifact" -- so after a review asked for changes it offered `ship`, whose gate is closed,
and nothing else. This drives the page's state through the fix -> review-again loop and
checks, at every step, that the stage the page offers is the stage `cos.mjs next` prints.

    0  every claim held
    1  at least one did not
    2  the environment could not answer: no `node`, `uv` or `git`

**At the level of the page's state, not a browser** (`0024` spec, Answers, Câu 3: no new
dependency). The stage the page offers is `_run_target(await SERVICE.next_step(...))[0]`,
which is exactly what `StudioState.load_next` assigns to `run_stage`, and `next_stage`
hands back. A step is started with `SERVICE.run_step(cwd, unit, stage)`, which is exactly
what the `run_step` handler calls with `self.next_stage`. `SERVICE` is the one
`coscc/state.py` builds at import, from the environment set below.

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


async def run(root: Path, fakebin: Path, workspace: Path) -> bool:
    from coscc import harness
    from coscc.service import Invalid
    from coscc.state import SERVICE, _run_target

    cwd = str(workspace)
    session = FakeSession()
    SERVICE.sessions = session
    units_root = SERVICE._units_root(cwd)
    ok = True
    ci = fakebin / "CI"

    def script_says(unit: str) -> dict:
        done = subprocess.run(
            ["node", str(harness.script()), "--root", str(units_root), "next", unit,
             "--repo", cwd],
            capture_output=True, text=True, timeout=60, env=harness.child_env(),
        )
        return json.loads(done.stdout) if done.returncode == 0 else {"stage": f"exit {done.returncode}"}

    async def page_offers(unit: str) -> tuple[str, str]:
        return _run_target(await SERVICE.next_step(cwd, unit))

    async def check(label: str, unit: str, want: str) -> bool:
        """R1: the page offers what the script prints, and here that is `want`."""
        stage, said = await page_offers(unit)
        script = script_says(unit)
        return say(
            stage == script.get("stage") == want,
            f"{label}: the page offers {want or 'nothing'!r}, as cos.mjs next does",
            f"page={stage!r}, script={script.get('stage')!r}, said={said!r}",
        )

    async def press(unit: str, stage: str) -> dict:
        """What the run button does with `next_stage`: one `run_step`, and R5 around it."""
        before = session.calls
        last: dict = {}
        async for kind, payload in SERVICE.run_step(cwd, unit, stage):
            if kind == "done":
                last = payload
        await asyncio.sleep(0)
        last["_sessions"] = session.calls - before
        return last

    def make(slug: str, files: dict[str, str]) -> tuple[str, Path]:
        made = SERVICE.create_unit(cwd, slug, "verify_0024 fixture")
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
    unit, directory = make(SLUG, {**chain, "review.md": header("changes-requested") + r1})
    review = directory / "review.md"
    ci.write_text("pass")

    # b: changes asked, nothing on the branch yet.
    ok &= await check("b", unit, "impl")

    # R4: a stage the gate closes is refused before any session starts.
    before = session.calls
    try:
        await press(unit, "ship")
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
        (workspace / f"fix{fixes}.txt").write_text(f"fix {fixes}\n", encoding="utf-8")
        git(workspace, "add", "-A")
        git(workspace, "commit", "-q", "-m", f"fix {fixes}")
        (directory / "impl.md").write_text(
            accepted("Impl") + f"\nfix {fixes}: {git(workspace, 'rev-parse', 'HEAD')}\n",
            encoding="utf-8",
        )
        return "impl recorded"

    # A fix is code, and `impl` holds tools only in `autonomous` -- in `manual` the app
    # writes `impl.md` from the reply and the session can commit nothing. Set the way the
    # page's mode control sets it (`StudioState.set_mode` -> `SERVICE.set_mode`).
    await SERVICE.set_mode(cwd, unit, "impl", "autonomous")
    session.reply = impl_reply
    done = await press(unit, "impl")
    ok &= say(done.get("outcome") == "done" and done["_sessions"] == 1,
              "R5 the impl press ran exactly one session and started nothing after it",
              f"{done.get('outcome')}, {done.get('error')}, sessions={done['_sessions']}")

    # d: the fix is on the head; CI runs, then fails.
    ci.write_text("pending")
    ok &= await check("d-pending", unit, "")
    ci.write_text("fail")
    ok &= await check("d-red", unit, "impl")
    done = await press(unit, "impl")
    ok &= say(done.get("outcome") == "done" and done["_sessions"] == 1,
              "R5 the second impl press ran exactly one session", f"{done}")

    # c: CI green on the fix.
    ci.write_text("pass")
    ok &= await check("c", unit, "review")

    # R3, the refusal: a reply that drops round 1 is not written.
    sha2 = git(workspace, "rev-parse", "HEAD")
    r2 = rnd(2, sha2, "pass", [f"- F1 [fixed {sha2}] [high] the thing the proof invented"])
    old = review.read_bytes()
    session.reply = lambda: header("accepted") + r2
    done = await press(unit, "review")
    ok &= say(done.get("outcome") == "failed" and review.read_bytes() == old
              and done["_sessions"] == 1,
              "R3 a review reply that drops round 1 is refused and review.md is untouched",
              f"{done.get('outcome')}, same={review.read_bytes() == old}")
    ok &= await check("c, after the refusal", unit, "review")

    # The review round the button runs: round 1 verbatim, round 2 after it.
    session.reply = lambda: header("accepted") + r1 + "\n" + r2
    done = await press(unit, "review")
    new = review.read_text(encoding="utf-8")
    old_rounds = rounds_part(old.decode("utf-8")).rstrip()
    ok &= say(
        done.get("outcome") == "done" and done["_sessions"] == 1
        and new.count("\n## Round ") == old.decode("utf-8").count("\n## Round ") + 1
        and rounds_part(new).encode("utf-8").startswith(old_rounds.encode("utf-8")),
        "R3 the review press adds exactly one round and keeps round 1 byte for byte",
        f"{done.get('outcome')}, {done.get('error')}",
    )

    # g: the pass, and nothing moved since.
    ok &= await check("g", unit, "ship")

    # i: code lands after the pass.
    (workspace / "late.txt").write_text("after the pass\n", encoding="utf-8")
    git(workspace, "add", "-A")
    git(workspace, "commit", "-q", "-m", "after the pass")
    ok &= await check("i", unit, "review")

    # --- the other rows, each its own unit -------------------------------------
    first, _ = make("a-fresh-one", {"intent.md": accepted("Intent", " Type: fix.")})
    ok &= await check("a", first, "spec")

    e_unit, _ = make("pr-open-ci-red", chain)
    ci.write_text("fail")
    ok &= await check("e (red)", e_unit, "impl")
    ci.write_text("pass")
    ok &= await check("e (green)", e_unit, "review")

    h_unit, _ = make("closed-long-ago", {
        **chain, "plan.md": accepted("Plan").replace("accepted", "done"),
        "review.md": header("changes-requested") + r1,
    })
    ok &= await check("h", h_unit, "")

    f_unit, _ = make("out-of-rounds", {**chain, "review.md": header("changes-requested") + r1})
    os.environ["COS_REVIEW_ROUNDS"] = "1"
    try:
        stage, said = await page_offers(f_unit)
        ok &= await check("f", f_unit, "")
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
        git(workspace, "init", "-q", "-b", "main")
        git(workspace, "commit", "-q", "--allow-empty", "-m", "the only commit")
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
