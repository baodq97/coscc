#!/usr/bin/env python3
"""Proof for .cos/0005_hand-driven-invisible-loop.

Seven propositions, listed in `plan.md ## Proof`. Four of them (8–11) are not here: they
need a real browser and they live in `scripts/verify_0003.py`, beside the other claim that
can only be seen on a rendered page.

Exit codes follow `scripts/proof_harness.py:36`:

    0  every proposition that could be checked held, and none were skipped
    1  a proposition is broken
    2  the environment is not ready — no remote to open a pull request against,
       no `gh`, no `node`, no `claude`

Two of those codes carry the same weight here as in the page proof: a `pr` step that cannot
run because this repository has no remote (`spec.md` C2, `plan.md` Risk 2) is **not** the
same fact as a `pr` step that ran and failed, and collapsing them would report a missing
remote as a broken app. Every claim prints its own verdict regardless, so a `2` still says
which of the other six stood up.

**It spends real quota.** Propositions 2–7 run a work unit from `idea` to `ship` through the
board, which is eight sessions, one of them an `impl` step with a $5 ceiling. Nothing here
loops, and the scratch unit is deliberately tiny, but this is not a command to run casually.

**It pushes and opens a pull request.** `COS_PROOF_REPO` names the repository it is allowed
to do that to, and there is no default: reaching off this machine is the author's decision,
not this script's. Unset means exit 2.
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

import httpx

import claude_agent_sdk as sdk

from cos_baodo import policy
from cos_baodo.api import build
from cos_baodo.config import from_env
from cos_baodo.journal import Journal
from cos_baodo.runner import build_prompt
from cos_baodo.sessions import _options  # the app's own construction path — see claim 5

REPO = Path(__file__).resolve().parent.parent
COS_MJS = REPO / ".claude" / "scripts" / "cos.mjs"

EXIT_PASS, EXIT_BROKEN, EXIT_ENV = 0, 1, 2

# The eight names. Read from the script rather than restated, so a ninth stage added to
# `cos.mjs` cannot leave this file quietly checking seven.
STAGES: list[str] = []

# The scratch unit. The slug is the only seed the `idea` stage gets: `build_prompt` has no
# field for a topic, so the unit's own name is what tells the first step what this is about.
# That is thin, and it is the real interface — a human opening a unit types exactly this.
UNIT = "0001_readme-has-no-greeting"
ARTIFACTS = {
    "idea": "idea.md", "intent": "intent.md", "spec": "spec.md", "plan": "plan.md",
    "impl": "impl.md", "pr": "pr.md", "review": "review.md", "ship": "ship.md",
}
# Only these two carry tools. The other six are the prose stages.
AUTONOMOUS = ("impl", "pr")

# A second unit, used once, to run a step into its ceiling on purpose.
CEILING_UNIT = "0001_a-step-that-runs-out-of-room"

PROOF_REPO_ENV = "COS_PROOF_REPO"


class Claim:
    def __init__(self, number: int, title: str):
        self.number, self.title = number, title
        self.failures: list[str] = []
        self.notes: list[str] = []
        self.skipped = ""

    def check(self, what: str, ok: bool, detail: str = "") -> bool:
        if not ok:
            self.failures.append(f"{what}{': ' + detail if detail else ''}")
        return ok

    def note(self, text: str) -> None:
        """Something a reader needs alongside the verdict. Held until the verdict prints."""
        self.notes.append(text)

    def skip(self, why: str) -> "Claim":
        self.skipped = why
        return self

    def report(self) -> str:
        """`pass`, `fail` or `skip` — printed here so one red claim cannot hide the rest."""
        if self.skipped:
            print(f"SKIP  claim {self.number}: {self.title}")
            print(f"        - {self.skipped}")
            return "skip"
        if self.failures:
            print(f"FAIL  claim {self.number}: {self.title}")
            for line in self.failures:
                print(f"        - {line}")
            verdict = "fail"
        else:
            print(f"PASS  claim {self.number}: {self.title}")
            verdict = "pass"
        for line in self.notes:
            print(f"        · {line}")
        return verdict


# --------------------------------------------------------------------------
# environment
# --------------------------------------------------------------------------


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=120
    )


def cannot_run_sessions() -> list[str]:
    """Reasons no step can run at all. Everything below claim 1 depends on these."""
    reasons: list[str] = []
    if shutil.which("node") is None:
        reasons.append("node is not on PATH, and the board reads its units by running cos.mjs")
    if not COS_MJS.is_file():
        reasons.append(f"missing {COS_MJS.relative_to(REPO)}")
    if shutil.which("claude") is None:
        reasons.append("the claude CLI is not on PATH, so no session can be created")
    if shutil.which("git") is None:
        reasons.append("git is not on PATH, and a workspace is a repository")
    return reasons


def cannot_open_a_pull_request() -> list[str]:
    """Reasons the `pr` step has nowhere to go.

    Kept apart from the reasons above so that a missing remote costs the author claims
    2, 3, 4 and 6 and **not** claims 5 and 7, which need no remote and are the two cheapest
    things this command measures. `plan.md` Risk 2 says nine tenths of the unit still runs;
    this is that sentence made mechanical.
    """
    url = (os.environ.get(PROOF_REPO_ENV) or "").strip()
    if not url:
        remote = _run(["git", "remote", "-v"], REPO).stdout.strip()
        return [
            f"{PROOF_REPO_ENV} is unset, so there is nowhere a pull request may be opened"
            + (" — and this repository has no remote either (spec.md C2)" if not remote else "")
        ]
    reasons: list[str] = []
    if shutil.which("gh") is None:
        reasons.append("gh is not on PATH, and the pr step opens the pull request with it")
    elif _run(["gh", "auth", "status"]).returncode != 0:
        reasons.append("gh is not logged in — `gh auth status` failed")
    if _run(["git", "ls-remote", url]).returncode != 0:
        reasons.append(f"{PROOF_REPO_ENV}={url} cannot be reached with git")
    return reasons


# --------------------------------------------------------------------------
# claim 1 — the gate knows eight names, and only eight
# --------------------------------------------------------------------------


def _gate(root: Path, unit: str, stage: str) -> subprocess.CompletedProcess:
    return _run(["node", str(COS_MJS), "--root", str(root), "gate", unit, stage])


ACCEPTED = "# {stage}: scratch\nAuthor: proof. Status: accepted.\n"


def claim_1() -> Claim:
    c = Claim(1, "the gate answers all eight stage names, and refuses a ninth")
    if not STAGES:
        return c.skip("cos.mjs did not report its stage list")
    c.check("there are eight stages", len(STAGES) == 8, f"got {len(STAGES)}: {STAGES}")

    root = Path(tempfile.mkdtemp(prefix="cos0005-gate-"))
    try:
        directory = root / ".cos" / UNIT
        directory.mkdir(parents=True)

        # Before anything is written, the gate has to be capable of saying no. That
        # closed on a check that could not fail; this is that lesson, one line long.
        blocked = _gate(root, UNIT, "spec")
        c.check(
            "an empty unit is blocked at spec",
            blocked.returncode != 0,
            f"exit {blocked.returncode}: {blocked.stdout.strip()}",
        )

        for stage in STAGES:
            (directory / ARTIFACTS[stage]).write_text(ACCEPTED.format(stage=stage))

        for stage in STAGES:
            got = _gate(root, UNIT, stage)
            c.check(
                f"gate knows {stage!r}",
                got.returncode == 0 and "open:" in got.stdout,
                f"exit {got.returncode}: {(got.stdout + got.stderr).strip()[:160]}",
            )

        for invented in ("deploy", "rollback", ""):
            got = _gate(root, UNIT, invented)
            c.check(
                f"gate refuses {invented!r}",
                got.returncode != 0 and (
                    "unknown stage" in got.stderr or "usage:" in got.stderr
                ),
                f"exit {got.returncode}: {(got.stdout + got.stderr).strip()[:160]}",
            )
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return c


# --------------------------------------------------------------------------
# driving the board
# --------------------------------------------------------------------------


def _client(app) -> httpx.AsyncClient:
    # No timeout worth the name: an `impl` step with a 50-turn ceiling takes as long as it
    # takes, and a client that gives up first would report a working app as broken.
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proof", timeout=None
    )


async def _json(http, method, path, **kw):
    r = await http.request(method, path, **kw)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"error": r.text[:300]}


async def _run_step(http, cwd: str, unit: str, stage: str, mode: str) -> dict:
    """Set the mode, run the step, return the `done` payload — or a `refused` key.

    The two are kept apart deliberately. A `done` payload always carries an `error` field,
    and it is often filled in on a step that ran perfectly well and was simply bounded, so
    reading that field as "the call failed" would report every `exhausted` step as a broken
    one. `refused` means the request never got as far as a run.

    Both calls go through the HTTP surface the browser uses. There is no test-only path
    into the runner, for the same reason given before: a path nobody walks proves nothing.
    """
    status, body = await _json(
        http, "POST", "/api/board/mode",
        json={"cwd": cwd, "unit": unit, "stage": stage, "mode": mode},
    )
    if status != 200:
        return {"refused": f"set mode: {body.get('error', status)}"}

    payload = {"cwd": cwd, "unit": unit, "stage": stage}
    async with http.stream("POST", "/api/board/run", json=payload) as r:
        if r.status_code != 200:
            await r.aread()
            return {"refused": f"HTTP {r.status_code}: {r.json().get('error', '')}"}
        out: dict = {"text": ""}
        async for raw in r.aiter_lines():
            line = raw.strip()
            if not line:
                continue
            event = json.loads(line)
            kind = event.get("type")
            if kind == "chunk":
                out["text"] += event.get("text", "")
            elif kind == "error":
                return {"refused": event["error"]}
            elif kind == "done":
                out.update({k: v for k, v in event.items() if k != "type"})
        return out


# --------------------------------------------------------------------------
# claim 5 — a prose step carries nothing
# --------------------------------------------------------------------------


async def _init_tools(config, cwd: str) -> tuple[list[str], list[str]]:
    """What a prose step's session is actually handed, read off the SDK's `init` message.

    Built with the app's own `_options`, with exactly the arguments `Runner` passes for a
    stage whose grant is empty. Asking the grant table instead would only prove the table
    agrees with itself; the table was measured, and it is not the whole answer.
    """
    grant = policy.grant_for("spec", "autonomous")
    options = _options(
        config, cwd, None,
        max_turns=grant.max_turns,
        can_use_tool=None,
        tools=list(grant.tools) if grant.opens_anything else None,
        max_budget_usd=grant.max_budget_usd or None,
    )
    client = sdk.ClaudeSDKClient(options=options)
    await client.connect()
    tools: list[str] = []
    servers: list[str] = []
    try:
        await client.query("Reply with exactly: READY")
        async for message in client.receive_response():
            if isinstance(message, sdk.SystemMessage) and message.subtype == "init":
                tools = [str(t) for t in (message.data.get("tools") or [])]
                servers = [
                    str(s.get("name", s)) if isinstance(s, dict) else str(s)
                    for s in (message.data.get("mcp_servers") or [])
                ]
    finally:
        await client.disconnect()
    return tools, servers


async def claim_5(config, cwd: str) -> Claim:
    c = Claim(5, "a prose step's session reports 0 tools at init")
    try:
        tools, servers = await _init_tools(config, cwd)
    except Exception as e:  # noqa: BLE001 — a failure to measure is not a pass
        return c.skip(f"could not read the init message: {type(e).__name__}: {e}")

    c.check("0 tools in the init message", tools == [], f"{len(tools)}: {', '.join(tools[:12])}")
    # The tool list alone is not a trustworthy measurement, and finding that out is worth
    # more than the list. Measured 2026-09-22, same machine, same options, two consecutive
    # runs: three `mcp__microsoft-learn__*` tools the first time and **none** the second,
    # with the same two servers attached both times. The list races the servers' connection,
    # so a run that reads it early sees zero and reports a session that is not empty as
    # empty. The attached servers are what does not flicker, so the claim asks for both.
    c.check("no MCP server attached", servers == [], ", ".join(servers))
    if tools and all(t.startswith("mcp__") for t in tools):
        c.note(
            "every tool that arrived is an MCP tool from this machine's own configuration."
        )
    if servers:
        c.note(
            f"mcp servers reaching a session whose grant is empty: {', '.join(servers)}. "
            "The zero-tool default is not enforced against MCP. `--tools` names the "
            "built-in set only, so it cannot subtract these. The grant is empty; the "
            "session is not, and this claim stays red until that is closed."
        )
    return c


# --------------------------------------------------------------------------
# claim 7 — a ceiling reads as a ceiling
# --------------------------------------------------------------------------


async def claim_7(http, cwd: str) -> Claim:
    c = Claim(7, "a step that runs past its turn ceiling reports `exhausted`, not a hang")
    directory = Path(cwd) / ".cos" / CEILING_UNIT
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "intent.md").write_text(
        "# Intent: a step with no room\nAuthor: proof. Status: accepted.\n\n"
        "## Proposed outcome\nMột bước `impl` bị chặn ở lượt đầu tiên.\n",
        encoding="utf-8",
    )
    (directory / "plan.md").write_text(
        "# Plan: a step with no room\nIntent: intent.md. Spec: skipped (proof). "
        "Author: proof. Status: accepted.\n\n## Order of work\n"
        "1. Tạo ba file `a.txt`, `b.txt`, `c.txt`, mỗi file một dòng khác nhau.\n"
        "2. Đọc lại cả ba và kiểm nội dung.\n",
        encoding="utf-8",
    )

    original = policy.GRANTS.get(("impl", "autonomous"))
    # The ceiling is a number in the grant table, so lowering it is configuration rather
    # than a second code path. One turn plus a task that needs tools is the shortest
    # reliable way to reach it: any tool call forces a second turn there is no room for.
    policy.GRANTS[("impl", "autonomous")] = policy.Grant(
        tools=original.tools if original else ("Read", "Write"),
        commands=original.commands if original else (),
        max_turns=1,
        max_budget_usd=0.5,
        app_writes_artifact=False,
    )
    try:
        done = await asyncio.wait_for(
            _run_step(http, cwd, CEILING_UNIT, "impl", "autonomous"), timeout=600
        )
    except asyncio.TimeoutError:
        c.check("it came back at all", False, "the step hung for 10 minutes")
        return c
    finally:
        if original is None:
            policy.GRANTS.pop(("impl", "autonomous"), None)
        else:
            policy.GRANTS[("impl", "autonomous")] = original

    if "refused" in done:
        c.check("the step ran", False, done["refused"])
        return c
    c.check(
        "outcome is `exhausted`",
        done.get("outcome") == "exhausted",
        f"got {done.get('outcome')!r} — {str(done.get('error') or '')[:160]}",
    )
    c.check("it says why", "ceiling" in str(done.get("error") or ""), str(done.get("error"))[:160])
    return c


# --------------------------------------------------------------------------
# claims 2, 3, 4, 6 — one unit, idea to ship
# --------------------------------------------------------------------------


async def the_full_run(http, sessions, cwd: str, journal: Journal) -> list[Claim]:
    c2 = Claim(2, "a unit runs idea → ship through the board, every step a session this app made")
    c3 = Claim(3, "every step's prompt carried the artifact of the step before it")
    c4 = Claim(4, "the impl step changes a file, and the pr step opens a pull request")
    c6 = Claim(6, "the unit's token total is not zero, and equals the sum of its steps")

    directory = Path(cwd) / ".cos" / UNIT
    directory.mkdir(parents=True, exist_ok=True)

    before = _run(["git", "rev-parse", "HEAD"], Path(cwd)).stdout.strip()
    outcomes: dict[str, dict] = {}
    for stage in STAGES:
        mode = "autonomous" if stage in AUTONOMOUS else "manual"
        print(f"      running {stage} ({mode}) …", flush=True)
        done = await _run_step(http, cwd, UNIT, stage, mode)
        outcomes[stage] = done
        if "refused" in done:
            c2.check(f"{stage} ran", False, done["refused"])
            continue
        c2.check(
            f"{stage} finished",
            done.get("outcome") == "done",
            f"{done.get('outcome')} — {str(done.get('error') or '')[:200]}",
        )
        session_id = done.get("session_id") or ""
        c2.check(f"{stage} has a session id", bool(session_id))
        c2.check(
            f"{stage}'s session was created here",
            bool(session_id) and sessions.created_here(session_id),
        )
        c2.check(
            f"{stage} wrote {ARTIFACTS[stage]}",
            (directory / ARTIFACTS[stage]).is_file(),
        )

    # --- claim 3, from two directions ------------------------------------
    key = str(Path(cwd).expanduser().resolve())
    starts = {
        r.get("stage"): r
        for r in journal.records(key, UNIT)
        if r.get("kind") == "start"
    }
    for position, stage in enumerate(STAGES):
        if stage not in starts:
            c3.check(f"{stage} was recorded", False, "no start record")
            continue
        included = list(starts[stage].get("included") or [])
        if position == 0:
            c3.check("idea had nothing before it", included == [], str(included))
            continue
        earlier = [ARTIFACTS[s] for s in STAGES[:position]]
        c3.check(
            f"{stage} was given the stage before it",
            any(name in included for name in earlier),
            f"included {included}",
        )

    # And again from the prompt itself, not from what the journal says about it: the body
    # of the previous artifact has to appear verbatim in the text the step would be sent.
    for position, stage in enumerate(STAGES[1:], start=1):
        previous = directory / ARTIFACTS[STAGES[position - 1]]
        if not previous.is_file():
            continue
        body = previous.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        marker = next((line for line in body if len(line.strip()) > 20), "")
        prompt, _ = build_prompt(cwd, UNIT, stage, STAGES, ARTIFACTS[stage])
        c3.check(
            f"{stage}'s prompt contains a line of {previous.name}",
            bool(marker) and marker in prompt,
            f"looked for {marker[:60]!r}",
        )

    # --- claim 4 ----------------------------------------------------------
    after = _run(["git", "rev-parse", "HEAD"], Path(cwd)).stdout.strip()
    changed = _run(["git", "status", "--porcelain"], Path(cwd)).stdout.strip()
    c4.check(
        "the impl step left a change in the workspace",
        bool(changed) or (before != after),
        "the working tree is clean and HEAD did not move",
    )
    pr_text = str(outcomes.get("pr", {}).get("text") or "")
    pr_file = directory / "pr.md"
    pr_body = pr_file.read_text(encoding="utf-8", errors="replace") if pr_file.is_file() else ""
    listed = _run(["gh", "pr", "list", "--json", "url,headRefName"], Path(cwd))
    opened: list[dict] = []
    if listed.returncode == 0:
        try:
            opened = json.loads(listed.stdout or "[]")
        except json.JSONDecodeError:
            opened = []
    c4.check(
        "a pull request exists on the remote",
        bool(opened),
        f"gh pr list returned {listed.stdout.strip()[:160] or listed.stderr.strip()[:160]}",
    )
    if opened:
        c4.note(f"pull request: {opened[0].get('url')}")
    c4.check(
        "pr.md names it",
        any(str(p.get("url", "")) in (pr_body + pr_text) for p in opened) if opened else False,
        "the artifact does not carry the URL that was opened",
    )

    # --- claim 6 ----------------------------------------------------------
    status, body = await _json(http, "GET", "/api/timeline", params={"cwd": cwd, "unit": UNIT})
    if status != 200:
        c6.check("timeline reads", False, str(body.get("error", status)))
        return [c2, c3, c4, c6]
    runs = body.get("runs") or []
    total = body.get("cost") or {}
    fields = ("input_tokens", "output_tokens", "cache_read_input_tokens",
              "cache_creation_input_tokens")
    tokens = sum(int(total.get(f) or 0) for f in fields)
    c6.check("the unit's token total is not zero", tokens > 0, f"got {tokens}")
    for field_name in fields:
        summed = sum(int((r.get("cost") or {}).get(field_name) or 0) for r in runs)
        c6.check(
            f"{field_name} totals to the sum of the steps",
            int(total.get(field_name) or 0) == summed,
            f"total {total.get(field_name)} vs steps {summed}",
        )
    c6.note(f"{len(runs)} runs, {tokens} tokens, ${float(total.get('cost_usd') or 0):.6f}")
    return [c2, c3, c4, c6]


# --------------------------------------------------------------------------


async def main() -> int:
    global STAGES

    listing = _run(["node", str(COS_MJS), "status", "--json"], REPO)
    if listing.returncode == 0:
        try:
            STAGES = [s["name"] for s in json.loads(listing.stdout)["stages"]]
        except (json.JSONDecodeError, KeyError, TypeError):
            STAGES = []

    # Claim 1 creates nothing and spends nothing, so it runs first and reports either way —
    # a `2` should still tell the reader whether the gate is sound.
    verdicts = [claim_1().report()]

    blocking = cannot_run_sessions()
    remote = cannot_open_a_pull_request()
    if blocking:
        for number, title in (
            (2, "a unit runs idea → ship through the board"),
            (3, "every step's prompt carried the step before it"),
            (4, "impl changes a file and pr opens a pull request"),
            (5, "a prose step reports 0 tools at init"),
            (6, "the unit's tokens total to the sum of its steps"),
            (7, "a step past its ceiling reports `exhausted`"),
        ):
            Claim(number, title).skip("no step can run here").report()
        _why(blocking + remote)
        return EXIT_BROKEN if "fail" in verdicts else EXIT_ENV

    root = Path(tempfile.mkdtemp(prefix="cos0005-"))
    try:
        env = {"COS_WORKING_DIR": str(root), "COS_DATA_DIR": str(root)}
        config = from_env(env)
        app = build(config)
        sessions = app.state.sessions
        journal = Journal(str(root), str(root))

        async with _client(app) as http:
            if remote:
                # No pull request is possible, so the scratch workspace is a local
                # repository rather than a clone. Claims 5 and 7 still run in it.
                (root / "scratch").mkdir(parents=True, exist_ok=True)
                _run(["git", "init", "-q"], root / "scratch")
                add: dict = {"name": "scratch"}
            else:
                add = {"name": "scratch", "repo_url": os.environ[PROOF_REPO_ENV].strip()}
            status, body = await _json(http, "POST", "/api/workspaces", json=add)
            if status != 200:
                print(f"FAIL  could not prepare the scratch workspace: {body.get('error')}")
                return EXIT_BROKEN
            cwd = body["path"]

            verdicts.append((await claim_5(config, cwd)).report())
            if remote:
                for number, title in (
                    (2, "a unit runs idea → ship through the board"),
                    (3, "every step's prompt carried the step before it"),
                    (4, "impl changes a file and pr opens a pull request"),
                    (6, "the unit's tokens total to the sum of its steps"),
                ):
                    Claim(number, title).skip("the unit cannot reach `pr`").report()
                verdicts.append("skip")
            else:
                for claim in await the_full_run(http, sessions, cwd, journal):
                    verdicts.append(claim.report())
            verdicts.append((await claim_7(http, cwd)).report())
    finally:
        shutil.rmtree(root, ignore_errors=True)

    if remote:
        _why(remote)
    if "fail" in verdicts:
        return EXIT_BROKEN
    if "skip" in verdicts:
        return EXIT_ENV
    return EXIT_PASS


def _why(reasons: list[str]) -> None:
    print("\nEnvironment is not ready:")
    for reason in reasons:
        print(f"  - {reason}")
    print(
        f"\nSet {PROOF_REPO_ENV} to a repository you are willing to have this command push "
        "a branch to and open a pull request on. There is no default on purpose: reaching "
        "off this machine is the author's decision, not this script's."
    )


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
