#!/usr/bin/env python3
"""Proof for `0055_the-pr-body-is-not-taken-from-pr-md`.

The plan's eight claims, C1-C8: after a `pr` step that was not stopped, the pull request's
title and body are the ones `cos.mjs pr-text` cuts from `pr.md` — whether the step found
the pull request open (C1) or opened it itself with `gh pr create --fill-first` (C2);
nothing is written when they already match (C3); a stopped step (C4) and a `pr.md` that is
`draft` or names no pull request (C5) call no `gh` for it; a refusal leaves the step
`done`, one `failed` row with gh's words and `pr.md` byte for byte (C6); `status --json`
is what the `cos.mjs` at the merge-base prints (C7); and the terminal line in
`write-pr/SKILL.md` sends `gh pr edit` the same argv and stdin the app sent in C1 (C8).

    0  every claim held
    1  at least one did not
    2  the environment could not answer: no `node`, `uv` or `git`, or no merge-base with
       `origin/main`

**No session, no quota, no network.** `gh` is a fake: a Python script first on `PATH` that
keeps one pull request's title and body in a JSON file, logs every argv and stdin to
`argv.jsonl`, and fails `pr edit` while a flag file exists (a file, not a variable:
`coscc/harness.py` `child_env` passes `gh` only `PATH` and a few others). The `pr` session
is a stand-in that writes `pr.md` itself — and, in C2, runs the fake `gh pr create`. The
remote is a bare directory. Everything is written under one temporary directory, set as
both `COS_DATA_DIR` and `COS_WORKING_DIR` before the app is imported, so `~/.cos` is never
opened.

**This does not measure the outcome in `intent.md`.** That is a deliberate trial on the real
board before 2026-10-15 — a pull request opened by hand with a temporary description, a
`pr` step, and a person comparing GitHub with `pr.md` — plus every `pr-sync` row with
`existed: true` until then.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.proof_harness import EXIT_BROKEN, EXIT_ENV, EXIT_PASS, REPO, say  # noqa: E402

PR_URL = "https://github.com/example/proof/pull/7"
SLUG = "a-description-the-proof-invented"
BRANCH = f"fix/{SLUG}"
COS = REPO / ".claude" / "scripts" / "cos.mjs"
SKILL = REPO / ".claude" / "skills" / "write-pr" / "SKILL.md"
TITLE = "the description is the proof's own"

FAKE_GH = r'''#!{python}
"""A fake gh for verify_0055. State lives beside this file."""
import json, os, subprocess, sys
here = os.path.dirname(os.path.abspath(__file__))
argv = sys.argv[1:]
stdin = sys.stdin.read() if "-" in argv else None
with open(os.path.join(here, "argv.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps({{"argv": argv, "stdin": stdin}}) + "\n")
store = os.path.join(here, "pr.json")
pr = json.load(open(store, encoding="utf-8")) if os.path.exists(store) else None
def save():
    json.dump(pr, open(store, "w", encoding="utf-8"))
if argv[:2] == ["pr", "list"]:
    print(json.dumps([{{"url": "{url}", "number": 7, "mergeable": "MERGEABLE",
                       "headRefOid": "0" * 40}}] if pr else []))
elif argv[:2] == ["pr", "create"] and "--fill-first" in argv:
    subject = subprocess.run(["git", "log", "--reverse", "--format=%s"], capture_output=True,
                             text=True).stdout.splitlines()[0]
    body = open(argv[argv.index("--body-file") + 1], encoding="utf-8").read()
    pr = {{"title": subject, "body": body}}
    save()
    print("{url}")
elif argv[:2] == ["pr", "view"] and argv[-1] == "title,body" and pr:
    print(json.dumps(pr))
elif argv[:2] == ["pr", "edit"] and pr:
    if os.path.exists(os.path.join(here, "FAIL")):
        sys.stderr.write("HTTP 422: the fake GitHub refused the edit\n")
        sys.exit(1)
    for a in argv:
        if a.startswith("--title="):
            pr["title"] = a[len("--title="):]
    pr["body"] = stdin
    save()
    print("{url}")
else:
    sys.stderr.write("the fake gh does not know: " + " ".join(argv) + "\n")
    sys.exit(2)
'''


def pr_md(status: str = "accepted", url: bool = True) -> str:
    head = f"PR: {PR_URL}. " if url else ""
    return (
        f"# PR: {TITLE}\n"
        f"Intent: intent.md. Impl: impl.md. {head}Author: verify_0055. Status: {status}.\n\n"
        f"## Where\n\n{PR_URL if url else '(none yet)'}, branch `{BRANCH}`, checks pending.\n\n"
        "## Scope of the diff\n\nnothing: the proof invented it.\n\n"
        "## What a reviewer should look at first\n\nStatus: a later mention stays in the body.\n"
    )


def require_environment() -> None:
    for tool in ("node", "uv", "git"):
        if shutil.which(tool) is None:
            print(f"no {tool} on PATH — this proof cannot answer without it")
            raise SystemExit(EXIT_ENV)


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=proof", "-c", "user.email=proof@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout.strip()


def pr_text(units_root: Path, unit: str) -> dict:
    done = subprocess.run(["node", str(COS), "--root", str(units_root), "pr-text", unit],
                          capture_output=True, text=True, timeout=60)
    return json.loads(done.stdout) if done.returncode == 0 else {"error": done.stderr}


class Calls:
    """What the fake `gh` was asked, from a mark on."""

    def __init__(self, fakebin: Path):
        self.log = fakebin / "argv.jsonl"
        self.mark = 0

    def all(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def start(self) -> None:
        self.mark = len(self.all())

    def since(self, sub: str | None = None) -> list[dict]:
        calls = self.all()[self.mark:]
        return [c for c in calls if sub is None or c["argv"][:2] == ["pr", sub]]


async def run(root: Path, fakebin: Path) -> tuple[bool, dict, Path, str]:
    from coscc.api import build
    from coscc.config import Config

    remote = root / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    workspace = root / "work" / "proj"
    workspace.mkdir(parents=True)
    git(workspace, "init", "-q", "-b", "main")
    (workspace / "README.md").write_text("proof\n", encoding="utf-8")
    git(workspace, "add", "-A")
    git(workspace, "commit", "-q", "-m", "the first commit's subject")
    git(workspace, "remote", "add", "origin", str(remote))
    git(workspace, "push", "-q", "origin", "main")
    git(workspace, "branch", BRANCH)

    cwd = str(workspace)
    app = build(Config(workspaces=(cwd,), working_dir=str(root / "work"), data_dir=str(root / "data")))
    service = app.state.service
    made = await service.create_unit(cwd, SLUG, "verify_0055 fixture")
    unit, directory = made["unit"], Path(made["path"])
    units_root = service._units_root(cwd)
    # Fixture data, written before anything is measured, in a unit that lives in a
    # temporary directory and dies with it.
    for name, title in (("intent.md", "Intent"), ("spec.md", "Spec"), ("plan.md", "Plan"),
                        ("impl.md", "Impl")):
        extra = " Type: fix." if name == "intent.md" else ""
        (directory / name).write_text(
            f"# {title}: a description the proof invented\nAuthor: verify_0055.{extra} Status: accepted.\n",
            encoding="utf-8",
        )

    class PrSession:
        """A `pr` session: writes `pr.md`; may open the pull request first; may wait to be stopped."""

        text = pr_md()
        create = False
        hold = False

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            step = kw.get("step")
            try:
                if self.create:
                    draft = directory / "pr.md"
                    draft.write_text(pr_md("draft", url=False), encoding="utf-8")
                    subprocess.run(["gh", "pr", "create", "--fill-first", "--body-file", str(draft)],
                                   cwd=cwd, capture_output=True, text=True, check=True)
                (directory / "pr.md").write_text(self.text, encoding="utf-8")
                yield ("chunk", "pr.md written")
                if self.hold:
                    await asyncio.sleep(30)
                yield ("done", {"session_id": "verify-0055", "cost": {}})
            finally:
                if step is not None:
                    await step.close()

    session = PrSession()
    service.sessions = session
    calls = Calls(fakebin)
    state = fakebin / "pr.json"

    def set_pr(pr: dict | None) -> None:
        if pr is None:
            state.unlink(missing_ok=True)
        else:
            state.write_text(json.dumps(pr), encoding="utf-8")

    def get_pr() -> dict | None:
        return json.loads(state.read_text(encoding="utf-8")) if state.exists() else None

    def rows() -> list[dict]:
        return service._journal().records(service._journal_key(cwd), unit, kind="pr-sync")

    async def run_pr(stop: bool = False) -> dict:
        calls.start()
        agen = service.run_step(cwd, unit, "pr")
        out = [await agen.__anext__()]
        if stop:
            await service.stop_step(cwd, unit, "verify_0055")
        out += [i async for i in agen]
        return out[-1][1] if out[-1][0] == "done" else {"outcome": f"no done: {out[-1]}"}

    ok = True
    want = pr_text(units_root, unit) if (directory / "pr.md").exists() else None

    # C2 (R3 b): no pull request before the step; the step opens it with --fill-first.
    set_pr(None)
    session.create = True
    done = await run_pr()
    session.create = False
    want = pr_text(units_root, unit)
    after = get_pr()
    ok &= say(
        done.get("outcome") == "done"
        and after == {"title": want.get("title"), "body": want.get("body")}
        and len(calls.since("create")) == 1 and len(calls.since("edit")) == 1
        and rows()[-1:] and rows()[-1]["outcome"] == "updated" and rows()[-1]["existed"] is False,
        "C2 a pull request the step opened with --fill-first ends with pr.md's title and body",
        f"done={done.get('outcome')}, pr={after}, want={want}, rows={rows()}",
    )

    # C3 (R3 c): the same pr.md again: nothing to write.
    n = len(rows())
    done = await run_pr()
    ok &= say(
        done.get("outcome") == "done" and calls.since("edit") == []
        and len(rows()) == n + 1 and rows()[-1]["outcome"] == "already",
        "C3 a title and body that already match are not written again",
        f"edits={calls.since('edit')}, rows={rows()[n:]}",
    )

    # C1 (R3 a): a person put a temporary description on an open pull request.
    set_pr({"title": "temporary", "body": "a temporary line"})
    done = await run_pr()
    after = get_pr()
    app_edit = calls.since("edit")
    ok &= say(
        done.get("outcome") == "done"
        and after == {"title": want["title"], "body": want["body"]}
        and len(app_edit) == 1
        and app_edit[0]["argv"] == ["pr", "edit", PR_URL, f"--title={want['title']}", "--body-file", "-"]
        and app_edit[0]["stdin"] == want["body"]
        and rows()[-1]["outcome"] == "updated" and rows()[-1]["existed"] is True
        and done.get("pr_sync", {}).get("outcome") == "updated",
        "C1 an open pull request with a temporary description gets pr.md's title and body, once",
        f"pr={after}, edits={app_edit}, row={rows()[-1:]}, done={done.get('pr_sync')}",
    )
    body_ok = "Status: accepted" not in want["body"] and not want["body"].startswith("# PR:") \
        and want["body"].startswith("## Where") and "Status: a later mention" in want["body"]
    ok &= say(body_ok, "C1 the body drops the header and the # PR: line and keeps a later Status:",
              repr(want["body"][:120]))

    # C4 (R3 d): a stopped step calls no gh for this.
    set_pr({"title": "temporary", "body": "a temporary line"})
    n = len(rows())
    session.hold = True
    done = await run_pr(stop=True)
    session.hold = False
    ok &= say(
        done.get("outcome") == "stopped" and calls.since("view") == [] and calls.since("edit") == []
        and len(rows()) == n and get_pr()["body"] == "a temporary line",
        "C4 a stopped step asks gh nothing about the title or body and writes no pr-sync row",
        f"done={done.get('outcome')}, calls={calls.since()}, rows={rows()[n:]}",
    )

    # C5 (R3 e): draft, or no PR: in the header.
    skipped = []
    for text in (pr_md("draft"), pr_md(url=False)):
        session.text = text
        n = len(rows())
        done = await run_pr()
        skipped.append((done.get("outcome"), calls.since("view"), calls.since("edit"),
                        [r["outcome"] for r in rows()[n:]], bool(rows()[-1].get("detail"))))
    session.text = pr_md()
    ok &= say(
        skipped == [("done", [], [], ["skipped"], True)] * 2,
        "C5 a draft pr.md, or one naming no pull request, is skipped with no gh call and says why",
        f"{skipped}",
    )

    # C6 (R5): GitHub refuses the edit.
    set_pr({"title": "temporary", "body": "a temporary line"})
    (fakebin / "FAIL").write_text("")
    done = await run_pr()
    (fakebin / "FAIL").unlink()
    written = (directory / "pr.md").read_bytes()
    ok &= say(
        done.get("outcome") == "done" and rows()[-1]["outcome"] == "failed"
        and "HTTP 422" in rows()[-1].get("detail", "")
        and written == pr_md().encode("utf-8"),
        "C6 a refused edit leaves the step done, one failed row with gh's words, pr.md byte for byte",
        f"done={done.get('outcome')}, row={rows()[-1:]}",
    )
    return bool(ok), app_edit[0] if app_edit else {}, units_root, unit


def c7(units_root: Path) -> bool:
    """C7 (R2): `status --json` from this `cos.mjs` and from the merge-base's, byte for byte."""
    base = subprocess.run(["git", "merge-base", "HEAD", "origin/main"], capture_output=True,
                          text=True, cwd=str(REPO))
    if base.returncode != 0:
        print(f"no merge-base with origin/main — C7 cannot answer: {base.stderr.strip()}")
        raise SystemExit(EXIT_ENV)
    sha = base.stdout.strip()
    old_text = subprocess.run(["git", "show", f"{sha}:.claude/scripts/cos.mjs"],
                              capture_output=True, text=True, cwd=str(REPO))
    if old_text.returncode != 0:
        print(f"cannot read cos.mjs at {sha} — C7 cannot answer: {old_text.stderr.strip()}")
        raise SystemExit(EXIT_ENV)
    with tempfile.TemporaryDirectory(prefix="verify-0055-old-") as d:
        old = Path(d) / ".claude" / "scripts" / "cos.mjs"
        old.parent.mkdir(parents=True)
        old.write_text(old_text.stdout, encoding="utf-8")
        outs = [
            subprocess.run(["node", str(script), "--root", str(units_root), "status", "--json"],
                           capture_output=True, text=True, timeout=60).stdout
            for script in (COS, old)
        ]
    return say(outs[0] == outs[1] and bool(outs[0]),
               f"C7 status --json is byte for byte what cos.mjs at {sha[:7]} prints",
               f"{len(outs[0])} vs {len(outs[1])} bytes")


def terminal_line() -> str:
    """The code block right after step 5's heading in `write-pr/SKILL.md`."""
    text = SKILL.read_text(encoding="utf-8")
    at = text.find("5. **Put pr.md onto the pull request.**")
    m = re.search(r"```\n(.*?)\n\s*```", text[at:], re.S) if at != -1 else None
    if m is None:
        return ""
    return "\n".join(line[3:] if line.startswith("   ") else line for line in m.group(1).splitlines())


def c8(root: Path, fakebin: Path, units_root: Path, unit: str, app_edit: dict) -> bool:
    """C8 (R7): the terminal line sends `gh pr edit` what the app sent in C1."""
    line = terminal_line()
    # A repository laid out as the line expects: this cos.mjs, and the unit's pr.md in `.cos/`.
    term = root / "terminal"
    (term / ".claude" / "scripts").mkdir(parents=True)
    shutil.copy(COS, term / ".claude" / "scripts" / "cos.mjs")
    (term / ".cos" / unit).mkdir(parents=True)
    shutil.copy(units_root / ".cos" / unit / "pr.md", term / ".cos" / unit / "pr.md")
    (fakebin / "pr.json").write_text(json.dumps({"title": "temporary", "body": "a temporary line"}),
                                     encoding="utf-8")
    calls = Calls(fakebin)
    calls.start()
    done = subprocess.run(["bash", "-c", line.replace("<NNNN_slug>", unit)], cwd=term,
                          capture_output=True, text=True, timeout=60)
    edits = calls.since("edit")
    same = len(edits) == 1 and bool(app_edit) and edits[0] == app_edit
    return say(
        bool(line) and done.returncode == 0 and same,
        "C8 the terminal line in write-pr/SKILL.md sends gh pr edit the argv and stdin the app sent",
        f"line={line!r}, exit={done.returncode}, stderr={done.stderr.strip()!r}, "
        f"terminal={edits}, app={app_edit}",
    )


def main() -> int:
    require_environment()
    with tempfile.TemporaryDirectory(prefix="verify-0055-") as d:
        root = Path(d)
        fakebin = root / "fakebin"
        fakebin.mkdir()
        gh = fakebin / "gh"
        gh.write_text(FAKE_GH.format(python=sys.executable, url=PR_URL), encoding="utf-8")
        gh.chmod(0o755)
        # Before `coscc` is imported: `coscc/state.py` builds an app from the environment
        # at import, and that app must open this directory, not `~/.cos`.
        os.environ["PATH"] = f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}"
        os.environ["COS_DATA_DIR"] = str(root / "data")
        os.environ["COS_WORKING_DIR"] = str(root / "work")
        for name, fallback in (("COS_PORT", "8790"), ("COS_HOST", "127.0.0.1")):
            if not os.environ.get(name, "").strip():
                os.environ[name] = fallback
        print(f"temporary data root: {root}")
        ok, app_edit, units_root, unit = asyncio.run(run(root, fakebin))
        ok = c7(units_root) and ok
        ok = c8(root, fakebin, units_root, unit, app_edit) and ok
        return EXIT_PASS if ok else EXIT_BROKEN


if __name__ == "__main__":
    raise SystemExit(main())
