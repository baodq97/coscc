#!/usr/bin/env python3
"""Proof for `0015_review-cannot-stop-a-merge`: nine claims, C1-C9, from its `plan.md`.

    0  every claim held
    1  at least one did not
    2  the environment could not answer: no `node`, `git` or `uv`

**No session, no quota, no network.** Every claim runs the real
`.claude/scripts/cos.mjs` of this checkout. The repository it gates is a temporary git
repository whose `origin` is a bare directory beside it, and `gh` is a fake placed first on
`PATH` that prints a canned `gh pr checks --json` answer. Nothing talks to GitHub.

C9 is the one claim about this repository rather than the fixture: the closed units'
`status` table, printed by the `cos.mjs` at the merge base with `origin/main` and by this
one, must be identical — except the legend line under the table, which gained
`c changes-requested` and is left out of the comparison on purpose, and said so below.

**This does not measure the outcome in `intent.md`.** That needs three real units shipped
after this merges, compared against `mergedAt`; `ship.md` carries the command for it.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
COS = REPO / ".claude" / "scripts" / "cos.mjs"
EXIT_PASS, EXIT_BROKEN, EXIT_ENV = 0, 1, 2

UNIT = "0001_demo-unit"
BRANCH = "feat/demo-unit"
FIX = "b" * 40


def say(text: str) -> None:
    print(text, flush=True)


def require_environment() -> None:
    for tool in ("node", "git", "uv"):
        if shutil.which(tool) is None:
            say(f"no {tool} on PATH — this proof cannot answer without it")
            raise SystemExit(EXIT_ENV)


class Fixture:
    """A git repository holding one unit, a bare origin, and a fake `gh`."""

    def __init__(self, base: Path):
        self.base = base
        self.work = base / "work"
        self.unit = self.work / ".cos" / UNIT
        self.bin = base / "bin"
        self.checks = base / "checks.json"
        self.bin.mkdir()
        gh = self.bin / "gh"
        # `gh pr view` answers with the pull request's head: whatever `$FAKE_GH_HEAD` holds,
        # else this checkout's HEAD. Everything else is `gh pr checks`.
        gh.write_text(
            "#!/bin/sh\n"
            'if [ "$2" = view ]; then\n'
            '  head=$(cat "$FAKE_GH_HEAD" 2>/dev/null || git rev-parse HEAD)\n'
            '  printf \'{"state":"OPEN","headRefOid":"%s"}\\n\' "$head"\n'
            "  exit 0\n"
            "fi\n"
            'cat "$FAKE_GH_CHECKS"\n',
            encoding="utf-8",
        )
        self.head_file = base / "head"
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
        self.set_checks([{"name": "tests", "bucket": "pass"}])

        subprocess.run(["git", "init", "-q", "--bare", str(base / "origin.git")], check=True)
        self.unit.mkdir(parents=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "proof@example.invalid")
        self.git("config", "user.name", "verify_0015")
        self.git("remote", "add", "origin", str(base / "origin.git"))
        self.write("intent.md", "# Intent: demo\nAuthor: t. Type: feat. Status: accepted.\n")
        for name in ("spec.md", "plan.md", "impl.md"):
            self.write(name, f"# {name}\nAuthor: t. Status: accepted.\n")
        self.write("pr.md", "# PR: demo\nPR: https://github.com/o/r/pull/7. Author: t. Status: accepted.\n")
        self.commit("the unit")
        self.git("switch", "-q", "-c", BRANCH)

    def git(self, *args: str) -> str:
        out = subprocess.run(["git", *args], cwd=self.work, check=True, capture_output=True, text=True)
        return out.stdout.strip()

    def write(self, name: str, text: str) -> None:
        (self.unit / name).write_text(text, encoding="utf-8")

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def set_checks(self, checks: list[dict]) -> None:
        self.checks.write_text(json.dumps(checks), encoding="utf-8")

    def cos(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        # The machine's own round limit, if it has one, must not decide a claim about the
        # default. A claim that wants another limit passes it in `env`.
        full = {k: v for k, v in os.environ.items() if k != "COS_REVIEW_ROUNDS"}
        full |= {
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "FAKE_GH_CHECKS": str(self.checks),
            "FAKE_GH_HEAD": str(self.head_file),
            **(env or {}),
        }
        return subprocess.run(
            ["node", str(COS), "--root", str(self.work), *args],
            capture_output=True, text=True, env=full,
        )

    def gate(self, stage: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return self.cos("gate", UNIT, stage, "--repo", str(self.work), env=env)

    def next_action(self) -> str:
        out = self.cos("status", "--json")
        return next(u for u in json.loads(out.stdout)["units"] if u["name"] == UNIT)["next"]["action"]


def review(status: str, *rounds: tuple[int, str, str, list[str]]) -> str:
    """A `review.md` with the given header status and rounds (n, sha, verdict, findings)."""
    text = f"# Review: demo\nPR: pr.md. Author: t. Concluded by: agent. Status: {status}.\n\n"
    for n, sha, verdict, findings in rounds:
        text += f"## Round {n}\n\nReviewed: {sha}. Verdict: {verdict}.\n\n### Findings\n\n"
        text += "".join(f"{f}\n" for f in findings)
        text += "\n### What was not reviewed\n\nnothing\n\n"
    return text


def claims(fx: Fixture) -> dict[str, tuple[bool, str]]:
    got: dict[str, tuple[bool, str]] = {}
    head = fx.git("rev-parse", "HEAD")

    # C1 — review gate needs a PR named in pr.md.
    fx.write("pr.md", "# PR: demo\nAuthor: t. Status: accepted.\n")
    r = fx.gate("review")
    got["C1"] = (r.returncode == 1 and "names no pull request" in r.stderr, r.stderr.strip())
    fx.write("pr.md", "# PR: demo\nPR: https://github.com/o/r/pull/7. Author: t. Status: accepted.\n")

    # C2 — changes-requested and rejected lead to different places.
    fx.write("review.md", review("changes-requested", (1, head, "changes-requested", ["- F1 [open] a.py:1 — x"])))
    asked = fx.next_action()
    fx.write("review.md", review("rejected", (1, head, "changes-requested", ["- F1 [open] a.py:1 — x"])))
    rejected = fx.next_action()
    got["C2"] = (
        asked.startswith("fix the open findings") and rejected == "closed — review rejected",
        f"changes-requested → {asked!r}; rejected → {rejected!r}",
    )

    # C3 — accepted with an open finding cannot ship, and the finding is named.
    fx.write("review.md", review("accepted", (1, head, "pass", ["- F1 [open] a.py:1 — x"])))
    r = fx.gate("ship")
    got["C3"] = (r.returncode == 1 and "F1 [open]" in r.stderr, r.stderr.strip())

    # C4 — the earlier round survives in what is read; a gap in numbering is refused.
    two = review(
        "accepted",
        (1, head, "changes-requested", ["- F1 [open] a.py:1 — x"]),
        (2, head, "pass", [f"- F1 [fixed {FIX}] a.py:1 — x"]),
    )
    fx.write("review.md", two)
    units = json.loads(fx.cos("status", "--json").stdout)["units"]
    # `.get` all the way down: a `cos.mjs` that does not read rounds is a failed claim, not
    # a crashed proof.
    artifact = next(u for u in units if u["name"] == UNIT)["artifacts"].get("review.md", {})
    rounds = (artifact.get("review") or {}).get("rounds") or []
    kept = [r["n"] for r in rounds] == [1, 2] and rounds[0]["findings"][:1] == [
        {"id": "F1", "label": "open", "fixedBy": None, "text": "a.py:1 — x"}
    ]
    fx.write("review.md", two.replace("## Round 2", "## Round 3"))
    r = fx.gate("ship")
    got["C4"] = (
        kept and r.returncode == 1 and "numbered 1, 3" in r.stderr,
        f"rounds read: {[r['n'] for r in rounds]}; gap → {r.stderr.strip()}",
    )

    # C5 — real git. Code is committed and reviewed; the unit's own files may move after
    # the pass, code may not.
    (fx.work / "src").mkdir()
    (fx.work / "src" / "a.txt").write_text("reviewed\n", encoding="utf-8")
    reviewed = fx.commit("code that was reviewed")
    fx.git("push", "-q", "origin", BRANCH)
    fx.write("review.md", review("accepted", (1, reviewed, "pass", [])))
    fx.commit("record the passing round")
    only_unit = fx.gate("ship")
    (fx.work / "src" / "a.txt").write_text("not reviewed\n", encoding="utf-8")
    fx.commit("code after the pass")
    after = fx.gate("ship")
    elsewhere = fx.git("rev-parse", "HEAD")
    fx.git("reset", "-q", "--hard", "HEAD~1")
    # Review round 1, F2: the code commit reached the pull request from another checkout.
    # Neither ref here has it; only GitHub's head does. The gate must read that head.
    fx.head_file.write_text(elsewhere, encoding="utf-8")
    stale = fx.gate("ship")
    fx.head_file.unlink()
    got["C5"] = (
        only_unit.returncode == 0
        and f"--match-head-commit {fx.git('rev-parse', 'HEAD')}" in only_unit.stdout
        and after.returncode == 1 and "src/a.txt" in after.stderr
        and stale.returncode == 1 and "head of #7" in stale.stderr and "src/a.txt" in stale.stderr,
        f"only .cos/{UNIT}/ → {only_unit.stdout.strip()}; code after → {after.stderr.strip()}; "
        f"pushed from elsewhere → {stale.stderr.strip()}",
    )

    # C6 — CI red blocks review and names the check; green opens it.
    fx.write("review.md", review("changes-requested", (1, reviewed, "changes-requested", ["- F1 [open] a.py:1 — x"])))
    fx.set_checks([{"name": "tests", "bucket": "fail"}, {"name": "branch-name", "bucket": "pass"}])
    red = fx.gate("review")
    fx.set_checks([{"name": "tests", "bucket": "pass"}, {"name": "branch-name", "bucket": "pass"}])
    green = fx.gate("review")
    got["C6"] = (
        red.returncode == 1 and "CI is red" in red.stderr and "tests" in red.stderr and green.returncode == 0,
        f"red → {red.stderr.strip()}; green → exit {green.returncode}",
    )

    # C7 — three rounds asking for changes need a person; a limit of four opens it again.
    three = review(
        "changes-requested",
        *[(n, reviewed, "changes-requested", ["- F1 [open] a.py:1 — x"]) for n in (1, 2, 3)],
    )
    fx.write("review.md", three)
    stopped = fx.gate("review")
    raised = fx.gate("review", env={"COS_REVIEW_ROUNDS": "4"})
    got["C7"] = (
        stopped.returncode == 1 and "needs a person" in stopped.stderr and raised.returncode == 0,
        f"3 of 3 → {stopped.stderr.strip()}; COS_REVIEW_ROUNDS=4 → exit {raised.returncode}",
    )
    return got


def c8() -> tuple[bool, str]:
    out = subprocess.run(
        ["grep", "-rn", "gh pr merge", ".claude/"], cwd=REPO, capture_output=True, text=True
    ).stdout.strip().splitlines()
    files = {line.split(":", 1)[0] for line in out}
    claude_lines = [line for line in out if line.startswith(".claude/CLAUDE.md:")]
    ok = files == {".claude/skills/write-ship/SKILL.md", ".claude/CLAUDE.md"} and all(
        "ship" in line for line in claude_lines
    )
    return ok, "; ".join(out)


def c9(base: Path) -> tuple[bool, str]:
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)

    merge_base = git("merge-base", "HEAD", "origin/main").stdout.strip()
    if not merge_base:
        return False, "no merge base with origin/main"
    old = base / "old-cos.mjs"
    old.write_text(git("show", f"{merge_base}:.claude/scripts/cos.mjs").stdout, encoding="utf-8")

    def table(script: Path) -> list[str]:
        env = {k: v for k, v in os.environ.items() if k != "COS_REVIEW_ROUNDS"}
        out = subprocess.run(
            ["node", str(script), "--root", str(REPO), "status"], capture_output=True, text=True, env=env
        ).stdout
        # The legend under the table gained `c changes-requested`; it is the one line left
        # out, and it is left out on purpose.
        return [line for line in out.splitlines() if not line.startswith("A accepted")]

    before, after = table(old), table(COS)
    same = before == after and len(before) > 2
    touched = git("diff", "--name-only", "origin/main...", "--", ".cos/00[01][0-9]_*").stdout.strip()
    return (
        same and not touched,
        f"table {'identical' if same else 'DIFFERS'} over {len(before)} lines "
        f"(legend excluded) against {merge_base[:12]}; closed units touched: {touched or 'none'}",
    )


def main() -> int:
    require_environment()
    with tempfile.TemporaryDirectory(prefix="verify-0015-") as tmp:
        base = Path(tmp)
        results = claims(Fixture(base))
        results["C8"] = c8()
        results["C9"] = c9(base)
    held = 0
    for name in sorted(results):
        ok, detail = results[name]
        held += ok
        say(f"{'ok  ' if ok else 'FAIL'} {name}: {detail}")
    say(f"{held}/{len(results)} claims hold")
    return EXIT_PASS if held == len(results) else EXIT_BROKEN


if __name__ == "__main__":
    sys.exit(main())
