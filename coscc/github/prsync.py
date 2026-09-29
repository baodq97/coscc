"""Puts `pr.md`'s title and body onto its pull request after a `pr` step.

Only reads title and body (`gh pr view --json title,body`) and replaces them (`gh pr edit
--title=<title> --body-file -`); no other flag. The text is what `cos.mjs pr-text` cut from
`pr.md`.

It overwrites: a description edited on GitHub is replaced and the old text is kept nowhere.
When both already match, nothing is written.

`--title=<title>` is one argv so a title starting with `-` is not read as a flag; the URL is
checked against `gh.PR_URL_RE` first.

`sync` never raises: a failure comes back as `Result("failed", reason=...)`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from coscc.git import gh


@dataclass(frozen=True)
class Result:
    state: str  # updated | already | failed
    reason: str = ""

    def as_dict(self) -> dict:
        return {"state": self.state, "reason": self.reason}


def same(a: str, b: str) -> bool:
    """Equal once `\\r\\n` is `\\n` and trailing whitespace is gone. Nothing else is compared."""
    return (a or "").replace("\r\n", "\n").rstrip() == (b or "").replace("\r\n", "\n").rstrip()


def edit_argv(url: str, title: str | None) -> list[str]:
    """The one `gh pr edit` this module runs."""
    return ["pr", "edit", url, *([f"--title={title}"] if title else []), "--body-file", "-"]


async def sync(
    url: str,
    title: str | None,
    body: str,
    cwd: str,
    run: gh.Run | None = None,
) -> Result:
    """Put `title` and `body` on the pull request at `url` unless they are there. Never raises.

    `title` None leaves the GitHub title as it is. `run` defaults to `gh.run`, looked up at
    call time; the timeout is `gh.TIMEOUT` per call.
    """
    run = run or gh.run
    if not url or not gh.PR_URL_RE.match(url):
        return Result("failed", reason=f"not a pull request URL: {url!r}")

    got = await gh.call(run, ["pr", "view", url, "--json", "title,body"], cwd, None)
    if isinstance(got, str):
        return Result("failed", reason=got)
    code, out, err = got
    if code != 0:
        return Result("failed", reason=gh.said(code, out, err))
    try:
        now = json.loads(out)
        now_title, now_body = str(now.get("title") or ""), str(now.get("body") or "")
    except (json.JSONDecodeError, ValueError, AttributeError) as e:
        return Result("failed", reason=f"gh pr view did not return JSON: {e}")
    if same(now_body, body) and (title is None or same(now_title, title)):
        return Result("already")

    got = await gh.call(run, edit_argv(url, title), cwd, body)
    if isinstance(got, str):
        return Result("failed", reason=got)
    code, out, err = got
    if code != 0:
        return Result("failed", reason=gh.said(code, out, err))
    return Result("updated")
