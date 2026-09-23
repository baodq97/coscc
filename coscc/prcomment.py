"""One review round, posted to the pull request as one ordinary comment (`0021`).

**What this module may do on GitHub, and nothing more.** It reads a pull request's comments
(`gh pr view <url> --json comments`) and adds one comment to it (`gh pr comment <url>
--body-file -`). It never calls `gh pr review` -- not `--approve`, not `--request-changes`,
not `--comment` -- because a review is GitHub's word for an approval decision and this is
an agent session's word about an agent's work (`0021` spec R5). It never edits or deletes a
comment. The body is built only from text already in `review.md`; no caller can hand it
other words.

**Why the pull request is named by its URL.** The URL comes from the unit's own `pr.md`,
so `gh` needs no guess about which repository is meant, and a branch deleted by
`--delete-branch` after the merge does not matter. The regex below also keeps a string
starting with `-` from reaching `gh` as a flag.

**"Exactly one comment per round"** is kept by the marker on the body's last line: before
posting, the comments already on the pull request are read, and one whose last non-empty
line is this round's marker means the round is already there. That covers a post that
reached GitHub but whose answer was lost. It does not cover two processes posting at the
same instant; `coscc/service.py` holds a lock for that within one process.

`post` never raises. A failure comes back as `Result("failed", reason=...)` with gh's own
words, so the caller -- which has already written `review.md` -- loses nothing.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Awaitable, Callable

from coscc.harness import child_env

# Chosen, not measured: the same figure as `coscc/board.py` `GATE_TIMEOUT`, the other place
# this app waits on `gh` talking to GitHub.
TIMEOUT = 30.0

PR_URL_RE = re.compile(r"^https://[^\s/]+/[^\s/]+/[^\s/]+/pull/\d+$")


def marker(unit: str, n: int) -> str:
    """The hidden line that identifies one round of one unit on the pull request."""
    return f"<!-- coscc-review unit={unit} round={n} -->"


def first_line(unit: str, n: int) -> str:
    """Says where the comment came from, before anything else (`0021` spec R4)."""
    return (
        f"**coscc review, round {n} of {unit}.** Written by an agent session, not a person. "
        "This comment is not an approval."
    )


def body(unit: str, n: int, verdict: str | None, text: str) -> str:
    """The whole comment. Pure: the same round always gives the same body.

    The round's text goes in verbatim -- no summary, no cut (`0021` spec R3). A round too
    long for GitHub fails to post and shows as not posted, rather than posting short.
    """
    return (
        f"{first_line(unit, n)}\n\n"
        f"Verdict: {verdict or 'unreadable'}\n\n"
        f"{(text or '').rstrip()}\n\n"
        f"{marker(unit, n)}\n"
    )


def _last_line(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""


@dataclass(frozen=True)
class Result:
    state: str  # posted | already | failed
    url: str = ""
    reason: str = ""

    def as_dict(self) -> dict:
        return {"state": self.state, "url": self.url, "reason": self.reason}


Run = Callable[[list[str], str, "str | None"], Awaitable[tuple[int, str, str]]]


async def _gh(argv: list[str], cwd: str, stdin: str | None) -> tuple[int, str, str]:
    """One `gh` call: exit code and both streams. Raises what the child raised."""
    proc = await asyncio.create_subprocess_exec(
        "gh",
        *argv,
        cwd=cwd,
        env=child_env(),
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(stdin.encode() if stdin is not None else None), timeout=TIMEOUT
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


async def _call(run: Run, argv: list[str], cwd: str, stdin: str | None) -> tuple[int, str, str] | str:
    """The call's answer, or a reason it could not be had."""
    try:
        return await run(argv, cwd, stdin)
    except FileNotFoundError:
        return "gh is not installed or not on PATH"
    except asyncio.TimeoutError:
        return f"gh {' '.join(argv[:2])} timed out after {TIMEOUT:.0f}s"
    except (OSError, ValueError) as e:
        return f"could not run gh: {e}"


def _said(code: int, out: str, err: str) -> str:
    return (err or out).strip() or f"gh exited {code} and said nothing"


async def post(
    unit: str,
    n: int,
    verdict: str | None,
    text: str,
    pr_url: str | None,
    cwd: str,
    run: Run | None = None,
) -> Result:
    """Post one round unless it is already there. Never raises.

    `run` defaults to `_gh`, looked up at call time so a test can replace the module's.
    """
    run = run or _gh
    if not pr_url:
        return Result("failed", reason="pr.md names no pull request")
    if not PR_URL_RE.match(pr_url):
        return Result("failed", reason=f"pr.md names something that is not a pull request URL: {pr_url!r}")

    mark = marker(unit, n)
    got = await _call(run, ["pr", "view", pr_url, "--json", "comments"], cwd, None)
    if isinstance(got, str):
        return Result("failed", reason=got)
    code, out, err = got
    if code != 0:
        return Result("failed", reason=_said(code, out, err))
    try:
        comments = json.loads(out).get("comments") or []
    except (json.JSONDecodeError, ValueError, AttributeError) as e:
        return Result("failed", reason=f"gh pr view did not return JSON: {e}")
    for c in comments:
        if isinstance(c, dict) and _last_line(c.get("body") or "") == mark:
            return Result("already", url=str(c.get("url") or ""))

    got = await _call(
        run, ["pr", "comment", pr_url, "--body-file", "-"], cwd, body(unit, n, verdict, text)
    )
    if isinstance(got, str):
        return Result("failed", reason=got)
    code, out, err = got
    if code != 0:
        return Result("failed", reason=_said(code, out, err))
    last = (out.strip().splitlines() or [""])[-1].strip()
    return Result("posted", url=last if last.startswith("https://") else "")
