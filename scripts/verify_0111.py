"""`0111` proof: a rebase no longer leaves `review` with screenshots of a head that is gone.

Plain: no session, no quota, no network once uv's and npm's caches are warm (a cold cache is
exit 2, not 1). Needs `node`, `git`, `uv`, Playwright's chromium and `127.0.0.1:18783` free;
any missing is exit 2. It clones this checkout into a temporary directory, with a bare
directory as its `origin`, cuts a UI unit's branch there (one comment line in
`coscc/screens.py`), takes its screenshots with `scripts/capture_screens.py /board` at X,
and then drives `Service.run_step(..., "review")` in-process, with the worktree, the gate and
the comment poster stood in for as `scripts/verify_0085.py` does, and the session replaced by
a stand-in that records that it was called and what `review.md` held then. This process's own
environment carries `__REFLEX_SKIP_COMPILE=1` and `__REFLEX_MOUNT_FRONTEND_COMPILED_APP=1`,
as the app's does (`spike.md ## U1`), so blanking them is exercised. Each claim prints PASS or
FAIL (`spec.md` R11):

- (a) A commit on `main` that touches no screen; the branch rebased, X to Y. The manifest's
  `head` is Y, one `screens` record says `taken`, the stand-in was called, `review.md` was the
  fixture when it was, and `cos.mjs` `screensNeeds` finds nothing wrong with a round
  `Reviewed: Y` whose `### Screens` says `Taken at: Y`.
- (b) The same with a commit on `main` that changes `coscc/ui.py`, Y to Z, over a `.web`
  left stale by (a) — the case the spike did not measure.
- (c) The same, W, with `127.0.0.1:18783` held by this process: the step is refused, the
  stand-in is not called, one `screens` record says `failed`, `review.md` is unchanged.

Each capture takes 17.8-57.7 s (`spike.md ## U1`); the whole run a few minutes.

`--measure --workspace <journal key> [--since 2026-09-26] [--until 2026-10-16]` (R12) reads
`<COS_DATA_DIR>/cos.db` with `mode=ro`, never through `Data`, and does not import `coscc`.
For each `review` step whose `end` has `changes-requested` among its `verdicts`, with an
`integration` that ended `pushed` after the review step before it, it prints the `[open]`
`high` findings of the round whose `Reviewed:` is the head its `start` recorded, from
`<COS_DATA_DIR>/units/*/.cos/<unit>/review.md`. A person counts those that are only about a
stale `.screens/manifest.json`; the target is 0. Run it at a terminal (`0076`). It writes
nothing, and has no pass or fail: exit 0 when it read the database, 2 when it could not.

Exit codes: 0 pass, 1 broken, 2 environment not ready.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
COS = REPO / ".claude" / "scripts" / "cos.mjs"
HOST, PORT = "127.0.0.1", 18783  # `scripts/capture_screens.py:112`
BRANCH = "fix/fixture-screens"
ADDRESS = "/board"
EXIT_PASS, EXIT_BROKEN, EXIT_ENV = 0, 1, 2
ROUND_HEAD = re.compile(r"^## Round (\d+)\s*$")
# `cos.mjs` `ROUND_META`, copied; `--measure` does not import the app.
ROUND_META = re.compile(
    r"^Reviewed:\s*([0-9a-f]{7,40})\.?\s+Verdict:\s*(pass|changes-requested|needs-person|incomplete)\.?\s*$",
    re.IGNORECASE,
)
HIGH_OPEN = re.compile(r"^\s*-\s+F\d+\s+\[open\].*\s—\s+high\s+—", re.IGNORECASE)


class NotReady(Exception):
    """The environment, not the change: exit 2."""


def say(line: str) -> None:
    print(line, flush=True)


def claim(ok: bool, what: str, detail: str = "") -> bool:
    say(f"{'PASS' if ok else 'FAIL'} {what}" + (f"\n     {detail}" if detail and not ok else ""))
    return ok


def run(*argv: str, cwd: Path | None = None, env: dict[str, str] | None = None,
        timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


def git(tree: Path, *args: str) -> str:
    out = run("git", *args, cwd=tree)
    if out.returncode != 0:
        raise NotReady(f"git {' '.join(args)} failed in {tree}: {out.stderr.strip()}")
    return out.stdout.strip()


# --- the fixture ---------------------------------------------------------------

HEADER = "# Review: x\nSpec: spec.md. Author: t. Status: {status}.\n\n"


def round_text(n: int, verdict: str, head: str, screens: str = "") -> str:
    return (f"## Round {n}\n\nReviewed: {head}. Verdict: {verdict}.\n\n### Findings\n\n"
            f"- F1 [open] coscc/screens.py:1 — high — x\n\n### What was not reviewed\n\nnothing\n{screens}")


def chain(d: Path) -> None:
    d.mkdir(parents=True)
    files = {
        "idea.md": "# Idea: x\nAuthor: t. Status: accepted.\n",
        "intent.md": "# Intent: x\nAuthor: t. Type: fix. Status: accepted.\n",
        "spec.md": "# Spec: x\nIntent: intent.md. Author: t. Status: accepted.\n\n## Concerns\n\n- C1. đo rồi.\n",
        "plan.md": "# Plan: x\nIntent: intent.md. Author: t. Status: accepted.\n",
        "impl.md": "# Impl: x\nIntent: intent.md. Plan: plan.md. Author: t. Status: accepted.\n",
        "pr.md": "# PR: x\nPR: https://github.com/o/r/pull/7. Status: accepted.\n",
    }
    for name, body in files.items():
        (d / name).write_text(body, encoding="utf-8")


def plain_env() -> dict[str, str]:
    """This process's environment without the two names `main` sets, for the capture at X."""
    return {k: v for k, v in os.environ.items() if not k.startswith("__REFLEX_")}


def capture(tree: Path) -> None:
    out = run("uv", "run", "python", "scripts/capture_screens.py", ADDRESS, cwd=tree, env=plain_env(), timeout=600)
    if out.returncode != 0:
        tail = "\n".join((out.stdout + out.stderr).strip().splitlines()[-12:])
        raise NotReady(f"the capture at X exited {out.returncode}:\n{tail}")


def manifest(tree: Path) -> dict:
    try:
        return json.loads((tree / ".screens" / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def build_tree(tmp: Path) -> Path:
    """A clone of this checkout on `main`, a bare `origin`, and the UI unit's branch."""
    tree, bare = tmp / "tree", tmp / "origin.git"
    head = git(REPO, "rev-parse", "HEAD")
    git(tmp, "clone", "-q", "--no-checkout", str(REPO), str(tree))
    git(tree, "checkout", "-q", "-B", "main", head)
    for key, value in (("user.name", "T"), ("user.email", "t@example.invalid"), ("commit.gpgsign", "false")):
        git(tree, "config", key, value)
    git(tmp, "init", "-q", "--bare", str(bare))
    git(tree, "remote", "set-url", "origin", str(bare))
    git(tree, "push", "-q", "origin", "main")
    git(tree, "fetch", "-q", "--prune", "origin")
    git(tree, "switch", "-q", "-c", BRANCH)
    with (tree / "coscc" / "screens.py").open("a", encoding="utf-8") as f:
        f.write("# 0111 fixture: the unit's own change to a screen\n")
    git(tree, "commit", "-q", "-am", "fixture: a UI change")
    return tree


def advance_main(tree: Path, path: str, line: str) -> str:
    """One commit on `main`, pushed, and the branch rebased onto it. Returns the new head."""
    git(tree, "switch", "-q", "main")
    target = tree / path
    with target.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    git(tree, "add", path)
    git(tree, "commit", "-q", "-m", f"main: {path}")
    git(tree, "push", "-q", "origin", "main")
    git(tree, "switch", "-q", BRANCH)
    git(tree, "fetch", "-q", "origin")
    git(tree, "rebase", "-q", "origin/main")
    return git(tree, "rev-parse", "HEAD")


def screens_needs(tree: Path, unit: str, head: str) -> str:
    """What `cos.mjs` `screensNeeds` says of a passing round `Reviewed: head`, `Taken at: head`."""
    screens = (f"\n### Screens\n\nTaken at: {head}. Standard: `.claude/rules/ui-standard.md`. "
               f"Looked at by: an agent session (write-review), from screenshots.\n\n"
               f"- .screens/board-1440x900.png — 1440×900 — /board — no violation\n")
    text = HEADER.format(status="accepted") + round_text(1, "pass", head, screens)
    script = (
        f"import {{ screensNeeds, makeProbe, parseReview }} from {json.dumps(COS.as_uri())}\n"
        f"const last = parseReview({json.dumps(text)}).rounds.at(-1)\n"
        # `said.head` is the pull request's head `shipNeeds` checked; here, the branch's.
        f"console.log(JSON.stringify(screensNeeds({{ name: {json.dumps(unit)} }}, makeProbe({json.dumps(str(tree))}), last, {{ head: {json.dumps(head)} }})))\n"
    )
    out = run("node", "--input-type=module", "-e", script, cwd=tree)
    return out.stdout.strip() if out.returncode == 0 else f"node exited {out.returncode}: {out.stderr.strip()[-400:]}"


class _Stand:
    """The session: records each call and what `review.md` held at that moment."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.review_md: Path | None = None

    def in_flight(self):
        return []

    async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
        held = self.review_md.read_text(encoding="utf-8") if self.review_md else ""
        self.calls.append({"text": text, "review_md": held})
        # Only its new round: the runner writes the earlier ones back from the file.
        yield ("chunk", HEADER.format(status="changes-requested") + round_text(2, "changes-requested", "b" * 40))
        yield ("done", {"session_id": "s1", "terminal_reason": "success", "cost": {"turns": 1, "cost_usd": 0.0}})


def screens_records(data: Path) -> list[dict]:
    db = data / "cos.db"
    if not db.exists():
        return []
    with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as conn:
        return [json.loads(raw) for (raw,) in conn.execute("SELECT record FROM runs WHERE kind = 'screens' ORDER BY id")]


def through_the_service(tmp: Path) -> bool:
    from coscc import board
    from coscc.config import Config
    from coscc.service import Invalid, Service

    tree = build_tree(tmp)
    capture(tree)
    x = git(tree, "rev-parse", "HEAD")
    if manifest(tree).get("head") != x:
        raise NotReady(f"the capture at X wrote no manifest for {x}")

    repo = tmp / "work" / "proj"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
        "commit", "-q", "--allow-empty", "-m", "first")
    data = tmp / "data"
    stand = _Stand()
    service = Service(Config(workspaces=(str(repo),), working_dir=str(tmp / "work"), data_dir=str(data)), stand)
    made = asyncio.run(service.create_unit(str(repo), "screens-after-a-rebase", "some words"))
    unit, d = made["unit"], Path(made["path"])
    shutil.rmtree(d)
    chain(d)
    review_md = d / "review.md"
    fixture = HEADER.format(status="changes-requested") + round_text(1, "changes-requested", "a" * 40)
    stand.review_md = review_md
    where = {"path": str(tree), "branch": BRANCH, "base": None}

    # The app's own environment, as `coscc/run.py` leaves it (`spike.md ## U1`, "Cách đo").
    os.environ["__REFLEX_SKIP_COMPILE"] = "1"
    os.environ["__REFLEX_MOUNT_FRONTEND_COMPILED_APP"] = "1"

    def step() -> tuple[list, Exception | None]:
        async def go():
            with mock.patch.object(Service, "_worktree", mock.AsyncMock(return_value=where)), \
                 mock.patch.object(board, "gate", mock.AsyncMock(return_value=(True, "open"))), \
                 mock.patch.object(Service, "_post_new_rounds", mock.AsyncMock(return_value=[])):
                return [i async for i in service.run_step(str(repo), unit, "review")]
        try:
            return asyncio.run(go()), None
        except Exception as e:  # noqa: BLE001 — (c) expects one; (a) and (b) print it
            return [], e

    ok = True
    heads = {"X": x}
    for case, path, line in (
        ("a", "NOTES.txt", "a commit on main that touches no screen"),
        ("b", "coscc/ui.py", "# 0111 fixture: a commit on main that changes a screen"),
    ):
        review_md.write_text(fixture, encoding="utf-8")
        new = advance_main(tree, path, line)
        heads[case] = new
        before, calls = len(screens_records(data)), len(stand.calls)
        _, raised = step()
        added = screens_records(data)[before:]
        say(f"     ({case}) the retake took {[r.get('seconds') for r in added]} s")
        got = manifest(tree).get("head", "")
        ok &= claim(got == new, f"({case}) the manifest's head is the rebased head {new[:7]}",
                    f"it is {got[:7] or 'missing'}; run_step raised {raised!r}")
        ok &= claim([r.get("outcome") for r in added] == ["taken"],
                    f"({case}) exactly one new screens record, outcome taken", json.dumps(added)[:600])
        ok &= claim(len(stand.calls) == calls + 1, f"({case}) the review session was started", f"raised {raised!r}")
        ok &= claim(len(stand.calls) > calls and stand.calls[-1]["review_md"] == fixture,
                    f"({case}) review.md was the fixture when the session started")
        said = screens_needs(tree, unit, new)
        ok &= claim(said == "[]", f"({case}) screensNeeds of a round Reviewed and Taken at {new[:7]} is []", said)

    review_md.write_text(fixture, encoding="utf-8")
    new = advance_main(tree, "NOTES.txt", "another commit on main that touches no screen")
    before, calls = len(screens_records(data)), len(stand.calls)
    with closing(socket.socket()) as held:
        held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        held.bind((HOST, PORT))
        held.listen(1)
        _, raised = step()
    added = screens_records(data)[before:]
    say(f"     (c) the retake took {[r.get('seconds') for r in added]} s, and said {raised}")
    ok &= claim(isinstance(raised, Invalid), "(c) with the port held, run_step refuses the step", f"raised {raised!r}")
    ok &= claim(len(stand.calls) == calls, "(c) the review session was not started")
    ok &= claim([r.get("outcome") for r in added] == ["failed"],
                "(c) exactly one new screens record, outcome failed", json.dumps(added)[:600])
    ok &= claim(review_md.read_text(encoding="utf-8") == fixture, "(c) review.md is unchanged")
    return ok


# --- R12 -----------------------------------------------------------------------

def data_root() -> Path:
    return Path(os.environ.get("COS_DATA_DIR") or "~/.cos").expanduser()


def _day(value: str, end: bool) -> datetime:
    at = datetime.fromisoformat(value + ("T23:59:59" if end else "T00:00:00"))
    return at.astimezone(timezone.utc)


def _when(at: str) -> datetime | None:
    try:
        t = datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return None
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def open_highs(text: str, head: str) -> list[str] | None:
    """The `[open]` `high` finding lines of the round whose `Reviewed:` starts `head`."""
    lines, found, inside, seen = [], None, False, False
    for line in text.splitlines():
        if line.rstrip() == "## Answers":
            break
        if ROUND_HEAD.match(line):
            inside, seen = False, False
            continue
        if not seen and line.strip():
            seen = True
            m = ROUND_META.match(line.strip())
            inside = bool(m and head.startswith(m.group(1).lower()))
            if inside:
                found = lines = []
            continue
        if inside and HIGH_OPEN.match(line):
            lines.append(line.strip())
    return found


def measure(root: Path, workspace: str, since: datetime, until: datetime) -> int:
    db = root / "cos.db"
    if not db.exists():
        say(f"no {db}")
        return EXIT_ENV
    try:
        with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as conn:
            rows = [json.loads(raw) for (raw,) in conn.execute(
                "SELECT record FROM runs WHERE workspace = ? AND kind IN ('start', 'end', 'integration') ORDER BY id",
                (workspace,))]
    except (sqlite3.Error, ValueError) as e:
        say(f"could not read {db}: {e}")
        return EXIT_ENV
    starts: dict[str, dict] = {}
    pushed: dict[str, bool] = {}
    cases = 0
    for r in rows:
        unit, kind = str(r.get("unit") or ""), r.get("kind")
        if kind == "integration" and r.get("outcome") == "pushed":
            pushed[unit] = True
            continue
        if r.get("stage") != "review":
            continue
        if kind == "start":
            starts[str(r.get("run") or unit)] = r
            continue
        if kind != "end":
            continue
        after_push, pushed[unit] = pushed.get(unit, False), False
        at = _when(str(r.get("at") or ""))
        if not after_push or at is None or not (since <= at <= until):
            continue
        if "changes-requested" not in [str(v) for v in r.get("verdicts") or []]:
            continue
        start = starts.get(str(r.get("run") or unit)) or {}
        head = str(start.get("head") or "")
        cases += 1
        say(f"{unit} review ended {r.get('at')} on {head[:12] or 'no head'}:")
        texts = [p.read_text(encoding="utf-8") for p in root.glob(f"units/*/.cos/{unit}/review.md")]
        found = next((h for h in (open_highs(t, head) for t in texts) if h is not None), None) if head else None
        if found is None:
            say("  no round in review.md names that head")
        for line in found or []:
            say(f"  {line}")
        if found == []:
            say("  no open high finding")
    say(f"{cases} review round(s) asked for changes after a pushed integration, "
        f"{since.astimezone().date()} to {until.astimezone().date()}: count the findings above that are only about a stale manifest; the target is 0")
    return EXIT_PASS


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--measure", action="store_true", help="R12, on the real <COS_DATA_DIR>")
    parser.add_argument("--workspace", help="the journal key: the workspace's resolved path")
    parser.add_argument("--since", default="2026-09-26")
    parser.add_argument("--until", default="2026-10-16")
    args = parser.parse_args()
    if args.measure:
        if not args.workspace:
            say("--measure needs --workspace <the workspace's resolved path>")
            return EXIT_ENV
        try:
            since, until = _day(args.since, False), _day(args.until, True)
        except ValueError as e:
            say(f"a bad date: {e}")
            return EXIT_ENV
        return measure(data_root(), args.workspace, since, until)

    for tool in ("node", "git", "uv"):
        if not shutil.which(tool):
            say(f"environment: {tool} is needed")
            return EXIT_ENV
    from proof_harness import port_free, require_browser

    if not port_free(HOST, PORT):
        say(f"environment: {HOST}:{PORT} is in use")
        return EXIT_ENV
    try:
        playwright, browser = require_browser()
    except SystemExit:
        return EXIT_ENV
    browser.close()
    playwright.stop()
    with tempfile.TemporaryDirectory(prefix="verify-0111-") as d:
        try:
            ok = through_the_service(Path(d))
        except (NotReady, subprocess.TimeoutExpired) as e:
            say(f"environment: {e}")
            return EXIT_ENV
    say("all claims pass" if ok else "some claims failed")
    return EXIT_PASS if ok else EXIT_BROKEN


if __name__ == "__main__":
    sys.exit(main())
