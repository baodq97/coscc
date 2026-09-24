#!/usr/bin/env python3
"""Proof for the store's `0042_a-plan-goes-stale-when-another-unit-merges` (plan step 7).

`idea.md`'s situation, rebuilt: a plan is written on commit A, another unit merges B, which
changes two files the plan names and one it does not, and then `impl` starts. The `impl`
prompt must name exactly those two files, and the `plan_drift` its `start` record carries
must equal the intent's own check — `git diff <plan>..<main> --name-only`, run here by
subprocess, intersected with the paths this script wrote into `## Files that change`.

    0  every claim held
    1  at least one did not
    2  the environment could not answer: no `node`, `uv` or `git`

**No session, no quota, no network.** The session is a stand-in that counts its calls and
keeps each prompt. `gh` is a script that always exits 1. `origin` is a bare repository in
the same temporary directory, which is both `COS_DATA_DIR` and `COS_WORKING_DIR` before
`coscc` is imported, so `~/.cos` is never opened.

**What this does not prove** (plan `## Proof`): the intent's outcome counted over the
calendar to 2026-10-31 (spec C8), and that a real session stops on a contradiction (Risk 8).
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

FAKE_GH = "#!/bin/sh\necho 'the fake gh answers nothing' >&2\nexit 1\n"
HEADING = "# The files main changed since the plan"
FILES = ("src/a.py", "src/b.py", "src/c.py", "lib/src/a.py", "docs/x.md")
# What this script writes into P's `## Files that change` — the intent's check intersects
# the diff with these, and nothing the app computed.
P_NAMES = ("src/a.py", "src/b.py", "src/c.py")
P_PLAN = """# Plan: {slug}
Intent: intent.md. Spec: spec.md. Author: verify_0042. Status: accepted.

## Files that change

| Path | What |
|---|---|
| `src/a.py` | one |
| `src/b.py` | two |
| `src/c.py:10-20` | three, with lines |

## Order of work

1. Everything.
"""
# Appended the way the app appends: a reply's own `## Answers` never reaches disk (`0025`).
P_ANSWERS = "\n## Answers\n\n### Câu 1\nAnswered by: verify_0042.\n\nSee `docs/x.md`.\n"
Q_PLAN = """# Plan: {slug}
Intent: intent.md. Spec: spec.md. Author: verify_0042. Status: accepted.

## Files that change

- `src/c.py`: the only one.
"""


def require_environment() -> None:
    for tool in ("node", "uv", "git"):
        if shutil.which(tool) is None:
            print(f"no {tool} on PATH — this proof cannot answer without it")
            raise SystemExit(EXIT_ENV)


def git(cwd: Path | str, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def write(root: Path, names, text: str) -> None:
    for name in names:
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


class StandIn:
    """`Sessions.stream`'s shape. Replies with `self.reply`, and remembers what it saw."""

    def __init__(self):
        self.reply = ""
        self.calls = 0
        self.prompts: list[str] = []
        self.main_seen: list[str] = []

    async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
        self.calls += 1
        self.prompts.append(text)
        self.main_seen.append(git(cwd, "rev-parse", "refs/remotes/origin/main"))
        yield ("chunk", self.reply)
        yield ("done", {"session_id": f"sess-{self.calls}", "text": self.reply,
                        "cost": {"turns": 1, "cost_usd": 0.0}, "terminal_reason": "success"})


def section_of(prompt: str) -> str:
    if HEADING not in prompt:
        return ""
    return prompt.split(HEADING, 1)[1].split("\n# ", 1)[0]


async def run(root: Path) -> bool:
    import httpx

    from coscc.api import build
    from coscc.config import Config
    from coscc.journal import Journal

    remote = root / "remote.git"
    git(root, "init", "-q", "--bare", "-b", "main", str(remote))
    workspace = root / "work" / "proj"
    workspace.mkdir(parents=True)
    git(workspace, "init", "-q", "-b", "main")
    write(workspace, FILES, "A\n")
    git(workspace, "add", "-A")
    git(workspace, "commit", "-q", "-m", "A")
    a_sha = git(workspace, "rev-parse", "HEAD")
    git(workspace, "remote", "add", "origin", str(remote))
    git(workspace, "push", "-q", "-u", "origin", "main")

    cwd = str(workspace)
    app = build(Config(workspaces=(cwd,), working_dir=str(root / "work"),
                       data_dir=str(root / "data")))
    service = app.state.service
    stand_in = StandIn()
    service.sessions = stand_in
    journal = Journal(str(root / "work"), str(root / "data"))
    key = service._journal_key(cwd)

    units = {}
    for name, slug in (("P", "a-proof-plan-overtaken"), ("Q", "a-proof-plan-untouched")):
        made = await service.create_unit(cwd, slug, "verify_0042 fixture")
        d = Path(made["path"])
        (d / "intent.md").write_text(
            f"# Intent: {slug}\nAuthor: verify_0042. Type: feat. Status: accepted.\n", encoding="utf-8"
        )
        (d / "spec.md").write_text(
            f"# Spec: {slug}\nAuthor: verify_0042. Status: accepted.\n", encoding="utf-8"
        )
        units[name] = (made["unit"], d, slug)

    ok = True
    presses = 0
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proof", timeout=300
    ) as client:

        async def press(unit: str, stage: str) -> dict:
            nonlocal presses
            presses += 1
            got = await client.post("/api/board/run", json={"cwd": cwd, "unit": unit, "stage": stage})
            lines = [json.loads(x) for x in got.text.splitlines() if x.strip()]
            done = [x for x in lines if x.get("type") in ("done", "error")]
            if got.status_code != 200 or not done:
                return {"type": "refused", "status": got.status_code, "body": got.text[:400]}
            return done[-1]

        def starts(unit: str, stage: str | None = None) -> list[dict]:
            return [r for r in journal.records(key, unit, kind="start")
                    if stage is None or r.get("stage") == stage]

        p_unit, p_dir, p_slug = units["P"]
        q_unit, q_dir, q_slug = units["Q"]

        # 1-2. Both plans are written on A.
        stand_in.reply = P_PLAN.format(slug=p_slug)
        p_plan = await press(p_unit, "plan")
        stand_in.reply = Q_PLAN.format(slug=q_slug)
        q_plan = await press(q_unit, "plan")
        with (p_dir / "plan.md").open("a", encoding="utf-8") as f:
            f.write(P_ANSWERS)

        # 3. Another unit merges B from a second clone.
        other = root / "other"
        git(root, "clone", "-q", str(remote), str(other))
        write(other, ("src/a.py", "src/b.py", "lib/src/a.py", "docs/x.md"), "B\n")
        git(other, "add", "-A")
        git(other, "commit", "-q", "-m", "B: another unit merged")
        git(other, "push", "-q", "origin", "main")
        b_sha = git(other, "rev-parse", "HEAD")

        # 4-5. `impl` starts for both.
        stand_in.reply = "working"
        await press(p_unit, "impl")
        p_prompt, p_main_seen = stand_in.prompts[-1], stand_in.main_seen[-1]
        await press(q_unit, "impl")
        q_prompt = stand_in.prompts[-1]

        p_plan_start = starts(p_unit, "plan")[-1] if starts(p_unit, "plan") else {}
        ok &= say(p_plan.get("outcome") == "done" and q_plan.get("outcome") == "done"
                  and p_plan_start.get("head") == a_sha,
                  "(a) P's plan run is done and its start record's head is A",
                  f"outcomes={p_plan.get('outcome')}/{q_plan.get('outcome')}, "
                  f"head={p_plan_start.get('head')}, A={a_sha}")

        listed = [line for line in section_of(p_prompt).splitlines() if line.startswith("- ")]
        named = sorted(f for f in FILES if any(f"`{f}`" in line for line in listed))
        ok &= say(named == ["src/a.py", "src/b.py"] and len(listed) == 2
                  and "lib/src/a.py" not in p_prompt and "docs/x.md" not in section_of(p_prompt),
                  "(b) P's impl prompt names exactly src/a.py and src/b.py",
                  f"listed={listed}")
        print(f"      (section lines: {listed})")

        p_impl = starts(p_unit, "impl")[-1] if starts(p_unit, "impl") else {}
        drift = p_impl.get("plan_drift") or {}
        diff = git(workspace, "diff", f"{drift.get('plan_sha')}..{drift.get('main_sha')}",
                   "--name-only").splitlines() if drift.get("plan_sha") and drift.get("main_sha") else []
        want = sorted((d for d in diff if d in P_NAMES), key=lambda s: s.encode())
        ok &= say(drift.get("checked") is True and drift.get("files") == want
                  and drift.get("plan_sha") == a_sha
                  and drift.get("main_sha") == p_main_seen == b_sha,
                  "(c) P's plan_drift equals the intent's check: git diff ∩ the section",
                  f"drift={drift}, want={want}, main seen in tree={p_main_seen}, B={b_sha}")
        print(f"      (git diff {a_sha[:7]}..{b_sha[:7]} --name-only: {diff})")

        q_impl = starts(q_unit, "impl")[-1] if starts(q_unit, "impl") else {}
        q_drift = q_impl.get("plan_drift") or {}
        ok &= say(q_drift.get("checked") is True and q_drift.get("files") == []
                  and HEADING not in q_prompt,
                  "(d) Q's plan names nothing B changed: checked, empty, no heading",
                  f"drift={q_drift}, heading={HEADING in q_prompt}")

        every = starts(p_unit) + starts(q_unit)
        ok &= say(stand_in.calls == presses == 4 and len(every) == 4
                  and all(r.get("agents") == 1 for r in every),
                  "(e) one stand-in call per press, and agents == 1 on every start record",
                  f"calls={stand_in.calls}, presses={presses}, starts={len(every)}, "
                  f"agents={[r.get('agents') for r in every]}")
    return bool(ok)


def main() -> int:
    require_environment()
    with tempfile.TemporaryDirectory(prefix="verify-0042-") as d:
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
