#!/usr/bin/env python3
"""Proof for `0083_ui-work-ships-without-anyone-looking-at-the-screen` (`spec.md` R14).

Plain, it builds a git repository with a bare-directory remote in a temporary directory,
copies this checkout's `.claude/rules/ui-standard.md` into it, cuts one branch per unit of
a store it builds beside it, and asks `cos.mjs gate` and `cos.mjs next` about each unit
with a fake `gh` first on `PATH` (the pull request's head is the unit's branch, its checks
green) and a `git` shim that logs every argv before running the real `git`:

    (a) R3   a branch changing `coscc/screens.py` is a UI unit; one changing only
             `coscc/runner.py`, or only the unit's own `.cos/` files, is not
    (b) R10  a UI unit whose passing round has no `### Screens` cannot ship, the reason
             names `### Screens`, and `next` offers `review`
    (c) R10  each condition broken once — no word "agent", the wrong `Standard:`, no
             `.png` line, `Taken at` not an ancestor of `Reviewed`, a commit changing
             `coscc/screens.py` between them — closes `ship` and names what is missing
    (d) R10  a valid `### Screens` opens `ship`, pinned with `--match-head-commit`
    (e) R11  an `[open]` `low` opening with `S3` closes `ship`; the same line without it
             does not, as `0061` has it
    (f) R12  for units that change no screen, `gate` for every stage and `next` print the
             same bytes and exit the same as the `cos.mjs` at `git merge-base HEAD
             origin/main`; `gate ship` runs at most one `git diff --name-only` more than it,
             and with the standard removed, not one `git` command more
    (g) R5   `capture_screens.scan` finds each of its six kinds, and nothing in clean text
    (h)      `--measure` on a store and repository built here, with `--today` given
    (i)      review round 1, F1: `capture_screens` refuses an `--out` holding files and no
             `manifest.json`, and removes only a previous run's PNGs and manifest

`--measure` measures the intent's outcome. It takes the line from this unit's `ship.md`
(`## What went out`, as `verify_0061` does), and once 2026-12-01 has come, lists the
first-parent commits on `origin/main` of this checkout from that line to the end of
2026-11-30, keeps those that change a file the standard at that commit lists — matched by
`cos.mjs`'s own `parseStandard` and `uiFiles`, through `node`, so there is no second glob
matcher here — and reads each one's unit number from `(NNNN)` in its subject. For each
unit it reads `cos.mjs --root <slot> status --json` under `<COS_DATA_DIR>/units/*/.cos/`
and asks `cos.mjs`'s `screensProblems` about the last passing round. It does not check
`Taken at` against `Reviewed` again: the squash removed the branch's commits, and the
`ship` gate checked both when it merged. It writes `<COS_DATA_DIR>/measurements/0083-
<timestamp>.json` and nothing else. Run it at a terminal: inside a step it reads a scratch
data root (`0076`).

    0  every claim held; with --measure, at least one UI unit and every one of them valid
    1  a claim did not hold; with --measure, no UI unit, or one without valid screenshots
    2  no `node`, `git` or `uv`, no merge-base with origin/main; with --measure, no line
       yet or the window still open

No session, no quota, no network.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.verify_0061 import data_root, shipped_at, status_json  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
COS = REPO / ".claude" / "scripts" / "cos.mjs"
UI_STANDARD = ".claude/rules/ui-standard.md"
SLUG = "ui-work-ships-without-anyone-looking-at-the-screen"
WINDOW_END = date(2026, 11, 30)
EXIT_PASS, EXIT_BROKEN, EXIT_ENV = 0, 1, 2

GIT_ID = ("-c", "user.name=verify", "-c", "user.email=verify@example.invalid", "-c", "commit.gpgsign=false")
STAGES = ["idea", "intent", "spec", "spike", "plan", "impl", "implement", "pr", "review", "ship"]


def say(line: str) -> None:
    print(line, flush=True)


def claim(ok: bool, text: str, detail: str = "") -> bool:
    say(f"{'PASS' if ok else 'FAIL'}  {text}{': ' + detail if detail and not ok else ''}")
    return ok


# ---------------------------------------------------------------------------
# cos.mjs's own functions, through node
# ---------------------------------------------------------------------------

NODE_HELPER = """
import { parseStandard, uiFiles, screensProblems, UI_STANDARD } from %s
let input = ''
for await (const chunk of process.stdin) input += chunk
const req = JSON.parse(input)
console.log(JSON.stringify({
  ui: (req.ui ?? []).map(({ standard, files }) => uiFiles(files, parseStandard(standard))),
  problems: (req.screens ?? []).map((s) => screensProblems(s, UI_STANDARD)),
}))
"""


def cos_functions(request: dict) -> dict:
    """`uiFiles`/`parseStandard` and `screensProblems` of this checkout's `cos.mjs`."""
    r = subprocess.run(
        ["node", "--input-type=module", "-e", NODE_HELPER % json.dumps(COS.as_uri())],
        input=json.dumps(request), capture_output=True, text=True, cwd=str(REPO),
    )
    if r.returncode != 0:
        raise RuntimeError(f"cos.mjs could not be asked: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout)


# ---------------------------------------------------------------------------
# --measure
# ---------------------------------------------------------------------------


def git_in(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def measure(root: Path, repo: Path, today: date) -> int:
    """The intent's outcome. Everything it prints, it also writes under `measurements/`."""
    slots = sorted(p for p in (root / "units").glob("*") if (p / ".cos").is_dir())
    units: dict[int, list[dict]] = {}
    line: datetime | None = None
    for slot in slots:
        data = status_json(COS, slot)
        if data is None:
            return EXIT_ENV
        for unit in data.get("units") or []:
            if isinstance(unit.get("number"), int):
                units.setdefault(unit["number"], []).append({**unit, "slot": slot.name})
            accepted = ((unit.get("artifacts") or {}).get("ship.md") or {}).get("status") == "accepted"
            if unit.get("slug") == SLUG and accepted:
                line = shipped_at(slot / ".cos" / unit["name"] / "ship.md") or line
    if line is None:
        say(f"line: none — {SLUG} has no accepted ship.md with a timestamp")
        return EXIT_ENV
    say(f"line: {line.isoformat()}")
    if today <= WINDOW_END:
        say(f"the window runs to {WINDOW_END.isoformat()} and today is {today.isoformat()}: measure from 2026-12-01")
        return EXIT_ENV

    log = git_in(repo, "log", "--first-parent", "--format=%H%x09%s", f"--since={line.isoformat()}",
                 f"--until={WINDOW_END.isoformat()}T23:59:59+00:00", "origin/main")
    if log.returncode != 0:
        say(f"git log origin/main failed: {log.stderr.strip()}")
        return EXIT_ENV
    commits = [l.split("\t", 1) for l in log.stdout.splitlines() if "\t" in l]
    asks = []
    for sha, _ in commits:
        standard = git_in(repo, "show", f"{sha}:{UI_STANDARD}")
        diff = git_in(repo, "diff", "--name-only", f"{sha}^", sha)
        files = diff.stdout.split() if diff.returncode == 0 else []
        asks.append({"standard": standard.stdout if standard.returncode == 0 else "", "files": files})
    touched = cos_functions({"ui": asks})["ui"] if asks else []

    rows: list[dict] = []
    for (sha, subject), files in zip(commits, touched):
        if not files:
            continue
        m = re.search(r"\((\d{4})\)", subject)
        row = {"commit": sha, "subject": subject, "files": files, "unit": None, "screens": None, "problems": []}
        rows.append(row)
        if not m:
            row["problems"] = ["the subject names no (NNNN), so no unit's review.md can be read"]
            continue
        found = units.get(int(m.group(1))) or []
        if len(found) != 1:
            row["problems"] = [f"{len(found)} units numbered {m.group(1)} in the store, not one"]
            continue
        unit = found[0]
        row["unit"] = f"{unit['slot']}/{unit['name']}"
        rounds = (((unit.get("artifacts") or {}).get("review.md") or {}).get("review") or {}).get("rounds") or []
        passed = [r for r in rounds if r.get("verdict") == "pass"]
        if not passed:
            row["problems"] = ["review.md has no passing round"]
            continue
        row["screens"] = passed[-1].get("screens")
        row["problems"] = cos_functions({"screens": [row["screens"]]})["problems"][0]

    for row in rows:
        verdict = "valid" if not row["problems"] else "; ".join(row["problems"])
        say(f"{row['commit'][:7]} {row['unit'] or '?'}: {', '.join(row['files'])} — {verdict}")
    failed = [r for r in rows if r["problems"]]
    if not rows:
        say("no UI unit merged in the window — the intent reads that as a miss")
        code = EXIT_BROKEN
    else:
        say(f"{len(rows)} UI units, {len(rows) - len(failed)} with valid screenshots")
        code = EXIT_BROKEN if failed else EXIT_PASS

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out = root / "measurements" / f"0083-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"line": line.isoformat(), "today": today.isoformat(), "units": rows, "exit": code},
                              indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    say(f"wrote {out}")
    return code


# ---------------------------------------------------------------------------
# The fixture: a repository, a store, a fake gh and a counting git
# ---------------------------------------------------------------------------


class Fixture:
    def __init__(self, tmp: Path, real_git: str):
        self.tmp = tmp
        self.remote = tmp / "remote.git"
        self.repo = tmp / "repo"
        self.store = tmp / "store"
        self.heads = tmp / "heads"
        self.bin = tmp / "bin"
        self.log = tmp / "git.log"
        for d in (self.store / ".cos", self.heads, self.bin):
            d.mkdir(parents=True)
        (self.bin / "gh").write_text(
            "#!/bin/sh\n"
            f"if [ \"$1\" = pr ] && [ \"$2\" = view ]; then printf '{{\"state\":\"OPEN\",\"headRefOid\":\"%s\"}}\\n' \"$(cat '{self.heads}'/\"$3\")\"; exit 0; fi\n"
            "if [ \"$1\" = pr ] && [ \"$2\" = checks ]; then echo '[{\"name\":\"tests\",\"bucket\":\"pass\"}]'; exit 0; fi\n"
            "exit 1\n", encoding="utf-8")
        (self.bin / "git").write_text(
            "#!/bin/sh\n"
            f"[ -n \"$GIT_LOG\" ] && printf '%s\\n' \"$*\" >> \"$GIT_LOG\"\n"
            f"exec '{real_git}' \"$@\"\n", encoding="utf-8")
        for f in ("gh", "git"):
            (self.bin / f).chmod(0o755)
        self.env = {**os.environ, "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}"}
        self.env.pop("GIT_LOG", None)
        self.number = 0

        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        subprocess.run(["git", "clone", "-q", str(self.remote), str(self.repo)], check=True, capture_output=True)
        self.git("symbolic-ref", "HEAD", "refs/heads/main")
        (self.repo / ".claude" / "rules").mkdir(parents=True)
        shutil.copyfile(REPO / UI_STANDARD, self.repo / UI_STANDARD)
        self.commit({"README.md": "fixture\n", "coscc/screens.py": "# screens\n", "coscc/runner.py": "# runner\n"}, "main")
        self.git("push", "-q", "origin", "main")
        self.git("fetch", "-q", "origin")

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.repo), *GIT_ID, *args],
                              capture_output=True, text=True, check=True).stdout.strip()

    def commit(self, files: dict[str, str], message: str) -> str:
        for rel, text in files.items():
            (self.repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / rel).write_text(text, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def branch(self, name: str, commits: list[dict[str, str]]) -> list[str]:
        """Cut `name` from `main`, one commit per mapping, push it, and go back to `main`."""
        self.git("switch", "-q", "-c", name, "main")
        shas = [self.commit(files, f"{name} {i}") for i, files in enumerate(commits)]
        self.git("push", "-q", "origin", name)
        self.git("switch", "-q", "main")
        return shas

    def unit(self, slug: str, commits: list[dict[str, str]], findings: list[str] = (), screens=None) -> tuple[str, list[str]]:
        """A unit whose branch carries `commits` and whose one review round passes at its head.
        `screens(shas)` returns the `### Screens` section, or `None` for none."""
        self.number += 1
        name = f"{self.number:04d}_{slug}"
        shas = self.branch(f"feat/{slug}", commits)
        head = shas[-1]
        (self.heads / str(self.number)).write_text(head, encoding="utf-8")
        section = screens(shas) if screens else ""
        d = self.store / ".cos" / name
        d.mkdir()
        files = {
            "intent.md": "# Intent: x\nAuthor: t. Type: feat. Status: accepted.\n",
            "spec.md": "# Spec: x\nAuthor: t. Status: accepted.\n",
            "plan.md": "# Plan: x\nAuthor: t. Status: accepted.\n",
            "impl.md": "# Impl: x\nAuthor: t. Status: accepted.\n",
            "pr.md": f"# PR: x\nPR: https://github.com/o/r/pull/{self.number}. Author: t. Status: accepted.\n",
            "review.md": (
                "# Review: x\nAuthor: t. Status: accepted.\n\n## Round 1\n\n"
                f"Reviewed: {head}. Verdict: pass.\n\n### Findings\n\n" + "\n".join(findings) + "\n\n"
                "### What was not reviewed\n\nNothing.\n" + section
            ),
        }
        for f, text in files.items():
            (d / f).write_text(text, encoding="utf-8")
        return name, shas

    def cos(self, script: Path, *args: str, log: bool = False) -> tuple[int, str, str, list[str]]:
        env = dict(self.env)
        if log:
            self.log.write_text("", encoding="utf-8")
            env["GIT_LOG"] = str(self.log)
        r = subprocess.run(["node", str(script), "--root", str(self.store), *args, "--repo", str(self.repo)],
                           capture_output=True, text=True, env=env, cwd=str(self.tmp))
        calls = self.log.read_text(encoding="utf-8").splitlines() if log else []
        return r.returncode, r.stdout, r.stderr, calls


def screens(taken: int = 0, by: str = "an agent session (write-review)", standard: str = UI_STANDARD,
            shots: tuple[str, ...] = ("- .screens/board-1440x900.png — 1440×900 — /board — no violation",)):
    """A `### Screens` section taken at the `taken`-th commit of the branch, or at a given sha."""
    def make(shas: list[str]) -> str:
        at = taken if isinstance(taken, str) else shas[taken]
        return (f"\n### Screens\n\nTaken at: {at}. Standard: `{standard}`. Looked at by: {by}, from screenshots.\n\n"
                + "\n".join(shots) + "\n")
    return make


SCREENS_PY = {"coscc/screens.py": "# screens, changed\n"}
SCREENS_PY_AGAIN = {"coscc/screens.py": "# screens, changed again\n"}
RUNNER_PY = {"coscc/runner.py": "# runner, changed\n"}


def proof(old: Path, tmp: Path, real_git: str) -> list[bool]:
    fx = Fixture(tmp / "fx", real_git)
    side = fx.branch("side", [{"coscc/screens.py": "# a commit no unit's branch holds\n"}])[0]
    plain, _ = fx.unit("plain", [RUNNER_PY])
    own, _ = fx.unit("own-files", [{f".cos/{fx.number + 1:04d}_own-files/notes.md": "notes\n"}])
    bare, _ = fx.unit("no-screens", [SCREENS_PY])
    not_agent, _ = fx.unit("not-agent", [SCREENS_PY], screens=screens(by="Bao"))
    wrong, _ = fx.unit("wrong-standard", [SCREENS_PY], screens=screens(standard=".claude/rules/other.md"))
    no_png, _ = fx.unit("no-png", [SCREENS_PY], screens=screens(shots=("- .screens/board.jpg — 1440×900 — /board — ok",)))
    stray, _ = fx.unit("not-ancestor", [SCREENS_PY], screens=screens(taken=side))
    after, _ = fx.unit("changed-after", [SCREENS_PY, SCREENS_PY_AGAIN], screens=screens(taken=0))
    valid, valid_shas = fx.unit("valid", [SCREENS_PY, RUNNER_PY], screens=screens(taken=0))
    s_low, _ = fx.unit("s-low", [RUNNER_PY], findings=["- F1 [open] coscc/runner.py:1 — low — S3 shows a full sha"])
    low, _ = fx.unit("low", [RUNNER_PY], findings=["- F1 [open] coscc/runner.py:1 — low — a word could be shorter"])

    def ship(unit: str) -> tuple[int, str]:
        code, out, err, _ = fx.cos(COS, "gate", unit, "ship")
        return code, out + err

    results = []
    got = {u: ship(u) for u in (plain, own, bare)}
    results.append(claim(
        got[plain][0] == 0 and got[own][0] == 0 and got[bare][0] == 1 and "coscc/screens.py" in got[bare][1],
        "(a) R3: coscc/screens.py makes a UI unit; coscc/runner.py or the unit's own .cos/ files do not",
        "; ".join(f"{u}: exit {c} {t.strip()[:160]!r}" for u, (c, t) in got.items()),
    ))

    code, text = got[bare]
    nxt = fx.cos(COS, "next", bare)[1]
    results.append(claim(
        code == 1 and "### Screens" in text and '"stage":"review"' in nxt,
        "(b) R10: a UI unit whose pass has no ### Screens cannot ship, and next offers review",
        f"exit {code} {text.strip()[:200]!r}; next {nxt.strip()[:200]!r}",
    ))

    cases = [
        (not_agent, "does not say it was an agent"),
        (wrong, "names the standard .claude/rules/other.md"),
        (no_png, "lists no screenshot"),
        (stray, "not an ancestor of the reviewed commit"),
        (after, "coscc/screens.py changed after the screenshots"),
    ]
    for unit, words in cases:
        code, text = ship(unit)
        nxt = fx.cos(COS, "next", unit)[1]
        results.append(claim(
            code == 1 and words in text and '"stage":"review"' in nxt,
            f"(c) R10: {unit} closes ship, says \"{words}\", and next offers review",
            f"exit {code} {text.strip()[:240]!r}; next {nxt.strip()[:160]!r}",
        ))

    code, text = ship(valid)
    results.append(claim(
        code == 0 and f"--match-head-commit {valid_shas[-1]}" in text,
        "(d) R10: a valid ### Screens opens ship, pinned to the head",
        f"exit {code} {text.strip()[:200]!r}",
    ))

    (c1, t1), (c2, t2) = ship(s_low), ship(low)
    results.append(claim(
        c1 == 1 and "F1 [open]" in t1 and c2 == 0,
        "(e) R11: an open low opening with S3 closes ship; without S3 it does not",
        f"S3: exit {c1} {t1.strip()[:160]!r}; plain low: exit {c2} {t2.strip()[:160]!r}",
    ))

    differ = []
    compared = 0
    for unit in (plain, own, low):
        for call in [("next", unit)] + [("gate", unit, s) for s in STAGES]:
            compared += 1
            if fx.cos(old, *call)[:3] != fx.cos(COS, *call)[:3]:
                differ.append(" ".join(call))
    results.append(claim(
        not differ, f"(f) R12: gate and next unchanged byte for byte on units that change no screen ({compared} comparisons)",
        ", ".join(differ),
    ))
    old_calls = fx.cos(old, "gate", plain, "ship", log=True)[3]
    new_calls = fx.cos(COS, "gate", plain, "ship", log=True)[3]
    extra = [c for c in new_calls if c not in old_calls or new_calls.count(c) > old_calls.count(c)]
    with_standard = (len(new_calls) - len(old_calls) <= 1 and all(c.startswith("diff --name-only") for c in extra)
                     and new_calls[:len(old_calls)] == old_calls)
    (fx.repo / UI_STANDARD).unlink()
    try:
        gone_calls = fx.cos(COS, "gate", plain, "ship", log=True)[3]
    finally:
        fx.git("checkout", "-q", "--", UI_STANDARD)
    results.append(claim(
        with_standard and gone_calls == old_calls and len(old_calls) > 0,
        "(f) R12: gate ship runs at most one git diff --name-only more, and none more without a standard",
        f"old {len(old_calls)} calls, new {len(new_calls)} (extra {extra}), without the standard {len(gone_calls)}",
    ))

    from scripts.capture_screens import scan
    kinds = {k for k, _ in scan(
        f"head {'a' * 40} id 123e4567-e89b-12d3-a456-426614174000 at 1759000000 set COS_WORKING_DIR "
        "in /tmp/cos-x on 2026-09-25T04:13:29Z"
    )}
    clean = scan("Board · 3 units · updated 5 min ago · abc1234 · Sep 25, 11:13")
    results.append(claim(
        kinds == {"sha", "uuid", "epoch", "env", "path", "iso-time"} and not clean,
        "(g) R5: scan finds each of its six kinds, and nothing in clean text",
        f"found {sorted(kinds)}, clean text gave {clean}",
    ))

    results += measure_claims(tmp / "measure", real_git)

    from scripts.capture_screens import clear_out, out_refused
    outs = tmp / "outs"
    stranger, previous, empty = outs / "stranger", outs / "previous", outs / "empty"
    for d in (stranger, previous, empty):
        d.mkdir(parents=True)
    (stranger / "notes.md").write_text("mine\n", encoding="utf-8")
    for name in ("manifest.json", "board-1440x900.png", "keep.md"):
        (previous / name).write_text("x\n", encoding="utf-8")
    refusals = [out_refused(d) for d in (stranger, stranger / "notes.md", previous, empty, outs / "missing")]
    clear_out(previous)
    left = sorted(p.name for p in previous.iterdir())
    results.append(claim(
        refusals[0] is not None and refusals[1] is not None and refusals[2:] == [None, None, None] and left == ["keep.md"],
        "(i) review F1: capture refuses an --out it did not write, and clears only its own PNGs and manifest",
        f"refusals {refusals}, left in a previous --out {left}",
    ))
    return results


# ---------------------------------------------------------------------------
# (h) --measure on a store and repository built here
# ---------------------------------------------------------------------------


SHIP = "# Ship: x\nReview: review.md. Author: t. Status: accepted.\n\n## What went out\n\n- `mergedAt`: 2026-09-26T00:00:00Z.\n"
PASS_ROUND = "# Review: x\nAuthor: t. Status: accepted.\n\n## Round 1\n\nReviewed: {sha}. Verdict: pass.\n\n### Findings\n\n{screens}"
GOOD_SCREENS = ("### Screens\n\nTaken at: {sha}. Standard: .claude/rules/ui-standard.md. Looked at by: an agent "
                "session, from screenshots.\n\n- .screens/board-1440x900.png — 1440×900 — /board — ok\n")


def measured(tmp: Path, name: str, today: date, window: dict[str, str] | None, valid: bool = True) -> int:
    """A repository whose `origin/main` has one base commit before the line and, when
    `window` is given, one squash commit `feat(0090): …` changing those files inside it; a
    store holding this unit's `ship.md` and `0090`'s review. `measure`'s exit code, quietly."""
    here = tmp / name
    remote, repo = here / "remote.git", here / "repo"
    here.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    subprocess.run(["git", "clone", "-q", str(remote), str(repo)], check=True, capture_output=True)

    def commit(files: dict[str, str], message: str, when: str) -> None:
        for rel, text in files.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(text, encoding="utf-8")
        env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
        for args in (["add", "-A"], ["commit", "-q", "-m", message]):
            subprocess.run(["git", "-C", str(repo), *GIT_ID, *args], check=True, env=env, capture_output=True)

    subprocess.run(["git", "-C", str(repo), "symbolic-ref", "HEAD", "refs/heads/main"], check=True)
    commit({UI_STANDARD: (REPO / UI_STANDARD).read_text(encoding="utf-8"), "coscc/screens.py": "#\n", "coscc/runner.py": "#\n"},
           "chore: base (#1)", "2026-09-20T00:00:00Z")
    if window is not None:
        commit(window, "feat(0090): a change (#90)", "2026-10-10T00:00:00Z")
    subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "fetch", "-q", "origin"], check=True, capture_output=True)

    cos = here / "data" / "units" / "slot" / ".cos"
    mine, other = cos / f"0083_{SLUG}", cos / "0090_a-change"
    for d in (mine, other):
        d.mkdir(parents=True)
        (d / "intent.md").write_text("# I\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8")
    (mine / "ship.md").write_text(SHIP, encoding="utf-8")
    sha = "f" * 40
    (other / "review.md").write_text(
        PASS_ROUND.format(sha=sha, screens=GOOD_SCREENS.format(sha=sha) if valid else ""), encoding="utf-8")

    quiet = io.StringIO()
    with contextlib.redirect_stdout(quiet):
        code = measure(here / "data", repo, today)
    return code


def measure_claims(tmp: Path, real_git: str) -> list[bool]:
    tmp.mkdir(parents=True)
    after, before = date(2026, 12, 1), date(2026, 11, 15)
    results = []
    for name, today, window, valid, want, text in [
        ("early", before, {"coscc/screens.py": "# x\n"}, True, EXIT_ENV, "before 2026-12-01: exit 2"),
        ("none", after, {"coscc/runner.py": "# x\n"}, True, EXIT_BROKEN, "no UI unit in the window: exit 1"),
        ("valid", after, {"coscc/screens.py": "# x\n"}, True, EXIT_PASS, "one UI unit with valid screens: exit 0"),
        ("missing", after, {"coscc/screens.py": "# x\n"}, False, EXIT_BROKEN, "one UI unit without screens: exit 1"),
    ]:
        got = measured(tmp, name, today, window, valid)
        results.append(claim(got == want, f"(h) --measure: {text}", f"exit {got}"))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--measure", action="store_true", help="measure the intent's outcome")
    parser.add_argument("--today", type=date.fromisoformat, default=None, help="YYYY-MM-DD, for --measure")
    args = parser.parse_args()
    missing = [t for t in ("node", "git", "uv") if not shutil.which(t)]
    if missing:
        say(f"environment: {', '.join(missing)} not found")
        return EXIT_ENV
    if args.measure:
        return measure(data_root(), REPO, args.today or datetime.now(timezone.utc).date())

    base = subprocess.run(["git", "merge-base", "HEAD", "origin/main"], capture_output=True, text=True, cwd=str(REPO))
    if base.returncode != 0:
        say(f"environment: no merge-base with origin/main: {base.stderr.strip()}")
        return EXIT_ENV
    sha = base.stdout.strip()
    old_text = subprocess.run(["git", "show", f"{sha}:.claude/scripts/cos.mjs"], capture_output=True, text=True, cwd=str(REPO))
    if old_text.returncode != 0:
        say(f"environment: cannot read cos.mjs at {sha}: {old_text.stderr.strip()}")
        return EXIT_ENV
    say(f"base: {sha[:7]}")

    real_git = shutil.which("git")
    with tempfile.TemporaryDirectory(prefix="verify_0083-") as tmp:
        old = Path(tmp) / "old" / "cos.mjs"
        old.parent.mkdir()
        old.write_text(old_text.stdout, encoding="utf-8")
        results = proof(old, Path(tmp), real_git)

    say(f"\n{sum(results)}/{len(results)} claims held")
    return EXIT_PASS if all(results) else EXIT_BROKEN


if __name__ == "__main__":
    sys.exit(main())
