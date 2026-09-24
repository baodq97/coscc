#!/usr/bin/env python3
"""Proof for the store's `0019_a-failed-step-destroys-the-work-that-succeeded` (plan step 8).

A step that ends without its artifact — at the turn ceiling, at the budget ceiling, or on
an exception — leaves one `attempt` record in the run log, the board tells it apart from
a stage that never ran, and the next run of that stage is handed the record in its
prompt. The record is checked against what `git log` and `git status` said at the moment
the step stopped, and against the run log's own `end` record.

    0  every claim held
    1  at least one did not
    2  the environment could not answer: no `node`, `uv` or `git`

**No session, no quota, no network.** The session is a stand-in that runs real `git` in
the unit's worktree and then stops the way it is told to; the transcript it "left" is a
fake `get_session_messages`. `gh` is a script that always exits 1. `origin` is a bare
repository in the same temporary directory, which is both `COS_DATA_DIR` and
`COS_WORKING_DIR` before `coscc` is imported, so `~/.cos` is never opened.

**What this does not prove** (plan `## Proof`): that a real `impl` stopped by the budget
ceiling reports the `terminal_reason` the stand-in uses (C7), that a real transcript is
flushed before it is read, and that the excerpt holds the measurements a rerun needs (C3 —
`scripts/measure_0019_excerpt.py` measured it does not, at 8000).
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
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.proof_harness import EXIT_BROKEN, EXIT_ENV, EXIT_PASS, say  # noqa: E402

FAKE_GH = "#!/bin/sh\necho 'the fake gh answers nothing' >&2\nexit 1\n"
PROMPT_MARK = "PROMPT-TEXT-THAT-IS-NOT-WORK-0019"
CANARY = "CANARY-0019-EXCERPT"


def require_environment() -> None:
    for tool in ("node", "uv", "git"):
        if shutil.which(tool) is None:
            print(f"no {tool} on PATH — this proof cannot answer without it")
            raise SystemExit(EXIT_ENV)


def git(cwd: Path | str, *args: str, strip: bool = True) -> str:
    out = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout
    return out.strip() if strip else out.rstrip("\n")


def truth(cwd: str) -> dict:
    """What git itself says at the moment a stand-in is about to stop."""
    base = git(cwd, "merge-base", "HEAD", "refs/remotes/origin/main")
    log = git(cwd, "log", "--format=%H %s", f"{base}..HEAD", strip=False)
    status = git(cwd, "status", "--porcelain", strip=False)
    return {
        "head": git(cwd, "rev-parse", "HEAD"),
        "log": log.splitlines() if log else [],
        "status": status.splitlines() if status else [],
    }


def commit(cwd: str, name: str, body: str, message: str) -> None:
    Path(cwd, name).write_text(body, encoding="utf-8")
    git(cwd, "add", name)
    git(cwd, "commit", "-q", "-m", message)


def transcript(session_id: str, measured: str) -> list:
    import claude_agent_sdk as sdk

    def msg(kind, content, n):
        return sdk.SessionMessage(
            type=kind, uuid=f"{session_id}-{n}", session_id=session_id,
            message={"role": kind, "content": content},
            parent_tool_use_id=None, parent_agent_id=None,
        )

    return [
        msg("user", f"{PROMPT_MARK} do the work", 1),
        msg("assistant", [{"type": "text", "text": f"measuring for {session_id}"},
                          {"type": "tool_use", "id": "t1", "name": "Bash",
                           "input": {"command": "npm test"}}], 2),
        msg("user", [{"type": "tool_result", "tool_use_id": "t1", "content": measured}], 3),
        msg("assistant", [{"type": "text", "text": f"{CANARY} {session_id} not yet written"}], 4),
    ]


class StandIn:
    """`Sessions.stream`'s shape. Each call runs the next scripted step."""

    def __init__(self):
        self.script: list = []
        self.prompts: list[str] = []
        self.cwds: list[str] = []

    async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
        self.prompts.append(text)
        self.cwds.append(cwd)
        step = self.script.pop(0)
        async for item in step(cwd):
            yield item


def fixture(directory: Path, upto: tuple[str, ...], slug: str) -> None:
    titles = {"intent.md": "Intent", "spec.md": "Spec", "plan.md": "Plan"}
    for name in upto:
        extra = " Type: fix." if name == "intent.md" else ""
        (directory / name).write_text(
            f"# {titles[name]}: {slug}\nAuthor: verify_0019.{extra} Status: accepted.\n",
            encoding="utf-8",
        )


def unit_files(directory: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir()) if p.is_file()}


async def run(root: Path) -> bool:
    import httpx

    from coscc import sessions as sessions_mod
    from coscc.api import build
    from coscc.config import Config
    from coscc.journal import Journal
    from coscc.state import _cell_label

    remote = root / "remote.git"
    git(root, "init", "-q", "--bare", "-b", "main", str(remote))
    workspace = root / "work" / "proj"
    workspace.mkdir(parents=True)
    git(workspace, "init", "-q", "-b", "main")
    commit(str(workspace), "README.md", "proof\n", "the first commit")
    git(workspace, "remote", "add", "origin", str(remote))
    git(workspace, "push", "-q", "-u", "origin", "main")
    git(workspace, "fetch", "-q", "origin")

    cwd = str(workspace)
    app = build(Config(workspaces=(cwd,), working_dir=str(root / "work"),
                       data_dir=str(root / "data")))
    service = app.state.service
    stand_in = StandIn()
    service.sessions = stand_in
    transcripts: dict[str, list] = {}
    journal = Journal(str(root / "work"), str(root / "data"))
    key = service._journal_key(cwd)

    def fake_messages(session_id, directory=None, **kw):
        return transcripts.get(session_id, [])

    units = {}
    for name, slug, upto in (
        ("A", "a-proof-impl-that-stops", ("intent.md", "spec.md", "plan.md")),
        ("B", "a-proof-spec-that-stops", ("intent.md",)),
        ("C", "a-proof-detached-tree", ("intent.md", "spec.md", "plan.md")),
    ):
        made = await service.create_unit(cwd, slug, "verify_0019 fixture")
        fixture(Path(made["path"]), upto, slug)
        units[name] = (made["unit"], Path(made["path"]), slug)

    ok = True
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proof", timeout=300
    ) as client:

        async def press(unit: str, stage: str) -> dict:
            got = await client.post("/api/board/run", json={"cwd": cwd, "unit": unit, "stage": stage})
            lines = [json.loads(x) for x in got.text.splitlines() if x.strip()]
            done = [x for x in lines if x.get("type") in ("done", "error")]
            if got.status_code != 200 or not done:
                return {"type": "refused", "status": got.status_code, "body": got.text[:400]}
            return done[-1]

        def attempts(unit: str, stage: str) -> tuple[list, list]:
            recs = [r for r in journal.records(key, unit) if r.get("stage") == stage]
            return ([r for r in recs if r.get("kind") == "attempt"],
                    [r for r in recs if r.get("kind") == "end"])

        def matches(att: dict, seen: dict) -> tuple[bool, bool]:
            logged = [f"{c['sha']} {c['subject']}" for c in att.get("commits") or []]
            return logged == seen["log"], (att.get("status") or []) == seen["status"]

        def earlier_lines(prompt: str) -> int:
            if "Earlier attempts before that one" not in prompt:
                return 0
            tail = prompt.split("Earlier attempts before that one", 1)[1]
            section = tail.split("\n# ", 1)[0]
            return sum(1 for line in section.splitlines() if line.startswith("at "))

        with mock.patch.object(sessions_mod.sdk, "get_session_messages", fake_messages):
            # ---------------- unit A, `impl`, three ways of stopping, then done --------
            a_unit, a_dir, a_slug = units["A"]
            seen: dict[int, dict] = {}
            before_after: dict[int, tuple] = {}

            async def a1(tree):
                git(tree, "switch", "-q", "-c", f"fix/{a_slug}")
                commit(tree, "one.txt", "1\n", "a1: first commit")
                commit(tree, "two.txt", "2\n", "a1: second commit")
                Path(tree, "untracked.txt").write_text("u\n", encoding="utf-8")
                transcripts["sess-a1"] = transcript("sess-a1", "MEASURED a1: 41 tests")
                seen[1] = truth(tree)
                yield ("chunk", "working")
                yield ("done", {"session_id": "sess-a1", "text": "working",
                                "cost": {"turns": 121, "cost_usd": 6.88},
                                "terminal_reason": "max_turns"})

            async def a2(tree):
                before_after[2] = (git(tree, "rev-parse", "HEAD"), truth(tree)["status"])
                commit(tree, "three.txt", "3\n", "a2: third commit")
                Path(tree, "one.txt").write_text("1 changed\n", encoding="utf-8")
                transcripts["sess-a2"] = transcript("sess-a2", "MEASURED a2: 42 tests")
                seen[2] = truth(tree)
                yield ("done", {"session_id": "sess-a2", "text": "",
                                "cost": {"turns": 60, "cost_usd": 8.01},
                                "terminal_reason": "error_max_budget_usd"})

            async def a3(tree):
                before_after[3] = (git(tree, "rev-parse", "HEAD"), truth(tree)["status"])
                yield ("session", "sess-a3")
                commit(tree, "four.txt", "4\n", "a3: fourth commit")
                transcripts["sess-a3"] = transcript("sess-a3", "MEASURED a3: 43 tests")
                seen[3] = truth(tree)
                raise RuntimeError("verify_0019: the stream broke")
                yield  # pragma: no cover — makes this an async generator

            async def a4(tree):
                before_after[4] = (git(tree, "rev-parse", "HEAD"), truth(tree)["status"])
                (a_dir / "impl.md").write_text(
                    f"# Impl: {a_slug}\nAuthor: verify_0019. Status: accepted.\n", encoding="utf-8"
                )
                yield ("done", {"session_id": "sess-a4", "text": "",
                                "cost": {"turns": 5, "cost_usd": 0.1}, "terminal_reason": "success"})

            stand_in.script = [a1, a2, a3, a4]
            store_before = unit_files(a_dir)
            outcomes = [await press(a_unit, "impl")]
            store_after_1 = unit_files(a_dir)
            outcomes.append(await press(a_unit, "impl"))
            outcomes.append(await press(a_unit, "impl"))
            board = (await client.get("/api/board", params={"cwd": cwd})).json()
            timeline_json = (await client.get("/api/timeline", params={"cwd": cwd, "unit": a_unit})).text
            outcomes.append(await press(a_unit, "impl"))
            a_prompts = list(stand_in.prompts)

            atts, ends = attempts(a_unit, "impl")
            ways = {1: "max_turns", 2: "budget", 3: "exception"}
            want = {1: ("exhausted", "max_turns", None),
                    2: ("exhausted", "error_max_budget_usd", None),
                    3: ("failed", None, "RuntimeError")}
            ok &= say(len(atts) == 3 and len(ends) == 4,
                      "three stops leave three attempt records and the done run none",
                      f"attempts={len(atts)}, ends={len(ends)}, outcomes={[o.get('outcome') for o in outcomes]}")
            for i, att in enumerate(atts[:3], start=1):
                outcome, terminal, err = want[i]
                got_err = (att.get("error") or {}).get("type")
                r1a = (att.get("outcome") == outcome and att.get("terminal") == terminal
                       and (got_err == err if err else True))
                if i == 3:
                    r1a = r1a and att.get("turns") is None and att.get("cost_usd") is None \
                        and "the stream broke" in (att.get("error") or {}).get("message", "")
                ok &= say(r1a, f"R1 a {ways[i]}: outcome, terminal and error match how it was stopped",
                          f"{att.get('outcome')}/{att.get('terminal')}/{att.get('error')}/"
                          f"turns={att.get('turns')}")
                ok &= say(att.get("turns") == ends[i - 1].get("turns")
                          and att.get("cost_usd") == ends[i - 1].get("cost_usd"),
                          f"R1 b {ways[i]}: turns and cost equal the end record",
                          f"attempt={att.get('turns')}/{att.get('cost_usd')}, "
                          f"end={ends[i - 1].get('turns')}/{ends[i - 1].get('cost_usd')}")
                log_ok, status_ok = matches(att, seen[i])
                ok &= say(log_ok and att.get("head") == seen[i]["head"],
                          f"R1 d {ways[i]}: commits match git log character for character",
                          f"want={seen[i]['log']}, got={att.get('commits')}")
                ok &= say(status_ok, f"R1 e {ways[i]}: status matches git status --porcelain verbatim",
                          f"want={seen[i]['status']!r}, got={att.get('status')!r}")
                full = "\n".join([f"measuring for sess-a{i}", f"MEASURED a{i}: {40 + i} tests",
                                  f"{CANARY} sess-a{i} not yet written"])
                ex = att.get("excerpt") or ""
                ok &= say(bool(ex) and ex in full and PROMPT_MARK not in ex
                          and att.get("session_id") == f"sess-a{i}",
                          f"R4 {ways[i]}: the excerpt is verbatim from the session's own work",
                          f"session={att.get('session_id')}, excerpt={ex[:80]!r}")
                ok &= say(before_after.get(i + 1, (None, None)) == (seen[i]["head"], seen[i]["status"]),
                          f"R2 {ways[i]}: HEAD and uncommitted files are intact when the next run starts",
                          f"stopped={seen[i]['head'][:7]}/{seen[i]['status']}, "
                          f"next={before_after.get(i + 1)}")
            for i in (1, 2, 3):
                print(f"      ({ways[i]}: {len(seen[i]['log'])} commits, status {seen[i]['status']!r})")
            ok &= say(store_before == store_after_1,
                      "R2 the unit's store directory is byte for byte the same after a failed run",
                      f"{sorted(store_before)} vs {sorted(store_after_1)}")

            [a_row] = [u for u in board["units"] if u["name"] == a_unit]
            rows = {r["stage"]: r for r in a_row["stages"]}
            impl_label, pr_label = _cell_label(rows["impl"]), _cell_label(rows["pr"])
            ok &= say((rows["impl"].get("last_run") or {}).get("outcome") not in (None, "done")
                      and rows["pr"].get("last_run") is None and impl_label != pr_label
                      and rows["impl"]["status"] == "not started",
                      "R5 the board tells a failed impl from a pr that never ran",
                      f"impl={rows['impl'].get('last_run')} {impl_label}, pr={pr_label}")

            starts = [r for r in journal.records(key, a_unit) if r.get("kind") == "start"]
            for n, (prompt, start) in enumerate(zip(a_prompts[1:], starts[1:]), start=1):
                att = atts[n - 1]
                values = [str(att.get("outcome")), att.get("head") or "",
                          *(f"{c['sha']} {c['subject']}" for c in att.get("commits") or []),
                          *(att.get("status") or []), att.get("excerpt") or ""]
                if att.get("turns") is not None:
                    values += [f"Turns: {att['turns']}", f"Cost: {att['cost_usd']} USD"]
                else:
                    values += ["Turns: unknown — the session returned no result"]
                missing = [v for v in values if v not in prompt]
                ok &= say(not missing and earlier_lines(prompt) == n - 1
                          and "last-attempt" in (start.get("included") or []),
                          f"R6 the run after the {ways[n]} stop is told every stored value, "
                          f"with {n - 1} earlier line(s)",
                          f"missing={missing}, earlier={earlier_lines(prompt)}, "
                          f"included={start.get('included')}")

            leaks = {
                "timeline": CANARY in timeline_json,
                "board": CANARY in json.dumps(board),
                "activity_and_usage": CANARY in json.dumps(service.activity_and_usage(cwd), default=str),
            }
            ok &= say(not any(leaks.values()), "R7 no route returns the excerpt", f"{leaks}")

            # ---------------- unit B, `spec`: a prose stage -------------------------------
            b_unit, b_dir, b_slug = units["B"]

            async def b_bad(tree):
                transcripts["sess-b1"] = transcript("sess-b1", "MEASURED b1")
                yield ("chunk", "# Spec: no status line here\n")
                yield ("done", {"session_id": "sess-b1", "text": "", "cost": {"turns": 1, "cost_usd": 0.02},
                                "terminal_reason": "success"})

            def b_good(tree):
                async def step(tree):
                    yield ("chunk", f"# Spec: {b_slug}\nAuthor: verify_0019. Status: accepted.\n")
                    yield ("done", {"session_id": "sess-b", "text": "",
                                    "cost": {"turns": 1, "cost_usd": 0.02}, "terminal_reason": "success"})
                return step(tree)

            stand_in.prompts.clear()
            stand_in.script = [b_bad, b_good, b_good]
            b_out = [await press(b_unit, "spec") for _ in range(3)]
            b_atts, _ = attempts(b_unit, "spec")
            ok &= say([o.get("outcome") for o in b_out] == ["failed", "done", "done"]
                      and len(b_atts) == 1 and "RunError" == (b_atts[0].get("error") or {}).get("type")
                      and "# The attempt before this one" in stand_in.prompts[1]
                      and "# The attempt before this one" not in stand_in.prompts[2],
                      "spec: a reply with no Status line is recorded, the next run is told, "
                      "and the run after a done is not",
                      f"outcomes={[o.get('outcome') for o in b_out]}, attempts={len(b_atts)}")

            # ---------------- unit C, C9: a detached tree is not moved off its commit -----
            c_unit, c_dir, c_slug = units["C"]
            c_seen: dict[str, str] = {}

            async def c1(tree):
                commit(tree, "detached.txt", "d\n", "c1: a commit on a detached tree")
                c_seen["stopped"] = git(tree, "rev-parse", "HEAD")
                c_seen["log"] = truth(tree)["log"]
                yield ("done", {"session_id": "sess-c1", "text": "",
                                "cost": {"turns": 121, "cost_usd": 1.0}, "terminal_reason": "max_turns"})

            async def c2(tree):
                c_seen["started"] = git(tree, "rev-parse", "HEAD")
                c_seen["branch"] = git(tree, "branch", "--show-current")
                yield ("done", {"session_id": "sess-c2", "text": "",
                                "cost": {"turns": 121, "cost_usd": 1.0}, "terminal_reason": "max_turns"})

            stand_in.script = [c1, c2]
            await press(c_unit, "impl")
            commit(cwd, "moved.txt", "m\n", "main moved on after c1 stopped")
            git(cwd, "push", "-q", "origin", "main")
            await press(c_unit, "impl")
            c_atts, _ = attempts(c_unit, "impl")
            ok &= say(c_seen.get("started") == c_seen.get("stopped") and c_seen.get("branch") == ""
                      and c_atts and c_atts[0].get("branch") == "detached"
                      and [f"{c['sha']} {c['subject']}" for c in c_atts[0].get("commits") or []] == c_seen["log"],
                      "C9 a detached tree's own commit is still HEAD when the next run starts, "
                      "after main moved on",
                      f"stopped={c_seen.get('stopped', '')[:7]}, started={c_seen.get('started', '')[:7]}, "
                      f"branch={c_seen.get('branch')!r}, recorded={c_atts[0].get('branch') if c_atts else None}")
    return bool(ok)


def main() -> int:
    require_environment()
    with tempfile.TemporaryDirectory(prefix="verify-0019-") as d:
        root = Path(d)
        fakebin = root / "fakebin"
        fakebin.mkdir()
        gh = fakebin / "gh"
        gh.write_text(FAKE_GH, encoding="utf-8")
        gh.chmod(0o755)
        # Before `coscc` is imported: `coscc/state.py` builds an app from the environment
        # at import, and that app must open this directory, not `~/.cos`.
        os.environ["PATH"] = f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}"
        os.environ["COS_DATA_DIR"] = str(root / "data")
        os.environ["COS_WORKING_DIR"] = str(root / "work")
        for name, value in (("GIT_AUTHOR_NAME", "proof"), ("GIT_COMMITTER_NAME", "proof"),
                            ("GIT_AUTHOR_EMAIL", "proof@example.invalid"),
                            ("GIT_COMMITTER_EMAIL", "proof@example.invalid")):
            os.environ[name] = value
        for name, fallback in (("COS_PORT", "8790"), ("COS_HOST", "127.0.0.1")):
            if not os.environ.get(name, "").strip():
                os.environ[name] = fallback
        print(f"temporary data root: {root}")
        ok = asyncio.run(run(root))
        return EXIT_PASS if ok else EXIT_BROKEN


if __name__ == "__main__":
    raise SystemExit(main())
