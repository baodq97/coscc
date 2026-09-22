#!/usr/bin/env python3
"""Proof for .cos/0014_product-cannot-start-a-work-unit.

The claim in `intent.md`: a work unit goes from **not existing** to a **merged pull
request** using only the product's HTTP API, scored against the six ordered steps
`.claude/CLAUDE.md` defines per unit. When the unit was written the score was 1 of 6, and
step 1 blocked the other five.

    0  every claim held
    1  at least one did not
    2  the environment could not answer: no `gh` logged in, no `node`, no throwaway
       repository named, or the app would not start

Set `COS_HOST` and `COS_PORT` to a free address the frontend was built for, or point
`COS_URL` at a service that is already running. In a checkout the bundle carries the
address it was compiled with, so those two have to agree — `uv run coscc-build` first.

**This command may not do the product's work for it.** `intent.md` constraint 1: it talks
to the app over HTTP and nothing else. It runs `git` and `gh` **only to read** — `status`,
`branch --show-current`, `log`, `pr view` — and never `switch`, `commit`, `pr create` or
`pr merge`. Each of those is a step the product has to take, and a proof that took one
would be measuring itself.

**It spends real money.** Up to eight sessions, with `impl` bounded at $5 and `pr` at $3
(`coscc/policy.py:96-115`). `.claude/rules/coscc-app.md` says plainly that anything talking
to this app does not belong in an unattended loop. `--dry` runs every claim that costs
nothing and stops before the first paid step; run that first.

**It writes to a repository on GitHub.** `COS_PROOF_REPO` names the throwaway one it is
allowed to touch and there is no default — `spec.md` C4, and `intent.md` constraint 2.
Unset is exit 2. The name is printed before anything happens.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from scripts.proof_harness import EXIT_BROKEN, EXIT_ENV, EXIT_PASS, RealApp, say  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PROOF_REPO_ENV = "COS_PROOF_REPO"

# Where to talk to. Default is the app this proof starts itself; point it at a running
# service to measure that one instead — which is how this file was shown red, against the
# released 0.3.0 that has no route to create a unit.
URL_ENV = "COS_URL"

SLUG = "a-problem-this-proof-invented"
BRIEF = (
    "Kho này chưa có gì ghi lại rằng nó đã được một proof chạy qua. "
    "Thêm một dòng vào README.md nói ngày giờ và tên unit, không sửa gì khác."
)

# Long: `impl` may take fifty turns and `pr` thirty. A step that has not answered in this
# many seconds has stopped, not slowed down.
STEP_TIMEOUT = 1800.0

STAGES = ("intent", "spec", "plan", "impl", "pr")

# Which stages need tools. Everything else runs `manual`, which grants nothing
# (`coscc/policy.py:94-96`), and that is the locked default this proof does not widen.
AUTONOMOUS = {"impl", "pr"}


def read_git(repo: Path, *args: str) -> str:
    """`git`, for reading only. Every call site in this file is a question."""
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=120
    )
    return done.stdout.strip()


def read_gh(*args: str) -> tuple[int, str]:
    done = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=180)
    return done.returncode, (done.stdout or done.stderr or "").strip()


def require_environment() -> str:
    target = os.environ.get(PROOF_REPO_ENV, "").strip()
    if not target:
        print(
            f"{PROOF_REPO_ENV} is not set. This proof opens and merges a pull request, so "
            "it needs a throwaway repository named on purpose — there is no default."
        )
        raise SystemExit(EXIT_ENV)
    code, out = read_gh("auth", "status")
    if code != 0:
        print(f"gh is not logged in: {out}")
        raise SystemExit(EXIT_ENV)
    if subprocess.run(["which", "node"], capture_output=True).returncode != 0:
        print("no node on PATH — the board cannot be read without it")
        raise SystemExit(EXIT_ENV)
    print(f"working against {target}")
    return target


class Steps:
    """The six ordered steps of `.claude/CLAUDE.md`, scored as they are performed."""

    NAMES = (
        "1 allocate the number",
        "2 write intent.md",
        "3 the branch name",
        "4 cut the branch",
        "5 work the stages",
        "6 open and merge the pull request",
    )

    def __init__(self) -> None:
        self.done: set[int] = set()

    def mark(self, number: int) -> None:
        self.done.add(number)

    def report(self) -> str:
        return f"{len(self.done)} of 6"


def post(client: httpx.Client, path: str, body: dict, timeout: float = 60.0) -> httpx.Response:
    return client.post(path, json=body, timeout=timeout)


def run_stage(client: httpx.Client, cwd: str, unit: str, stage: str) -> dict:
    """One step, over HTTP, reading the stream to its end. Returns the final payload."""
    mode = "autonomous" if stage in AUTONOMOUS else "manual"
    told = post(client, "/api/board/mode", {"cwd": cwd, "unit": unit, "stage": stage, "mode": mode})
    if told.status_code != 200:
        return {"outcome": "failed", "error": f"mode: {told.text}"}

    last: dict = {}
    with client.stream(
        "POST", "/api/board/run",
        json={"cwd": cwd, "unit": unit, "stage": stage},
        timeout=STEP_TIMEOUT,
    ) as response:
        if response.status_code != 200:
            return {"outcome": "failed", "error": response.read().decode()[:400]}
        for line in response.iter_lines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            # The field is `type`, and `done` carries the payload flattened beside it
            # (`coscc/api.py` run route). An `error` line is the stream giving up.
            if item.get("type") in ("done", "error"):
                last = item
    if not last:
        return {"outcome": "failed", "error": "the stream ended with no result"}
    if last.get("type") == "error":
        return {"outcome": "failed", "error": last.get("error", "")}
    return last


def main() -> int:
    target = require_environment()
    dry = "--dry" in sys.argv
    steps = Steps()
    results: list[bool] = []

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "work"
        work.mkdir()
        base = os.environ.get(URL_ENV, "").rstrip("/")
        app = None
        if not base:
            from coscc.config import from_env

            # The address comes from the environment, not from a number written here. In a
            # checkout the compiled bundle carries the address it was built for
            # (`coscc/frontend.py` job two), so a proof that picked its own port would be
            # refused by the build guard — measured 2026-09-22, and it is the same posture
            # `scripts/verify_0003.py` takes with `COS_PORT`.
            app = RealApp(from_env(), work, Path(tmp) / "data").start()
            base = app.base
        print(f"talking to {base}")

        client = httpx.Client(base_url=base)
        try:
            # --- getting a workspace is the product's job too --------------------
            # The field is `repo_url`, and the app clones into its working folder. Asking
            # it to do this rather than cloning here is the point: getting a workspace is
            # a product capability too, and one it already had.
            added = post(
                client, "/api/workspaces",
                {"repo_url": target, "name": f"proof-{int(time.time())}"}, 600.0,
            )
            if added.status_code != 200:
                print(f"the app could not clone {target}: {added.text[:300]}")
                return EXIT_ENV
            cwd = added.json()["path"]
            checkout = Path(cwd)

            # --- claim 1: a unit exists that did not -----------------------------
            made = post(client, "/api/units", {"cwd": cwd, "slug": SLUG, "brief": BRIEF})
            created = made.status_code == 200
            unit = made.json().get("unit", "") if created else ""
            board = client.get("/api/board", params={"cwd": cwd}, timeout=120).json()
            listed = created and unit in [u.get("name") for u in board.get("units", [])]
            results.append(say(
                listed,
                f"the product created {unit or SLUG} and lists it on its own board",
                "" if listed else f"POST /api/units -> {made.status_code}: {made.text[:200]}",
            ))
            if not listed:
                # Nothing below can be attempted, and saying so is more useful than five
                # more failures that all mean this one.
                print(f"    steps: {steps.report()} — step 1 blocks the rest, which is "
                      "exactly what intent.md measured")
                return EXIT_BROKEN
            steps.mark(1)

            # --- claim 5 (early): nothing of coscc's is in the repository --------
            # Taken here as well as at the end, because a `.cos/` created at unit time is
            # a different fault from one created by a step.
            dirty = [l for l in read_git(checkout, "status", "--porcelain").splitlines() if l.strip()]
            clean_now = not any(".cos/" in l for l in dirty) and not (checkout / ".cos").exists()
            results.append(say(
                clean_now,
                "creating the unit put nothing into the repository's tree",
                "; ".join(dirty[:5]),
            ))

            if dry:
                print(f"--dry: stopping before the first paid step. steps: {steps.report()}")
                return EXIT_PASS if all(results) else EXIT_BROKEN

            # --- claim 2: the intent step runs, from the brief -------------------
            done = run_stage(client, cwd, unit, "intent")
            wrote_intent = done.get("outcome") == "done"
            results.append(say(
                wrote_intent,
                "the intent step ran and wrote intent.md from the brief",
                str(done.get("error") or done.get("outcome"))[:300],
            ))
            if wrote_intent:
                steps.mark(2)

            # --- claims 3 and 4: the branch --------------------------------------
            cut = post(client, "/api/units/branch", {"cwd": cwd, "unit": unit})
            named = cut.status_code == 200 and cut.json().get("branch")
            on = read_git(checkout, "branch", "--show-current")
            results.append(say(
                bool(named) and on == named,
                f"the product named this unit's branch and the workspace is on it ({on})",
                "" if named else cut.text[:300],
            ))
            if named and on == named:
                steps.mark(3)
                steps.mark(4)

            # --- claim 5: the stages ---------------------------------------------
            ran: list[str] = []
            for stage in STAGES[1:]:
                done = run_stage(client, cwd, unit, stage)
                if done.get("outcome") != "done":
                    results.append(say(
                        False, f"every stage after intent ran ({', '.join(ran) or 'none'})",
                        f"{stage}: {str(done.get('error') or done.get('outcome'))[:300]}",
                    ))
                    break
                ran.append(stage)
            else:
                results.append(say(True, f"every stage ran: {', '.join(ran)}"))
                steps.mark(5)

            # --- claim 6: the log knows who did it -------------------------------
            history = client.get(
                "/api/unit-history", params={"cwd": cwd, "unit": unit}, timeout=120
            ).json()
            rows = history.get("transitions", [])
            known = [r for r in rows if r.get("session") != "unknown"]
            results.append(say(
                bool(rows) and len(known) == len(rows),
                f"all {len(rows)} transitions name the stage and the session that made them",
                f"{len(rows) - len(known)} of {len(rows)} say 'unknown'",
            ))

            # --- claim 7: still nothing of coscc's in the repository -------------
            dirty = [l for l in read_git(checkout, "status", "--porcelain").splitlines() if l.strip()]
            clean = not any(".cos/" in l for l in dirty) and not (checkout / ".cos").exists()
            results.append(say(
                clean,
                "after every stage, the repository's tree still holds nothing of coscc's",
                "; ".join(dirty[:5]),
            ))

            # --- claim 8: the pull request ---------------------------------------
            code, out = read_gh("pr", "list", "--repo", target, "--head", on or "none",
                                "--state", "all", "--json", "number,state,url")
            opened = []
            if code == 0:
                try:
                    opened = json.loads(out or "[]")
                except (json.JSONDecodeError, ValueError):
                    opened = []
            merged = [p for p in opened if str(p.get("state")).upper() == "MERGED"]
            results.append(say(
                bool(merged),
                f"a pull request for {on} exists and is merged "
                f"({merged[0]['url'] if merged else 'none'})",
                f"found {[(p.get('state'), p.get('url')) for p in opened]}"[:300],
            ))
            if merged:
                steps.mark(6)
        finally:
            client.close()
            if app is not None:
                app.stop()

    print(f"    steps performed by the product: {steps.report()}")
    for number, name in enumerate(Steps.NAMES, start=1):
        print(f"      {'yes' if number in steps.done else ' no'}  {name}")
    return EXIT_PASS if all(results) and len(steps.done) == 6 else EXIT_BROKEN


if __name__ == "__main__":
    raise SystemExit(main())
